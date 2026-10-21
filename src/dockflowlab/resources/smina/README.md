# Smina 可执行文件

`windows/smina.exe` 来自 conda-forge 的 `smina 2020.12.10`（build `he0c23c2_2`），
供 Windows 安装包直接使用。Smina 是 AutoDock Vina 1.1.2 的分支，默认评分函数
在 Vina 基础上做了修正，并提供更稳健的能量最小化流程，可用于 Vina 表现异常
或卡死的复合物。

依赖 `boost_*.dll`、`openbabel-3.dll` 和 MSVC 运行库；DockFlowLab 的打包脚本
已经会收集 conda 环境中的这些运行库。

Smina 以 Apache License 2.0 发布，完整许可见 [`LICENSE`](LICENSE)。

macOS / Linux 用户可在系统中安装 Smina（或把可执行文件放到
`macos/smina`、`linux/smina`），DockFlowLab 会自动查找；也可以在“设置”里
手动指定可执行文件。

来源：

- 项目主页：<https://github.com/gnina/smina>
- conda-forge 包：<https://anaconda.org/conda-forge/smina>
