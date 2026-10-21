"""受体准备：肽类共晶配体链的处理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dockflowlab.core import pdbio
from dockflowlab.errors import InputError
from dockflowlab.services import receptor

from .factories import make_peptide_ligand_pdb


class _FakeObabel:
    available = True

    def receptor_to_pdbqt(self, pdb: Path, pdbqt: Path) -> tuple[bool, str]:
        Path(pdbqt).write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n", encoding="utf-8")
        return True, "OK"


def test_exclude_ligand_chains_removes_peptide_chain(tmp_path):
    source = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")
    warnings: list[str] = []

    stripped, options = receptor._exclude_ligand_chains(
        source, tmp_path, receptor.ReceptorPrepOptions(), warnings
    )

    assert stripped != source
    atoms = pdbio.parse_atoms(stripped)
    assert atoms
    assert all(atom.chain == "A" for atom in atoms)
    assert options.chains == ()
    assert any("共晶配体链" in warning for warning in warnings)
    assert "链 C" in warnings[-1]


def test_exclude_ligand_chains_filters_chain_selection(tmp_path):
    source = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")

    stripped, options = receptor._exclude_ligand_chains(
        source,
        tmp_path,
        receptor.ReceptorPrepOptions(chains=("A", "C")),
        [],
    )

    assert all(atom.chain == "A" for atom in pdbio.parse_atoms(stripped))
    assert options.chains == ("A",)


def test_ligand_chain_alone_cannot_be_receptor(tmp_path):
    source = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")

    with pytest.raises(InputError, match="共晶配体"):
        receptor._exclude_ligand_chains(
            source,
            tmp_path,
            receptor.ReceptorPrepOptions(chains=("C",)),
            [],
        )


def test_prepare_receptor_strips_ligand_chain_before_fixing(tmp_path, monkeypatch):
    source = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")
    captured: dict[str, Path] = {}

    def fake_fixer(
        pdb_source: Path,
        output_pdb: Path,
        options: receptor.ReceptorPrepOptions,
        warnings: list[str],
        progress,
    ) -> dict[str, object]:
        captured["source"] = Path(pdb_source)
        atoms = pdbio.parse_atoms(pdb_source)
        assert atoms
        assert all(atom.chain == "A" for atom in atoms)
        Path(output_pdb).write_text("END\n", encoding="utf-8")
        return {"atoms": len(atoms), "residues": 25, "method": "fake"}

    monkeypatch.setattr(receptor, "_prepare_with_pdbfixer", fake_fixer)
    monkeypatch.setattr(
        pdbio, "validate_pdbqt", lambda *args, **kwargs: (True, "OK", {"atoms": 2})
    )

    outcome = receptor.prepare_receptor(
        source,
        tmp_path / "prepared.pdbqt",
        tmp_path / "prepared.pdb",
        obabel=_FakeObabel(),
    )

    assert outcome.ok
    assert "source" in captured
    assert any("共晶配体链" in warning for warning in outcome.warnings)


def test_prepare_receptor_without_ligand_chain_unchanged(tmp_path):
    source = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")
    warnings: list[str] = []
    stripped, _ = receptor._exclude_ligand_chains(
        source,
        tmp_path,
        receptor.ReceptorPrepOptions(remove_heterogens=False),
        warnings,
    )

    assert stripped == source
    assert warnings == []
