"""相互作用结果导出为 CSV。

与结果页的批量导出共用同一套字段命名：类型、残基、原子、距离、构象排名。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .interactions import InteractionAnalysis

HEADERS = (
    "靶点",
    "候选分子",
    "构象排名",
    "类型",
    "残基",
    "链",
    "残基序号",
    "残基名",
    "蛋白原子",
    "配体原子",
    "距离 (Å)",
    "蛋白原子坐标",
    "配体原子坐标",
)


def _coord_text(coord: Iterable[float]) -> str:
    return " ".join(f"{float(value):.3f}" for value in coord)


def interaction_rows(
    analysis: InteractionAnalysis,
    *,
    target: str = "",
    ligand: str = "",
    pose_rank: int = 1,
) -> list[dict[str, object]]:
    """把一次分析结果展开成 CSV 行（保持界面上的显示顺序）。"""
    rows: list[dict[str, object]] = []
    for item in analysis.interactions:
        rows.append(
            {
                "靶点": target,
                "候选分子": ligand,
                "构象排名": int(pose_rank),
                "类型": item.kind_label,
                "残基": item.residue_label,
                "链": item.chain,
                "残基序号": f"{item.resseq}{item.icode}".strip(),
                "残基名": item.resname,
                "蛋白原子": item.protein_atom,
                "配体原子": item.ligand_atom,
                "距离 (Å)": f"{item.distance:.2f}",
                "蛋白原子坐标": _coord_text(item.protein_coord),
                "配体原子坐标": _coord_text(item.ligand_coord),
            }
        )
    return rows


def write_interactions_csv(
    path: str | Path,
    analysis: InteractionAnalysis,
    *,
    target: str = "",
    ligand: str = "",
    pose_rank: int = 1,
) -> int:
    """写出 CSV 并返回行数；没有相互作用时仍写出表头（便于批量汇总）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = interaction_rows(
        analysis, target=target, ligand=ligand, pose_rank=pose_rank
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(HEADERS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
