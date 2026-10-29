"""相互作用 CSV 导出：字段完整、编码可被 Excel 读取。"""

from __future__ import annotations

import csv

from dockflowlab.services.interaction_export import (
    HEADERS,
    interaction_rows,
    write_interactions_csv,
)
from dockflowlab.services.interactions import Interaction, InteractionAnalysis


def _analysis() -> InteractionAnalysis:
    return InteractionAnalysis(
        interactions=[
            Interaction(
                kind="hbond",
                residue_label="MET318",
                chain="A",
                resseq=318,
                icode="",
                resname="MET",
                protein_atom="SD",
                ligand_atom="O2",
                protein_coord=(1.0, 2.0, 3.0),
                ligand_coord=(4.5, 5.25, 6.0),
                distance=2.87,
            ),
            Interaction(
                kind="hydrophobic",
                residue_label="LEU50",
                chain="A",
                resseq=50,
                icode="A",
                resname="LEU",
                protein_atom="CD1",
                ligand_atom="C7",
                protein_coord=(0.0, 0.0, 0.0),
                ligand_coord=(3.0, 3.0, 3.0),
                distance=4.12,
            ),
        ],
        counts={"hbond": 1, "hydrophobic": 1},
        engine="plip",
    )


def test_interaction_rows_include_fields_and_pose_rank():
    rows = interaction_rows(
        _analysis(), target="靶点", ligand="配体", pose_rank=3
    )

    assert [row["类型"] for row in rows] == ["氢键", "疏水作用"]
    first = rows[0]
    assert first["靶点"] == "靶点"
    assert first["候选分子"] == "配体"
    assert first["构象排名"] == 3
    assert first["残基"] == "MET318"
    assert first["蛋白原子"] == "SD"
    assert first["配体原子"] == "O2"
    assert first["距离 (Å)"] == "2.87"
    assert first["蛋白原子坐标"] == "1.000 2.000 3.000"
    assert first["配体原子坐标"] == "4.500 5.250 6.000"
    # 带插入码的残基序号保持原样。
    assert rows[1]["残基序号"] == "50A"


def test_write_interactions_csv_has_bom_and_headers(tmp_path):
    path = tmp_path / "out" / "interactions.csv"

    count = write_interactions_csv(
        path, _analysis(), target="靶点", ligand="配体", pose_rank=1
    )

    assert count == 2
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == HEADERS
    assert rows[1][3] == "氢键"


def test_write_interactions_csv_without_interactions(tmp_path):
    path = tmp_path / "empty.csv"

    count = write_interactions_csv(path, InteractionAnalysis())

    assert count == 0
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines == [",".join(HEADERS)]
