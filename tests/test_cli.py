"""命令行入口测试。"""

from __future__ import annotations

import json

import pytest

from dockflowlab.cli import main
from dockflowlab.workspace import Workspace


def test_info_json(workspace, pdb_path, capsys):
    workspace.import_file("target", pdb_path, display_name="1BCI")
    code = main(["info", str(workspace.root), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["targets"] == 1
    assert output["entries"]["targets"][0]["name"] == "1BCI"
    assert output["results"] == {"count": 0, "success": 0, "best": None}


def test_doctor_json_has_expected_sections(capsys):
    code = main(["doctor", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code in (0, 1)
    for key in ("app", "python", "vina", "openbabel", "packages", "temp_dir"):
        assert key in output
    assert output["app"]["name"] == "DockFlowLab"


def test_report_without_results_fails(workspace, capsys):
    code = main(["report", str(workspace.root)])
    assert code == 1
    assert "还没有对接结果" in capsys.readouterr().err


def test_prepare_without_entries_fails(workspace, capsys):
    code = main(["prepare", str(workspace.root)])
    assert code == 1
    assert "没有需要准备" in capsys.readouterr().err


def test_import_command(workspace, sdf_path, capsys):
    code = main(
        ["import", str(workspace.root), str(sdf_path), "--kind", "ligand", "--name", "阿司匹林"]
    )
    assert code == 0
    assert "阿司匹林" in capsys.readouterr().out
    reopened = Workspace.open(workspace.root)
    assert reopened.ligands[0].display_name == "阿司匹林"


def test_dock_requires_prepared(workspace, pdb_path, sdf_path, capsys):
    workspace.import_file("target", pdb_path)
    workspace.import_file("ligand", sdf_path)
    code = main(["dock", str(workspace.root)])
    assert code == 2
    assert "尚未准备完成" in capsys.readouterr().err


def test_fetch_command(monkeypatch, workspace, capsys):
    from dockflowlab.core.models import DownloadResult
    from dockflowlab.services.download import DownloadClient

    monkeypatch.setattr(
        DownloadClient,
        "pdb",
        lambda self, query: DownloadResult(
            data=b"ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
            + b"ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00  0.00           C\n"
            + b"ATOM      3  CA  ALA A   3       2.000   0.000   0.000  1.00  0.00           C\n",
            filename="pdb_TEST.pdb",
            display_name="TEST",
            meta={"source": "RCSB PDB", "warnings": []},
        ),
    )
    code = main(["fetch", str(workspace.root), "pdb", "1ABC"])
    assert code == 0
    assert "TEST" in capsys.readouterr().out
    assert len(Workspace.open(workspace.root).targets) == 1


def test_bad_command_arguments(workspace):
    with pytest.raises(SystemExit) as exc:
        main(["dock", str(workspace.root), "--center", "1,2"])
    assert exc.value.code == 2
