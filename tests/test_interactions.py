"""相互作用几何分类测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dockflowlab.services import interactions

from .factories import atom_line


def _non_zero(counts: dict[str, int]) -> dict[str, int]:
    return {kind: count for kind, count in counts.items() if count}


def _make_contact_complex(path: Path) -> Path:
    """构造三种接触各一条、外加一个超出截断值残基的最小复合物。"""
    lines = ["HEADER    INTERACTION TEST"]
    serial = 1

    def add(record: str, name: str, resname: str, chain: str, resseq: int,
            x: float, y: float, z: float, element: str) -> None:
        nonlocal serial
        lines.append(
            atom_line(record, serial, name, resname, chain, resseq, x, y, z, element)
        )
        serial += 1

    # 配体 N1 与 SER1 的 OG 相距 3.20 Å → 氢键
    add("ATOM", "OG", "SER", "A", 1, -3.2, 0.0, 0.0, "O")
    # 配体 C1 与 LEU2 的 CD1 相距 3.50 Å → 疏水作用
    add("ATOM", "CD1", "LEU", "A", 2, 10.0, 13.5, 10.0, "C")
    # 配体 C1 与 VAL3 的 O 相距 3.90 Å → 范德华接触
    add("ATOM", "O", "VAL", "A", 3, 10.0, 10.0, 13.9, "O")
    # 与配体 N1 相距 4.50 Å，超出 4.4 Å 截断 → 不应出现
    add("ATOM", "CB", "ALA", "A", 4, 0.0, 0.0, 4.5, "C")

    add("HETATM", "N1", "LIG", "L", 1, 0.0, 0.0, 0.0, "N")
    add("HETATM", "C1", "LIG", "L", 1, 10.0, 10.0, 10.0, "C")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_classifies_hbond_hydrophobic_and_vdw(tmp_path):
    path = _make_contact_complex(tmp_path / "complex.pdb")

    result = interactions.analyze_complex(path)

    assert result.ok
    assert result.total_residues == 3
    assert result.truncated is False
    assert [item.kind for item in result.interactions] == [
        "hbond",
        "hydrophobic",
        "vdw",
    ]
    assert [item.residue_label for item in result.interactions] == [
        "SER1",
        "LEU2",
        "VAL3",
    ]
    assert [item.protein_atom for item in result.interactions] == ["OG", "CD1", "O"]
    assert [item.ligand_atom for item in result.interactions] == ["N1", "C1", "C1"]
    assert result.interactions[0].distance == pytest.approx(3.2, abs=1e-6)
    assert result.interactions[1].distance == pytest.approx(3.5, abs=1e-6)
    assert result.interactions[2].distance == pytest.approx(3.9, abs=1e-6)
    assert _non_zero(result.counts) == {"hbond": 1, "hydrophobic": 1, "vdw": 1}
    assert "氢键 1" in result.summary
    assert result.ligand_atoms == 2
    assert result.ligand_selection == "[LIG] and :L"
    assert result.interactions[0].residue_selection == "[SER] and 1 and :A"


def test_one_contact_per_residue_prefers_hbond(tmp_path):
    path = tmp_path / "priority.pdb"
    path.write_text(
        "\n".join(
            [
                atom_line("ATOM", 1, "CB", "LEU", "A", 1, 0.0, 3.2, 0.0, "C"),
                atom_line("ATOM", 2, "O", "LEU", "A", 1, -3.4, 0.0, 0.0, "O"),
                atom_line("HETATM", 3, "N1", "LIG", "L", 1, 0.0, 0.0, 0.0, "N"),
                "END",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = interactions.analyze_complex(path)

    assert len(result.interactions) == 1
    interaction = result.interactions[0]
    assert interaction.kind == "hbond"
    assert interaction.protein_atom == "O"
    assert result.total_residues == 1


def test_truncates_and_reports(tmp_path):
    path = _make_contact_complex(tmp_path / "complex.pdb")

    result = interactions.analyze_complex(path, max_interactions=2)

    assert len(result.interactions) == 2
    assert result.total_residues == 3
    assert result.truncated is True
    assert _non_zero(result.counts) == {"hbond": 1, "hydrophobic": 1}
    assert any("仅展示" in warning for warning in result.warnings)
    assert "仅显示前 2 条" in result.summary


def test_missing_complex_returns_warning(tmp_path):
    result = interactions.analyze_complex(tmp_path / "missing.pdb")

    assert result.ok is False
    assert result.interactions == []
    assert result.warnings


def test_complex_without_ligand_returns_warning(tmp_path):
    path = tmp_path / "receptor.pdb"
    path.write_text(
        atom_line("ATOM", 1, "CA", "ALA", "A", 1, 0.0, 0.0, 0.0, "C") + "\nEND\n",
        encoding="utf-8",
    )

    result = interactions.analyze_complex(path)

    assert result.ok is False
    assert any("配体" in warning for warning in result.warnings)
