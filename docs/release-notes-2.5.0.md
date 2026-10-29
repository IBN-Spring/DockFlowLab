# DockFlowLab 2.5.0

> 柔性侧链、配体状态枚举，以及一套能解释“结果为什么可信”的复核工具。

DockFlowLab 是一个离线运行的分子对接桌面工作台：结构准备、口袋检测、盒子设置、
AutoDock Vina / QuickVina 2 / Smina 对接、相互作用分析与中文报告都在同一个窗口完成。
2.5.0 在 2.3.0 的基础上补齐了受体柔性与配体状态处理，并把重对接、构象收敛、结构体检
等复核步骤做成了日常流程的一部分。

## 主要更新

### 柔性侧链

- 在“设置对接”页为每个靶点选择最多 **12 个**可旋转残基：程序按搜索盒子中心 6 Å
  自动推荐，也可以手动勾选（列表展示 12 Å 内的残基）。
- Vina / Smina 使用 `--flex` 让选中侧链与配体一起采样；QuickVina 2 不支持柔性残基，
  开始前会提示切换引擎。
- 柔性受体按“靶点 + 残基选择”缓存，同一靶点的不同配体、不同配体状态复用同一份拆分文件。
- 界面直接说明耗时关系：大致按“柔性残基数 × 精细度 × 配体状态数”成倍增加，建议只保留
  真正挡住配体的 2–4 个侧链，或先用快速（8）设置看趋势。

### 配体状态枚举

- 默认枚举 **4 个**互变异构 / 质子化状态；每个状态单独准备、单独对接，结果表只展示最优。
- 状态分数写入日志、结果参数与报告；最优状态不是原始结构时会给出提示。
- 命令行新增 `prepare --states {1,2,4,8}`，`--states 1` 即关闭枚举。

### 结果复核与可信度

- **重对接验证**：用靶点自带的共晶配体做对照对接，按对称校正 RMSD 判断盒子与参数能否
  复现实验构象，结果写入项目与报告。
- **构象浏览 + 收敛提示**：逐个查看 Vina 输出构象，切换时重新分析相互作用；最佳与次优
  分数接近时提示收敛性不足。
- **结构体检卡**：解析缺失残基 / 原子、altloc、占有率、金属离子与共晶配体，开始对接前
  提示可能影响结果的结构问题。
- **exhaustiveness 建议**：按配体可旋转键与重原子数给出搜索深度建议，实际值偏低时提醒。
- **配体效率与结果可信度卡**：按 `LE = −ΔG / 重原子数` 计算配体效率，集中展示盒子来源、
  搜索深度、结构体检、重对接与相互作用来源。
- **批量相互作用筛选**：为整批结果建立带缓存的相互作用索引，可按类型、残基包含/排除
  过滤结果表。
- 相互作用结果可导出 CSV；Smina 支持 `--scoring vinardo`；氢键的供体–氢–受体几何证据
  提示更明确。

### 相互作用细节视图

- 默认关闭蛋白卡通背景，只保留配体与接触残基，避免半透明蛋白盖在配体上造成“断点”或
  结构错乱；需要判断整体位置时可在底部勾选“蛋白背景”。
- 接触残基线框更细、配体略微加粗；没有识别到相互作用时自动把视角对准配体。
- 支持按类型隐藏接触、显示/隐藏 3D 标签与右侧列表，点击条目定位残基。

### 界面与稳定性

- “设置对接”页按“盒子 → 搜索参数 → 本次任务”重新组织，柔性残基集中在一个入口。
- 配体能量最小化优先 MMFF94s；参数缺失或优化器内部报错时自动回退 UFF，不再把 RDKit
  底层异常显示给用户。
- 新增 Windows 向导式安装包（NSIS）：按用户安装，不需要管理员权限，自动创建开始菜单与
  桌面快捷方式，并支持在“设置 → 应用”中卸载。

## 下载

| 平台 | 文件 | 大小 | SHA256 |
| --- | --- | --- | --- |
| Windows 10/11 x64（安装版） | `DockFlowLab-Setup-2.5.0-x64.exe` | 211 MB | `c8bc71dd3784061a8f05c3526f3107d5558943a5202ee362804bd55601629506` |
| Windows 10/11 x64（便携版） | `DockFlowLab-Windows-x64.zip` | 325 MB | `b3d75f074ff9cbac426143eb2e2403324c3343d4d112701435ddcb9102e685c5` |
| macOS 11+ Apple Silicon | `DockFlowLab-macOS-arm64.dmg` | 332 MB | `1a8a00f86f2ef8e337b425f0afd0f99b3d2e9c3c5529e53b1753421cb0fc0e97` |
| macOS 11+ Apple Silicon（zip） | `DockFlowLab-macOS-arm64.zip` | 338 MB | `cccd9b70da84546cdaa336b4061d5f422ce4f8ca7711739b8b745ae23e73e5d3` |

Windows 与 macOS 安装包均内置 AutoDock Vina 1.2.7、QuickVina 2.1、Smina 2020.12.10、
无界面 PyMOL 3.1，以及 PLIP 3.0.1、RDKit、OpenBabel、Meeko、OpenMM / PDBFixer。

## 安装

### Windows（安装版）

1. 运行 `DockFlowLab-Setup-2.5.0-x64.exe`。
2. 选择安装目录（默认 `%LOCALAPPDATA%\Programs\DockFlowLab`），按向导完成。
3. 安装按当前用户进行，不需要管理员权限；开始菜单与桌面会生成快捷方式。
4. 首次运行若被 SmartScreen 拦截，点击“更多信息 → 仍要运行”。

静默安装：`DockFlowLab-Setup-2.5.0-x64.exe /S /LANG=2052`。

### Windows（便携版）

解压 `DockFlowLab-Windows-x64.zip`，双击 `DockFlowLab.exe`。不写注册表，删除程序目录即可。

### macOS

1. 打开 `DockFlowLab-macOS-arm64.dmg`，把 `DockFlowLab.app` 拖入“应用程序”。
2. 首次打开若提示“无法验证开发者”，右键应用 →“打开”，或在“系统设置 → 隐私与安全性”
   中允许；也可以执行
   `xattr -dr com.apple.quarantine /Applications/DockFlowLab.app`。
3. 当前构建未做 Apple 公证，首次打开需要上述步骤。

## 升级说明

- 2.3.0 创建的项目可以直接打开，项目目录结构不变。
- 修改柔性残基选择或状态枚举数量后，相关对接结果会被判定为需要重算；对接记录会注明
  实际使用的状态、柔性残基与引擎版本。
- 柔性侧链只在搜索阶段生效，保存的复合物与可视化文件仍使用原始侧链坐标。
- 相互作用细节默认不再显示蛋白卡通背景，可在窗口底部开启“蛋白背景”。

## 验证

- 单元与集成测试：290 项全部通过（Python 3.12 / conda 环境）。
- Windows：`DockFlowLab.exe` 与安装包版本均为 2.5.0；三个内置引擎可直接运行；
  安装 → 启动文件校验 → 卸载流程已实测，卸载后目录、快捷方式与注册表项均清除。
- macOS：应用包 `CFBundleShortVersionString` / `CFBundleVersion` 均为 2.5.0，
  最低系统版本 macOS 11.0。

## 已知限制

- 安装包与 macOS 应用未做代码签名 / 公证，首次打开可能触发系统安全提示。
- 对接分数是经验评分函数的输出，与同一项目、同一套参数下的结果比较才有意义，不等同于
  实验测得的结合亲和力。
- 受体仍以刚性为主；柔性侧链只调整选中残基的 χ 角，不能替代完整的诱导契合模拟。
- 水分子保留、水桥显示、多口袋盲对接与 AutoDock4 计划在 2.6.0 处理。

## 链接

- 项目主页：<https://github.com/IBN-Spring/DockFlowLab>
- 使用指南：[docs/user-guide.md](https://github.com/IBN-Spring/DockFlowLab/blob/main/docs/user-guide.md)
- 升级清单：[docs/roadmap-2.5.0.md](https://github.com/IBN-Spring/DockFlowLab/blob/main/docs/roadmap-2.5.0.md)
- 问题反馈：<https://github.com/IBN-Spring/DockFlowLab/issues>

如果 DockFlowLab 对你的研究或教学有帮助，欢迎引用本项目（见
[CITATION.cff](https://github.com/IBN-Spring/DockFlowLab/blob/main/CITATION.cff)）。
