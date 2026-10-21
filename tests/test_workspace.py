"""工作目录（项目）行为测试。"""

from __future__ import annotations

import json

import pytest

from dockflowlab.core.models import Box, ProjectFile
from dockflowlab.errors import InputError
from dockflowlab.workspace import Workspace, safe_stem


def test_safe_stem_keeps_display_name_separate():
    assert safe_stem("阿司匹林") == "item"
    assert safe_stem("Aspirin 100mg") == "Aspirin_100mg"
    assert safe_stem("../../etc/passwd") == "passwd"
    assert len(safe_stem("x" * 100)) <= 48


def test_create_and_reopen_preserves_chinese_display_name(tmp_path, sdf_path):
    root = tmp_path / "我的对接项目"
    workspace = Workspace.create(root, name="阿司匹林筛选")
    entry = workspace.import_file("ligand", sdf_path, display_name="阿司匹林")

    assert entry.display_name == "阿司匹林"
    assert entry.stem.isascii()
    assert entry.source == "inputs/ligands/aspirin.sdf"
    assert (root / "project.json").is_file()
    assert (root / "prepared" / "ligands").is_dir()

    reopened = Workspace.open(root)
    assert reopened.name == "阿司匹林筛选"
    assert reopened.ligands[0].display_name == "阿司匹林"


def test_import_duplicate_returns_existing_entry(workspace, sdf_path):
    first = workspace.import_file("ligand", sdf_path, display_name="阿司匹林")
    second = workspace.import_file("ligand", sdf_path, display_name="另一个名字")
    assert second.stem == first.stem
    assert len(workspace.ligands) == 1


def test_import_rejects_unsupported_extension(workspace, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not a structure", encoding="utf-8")
    with pytest.raises(InputError, match="不支持"):
        workspace.import_file("ligand", bad)


def test_path_traversal_is_rejected(workspace):
    with pytest.raises(InputError):
        workspace.absolute("../outside.pdb")
    with pytest.raises(InputError):
        workspace.relative(workspace.root.parent / "outside.pdb")


def test_box_roundtrip(workspace):
    box = Box(center=(1.5, 2.5, 3.5), size=(20, 22, 24), source="manual", note="测试")
    path = workspace.save_box("target", box)
    assert path.is_file()
    loaded = workspace.load_box("target")
    assert loaded == box
    assert json.loads(path.read_text(encoding="utf-8"))["center"] == [1.5, 2.5, 3.5]


def test_reprepare_invalidates_auto_box_but_keeps_manual(workspace, pdb_path):
    from dockflowlab.services.preparation import _invalidate_auto_box

    entry = workspace.import_file("target", pdb_path, display_name="1BCI")
    workspace.save_box(
        entry.stem,
        Box(center=(1, 2, 3), size=(20, 20, 20), source="pocket"),
    )
    workspace.update_entry(entry, box="boxes/pocket.json")

    _invalidate_auto_box(workspace, entry)

    assert workspace.load_box(entry.stem) is None
    assert entry.box == ""

    workspace.save_box(
        entry.stem,
        Box(center=(1, 2, 3), size=(20, 20, 20), source="manual"),
    )

    _invalidate_auto_box(workspace, entry)

    assert workspace.load_box(entry.stem) is not None


def test_remove_entry_purges_inputs_and_prepared(workspace, sdf_path):
    entry = workspace.import_file("ligand", sdf_path)
    prepared = workspace.prepared_dir("ligand", entry.stem)
    prepared.mkdir(parents=True)
    (prepared / "x.pdbqt").write_text("dummy", encoding="utf-8")
    source = workspace.absolute(entry.source)
    assert source.is_file()

    workspace.remove_entry(entry)

    assert not source.exists()
    assert not prepared.exists()
    assert workspace.ligands == []


def test_missing_source_marks_failed(workspace, sdf_path):
    entry = workspace.import_file("ligand", sdf_path)
    workspace.absolute(entry.source).unlink()
    missing = workspace.mark_missing_sources()
    assert missing == [entry]
    assert entry.status == "failed"
    assert "移动" in entry.message


def test_sync_inputs_registers_manually_copied_files(workspace, sdf_path):
    destination = workspace.root / "inputs" / "ligands" / "manual.sdf"
    destination.write_bytes(sdf_path.read_bytes())
    added = workspace.sync_inputs("ligand")
    assert [entry.stem for entry in added] == ["manual"]
    assert workspace.ligands[0].meta["imported_by_scan"] is True


def test_project_file_rejects_corrupt_json(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    (root / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError, match="损坏"):
        Workspace.open(root)


def test_open_or_create(tmp_path):
    root = tmp_path / "new"
    first = Workspace.open_or_create(root)
    second = Workspace.open_or_create(root)
    assert first.root == second.root
    for relative in ("inputs/targets", "inputs/ligands", "docking", "analysis", "report"):
        assert (root / relative).is_dir()


def test_project_file_roundtrip_keeps_status(workspace, sdf_path):
    entry = workspace.import_file("ligand", sdf_path)
    workspace.update_entry(
        entry, status="ready", prepared="prepared/ligands/x/x.pdbqt", message="完成"
    )
    reopened = Workspace.open(workspace.root)
    loaded = reopened.ligands[0]
    assert loaded.status == "ready"
    assert loaded.prepared == "prepared/ligands/x/x.pdbqt"
    assert loaded.message == "完成"


def test_project_file_from_dict_ignores_junk():
    entry = ProjectFile.from_dict({"stem": "x", "display_name": "X", "source": "a.pdb"})
    assert entry.status == "new"
    assert entry.meta == {}
