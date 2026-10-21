"""统一的异常类型。

所有面向用户的错误都继承 :class:`DockFlowError`，其消息可以直接展示在界面上。
"""

from __future__ import annotations


class DockFlowError(Exception):
    """DockFlowLab 的基础异常。"""


class DependencyError(DockFlowError):
    """缺少外部程序或 Python 依赖。"""


class InputError(DockFlowError):
    """用户提供的文件或参数无效。"""


class PreparationError(DockFlowError):
    """结构预处理失败。"""


class DockingError(DockFlowError):
    """对接计算失败。"""


class DownloadError(DockFlowError):
    """在线下载失败。"""


class DownloadCancelled(DownloadError):
    """用户在下载过程中取消。"""


class CancelledError(DockFlowError):
    """用户取消了任务。"""
