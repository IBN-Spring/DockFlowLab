"""对接盒子（搜索空间）计算。

优先级（从最可靠到最粗略）：
1. 用户手动设置
2. 用户提供的参考分子（如共晶配体）
3. 原始结构中的共晶小分子
4. PDB SITE 记录
5. 全蛋白盲对接
6. 小分子靶点：以靶点分子为中心

盒子会自动扩张以容纳所有待对接配体，并限制在科学合理的范围内。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..config import AppSettings
from ..core import pdbio
from ..core.models import Box
from ..errors import InputError
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import work_directory

log = get_logger("boxes")

LIGAND_MARGIN = 4.0  # 配体边界到盒子壁的最小距离（Å）
SITE_PADDING = 6.0
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
        return [atom.coord for atom in atoms if not atom.is_hydrogen]
    if fmt in {"sdf", "mol", "mol2"}:
        try:
            from rdkit import Chem

            if fmt == "sdf":
                supplier = Chem.SDMolSupplier(str(path), removeHs=True, sanitize=True)
                mol = next((candidate for candidate in supplier if candidate is not None), None)
            elif fmt == "mol":
                mol = Chem.MolFromMolFile(str(path), removeHs=True)
            else:
                mol = Chem.MolFromMol2File(str(path), removeHs=True)
            if mol is not None and mol.GetNumConformers() > 0:
                conformer = mol.GetConformer(0)
                return [
                    (conformer.GetAtomPosition(i).x, conformer.GetAtomPosition(i).y, conformer.GetAtomPosition(i).z)
                    for i in range(mol.GetNumAtoms())
                ]
        except ImportError:
            pass
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


def _co_crystal_site(source: Path, chains: Sequence[str], obabel: OpenBabelAdapter) -> pdbio.LigandSite | None:
    if pdbio.detect_format(source) == "cif":
        if not obabel.available:
            return None
        with work_directory("box_cif") as temp:
            converted = temp / "converted.pdb"
            if not obabel.convert(source, converted, out_format="pdb")[0]:
                return None
            return _select_site(pdbio.find_ligand_sites(converted), chains)
    return _select_site(pdbio.find_ligand_sites(source), chains)


def _select_site(sites: list[pdbio.LigandSite], chains: Sequence[str]) -> pdbio.LigandSite | None:
    if chains:
        allowed = set(chains)
        sites = [site for site in sites if not site.chain or site.chain in allowed]
    return sites[0] if sites else None


def _site_box(source: Path, chains: Sequence[str], settings: AppSettings) -> tuple[Box, list[str]] | None:
    sites = pdbio.parse_site_records(source)
    if not sites:
        return None
    if chains:
        allowed = set(chains)
        sites = [
            site for site in sites
            if any(not residue.chain or residue.chain in allowed for residue in site)
        ]
    for site in sites:
        coords = pdbio.site_residue_coords(source, site)
        if len(coords) < 2:
            continue
        center, size = pdbio.bounds_center_size(coords, padding=SITE_PADDING)
        labels = "、".join(
            f"{residue.resname}{residue.chain}{residue.resseq}" for residue in site[:6]
        )
        if len(site) > 6:
            labels += "…"
        box, warnings = _sanitize_box(
            center,
            size,
            settings,
            source="site",
            note=f"根据 PDB SITE 记录（{labels}）确定",
        )
        return box, warnings
    return None


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

    # 共晶配体
    try:
        site = _co_crystal_site(source, chains, obabel)
    except Exception as exc:
        site = None
        warnings.append(f"共晶配体检测失败：{exc}")
    if site is not None:
        padding = max(0.0, float(settings.box_padding))
        size = tuple(site.size[i] + 2.0 * padding for i in range(3))
        if required is not None:
            size = _elementwise_max(size, required)
        box, extra = _sanitize_box(
            site.center,
            size,
            settings,
            source="cocrystal",
            note=f"检测到共晶配体 {site.label}，以其为中心",
            reference=site.resname,
        )
        warnings.extend(extra)
        _check_ligand_fit(box, required, warnings)
        return box, warnings

    # SITE 记录
    site_box = _site_box(source, chains, settings)
    if site_box is not None:
        box, extra = site_box
        if required is not None:
            size = _elementwise_max(box.size, required)
            box, extra2 = _sanitize_box(
                box.center, size, settings, source="site", note=box.note
            )
            extra.extend(extra2)
            _check_ligand_fit(box, required, extra)
        warnings.extend(extra)
        return box, warnings

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
        note="未检测到结合位点信息，使用全蛋白盲对接",
    )
    warnings.extend(extra)
    warnings.append(
        "未检测到共晶配体或 SITE 记录，已使用全蛋白盲对接。"
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
