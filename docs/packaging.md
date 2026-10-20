# 打包与发布

DockFlowLab 使用 **PyInstaller onedir** 打包：启动速度快、DLL 与数据文件可检查，
出现缺库时容易定位。Windows 产物是目录 + exe，macOS 产物是 `.app`。

## 构建环境

- Python 3.10–3.12（推荐 3.12）
- PyInstaller 6.x
- 与运行环境一致的化学依赖：RDKit、Meeko、OpenBabel、OpenMM、PDBFixer
- Windows 需要 MSVC 运行库（conda 环境通常已带）

推荐直接使用开发用的 conda 环境：

```bash
conda create -n DockFlowLab python=3.12 -y
conda activate DockFlowLab
conda install -c conda-forge rdkit openbabel openmm pdbfixer -y
pip install -e ".[dev]" meeko pyinstaller
```

pip 环境（macOS/Linux 更容易成功）：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[chem,build,dev]"
```

> **不要**在打包环境里同时存在两套 OpenBabel（conda 的 `Library/bin` 与
> pip 的 `openbabel/bin`）。spec 会优先使用 wheel 布局，但混装容易导致 DLL 冲突。

## Windows 构建

在仓库根目录、已激活的目标环境中执行：

```bat
packaging\build_windows.bat
```

脚本会：

1. 检查 PyInstaller / PySide6 / RDKit / Meeko / OpenMM / PDBFixer 是否可导入；
2. 运行 `python -m PyInstaller --clean --noconfirm packaging\dockflowlab.spec`；
3. 将 `dist\DockFlowLab` 压缩为 `release\DockFlowLab-Windows-x64.zip`。

也可以手动执行：

```bat
python -m PyInstaller --clean --noconfirm packaging\dockflowlab.spec
```

产物：

```text
dist/DockFlowLab/
  DockFlowLab.exe
  _internal/
    dockflowlab/resources/vina/windows/vina.exe
    openbabel/bin/{openbabel-3.dll, obabel.exe, formats_*.obf, data/}
    openmm_plugins/OpenMMCPU.dll ...
    RDKit*.dll, boost_*.dll, Qt6*.dll ...
```

内置 Vina 为 AutoDock Vina 1.2.7 官方 Windows 二进制，路径由
`tools/runtime.resource_path()` 在源码与打包环境中统一解析。

### 构建诊断版（带控制台）

正式版 `console=False`，启动阶段的错误不会显示在终端。排查打包问题时构建控制台版：

```bat
set DOCKFLOWLAB_CONSOLE=1
python -m PyInstaller --clean --noconfirm packaging\dockflowlab.spec
dist\DockFlowLabConsole\DockFlowLabConsole.exe
```

控制台版与正式版共用同一份 spec，只是入口为 `console=True`，不会进入发布压缩包。

### 发布前自检

在控制台版中运行完整环境检查：

```bat
set DOCKFLOWLAB_SELF_CHECK=1
dist\DockFlowLabConsole\DockFlowLabConsole.exe
```

应返回 `"ok": true`，并显示 Vina、OpenBabel、RDKit、Meeko、OpenMM、PDBFixer 的版本。
这项检查会真实导入化学组件，能暴露缺失 DLL 的问题。

还可以临时把控制台版当命令行使用：

```bat
set DOCKFLOWLAB_CLI=1
dist\DockFlowLabConsole\DockFlowLabConsole.exe init _testwork/demo
dist\DockFlowLabConsole\DockFlowLabConsole.exe prepare _testwork/demo
dist\DockFlowLabConsole\DockFlowLabConsole.exe dock _testwork/demo --exhaustiveness 1
```

正式发布前建议用 `tests/data` 中的 1BCI 与阿司匹林跑一次完整的
“导入 → 准备 → 对接 → 报告”，确认 Vina、Meeko、OpenBabel、matplotlib 都在包内。

## macOS 构建

```bash
bash packaging/build_macos.sh
```

脚本会构建 `dist/DockFlowLab.app` 并压缩为 `release/DockFlowLab-macOS-<arch>.zip`。
如果构建机的 `PATH` 中有 `vina`，会自动收进应用包；也可以通过环境变量指定：

```bash
DOCKFLOWLAB_VINA=/opt/homebrew/bin/vina bash packaging/build_macos.sh
```

### 图标

Windows 使用 `src/dockflowlab/resources/app.ico`。macOS 的 `.app` 图标需要
`packaging/DockFlowLab.icns`；提供该文件后 spec 会自动使用，否则使用系统默认图标。
可以用 macOS 的 `iconutil` 从 PNG 图标集生成：

```bash
mkdir DockFlowLab.iconset
sips -z 16 16 app.png --out DockFlowLab.iconset/icon_16x16.png
# ... 生成 32/128/256/512 及 @2x 尺寸
iconutil -c icns DockFlowLab.iconset -o packaging/DockFlowLab.icns
```

### 签名与公证（可选）

未签名应用在 macOS 上会被 Gatekeeper 拦截，用户可以右键 →“打开”。正式分发建议：

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  dist/DockFlowLab.app

xcrun notarytool submit release/DockFlowLab-macOS-arm64.zip \
  --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PASSWORD \
  --wait

xcrun stapler staple dist/DockFlowLab.app
```

## spec 的收集逻辑

`packaging/dockflowlab.spec` 按平台与安装形态收集：

| 依赖 | 收集内容 |
| --- | --- |
| DockFlowLab | `src/dockflowlab/resources` 全部资源 |
| RDKit | `collect_all("rdkit")` + conda `Library/lib/RDKit*.dll` |
| OpenBabel | wheel 的 `openbabel/bin`；conda 的 DLL、`obabel.exe`、`formats_*.obf`、`plugin_*.obf`、数据目录 |
| OpenMM | `collect_all("openmm")` + conda 的 `OpenMM*.dll` 与 CPU/Reference 插件 |
| PDBFixer / Meeko | `collect_all` 的 Python 数据文件 |
| Vina | `resources/vina/windows/vina.exe`；macOS 构建时按需收集 |
| Matplotlib | PyInstaller 官方 hook，图表使用 Agg 后端 |

conda 把 Boost、OpenBabel、OpenMM、MKL 的 DLL 放在 `Library/bin` 或
`Library/lib`，而不是 Python 包目录，因此 spec 在分析前把这些目录加入 `PATH`，
让 PyInstaller 能解析依赖闭包。Qt 由 PyInstaller 的 PySide6 hook 处理。

conda 版 SciPy 依赖 MKL。Meeko 只需要 `scipy.spatial.cKDTree`，spec 收集
`mkl_rt.2.dll` 即可满足导入；不需要把数百 MB 的 MKL 内核全部打包。如果未来代码
直接调用 SciPy 的 BLAS/LAPACK，需要再补充 `mkl_core.2.dll` 等运行库。

## 运行时钩子

`packaging/runtime_hooks/rthook_dockflowlab.py` 在 Python 启动、业务模块导入之前执行：

- 设置 `BABEL_LIBDIR` 与 `BABEL_DATADIR`；
- 设置 `OPENMM_PLUGIN_DIR`；
- 在 Windows 上把 `_internal` 与 `openbabel/bin` 加入 DLL 搜索目录和 `PATH`；
- 默认 `MPLBACKEND=Agg`，避免图表依赖 Qt 后端。

## 常见打包问题

**`ImportError: attempted relative import with no known parent package`**
PyInstaller 把入口脚本当作顶层 `__main__`。入口必须用绝对导入，因此 spec 使用
`packaging/launcher.py`，而不是 `src/dockflowlab/__main__.py`。

**`DLL load failed while importing ...`**
用控制台版运行并查看缺失模块；检查 `_internal` 中是否存在对应 DLL。Windows 上优先
用 `Dumpbin /dependents` 或 `pefile` 查看依赖，再把所在目录加入 spec。

**Meeko 导入失败，回退到 OpenBabel**
确认 `scipy` 未被 `excludes` 排除，且 `mkl_rt.2.dll` 在 `_internal` 中。回退本身
是可用路径，但会缺少 RDKit 力场优化与 Meeko 的原子类型细节。

**PySide6 启动时报找不到平台插件**
确认没有手动删除 `_internal/PySide6/plugins/platforms`；conda 与 pip 混装 Qt 时
先清理环境再重建。

**安装包过大**
onedir 中包含 Qt、RDKit、OpenMM、OpenBabel 与数据文件，通常在 300 MB 左右（压缩后
更小）。可以通过排除未使用的 Qt 模块/插件进一步缩小，但不要删除
`platforms`、`styles`、`imageformats` 与 `translations`。

## 发布清单

- [ ] `python -m pytest` 全部通过
- [ ] `DOCKFLOWLAB_SELF_CHECK=1` 返回 `ok: true`
- [ ] 用真实结构跑通导入、准备、对接、报告
- [ ] 检查 `dist/DockFlowLab/_internal/dockflowlab/resources/vina/...`
- [ ] 在未安装 Python 与 conda 的干净机器上启动测试
- [ ] 压缩包命名包含平台与架构，并附带 README/使用指南
- [ ] macOS 应用完成签名/公证（正式发布时）
