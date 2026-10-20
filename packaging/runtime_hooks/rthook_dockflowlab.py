"""PyInstaller 运行时钩子：让内置化学依赖在打包后能被找到。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(sys.executable).resolve().parent


def _prepend_path(directory: Path) -> None:
    if not directory.is_dir():
        return
    if os.name == "nt":
        try:
            os.add_dll_directory(str(directory))
        except (AttributeError, OSError):
            pass
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if str(directory) not in parts:
        os.environ["PATH"] = str(directory) + (os.pathsep + current if current else "")


_BASE = _base_dir()

# OpenBabel：DLL、格式插件与数据
_OPENBABEL_BIN = _BASE / "openbabel" / "bin"
if _OPENBABEL_BIN.is_dir():
    os.environ["BABEL_LIBDIR"] = str(_OPENBABEL_BIN)
    _OPENBABEL_DATA = _OPENBABEL_BIN / "data"
    if _OPENBABEL_DATA.is_dir():
        os.environ["BABEL_DATADIR"] = str(_OPENBABEL_DATA)

# OpenMM：CPU / Reference 插件
_OPENMM_PLUGINS = _BASE / "openmm_plugins"
if _OPENMM_PLUGINS.is_dir():
    os.environ["OPENMM_PLUGIN_DIR"] = str(_OPENMM_PLUGINS)

for _directory in (_BASE, _OPENBABEL_BIN):
    _prepend_path(_directory)

# 图表只用于导出 PNG，不依赖 Qt 后端
os.environ.setdefault("MPLBACKEND", "Agg")
