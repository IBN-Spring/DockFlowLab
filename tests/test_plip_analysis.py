"""PLIP 分析路径测试：文本整理、去重与回退。"""

from __future__ import annotations

from collections import namedtuple

import pytest

from dockflowlab.services import plip_analysis
from dockflowlab.services.interactions import Interaction

from .test_interactions import _make_contact_complex

_Atom = namedtuple("_Atom", "type coords")
_TargetAtom = namedtuple(
    "_TargetAtom", "atom type location restype resnr reschain"
)
_MetalRecord = namedtuple(
    "_MetalRecord",
    "metal target restype_l resnr_l reschain_l distance",
)


def _metal_record(location: str = "ligand") -> _MetalRecord:
    return _MetalRecord(
        metal=_Atom("Zn", (0.0, 0.0, 0.0)),
        target=_TargetAtom(
            atom=_Atom("O", (2.05, 0.0, 0.0)),
            type="O",
            location=location,
            restype="LIG",
            resnr=1,
            reschain="L",
        ),
        restype_l="ZN",
        resnr_l=301,
        reschain_l="A",
        distance=2.05,
    )


def _interaction(
    kind: str,
    resname: str,
    resseq: int,
    distance: float,
    chain: str = "A",
) -> Interaction:
    return Interaction(
        kind=kind,
        residue_label=f"{resname}{resseq}",
        chain=chain,
        resseq=resseq,
        icode="",
        resname=resname,
        protein_atom="CA",
        ligand_atom="C1",
        protein_coord=(0.0, 0.0, 0.0),
        ligand_coord=(distance, 0.0, 0.0),
        distance=distance,
    )


def test_prepare_complex_text_normalizes_elements_and_strips_hydrogens():
    text = (
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000"
        "  1.00  0.00           C\n"
        "HETATM    2  C1  LIG L   1       1.000   1.000   1.000"
        "  1.00  0.00           A\n"
        "HETATM    3  H1  LIG L   1       2.000   2.000   2.000"
        "  1.00  0.00           H\n"
        "END\n"
    )

    prepared = plip_analysis._prepare_complex_text(text)
    lines = prepared.splitlines()

    assert all(" H1 " not in line for line in lines)
    ligand = [line for line in lines if "LIG" in line]
    assert len(ligand) == 1
    assert ligand[0][76:78].strip() == "C"


def test_metal_converter_uses_metal_residue_for_ligand_coordination():
    item = plip_analysis._metal(_metal_record("ligand"))

    assert item is not None
    assert item.kind == "metal"
    assert item.residue_label == "ZN301"
    assert item.chain == "A"
    assert item.resseq == 301
    assert item.protein_atom == "Zn"
    assert item.ligand_atom == "O"
    assert item.protein_coord == (0.0, 0.0, 0.0)
    assert item.ligand_coord == (2.05, 0.0, 0.0)
    assert item.distance == pytest.approx(2.05)


def test_metal_converter_skips_protein_coordination():
    # 金属–蛋白配位不是配体相互作用，不应进入列表。
    assert plip_analysis._metal(_metal_record("protein.sidechain")) is None


def test_dedupe_keeps_highest_priority_per_residue():
    items = [
        _interaction("hydrophobic", "SER", 1, 3.2),
        _interaction("hbond", "SER", 1, 3.9),
        _interaction("vdw", "LEU", 2, 4.0),
    ]

    shown, total = plip_analysis._dedupe_and_limit(items, max_interactions=10)

    assert total == 2
    assert [(item.kind, item.residue_label) for item in shown] == [
        ("hbond", "SER1"),
        ("vdw", "LEU2"),
    ]


def test_dedupe_applies_limit():
    items = [
        _interaction("hbond", "SER", index + 1, 3.0) for index in range(5)
    ]

    shown, total = plip_analysis._dedupe_and_limit(items, max_interactions=3)

    assert total == 5
    assert len(shown) == 3


def test_falls_back_to_geometric_without_plip(tmp_path, monkeypatch):
    path = _make_contact_complex(tmp_path / "complex.pdb")
    monkeypatch.setattr(plip_analysis, "plip_available", lambda: False)

    result = plip_analysis.analyze_complex_precise(path)

    assert result.engine == "geometric"
    assert result.ok


def test_missing_complex_returns_warning(tmp_path):
    result = plip_analysis.analyze_complex_precise(tmp_path / "missing.pdb")

    assert result.ok is False
    assert result.warnings


@pytest.mark.skipif(
    not plip_analysis.plip_available(), reason="当前环境未安装 PLIP"
)
def test_plip_analyzes_synthetic_complex(tmp_path):
    path = _make_contact_complex(tmp_path / "complex.pdb")

    result = plip_analysis.analyze_complex_precise(path)

    assert result.engine == "plip"
    assert result.ok
    assert "hbond" in {item.kind for item in result.interactions}
