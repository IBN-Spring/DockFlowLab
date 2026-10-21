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


def _prepend_env(name: str, directory: Path) -> None:
    if not directory.is_dir():
        return
    current = os.environ.get(name, "")
    parts = current.split(os.pathsep) if current else []
    if str(directory) not in parts:
        os.environ[name] = str(directory) + (os.pathsep + current if current else "")


def _prepend_path(directory: Path) -> None:
    if not directory.is_dir():
        return
    if os.name == "nt":
        try:
            os.add_dll_directory(str(directory))
        except (AttributeError, OSError):
            pass
    _prepend_env("PATH", directory)


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

# OpenBabel 的 DLL 与格式插件必须同目录。_BASE 下也有一份 openbabel-3.dll，
# 若它先于 openbabel/bin 被加载，Smina 会找不到插件。
for _directory in (_OPENBABEL_BIN, _BASE):
    _prepend_path(_directory)

# macOS / Linux 的 dylib、so 需要动态加载器路径；BABEL_LIBDIR 本身不参与
# 依赖解析，内置的 obabel 与 Python 扩展仍要能找到同目录的共享库。
if os.name != "nt":
    for _variable in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        for _directory in (_BASE, _OPENBABEL_BIN):
            _prepend_env(_variable, _directory)

# 3D 盒子视图使用 pyqtgraph；固定 Qt 绑定，避免它探测到未打包的 PyQt5/PyQt6
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

# 图表只用于导出 PNG，不依赖 Qt 后端
os.environ.setdefault("MPLBACKEND", "Agg")
