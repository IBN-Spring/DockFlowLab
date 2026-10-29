"""结构体检（REMARK 350/465/470、altloc、occupancy）测试。"""

from __future__ import annotations

from dockflowlab.core import structure_qc
from dockflowlab.services.receptor import inspect_target

from .factories import make_protein_pdb


def _atom(
    record: str,
    serial: int,
    name: str,
    resname: str,
    chain: str,
    resseq: int,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    occupancy: float = 1.0,
    altloc: str = "",
    element: str = "C",
) -> str:
    return (
        f"{record:<6}{serial:5d} {name:>4}{altloc:1}{resname:>3} {chain:1}"
        f"{resseq:4d}{'':1}   {x:8.3f}{y:8.3f}{z:8.3f}"
        f"{occupancy:6.2f}{0.0:6.2f}          {element:>2}"
    )


def _qc_structure(path):
    lines = [
        "HEADER    TEST STRUCTURE",
        "REMARK 350 BIOMOLECULE: 1",
        "REMARK 350 AUTHOR DETERMINED BIOLOGICAL UNIT: DIMERIC",
        "REMARK 350 APPLY THE FOLLOWING TO CHAINS: A, B",
        "REMARK 350   BIOMT1   1  1.000000  0.000000  0.000000     0.00000",
        "REMARK 350   BIOMT2   1  0.000000  1.000000  0.000000     0.00000",
        "REMARK 350   BIOMT3   1  0.000000  0.000000  1.000000     0.00000",
        "REMARK 350   BIOMT1   2 -1.000000  0.000000  0.000000     0.00000",
        "REMARK 350   BIOMT2   2  0.000000 -1.000000  0.000000     0.00000",
        "REMARK 350   BIOMT3   2  0.000000  0.000000  1.000000     0.00000",
        "REMARK 465   M RES C SSSEQI",
        "REMARK 465     GLY A   103",
        "REMARK 465     SER A   104",
        "REMARK 470     LYS A   210   CE   NZ",
        "REMARK 470     LYS A   210   CD",
        _atom("ATOM", 1, "N", "ALA", "A", 1, x=0.0, y=0.0, z=0.0, element="N"),
        _atom("ATOM", 2, "CA", "ALA", "A", 1, x=1.5, y=0.0, z=0.0, occupancy=0.4),
        _atom("ATOM", 3, "CB", "ALA", "A", 1, x=2.5, y=0.0, z=0.0, altloc="A"),
        _atom("ATOM", 4, "CB", "ALA", "A", 1, x=3.5, y=0.0, z=0.0, altloc="B"),
        _atom("HETATM", 5, "ZN", "ZN", "A", 301, x=4.0, y=0.0, z=0.0, element="Zn"),
        _atom("HETATM", 6, "O", "HOH", "A", 401, x=5.0, y=0.0, z=0.0, element="O"),
        _atom("HETATM", 7, "C1", "LIG", "A", 501, x=6.0, y=0.0, z=0.0),
        _atom("HETATM", 8, "C2", "LIG", "A", 501, x=7.0, y=0.0, z=0.0),
        _atom("HETATM", 9, "O1", "LIG", "A", 501, x=8.0, y=0.0, z=0.0, element="O"),
        "END",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_structure_qc_reports_hidden_problems(tmp_path):
    path = _qc_structure(tmp_path / "qc.pdb")

    qc = structure_qc.analyze_structure(path)

    assert qc.is_multimer
    assert qc.biomt_transforms == 2
    assert qc.biological_unit == "DIMERIC"
    assert qc.missing_residue_count == 2
    assert [item.label for item in qc.missing_residues] == ["GLY A103", "SER A104"]
    assert qc.missing_atom_count == 3
    assert qc.missing_atoms[0].label == "LYS A210"
    assert qc.altloc_atoms == 1
    assert qc.altloc_labels == ("A", "B")
    assert qc.low_occupancy_atoms == 1
    assert qc.metals == ("ZN",)
    assert qc.waters == 1
    assert qc.nonstandard_residues == ("LIG",)
    assert any("多聚体" in item for item in qc.warnings())
    assert any("缺失残基" in item for item in qc.warnings())
    assert any("altloc" in item for item in qc.warnings())
    data = qc.as_dict()
    assert data["missing_residue_count"] == 2
    assert data["biomt_transforms"] == 2
    assert data["metals"] == ["ZN"]


def test_structure_qc_clean_structure_has_no_warnings(tmp_path):
    path = make_protein_pdb(tmp_path / "clean.pdb", residues=12)

    qc = structure_qc.analyze_structure(path)

    assert not qc.has_findings
    assert qc.warnings() == []
    assert qc.models == 1
    assert qc.missing_residue_count == 0
    assert qc.altloc_atoms == 0
    assert qc.low_occupancy_atoms == 0


def test_structure_qc_handles_unreadable_file(tmp_path):
    qc = structure_qc.analyze_structure(tmp_path / "missing.pdb")
    assert not qc.has_findings
    assert qc.notes and "无法读取" in qc.notes[0]


def test_inspect_target_includes_structure_qc(tmp_path):
    path = _qc_structure(tmp_path / "inspect.pdb")

    info = inspect_target(path)

    assert info["ok"]
    assert info["structure_qc"]["biomt_transforms"] == 2
    assert info["structure_qc"]["metals"] == ["ZN"]
