"""可复用的界面组件。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ProjectFile
from . import theme


class PageHeader(QWidget):
    def __init__(self, eyebrow: str, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.eyebrow = QLabel(eyebrow)
        self.eyebrow.setObjectName("Eyebrow")
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.eyebrow)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)


class Card(QFrame):
    def __init__(self, title: str = "", hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        if title:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("CardTitle")
            row.addWidget(label)
            row.addStretch(1)
            outer.addLayout(row)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("CardHint")
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class StatTile(QFrame):
    def __init__(self, label: str, value: str = "—", accent: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TileAccent" if accent else "Tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Metric")
        if accent:
            self.value_label.setStyleSheet(f"color: {theme.ACCENT_DARK};")
        self.name_label = QLabel(label)
        self.name_label.setObjectName("MetricLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class StatusChip(QLabel):
    _STYLES = {
        "ready": (theme.SUCCESS, theme.SUCCESS_BG, "已就绪"),
        "new": (theme.SECONDARY, "#f1f0ec", "待准备"),
        "preparing": (theme.ACCENT_DARK, theme.ACCENT_SOFT, "准备中"),
        "failed": (theme.DANGER, theme.DANGER_BG, "需要处理"),
        "success": (theme.SUCCESS, theme.SUCCESS_BG, "成功"),
        "skipped": (theme.SECONDARY, "#f1f0ec", "已有结果"),
        "error": (theme.DANGER, theme.DANGER_BG, "失败"),
        "running": (theme.ACCENT_DARK, theme.ACCENT_SOFT, "运行中"),
    }

    def __init__(self, status: str = "new", text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status, text)

    def set_status(self, status: str, text: str = "") -> None:
        color, background, default = self._STYLES.get(status, self._STYLES["new"])
        self.setText(text or default)
        self.setStyleSheet(
            f"color: {color}; background: {background}; border-radius: 9px;"
            "padding: 2px 10px; font-size: 12px;"
        )


class DropZone(QFrame):
    activated = Signal()
    files_dropped = Signal(list)

    def __init__(self, title: str, hint: str, button_text: str = "选择文件", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(124)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("Muted")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.button = QPushButton(button_text)
        self.button.setObjectName("Small")
        self.button.clicked.connect(self.activated.emit)
        layout.addStretch(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.hint_label)
        layout.addSpacing(4)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def _set_dragging(self, active: bool) -> None:
        self.setProperty("dragging", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_dragging(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseReleaseEvent(event)


class EntityRow(QFrame):
    remove_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, entry: ProjectFile, parent: QWidget | None = None, *, show_actions: bool = True) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("EntityRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(54)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 8)
        layout.setSpacing(10)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name = QLabel(entry.display_name)
        name.setStyleSheet("font-weight: 600; font-size: 14px;")
        text_box.addWidget(name)
        meta_parts = []
        fmt = str(entry.meta.get("format", "")).upper()
        if fmt:
            meta_parts.append(fmt)
        if entry.meta.get("prep_method"):
            meta_parts.append(str(entry.meta["prep_method"]))
        meta = QLabel(" · ".join(meta_parts) or "等待准备")
        meta.setObjectName("Muted")
        meta.setWordWrap(True)
        text_box.addWidget(meta)
        layout.addLayout(text_box, 1)

        if entry.message and entry.status != "ready":
            message = QLabel(entry.message)
            message.setObjectName("Muted")
            message.setWordWrap(True)
            message.setMaximumWidth(280)
            layout.addWidget(message)

        chip = StatusChip(entry.status)
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

        if not show_actions:
            return

        open_button = QPushButton("定位")
        open_button.setObjectName("Small")
        open_button.setToolTip("在文件管理器中显示原始文件")
        open_button.clicked.connect(lambda: self.open_requested.emit(entry.stem))
        layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)

        remove_button = QPushButton("移除")
        remove_button.setObjectName("Small")
        remove_button.clicked.connect(lambda: self.remove_requested.emit(entry.stem))
        layout.addWidget(remove_button, 0, Qt.AlignmentFlag.AlignVCenter)


class EmptyState(QFrame):
    action_requested = Signal()

    def __init__(
        self,
        title: str,
        hint: str,
        button_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 26, 24, 26)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_label = QLabel(hint)
        hint_label.setObjectName("Muted")
        hint_label.setWordWrap(True)
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        if button_text:
            button = QPushButton(button_text)
            button.setObjectName("Primary")
            button.clicked.connect(self.action_requested.emit)
            layout.addSpacing(6)
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)


class LogPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("准备开始")
        self.status_label.setObjectName("CardHint")
        status_row.addWidget(self.status_label, 1)
        self.toggle_button = QPushButton("查看运行日志")
        self.toggle_button.setObjectName("Link")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setVisible(False)
        self.toggle_button.toggled.connect(self._toggle_log)
        status_row.addWidget(self.toggle_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("Small")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.setVisible(False)
        status_row.addWidget(self.cancel_button)
        layout.addLayout(status_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.log.setFont(font)
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log)

    def start(self, message: str = "正在运行…", cancellable: bool = True) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("")
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.log.clear()
        self.log.setVisible(True)
        self.toggle_button.setVisible(False)
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(True)

    def set_progress(self, current: int, total: int, message: str = "") -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
        if message:
            self.status_label.setText(message)

    def append_line(self, text: str) -> None:
        self.log.appendPlainText(text.rstrip())

    def finish(self, message: str, ok: bool = True) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setVisible(False)
        self.status_label.setText(message)
        self.cancel_button.setVisible(False)
        self.status_label.setStyleSheet(
            f"color: {theme.SUCCESS if ok else theme.DANGER}; font-size: 12px;"
        )
        self.toggle_button.setVisible(True)
        self.log.setVisible(False)
        self.toggle_button.setChecked(False)
        self.toggle_button.setText("查看运行日志")

    def _toggle_log(self, checked: bool) -> None:
        self.log.setVisible(checked)
        self.toggle_button.setText("隐藏运行日志" if checked else "查看运行日志")

    def request_cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消，请稍候…")


def make_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.BORDER}; background: {theme.BORDER}; max-height: 1px;")
    return line


def file_display_name(path: str | Path) -> str:
    return Path(path).stem
