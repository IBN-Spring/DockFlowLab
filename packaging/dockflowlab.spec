# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / macOS 通用）。

用法（在 DockFlowLab 环境中）：
    python -m PyInstaller --clean --noconfirm packaging/dockflowlab.spec

产物：
    dist/DockFlowLab/            Windows / Linux 目录版
    dist/DockFlowLab.app/        macOS 应用包

设计说明：
* 采用 onedir，启动快，杀毒软件误报少，且化学依赖的 DLL/数据文件更容易排查。
* conda 与 pip wheel 两种依赖布局都做了探测。
* OpenBabel 的格式插件、数据目录与 DLL 会被放到 ``openbabel/bin``，由运行时
  钩子设置 BABEL_DATADIR / BABEL_LIBDIR。
* OpenMM 插件放到 ``openmm_plugins``，由运行时钩子设置 OPENMM_PLUGIN_DIR。
* Windows 直接使用仓库内置的 AutoDock Vina / QuickVina 2 / Smina 二进制；
  macOS/Linux 若构建机 PATH 上有对应引擎（或用 DOCKFLOWLAB_VINA /
  DOCKFLOWLAB_QVINA2 / DOCKFLOWLAB_SMINA 指定），会一并收进安装包。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
RESOURCES = SRC / "dockflowlab" / "resources"
CONSOLE_BUILD = os.environ.get("DOCKFLOWLAB_CONSOLE") == "1"
APP_NAME = "DockFlowLabConsole" if CONSOLE_BUILD else "DockFlowLab"
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
PREFIX = Path(sys.prefix)

datas = []
binaries = []
hiddenimports = []


# ----------------------------------------------------------------------
# 基础资源
# ----------------------------------------------------------------------
datas.append((str(RESOURCES), "dockflowlab/resources"))

for package in ("rdkit", "meeko", "openmm", "pdbfixer", "pyqtgraph", "OpenGL"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception as exc:  # pragma: no cover - 构建期诊断
        print(f"[spec] 警告：收集 {package} 失败：{exc}")
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for distribution in (
    "meeko",
    "openmm",
    "pdbfixer",
    "PySide6",
    "pyqtgraph",
    "PyOpenGL",
    "matplotlib",
    "pandas",
    "numpy",
    "scipy",
    "requests",
    "psutil",
    "pymol",
    "plip",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass


# ----------------------------------------------------------------------
# PLIP：相互作用分析（GPL-2.0）
# ----------------------------------------------------------------------
# PLIP 是运行时按需导入的纯 Python 包，PyInstaller 看不到引用，必须显式
# 收集。可视化/命令行/测试模块不参与分析，排除以减小体积；它们内部的
# ``import pymol`` 由主程序的 excludes 挡住（渲染在 PyMOLRunner 中完成）。
try:
    plip_hidden = collect_submodules(
        "plip",
        filter=lambda name: not (
            name.startswith("plip.test")
            or name.startswith("plip.visualization")
            or name == "plip.plipcmd"
        ),
    )
    hiddenimports += plip_hidden
    print(f"[spec] PLIP：收集 {len(plip_hidden)} 个模块")
except Exception as exc:  # pragma: no cover - 构建期诊断
    print(f"[spec] 提示：未找到 PLIP，安装包将回退到几何判定：{exc}")


# ----------------------------------------------------------------------
# PyMOL：内置无界面渲染器
# ----------------------------------------------------------------------
# 主程序不导入 PyMOL，而是生成一个独立的 PyMOLRunner.exe。这样 PyMOL 的
# OpenGL / Tcl 初始化与原生崩溃不会影响界面进程，也不需要完整的 PyMOL GUI。
pymol_datas = []
pymol_binaries = []
pymol_hidden = []
for package in ("pymol", "pymol2", "chempy"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception as exc:  # pragma: no cover - 构建期诊断
        print(f"[spec] 警告：收集 {package} 失败：{exc}")
        continue
    pymol_datas += package_datas
    pymol_binaries += package_binaries
    pymol_hidden += package_hidden

# 渲染入口只使用 pymol2 的无界面会话，不创建 Qt/Tk 窗口。把 GUI 模块排除，
# 避免 PyInstaller 为 PyMOLRunner 再收集一套 Qt。
pymol_hidden = [
    name
    for name in pymol_hidden
    if not (
        name == "pymol.Qt"
        or name.startswith("pymol.Qt.")
        or name.startswith("pmg_qt")
        or name == "pymol.plugins.managergui_qt"
    )
]
HAS_PYMOL = bool(pymol_hidden)
if HAS_PYMOL:
    print(f"[spec] PyMOL：收集 {len(pymol_datas)} 个数据文件，内置渲染器将启用")
else:
    print("[spec] 提示：构建环境未安装 PyMOL，安装包不包含内置渲染器")


# ----------------------------------------------------------------------
# OpenBabel：DLL、命令行、格式插件与数据
# ----------------------------------------------------------------------
def _tree(source: Path, destination: str) -> None:
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_file():
            relative = path.relative_to(source)
            target = Path(destination) / relative.parent
            datas.append((str(path), str(target)))


def _openbabel_package_dir() -> Path | None:
    try:
        spec = importlib.util.find_spec("openbabel")
    except (ImportError, ValueError):
        return None
    if spec and spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    return None


def _openbabel_data_dir(share_dir: Path) -> Path | None:
    """在 share/openbabel 下选出真正的数据目录（通常带版本号）。"""
    if not share_dir.is_dir():
        return None
    candidates = [p for p in share_dir.iterdir() if p.is_dir()]
    candidates.sort(
        key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()],
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "element.txt").is_file() or list(candidate.glob("bondtyp.txt")):
            return candidate
    if (share_dir / "element.txt").is_file() or list(share_dir.glob("bondtyp.txt")):
        return share_dir
    return None


def _collect_openbabel_posix() -> None:
    """收集 conda / Homebrew 在 macOS、Linux 上的 OpenBabel 布局。"""
    bin_dir = PREFIX / "bin"
    lib_dir = PREFIX / "lib"
    share_dir = PREFIX / "share" / "openbabel"
    dest = "openbabel/bin"

    obabel = bin_dir / "obabel"
    if obabel.is_file():
        binaries.append((str(obabel), dest))

    for pattern in (
        "libopenbabel*.dylib",
        "libopenbabel*.so*",
        "libinchi*.dylib",
        "libinchi*.so*",
    ):
        for source in sorted(lib_dir.glob(pattern)):
            if source.is_file():
                binaries.append((str(source), dest))

    plugin_root = lib_dir / "openbabel"
    if plugin_root.is_dir():
        for source in sorted(plugin_root.rglob("*")):
            if source.is_file() and source.suffix in (".so", ".dylib", ".obf"):
                binaries.append((str(source), dest))

    data_dir = _openbabel_data_dir(share_dir)
    if data_dir is not None:
        _tree(data_dir, f"{dest}/data")
        print(f"[spec] OpenBabel：POSIX 布局 {bin_dir} + 数据 {data_dir}")
    else:
        print("[spec] 警告：找到 POSIX OpenBabel，但未找到数据目录")


def _collect_openbabel() -> None:
    package_dir = _openbabel_package_dir()

    # pip 的 openbabel-wheel：openbabel/bin 下已包含 DLL、obabel 与 data
    if package_dir and (package_dir / "bin").is_dir():
        _tree(package_dir / "bin", "openbabel/bin")
        print(f"[spec] OpenBabel：使用 wheel 布局 {package_dir / 'bin'}")
        return

    if not IS_WINDOWS:
        _collect_openbabel_posix()
        return

    bin_dir = PREFIX / "Library" / "bin"
    share_dir = PREFIX / "Library" / "share" / "openbabel"
    if not bin_dir.is_dir():
        print("[spec] 警告：未找到 OpenBabel 安装")
        return

    dest = "openbabel/bin"
    wanted = [
        "openbabel-3.dll",
        "obabel.exe",
        "inchi.dll",
        "zlib.dll",
        "libxml2.dll",
        "iconv.dll",
        "charset.dll",
    ]
    for name in wanted:
        source = bin_dir / name
        if source.is_file():
            binaries.append((str(source), dest))

    for pattern in ("formats_*.obf", "plugin_*.obf"):
        for source in sorted(bin_dir.glob(pattern)):
            # Cairo 绘图插件依赖一整套图形库，本软件不使用，跳过以减小体积
            if source.name == "formats_cairo.obf":
                continue
            datas.append((str(source), dest))

    data_root = _openbabel_data_dir(share_dir)
    if data_root:
        _tree(data_root, f"{dest}/data")
        print(f"[spec] OpenBabel：{bin_dir} + 数据 {data_root}")
    else:
        print("[spec] 警告：找到 OpenBabel 程序，但未找到数据目录")


_collect_openbabel()


# ----------------------------------------------------------------------
# RDKit / OpenMM：conda 将 DLL 放在 Library/{bin,lib}，需要显式收集
# ----------------------------------------------------------------------
def _add_if_exists(path: Path, destination: str) -> None:
    if path.is_file():
        binaries.append((str(path), destination))


def _collect_rdkit_and_openmm() -> None:
    if not IS_WINDOWS:
        # macOS/Linux conda 把共享库直接放在 $PREFIX/lib
        lib_dir = PREFIX / "lib"
        if lib_dir.is_dir():
            for pattern in (
                "libRDKit*.dylib",
                "libRDKit*.so*",
                "libOpenMM*.dylib",
                "libOpenMM*.so*",
                "libboost_*.dylib",
                "libboost_*.so*",
            ):
                for library in sorted(lib_dir.glob(pattern)):
                    if library.is_file():
                        binaries.append((str(library), "."))

        # OpenMM 插件：conda 放在 lib/plugins，部分布局放在 lib/openmm/plugins
        plugin_names = (
            "libOpenMMCPU",
            "libOpenMMPME",
            "libOpenMMAmoebaReference",
            "libOpenMMDrudeReference",
            "libOpenMMRPMDReference",
        )
        for plugin_dir in (lib_dir / "plugins", lib_dir / "openmm" / "plugins"):
            if not plugin_dir.is_dir():
                continue
            for stem in plugin_names:
                for suffix in (".dylib", ".so"):
                    for plugin in sorted(plugin_dir.glob(stem + "*" + suffix)):
                        binaries.append((str(plugin), "openmm_plugins"))
        return

    lib_dir = PREFIX / "Library" / "lib"
    bin_dir = PREFIX / "Library" / "bin"
    if lib_dir.is_dir():
        for dll in sorted(lib_dir.glob("RDKit*.dll")):
            binaries.append((str(dll), "."))
        for name in ("OpenMM.dll", "OpenMMAmoeba.dll", "OpenMMDrude.dll", "OpenMMRPMD.dll"):
            _add_if_exists(lib_dir / name, ".")
        for name in ("libbz2.dll", "zstd.dll", "zlib.dll", "liblzma.dll", "libxml2.dll", "iconv.dll"):
            _add_if_exists(bin_dir / name, ".")
        # conda 版 scipy 通过 liblapack 的转发导出使用 MKL；Meeko 只用到
        # scipy.spatial.cKDTree，加载 mkl_rt.2 即可，无需整套 MKL 内核。
        _add_if_exists(bin_dir / "mkl_rt.2.dll", ".")

    # conda 把 boost 运行库放在 Library/bin（而不是 Library/lib）
    for dll in sorted(bin_dir.glob("boost_*.dll")):
        binaries.append((str(dll), "."))

    plugin_dir = lib_dir / "plugins"
    if plugin_dir.is_dir():
        # 只收集 CPU / Reference 系列，避免把 CUDA、OpenCL 运行时塞进安装包
        for name in (
            "OpenMMCPU.dll",
            "OpenMMPME.dll",
            "OpenMMAmoebaReference.dll",
            "OpenMMDrudeReference.dll",
            "OpenMMRPMDReference.dll",
        ):
            _add_if_exists(plugin_dir / name, "openmm_plugins")

    # MSVC 运行库（部分精简版 Windows 未预装）
    for dll in sorted(bin_dir.glob("msvcp140*.dll")) + sorted(bin_dir.glob("vcruntime140*.dll")):
        binaries.append((str(dll), "."))


_collect_rdkit_and_openmm()


# ----------------------------------------------------------------------
# 对接引擎：Windows 使用内置二进制；macOS/Linux 若构建机已安装则一并打包
# ----------------------------------------------------------------------
if IS_WINDOWS:
    # resources/{vina,qvina2,smina}/windows/*.exe 已随 resources 目录收集。
    pass
else:
    platform_dir = "macos" if IS_MACOS else "linux"
    for engine, env_name, names in (
        ("vina", "DOCKFLOWLAB_VINA", ("vina",)),
        ("qvina2", "DOCKFLOWLAB_QVINA2", ("qvina2", "qvina02")),
        ("smina", "DOCKFLOWLAB_SMINA", ("smina",)),
    ):
        source = os.environ.get(env_name)
        if not source:
            for name in names:
                source = shutil.which(name)
                if source:
                    break
        if source and Path(source).is_file():
            binaries.append((source, f"dockflowlab/resources/{engine}/{platform_dir}"))
            print(f"[spec] 打包 {engine}：{source}")
        else:
            print(f"[spec] 提示：构建机未找到 {engine}，macOS/Linux 安装包需用户自行安装")


# ----------------------------------------------------------------------
# 分析
# ----------------------------------------------------------------------
hiddenimports += [
    "scipy.ndimage",
    "scipy.spatial",
    "openbabel.openbabel",
    "openbabel.pybel",
    "rdkit.Chem.AllChem",
    "rdkit.Chem.Descriptors",
    "rdkit.Chem.rdMolDescriptors",
    "rdkit.Chem.rdMolTransforms",
    "rdkit.Chem.rdDistGeom",
    "openmm.app",
    "openmm.app.internal",
    "openmm.app.internal.unitcell",
    "pdbfixer.pdbfixer",
    "meeko",
    "meeko.preparation",
    "meeko.writer",
    "meeko.rdkit_mol_create",
    "pyqtgraph",
    "pyqtgraph.opengl",
    "pyqtgraph.opengl.items",
    "pyqtgraph.opengl.items.GLMeshItem",
    "pyqtgraph.opengl.items.GLLinePlotItem",
    "pyqtgraph.opengl.items.GLScatterPlotItem",
    "pyqtgraph.opengl.MeshData",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLU",
    "OpenGL.arrays",
    "OpenGL.arrays.numpymodule",
    "OpenGL.platform",
]

# 让 PyInstaller 的依赖分析能看到 conda 的库目录
if IS_WINDOWS:
    for directory in (PREFIX / "Library" / "bin", PREFIX / "Library" / "lib", PREFIX / "DLLs"):
        if directory.is_dir():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
else:
    lib_path = str(PREFIX / "lib")
    for directory in (PREFIX / "bin", PREFIX / "lib"):
        if directory.is_dir():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    for variable in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        current = os.environ.get(variable, "")
        os.environ[variable] = lib_path + (os.pathsep + current if current else "")

excludes = [
    "tkinter",
    "_tkinter",
    "pytest",
    "_pytest",
    "IPython",
    "jupyter",
    "notebook",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "seaborn",
    "sklearn",
    "torch",
    "tensorflow",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hooks" / "rthook_dockflowlab.py")],
    # PLIP 的辅助模块里有函数级 ``import pymol``，主程序用不到；排除后
    # 避免把整套 PyMOL 再塞进界面进程（渲染由 PyMOLRunner 负责）。
    excludes=excludes + ["pymol", "pymol2", "chempy"],
    noarchive=False,
)

if IS_WINDOWS:
    # PyInstaller 会把 openbabel-3.dll 再复制一份到 _internal 根目录。
    # 两份同名 DLL 会让 Smina 选中根目录那份，而格式插件在
    # openbabel/bin 下，于是报 "Unable to find OpenBabel plugins"。
    # 只保留 openbabel/bin 中的一份，Python 扩展与引擎都从该目录加载。
    a.binaries = [
        entry
        for entry in a.binaries
        if str(entry[0]).replace("\\", "/").lower() != "openbabel-3.dll"
    ]

pyz = PYZ(a.pure)


def _merge_toc(base, extra):
    """把第二个 Analysis 的 TOC 并入主 TOC，同名目标只保留第一次出现的项。"""
    seen = {str(entry[0]).replace("\\", "/").lower() for entry in base}
    for entry in extra:
        key = str(entry[0]).replace("\\", "/").lower()
        if key not in seen:
            seen.add(key)
            base.append(entry)


pymol_exe = None
if HAS_PYMOL:
    pymol_a = Analysis(
        [str(ROOT / "packaging" / "pymol_runner.py")],
        pathex=[str(SRC)],
        binaries=pymol_binaries,
        datas=pymol_datas,
        hiddenimports=pymol_hidden,
        hookspath=[str(ROOT / "packaging" / "hooks")],
        runtime_hooks=[str(ROOT / "packaging" / "runtime_hooks" / "rthook_dockflowlab.py")],
        excludes=excludes + ["pymol.Qt", "pmg_qt", "pmg_tk"],
        noarchive=False,
    )
    pymol_pyz = PYZ(pymol_a.pure)
    pymol_exe = EXE(
        pymol_pyz,
        pymol_a.scripts,
        [],
        exclude_binaries=True,
        name="PyMOLRunner",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
    )
    # PyMOL 的 glew32 / netcdf 等 DLL 与主程序重叠，去重后共用一份。
    _merge_toc(a.binaries, pymol_a.binaries)
    _merge_toc(a.datas, pymol_a.datas)

icon = RESOURCES / "app.ico"
version_file = ROOT / "packaging" / "version_info.txt"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
    icon=str(icon) if IS_WINDOWS and icon.is_file() else None,
    version=str(version_file) if IS_WINDOWS and version_file.is_file() else None,
)

collect_args = [exe, a.binaries, a.datas]
if pymol_exe is not None:
    collect_args.append(pymol_exe)

coll = COLLECT(
    *collect_args,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if IS_MACOS:
    icns = ROOT / "packaging" / "DockFlowLab.icns"
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(icns) if icns.is_file() else None,
        bundle_identifier="com.dockflowlab.desktop",
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleName": APP_NAME,
            "CFBundleShortVersionString": "2.3.0",
            "CFBundleVersion": "2.3.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSPrincipalClass": "NSApplication",
        },
    )
