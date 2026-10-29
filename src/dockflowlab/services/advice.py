"""对接参数建议与结果可信度辅助。

exhaustiveness 的建议值只基于配体柔性（可旋转键）和重原子数，是一个
保守的起点，不会自动修改用户设置。真正的收敛判断还要看前几个 pose
的空间分散度（见 :mod:`dockflowlab.core.rmsd`）。
"""

from __future__ import annotations

from dataclasses import dataclass

_TIERS = (8, 16, 32, 64)


@dataclass(frozen=True)
class ExhaustivenessAdvice:
    recommended: int
    reason: str
    rotatable_bonds: int
    heavy_atoms: int

    def warning_for(self, current: int) -> str:
        if current >= self.recommended:
            return ""
        return (
            f"当前 exhaustiveness={current}，低于建议值 {self.recommended}。"
            f"{self.reason} 低搜索深度可能产生看似合理、实际未收敛的构象。"
        )


def recommend_exhaustiveness(
    rotatable_bonds: int,
    heavy_atoms: int,
) -> ExhaustivenessAdvice:
    """按配体柔性给出 exhaustiveness 建议值。"""
    rotatable = max(0, int(rotatable_bonds or 0))
    heavy = max(0, int(heavy_atoms or 0))

    if rotatable <= 3:
        index = 0
    elif rotatable <= 8:
        index = 1
    elif rotatable <= 14:
        index = 2
    else:
        index = 3

    # 大分子即使可旋转键不多，搜索空间也更大；每跨过一个阈值提升一档。
    if heavy > 100:
        index = min(len(_TIERS) - 1, index + 2)
    elif heavy > 60:
        index = min(len(_TIERS) - 1, index + 1)

    recommended = _TIERS[index]
    if rotatable <= 3:
        flexibility = "配体较刚性"
    elif rotatable <= 8:
        flexibility = "配体有一定柔性"
    elif rotatable <= 14:
        flexibility = "配体柔性较高"
    else:
        flexibility = "配体非常柔性"
    reason = (
        f"{flexibility}（可旋转键 {rotatable} 个、重原子 {heavy} 个），"
        f"建议从 exhaustiveness={recommended} 开始。"
    )
    return ExhaustivenessAdvice(
        recommended=recommended,
        reason=reason,
        rotatable_bonds=rotatable,
        heavy_atoms=heavy,
    )


def ligand_efficiency(affinity: float | None, heavy_atoms: int | None) -> float | None:
    """配体效率 LE = −ΔG / 重原子数，单位 kcal/mol/重原子。"""
    if affinity is None or heavy_atoms is None:
        return None
    try:
        heavy = int(heavy_atoms)
    except (TypeError, ValueError):
        return None
    if heavy <= 0:
        return None
    return float(-affinity) / heavy
