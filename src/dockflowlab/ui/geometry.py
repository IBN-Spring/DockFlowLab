"""屏幕适配：让窗口与对话框不超过当前显示器的可用区域。

Windows 上常见的情况是多屏混合 DPI（例如 1920×1080 主屏 + 810×1440
竖屏），固定的最小尺寸会让窗口在较窄的屏幕上无法显示完整。这里的函数
统一按 ``QScreen.availableGeometry()`` 计算尺寸，并在恢复上次窗口位置
时确认它仍落在某个显示器内。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QSize
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QDialog, QWidget

WINDOW_MINIMUM = QSize(1080, 720)
WINDOW_PREFERRED = QSize(1280, 840)

# 窗口与屏幕边缘的留白；较窄的屏幕少留一点，避免内容被挤压。
WINDOW_MARGIN = 32
COMPACT_MARGIN = 12
DIALOG_MARGIN = 24


def _screen_for(widget: QWidget | None) -> QScreen | None:
    if widget is not None:
        try:
            # 顶层窗口可能已经被恢复到一个非主屏的位置，先用几何中心
            # 判断它在哪块屏幕上；未显示时 screenAt() 仍能给出结果。
            if widget.isWindow():
                screen = QGuiApplication.screenAt(widget.frameGeometry().center())
                if screen is not None:
                    return screen
            screen = widget.screen()
            if screen is not None:
                return screen
            handle = widget.windowHandle()
            if handle is not None:
                return handle.screen()
        except RuntimeError:  # 控件已销毁
            return None
    return QGuiApplication.primaryScreen()


def available_geometry(widget: QWidget | None = None) -> QRect | None:
    """返回控件所在屏幕的可用区域；无图形环境时返回 ``None``。"""
    screen = _screen_for(widget)
    if screen is None:
        return None
    rect = screen.availableGeometry()
    if rect.width() <= 0 or rect.height() <= 0:
        return None
    return rect


def _bounded_size(rect: QRect, preferred: QSize, minimum: QSize | None, margin: int) -> tuple[int, int, int, int]:
    max_width = max(360, rect.width() - margin)
    max_height = max(280, rect.height() - margin)
    min_width = 0 if minimum is None else minimum.width()
    min_height = 0 if minimum is None else minimum.height()
    min_width = min(min_width, max_width)
    min_height = min(min_height, max_height)
    width = max(min_width, min(preferred.width(), max_width))
    height = max(min_height, min(preferred.height(), max_height))
    return min_width, min_height, width, height


def fit_window_to_screen(
    window: QWidget,
    *,
    preferred: QSize = WINDOW_PREFERRED,
    minimum: QSize = WINDOW_MINIMUM,
) -> QSize:
    """按当前屏幕设置窗口的最小尺寸与初始尺寸。"""
    rect = available_geometry(window)
    if rect is None:
        window.setMinimumSize(minimum)
        window.resize(preferred)
        return QSize(preferred)
    margin = WINDOW_MARGIN if rect.width() >= 1100 else COMPACT_MARGIN
    min_width, min_height, width, height = _bounded_size(rect, preferred, minimum, margin)
    window.setMinimumSize(min_width, min_height)
    window.resize(width, height)
    return QSize(width, height)


def apply_screen_minimum(window: QWidget, minimum: QSize = WINDOW_MINIMUM) -> None:
    """只更新最小尺寸（用于窗口被拖到另一块屏幕时），不改变当前大小。"""
    rect = available_geometry(window)
    if rect is None:
        window.setMinimumSize(minimum)
        return
    margin = WINDOW_MARGIN if rect.width() >= 1100 else COMPACT_MARGIN
    max_width = max(360, rect.width() - margin)
    max_height = max(280, rect.height() - margin)
    window.setMinimumSize(
        min(minimum.width(), max_width),
        min(minimum.height(), max_height),
    )


def fit_dialog_to_screen(
    dialog: QWidget,
    *,
    preferred: QSize,
    minimum: QSize | None = None,
    parent: QWidget | None = None,
) -> None:
    """让对话框在父窗口所在屏幕上完整显示。"""
    rect = available_geometry(parent or dialog)
    if rect is None:
        if minimum is not None:
            dialog.setMinimumSize(minimum)
        dialog.resize(preferred)
        return
    min_width, min_height, width, height = _bounded_size(
        rect, preferred, minimum, DIALOG_MARGIN
    )
    dialog.setMinimumSize(min_width, min_height)
    dialog.resize(width, height)


class ScreenFittedDialog(QDialog):
    """首次显示时按实际所在屏幕校正尺寸的对话框基类。

    Qt 在窗口首次显示到不同 DPI 的屏幕时，会为了保持物理尺寸而缩放
    逻辑尺寸；构造阶段调用 ``resize()`` 后，窗口可能在显示时被放大。
    因此在 ``showEvent`` 里再校正一次。
    """

    _fit_preferred: QSize | None = None
    _fit_minimum: QSize | None = None

    def fit_to_screen(
        self, *, preferred: QSize, minimum: QSize | None = None
    ) -> None:
        self._fit_preferred = QSize(preferred)
        self._fit_minimum = QSize(minimum) if minimum is not None else None
        fit_dialog_to_screen(
            self, preferred=self._fit_preferred, minimum=self._fit_minimum
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fit_preferred is not None:
            fit_dialog_to_screen(
                self,
                preferred=self._fit_preferred,
                minimum=self._fit_minimum,
            )


def window_visible_on_screen(window: QWidget) -> bool:
    """窗口是否与任意一块屏幕有足够的重叠（防止恢复到已断开的显示器）。"""
    frame = window.frameGeometry()
    for screen in QGuiApplication.screens():
        overlap = screen.availableGeometry().intersected(frame)
        if overlap.width() >= 80 and overlap.height() >= 60:
            return True
    return False


def center_window_on_screen(window: QWidget) -> None:
    """把窗口放到所在屏幕（默认主屏）中央。"""
    screen = _screen_for(window)
    if screen is None:
        return
    area = screen.availableGeometry()
    frame = window.frameGeometry()
    window.move(
        area.center().x() - frame.width() // 2,
        area.center().y() - frame.height() // 2,
    )


def clamp_window_to_screen(window: QWidget) -> None:
    """把恢复后的窗口收缩并移动到当前屏幕的可用区域内。"""
    if not window_visible_on_screen(window):
        center_window_on_screen(window)
        return
    rect = available_geometry(window)
    if rect is None:
        return
    frame = window.frameGeometry()
    if frame.width() > rect.width() or frame.height() > rect.height():
        # resize() 设置的是客户区；把窗口边框的宽度扣掉，保证含边框后
        # 仍能完整落在屏幕内。
        extra_width = max(0, frame.width() - window.width())
        extra_height = max(0, frame.height() - window.height())
        window.resize(
            max(320, min(window.width(), rect.width() - extra_width)),
            max(240, min(window.height(), rect.height() - extra_height)),
        )
        frame = window.frameGeometry()
    x, y = window.x(), window.y()
    if frame.right() > rect.right():
        x -= frame.right() - rect.right()
    if frame.bottom() > rect.bottom():
        y -= frame.bottom() - rect.bottom()
    if frame.left() < rect.left():
        x = rect.left()
    if frame.top() < rect.top():
        y = rect.top()
    if (x, y) != (window.x(), window.y()):
        window.move(x, y)


def save_window_geometry(window: QWidget) -> str:
    """把 ``saveGeometry()`` 的结果编码成可写入 JSON 的文本。"""
    return bytes(window.saveGeometry().toBase64()).decode("ascii")


def restore_window_geometry(window: QWidget, encoded: str) -> bool:
    """恢复窗口几何；无效或已离开屏幕时退回当前屏幕的默认位置。"""
    if not isinstance(encoded, str) or not encoded.strip():
        return False
    try:
        data = QByteArray.fromBase64(encoded.strip().encode("ascii"))
    except (UnicodeEncodeError, ValueError):
        return False
    if data.isEmpty() or not window.restoreGeometry(data):
        return False
    if not window.isMaximized():
        clamp_window_to_screen(window)
    return True
