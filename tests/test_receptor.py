"""受体准备：肽类共晶配体链的处理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dockflowlab.core import pdbio
from dockflowlab.errors import InputError
from dockflowlab.services import receptor

from .factories import atom_line, make_peptide_ligand_pdb, make_protein_pdb


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


def test_filter_for_metal_retention_keeps_metals_and_modified_residues(tmp_path):
    source = tmp_path / "metal.pdb"
    source.write_text(
        "\n".join(
            [
                atom_line("ATOM", 1, "CA", "ALA", "A", 1, 1.0, 2.0, 3.0, "C"),
                atom_line("HETATM", 2, "ZN", "ZN", "A", 301, 1.5, 2.0, 3.0, "ZN"),
                atom_line("HETATM", 3, "O", "HOH", "A", 401, 4.0, 2.0, 3.0, "O"),
                atom_line("HETATM", 4, "C1", "GOL", "A", 402, 5.0, 2.0, 3.0, "C"),
                atom_line("HETATM", 5, "C1", "LIG", "L", 1, 6.0, 2.0, 3.0, "C"),
                atom_line("HETATM", 6, "SE", "MSE", "A", 2, 7.0, 2.0, 3.0, "SE"),
                "END",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output, kept_metals, removed_other = receptor._filter_for_metal_retention(
        source, tmp_path / "filtered.pdb"
    )

    resnames = [atom.resname for atom in pdbio.parse_atoms(output)]
    assert kept_metals == 1
    assert removed_other == 3
    assert "ZN" in resnames
    assert "MSE" in resnames
    assert "ALA" in resnames
    assert "HOH" not in resnames
    assert "GOL" not in resnames
    assert "LIG" not in resnames


def test_prepare_receptor_keep_metals_filters_before_fixing(tmp_path, monkeypatch):
    source = make_protein_pdb(tmp_path / "protein.pdb")
    text = source.read_text(encoding="utf-8").replace("END\n", "")
    text += (
        atom_line("HETATM", 900, "ZN", "ZN", "A", 301, 12.0, 21.0, 31.0, "ZN")
        + "\n"
        + atom_line("HETATM", 901, "O", "HOH", "A", 401, 15.0, 21.0, 31.0, "O")
        + "\nEND\n"
    )
    source.write_text(text, encoding="utf-8")
    captured: dict[str, Path] = {}

    def fake_fixer(
        pdb_source: Path,
        output_pdb: Path,
        options: receptor.ReceptorPrepOptions,
        warnings: list[str],
        progress,
    ) -> dict[str, object]:
        captured["source"] = Path(pdb_source)
        resnames = {atom.resname for atom in pdbio.parse_atoms(pdb_source)}
        assert "ZN" in resnames
        assert "HOH" not in resnames
        Path(output_pdb).write_text("END\n", encoding="utf-8")
        return {"atoms": 10, "residues": 6, "method": "fake"}

    monkeypatch.setattr(receptor, "_prepare_with_pdbfixer", fake_fixer)
    monkeypatch.setattr(
        pdbio, "validate_pdbqt", lambda *args, **kwargs: (True, "OK", {"atoms": 2})
    )

    outcome = receptor.prepare_receptor(
        source,
        tmp_path / "prepared.pdbqt",
        tmp_path / "prepared.pdb",
        options=receptor.ReceptorPrepOptions(keep_metals=True),
        obabel=_FakeObabel(),
    )

    assert outcome.ok
    assert captured["source"].name == "with_metals.pdb"
    assert any("已保留 1 个金属离子原子" in warning for warning in outcome.warnings)
    assert any("Vina 评分没有专门的金属配位项" in warning for warning in outcome.warnings)
