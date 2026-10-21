# QuickVina 2 可执行文件

`windows/qvina2.exe` 来自 conda-forge 的 `qvina 2.1.0`（build `he453025_8`），
供 Windows 安装包直接使用。QuickVina 2 基于 AutoDock Vina 1.1.2，使用相同的
评分函数，但改进了全局搜索策略，通常更快，也能绕开个别复合物上的搜索停滞。

依赖 `boost_*.dll`、MSVC 运行库和系统 DLL；DockFlowLab 的打包脚本已经会收集
conda 环境中的这些运行库。

QuickVina 2 与 AutoDock Vina 均由 The Scripps Research Institute 以
Apache License 2.0 发布，完整许可见 [`LICENSE`](LICENSE)。

macOS / Linux 用户可在系统中安装 QuickVina 2（或把可执行文件放到
`macos/qvina2`、`linux/qvina2`），DockFlowLab 会自动查找；也可以在“设置”
里手动指定可执行文件。

来源：

- 项目主页：<https://github.com/QVina/qvina>
- conda-forge 包：<https://anaconda.org/conda-forge/qvina>
