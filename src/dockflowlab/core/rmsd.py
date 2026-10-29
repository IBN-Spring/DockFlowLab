"""配体构象比较：对称校正 RMSD 与收敛启发式。

重对接验证需要比较“对接 pose”和“实验晶体构象”。直接把原子按文件顺序
相减会被等价原子（苯环翻转、羧基两个氧互换等）误导，因此优先枚举
RDKit 子结构匹配（等价原子映射），取原始坐标 RMSD 的最小值；图不匹配
时退回到按元素分组的最优原子匹配（匈牙利算法）。

注意：这里 **不做事后叠合**。重对接关心的是 pose 在受体坐标系里的位置，
如果把两个构象先叠合，整体平移的错误会被抵消，跑错位点的 pose 也会
显示成 RMSD 0。Vina 自带的 pose RMSD 同样基于受体坐标系。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .pdbio import Atom


@dataclass(frozen=True)
class RmsdResult:
    value: float | None
    method: str
    matched_atoms: int = 0
    reference_atoms: int = 0
    probe_atoms: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class ConvergenceReport:
    pose_count: int
    compared: int
    max_pairwise_rmsd: float | None
    energy_span: float | None
    likely_unconverged: bool
    message: str = ""


def _heavy(atoms: Sequence[Atom]) -> list[Atom]:
    return [atom for atom in atoms if not atom.is_hydrogen]


def _pdb_block(atoms: Sequence[Atom], resname: str = "LIG", chain: str = "L") -> str:
    from . import pdbio

    lines = [
        pdbio.make_pdb_atom_line(atom, index, resname, chain)
        for index, atom in enumerate(atoms, start=1)
    ]
    lines.append("END")
    return "\n".join(lines) + "\n"


def _rdkit_best_rms(reference: Sequence[Atom], probe: Sequence[Atom]) -> float:
    import numpy as np
    from rdkit import Chem

    ref_mol = Chem.MolFromPDBBlock(
        _pdb_block(reference), sanitize=False, removeHs=False
    )
    probe_mol = Chem.MolFromPDBBlock(
        _pdb_block(probe), sanitize=False, removeHs=False
    )
    if ref_mol is None or probe_mol is None:
        raise ValueError("RDKit 无法解析配体坐标")
    if ref_mol.GetNumAtoms() != probe_mol.GetNumAtoms():
        raise ValueError("原子数不一致")

    # GetSubstructMatches(query) 返回的每个 tuple 中，第 i 个元素是
    # target 中与 query 第 i 个原子等价的原子。query=ref、target=probe
    # 时正好给出 ref→probe 的原子映射。
    matches = probe_mol.GetSubstructMatches(ref_mol, uniquify=False, maxMatches=5000)
    reverse = False
    if not matches:
        matches = ref_mol.GetSubstructMatches(probe_mol, uniquify=False, maxMatches=5000)
        reverse = True
    if not matches:
        raise ValueError("RDKit 找不到等价的原子映射")

    ref_conf = ref_mol.GetConformer()
    probe_conf = probe_mol.GetConformer()
    ref_coords = np.array(
        [list(ref_conf.GetAtomPosition(i)) for i in range(ref_mol.GetNumAtoms())],
        dtype=float,
    )
    probe_coords = np.array(
        [list(probe_conf.GetAtomPosition(i)) for i in range(probe_mol.GetNumAtoms())],
        dtype=float,
    )
    best = None
    for match in matches:
        if len(match) != ref_mol.GetNumAtoms():
            continue
        mapped = (ref_coords if reverse else probe_coords)[list(match)]
        target = probe_coords if reverse else ref_coords
        value = float(np.sqrt(((target - mapped) ** 2).sum(axis=1).mean()))
        if best is None or value < best:
            best = value
    if best is None:
        raise ValueError("原子映射不完整")
    return best


def _element_groups(atoms: Sequence[Atom]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index, atom in enumerate(atoms):
        element = (atom.element or atom.name[:1]).upper()
        groups.setdefault(element, []).append(index)
    return groups


def _hungarian_rms(reference: Sequence[Atom], probe: Sequence[Atom]) -> tuple[float, int]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    ref_groups = _element_groups(reference)
    probe_groups = _element_groups(probe)
    pairs: list[tuple[int, int]] = []
    for element in sorted(ref_groups.keys() & probe_groups.keys()):
        ref_index = np.array(ref_groups[element], dtype=int)
        probe_index = np.array(probe_groups[element], dtype=int)
        ref_coords = np.array([reference[i].coord for i in ref_index], dtype=float)
        probe_coords = np.array([probe[i].coord for i in probe_index], dtype=float)
        distances = ((ref_coords[:, None, :] - probe_coords[None, :, :]) ** 2).sum(axis=2)
        rows, cols = linear_sum_assignment(distances)
        pairs.extend(
            (int(ref_index[row]), int(probe_index[col]))
            for row, col in zip(rows, cols)
        )
    if not pairs:
        raise ValueError("没有可匹配的重原子")

    ref_coords = np.array([reference[i].coord for i, _ in pairs], dtype=float)
    probe_coords = np.array([probe[j].coord for _, j in pairs], dtype=float)
    rms = float(np.sqrt(((ref_coords - probe_coords) ** 2).sum(axis=1).mean()))
    return rms, len(pairs)


def symmetry_corrected_rmsd(
    reference: Sequence[Atom],
    probe: Sequence[Atom],
) -> RmsdResult:
    """计算参考构象与待测构象之间的重原子对称校正 RMSD（受体坐标系，不叠合）。"""
    reference_heavy = _heavy(reference)
    probe_heavy = _heavy(probe)
    if not reference_heavy or not probe_heavy:
        return RmsdResult(
            value=None,
            method="",
            reference_atoms=len(reference_heavy),
            probe_atoms=len(probe_heavy),
            message="缺少重原子坐标",
        )

    try:
        value = _rdkit_best_rms(reference_heavy, probe_heavy)
        return RmsdResult(
            value=value,
            method="RDKit 等价原子匹配（对称校正）",
            matched_atoms=len(reference_heavy),
            reference_atoms=len(reference_heavy),
            probe_atoms=len(probe_heavy),
        )
    except Exception as exc:
        rdkit_error = str(exc)

    try:
        value, matched = _hungarian_rms(reference_heavy, probe_heavy)
        message = f"RDKit 无法匹配（{rdkit_error}），已回退到按元素最优匹配"
        if matched < max(len(reference_heavy), len(probe_heavy)):
            message += f"；仅匹配 {matched} 个原子"
        return RmsdResult(
            value=value,
            method="按元素匈牙利匹配",
            matched_atoms=matched,
            reference_atoms=len(reference_heavy),
            probe_atoms=len(probe_heavy),
            message=message,
        )
    except Exception as exc:
        return RmsdResult(
            value=None,
            method="",
            reference_atoms=len(reference_heavy),
            probe_atoms=len(probe_heavy),
            message=f"无法计算 RMSD：{exc}",
        )


def pairwise_pose_rmsd(poses: Sequence[Sequence[Atom]]) -> float | None:
    """返回一组 pose 两两之间的最大对称校正 RMSD。"""
    values: list[float] = []
    for first in range(len(poses)):
        for second in range(first + 1, len(poses)):
            result = symmetry_corrected_rmsd(poses[first], poses[second])
            if result.value is not None:
                values.append(result.value)
    return max(values) if values else None


def convergence_report(
    poses: Sequence[Sequence[Atom]],
    affinities: Sequence[float],
    *,
    top_n: int = 5,
    spread_threshold: float = 2.0,
    energy_threshold: float = 1.0,
) -> ConvergenceReport:
    """根据前几个 pose 的空间分散度和能量差判断搜索是否可能未收敛。

    判据刻意保守：只有前 ``top_n`` 个 pose 明显分散（最大两两 RMSD
    ≥ ``spread_threshold``）且能量差 < ``energy_threshold`` 时才提示。
    单 pose 或分数缺失时不提示。
    """
    count = min(len(poses), len(affinities), top_n)
    if count <= 1:
        return ConvergenceReport(
            pose_count=len(poses),
            compared=max(0, count),
            max_pairwise_rmsd=None,
            energy_span=None,
            likely_unconverged=False,
        )
    selected = poses[:count]
    energies = list(affinities[:count])
    spread = pairwise_pose_rmsd(selected)
    energy_span = max(energies) - min(energies)
    likely = (
        spread is not None
        and spread >= spread_threshold
        and energy_span < energy_threshold
    )
    message = ""
    if likely:
        message = (
            f"前 {count} 个 pose 最大两两 RMSD 为 {spread:.2f} Å，"
            f"但能量差仅 {energy_span:.2f} kcal/mol：搜索可能没有收敛。"
            "建议提高 exhaustiveness，或检查配体是否过于柔性。"
        )
    return ConvergenceReport(
        pose_count=len(poses),
        compared=count,
        max_pairwise_rmsd=spread,
        energy_span=energy_span,
        likely_unconverged=bool(likely),
        message=message,
    )
