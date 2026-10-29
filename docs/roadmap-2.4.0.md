# DockFlowLab 2.4.0 升级清单

> 目标：把 2.3.0 的“能跑完流程”升级为“结果可信、可解释、可复核”。
> 对应 AutoDock/Vina 经典流程里最容易被忽略的三件事：重对接校准、构象收敛、
> 关键相互作用过滤。

## 版本范围

### 必做（P0）

- [x] **R1 重对接验证（redocking）**
- [x] **R2 构象浏览 + 收敛提示**
- [x] **R3 批量相互作用筛选**
- [x] **R4 结构体检卡**
- [x] **R5 exhaustiveness 智能建议**
- [x] **R6 配体效率（LE）+ 结果可信度卡**

### 低成本顺手项（P1）

- [x] **R7 Smina Vinardo 评分选项**
- [x] **R8 氢键几何证据提示加强**
- [x] **R9 相互作用结果导出 CSV**

### 明确延后到 2.5.0

- [ ] 水分子保留 + 水桥显示
- [ ] 柔性侧链（Vina `--flex` / Meeko flexres）
- [ ] 多口袋盲对接 / AutoLigand 式探针
- [ ] protomer / tautomer 枚举
- [ ] AutoDock4 内置（autogrid 映射链）
- [ ] 集合对接 / 突变体建模

---

## R1 重对接验证（redocking）

**问题**：现在只显示 Vina 的 pose 间 RMSD，没有“用已知晶体结构校准当前参数”的能力。

**实现要点**

- [ ] 新增 `services/redocking.py`
- [ ] 从原始结构中提取共晶配体并写成独立 PDB（复用 `pdbio.find_ligand_atoms` / `find_ligand_sites`）
- [ ] 用现有 `prepare_ligand` 准备提取出的配体
- [ ] 复用当前盒子与对接参数跑一次对接
- [ ] 计算对称校正的重原子 RMSD：
  - [ ] 优先 RDKit `rdMolAlign.GetBestRMS`（需要正确的原子映射）
  - [ ] 图不匹配时回退到 MCS/匈牙利匹配，或引入纯 Python 的 `spyrmsd`
- [ ] 结果写入 `workspace/validation/`，并在结果对象/报告中展示
- [ ] UI：靶点行或“检查与准备”页提供“重对接验证”按钮；没有共晶配体时禁用并说明原因
- [ ] 报告：新增“重对接验证”小节（RMSD、是否 ≤ 2 Å、使用的参数）

**验收标准**

- [ ] 3HS4（碳酸酐酶 + 乙酰唑胺 + Zn）重对接 RMSD ≤ 2 Å
- [ ] 1BCI 等含共晶配体的结构可完成验证
- [ ] 没有共晶配体时按钮禁用且给出原因，不抛异常
- [ ] 引擎不可用/对接失败时有明确提示
- [ ] 报告中 RMSD 明确标注“与实验构象的对称校正 RMSD”，不与 Vina 的 pose RMSD 混淆

**测试**

- [ ] RMSD 对称性单测（等价原子重排后结果不变）
- [ ] 提取配体 + mock 引擎的端到端测试
- [ ] 无共晶配体、对接失败的路径测试

**涉及文件**：`pdbio.py`、`services/ligand.py`、`services/docking.py`、`workspace.py`、`report.py`、`ui/pages.py`、`ui/main_window.py`

**预估**：M/L（3–5 天）

---

## R2 构象浏览 + 收敛提示

**问题**：复合物和细节视图只取第一个 pose，其余构象只能看到分数。

**实现要点**

- [ ] `pdbio` 增加 `parse_pose_atoms(path, index)` / 拆分多 pose PDBQT
- [ ] 细节对话框增加 pose 下拉框（排名、分数、RMSD）
- [ ] 切换 pose 时按需生成临时复合物并重新做 PLIP/3D
- [ ] 每个 pose 的相互作用结果缓存，避免来回切换重复计算
- [ ] 收敛提示：
  - [ ] 读取前 N 个 pose 的重原子坐标
  - [ ] 计算 pose 间 RMSD；若前 5 个空间分散且 ΔG 差 < 1 kcal/mol，提示“搜索可能未收敛”
  - [ ] 提示提高 exhaustiveness 或检查配体柔性
- [ ] 结果表保持一行一个 pose，但“细节”按钮可打开同一组的 pose 浏览器

**验收标准**

- [ ] 9 个 pose 都能在 3D 和列表中查看
- [ ] 切换 pose 后 3D、相互作用列表、距离同步更新
- [ ] 只有 1 个 pose 时不显示无意义的下拉框
- [ ] 收敛提示只作建议，不自动修改参数

**测试**

- [ ] 多 model PDBQT 解析
- [ ] 收敛启发式单测（构造分散/收敛两组坐标）
- [ ] 细节对话框状态切换测试（可无头）

**涉及文件**：`core/pdbio.py`、`ui/interaction_dialog.py`、`ui/main_window.py`、`services/interactions.py`、`services/plip_analysis.py`

**预估**：M（3–4 天）

---

## R3 批量相互作用筛选

**问题**：PLIP 只在单条结果的细节里运行，结果表没有相互作用列，无法按关键残基过滤假阳性。

**实现要点**

- [ ] 新增批量分析服务（例如 `services/interaction_index.py`）
- [ ] 对每个成功结果运行 PLIP，结果缓存到 `analysis/interactions/<target>/<ligand>.json`
- [ ] 缓存键包含复合物 mtime/大小/参数，重新对接后自动失效
- [ ] 后台任务 + 进度 + 取消；PLIP 不可用时回退到几何判定并标注
- [ ] 结果表新增列：氢键数、盐桥数、金属配位、关键残基接触
- [ ] 筛选面板：必须包含某类型/某残基（如“MET318 氢键”）、排除某类型
- [ ] 过滤后的结果可导出 CSV
- [ ] 报告可选附每条的相互作用摘要

**验收标准**

- [ ] 10 条结果的批量分析可完成且有进度
- [ ] 第二次打开直接复用缓存
- [ ] 筛选即时生效，导出内容与界面一致
- [ ] 缓存失效、PLIP 缺失、单条失败都不影响其他结果

**测试**

- [ ] 缓存失效逻辑
- [ ] 筛选组合（类型 + 残基 + 排除）
- [ ] PLIP 不可用时的几何回退
- [ ] CSV 导出内容

**涉及文件**：`services/analysis.py`、`services/plip_analysis.py`、`workspace.py`、`ui/pages.py`、`ui/main_window.py`、`services/report.py`

**预估**：L（4–5 天，主要成本在批量性能与缓存）

---

## R4 结构体检卡

**问题**：下载的 PDB 可能只是不对称单元、含 altloc、低 occupancy 或缺失残基，当前只提示补全残基数量。

**实现要点**

- [ ] 新增 `core/structure_qc.py`
- [ ] 解析 `REMARK 350`（BIOMT/生物学装配）并提示“可能是二聚体/多聚体”
- [ ] 解析 `REMARK 465/470`（缺失残基/原子）
- [ ] 统计 altloc、occupancy < 0.5 的原子、非标准残基
- [ ] 统计金属离子、水、常见添加剂
- [ ] 准备页/报告展示“结构体检”卡
- [ ] 2.4.0 只警告，不自动应用 BIOMT；需要时引导用户下载生物学装配

**验收标准**

- [ ] 含 BIOMT 的结构给出明确提示
- [ ] altloc/occupancy 统计准确
- [ ] 普通单链结构不产生误报
- [ ] 体检结果进入报告与结果可信度卡

**测试**

- [ ] 构造含 REMARK 350/465/470、altloc、低 occupancy 的 fixture PDB
- [ ] 正常结构无误报

**涉及文件**：`core/pdbio.py`、`core/structure_qc.py`、`services/receptor.py`、`ui/pages.py`、`services/report.py`

**预估**：S/M（1–2 天）

---

## R5 exhaustiveness 智能建议

**问题**：只有固定档位 8/16/32，没有和配体柔性联动；低精细度跑柔性配体容易假收敛。

**实现要点**

- [ ] 新增启发式函数（可旋转键、重原子数、分子尺寸）
- [ ] 对接参数对话框显示“建议值”和理由
- [ ] 用户选择低于建议值时给出警告，但不强制修改
- [ ] 对接完成后结合 pose 分散度给出“是否建议重跑”的提示（与 R2 共用）
- [ ] 参数与建议值写入报告

**验收标准**

- [ ] 高柔性配体（>10 可旋转键）建议 32 或更高
- [ ] 不静默修改用户参数
- [ ] 建议值有测试覆盖

**测试**

- [ ] 不同可旋转键/重原子数的建议值
- [ ] 边界值（0 个可旋转键、超大分子）

**涉及文件**：`services/docking.py`、`ui/dialogs.py`、`config.py`、`services/report.py`

**预估**：S（1 天）

---

## R6 配体效率 + 结果可信度卡

**问题**：不同大小的分子只比总分容易误导；现有警告分散在多个地方，用户看不到整体可信度。

**实现要点**

- [ ] 结果表/报告增加：重原子数、分子量、可旋转键、配体效率 `LE = −ΔG / 重原子数`
- [ ] 明确 LE 公式与单位（kcal/mol/重原子）
- [ ] “结果可信度卡”汇总：
  - [ ] 盒子来源与 `needs_review`
  - [ ] exhaustiveness 与配体柔性
  - [ ] 缺失残基/altloc/occupancy/BIOMT
  - [ ] 金属/水处理
  - [ ] 重对接 RMSD（若已运行）
  - [ ] 相互作用过滤状态
- [ ] 绿/黄/红规则写进文档，并在界面提供“为什么”说明
- [ ] 卡片同时出现在结果页和 HTML 报告

**验收标准**

- [ ] LE 在结果表、CSV、报告中一致
- [ ] 可信度卡的状态与规则一致，不出现误报红卡
- [ ] 每个红色/黄色项都能点开看到具体原因

**测试**

- [ ] LE 计算
- [ ] 可信度规则单测（正常/警告/失败三组输入）

**涉及文件**：`core/models.py`、`services/analysis.py`、`services/report.py`、`ui/pages.py`、`ui/main_window.py`

**预估**：S/M（1–2 天）

---

## R7 Smina Vinardo 评分选项（P1）

- [x] Smina 适配器支持 `--scoring vinardo`
- [x] UI 仅在 Smina 下显示评分函数选项
- [x] 参数写入结果与报告
- [x] 测试命令构建与参数持久化

**预估**：S（0.5–1 天）

## R8 氢键几何证据提示加强（P1）

- [x] 细节视图、报告、用户指南统一强调：Vina 的极性氢位置未优化，氢键几何只作参考
- [x] 不展示/不强调供体–氢–受体角度结论
- [x] 保留 PLIP 的距离与类型判定

**预估**：S（0.5 天）

## R9 相互作用 CSV 导出（P1）

- [x] 单条结果的相互作用表导出 CSV
- [x] 批量筛选后的结果表导出 CSV（与 R3 共用导出器）
- [x] 字段包含类型、残基、原子、距离、pose 排名

**预估**：S（0.5–1 天）

---

## 实施顺序

1. 冻结范围，创建 `feature/2.4.0` 分支（或按功能拆分分支）
2. 底层能力：RMSD、pose 拆分、结构体检解析
3. 服务层：redocking、interaction index、exhaustiveness 建议、LE
4. UI：pose 浏览器、相互作用筛选、结构体检卡、可信度卡
5. 测试 + 文档（user-guide / architecture / README）
6. 版本号 2.4.0、打包、发布

## 发布前检查

- [ ] `PYTHONPATH=src python -m pytest` 全部通过
- [ ] 1BCI + 阿司匹林走通“导入 → 准备 → 盒子 → 对接 → 分析 → 报告”
- [ ] 3HS4 走通金属保留 + 重对接验证 + 金属配位显示
- [ ] 高柔性配体（如伊马替尼/1IEP）验证 exhaustiveness 建议与收敛提示
- [ ] 批量 3 个以上结果验证相互作用缓存与筛选
- [ ] 版本号同步：`version.py`、`pyproject.toml`、`version_info.txt`、`dockflowlab.spec`、README 徽章/BibTeX、`CITATION.cff`
- [ ] Windows 包构建，`DockFlowLab.exe` 版本号 2.4.0
- [ ] 发布说明、tag `v2.4.0`、上传 zip
- [ ] 不把 `可视化脚本/` 加入提交；提交不加 Claude 共同作者

## 风险与应对

- **PLIP 批量慢**：缓存 + 后台任务 + 可取消；先只对最佳 pose 做批量分析。
- **RMSD 原子映射**：先用 RDKit `GetBestRMS`，图不匹配时回退 MCS；用 3HS4/1BCI 做回归。
- **pose 切换卡顿**：临时复合物与相互作用结果都缓存；默认只预生成最佳 pose。
- **范围膨胀**：2.4.0 不碰水、柔性侧链、AutoDock4；这些统一进 2.5.0。
