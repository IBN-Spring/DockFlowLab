"""版本信息与组件版本探测。"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as package_version

__version__ = "2.4.0"
__app_name__ = "DockFlowLab"
__app_name_zh__ = "分子对接工作台"


def component_version(distribution: str, module: str = "") -> str:
    """返回已安装组件版本；同时兼容 pip 包与 conda 包。

    conda-forge 的 RDKit 没有 pip 元数据，因此失败时回退到模块的
    ``__version__`` 属性。任何导入或元数据错误都返回空字符串，调用方
    可以据此显示“缺失”。
    """
    for name in (distribution, distribution.replace("-", "_")):
        try:
            return package_version(name).strip()
        except (PackageNotFoundError, ValueError):
            continue
    try:
        imported = import_module(module or distribution)
    except Exception:
        return ""
    value = getattr(imported, "__version__", "")
    return str(value) if value else ""
