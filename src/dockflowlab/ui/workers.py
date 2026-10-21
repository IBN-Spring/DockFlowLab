"""把耗时任务放到后台线程，保持界面不冻结。"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..errors import CancelledError, DownloadCancelled
from ..logging_setup import get_logger

log = get_logger("ui.worker")

TaskFunction = Callable[[Callable[[int, int, str], None], threading.Event, Callable[[str], None]], Any]


class Task(QThread):
    """运行 ``fn(progress, cancel, line)`` 并回传结果。"""

    progress = Signal(int, int, str)
    line = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: TaskFunction, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def run(self) -> None:  # noqa: D401
        try:
            result = self._fn(self._emit_progress, self.cancel_event, self.line.emit)
        except (CancelledError, DownloadCancelled) as exc:
            # 用户主动取消不是故障，不要把堆栈写进日志。
            log.info("后台任务已取消")
            self.failed.emit(str(exc) or "已取消")
        except Exception as exc:
            log.exception("后台任务失败：%s", exc)
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.done.emit(result)

    def _emit_progress(self, current: int, total: int, message: str = "") -> None:
        self.progress.emit(int(current), int(total), str(message))

    def request_cancel(self) -> None:
        self.cancel_event.set()


def describe_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    return f"{exc.__class__.__name__}\n{traceback.format_exc(limit=3)}"
