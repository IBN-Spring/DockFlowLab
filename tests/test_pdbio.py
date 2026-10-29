"""PDB/PDBQT 解析、分类与复合物生成测试。"""

from __future__ import annotations

import pytest

from dockflowlab.core import pdbio

from .factories import (
    make_ligand_pdbqt,
    make_peptide_ligand_pdb,
    make_protein_pdb,
)


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


def test_normalize_autodock_element_types():
    # PDBQT 的元素列会把芳香碳写成 A；交给 OpenBabel/PLIP 前必须还原。
    assert pdbio.normalize_element("A", "C1", "LIG") == "C"
    assert pdbio.normalize_element("OA", "O1", "LIG") == "O"
    assert pdbio.normalize_element("SA", "S1", "LIG") == "S"
    assert pdbio.normalize_element("HD", "H1", "LIG") == "H"
    assert pdbio.normalize_element("NA", "N1", "LIG") == "N"
    assert pdbio.normalize_element("NA", "NA", "NA") == "Na"
    assert pdbio.normalize_element("", "CA", "ALA", "ATOM") == "C"


def test_parse_pdbqt_normalizes_aromatic_carbon(tmp_path):
    path = tmp_path / "ligand.pdbqt"
    path.write_text(
        "MODEL 1\n"
        "ATOM      1  C1  LIG L   1      12.000  21.000  31.000"
        "  1.00  0.00     0.000 A\n"
        "ATOM      2  N1  LIG L   1      13.200  21.000  31.000"
        "  1.00  0.00     0.000 NA\n"
        "ENDMDL\n",
        encoding="utf-8",
    )

    atoms = pdbio.parse_pdbqt_atoms(path)

    assert [atom.element for atom in atoms] == ["C", "N"]
    # 原始 AutoDock 类型仍保留在 atom_type 中，方便需要它的调用方。
    assert [atom.atom_type for atom in atoms] == ["A", "NA"]


def test_structure_kind_uses_first_model(tmp_path):
    """多模型 PDB（如 NMR 结构）只按第一个模型统计，不能累加成蛋白质。"""
    single = make_protein_pdb(tmp_path / "peptide.pdb", residues=10)
    body = [
        line
        for line in single.read_text(encoding="utf-8").splitlines()
        if line.startswith(("ATOM", "HETATM"))
    ]
    lines: list[str] = []
    for model in (1, 2, 3):
        lines.append(f"MODEL     {model:>4}")
        lines.extend(body)
        lines.append("ENDMDL")
    lines.append("END")
    path = tmp_path / "multi_model.pdb"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kind, detail = pdbio.structure_kind(path)
    assert kind == "peptide"
    assert "10 个氨基酸残基" in detail
    assert pdbio.polymer_chains(path)[0]["residues"] == 10
    assert pdbio.protein_chains(path)[0]["residues"] == 10


def test_peptide_ligand_chain_is_grouped(tmp_path):
    """标准氨基酸 + 非标准残基组成的肽类配体应作为一个整体识别。"""
    path = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")

    chains = pdbio.find_ligand_chains(path)
    assert [chain.chain for chain in chains] == ["C"]
    assert chains[0].residue_count == 6
    assert chains[0].heavy_atom_count == 49

    sites = pdbio.find_ligand_sites(path)
    assert len(sites) == 1
    site = sites[0]
    assert site.kind == "chain"
    assert site.chain == "C"
    assert site.residue_count == 6
    assert site.heavy_atom_count == 49
    assert site.resname == "PJE"  # 非标准残基中原子最多
    assert "链 C" in site.label
    assert "6 个残基" in site.label
    assert site.reference == "链 C 配体"


def test_remove_chains_drops_peptide_ligand(tmp_path):
    path = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")
    output = tmp_path / "without_chain_c.pdb"

    removed = pdbio.remove_chains(path, output, {"C"})

    assert removed == 49
    atoms = pdbio.parse_atoms(output)
    assert atoms
    assert all(atom.chain == "A" for atom in atoms)
    text = output.read_text(encoding="utf-8")
    assert "SEQRES   1 C" not in text
    assert " PJE C" not in text


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


def test_parse_pose_atoms_by_index(tmp_path):
    ligand = make_ligand_pdbqt(tmp_path / "ligand.pdbqt")

    first = pdbio.parse_pose_atoms(ligand, 0)
    second = pdbio.parse_pose_atoms(ligand, 1)
    missing = pdbio.parse_pose_atoms(ligand, 2)

    assert pdbio.count_poses(ligand) == 2
    assert first[0].x == pytest.approx(12.0)
    assert second[0].x == pytest.approx(12.1)
    assert missing == []


def test_make_complex_can_select_pose(tmp_path, pdb_path):
    ligand = make_ligand_pdbqt(tmp_path / "ligand.pdbqt")
    output = tmp_path / "complex_pose2.pdb"

    pdbio.make_complex(pdb_path, ligand, output, pose_index=1)

    text = output.read_text(encoding="utf-8")
    assert "LIG L   1" in text
    ligand_atoms = [
        atom
        for atom in pdbio.parse_first_pose_atoms(output)
        if atom.resname == "LIG"
    ]
    assert ligand_atoms[0].x == pytest.approx(12.1)


def test_count_poses_without_model(tmp_path):
    path = tmp_path / "single.pdbqt"
    path.write_text(
        "ATOM      1  C1  LIG L   1      12.000  21.000  31.000"
        "  1.00  0.00     0.000 C\n",
        encoding="utf-8",
    )
    assert pdbio.count_poses(path) == 1
    assert len(pdbio.parse_pose_atoms(path, 0)) == 1
    assert pdbio.parse_pose_atoms(path, 1) == []


def test_detect_format_by_content(tmp_path):
    cif = tmp_path / "model.xyz"
    cif.write_text("data_TEST\n_atom_site.group_PDB\n", encoding="utf-8")
    assert pdbio.detect_format(cif) == "cif"
    bad = tmp_path / "unknown.xyz"
    bad.write_text("hello", encoding="utf-8")
    assert pdbio.detect_format(bad) == "unknown"
