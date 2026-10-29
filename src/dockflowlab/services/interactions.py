"""对接复合物的蛋白–配体相互作用分析。

这里分析的是**几何接触**，不是量子化学或实验测定的相互作用：

* 氢键：两侧都是 N/O/S，且至少一侧是 N/O，重原子距离 ≤ 3.6 Å；
* 金属配位：受体金属离子与配体 N/O/S 距离 ≤ 3.0 Å；
* 疏水作用：两侧都是 C/S，距离 ≤ 4.4 Å；
* 范德华接触：其余重原子对，距离 ≤ 4.2 Å。

没有氢原子坐标时无法计算 D–H…A 角度，因此氢键只按重原子距离和元素类型
判定。这里的几何判定是 PLIP 不可用时的回退路径；正常安装会优先使用
:mod:`dockflowlab.services.plip_analysis` 中的 PLIP 分析。每个残基只保留
一条最具代表性的接触，避免几十条线把视图淹没。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..core import pdbio

HBOND_MAX = 3.6
HYDROPHOBIC_MAX = 4.4
VDW_MAX = 4.2
METAL_MAX = 3.0
MAX_INTERACTIONS = 12

POLAR_ELEMENTS = {"N", "O", "S"}
HYDROPHOBIC_ELEMENTS = {"C", "S"}
METAL_COORD_ELEMENTS = {"N", "O", "S"}

KIND_LABELS = {
    "hbond": "氢键",
    "metal": "金属配位",
    "hydrophobic": "疏水作用",
    "saltbridge": "盐桥",
    "pistacking": "π-堆积",
    "pication": "π-阳离子",
    "halogen": "卤键",
    "vdw": "范德华接触",
}
# 界面上的排列顺序：先保留原有的三类，再展示 PLIP 扩展类型。
KIND_ORDER = (
    "hbond",
    "metal",
    "hydrophobic",
    "saltbridge",
    "pistacking",
    "pication",
    "halogen",
    "vdw",
)
# 几何回退真正会判定的类型（金属配位也由几何规则补充）。
GEOMETRIC_KINDS = ("hbond", "metal", "hydrophobic", "vdw")
# 各类型的判定要点，用作界面提示：某种类型计数为 0 表示本次结构里没有
# 满足条件的接触，而不是没有分析。
KIND_HINTS = {
    "hbond": (
        "供体与受体的 N/O/S 重原子距离符合要求；"
        "对接构象的极性氢未优化，角度只作参考"
    ),
    "metal": "金属离子与配体 N/O/S 原子距离 ≤ 3.0 Å；金属酶建议保留离子后对接",
    "hydrophobic": "疏水或芳香碳原子在 4.0 Å 内相互靠近",
    "saltbridge": "带相反电荷的基团中心距离在 5.5 Å 内",
    "pistacking": "两个芳香环平行或呈 T 型，中心距 3.5–5.5 Å",
    "pication": "带正电基团位于芳香环上方 6 Å 内",
    "halogen": "C–Cl/Br/I 与电负性原子形成方向性接触",
    "vdw": "4.2 Å 内的非特异性重原子接触（几何判定补充）",
}
# 同一残基有多条相互作用时，保留优先级最高的一条。
KIND_PRIORITY = {
    "hbond": 0,
    "metal": 1,
    "saltbridge": 2,
    "pistacking": 3,
    "pication": 4,
    "halogen": 5,
    "hydrophobic": 6,
    "vdw": 7,
}


@dataclass(frozen=True)
class Interaction:
    """一条配体–残基接触。坐标单位均为 Å。"""

    kind: str
    residue_label: str
    chain: str
    resseq: int
    icode: str
    resname: str
    protein_atom: str
    ligand_atom: str
    protein_coord: tuple[float, float, float]
    ligand_coord: tuple[float, float, float]
    distance: float

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def residue_selection(self) -> str:
        """NGL Viewer 的残基选择表达式。

        NGL 不使用 ``resname`` / ``resno`` / ``chainname`` 关键字：残基名写
        裸词，序号写数字，链写 ``:A``。写成 PyMOL 风格会让解析器报错，并
        把表示退化成“全部原子”。
        """
        resno = f"{self.resseq}{'^' + self.icode if self.icode else ''}"
        # 方括号写法同时支持以数字开头或带修饰的残基名（如 1MA）。
        parts = [f"[{self.resname}]", resno]
        if self.chain:
            parts.append(f":{self.chain}")
        return " and ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "kindLabel": self.kind_label,
            "residue": self.residue_label,
            "resname": self.resname,
            "chain": self.chain,
            "resseq": self.resseq,
            "icode": self.icode,
            "proteinAtom": self.protein_atom,
            "ligandAtom": self.ligand_atom,
            "proteinCoord": list(self.protein_coord),
            "ligandCoord": list(self.ligand_coord),
            "distance": round(self.distance, 2),
            "selection": self.residue_selection,
        }


@dataclass
class InteractionAnalysis:
    """一次复合物分析的结果。"""

    interactions: list[Interaction] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total_residues: int = 0
    truncated: bool = False
    ligand_selection: str = ""
    ligand_atoms: int = 0
    receptor_residues: int = 0
    warnings: list[str] = field(default_factory=list)
    # "plip" 表示主体结果由 PLIP 给出，"geometric" 表示本地几何判定。
    engine: str = "geometric"

    @property
    def ok(self) -> bool:
        return bool(self.interactions)

    @property
    def summary(self) -> str:
        if not self.interactions:
            return "未检测到可展示的相互作用"
        parts = [
            f"{KIND_LABELS[key]} {self.counts[key]}"
            for key in KIND_ORDER
            if self.counts.get(key)
        ]
        text = f"{len(self.interactions)} 条相互作用"
        if parts:
            text += " · " + " · ".join(parts)
        if self.truncated:
            text += f"（仅显示前 {len(self.interactions)} 条）"
        return text

    def to_dict(self) -> dict[str, object]:
        return {
            "interactions": [item.to_dict() for item in self.interactions],
            "counts": dict(self.counts),
            "totalResidues": self.total_residues,
            "truncated": self.truncated,
            "ligandSelection": self.ligand_selection,
            "ligandAtoms": self.ligand_atoms,
            "receptorResidues": self.receptor_residues,
            "warnings": list(self.warnings),
            "engine": self.engine,
        }


def _element(atom: pdbio.Atom) -> str:
    return (atom.element or "").strip().upper()


def _ligand_atoms(atoms: Sequence[pdbio.Atom]) -> list[pdbio.Atom]:
    """从复合物中找出对接配体。

    :func:`pdbio.make_complex` 把配体写成 ``LIG`` / 链 ``L``。旧项目或手工
    生成的复合物可能使用别的链，因此再按 ``LIG`` 残基名兜底。
    """
    heavy = [atom for atom in atoms if not atom.is_hydrogen]
    ligand = [
        atom for atom in heavy
        if atom.resname.upper() == "LIG" and atom.chain == "L"
    ]
    if not ligand:
        ligand = [atom for atom in heavy if atom.resname.upper() == "LIG"]
    if ligand:
        return ligand

    # 最后的兜底：只保留非聚合物、非水、非离子的 HETATM。
    return [
        atom for atom in heavy
        if atom.record == "HETATM"
        and not pdbio.is_polymer_residue(atom.resname)
        and atom.resname not in pdbio.WATERS
        and atom.resname not in pdbio.IONS
        and atom.resname not in pdbio.ADDITIVES
    ]


def find_ligand_atoms(atoms: Sequence[pdbio.Atom]) -> list[pdbio.Atom]:
    """公开的配体识别入口，供 PLIP 分析复用。"""
    return _ligand_atoms(atoms)


def _receptor_atoms(
    atoms: Sequence[pdbio.Atom],
    ligand: Sequence[pdbio.Atom],
) -> list[pdbio.Atom]:
    ligand_serials = {atom.serial for atom in ligand}
    heavy = [
        atom for atom in atoms
        if not atom.is_hydrogen and atom.serial not in ligand_serials
    ]
    polymer = [atom for atom in heavy if pdbio.is_polymer_residue(atom.resname)]
    if polymer:
        return polymer
    # 小分子–小分子对接的“受体”本身不是聚合物，此时保留全部非配体原子。
    return heavy


def _receptor_metals(
    atoms: Sequence[pdbio.Atom],
    ligand: Sequence[pdbio.Atom],
) -> list[pdbio.Atom]:
    """找出受体一侧的金属离子；配体自身携带的金属不算。"""
    ligand_serials = {atom.serial for atom in ligand}
    return [
        atom
        for atom in atoms
        if not atom.is_hydrogen
        and atom.serial not in ligand_serials
        and atom.resname.upper() in pdbio.METAL_IONS
    ]


def _ligand_selection(ligand: Sequence[pdbio.Atom]) -> str:
    """生成 NGL 选择表达式。

    注意 NGL 的解析器会把“首尾都是括号”的整个表达式剥掉一层，因此这里
    不能把完整条件包在括号里，否则会得到不配对的括号并把选择退化成全部
    原子。多个残基名用 NGL 的 ``[LIG,UNL]`` 写法，避免嵌套括号。
    """
    resnames = sorted({atom.resname for atom in ligand if atom.resname})
    chains = sorted({atom.chain for atom in ligand if atom.chain})
    if not resnames:
        return ""
    names = "[" + ",".join(resnames) + "]"
    if not chains:
        return names
    if len(chains) == 1:
        return f"{names} and :{chains[0]}"
    return (
        f"{names} and ("
        + " or ".join(f":{chain}" for chain in chains)
        + ")"
    )


def _classify(
    protein_element: str,
    ligand_element: str,
    distance: float,
) -> str | None:
    """按元素和距离给出接触类型；不满足任何条件时返回 ``None``。"""
    if (
        protein_element in POLAR_ELEMENTS
        and ligand_element in POLAR_ELEMENTS
        and (protein_element in {"N", "O"} or ligand_element in {"N", "O"})
        and distance <= HBOND_MAX
    ):
        return "hbond"
    if (
        protein_element in HYDROPHOBIC_ELEMENTS
        and ligand_element in HYDROPHOBIC_ELEMENTS
        and distance <= HYDROPHOBIC_MAX
    ):
        return "hydrophobic"
    if distance <= VDW_MAX:
        return "vdw"
    return None


def analyze_complex(
    complex_path: str | Path,
    *,
    max_interactions: int = MAX_INTERACTIONS,
) -> InteractionAnalysis:
    """分析复合物 PDB 中的蛋白–配体几何接触。

    每个残基仅保留一条代表性接触，优先级为氢键 > 疏水作用 > 范德华接触；
    同一类型内取最短距离。按优先级和距离排序后最多返回
    ``max_interactions`` 条。
    """
    path = Path(complex_path)
    if not path.is_file():
        return InteractionAnalysis(warnings=[f"复合物文件不存在：{path.name}"])

    atoms = pdbio.parse_atoms(path, first_model=True)
    if not atoms:
        return InteractionAnalysis(warnings=["复合物中没有可读取的原子"])

    ligand = _ligand_atoms(atoms)
    if not ligand:
        return InteractionAnalysis(warnings=["没有在复合物中识别到对接配体（LIG）"])
    receptor = _receptor_atoms(atoms, ligand)
    if not receptor:
        return InteractionAnalysis(warnings=["没有在复合物中识别到受体原子"])
    metals = _receptor_metals(atoms, ligand)

    try:
        import numpy as np
        from scipy.spatial import cKDTree
    except ImportError:  # pragma: no cover - 正常安装包含 NumPy/SciPy
        return InteractionAnalysis(
            warnings=["缺少 NumPy/SciPy，无法计算相互作用"]
        )

    protein_xyz = np.asarray([atom.coord for atom in receptor], dtype=float)
    ligand_xyz = np.asarray([atom.coord for atom in ligand], dtype=float)
    tree = cKDTree(protein_xyz)

    # 每个残基 -> 当前最优候选。key 包含链、序号、插入码与残基名，
    # 避免不同链上的同号残基被合并。
    best: dict[tuple[str, int, str, str], tuple[tuple[int, float], Interaction]] = {}
    for ligand_index, ligand_atom in enumerate(ligand):
        ligand_element = _element(ligand_atom)
        if ligand_element not in POLAR_ELEMENTS | HYDROPHOBIC_ELEMENTS:
            continue
        for protein_index in tree.query_ball_point(
            ligand_xyz[ligand_index], r=HYDROPHOBIC_MAX
        ):
            protein_atom = receptor[int(protein_index)]
            protein_element = _element(protein_atom)
            if protein_element not in POLAR_ELEMENTS | HYDROPHOBIC_ELEMENTS:
                continue
            distance = float(math.dist(protein_atom.coord, ligand_atom.coord))
            kind = _classify(protein_element, ligand_element, distance)
            if kind is None:
                continue
            key = protein_atom.residue_key
            candidate = Interaction(
                kind=kind,
                residue_label=(
                    f"{protein_atom.resname}{protein_atom.resseq}{protein_atom.icode}"
                ).strip(),
                chain=protein_atom.chain,
                resseq=protein_atom.resseq,
                icode=protein_atom.icode,
                resname=protein_atom.resname,
                protein_atom=protein_atom.name,
                ligand_atom=ligand_atom.name,
                protein_coord=protein_atom.coord,
                ligand_coord=ligand_atom.coord,
                distance=distance,
            )
            rank = (KIND_PRIORITY[kind], distance)
            previous = best.get(key)
            if previous is None or rank < previous[0]:
                best[key] = (rank, candidate)

    # 金属配位：PLIP 对配体金属结合基团的识别并不完整（例如磺酰胺 N
    # 不会被当作 metal-binding），这里直接按金属–配体 N/O/S 距离补充。
    for metal_atom in metals:
        key = metal_atom.residue_key
        for ligand_atom in ligand:
            ligand_element = _element(ligand_atom)
            if ligand_element not in METAL_COORD_ELEMENTS:
                continue
            distance = float(math.dist(metal_atom.coord, ligand_atom.coord))
            if distance > METAL_MAX:
                continue
            candidate = Interaction(
                kind="metal",
                residue_label=(
                    f"{metal_atom.resname}{metal_atom.resseq}{metal_atom.icode}"
                ).strip(),
                chain=metal_atom.chain,
                resseq=metal_atom.resseq,
                icode=metal_atom.icode,
                resname=metal_atom.resname,
                protein_atom=metal_atom.name or _element(metal_atom),
                ligand_atom=ligand_atom.name or ligand_element,
                protein_coord=metal_atom.coord,
                ligand_coord=ligand_atom.coord,
                distance=distance,
            )
            rank = (KIND_PRIORITY["metal"], distance)
            previous = best.get(key)
            if previous is None or rank < previous[0]:
                best[key] = (rank, candidate)

    ordered = sorted(
        (item for _, item in best.values()),
        key=lambda item: (KIND_PRIORITY[item.kind], item.distance, item.residue_label),
    )
    total_residues = len(ordered)
    limit = max(0, max_interactions)
    truncated = total_residues > limit
    shown = ordered[:limit]
    counts = {key: 0 for key in KIND_LABELS}
    for item in shown:
        counts[item.kind] = counts.get(item.kind, 0) + 1

    warnings: list[str] = []
    if total_residues == 0:
        warnings.append(f"配体周围 {HYDROPHOBIC_MAX:g} Å 内没有可展示的蛋白残基接触")
    if truncated:
        warnings.append(
            f"共检测到 {total_residues} 个残基接触，仅展示最接近的 {len(shown)} 条"
        )

    return InteractionAnalysis(
        interactions=shown,
        counts=counts,
        total_residues=total_residues,
        truncated=truncated,
        ligand_selection=_ligand_selection(ligand),
        ligand_atoms=len(ligand),
        receptor_residues=len({atom.residue_key for atom in receptor}),
        warnings=warnings,
    )
