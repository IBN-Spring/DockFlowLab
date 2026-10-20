# AutoDock Vina 可执行文件

`windows/vina.exe` 来自 AutoDock Vina **1.2.7** 官方发布版，供 Windows 安装包直接使用。
AutoDock Vina 由 The Scripps Research Institute 以 Apache License 2.0 发布，完整许可见
[`LICENSE`](LICENSE)。

macOS / Linux 用户可在系统中安装 Vina（`conda install -c conda-forge vina` 或
`brew install autodock-vina`），DockFlowLab 会自动在 `PATH` 中查找；也可以在“设置”
里手动指定可执行文件。发布 macOS 安装包时，可把对应架构的 `vina` 放到
`macos/vina`，打包配置会自动收集。

来源：<https://github.com/ccsb-scripps/AutoDock-Vina/releases/tag/v1.2.7>
