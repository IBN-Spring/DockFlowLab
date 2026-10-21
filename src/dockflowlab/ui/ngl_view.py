"""基于 NGL Viewer 的 3D 盒子视图。

NGL 是 Mol* 生态里的 WebGL 分子可视化库（MIT），通过 QtWebEngine 内嵌到
界面中。与自行放样卡通模型相比，NGL 的卡通、球棍、光照和抗锯齿效果更接近
PyMOL，并且支持蛋白、核酸与多肽的统一表示。

本模块负责三件事：

* 在导入 QtWebEngine 之前修正 conda 布局下的运行时路径（资源、语言包、
  ``QtWebEngineProcess`` 与 DLL 搜索路径）；
* 用 ``QWebChannel`` 把 JavaScript 中拖拽出的盒子参数回传给 Python；
* 提供与 ``box_view.BoxGLView`` 相同的最小接口，供 ``BoxViewer`` 在
  NGL 不可用时回退到 pyqtgraph 渲染。

界面只传纯文本 PDB 与盒子数值；所有相机、拾取与拖拽计算都在
``resources/ngl/viewer.html`` 中完成。
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..core.models import MAX_BOX_SIZE, MIN_BOX_SIZE, Box
from ..tools.runtime import resource_path

log = logging.getLogger("ngl_view")


# ----------------------------------------------------------------------
# QtWebEngine 运行时路径
# ----------------------------------------------------------------------
def _frozen_pyside_dir() -> Path | None:
    """PyInstaller 打包后 PySide6 在 ``_MEIPASS`` 下的位置。"""
    if not getattr(sys, "frozen", False):
        return None
    base = Path(getattr(sys, "_MEIPASS", ""))
    candidate = base / "PySide6"
    return candidate if candidate.is_dir() else base


def webengine_layout(prefix: str | Path | None = None) -> dict[str, Path]:
    """返回 QtWebEngine 资源、语言包与渲染进程的候选路径。

    * 冻结包（PyInstaller）：资源在 ``_MEIPASS/PySide6`` 下；
    * conda：Qt 放在 ``<env>/Library``，而运行时 ``QLibraryInfo`` 可能指向
      错误的层级，导致渲染进程找不到资源与 DLL；
    * wheel 安装的 PySide6 布局正常，因此这些路径只在文件存在时才使用。
    """
    if prefix is None:
        pyside = _frozen_pyside_dir()
        if pyside is not None:
            base = Path(getattr(sys, "_MEIPASS", ""))
            process = pyside / "QtWebEngineProcess.exe"
            if sys.platform == "darwin":
                process_candidates = (
                    pyside / "QtWebEngineProcess",
                    pyside
                    / "QtWebEngineProcess.app"
                    / "Contents"
                    / "MacOS"
                    / "QtWebEngineProcess",
                    pyside
                    / "Qt"
                    / "lib"
                    / "QtWebEngineCore.framework"
                    / "Helpers"
                    / "QtWebEngineProcess.app"
                    / "Contents"
                    / "MacOS"
                    / "QtWebEngineProcess",
                )
                process = next(
                    (path for path in process_candidates if path.is_file()),
                    process_candidates[0],
                )
            return {
                "resources": pyside / "resources",
                "locales": pyside / "translations" / "qtwebengine_locales",
                "process": process,
                "bin": base,
            }

    root = Path(prefix) if prefix is not None else Path(sys.prefix)
    library = root / "Library"
    return {
        "resources": library / "share" / "qt6" / "resources",
        "locales": library
        / "share"
        / "qt6"
        / "translations"
        / "qtwebengine_locales",
        "process": library / "lib" / "qt6" / "QtWebEngineProcess.exe",
        "bin": library / "bin",
    }


def apply_webengine_environment(prefix: str | Path | None = None) -> None:
    """在创建 ``QApplication`` 之前配置 QtWebEngine。

    必须在导入 ``PySide6.QtWebEngineWidgets`` 之前调用。已存在的环境变量
    不会被覆盖，便于开发者自行覆盖路径。
    """
    paths = webengine_layout(prefix)
    resources = paths["resources"]
    if resources.is_dir() and "QTWEBENGINE_RESOURCES_PATH" not in os.environ:
        os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(resources)
    locales = paths["locales"]
    if locales.is_dir() and "QTWEBENGINE_LOCALES_PATH" not in os.environ:
        os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales)
    process = paths["process"]
    if process.is_file() and "QTWEBENGINEPROCESS_PATH" not in os.environ:
        os.environ["QTWEBENGINEPROCESS_PATH"] = str(process)

    # 渲染进程只通过 PATH 查找 Qt DLL；不能用 add_dll_directory。
    bin_dir = paths["bin"]
    if sys.platform == "win32":
        extra_dirs = [bin_dir]
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", ""))
            extra_dirs = [base, base / "PySide6", *extra_dirs]
        entries = os.environ.get("PATH", "").split(os.pathsep)
        for directory in reversed(extra_dirs):
            if directory.is_dir() and str(directory) not in entries:
                entries.insert(0, str(directory))
        os.environ["PATH"] = os.pathsep.join(entries)

    # conda 的 QtWebEngine 沙箱无法加载 Library/bin 中的 DLL，渲染进程会以
    # STATUS_DLL_NOT_FOUND 退出。页面只加载本地受信内容，关闭沙箱是可接受的。
    if sys.platform == "win32" and bin_dir.is_dir():
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
        if "--no-sandbox" not in flags:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
                f"{flags} --no-sandbox".strip()
            )


apply_webengine_environment()

try:  # pragma: no cover - 取决于运行环境是否包含 QtWebEngine
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _WEBENGINE_ERROR = ""
except Exception as exc:  # pragma: no cover - 缺少 QtWebEngine 时的降级路径
    QWebChannel = None  # type: ignore[assignment]
    QWebEnginePage = None  # type: ignore[assignment]
    QWebEngineView = None  # type: ignore[assignment]
    _WEBENGINE_ERROR = str(exc)


def _viewer_assets_exist() -> bool:
    try:
        html = resource_path("ngl/viewer.html")
        script = resource_path("ngl/ngl.js")
    except Exception:  # pragma: no cover - 打包布局异常
        return False
    return html.is_file() and script.is_file()


NGL_AVAILABLE = (
    QWebEngineView is not None
    and QWebChannel is not None
    and _viewer_assets_exist()
)
NGL_UNAVAILABLE_REASON = (
    _WEBENGINE_ERROR or ("缺少 NGL 资源文件" if not _viewer_assets_exist() else "")
)

VIEWER_HTML = resource_path("ngl/viewer.html") if NGL_AVAILABLE else Path()


# ----------------------------------------------------------------------
# Python ⇄ JavaScript
# ----------------------------------------------------------------------
class _Bridge(QObject):
    """QWebChannel 暴露给 JavaScript 的对象。"""

    box_edited = Signal(object)
    ready = Signal()
    structure_ready = Signal()
    failed = Signal(str)
    message = Signal(str)

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
    def boxEdited(self, payload: str) -> None:  # noqa: N802 - JS 端命名
        try:
            data = json.loads(payload)
            center = tuple(float(v) for v in data["center"])
            size = tuple(float(v) for v in data["size"])
        except (KeyError, TypeError, ValueError):
            return
        if len(center) != 3 or len(size) != 3:
            return
        if not all(math.isfinite(value) for value in (*center, *size)):
            return
        size = tuple(
            min(MAX_BOX_SIZE, max(MIN_BOX_SIZE, value)) for value in size
        )
        note = str(data.get("note") or "在 3D 视图中拖动调整")
        self.box_edited.emit(
            Box(center=center, size=size, source="manual", note=note)
        )


def _payload_message(payload: str, fallback: str) -> str:
    try:
        data = json.loads(payload)
        text = str(data.get("message") or "").strip()
    except (TypeError, ValueError):
        text = ""
    return text or fallback


class _ViewerPage(QWebEnginePage if QWebEnginePage is not None else object):
    """只允许加载本地页面，禁用外部链接跳转。"""

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            log.debug("NGL JS 错误：%s (%s:%s)", message, source, line)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        try:
            link_clicked = (
                navigation_type
                == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            )
        except AttributeError:  # pragma: no cover - Qt 版本差异
            link_clicked = False
        return not link_clicked


class NglBoxView(QWidget):
    """内嵌 NGL Viewer 的盒子视图，接口与 ``BoxGLView`` 保持一致。"""

    box_changed = Signal(object)
    failed = Signal(str)
    ready = Signal()
    structure_ready = Signal()

    READY_TIMEOUT_MS = 20000

    def __init__(self, parent: QWidget | None = None) -> None:
        if not NGL_AVAILABLE:  # pragma: no cover - 由调用方提前判断
            raise RuntimeError(NGL_UNAVAILABLE_REASON or "NGL 不可用")
        super().__init__(parent)
        self._ready = False
        self._failed = False
        self._queue: list[str] = []
        self._structure_pdb = ""
        self._box: Box | None = None
        self._show_atoms = False
        self._click_to_center = True
        self._sites_visible = False  # 洞察模式默认关闭
        # NGL 不监听窗口尺寸，画布需要显式同步；合并连续 resize 事件。
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(40)
        self._resize_timer.timeout.connect(self._sync_size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web_view = QWebEngineView(self)
        self.web_view.setPage(_ViewerPage(self.web_view))
        self.web_view.page().setBackgroundColor(QColor("#0b0f16"))
        self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.web_view.setMinimumSize(460, 380)
        layout.addWidget(self.web_view)

        self._bridge = _Bridge()
        self._bridge.box_edited.connect(self._on_box_edited)
        self._bridge.ready.connect(self._on_ready)
        self._bridge.structure_ready.connect(self.structure_ready)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.message.connect(lambda message: log.debug("NGL：%s", message))
        self._channel = QWebChannel(self.web_view.page())
        self._channel.registerObject("bridge", self._bridge)
        self.web_view.page().setWebChannel(self._channel)

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(self.READY_TIMEOUT_MS)
        self._watchdog.timeout.connect(
            lambda: self._on_failed("3D 视图初始化超时")
        )
        self._watchdog.start()

        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.setUrl(QUrl.fromLocalFile(str(VIEWER_HTML)))

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
        # 初始化时 WebEngine 往往还是最小尺寸（460×380），等这一轮事件处理完
        # 再让 NGL 按实际大小重建画布，避免只显示左上角一块。
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
        log.warning("NGL 视图不可用：%s", message)
        self.failed.emit(message)

    # -- 数据 -----------------------------------------------------------
    def set_structure(self, structure) -> None:
        """接收 ``box_view.ViewStructure``；只使用其中的 PDB 文本。"""
        pdb_text = getattr(structure, "pdb_text", "") or ""
        self._structure_pdb = pdb_text
        self._call("setStructure", pdb_text)

    def set_box(self, box: Box, *, emit: bool = False) -> None:
        self._box = box
        # 自动盒子可能来自 numpy，先转成普通 float 才能 JSON 序列化。
        self._call(
            "setBox",
            [float(value) for value in box.center],
            [float(value) for value in box.size],
        )
        if emit:  # 与 BoxGLView 接口兼容；NGL 端不会主动触发
            self.box_changed.emit(box)

    def box(self) -> Box | None:
        return self._box

    def set_site_structures(self, cocrystal_pdb: str, site_pdb: str) -> None:
        """叠加显示共晶配体与 SITE 残基（独立于主体结构的参考层）。"""
        self._call("setSiteStructures", cocrystal_pdb or "", site_pdb or "")

    def set_sites_visible(self, visible: bool) -> None:
        self._sites_visible = bool(visible)
        self._call("setSitesVisible", bool(visible))

    def set_show_atoms(self, visible: bool) -> None:
        self._show_atoms = bool(visible)
        self._call("setAtomsVisible", bool(visible))

    def set_click_to_center(self, enabled: bool) -> None:
        self._click_to_center = bool(enabled)
        self._call("setClickToCenter", bool(enabled))

    def focus_box(self) -> None:
        self._call("focusBox")

    def reset_view(self) -> None:
        self._call("resetView")

    def show_placeholder(self, message: str) -> None:
        self._call("clearStructure")
        self._call("showMessage", message)

    # -- 通信 -----------------------------------------------------------
    def _call(self, name: str, *args) -> None:
        script = f"window.dfl && window.dfl.{name}({', '.join(json.dumps(a) for a in args)})"
        if self._failed:
            return
        if self._ready:
            self.web_view.page().runJavaScript(script)
        else:
            if len(self._queue) >= 32:
                self._queue.pop(0)
            self._queue.append(script)

    def _on_box_edited(self, box: Box) -> None:
        self._box = box
        self.box_changed.emit(box)

    # -- 清理 -----------------------------------------------------------
    def shutdown(self) -> None:
        """停止定时器；窗口关闭时避免回调访问已销毁的页面。"""
        self._watchdog.stop()
        self._resize_timer.stop()
        self._bridge.box_edited.disconnect()
        self._bridge.ready.disconnect()
        self._bridge.structure_ready.disconnect()
        self._bridge.failed.disconnect()
