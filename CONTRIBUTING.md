# 参与 DockFlowLab

感谢你愿意改进 DockFlowLab。这个项目面向非专业用户，优先保证**主流程可跑通、结果可解释、界面不乱**。

## 报告问题

请在 Issue 中附上：

- DockFlowLab 版本（设置页或启动日志中可见）
- Windows / macOS 版本与 CPU 架构
- 复现步骤，以及输入结构的来源（PDB ID 或公开文件）
- 相关日志或截图

日志位置（`session.log`）：

- Windows：`%LOCALAPPDATA%\DockFlowLab\logs`
- macOS：`~/Library/Caches/DockFlowLab/logs`
- 项目目录下的 `logs/` 也会保留该次任务的运行记录

请勿上传未公开的受体结构、患者数据或实验室内部资料。需要示例时，优先使用 PDB 公共结构。

## 提交代码

1. 从 `main` 创建分支，例如 `fix/box-center`。
2. 先补测试，再改代码；跨平台逻辑要考虑 `IS_WINDOWS` / `IS_MACOS` 分支。
3. 运行：

   ```bash
   PYTHONPATH=src python -m pytest
   ```

4. 提交信息说明“做了什么、为什么”，不要提交构建产物、引擎二进制或用户项目数据。
5. 发起 Pull Request，描述复现方式、改动范围和验证结果。

## 代码约定

- 界面文案使用中文；内部目录名保持英文。
- 对接分数只能来自真实引擎，禁止用占位值或伪分数填充结果。
- 不支持的体系要明确说明原因，并给出替代工具建议。
- 新增第三方依赖或二进制时，必须记录版本、来源和许可证。
- 保持现有默认 3D 视图与五步主流程，不要为了单个场景破坏通用路径。

## 开发环境

```bash
conda create -n DockFlowLab python=3.12 -y
conda activate DockFlowLab
conda install -c conda-forge rdkit openbabel openmm pdbfixer lxml -y
pip install -e ".[dev]" meeko
# PLIP 声明依赖 pip 版 openbabel；conda 已提供 OpenBabel，跳过依赖解析。
pip install --no-deps plip==3.0.1
PYTHONPATH=src python -m dockflowlab
```

没有安装 PLIP 时，相互作用视图会自动回退到几何判定，相关测试会自动跳过。

打包与平台细节见 [docs/packaging.md](docs/packaging.md)，架构说明见 [docs/architecture.md](docs/architecture.md)。

## 许可证

提交代码即表示你同意以本项目的 GPL-2.0 许可发布你的贡献。第三方代码或数据请单独说明许可证，
并确认与 GPL-2.0 兼容。
