# DockFlowLab 架构说明

## 目标

- **低耦合**：界面只负责交互，计算流程放在服务层，外部程序通过适配器调用。
- **可测试**：纯解析/计算逻辑不依赖 Qt；服务层可在命令行与测试中直接调用。
- **可分发**：所有依赖都能被 PyInstaller 收集，运行时路径由统一入口解析。
- **跨平台**：Windows 为主，macOS/Linux 不写死路径；外部程序缺失时给出明确提示。

## 分层

```text
ui/          PySide6 五步向导、对话框、后台线程
  │ 调用
services/    准备、盒子、批量对接、分析、报告、下载、PyMOL
  │ 调用
core/        结构解析、PDBQT 校验、数据模型、几何计算（无 Qt 依赖）
  │ 调用
tools/       Vina / OpenBabel / 运行时 / 平台路径适配
workspace.py 项目目录与 project.json（服务层与 CLI 共用）
config.py    用户偏好（与项目无关）
```

`cli.py` 和 `ui/` 是并列入口，二者只依赖 `services/`、`workspace.py`、`config.py`，
不互相导入。

### core

- `models.py`：`ProjectFile`、`Box`、`Pose`、`DockingResult` 等 dataclass。
- `pdbio.py`：PDB/PDBQT 解析、元素推断、位点/共晶配体识别、复合物生成。
- 只做确定性的文本/几何处理，不启动外部程序。

### services

- `preparation.py`：编排受体与配体的准备流程，汇总每个条目的结果。
- `receptor.py` / `ligand.py`：具体的结构修复、加氢、Meeko 与 OpenBabel 回退。
- `boxes.py`：盒子推荐与校验，优先级为共晶配体 → 位点 → 自动推断 → 手动。
- `docking.py`：批量组合、跳过已有结果、写元数据、生成复合物与 PyMOL 脚本。
- `analysis.py`：结果表、矩阵、汇总 JSON、图表（matplotlib Agg）。
- `report.py`：自包含 HTML，图片以 base64 内嵌，不依赖网络。
- `download.py`：PDB / PubChem / AlphaFold / ChEMBL，带缓存与格式回退。
- `pymol.py`：生成 `.pml` 可视化脚本。

服务函数接收 `Workspace`、`AppSettings` 等显式参数，不读全局界面状态。

### tools

- `vina.py`：查找 Vina、在 ASCII 临时目录运行、解析 PDBQT 姿态。
- `obabel.py`：优先 Python API，失败时 CLI 回退；兼容 conda、wheel、系统安装与
  PyInstaller 内置布局。
- `runtime.py`：`resource_path`、临时目录、子进程封装、平台判断。
- `download` 相关网络逻辑放在 services，避免 tools 依赖业务模型。

### workspace

`Workspace` 管理一个工作目录：

- `project.json` 记录 schema、名称、条目（targets/ligands）与盒子设置。
- 内部目录名固定为英文；用户可见名称保存在 `display_name`，两者解耦。
- 内部文件名（stem）经过 ASCII 化并处理重名；中文名通过摘要后缀区分。
- 所有路径访问都经过 `absolute()`，拒绝越界路径。
- 保存采用临时文件 + `os.replace`，避免写一半损坏清单。

目录约定：

```text
inputs/{targets,ligands}/     原始文件
prepared/{targets,ligands}/   准备结果（PDB/PDBQT）
boxes/<stem>.json             盒子
docking/<target>/<ligand>/     Vina 输出与日志
complexes/                    复合物
analysis/                     表格、图表、summary.json
report/report.html
pymol/                        可视化脚本
logs/session.log
```

## 主要数据流

```text
导入/下载 → Workspace.import_* → inputs/
    → prepare_all() → prepared/ + 条目状态
    → suggest_box() / 手动盒子 → boxes/
    → run_batch() → docking/ + complexes/ + 元数据
    → analyze() → analysis/
    → generate_report() → report/
```

`DockingResult` 是结果页、CSV、报告和 PyMOL 的共同数据源，避免各自解析 Vina 日志。

## 线程与取消

界面中的耗时任务由 `ui/workers.py` 的 QThread 执行，通过信号回报进度与结果。
服务层接收 `progress` 回调与 `threading.Event` 取消标记；Vina 子进程在取消时会
被终止。界面线程不直接执行计算，也不直接操作外部进程。

## 错误处理

- `errors.py` 定义 `DockFlowError` 及输入、依赖、准备、对接等子类。
- 可预期的用户问题（文件损坏、缺依赖、盒子无效）转换为中文提示，不向界面抛裸异常。
- 单个条目失败不会中断整个批次；结果对象保留错误信息，最终汇总展示。
- 未捕获异常由 `app.py` 的异常钩子写日志并弹出可复制的技术详情。

## 路径与编码

- 外部程序对中文路径兼容性差：Vina/OpenBabel 在 `ascii_temp_root()` 下运行，
  完成后复制回项目。
- 报告、CSV、JSON 一律 UTF-8；日志文件使用 UTF-8。
- 资源通过 `resource_path()` 查找，同时支持源码运行与 PyInstaller
  `_MEIPASS` 布局。

## 打包集成

`packaging/dockflowlab.spec` 收集 Python 包、Qt、RDKit/Boost、OpenMM 插件、
OpenBabel DLL/格式插件/数据、Vina 与报告模板。运行时钩子
`rthook_dockflowlab.py` 设置：

- `BABEL_DATADIR`、`BABEL_LIBDIR`
- `OPENMM_PLUGIN_DIR`
- Windows DLL 搜索目录与 `PATH`

具体说明见 [packaging.md](packaging.md)。

## 测试策略

- `tests/test_workspace.py`：项目创建、中文名称、路径安全、损坏 JSON。
- `tests/test_pdbio.py`：真实 1BCI 解析、位点、复合物、PDBQT 校验。
- `tests/test_boxes.py`：盒子优先级、边界、错误输入。
- `tests/test_analysis_report.py`：结果汇总与自包含报告。
- `tests/test_download.py`：用 mock 网络验证格式回退。
- `tests/test_cli.py`：命令行参数与失败路径。
- `tests/test_integration.py`：真实准备 → 盒子 → Vina 对接 → 分析 → 报告（标记 slow）。

运行：

```bash
python -m pytest
python -m pytest -m "not slow"   # 跳过真实对接
```
