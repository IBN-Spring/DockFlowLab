"""对接盒子（搜索空间）计算。

自动盒子的决策顺序：

1. 用户手动设置；
2. 用户提供的参考分子；
3. 几何口袋检测（LIGSITE 式包埋扫描）；
4. 共晶配体（几何口袋不可用或与之不重叠时兜底）；
5. PDB SITE 记录；
6. 全蛋白盲对接。

几何口袋与共晶配体 / PDB SITE 重叠时，采用“几何口袋 + 实验位点”的
联合盒子：既保留口袋检测的空间信息，又确保盒子覆盖实验配体。盒子会
自动扩张以容纳所有待对接配体，并限制在科学合理的范围内。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import AppSettings
from ..core import pdbio
from ..core.models import Box
from ..errors import InputError
from ..logging_setup import get_logger
from ..tools import rdkit_io
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import work_directory
from .pockets import Pocket, detect_pockets_cached

log = get_logger("boxes")

LIGAND_MARGIN = 4.0  # 配体边界到盒子壁的最小距离（Å）
SITE_PADDING = 6.0
POCKET_MARGIN = 4.0
EVIDENCE_POCKET_REACH = 4.0  # 联合盒子最多延伸到实验位点外多远
MIN_POCKET_SCORE = 0.50
MIN_POCKET_VOLUME = 120.0  # 小于该体积的空腔无法容纳常见候选分子
CONSENSUS_COVERAGE = 0.35
SITE_CONSENSUS_COVERAGE = 0.50
MAX_VOLUME_WARNING = 40_000.0
MIN_VOLUME_WARNING = 1_500.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _elementwise_max(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(max(float(a[i]), float(b[i])) for i in range(3))  # type: ignore[return-value]


def _sanitize_box(
    center: Sequence[float],
    size: Sequence[float],
    settings: AppSettings,
    *,
    source: str,
    note: str,
    reference: str = "",
) -> tuple[Box, list[str]]:
    warnings: list[str] = []
    center_t = tuple(round(float(v), 3) for v in center)
    if any(not math.isfinite(v) for v in center_t):
        raise InputError("盒子中心包含无效数值")
    raw_size = [float(v) for v in size]
    if any(not math.isfinite(v) or v <= 0 for v in raw_size):
        raise InputError("盒子尺寸必须是正数")
    low = max(6.0, float(settings.box_min_size))
    high = max(low, float(settings.box_max_size))
    clamped = [_clamp(v, low, high) for v in raw_size]
    if any(abs(clamped[i] - raw_size[i]) > 1e-6 for i in range(3)):
        warnings.append(
            f"盒子尺寸已限制在 {low:.0f}–{high:.0f} Å 范围内"
            f"（原尺寸 {raw_size[0]:.1f} × {raw_size[1]:.1f} × {raw_size[2]:.1f} Å）"
        )
    box = Box(
        center=center_t,  # type: ignore[arg-type]
        size=tuple(round(v, 2) for v in clamped),  # type: ignore[arg-type]
        source=source,
        note=note,
        reference=reference,
    )
    if box.volume > MAX_VOLUME_WARNING:
        warnings.append(
            f"搜索空间较大（{box.volume:,.0f} Å³），对接耗时会明显增加；"
            "如果有明确的结合位点，建议手动缩小盒子。"
        )
    elif box.volume < MIN_VOLUME_WARNING:
        warnings.append(
            f"搜索空间较小（{box.volume:,.0f} Å³），可能限制配体的结合构象。"
        )
    return box, warnings


# ----------------------------------------------------------------------
# 坐标提取
# ----------------------------------------------------------------------
def structure_coords(path: str | Path, obabel: OpenBabelAdapter | None = None) -> list[tuple[float, float, float]]:
    """从 PDB/PDBQT/SDF/MOL2/CIF 提取重原子坐标。"""
    path = Path(path)
    fmt = pdbio.detect_format(path)
    if fmt == "cif":
        adapter = obabel or get_openbabel()
        if adapter.available:
            with work_directory("coords_cif") as temp:
                converted = temp / "coords.pdb"
                if adapter.convert(path, converted, out_format="pdb")[0]:
                    atoms = pdbio.parse_atoms(converted)
                    return [atom.coord for atom in atoms if not atom.is_hydrogen]
        raise InputError(f"读取 mmCIF 坐标需要 OpenBabel：{path.name}")
    if fmt in {"pdb", "pdbqt"}:
        atoms = pdbio.parse_pdbqt_atoms(path) if fmt == "pdbqt" else pdbio.parse_atoms(path)
        coords = [atom.coord for atom in atoms if not atom.is_hydrogen]
        if not coords:
            raise InputError(f"文件中没有可用的原子坐标：{path.name}")
        return coords
    if fmt in {"sdf", "mol", "mol2"}:
        try:
            if fmt == "sdf":
                molecules, _ = rdkit_io.read_sdf_molecules(path, remove_hs=True)
                mol = molecules[0] if molecules else None
            elif fmt == "mol":
                mol, _ = rdkit_io.read_mol(path, remove_hs=True)
            else:
                mol, _ = rdkit_io.read_mol2(path, remove_hs=True)
            if mol is not None and mol.GetNumConformers() > 0:
                conformer = mol.GetConformer(0)
                return [
                    (conformer.GetAtomPosition(i).x, conformer.GetAtomPosition(i).y, conformer.GetAtomPosition(i).z)
                    for i in range(mol.GetNumAtoms())
                ]
        except ImportError:
            pass
        except Exception as exc:
            log.debug("RDKit 坐标读取失败，改用 OpenBabel：%s", exc)
        adapter = obabel or get_openbabel()
        if adapter.available:
            with work_directory("box_coords") as temp:
                converted = temp / "coords.pdb"
                if adapter.convert(path, converted, out_format="pdb")[0]:
                    atoms = pdbio.parse_atoms(converted)
                    return [atom.coord for atom in atoms if not atom.is_hydrogen]
    raise InputError(f"无法从文件中读取坐标：{path.name}")


def _ligand_required_sizes(ligand_paths: Iterable[str | Path]) -> tuple[float, float, float] | None:
    spans: list[tuple[float, float, float]] = []
    for path in ligand_paths:
        try:
            coords = structure_coords(path)
            _, size = pdbio.bounds_center_size(coords)
            spans.append(size)
        except Exception:
            continue
    if not spans:
        return None
    return tuple(max(span[i] for span in spans) + 2.0 * LIGAND_MARGIN for i in range(3))  # type: ignore[return-value]


def _co_crystal_sites(
    source: Path,
    chains: Sequence[str],
    obabel: OpenBabelAdapter,
) -> list[pdbio.LigandSite]:
    """返回所有可用的共晶配体，已按链过滤并按重原子数降序。"""
    if pdbio.detect_format(source) == "cif":
        if not obabel.available:
            return []
        with work_directory("box_cif") as temp:
            converted = temp / "converted.pdb"
            if not obabel.convert(source, converted, out_format="pdb")[0]:
                return []
            return _filter_sites(pdbio.find_ligand_sites(converted), chains)
    return _filter_sites(pdbio.find_ligand_sites(source), chains)


def _filter_sites(
    sites: list[pdbio.LigandSite],
    chains: Sequence[str],
) -> list[pdbio.LigandSite]:
    if chains:
        allowed = set(chains)
        sites = [site for site in sites if not site.chain or site.chain in allowed]
    return sites


@dataclass
class _SiteSpec:
    coords: list[tuple[float, float, float]]
    labels: str


def _site_specs(source: Path, chains: Sequence[str]) -> list[_SiteSpec]:
    """返回所有可用的 PDB SITE 位点及其残基坐标。"""
    sites = pdbio.parse_site_records(source)
    if not sites:
        return []
    if chains:
        allowed = set(chains)
        sites = [
            site
            for site in sites
            if any(not residue.chain or residue.chain in allowed for residue in site)
        ]
    specs: list[_SiteSpec] = []
    for site in sites:
        coords = pdbio.site_residue_coords(source, site)
        if len(coords) < 2:
            continue
        labels = "、".join(
            f"{residue.resname}{residue.chain}{residue.resseq}" for residue in site[:6]
        )
        if len(site) > 6:
            labels += "…"
        specs.append(_SiteSpec(coords=coords, labels=labels))
    return specs


def _site_box(spec: _SiteSpec, settings: AppSettings) -> tuple[Box, list[str]]:
    center, size = pdbio.bounds_center_size(spec.coords, padding=SITE_PADDING)
    return _sanitize_box(
        center,
        size,
        settings,
        source="site",
        note=f"根据 PDB SITE 记录（{spec.labels}）确定",
    )


def _co_crystal_box(
    site: pdbio.LigandSite,
    required: tuple[float, float, float] | None,
    settings: AppSettings,
) -> tuple[Box, list[str]]:
    padding = max(0.0, float(settings.box_padding))
    size = tuple(site.size[i] + 2.0 * padding for i in range(3))
    if required is not None:
        size = _elementwise_max(size, required)
    box, warnings = _sanitize_box(
        site.center,
        size,
        settings,
        source="cocrystal",
        note=f"检测到共晶配体 {site.label}，以其为中心",
        reference=site.resname,
    )
    _check_ligand_fit(box, required, warnings)
    return box, warnings


def _pocket_note(pocket: Pocket, prefix: str) -> str:
    return (
        f"{prefix}（埋藏度 {pocket.enclosure:.2f}，"
        f"体积 {pocket.volume:,.0f} Å³，"
        f"口袋残基 {pocket.residue_text}）"
    )


def _pocket_overlap(
    pocket: Pocket,
    coords: Sequence[Sequence[float]],
    *,
    margin: float = 5.0,
) -> float:
    """实验位点原子与真实口袋点的重合比例。

    优先用口袋采样点而不是包围盒：细长口袋的包围盒可能覆盖大片空区域，
    仅凭包围盒会高估重合度。缺少采样点时退回包围盒判断。
    """
    if not coords:
        return 0.0
    if pocket.coords:
        try:
            import numpy as np
            from scipy.spatial import cKDTree

            points = np.asarray(pocket.coords, dtype=float)
            targets = np.asarray(coords, dtype=float)
            distances = cKDTree(points).query(targets, k=1)[0]
            return float(np.mean(distances <= margin))
        except Exception as exc:  # pragma: no cover - 依赖缺失时退回包围盒
            log.debug("口袋点重合计算失败，改用包围盒：%s", exc)
    bbox_margin = min(margin, 3.0)
    low = [pocket.center[i] - pocket.size[i] / 2.0 for i in range(3)]
    high = [pocket.center[i] + pocket.size[i] / 2.0 for i in range(3)]
    hits = 0
    for coord in coords:
        squared = 0.0
        for i in range(3):
            value = float(coord[i])
            if value < low[i] - bbox_margin:
                squared += (low[i] - bbox_margin - value) ** 2
            elif value > high[i] + bbox_margin:
                squared += (value - high[i] - bbox_margin) ** 2
        if squared <= 1e-6:
            hits += 1
    return hits / len(coords)


def _select_consensus_pocket(
    pockets: Sequence[Pocket],
    coords: Sequence[Sequence[float]],
    threshold: float,
) -> tuple[Pocket | None, float]:
    best: Pocket | None = None
    best_key = (-1.0, -1.0)
    best_coverage = 0.0
    for pocket in pockets:
        coverage = _pocket_overlap(pocket, coords)
        key = (coverage * pocket.score, pocket.score)
        if coverage >= threshold and key > best_key:
            best = pocket
            best_key = key
            best_coverage = coverage
    return best, best_coverage


def _best_consensus(
    evidence: Sequence[tuple[Any, Sequence[Sequence[float]]]],
    pockets: Sequence[Pocket],
    threshold: float,
) -> tuple[Any, Pocket, float] | None:
    """在多个实验位点中挑选与几何口袋最吻合的一组。"""
    best: tuple[Any, Pocket, float] | None = None
    best_key = (-1.0, -1.0)
    for item, coords in evidence:
        pocket, coverage = _select_consensus_pocket(pockets, coords, threshold)
        if pocket is None:
            continue
        key = (coverage * pocket.score, pocket.score)
        if key > best_key:
            best = (item, pocket, coverage)
            best_key = key
    return best


def _pocket_box(
    pocket: Pocket,
    settings: AppSettings,
    required: tuple[float, float, float] | None,
    *,
    note: str,
    reference: str = "",
    evidence_coords: Sequence[Sequence[float]] = (),
) -> tuple[Box, list[str]]:
    """把口袋（可合并实验位点坐标）转换为对接盒子。

    与实验位点联合时，口袋只保留实验位点附近 ``EVIDENCE_POCKET_REACH``
    范围内的部分。细长的溶剂通道会让口袋包围盒明显膨胀，裁掉远端既能
    保留口袋信息，又能避免对接搜索空间过大。
    """
    if evidence_coords:
        low = [pocket.center[i] - pocket.size[i] / 2.0 for i in range(3)]
        high = [pocket.center[i] + pocket.size[i] / 2.0 for i in range(3)]
        evidence_low = [
            min(float(coord[i]) for coord in evidence_coords) for i in range(3)
        ]
        evidence_high = [
            max(float(coord[i]) for coord in evidence_coords) for i in range(3)
        ]
        low = [
            min(evidence_low[i], max(low[i], evidence_low[i] - EVIDENCE_POCKET_REACH))
            for i in range(3)
        ]
        high = [
            max(evidence_high[i], min(high[i], evidence_high[i] + EVIDENCE_POCKET_REACH))
            for i in range(3)
        ]
        center = tuple((low[i] + high[i]) / 2.0 for i in range(3))
        size = tuple(high[i] - low[i] + 2.0 * POCKET_MARGIN for i in range(3))
    else:
        center = pocket.center
        size = tuple(value + 2.0 * POCKET_MARGIN for value in pocket.size)
    if required is not None:
        size = _elementwise_max(size, required)
    box, warnings = _sanitize_box(
        center,
        size,
        settings,
        source="pocket",
        note=note,
        reference=reference,
    )
    _check_ligand_fit(box, required, warnings)
    return box, warnings


def _pocket_candidates(
    source: Path,
    target_prepared_pdb: str | Path | None,
    chains: Sequence[str],
    obabel: OpenBabelAdapter,
) -> list[Pocket]:
    """优先用修复后的受体结构做几何检测。"""
    prepared = Path(target_prepared_pdb) if target_prepared_pdb else None
    path = prepared if prepared is not None and prepared.is_file() else source
    return detect_pockets_cached(path, chains=chains, obabel=obabel)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def suggest_box(
    *,
    target_source: str | Path,
    target_prepared_pdb: str | Path | None,
    ligand_paths: Sequence[str | Path] = (),
    settings: AppSettings | None = None,
    target_kind: str = "macromolecule",
    chains: Sequence[str] = (),
    manual: Box | None = None,
    reference_path: str | Path | None = None,
    obabel: OpenBabelAdapter | None = None,
) -> tuple[Box, list[str]]:
    """计算推荐盒子。返回 (Box, 警告列表)。"""
    settings = settings or AppSettings()
    source = Path(target_source)
    obabel = obabel or get_openbabel()
    warnings: list[str] = []
    required = _ligand_required_sizes(ligand_paths)

    if manual is not None:
        size = list(manual.size)
        if required is not None:
            size = list(_elementwise_max(size, required))
        box, extra = _sanitize_box(
            manual.center, size, settings, source="manual", note="用户手动设置"
        )
        warnings.extend(extra)
        _check_ligand_fit(box, required, warnings)
        return box, warnings

    if target_kind == "small_molecule":
        coords = structure_coords(target_prepared_pdb or source, obabel)
        center, span = pdbio.bounds_center_size(coords)
        ligand_size = required or (0.0, 0.0, 0.0)
        size = tuple(span[i] + ligand_size[i] + 2.0 * LIGAND_MARGIN for i in range(3))
        box, extra = _sanitize_box(
            center, size, settings, source="ligand", note="以目标分子为中心，覆盖两侧分子"
        )
        warnings.extend(extra)
        warnings.append("分子-分子对接的盒子按两个分子的尺寸自动估计，建议检查结果")
        _check_ligand_fit(box, required, warnings)
        return box, warnings

    if reference_path:
        coords = structure_coords(reference_path, obabel)
        center, span = pdbio.bounds_center_size(coords, padding=settings.box_padding)
        size = list(span)
        if required is not None:
            size = list(_elementwise_max(size, required))
        box, extra = _sanitize_box(
            center,
            size,
            settings,
            source="reference",
            note="以参考分子所在位置为结合位点",
            reference=Path(reference_path).name,
        )
        warnings.extend(extra)
        _check_ligand_fit(box, required, warnings)
        return box, warnings

    # 几何口袋检测：没有实验位点时提供科学的自动候选。
    try:
        pockets = _pocket_candidates(source, target_prepared_pdb, chains, obabel)
    except Exception as exc:
        pockets = []
        log.warning("几何口袋检测失败：%s", exc)
        warnings.append(f"几何口袋检测失败：{exc}")

    # 共晶配体（几何口袋不可用或与之不重叠时兜底）
    co_crystal_sites: list[pdbio.LigandSite] = []
    try:
        co_crystal_sites = _co_crystal_sites(source, chains, obabel)
    except Exception as exc:
        warnings.append(f"共晶配体检测失败：{exc}")
    if co_crystal_sites:
        # 以最大的共晶配体为主；多拷贝结构（如多条链各有一个配体）允许在
        # 同名配体中选择与几何口袋最吻合的那一份。
        primary = co_crystal_sites[0]
        same_ligand = [
            site for site in co_crystal_sites if site.resname == primary.resname
        ]
        consensus = _best_consensus(
            [
                (
                    site,
                    [atom.coord for atom in site.atoms if not atom.is_hydrogen],
                )
                for site in same_ligand
            ],
            pockets,
            CONSENSUS_COVERAGE,
        )
        if consensus is not None:
            co_crystal, pocket, _coverage = consensus
            evidence = [
                atom.coord for atom in co_crystal.atoms if not atom.is_hydrogen
            ]
            prefix = f"几何口袋与共晶配体 {co_crystal.resname} 一致"
            if co_crystal.chain:
                prefix += f"（链 {co_crystal.chain}）"
            note = _pocket_note(pocket, prefix)
            box, extra = _pocket_box(
                pocket,
                settings,
                required,
                note=note,
                reference=co_crystal.resname,
                evidence_coords=evidence,
            )
            warnings.extend(extra)
            return box, warnings
        if pockets:
            warnings.append("几何口袋未与共晶配体重叠，已回退到共晶配体位置")
        elif not warnings:
            warnings.append("未检测到可靠几何口袋，使用共晶配体位置")
        box, extra = _co_crystal_box(primary, required, settings)
        warnings.extend(extra)
        return box, warnings

    # PDB SITE 记录（共晶配体之后的实验位点兜底）
    site_specs = _site_specs(source, chains)
    if site_specs:
        consensus = _best_consensus(
            [(spec, spec.coords) for spec in site_specs],
            pockets,
            SITE_CONSENSUS_COVERAGE,
        )
        if consensus is not None:
            site_spec, pocket, _coverage = consensus
            note = _pocket_note(pocket, "几何口袋与 PDB SITE 位点一致")
            box, extra = _pocket_box(
                pocket,
                settings,
                required,
                note=note,
                reference=site_spec.labels,
                evidence_coords=site_spec.coords,
            )
            warnings.extend(extra)
            return box, warnings
        box, extra = _site_box(site_specs[0], settings)
        if required is not None:
            size = _elementwise_max(box.size, required)
            box, extra2 = _sanitize_box(
                box.center, size, settings, source="site", note=box.note
            )
            extra.extend(extra2)
            _check_ligand_fit(box, required, extra)
        if pockets:
            warnings.append("几何口袋未与 PDB SITE 位点重叠，已回退到 SITE 记录")
        elif not warnings:
            warnings.append("未检测到可靠几何口袋，使用 PDB SITE 位点")
        warnings.extend(extra)
        return box, warnings

    # 没有实验位点时，使用几何口袋。体积过小的空腔无法容纳候选分子，
    # 即使包埋度很高也不能作为自动盒子。
    usable = [pocket for pocket in pockets if pocket.volume >= MIN_POCKET_VOLUME]
    if usable and usable[0].score >= MIN_POCKET_SCORE:
        best = usable[0]
        note = _pocket_note(best, "几何口袋检测")
        box, extra = _pocket_box(best, settings, required, note=note)
        warnings.extend(extra)
        return box, warnings
    if usable:
        warnings.append(
            f"几何口袋置信度较低（{usable[0].score:.2f}），"
            "建议检查推荐盒子或手动设置结合位点。"
        )
    elif pockets:
        warnings.append(
            f"检测到的口袋体积过小（最大 {pockets[0].volume:.0f} Å³），"
            "不足以容纳候选分子，已改用全蛋白盲对接。"
        )

    # 盲对接
    coords = structure_coords(source, obabel)
    center, span = pdbio.bounds_center_size(coords)
    size = tuple(span[i] + 2.0 * settings.box_padding for i in range(3))
    if required is not None:
        size = _elementwise_max(size, required)
    box, extra = _sanitize_box(
        center,
        size,
        settings,
        source="blind",
        note="未检测到可靠的结合位点信息，使用全蛋白盲对接",
    )
    warnings.extend(extra)
    warnings.append(
        "未检测到可靠的几何口袋、共晶配体或 SITE 记录，已使用全蛋白盲对接。"
        "盲对接结果仅适合初步筛选；若有已知结合位点，请手动设置盒子中心。"
    )
    if len(chains) > 1:
        warnings.append("结构包含多条链，盲对接盒子覆盖了全部链")
    _check_ligand_fit(box, required, warnings)
    return box, warnings


def _check_ligand_fit(box: Box, required: tuple[float, float, float] | None, warnings: list[str]) -> None:
    if required is None:
        return
    if any(box.size[i] + 1e-6 < required[i] for i in range(3)):
        warnings.append(
            "盒子尺寸小于“配体尺寸 + 安全边距”，配体可能无法充分探索结合构象。"
            "建议在设置中提高盒子最大尺寸，或手动扩大盒子。"
        )


def validate_manual_box(center: Sequence[float], size: Sequence[float]) -> Box:
    """校验界面输入的手动盒子。"""
    try:
        center_t = tuple(float(v) for v in center)
        size_t = tuple(float(v) for v in size)
    except (TypeError, ValueError) as exc:
        raise InputError("盒子参数必须是数字") from exc
    if len(center_t) != 3 or len(size_t) != 3:
        raise InputError("盒子需要三个中心坐标和三个边长")
    if any(not math.isfinite(v) for v in center_t + size_t):
        raise InputError("盒子参数包含无效数值")
    if any(v < 5 for v in size_t):
        raise InputError("盒子边长至少为 5 Å")
    if any(v > 150 for v in size_t):
        raise InputError("盒子边长最多为 150 Å")
    return Box(
        center=tuple(round(v, 3) for v in center_t),  # type: ignore[arg-type]
        size=tuple(round(v, 2) for v in size_t),  # type: ignore[arg-type]
        source="manual",
        note="用户手动设置",
    )
