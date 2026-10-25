"""基于 PLIP 的蛋白–配体相互作用分析。

PLIP（Protein-Ligand Interaction Profiler）以 GPL-2.0 发布：
https://github.com/pharmai/plip 。它使用 OpenBabel 解析结构并自动补极性氢，
能给出氢键、疏水作用、盐桥、π-堆积、π-阳离子、卤键和金属配位等相互作用；
范德华接触不是 PLIP 的输出类型，仍由
:mod:`dockflowlab.services.interactions` 的几何判定补充。PLIP 对金属配体
基团的识别不完整（例如磺酰胺氮），因此几何判定还会补充配体–金属接触。

PLIP 是可选依赖：未安装、结构无法识别或分析抛错时，自动回退到纯几何判定，
界面仍然可用。
"""

from __future__ import annotations

import importlib.util
import logging
import tempfile
from pathlib import Path
from typing import Any

from ..core import pdbio
from ..tools.obabel import configure_environment
from .interactions import (
    KIND_LABELS,
    KIND_PRIORITY,
    Interaction,
    InteractionAnalysis,
    analyze_complex,
    find_ligand_atoms,
)

log = logging.getLogger("plip_analysis")


def plip_available() -> bool:
    """检测 PLIP 是否可以导入；不触发 OpenBabel 初始化。"""
    try:
        return importlib.util.find_spec("plip") is not None
    except (ImportError, ValueError):
        return False


# ----------------------------------------------------------------------
# PLIP 记录 -> Interaction
# ----------------------------------------------------------------------
def _coords(value: Any) -> tuple[float, float, float]:
    values = getattr(value, "coords", value)
    return (
        float(values[0]),
        float(values[1]),
        float(values[2]),
    )


def _atom_name(atom: Any) -> str:
    try:
        obatom = atom.OBAtom
        name = obatom.GetResidue().GetAtomID(obatom).strip()
        if name:
            return name
    except Exception:  # pragma: no cover - pybel 版本差异
        pass
    try:
        return str(atom.type)
    except Exception:  # pragma: no cover
        return "?"


def _group_name(group: Any) -> str:
    atoms = getattr(group, "atoms", None)
    if atoms:
        try:
            return _atom_name(atoms[0])
        except Exception:  # pragma: no cover
            pass
    return str(getattr(group, "type", "?"))


def _residue_fields(record: Any) -> tuple[str, int, str]:
    resname = str(getattr(record, "restype", "") or "").strip().upper() or "UNK"
    raw = getattr(record, "resnr", 0)
    try:
        resseq = int(raw)
    except (TypeError, ValueError):
        resseq = 0
    chain = str(getattr(record, "reschain", "") or "").strip()
    return resname, resseq, chain


def _distance(record: Any, *names: str) -> float:
    for name in names:
        try:
            value = float(getattr(record, name))
        except (AttributeError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _make(
    record: Any,
    kind: str,
    protein_atom: Any,
    ligand_atom: Any,
    distance: float,
    *,
    protein_name: str | None = None,
    ligand_name: str | None = None,
) -> Interaction:
    resname, resseq, chain = _residue_fields(record)
    return Interaction(
        kind=kind,
        residue_label=f"{resname}{resseq}",
        chain=chain,
        resseq=resseq,
        icode="",
        resname=resname,
        protein_atom=protein_name or _atom_name(protein_atom),
        ligand_atom=ligand_name or _atom_name(ligand_atom),
        protein_coord=_coords(protein_atom),
        ligand_coord=_coords(ligand_atom),
        distance=float(distance),
    )


def _hbond(record: Any) -> Interaction:
    # hbonds_ldon: protisdon=False（配体供体）；hbonds_pdon: protisdon=True。
    if bool(getattr(record, "protisdon", False)):
        protein_atom, ligand_atom = record.d, record.a
    else:
        protein_atom, ligand_atom = record.a, record.d
    return _make(
        record,
        "hbond",
        protein_atom,
        ligand_atom,
        _distance(record, "distance_ad", "distance_ah", "distance"),
    )


def _hydrophobic(record: Any) -> Interaction:
    return _make(
        record,
        "hydrophobic",
        record.bsatom,
        record.ligatom,
        _distance(record, "distance"),
    )


def _saltbridge(record: Any) -> Interaction:
    if bool(getattr(record, "protispos", True)):
        protein_group, ligand_group = record.positive, record.negative
    else:
        protein_group, ligand_group = record.negative, record.positive
    return _make(
        record,
        "saltbridge",
        protein_group.center,
        ligand_group.center,
        _distance(record, "distance"),
        protein_name=_group_name(protein_group),
        ligand_name=_group_name(ligand_group),
    )


def _pistacking(record: Any) -> Interaction:
    return _make(
        record,
        "pistacking",
        record.proteinring.center,
        record.ligandring.center,
        _distance(record, "distance"),
        protein_name="环中心",
        ligand_name="环中心",
    )


def _pication(record: Any) -> Interaction:
    if bool(getattr(record, "protcharged", False)):
        protein_group, ligand_group = record.charge, record.ring
        protein_name, ligand_name = _group_name(record.charge), "环中心"
    else:
        protein_group, ligand_group = record.ring, record.charge
        protein_name, ligand_name = "环中心", _group_name(record.charge)
    return _make(
        record,
        "pication",
        protein_group.center,
        ligand_group.center,
        _distance(record, "distance"),
        protein_name=protein_name,
        ligand_name=ligand_name,
    )


def _halogen(record: Any) -> Interaction:
    return _make(
        record,
        "halogen",
        record.acc.o,
        record.don.x,
        _distance(record, "distance"),
    )


def _metal(record: Any) -> Interaction | None:
    """把 PLIP 的 metal_complex 转成配体–金属接触。

    PLIP 的 ``metal_complexes`` 同时包含金属与蛋白残基、水、配体的配位。
    这里只保留目标原子属于配体的记录；金属–蛋白配位不是配体相互作用，
    放在“蛋白–配体相互作用”列表里会造成误解。
    """
    target = getattr(record, "target", None)
    metal = getattr(record, "metal", None)
    if target is None or metal is None:
        return None
    if str(getattr(target, "location", "")).strip().lower() != "ligand":
        return None
    target_atom = getattr(target, "atom", target)

    # restype_l/resnr_l/reschain_l 描述的是金属离子本身，而不是被配位的
    # 配体原子；列表里以 ZN301 这样的标签展示金属更直观。
    resname = str(getattr(record, "restype_l", "") or "").strip().upper() or "MET"
    raw_resseq = getattr(record, "resnr_l", 0)
    try:
        resseq = int(raw_resseq)
    except (TypeError, ValueError):
        resseq = 0
    chain = str(getattr(record, "reschain_l", "") or "").strip()

    return Interaction(
        kind="metal",
        residue_label=f"{resname}{resseq}",
        chain=chain,
        resseq=resseq,
        icode="",
        resname=resname,
        protein_atom=_atom_name(metal),
        ligand_atom=_atom_name(target_atom),
        protein_coord=_coords(metal),
        ligand_coord=_coords(target_atom),
        distance=_distance(record, "distance"),
    )


_CONVERTERS = {
    "hbond_ldon": (_hbond, "hbonds_ldon"),
    "hbond_pdon": (_hbond, "hbonds_pdon"),
    "hydrophobic": (_hydrophobic, "hydrophobic_contacts"),
    "saltbridge_lneg": (_saltbridge, "saltbridge_lneg"),
    "saltbridge_pneg": (_saltbridge, "saltbridge_pneg"),
    "pistacking": (_pistacking, "pistacking"),
    "pication_laro": (_pication, "pication_laro"),
    "pication_paro": (_pication, "pication_paro"),
    "halogen": (_halogen, "halogen_bonds"),
    "metal": (_metal, "metal_complexes"),
}


# ----------------------------------------------------------------------
# 站点选择与分析
# ----------------------------------------------------------------------
def _select_site(complex_obj: Any, complex_path: Path) -> Any | None:
    """选出对接配体对应的 PLIP 站点。

    PLIP 会把离子、辅因子也识别成配体；这里优先匹配复合物里的 ``LIG``
    配体（链和残基名同时匹配），匹配不到再退回同残基名的站点。
    """
    try:
        atoms = pdbio.parse_atoms(complex_path, first_model=True)
    except Exception:  # pragma: no cover - 文件损坏时交给调用方回退
        atoms = []
    ligand = find_ligand_atoms(atoms) if atoms else []
    ligand_resnames = {atom.resname.upper() for atom in ligand if atom.resname}
    ligand_chains = {atom.chain for atom in ligand if atom.chain}

    candidates: list[tuple[str, Any, str, str]] = []
    for key, site in complex_obj.interaction_sets.items():
        parts = str(key).split(":")
        hetid = parts[0].strip().upper()
        chain = parts[1].strip() if len(parts) > 1 else ""
        if hetid in pdbio.WATERS or hetid in pdbio.IONS:
            continue
        candidates.append((str(key), site, hetid, chain))
    if not candidates:
        return None

    for key, site, hetid, chain in candidates:
        if hetid in ligand_resnames and (
            not ligand_chains or chain in ligand_chains
        ):
            return site
    for key, site, hetid, chain in candidates:
        if hetid in ligand_resnames:
            return site
    return None


def _prepare_complex_text(text: str) -> str:
    """整理交给 PLIP 的结构文本：规范元素列并去掉已有氢原子。

    * 旧版复合物把 AutoDock 原子类型写进元素列（芳香碳为 ``A``、受体氧为
      ``OA``）。OpenBabel 会把 ``A`` 当成未知元素，盐桥与疏水作用随之漏判。
    * PLIP 自己补的氢与它的供体判定一致；沿用结构里已有的氢（尤其是
      PDBFixer 生成的氢）时，供体角度容易失真，会漏掉本应识别出的氢键。
    """
    lines: list[str] = []
    for line in text.splitlines():
        if line[:6] in {"ATOM  ", "HETATM"}:
            raw = line[76:78].strip() if len(line) >= 78 else ""
            name = line[12:16].strip()
            resname = line[17:20].strip()
            element = pdbio.normalize_element(
                raw, name, resname, line[:6].strip()
            )
            if element.upper() in {"H", "D"}:
                continue
            line = line[:76] + f"{element[:2].upper():>2}" + line[78:]
        lines.append(line)
    return "\n".join(lines) + "\n"


def _plip_interactions(complex_path: Path) -> list[Interaction] | None:
    """运行 PLIP 并转换结果；无法识别配体时返回 ``None``。"""
    # 必须在 import openbabel / plip 之前设置插件目录。
    configure_environment()
    from plip.structure.preparation import PDBComplex

    text = complex_path.read_text(encoding="utf-8", errors="replace")
    # PLIP 会把补氢后的结构写到 output_path；放到临时目录，避免污染工作区。
    with tempfile.TemporaryDirectory(prefix="dfl_plip_") as tmp:
        complex_obj = PDBComplex()
        complex_obj.output_path = tmp
        complex_obj.load_pdb(_prepare_complex_text(text), as_string=True)
        complex_obj.analyze()

        site = _select_site(complex_obj, complex_path)
        if site is None:
            return None

        interactions: list[Interaction] = []
        for _, (converter, attribute) in _CONVERTERS.items():
            records = getattr(site, attribute, None) or []
            for record in records:
                try:
                    converted = converter(record)
                except Exception as exc:  # pragma: no cover - 单条记录异常不影响整体
                    log.debug("跳过无法转换的 PLIP 记录 %s：%s", attribute, exc)
                    continue
                if converted is not None:
                    interactions.append(converted)
        return interactions


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------
def _dedupe_and_limit(
    items: list[Interaction],
    *,
    max_interactions: int,
) -> tuple[list[Interaction], int]:
    best: dict[tuple[str, int, str, str], tuple[tuple[int, float], Interaction]] = {}
    for item in items:
        key = (item.chain, item.resseq, item.icode, item.resname)
        rank = (KIND_PRIORITY.get(item.kind, 99), item.distance)
        previous = best.get(key)
        if previous is None or rank < previous[0]:
            best[key] = (rank, item)

    ordered = sorted(
        (value for _, value in best.values()),
        key=lambda item: (
            KIND_PRIORITY.get(item.kind, 99),
            item.distance,
            item.residue_label,
        ),
    )
    total = len(ordered)
    limit = max(0, max_interactions)
    return ordered[:limit], total


def _with_geometry(
    items: list[Interaction],
    geometric: InteractionAnalysis,
) -> list[Interaction]:
    """用几何判定补上范德华接触和漏检的金属配位。

    范德华接触不是 PLIP 的输出类型；PLIP 的金属结合基团规则也不完整
    （例如不识别磺酰胺氮）。未与 PLIP 结果覆盖同一残基的这两类几何接触
    会补进来。
    """
    covered = {
        (item.chain, item.resseq, item.icode, item.resname) for item in items
    }
    for item in geometric.interactions:
        if item.kind not in {"vdw", "metal"}:
            continue
        key = (item.chain, item.resseq, item.icode, item.resname)
        if key in covered:
            continue
        covered.add(key)
        items.append(item)
    return items


def analyze_complex_precise(
    complex_path: str | Path,
    *,
    max_interactions: int = 12,
) -> InteractionAnalysis:
    """优先用 PLIP 分析复合物，失败时回退到几何判定。

    返回结果始终是 :class:`InteractionAnalysis`；``engine`` 字段标明实际
    使用的分析方式（``"plip"`` / ``"geometric"``）。
    """
    path = Path(complex_path)
    if not path.is_file():
        return analyze_complex(path, max_interactions=max_interactions)
    if not plip_available():
        return analyze_complex(path, max_interactions=max_interactions)

    try:
        items = _plip_interactions(path)
    except Exception as exc:
        log.warning("PLIP 分析失败，回退到几何判定：%s", exc)
        result = analyze_complex(path, max_interactions=max_interactions)
        result.warnings.append("PLIP 分析失败，已回退到本地几何判定")
        return result

    if items is None:
        result = analyze_complex(path, max_interactions=max_interactions)
        result.warnings.append("PLIP 未识别到对接配体，已回退到本地几何判定")
        return result

    # 几何分析同时负责配体识别、选择表达式和范德华/疏水补充。
    geometric = analyze_complex(path, max_interactions=max(200, max_interactions))
    items = _with_geometry(items, geometric)
    shown, total = _dedupe_and_limit(items, max_interactions=max_interactions)
    counts = {kind: 0 for kind in KIND_LABELS}
    for item in shown:
        counts[item.kind] = counts.get(item.kind, 0) + 1

    warnings: list[str] = []
    if not shown:
        warnings.append("PLIP 未检测到可展示的相互作用")
    if total > len(shown):
        warnings.append(
            f"共检测到 {total} 个残基接触，仅展示最接近的 {len(shown)} 条"
        )

    return InteractionAnalysis(
        interactions=shown,
        counts=counts,
        total_residues=total,
        truncated=total > len(shown),
        ligand_selection=geometric.ligand_selection,
        ligand_atoms=geometric.ligand_atoms,
        receptor_residues=geometric.receptor_residues,
        warnings=warnings,
        engine="plip",
    )
