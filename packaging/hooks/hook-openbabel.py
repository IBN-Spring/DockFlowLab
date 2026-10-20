"""PyInstaller 钩子：确保 OpenBabel 的 Python 子模块全部被收集。

DLL、格式插件与数据文件由 ``dockflowlab.spec`` 负责收集，这里只处理
``import openbabel.openbabel`` 这类动态导入。
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("openbabel")
