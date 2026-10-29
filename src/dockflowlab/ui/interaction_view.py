"""内嵌 NGL Viewer 的蛋白–配体相互作用视图。

页面只负责渲染；接触判定在 :mod:`dockflowlab.services.interactions` 中完成。
Python 端把复合物 PDB 文本与接触列表一起传给 ``interaction.html``，由 JavaScript
绘制配体球棍、残基线框、虚线连接和 HTML 标签；蛋白卡通背景默认隐藏，可通过
``set_protein_visible`` 按需显示。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..services.interactions import InteractionAnalysis
from ..tools.runtime import resource_path
from . import ngl_view

log = logging.getLogger("interaction_view")

INTERACTION_HTML = resource_path("ngl/interaction.html")
NGL_AVAILABLE = ngl_view.NGL_AVAILABLE and INTERACTION_HTML.is_file()
NGL_UNAVAILABLE_REASON = (
    ngl_view.NGL_UNAVAILABLE_REASON
    or ("" if INTERACTION_HTML.is_file() else "缺少 NGL 相互作用页面")
)

QWebEnginePage = ngl_view.QWebEnginePage
QWebEngineView = ngl_view.QWebEngineView
QWebChannel = ngl_view.QWebChannel


class _Bridge(QObject):
    """QWebChannel 暴露给 ``interaction.html`` 的对象。"""

    ready = Signal()
    structure_ready = Signal()
    failed = Signal(str)
    message = Signal(str)
    interaction_selected = Signal(int)

    @Slot(str)
    def viewerReady(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        self.ready.emit()

    @Slot(str)
    def structureReady(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        self.structure_ready.emit()

    @Slot(str)
    def viewerFailed(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        self.failed.emit(_payload_message(payload, "3D 视图初始化失败"))

    @Slot(str)
    def viewerMessage(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        message = _payload_message(payload, "")
        if message:
            log.debug("NGL：%s", message)
            self.message.emit(message)

    @Slot(str)
    def interactionSelected(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        try:
            index = int(json.loads(payload).get("index", -1))
        except (TypeError, ValueError, AttributeError):
            index = -1
        self.interaction_selected.emit(index)


def _payload_message(payload: str, fallback: str) -> str:
    try:
        data = json.loads(payload)
        text = str(data.get("message") or "").strip()
    except (TypeError, ValueError):
        text = ""
    return text or fallback


if QWebEnginePage is not None:

    class _InteractionPage(QWebEnginePage):
        """只允许加载本地页面，并记录 JavaScript 错误。"""

        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                log.debug("NGL 相互作用页 JS 错误：%s (%s:%s)", message, source, line)

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
            try:
                link_clicked = (
                    navigation_type
                    == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                )
            except AttributeError:  # pragma: no cover - Qt 版本差异
                link_clicked = False
            return not link_clicked

else:  # pragma: no cover - 缺少 QtWebEngine
    _InteractionPage = object


class InteractionView(QWidget):
    """与 ``NglBoxView`` 类似的最小封装。"""

    failed = Signal(str)
    ready = Signal()
    structure_ready = Signal()
    interaction_selected = Signal(int)

    READY_TIMEOUT_MS = 20000

    def __init__(self, parent: QWidget | None = None) -> None:
        if not NGL_AVAILABLE:
            raise RuntimeError(NGL_UNAVAILABLE_REASON or "NGL 不可用")
        super().__init__(parent)
        self._ready = False
        self._failed = False
        self._queue: list[str] = []
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(40)
        self._resize_timer.timeout.connect(self._sync_size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web_view = QWebEngineView(self)
        self.web_view.setPage(_InteractionPage(self.web_view))
        self.web_view.page().setBackgroundColor(QColor("#f7f8fa"))
        self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.web_view.setMinimumSize(460, 360)
        layout.addWidget(self.web_view)

        self._bridge = _Bridge()
        self._bridge.ready.connect(self._on_ready)
        self._bridge.structure_ready.connect(self.structure_ready)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.message.connect(lambda message: log.debug("NGL：%s", message))
        self._bridge.interaction_selected.connect(
            self.interaction_selected.emit
        )
        self._channel = QWebChannel(self.web_view.page())
        self._channel.registerObject("bridge", self._bridge)
        self.web_view.page().setWebChannel(self._channel)

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(self.READY_TIMEOUT_MS)
        self._watchdog.timeout.connect(lambda: self._on_failed("3D 视图初始化超时"))
        self._watchdog.start()

        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.setUrl(QUrl.fromLocalFile(str(INTERACTION_HTML)))

    # -- 生命周期 ------------------------------------------------------
    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._on_failed("3D 视图页面加载失败")

    def _on_ready(self) -> None:
        if self._ready or self._failed:
            return
        self._ready = True
        self._watchdog.stop()
        pending, self._queue = self._queue, []
        for script in pending:
            self.web_view.page().runJavaScript(script)
        self.ready.emit()
        self._resize_timer.start(0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_timer.start()

    def _sync_size(self) -> None:
        if self._ready:
            self._call("syncSize")

    def _on_failed(self, message: str) -> None:
        if self._failed or self._ready:
            return
        self._failed = True
        self._watchdog.stop()
        log.warning("NGL 相互作用视图不可用：%s", message)
        self.failed.emit(message)

    # -- 数据 -----------------------------------------------------------
    def set_data(self, analysis: InteractionAnalysis, pdb_text: str) -> None:
        payload = analysis.to_dict()
        payload["pdb"] = pdb_text
        self._call("setData", payload)

    def set_kind_visible(self, kind: str, visible: bool) -> None:
        self._call("setKindVisible", str(kind), bool(visible))

    def set_labels_visible(self, visible: bool) -> None:
        self._call("setLabelsVisible", bool(visible))

    def set_protein_visible(self, visible: bool) -> None:
        """显示或隐藏蛋白卡通背景；默认隐藏以保证配体清晰。"""
        self._call("setProteinVisible", bool(visible))

    def select_interaction(self, index: int) -> None:
        """高亮并定位一条接触；传入 -1 取消选中。"""
        self._call("selectInteraction", int(index))

    def reset_view(self) -> None:
        self._call("resetView")

    # -- 通信 -----------------------------------------------------------
    def _call(self, name: str, *args) -> None:
        script = (
            f"window.dflInteractions && window.dflInteractions.{name}("
            f"{', '.join(json.dumps(arg) for arg in args)})"
        )
        if self._failed:
            return
        if self._ready:
            self.web_view.page().runJavaScript(script)
        else:
            if len(self._queue) >= 32:
                self._queue.pop(0)
            self._queue.append(script)

    # -- 清理 -----------------------------------------------------------
    def shutdown(self) -> None:
        self._watchdog.stop()
        self._resize_timer.stop()
        for signal, slot in (
            (self._bridge.ready, self._on_ready),
            (self._bridge.structure_ready, self.structure_ready),
            (self._bridge.failed, self._on_failed),
            (
                self._bridge.interaction_selected,
                self.interaction_selected.emit,
            ),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
