# DockFlowLab 架构说明

## 目标

- **低耦合**：界面只负责交互，计算流程放在服务层，外部程序通过适配器调用。
- **可测试**：纯解析/计算逻辑不依赖 Qt；服务层可在命令行与测试中直接调用。
- **可分发**：所有依赖都能被 PyInstaller 收集，运行时路径由统一入口解析。
- **跨平台**：Windows 为主，macOS/Linux 不写死路径；外部程序缺失时给出明确提示。

## 分层

```text
ui/          PySide6 五步向导、PyMOL 渲染页、对话框、后台线程
  │ 调用
services/    准备、盒子、批量对接、分析、报告、下载、PyMOL 渲染
  │ 调用
core/        结构解析、PDBQT 校验、数据模型、几何计算（无 Qt 依赖）
  │ 调用
tools/       Vina / OpenBabel / 运行时 / 平台路径适配
workspace.py 项目目录与 project.json（服务层与 CLI 共用）
config.py    用户偏好（与项目无关）
```

`cli.py` 和 `ui/` 是并列入口，二者只依赖 `services/`、`workspace.py`、`config.py`，
不互相导入。

- `ui/box_view.py` / `ui/ngl_view.py`：3D 盒子编辑器。优先用 QtWebEngine 内嵌
  NGL Viewer 2.5（MIT，离线打包）渲染接近 PyMOL 的卡通模型与元素着色球棍；
  盒子线框、七个拖拽手柄、点击原子设置中心、聚焦/重置视角都在
  `resources/ngl/viewer.html` 的 JavaScript 中完成，通过 `QWebChannel` 把
  盒子参数回传 Python。`ngl_view.py` 还负责修正 conda / PyInstaller 布局下
  QtWebEngine 的资源、语言包与渲染进程路径。
- 回退路径：NGL 初始化失败（无 QtWebEngine、WebGL 不可用）时，`box_view.py`
  切换到基于 `pyqtgraph.opengl` / PyOpenGL 的原生视图。该视图用主链 φ/ψ 角
  分配二级结构并放样卡通几何；投影、射线拾取与缩放都是无 Qt 的纯函数，便于
  测试。两条路径都不可用时退化为数值输入。导入 pyqtgraph 之前固定
  `PYQTGRAPH_QT_LIB=PySide6`，避免与环境中并存的 PyQt5/PyQt6 混用。
- `ui/render_page.py`：可选的“PyMOL 渲染”页面。复合物、脚本与分辨率由界面
  收集，实际渲染通过 `Task` 调用 `services/pymol_render.py`；预览图按控件大小
  等比缩放，不加载完整尺寸的像素到界面布局。页面在未检测到 PyMOL 或没有复合物
  时禁用渲染按钮并说明原因。用户脚本目录、上次分辨率与标签字号保存在
  `AppSettings`。PyMOL 的标签按固定像素绘制，渲染时按“输出尺寸 / 900 ×
  用户倍数”改写脚本中的 `label_size`，避免高分辨率下残基编号和氢键距离过小。
- `ui/heatmap_dialog.py`：结果页“编辑热图”对话框。表格中的第三列可直接修改
  靶点与候选分子的显示名称，另有标题、副标题、配色和输出格式；改动先在临时
  目录渲染预览，确认后写入项目设置并重新生成热图与 HTML 报告。

### core

- `models.py`：`ProjectFile`、`Box`、`Pose`、`DockingResult` 等 dataclass。
  `DockingResult` 额外记录对接类型（`mode_key`、`mode_label`、`mode_supported`）。
- `pdbio.py`：PDB/PDBQT 解析、元素推断、结构类型识别（蛋白 / 多肽 / 核酸 /
  小分子）、位点/共晶配体识别（含标准氨基酸与非标准残基混合的多残基肽类
  配体链）、指定链移除、复合物生成。
- 只做确定性的文本/几何处理，不启动外部程序。

### services

- `modes.py`：对接场景注册表。根据靶点与候选分子的结构类型解析出
  `DockingMode`（是否支持、显示名称、科学限制、替代工具建议）。结构类型优先
  读取准备阶段写入的 `meta["kind"]`，否则按文件内容识别，并用文件时间戳/大小
  做 LRU 缓存。所有入口（界面、CLI、对接、分析、报告）共用这一份判断。
- `preparation.py`：编排受体与配体的准备流程，汇总每个条目的结果，并把结构
  类型写入条目元数据。
- `receptor.py` / `ligand.py`：具体的结构修复、加氢、Meeko 与 OpenBabel 回退。
  受体准备会先整链移除共晶配体所在的短链（包括肽类抑制剂），再做 PDBFixer
  修复，避免配体的标准残基被当成受体的一部分。需要金属酶研究时，
  `ReceptorPrepOptions.keep_metals` 会在 PDBFixer 之前只保留聚合物、修饰残基
  与 `pdbio.METAL_IONS`，从而绕开 `removeHeterogens` 对离子的无差别删除；水、
  添加剂和共晶配体仍被移除。小分子靶点走刚性受体流程。
- `pockets.py`：基于 LIGSITE 思路的几何口袋检测（NumPy/SciPy，无需外部程序），
  对蛋白、核酸与多肽一视同仁。
- `boxes.py`：盒子推荐与校验。手动 / 参考分子优先；自动模式先用几何口袋，
  再与共晶配体、`SITE` 记录做一致性校验，实验位点作为兜底。小分子靶点按
  两侧分子尺寸估算。`Box.needs_review` 标记盲对接与估算盒子，界面据此提示
  用户手动确认中心与边长，而不是静默使用。`known_site_highlights()` 把共晶
  配体与 `SITE` 残基整理为独立原子组，供盒子编辑器叠加参考层；它只读原始
  结构，不参与盒子计算。
- `docking.py`：批量组合、跳过已有结果、写元数据、生成复合物与 PyMOL 脚本。
- `analysis.py`：结果表、矩阵、汇总 JSON、图表（matplotlib Agg）。热图支持
  自定义行列名称、标题、单色相配色与 PNG/SVG/PDF 输出；这些设置保存在
  `analysis/heatmap_config.json`，`analyze()` 每次都会重新应用。SVG 使用
  `svg.fonttype=none` 保留真实文字，便于后续编辑。
- `report.py`：自包含 HTML，图片以 base64 内嵌，不依赖网络。
- `download.py`：PDB / PubChem / AlphaFold / ChEMBL，带缓存与格式回退。
- `pymol.py`：生成复合物（以及可选的盒子预览）`.pml` 脚本，查找并启动 PyMOL。
  PyMOL 在中文 Windows 上按系统区域编码读取脚本，且部分构建遇到非 ASCII
  字符会崩溃，因此脚本正文保持纯 ASCII；中文路径优先转成相对路径或 8.3 短路径，
  仍不可用时复制到 ASCII 临时目录。
- `interactions.py` / `plip_analysis.py`：对接细节视图的相互作用分析。优先用
  内置 PLIP（补全极性氢后识别氢键、盐桥、π 堆积、π-阳离子、卤键、金属配位与
  疏水作用）；PLIP 不可用、没有识别到配体或分析失败时回退到 `interactions.py`
  的纯几何判定，界面始终可用。范德华接触不是 PLIP 的输出类型，由几何判定补充；
  PLIP 对配体金属结合基团的规则不完整（例如磺酰胺氮），因此金属–配体 N/O/S
  距离 ≤ 3.0 Å 的接触也由几何判定补入。两者
  共用 `Interaction` / `InteractionAnalysis` 数据模型（按残基去重并按类型
  优先级排序），`engine` 字段标明本次结果来自哪种方式。输入结构会先去掉已有
  氢原子并规范元素列（旧复合物把 AutoDock 类型 ``A`` / ``OA`` 写进了元素列），
  避免 OpenBabel 把芳香碳当成未知元素。
- `pymol_render.py`：把用户风格脚本套用到复合物并输出 PNG。PyMOL 始终以
  `-cq` 独立进程运行，不导入主进程，也不长期驻留；安装包使用内置的
  `PyMOLRunner.exe`（`packaging/pymol_runner.py`，`pymol2` 无界面会话），
  源码运行时可回退到本机 `pymol`。结构、转码后的脚本与输出都放在 ASCII
  临时目录，完成后只复制 PNG 回项目。`styles_in()` 递归枚举 `.pml` / `.txt`，
  `complexes_in()` 扫描 `complexes/`，`style_token()` 生成保留中文的输出标签。
  渲染带超时，取消/超时时调用 `run_process(kill_tree=True)` 结束 PyMOL 启动器
  及其派生的 `python.exe`，因此原生崩溃或卡死不会影响界面。

服务函数接收 `Workspace`、`AppSettings` 等显式参数，不读全局界面状态。

### tools

- `vina.py`：对接引擎适配层（AutoDock Vina / QuickVina 2 / Smina）。统一查找
  可执行文件、补充 DLL 搜索路径、在 ASCII 临时目录运行、解析 PDBQT 姿态；
  Vina 用 `--config`，两个 Vina 1.1.2 风格引擎用命令行参数。Smina 输出的
  `minimizedAffinity` 会改写为标准 `VINA RESULT` 备注，供后续统一解析。
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
pymol/renders/                风格脚本渲染的 PNG
logs/session.log
```

## 主要数据流

```text
导入/下载 → Workspace.import_* → inputs/
    → prepare_all() → prepared/ + 条目状态 + meta["kind"]
    → pair_mode()（modes.py）→ 支持性判断与对接类型
    → suggest_box() / 手动盒子 → boxes/
    → run_batch() → docking/ + complexes/ + 元数据
    → analyze() → analysis/
    → generate_report() → report/
```

对接类型在 `modes.py` 中集中定义，不支持的组合在 `run_batch()` 入口即被拦截，
不会启动 Vina。`DockingResult` 是结果页、CSV、报告和 PyMOL 的共同数据源，
避免各自解析 Vina 日志。

### 对接模式

| 模式 | 靶点 | 配体 | 说明 |
| --- | --- | --- | --- |
| `protein_small_molecule` | 蛋白质 | 小分子 | 主流程 |
| `peptide_small_molecule` | 多肽 | 小分子 | 靶点柔性较高 |
| `nucleic_acid_small_molecule` | DNA/RNA | 小分子 | 评分仅宜做相对排序 |
| `small_molecule_small_molecule` | 小分子 | 小分子 | 靶点按刚性受体处理 |

不支持的组合由 `_unsupported()` 生成明确的 `guidance`（例如蛋白–蛋白、
蛋白–多肽指向 ClusPro / HADDOCK / RosettaDock），而不是返回空分数。
以蛋白质或多肽作为候选分子的组合暂不支持：多肽的扭转自由度远超 Vina
能可靠搜索的范围。结构分类以 20 个残基 / 500 个重原子区分蛋白与多肽。

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
OpenBabel DLL/格式插件/数据、PLIP 模块、PyMOL 与 Vina 等资源。PyMOL 通过第二个
`Analysis` 生成 `PyMOLRunner.exe`，与主程序共用 `_internal`，避免把 PyMOL
载入界面进程；主程序则排除 PyMOL，防止 PLIP 辅助模块里的函数级导入把它带进
界面进程。运行时钩子 `rthook_dockflowlab.py` 设置：

- `BABEL_DATADIR`、`BABEL_LIBDIR`
- `OPENMM_PLUGIN_DIR`
- Windows DLL 搜索目录与 `PATH`

具体说明见 [packaging.md](packaging.md)。

## 测试策略

- `tests/test_workspace.py`：项目创建、中文名称、路径安全、损坏 JSON。
- `tests/test_pdbio.py`：真实 1BCI 解析、位点、复合物、PDBQT 校验。
- `tests/test_modes.py`：结构类型识别（含 DNA）、支持矩阵、不支持组合不调用
  Vina、准备阶段类型元数据。
- `tests/test_boxes.py`：盒子优先级、边界、错误输入。
- `tests/test_pockets.py`：几何口袋检测、实验位点一致性、盲对接兜底。
- `tests/test_box_view.py`：盒子顶点/手柄/缩放/射线几何、二级结构判定与卡通
  网格、结构加载、PDBQT → PDB 文本转换、NGL 桥接参数校验与钳制、QtWebEngine
  路径探测、PyMOL 脚本的 ASCII 与中文路径处理、数值输入与自动推荐的状态同步。
- `tests/test_analysis_report.py`：结果汇总与自包含报告。
- `tests/test_interactions.py` / `tests/test_plip_analysis.py`：几何分类与按残基
  去重、PLIP 输入文本整理（元素规范化与去氢）、去重/截断、未安装 PLIP 时的
  回退，以及 PLIP 可用时的端到端分析（未安装则跳过）。
- `tests/test_engines.py`：三种引擎的命令构建、Smina 输出解析、卡死检测、
  自动回退与打包环境的 OpenBabel 搜索路径。
- `tests/test_download.py`：用 mock 网络验证格式回退。
- `tests/test_cli.py`：命令行参数与失败路径。
- `tests/test_integration.py`：真实准备 → 盒子 → Vina 对接 → 分析 → 报告（标记 slow）。

运行：

```bash
python -m pytest
python -m pytest -m "not slow"   # 跳过真实对接
```
