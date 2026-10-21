"""配体读取行为测试。"""

from __future__ import annotations

import pytest


def test_multirecord_sdf_uses_first_record(tmp_path, sdf_path):
    pytest.importorskip("rdkit")
    from dockflowlab.services.ligand import read_molecule

    first = sdf_path.read_text(encoding="utf-8")
    second = first.replace("2244", "9999", 1)
    combined = tmp_path / "two_records.sdf"
    combined.write_text(first + second, encoding="utf-8")

    mol, warnings = read_molecule(combined)

    assert mol.GetProp("_Name") == "2244"
    assert any("2 条记录" in message for message in warnings)


def test_unnamed_sdf_roundtrip(tmp_path, sdf_path):
    """空标题 SDF 的计数行不能被误当成第 3 行。"""
    pytest.importorskip("rdkit")
    from dockflowlab.services.ligand import read_molecule
    from dockflowlab.tools import rdkit_io

    mol, _ = read_molecule(sdf_path)
    mol.SetProp("_Name", "")
    target = tmp_path / "未命名" / "无标题.sdf"

    rdkit_io.write_sdf(mol, target)
    restored, _ = rdkit_io.read_mol(target, remove_hs=False)

    assert restored is not None
    assert restored.GetNumAtoms() == mol.GetNumAtoms()

    second = target.read_text(encoding="utf-8")
    target.write_text(
        target.read_text(encoding="utf-8") + second, encoding="utf-8"
    )
    molecules, loose = rdkit_io.read_sdf_molecules(target, remove_hs=False)
    assert len(molecules) == 2
    assert not loose


def test_loose_molecule_can_add_hydrogens(tmp_path):
    """价态异常的 SDF 按宽松模式读入后，AddHs 不应再崩溃。"""
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from dockflowlab.tools import rdkit_io

    loose = Chem.MolFromSmiles("C(C)(C)(C)(C)C", sanitize=False)
    assert loose is not None
    block = Chem.MolToMolBlock(loose)
    source = tmp_path / "价态异常.sdf"
    source.write_text(block + "$$$$\n", encoding="utf-8")

    molecules, was_loose = rdkit_io.read_sdf_molecules(source, remove_hs=False)

    assert was_loose
    assert molecules
    assert Chem.AddHs(molecules[0]).GetNumAtoms() > molecules[0].GetNumAtoms()


def test_read_molecule_from_chinese_path(tmp_path, sdf_path):
    """Windows 中文/OneDrive 路径不应再触发 RDKit 的 Bad input file。"""
    pytest.importorskip("rdkit")
    from dockflowlab.services.ligand import read_molecule

    source = tmp_path / "桌面" / "对接 项目" / "阿司匹林.sdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(sdf_path.read_bytes())

    mol, warnings = read_molecule(source)

    assert mol.GetNumAtoms() > 10
    assert mol.GetNumConformers() > 0
    assert warnings == []


def test_openbabel_converts_chinese_path(tmp_path, sdf_path):
    """OpenBabel 在 Windows 上应自动改用 CLI + ASCII 临时目录。"""
    from dockflowlab.tools.obabel import get_openbabel
    from dockflowlab.tools.runtime import IS_WINDOWS

    adapter = get_openbabel()
    if not adapter.available:
        pytest.skip("OpenBabel 不可用")
    folder = tmp_path / "桌面" / "对接 项目"
    source = folder / "阿司匹林.sdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(sdf_path.read_bytes())
    output = folder / "阿司匹林.pdb"

    ok, message = adapter.convert(source, output, out_format="pdb")

    assert ok, message
    if adapter.has_python_api and IS_WINDOWS:
        # 非 ASCII 路径必须直接路由到 CLI，而不是先让 Python API 失败一次。
        assert message == "OpenBabel CLI"
    assert output.is_file()
    assert "HETATM" in output.read_text(encoding="utf-8", errors="replace")


def test_openbabel_cli_adds_hydrogens_at_ph(tmp_path, sdf_path):
    """CLI 兜底也必须执行 add_hydrogens/ph，不能静默跳过质子化。"""
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from dockflowlab.services.ligand import read_molecule
    from dockflowlab.tools import rdkit_io
    from dockflowlab.tools.obabel import get_openbabel

    adapter = get_openbabel()
    if not adapter.available:
        pytest.skip("OpenBabel 不可用")
    mol, _ = read_molecule(sdf_path)
    heavy = Chem.RemoveHs(mol)
    folder = tmp_path / "桌面" / "对接 项目"
    source = folder / "无氢结构.sdf"
    output = folder / "加氢结构.sdf"
    rdkit_io.write_sdf(heavy, source)

    ok, message = adapter.convert(
        source, output, out_format="sdf", add_hydrogens=True, ph=7.4
    )

    assert ok, message
    restored, _ = rdkit_io.read_mol(output, remove_hs=False)
    assert restored is not None
    assert any(atom.GetAtomicNum() == 1 for atom in restored.GetAtoms())


def test_read_mol_from_chinese_path(tmp_path, sdf_path):
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from dockflowlab.services.ligand import read_molecule

    mol, _ = read_molecule(sdf_path)
    source = tmp_path / "桌面" / "对接 项目" / "阿司匹林.mol"
    source.parent.mkdir(parents=True)
    source.write_text(Chem.MolToMolBlock(mol), encoding="utf-8")

    restored, warnings = read_molecule(source)

    assert restored.GetNumAtoms() == mol.GetNumAtoms()
    assert warnings == []


def test_prepare_ligand_writes_into_chinese_path(tmp_path, sdf_path):
    """准备结果（PDBQT 与预览 SDF）必须能写进中文工作目录。"""
    pytest.importorskip("rdkit")
    from dockflowlab.core import pdbio
    from dockflowlab.services.ligand import LigandPrepOptions, prepare_ligand

    folder = tmp_path / "桌面" / "对接 项目" / "准备结果"
    source = folder / "阿司匹林.sdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(sdf_path.read_bytes())
    pdbqt = folder / "阿司匹林.pdbqt"
    preview = folder / "阿司匹林_预览.sdf"

    outcome = prepare_ligand(
        source,
        pdbqt,
        preview,
        options=LigandPrepOptions(),
    )

    assert outcome.ok
    assert pdbqt.is_file()
    assert preview.is_file()
    valid, message, _ = pdbio.validate_pdbqt(pdbqt)
    assert valid, message

    from dockflowlab.services.ligand import read_molecule

    preview_mol, _ = read_molecule(preview)
    assert preview_mol.GetNumAtoms() > 10
