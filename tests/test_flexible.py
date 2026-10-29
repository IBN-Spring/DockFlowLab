"""柔性残基选择与受体拆分的前置校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from .factories import atom_line

from dockflowlab.core.models import KIND_TARGET, STATUS_READY, ProjectFile
from dockflowlab.services import flexible

CENTER = (10.0, 10.0, 10.0)


def _write_pdb(path: Path) -> Path:
    """一条链上放 SER/GLY/ALA/LEU，外加一个远离中心的 LEU 与一个配体。"""

    def residue(
        serial: int,
        resname: str,
        resseq: int,
        x: float,
        atoms: list[tuple[str, str, float, float, float]],
    ) -> tuple[list[str], int]:
        lines = []
        for name, element, dx, dy, dz in atoms:
            lines.append(
                atom_line(
                    "ATOM", serial, name, resname, "A", resseq,
                    x + dx, 10.0 + dy, 10.0 + dz, element,
                )
            )
            serial += 1
        return lines, serial

    lines = ["HEADER    TEST FLEX"]
    serial = 1
    for resname, resseq, x, atoms in (
        (
            "SER", 1, 10.0,
            [
                ("N", "N", 0.0, 0.0, 0.0),
                ("CA", "C", 1.2, 0.0, 0.0),
                ("C", "C", 2.4, 0.0, 0.0),
                ("O", "O", 3.2, 0.0, 0.0),
                ("CB", "C", 1.4, 1.4, 0.0),
                ("OG", "O", 2.6, 1.8, 0.0),
            ],
        ),
        (
            "GLY", 2, 13.0,
            [
                ("N", "N", 0.0, 0.0, 0.0),
                ("CA", "C", 1.2, 0.0, 0.0),
                ("C", "C", 2.4, 0.0, 0.0),
                ("O", "O", 3.2, 0.0, 0.0),
            ],
        ),
        (
            "ALA", 3, 16.0,
            [
                ("N", "N", 0.0, 0.0, 0.0),
                ("CA", "C", 1.2, 0.0, 0.0),
                ("C", "C", 2.4, 0.0, 0.0),
                ("O", "O", 3.2, 0.0, 0.0),
                ("CB", "C", 1.4, 1.4, 0.0),
            ],
        ),
        (
            "LEU", 4, 40.0,
            [
                ("N", "N", 0.0, 0.0, 0.0),
                ("CA", "C", 1.2, 0.0, 0.0),
                ("C", "C", 2.4, 0.0, 0.0),
                ("O", "O", 3.2, 0.0, 0.0),
                ("CB", "C", 1.4, 1.4, 0.0),
                ("CG", "C", 2.6, 1.8, 0.0),
                ("CD1", "C", 3.8, 1.6, 0.0),
                ("CD2", "C", 2.8, 3.2, 0.0),
            ],
        ),
    ):
        new_lines, serial = residue(serial, resname, resseq, x, atoms)
        lines.extend(new_lines)
    lines.append(
        atom_line("HETATM", serial, "C1", "LIG", "A", 100, 10.5, 12.0, 10.0, "C")
    )
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _target(tmp_path: Path) -> ProjectFile:
    return ProjectFile(
        kind=KIND_TARGET,
        stem="target",
        display_name="测试靶点",
        source="inputs/target.pdb",
        status=STATUS_READY,
        prepared="prepared/target.pdbqt",
    )


def test_residue_candidates_lists_rotatable_sidechains(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")

    candidates = flexible.residue_candidates(pdb, CENTER, radius=12.0)

    assert [item.resname for item in candidates] == ["SER", "GLY", "ALA"]
    assert candidates[0].distance == pytest.approx(0.0, abs=0.5)
    assert candidates[0].rotatable is True
    assert candidates[1].rotatable is False and candidates[1].note
    assert candidates[2].rotatable is False
    # 距离单调不减，列表顺序即“由近到远”
    distances = [item.distance for item in candidates]
    assert distances == sorted(distances)


def test_residue_candidates_skips_ligands_and_far_residues(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")

    candidates = flexible.residue_candidates(pdb, CENTER, radius=2.0)

    assert [item.resname for item in candidates] == ["SER"]


def test_residue_candidates_without_center_keeps_all(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")

    candidates = flexible.residue_candidates(pdb, None)

    assert {item.resname for item in candidates} == {"SER", "GLY", "ALA", "LEU"}


def test_residue_candidates_missing_file_returns_empty(tmp_path):
    assert flexible.residue_candidates(tmp_path / "missing.pdb", CENTER) == []


def test_auto_select_prefers_nearby_rotatable_residues(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")
    candidates = flexible.residue_candidates(pdb, CENTER, radius=12.0)

    selected = flexible.auto_select(candidates, radius=6.0)

    assert [item.label for item in selected] == ["SER A1"]


def test_auto_select_falls_back_to_nearest_rotatable(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")
    candidates = flexible.residue_candidates(pdb, CENTER, radius=12.0)

    # 半径内没有可旋转侧链时仍给出最近的候选，避免界面直接“无结果”。
    selected = flexible.auto_select(candidates, radius=0.0)

    assert selected and selected[0].label == "SER A1"


def test_auto_select_respects_limit(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")
    candidates = flexible.residue_candidates(pdb, None)

    selected = flexible.auto_select(candidates, limit=2)

    assert len(selected) == 2
    assert flexible.MAX_FLEX_RESIDUES == 12


def test_selection_roundtrip(tmp_path):
    target = _target(tmp_path)
    residues = [
        flexible.FlexResidue(chain="A", resseq=292, resname="ASP"),
        flexible.FlexResidue(chain="B", resseq=45, icode="A", resname="LYS"),
    ]

    flexible.save_selection(target, residues)
    restored = flexible.selected_residues(target)

    assert [item.key for item in restored] == ["A:292", "B:45A"]
    assert target.meta["flex_residues"][1]["resname"] == "LYS"

    flexible.save_selection(target, [])
    assert "flex_residues" not in target.meta
    assert flexible.selected_residues(target) == []


def test_selected_residues_ignores_broken_entries(tmp_path):
    target = _target(tmp_path)
    target.meta["flex_residues"] = [{"resseq": "x"}, "bad", {"resseq": 5, "chain": "A"}]

    restored = flexible.selected_residues(target)

    assert [item.key for item in restored] == ["A:5"]


def test_prepare_rejects_too_many_residues(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")
    residues = [
        flexible.FlexResidue(chain="A", resseq=index, resname="SER")
        for index in range(1, flexible.MAX_FLEX_RESIDUES + 2)
    ]

    with pytest.raises(flexible.FlexibleResidueError, match="最多"):
        flexible.prepare_flexible_receptor(pdb, residues, tmp_path / "out")


def test_prepare_rejects_empty_selection(tmp_path):
    pdb = _write_pdb(tmp_path / "receptor.pdb")

    with pytest.raises(flexible.FlexibleResidueError, match="没有选择"):
        flexible.prepare_flexible_receptor(pdb, [], tmp_path / "out")


def test_prepare_rejects_missing_structure(tmp_path):
    with pytest.raises(flexible.FlexibleResidueError, match="找不到"):
        flexible.prepare_flexible_receptor(
            tmp_path / "missing.pdb",
            [flexible.FlexResidue(chain="A", resseq=1, resname="SER")],
            tmp_path / "out",
        )


def test_flex_receptor_labels():
    receptor = flexible.FlexReceptor(
        rigid=Path("rigid.pdbqt"),
        flex=Path("flex.pdbqt"),
        residues=(
            flexible.FlexResidue(chain="A", resseq=292, resname="ASP"),
            flexible.FlexResidue(chain="A", resseq=293, resname="LYS"),
        ),
    )

    assert receptor.count == 2
    assert receptor.labels == "ASP A292、LYS A293"
