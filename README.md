# DockFlowLab

面向科研人员的跨平台分子对接桌面工具。无需写代码，按五步向导即可完成
**靶点蛋白 + 候选分子** 的结构准备、结合位点选择、AutoDock Vina 对接与结果报告。

- 主流程：小分子与蛋白对接
- 支持导入 PDB / mmCIF / PDBQT / MOL2 / SDF / MOL 等结构
- 自动修复缺失原子、按生理 pH 加氢、生成 PDBQT
- 优先使用共晶配体或已知位点确定搜索盒子，也可自动推断或手动指定
- 输出结合能表、热图、柱状图和可离线打开的中文 HTML 报告
- 项目目录自动整理，原始文件与计算产物分开保存

> DockFlowLab 的对接分数是经验评分函数的输出，用于缩小实验筛选范围，
> 不等同于实验测得的结合亲和力。关键结论请结合实验或文献验证。

## 普通用户：下载即用

### Windows

1. 下载并解压 `DockFlowLab-Windows-x64.zip`。
2. 双击文件夹里的 `DockFlowLab.exe`。
3. 首次启动被 SmartScreen 拦截时，点击“更多信息 → 仍要运行”。

安装包已内置 AutoDock Vina 1.2.7，无需另装。程序不写注册表，删除文件夹即可卸载；
用户设置与日志位于 `%APPDATA%\DockFlowLab` 与 `%LOCALAPPDATA%\DockFlowLab`。

### macOS

1. 下载对应架构的 `DockFlowLab-macOS-*.zip`，解压后把 `DockFlowLab.app` 拖入“应用程序”。
2. 首次打开若提示“无法验证开发者”，右键应用 →“打开”，或在“系统设置 → 隐私与安全性”中允许。
3. 如果发布包未内置 Vina，请先安装：

   ```bash
   brew install autodock-vina
   # 或：conda install -c conda-forge vina
   ```

   也可以在程序“设置”中手动指定 `vina` 可执行文件。

## 五步使用流程

1. **工作目录**：选择一个空文件夹或已有项目。所有文件都保存在这个目录内。
2. **添加分子**：把靶点蛋白和候选分子拖进窗口，或从 PDB / PubChem / AlphaFold / ChEMBL 下载。
3. **检查与准备**：程序补全缺失原子、加氢、检查价态并生成对接格式；有问题的条目会标红说明。
4. **设置对接**：为每个靶点确认搜索盒子；优先采用共晶配体位置，其次自动推断，最后可手动输入。
5. **运行与结果**：开始对接，查看打分表与图表，生成 HTML 报告，或用 PyMOL 打开复合物。

名词约定：**靶点蛋白**（接收分子的蛋白结构）与 **候选分子**（待筛选的小分子）。
项目内部使用 `targets/`、`ligands/` 等英文目录名，界面显示中文名称。

## 命令行

图形界面和命令行共用同一套服务层，便于批处理与服务器使用：

```bash
# 查看环境（Vina、OpenBabel、RDKit、Meeko、OpenMM 等）
dockflowlab-cli doctor

# 新建项目
dockflowlab-cli init D:/docking/demo --name 阿司匹林筛选

# 导入结构
dockflowlab-cli import D:/docking/demo protein.pdb --kind target --name 1BCI
dockflowlab-cli import D:/docking/demo aspirin.sdf --kind ligand --name 阿司匹林

# 准备、对接、报告
dockflowlab-cli prepare D:/docking/demo
dockflowlab-cli dock D:/docking/demo --exhaustiveness 8
dockflowlab-cli report D:/docking/demo
```

也可以从公共数据库直接下载：

```bash
dockflowlab-cli fetch D:/docking/demo pdb 1BCI
dockflowlab-cli fetch D:/docking/demo pubchem "aspirin" --kind ligand
```

## 从源码运行

需要 Python 3.10–3.12。推荐使用 conda 环境（Windows 上 OpenMM 用 conda 安装最省事）：

```bash
conda create -n DockFlowLab python=3.12 -y
conda activate DockFlowLab
conda install -c conda-forge rdkit openbabel openmm pdbfixer -y
pip install -e ".[dev]" meeko
python -m dockflowlab
```

只使用 pip 时：

```bash
python -m pip install -e ".[chem,dev]"
python -m dockflowlab
```

Windows 上若 `openmm` 没有可用的 wheel，请改用上面的 conda 命令。

运行测试：

```bash
python -m pytest
```

## 打包分发

Windows：

```bat
packaging\build_windows.bat
```

macOS：

```bash
bash packaging/build_macos.sh
```

产物位于 `release/`，目录版同时保留在 `dist/`。详细说明（依赖布局、图标、
Vina、macOS 签名与公证）见 [docs/packaging.md](docs/packaging.md)。

## 项目结构

```text
src/dockflowlab/
  app.py            图形界面入口
  cli.py            命令行入口
  workspace.py      工作目录与项目文件
  core/             结构解析、数据模型等纯计算基础
  services/         准备、盒子、对接、分析、报告、下载
  tools/            Vina / OpenBabel / 运行时适配
  ui/               PySide6 五步向导界面
  resources/        图标、报告模板、内置 Vina
packaging/          PyInstaller 配置与构建脚本
tests/              单元测试与端到端集成测试
docs/               用户、架构与打包文档
```

更多设计说明见 [docs/architecture.md](docs/architecture.md)，界面操作细节见
[docs/user-guide.md](docs/user-guide.md)。

## 许可

本项目使用 MIT License。内置的 AutoDock Vina 1.2.7 由 The Scripps Research
Institute 以 Apache License 2.0 发布，许可见
[src/dockflowlab/resources/vina/LICENSE](src/dockflowlab/resources/vina/LICENSE)。

## 致谢

AutoDock Vina、Meeko、RDKit、OpenBabel、OpenMM、PDBFixer、PySide6、
matplotlib 与 pandas 的开发者与维护者。
