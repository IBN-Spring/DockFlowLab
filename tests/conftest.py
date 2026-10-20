"""测试公共配置。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA


@pytest.fixture(scope="session")
def pdb_path() -> Path:
    path = DATA / "1BCI.pdb"
    assert path.is_file(), "缺少测试结构 tests/data/1BCI.pdb"
    return path


@pytest.fixture(scope="session")
def sdf_path() -> Path:
    path = DATA / "aspirin.sdf"
    assert path.is_file(), "缺少测试结构 tests/data/aspirin.sdf"
    return path


@pytest.fixture()
def workspace(tmp_path):
    from dockflowlab.workspace import Workspace

    return Workspace.create(tmp_path / "项目 测试")


@pytest.fixture()
def prepared_workspace(tmp_path, pdb_path, sdf_path):
    """已经导入靶点与配体、但尚未准备的工作目录。"""
    from dockflowlab.workspace import Workspace

    ws = Workspace.create(tmp_path / "prepare")
    ws.import_file("target", pdb_path, display_name="1BCI")
    ws.import_file("ligand", sdf_path, display_name="阿司匹林")
    return ws
