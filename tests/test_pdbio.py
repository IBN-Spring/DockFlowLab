"""PDB/PDBQT 解析、分类与复合物生成测试。"""

from __future__ import annotations

import pytest

from dockflowlab.core import pdbio

from .factories import make_ligand_pdbqt, make_protein_pdb


def test_parse_real_structure(pdb_path):
    atoms = pdbio.parse_atoms(pdb_path)
    assert len(atoms) > 800
    assert pdbio.detect_format(pdb_path) == "pdb"
    assert any(atom.chain == "A" for atom in atoms)
    assert all(atom.element for atom in atoms[:50])


def test_structure_kind_and_chains(pdb_path):
    kind, message = pdbio.structure_kind(pdb_path)
    assert kind == "protein"
    assert "残基" in message
    chains = pdbio.protein_chains(pdb_path)
    assert chains
    assert chains[0]["residues"] > 20
    assert pdbio.looks_like_protein(pdb_path)


def test_site_records_and_coordinates(pdb_path):
    sites = pdbio.parse_site_records(pdb_path)
    assert len(sites) == 2
    coords = pdbio.site_residue_coords(pdb_path, sites[0])
    assert len(coords) >= 4
    center, size = pdbio.bounds_center_size(coords, padding=2.0)
    assert len(center) == 3
    assert all(value > 0 for value in size)


def test_bounds_geometry():
    center, size = pdbio.bounds_center_size([(0, 0, 0), (2, 4, 6)], padding=1.0)
    assert center == (1.0, 2.0, 3.0)
    assert size == (4.0, 6.0, 8.0)
    assert pdbio.distance((0, 0, 0), (3, 4, 0)) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        pdbio.bounds([])


def test_synthetic_protein_and_ligand_site(tmp_path):
    path = make_protein_pdb(tmp_path / "complex.pdb", residues=25, ligand=True, site=True)
    assert pdbio.structure_kind(path)[0] == "protein"
    sites = pdbio.find_ligand_sites(path)
    assert len(sites) == 1
    assert sites[0].resname == "LIG"
    assert sites[0].heavy_atom_count == 8
    assert pdbio.parse_site_records(path)
    assert pdbio.infer_element("CA", "ALA", "ATOM") == "C"
    assert pdbio.infer_element("CA", "CA", "HETATM") == "Ca"


def test_validate_receptor_and_ligand_pdbqt(tmp_path, pdb_path):
    receptor_lines = []
    for index, atom in enumerate(pdbio.parse_atoms(pdb_path)[:80], start=1):
        line = (
            f"ATOM  {index:5d} {atom.name:>4} {atom.resname:>3} {atom.chain}{atom.resseq:4d}    "
            f"{atom.x:8.3f}{atom.y:8.3f}{atom.z:8.3f}  1.00  0.00     0.000 {atom.element:>2}"
        )
        receptor_lines.append(line)
    receptor = tmp_path / "receptor.pdbqt"
    receptor.write_text("\n".join(receptor_lines) + "\n", encoding="utf-8")

    valid, message, stats = pdbio.validate_pdbqt(receptor, receptor=True)
    assert valid, message
    assert stats["atoms"] == 80

    ligand = make_ligand_pdbqt(tmp_path / "ligand.pdbqt")
    valid, message, stats = pdbio.validate_pdbqt(ligand)
    assert valid, message
    poses = pdbio.parse_pdbqt_poses(ligand)
    assert [pose.affinity for pose in poses] == [-7.5, -6.8]
    assert poses[1].rmsd_lb == 1.2


def test_make_complex_uses_first_pose(tmp_path, pdb_path):
    ligand = make_ligand_pdbqt(tmp_path / "ligand.pdbqt")
    output = tmp_path / "complex.pdb"
    count = pdbio.make_complex(pdb_path, ligand, output)
    assert count == 3
    text = output.read_text(encoding="utf-8")
    assert "LIG L   1" in text
    assert text.rstrip().splitlines()[-1] == "END"
    assert pdbio.parse_first_pose_atoms(ligand)[0].x == pytest.approx(12.0)


def test_detect_format_by_content(tmp_path):
    cif = tmp_path / "model.xyz"
    cif.write_text("data_TEST\n_atom_site.group_PDB\n", encoding="utf-8")
    assert pdbio.detect_format(cif) == "cif"
    bad = tmp_path / "unknown.xyz"
    bad.write_text("hello", encoding="utf-8")
    assert pdbio.detect_format(bad) == "unknown"
