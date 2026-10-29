"""主窗口：侧栏导航 + 五步向导 + PyMOL 渲染页 + 后台任务编排。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppSettings
from ..core.models import (
    KIND_LIGAND,
    KIND_TARGET,
    STATUS_READY,
    DockingResult,
    ProjectFile,
)
from ..errors import DockFlowError
from ..logging_setup import get_logger, setup_logging
from ..services import modes
from ..services.analysis import save_heatmap_config
from ..services.boxes import suggest_box
from ..services.docking import DockingOptions, collect_existing_results, run_batch
from ..services.download import DownloadResult
from ..services.pose_browser import build_pose_browser
from ..services.preparation import prepare_all
from ..services.pymol import open_in_pymol
from ..services.pymol_render import find_renderer, render_complex
from ..services.report import generate_report
from ..tools.obabel import OpenBabelAdapter
from ..tools.runtime import open_path, reveal_path
from ..tools.vina import (
    ENGINE_DESCRIPTIONS,
    ENGINE_LABELS,
    ENGINE_ORDER,
    EngineAdapter,
    available_engines,
    engine_from_settings,
)
from ..version import __app_name_zh__, __version__
from ..workspace import Workspace
from . import theme
from .dialogs import BoxDialog, DownloadDialog, SettingsDialog
from .geometry import (
    WINDOW_MINIMUM,
    WINDOW_PREFERRED,
    apply_screen_minimum,
    center_window_on_screen,
    clamp_window_to_screen,
    fit_window_to_screen,
    restore_window_geometry,
    save_window_geometry,
)
from .heatmap_dialog import HeatmapDialog
from .pages import (
    LIGAND_FILTER,
    TARGET_FILTER,
    DockPage,
    MoleculesPage,
    PreparePage,
    ResultsPage,
    WelcomePage,
)
from .render_page import RenderPage
from .widgets import LogPanel
from .workers import Task

log = get_logger("ui")

TARGET_SUFFIXES = {".pdb", ".cif", ".pdbqt", ".mol2", ".sdf", ".mol"}
LIGAND_SUFFIXES = {".sdf", ".sd", ".mol2", ".mol", ".pdb", ".pdbqt", ".smi", ".smiles"}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        setup_logging()
        self.settings = AppSettings.load()
        self.workspace: Workspace | None = None
        self._task: Task | None = None
        self._busy = False
        self._obabel: OpenBabelAdapter | None = None
        self._engines: dict[str, EngineAdapter] = {}
        self._screen_connected = False
        self._shortcuts: list[QShortcut] = []

        # 界面字号随屏幕密度放大后，窗口也要同步变大，否则内容会被挤在一起。
        # 最小尺寸只按 sqrt 放大（小屏幕仍可使用），首选尺寸按完整比例放大，
        # 实际显示时 fit_window_to_screen 会把它裁剪到屏幕范围内。
        scale = theme.ui_scale()
        self._window_minimum = QSize(
            round(WINDOW_MINIMUM.width() * math.sqrt(scale)),
            round(WINDOW_MINIMUM.height() * math.sqrt(scale)),
        )
        self._window_preferred = QSize(
            round(WINDOW_PREFERRED.width() * scale),
            round(WINDOW_PREFERRED.height() * scale),
        )

        self.setWindowTitle(f"{__app_name_zh__} · DockFlowLab")

        central = QWidget()
        central.setObjectName("Root")
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.welcome_page = WelcomePage()
        self.molecules_page = MoleculesPage()
        self.prepare_page = PreparePage()
        self.dock_page = DockPage()
        self.results_page = ResultsPage()
        self.render_page = RenderPage()
        for page in (
            self.welcome_page,
            self.molecules_page,
            self.prepare_page,
            self.dock_page,
            self.results_page,
            self.render_page,
        ):
            self.stack.addWidget(page)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._restore_window_state()
        self._install_shortcuts()
        self._connect_signals()
        self.welcome_page.set_recent(self.settings.recent_workspaces)
        self.prepare_page.ph_spin.setValue(self.settings.add_hydrogens_ph)
        self.prepare_page.keep_metals_check.setChecked(self.settings.keep_metals)
        self.render_page.set_render_size(self.settings.pymol_render_size)
        self.render_page.set_label_scale(self.settings.pymol_label_scale)
        if self.settings.pymol_script_dir:
            self.render_page.set_script_dir(self.settings.pymol_script_dir)
        self.render_page.set_renderer(find_renderer(self.settings.pymol_path))
        self._apply_settings_to_controls()
        self._configure_engine_choices()
        self._update_environment()
        self._update_sidebar()
        self.statusBar().showMessage("请选择一个工作文件夹开始。", 8000)

    # ------------------------------------------------------------------
    # 侧栏
    # ------------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        # 字号放大时侧栏略加宽，但按 sqrt 放大以免挤占正文区域。
        sidebar.setFixedWidth(round(250 * math.sqrt(theme.ui_scale())))
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 22, 18, 16)
        layout.setSpacing(8)

        brand = QLabel("DockFlowLab")
        brand.setObjectName("Brand")
        brand_sub = QLabel("分子对接工作台")
        brand_sub.setObjectName("BrandSub")
        layout.addWidget(brand)
        layout.addWidget(brand_sub)
        layout.addSpacing(14)
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(10)

        self.project_name = QLabel("尚未打开项目")
        self.project_name.setObjectName("SidebarProject")
        self.project_name.setWordWrap(True)
        self.project_path = QLabel("")
        self.project_path.setObjectName("SidebarPath")
        self.project_path.setToolTip("")
        layout.addWidget(self.project_name)
        layout.addWidget(self.project_path)
        layout.addSpacing(16)

        caption = QLabel("工作流程")
        caption.setObjectName("SidebarCaption")
        layout.addWidget(caption)

        self.step_buttons: list[QPushButton] = []
        labels = [
            "1   工作目录",
            "2   添加分子",
            "3   检查与准备",
            "4   设置对接",
            "5   运行与结果",
            "6   PyMOL 渲染",
        ]
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setObjectName("StepButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._navigate(i))
            layout.addWidget(button)
            self.step_buttons.append(button)
        layout.addStretch(1)

        self.env_label = QLabel("")
        self.env_label.setObjectName("SidebarPath")
        self.env_label.setWordWrap(True)
        self.env_label.setOpenExternalLinks(False)
        layout.addWidget(self.env_label)
        layout.addSpacing(8)

        settings_button = QPushButton("设置与环境检查")
        settings_button.setObjectName("SidebarButton")
        settings_button.clicked.connect(self._open_settings)
        layout.addWidget(settings_button)
        about_button = QPushButton("关于 DockFlowLab")
        about_button.setObjectName("SidebarButton")
        about_button.clicked.connect(self._about)
        layout.addWidget(about_button)
        return sidebar

    def _update_sidebar(self) -> None:
        workspace = self.workspace
        if workspace is None:
            self.project_name.setText("尚未打开项目")
            self.project_path.setText("")
            self.project_path.setToolTip("")
        else:
            self.project_name.setText(workspace.name)
            text = str(workspace.root)
            metrics = self.fontMetrics()
            self.project_path.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, 205))
            self.project_path.setToolTip(text)

        has_entries = bool(workspace and (workspace.targets or workspace.ligands))
        ready_pairs = False
        if workspace is not None:
            ready_targets = [entry for entry in workspace.targets if entry.status == STATUS_READY]
            ready_ligands = [entry for entry in workspace.ligands if entry.status == STATUS_READY]
            ready_pairs = bool(ready_targets and ready_ligands)
        enabled = [
            True,
            workspace is not None,
            workspace is not None and has_entries,
            workspace is not None and ready_pairs,
            workspace is not None,
            workspace is not None,
        ]
        current = self.stack.currentIndex()
        for index, button in enumerate(self.step_buttons):
            button.setEnabled(enabled[index] and not self._busy)
            button.setProperty("current", "true" if index == current else "false")
            button.style().unpolish(button)
            button.style().polish(button)

    def _update_environment(self) -> None:
        usable = available_engines(self.settings)
        if usable:
            primary = self._get_engine(self.settings.docking_engine)
            if primary.available:
                text = f"{primary.label} 已就绪"
            else:
                text = f"{usable[0].label} 已就绪"
            extra = [item.label for item in usable if item.label not in text]
            if extra:
                text += " · 另可用 " + "、".join(extra)
            self.env_label.setText(
                f"<span style='color:#6ee7a8'>●</span> {text}"
            )
            self.env_label.setToolTip(
                "\n".join(f"{item.label}：{item.version or item.executable}" for item in usable)
            )
        else:
            self.env_label.setText(
                "<span style='color:#ffb4a8'>●</span> 未找到对接引擎，请在设置中指定"
            )
            self.env_label.setToolTip("对接计算需要至少一个可用的对接引擎可执行文件")

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.welcome_page.choose_requested.connect(self._choose_workspace)
        self.welcome_page.create_requested.connect(self._create_workspace)
        self.welcome_page.recent_requested.connect(self._open_recent)

        self.molecules_page.add_requested.connect(self._add_files)
        self.molecules_page.download_requested.connect(self._download)
        self.molecules_page.files_dropped.connect(self._add_dropped_files)
        self.molecules_page.remove_requested.connect(self._remove_entry)
        self.molecules_page.locate_requested.connect(self._locate_source)
        self.molecules_page.back_requested.connect(lambda: self._navigate(0))
        self.molecules_page.next_requested.connect(lambda: self._navigate(2))
        self.molecules_page.change_workspace_requested.connect(lambda: self._navigate(0))

        self.prepare_page.prepare_requested.connect(self._start_prepare)
        self.prepare_page.cancel_requested.connect(self._request_cancel)
        self.prepare_page.back_requested.connect(lambda: self._navigate(1))
        self.prepare_page.next_requested.connect(lambda: self._navigate(3))
        self.prepare_page.change_workspace_requested.connect(lambda: self._navigate(0))

        self.dock_page.box_requested.connect(self._edit_box)
        self.dock_page.auto_boxes_requested.connect(self._auto_boxes)
        self.dock_page.cancel_requested.connect(self._request_cancel)
        self.dock_page.run_requested.connect(self._start_docking)
        self.dock_page.back_requested.connect(lambda: self._navigate(2))
        self.dock_page.change_workspace_requested.connect(lambda: self._navigate(0))

        self.results_page.cancel_requested.connect(self._request_cancel)
        self.results_page.report_requested.connect(self._make_report)
        self.results_page.heatmap_edit_requested.connect(self._edit_heatmap)
        self.results_page.open_folder_requested.connect(self._open_workspace_folder)
        self.results_page.refresh_requested.connect(self._refresh_results)
        self.results_page.open_pymol_requested.connect(self._open_pymol)
        self.results_page.locate_result_requested.connect(self._locate_result)
        self.results_page.interaction_requested.connect(self._show_interactions)
        self.results_page.back_requested.connect(lambda: self._navigate(3))

        self.render_page.render_requested.connect(self._start_render)
        self.render_page.cancel_requested.connect(self._request_cancel)
        self.render_page.refresh_requested.connect(self._refresh_render_page)
        self.render_page.open_image_requested.connect(open_path)
        self.render_page.locate_image_requested.connect(reveal_path)
        self.render_page.script_dir_changed.connect(self._remember_script_dir)
        self.render_page.render_size_changed.connect(self._remember_render_size)
        self.render_page.label_scale_changed.connect(self._remember_label_scale)
        self.render_page.back_requested.connect(lambda: self._navigate(4))

    # ------------------------------------------------------------------
    # 窗口状态与快捷键
    # ------------------------------------------------------------------
    def _restore_window_state(self) -> None:
        """按当前屏幕限制尺寸，再恢复上次关闭时的位置与最大化状态。"""
        fit_window_to_screen(
            self, preferred=self._window_preferred, minimum=self._window_minimum
        )
        restored = restore_window_geometry(self, self.settings.window_geometry)
        if not restored:
            center_window_on_screen(self)
        if self.settings.window_maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _install_shortcuts(self) -> None:
        bindings: list[tuple[str, Callable]] = [
            ("Ctrl+O", self._choose_workspace),
            ("Ctrl+,", self._open_settings),
            ("Ctrl+R", self._refresh_current),
            ("F5", self._refresh_current),
            ("Ctrl+Return", self._run_from_shortcut),
            ("Ctrl+Enter", self._run_from_shortcut),
        ]
        for index in range(6):
            bindings.append((f"Ctrl+{index + 1}", lambda i=index: self._navigate(i)))
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

        self.dock_page.run_button.setToolTip("开始对接（Ctrl+Enter）")
        self.results_page.refresh_button.setToolTip("重新扫描对接结果（Ctrl+R 或 F5）")
        self.welcome_page.choose_button.setToolTip("打开工作目录（Ctrl+O）")
        for index, button in enumerate(self.step_buttons):
            button.setToolTip(f"{button.text().strip()}（Ctrl+{index + 1}）")

    def _refresh_current(self) -> None:
        """F5 / Ctrl+R：刷新当前页面看到的数据。"""
        if self.workspace is None:
            self.statusBar().showMessage("请先打开一个工作文件夹。", 4000)
            return
        if self._busy_notice():
            return
        index = self.stack.currentIndex()
        if index == 4:
            self._refresh_results()
        elif index == 5:
            self._refresh_render_page()
            self.statusBar().showMessage("已刷新渲染页。", 4000)
        else:
            self._refresh_all()
            self.statusBar().showMessage("已刷新。", 4000)

    def _run_from_shortcut(self) -> None:
        """Ctrl+Enter：在对接设置页直接开始，等价于点击主按钮。"""
        if self.stack.currentIndex() != 3:
            self.statusBar().showMessage("请先进入“设置对接”页面。", 4000)
            return
        if self._busy_notice():
            return
        if not self.dock_page.run_button.isEnabled():
            self.statusBar().showMessage("请先准备靶点和候选分子。", 4000)
            return
        self.dock_page.run_button.click()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._screen_connected:
            return
        handle = self.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self._screen_changed)
            self._screen_connected = True

    def _screen_changed(self, _screen=None) -> None:
        # 拖到更小/更大屏幕时同步最小尺寸，避免窗口被卡在旧屏幕的尺寸上；
        # 若当前大小超出新屏幕，则收缩到可见范围内。
        apply_screen_minimum(self, self._window_minimum)
        if not self.isMaximized():
            clamp_window_to_screen(self)

    # ------------------------------------------------------------------
    # 工作目录
    # ------------------------------------------------------------------
    def _choose_workspace(self) -> None:
        start = self.settings.last_workspace or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择工作文件夹", start)
        if path:
            self._load_workspace(path)

    def _create_workspace(self) -> None:
        start = self.settings.last_workspace or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择或新建一个文件夹", start)
        if path:
            self._load_workspace(path)

    def _open_recent(self, path: str) -> None:
        if not Path(path).is_dir():
            self.settings.forget_workspace(path)
            self.welcome_page.set_recent(self.settings.recent_workspaces)
            QMessageBox.warning(self, "文件夹不存在", "这个工作文件夹已被移动或删除。")
            return
        self._load_workspace(path)

    def _load_workspace(self, path: str) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "当前有任务正在运行，完成后再切换工作目录。")
            return
        root = Path(path).expanduser().resolve()
        if not (root / "project.json").is_file():
            if any(root.iterdir()):
                answer = QMessageBox.question(
                    self,
                    "在此文件夹建立项目？",
                    "该文件夹里已经有其他文件。\n\n"
                    "DockFlowLab 会新增 project.json 以及 inputs、prepared 等子文件夹，"
                    "不会改动或删除已有文件。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        try:
            workspace = Workspace.open_or_create(root)
        except (DockFlowError, OSError) as exc:
            QMessageBox.critical(self, "无法打开工作目录", str(exc))
            return
        self.workspace = workspace
        self.settings.remember_workspace(root)
        self.welcome_page.set_recent(self.settings.recent_workspaces)
        self.prepare_page.ph_spin.setValue(self.settings.add_hydrogens_ph)
        self.prepare_page.keep_metals_check.setChecked(self.settings.keep_metals)
        self._apply_settings_to_controls()
        self._refresh_all()
        self._navigate(1)
        self.statusBar().showMessage(f"已打开工作目录：{root}", 6000)
        log.info("打开工作目录：%s", root)

    def _refresh_all(self) -> None:
        workspace = self.workspace
        if workspace is None:
            return
        self.molecules_page.set_workspace(workspace)
        self.prepare_page.set_workspace(workspace)
        self.dock_page.set_workspace(workspace)
        results = collect_existing_results(workspace)
        self.results_page.show_results(workspace, results)
        self.render_page.set_workspace(workspace, results)
        self._update_sidebar()

    # ------------------------------------------------------------------
    # 添加与移除
    # ------------------------------------------------------------------
    def _add_files(self, kind: str) -> None:
        if self.workspace is None:
            return
        caption = "选择靶点结构文件" if kind == KIND_TARGET else "选择候选分子文件"
        files, _ = QFileDialog.getOpenFileNames(self, caption, "", TARGET_FILTER if kind == KIND_TARGET else LIGAND_FILTER)
        if files:
            self._import_paths(kind, files)

    def _add_dropped_files(self, kind: str, paths: list[str]) -> None:
        if self.workspace is None:
            return
        self._import_paths(kind, paths)

    def _import_paths(self, kind: str, paths: list[str]) -> None:
        if self.workspace is None:
            return
        allowed = TARGET_SUFFIXES if kind == KIND_TARGET else LIGAND_SUFFIXES
        added = 0
        skipped = 0
        errors: list[str] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                continue
            if path.suffix.lower() not in allowed:
                errors.append(f"{path.name}：格式不支持")
                continue
            before = len(self.workspace.entries(kind))
            try:
                self.workspace.import_file(kind, path)
            except DockFlowError as exc:
                errors.append(f"{path.name}：{exc}")
                continue
            if len(self.workspace.entries(kind)) > before:
                added += 1
            else:
                skipped += 1
        self._refresh_all()
        message = f"已添加 {added} 个文件"
        if skipped:
            message += f"，{skipped} 个已经存在"
        if errors:
            message += f"，{len(errors)} 个未添加"
            QMessageBox.warning(self, "部分文件未添加", "\n".join(errors[:12]))
        self.statusBar().showMessage(message, 7000)

    def _remove_entry(self, kind: str, stem: str) -> None:
        if self.workspace is None:
            return
        entry = self.workspace.get(kind, stem)
        if entry is None:
            return
        noun = "靶点" if kind == KIND_TARGET else "候选分子"
        answer = QMessageBox.question(
            self,
            f"移除{noun}",
            f"确定移除“{entry.display_name}”吗？\n\n"
            "原始文件、准备文件和该靶点相关的对接结果会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.workspace.remove_entry(entry, purge_results=(kind == KIND_TARGET))
        except OSError as exc:
            QMessageBox.warning(self, "移除失败", str(exc))
            return
        self._refresh_all()
        self.statusBar().showMessage(f"已移除：{entry.display_name}", 5000)

    def _locate_source(self, kind: str, stem: str) -> None:
        if self.workspace is None:
            return
        entry = self.workspace.get(kind, stem)
        if entry is None:
            return
        path = self.workspace.absolute(entry.source)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", "原始文件可能已被移动或删除。")
            return
        reveal_path(path)

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def _download(self, kind: str) -> None:
        if self.workspace is None:
            return
        dialog = DownloadDialog(kind, self.settings, self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.result is None:
            return
        result: DownloadResult = dialog.result
        try:
            entry = self.workspace.import_bytes(
                kind,
                result.data,
                result.filename,
                display_name=result.display_name,
                meta={key: value for key, value in result.meta.items() if key != "warnings"},
            )
        except DockFlowError as exc:
            QMessageBox.warning(self, "无法加入项目", str(exc))
            return
        self._refresh_all()
        self.statusBar().showMessage(f"已加入：{entry.display_name}", 7000)
        warnings = result.meta.get("warnings") or []
        if warnings:
            QMessageBox.information(self, "下载提示", "\n".join(str(item) for item in warnings))

    # ------------------------------------------------------------------
    # 准备
    # ------------------------------------------------------------------
    def _get_obabel(self) -> OpenBabelAdapter:
        if self._obabel is None:
            self._obabel = OpenBabelAdapter(self.settings.obabel_path)
        return self._obabel

    def _get_engine(self, key: str) -> EngineAdapter:
        if key not in self._engines:
            self._engines[key] = engine_from_settings(self.settings, key)
        return self._engines[key]

    def _configure_engine_choices(self) -> None:
        """按当前设置刷新“对接引擎”下拉框与实际可用的引擎。"""
        choices = []
        for key in ENGINE_ORDER:
            engine = self._get_engine(key)
            choices.append(
                (key, ENGINE_LABELS[key], engine.available, ENGINE_DESCRIPTIONS[key])
            )
        self.dock_page.set_engine_choices(choices, self.settings.docking_engine)

    def _fallback_engines(self, primary: EngineAdapter) -> list[EngineAdapter]:
        """按优先级返回其他可用引擎，供首选引擎卡死时自动重试。"""
        if not self.settings.engine_fallback:
            return []
        engines = []
        for key in ENGINE_ORDER:
            if key == primary.key:
                continue
            engine = self._get_engine(key)
            if engine.available:
                engines.append(engine)
        return engines

    def _start_prepare(self, force: bool) -> None:
        if self.workspace is None or self._busy_notice():
            return
        self.settings.add_hydrogens_ph = float(self.prepare_page.ph_spin.value())
        self.settings.keep_metals = bool(
            self.prepare_page.keep_metals_check.isChecked()
        )
        self.settings.save()
        obabel = self._get_obabel()
        workspace = self.workspace
        self.prepare_page.start_running()
        self._set_busy(True)

        def job(progress, cancel, line):
            return prepare_all(
                workspace,
                self.settings,
                force=force,
                obabel=obabel,
                progress=progress,
                cancel=cancel,
            )

        self._run_task(job, self.prepare_page.log_panel, self._prepare_done, self._prepare_failed)

    def _prepare_done(self, report) -> None:
        self._set_busy(False)
        self._refresh_all()
        if report.cancelled:
            message = f"已取消。成功 {report.succeeded} 个，失败 {report.failed} 个。"
            self.prepare_page.finish_running(message, False)
        else:
            message = (
                f"准备完成：成功 {report.succeeded} 个，跳过 {report.skipped} 个，"
                f"失败 {report.failed} 个。"
            )
            self.prepare_page.finish_running(message, report.failed == 0)
        failures = [outcome.message for _, outcome in report.outcomes if not outcome.ok]
        if failures:
            QMessageBox.warning(
                self,
                "部分条目需要处理",
                "以下条目没有准备成功：\n\n" + "\n".join(f"· {item}" for item in failures[:10]),
            )
        elif not report.cancelled and report.succeeded:
            self.statusBar().showMessage("结构准备完成。可以进入下一步设置对接盒子。", 8000)

    def _prepare_failed(self, message: str) -> None:
        self._set_busy(False)
        self.prepare_page.refresh(self.workspace)
        self.prepare_page.finish_running(f"准备失败：{message}", False)
        QMessageBox.critical(self, "准备失败", message)

    # ------------------------------------------------------------------
    # 盒子
    # ------------------------------------------------------------------
    def _edit_box(self, stem: str) -> None:
        if self.workspace is None or self._busy_notice():
            return
        target = self.workspace.get(KIND_TARGET, stem)
        if target is None:
            return
        dialog = BoxDialog(
            self.workspace,
            target,
            self.settings,
            obabel=self._get_obabel(),
            parent=self,
        )
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.box is not None:
            self.dock_page.refresh(self.workspace)
            self.statusBar().showMessage(f"盒子已保存：{target.display_name}", 6000)

    def _auto_boxes(self) -> None:
        if self.workspace is None or self._busy_notice():
            return
        workspace = self.workspace
        ready_targets = [entry for entry in workspace.targets if entry.status == STATUS_READY]
        ready_ligands = [entry for entry in workspace.ligands if entry.is_ready]
        if not ready_targets:
            QMessageBox.information(self, "没有可用的靶点", "请先在“检查与准备”中准备靶点。")
            return
        supported = modes.supported_pairs(ready_targets, ready_ligands, workspace)
        if not supported:
            QMessageBox.information(
                self,
                "没有可运行的组合",
                "当前的靶点与候选分子组合都属于不支持的对接类型，无法生成盒子。\n\n"
                "蛋白–蛋白、蛋白–多肽等大分子配体对接请使用 ClusPro、HADDOCK 或 RosettaDock。",
            )
            return
        ligands_by_target: dict[str, list] = {}
        for target, ligand, _mode in supported:
            ligands_by_target.setdefault(target.stem, []).append(
                workspace.absolute(ligand.prepared)
            )
        targets = [target for target in ready_targets if target.stem in ligands_by_target]
        settings = self.settings
        obabel = self._get_obabel()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        failures: list[str] = []

        def job(progress, cancel, line):
            errors = []
            generated = []
            box_warnings: dict[str, list[str]] = {}
            cancelled = False
            for index, target in enumerate(targets, start=1):
                if cancel.is_set():
                    cancelled = True
                    break
                progress(index - 1, len(targets), f"正在计算：{target.display_name}")
                try:
                    box, notes = suggest_box(
                        target_source=workspace.absolute(target.source),
                        target_prepared_pdb=workspace.prepared_target_pdb(target.stem),
                        ligand_paths=ligands_by_target[target.stem],
                        settings=settings,
                        target_kind=modes.target_kind_for(target, workspace),
                        chains=target.meta.get("chains", []),
                        obabel=obabel,
                    )
                    workspace.save_box(target.stem, box)
                    workspace.update_entry(
                        target, box=workspace.relative(workspace.box_path(target.stem))
                    )
                    generated.append(target)
                    cleaned = [str(note).strip() for note in notes if str(note).strip()]
                    if cleaned:
                        box_warnings[target.stem] = cleaned
                except Exception as exc:
                    errors.append(f"{target.display_name}：{exc}")
            return {
                "errors": errors,
                "generated": generated,
                "box_warnings": box_warnings,
                "total": len(targets),
                "cancelled": cancelled,
            }

        def done(payload) -> None:
            QApplication.restoreOverrideCursor()
            self._set_busy(False)
            self.dock_page.refresh(workspace)
            errors = payload["errors"]
            generated = payload["generated"]
            cancelled = payload["cancelled"]
            review = []
            for target in generated:
                saved = workspace.load_box(target.stem)
                if saved is not None and saved.needs_review:
                    review.append(f"· {target.display_name}（{saved.source_label}）")
            if errors:
                QMessageBox.warning(self, "部分盒子未生成", "\n".join(errors[:10]))
            if cancelled:
                self.statusBar().showMessage(
                    f"已取消：已为 {len(generated)}/{payload['total']} 个靶点生成盒子。",
                    7000,
                )
                return
            sections = []
            if review:
                sections.append(
                    "以下靶点无法自动确定可靠的结合位点，盒子只是兜底结果：\n\n"
                    + "\n".join(review[:10])
                    + "\n\n请在对应行点击“设置盒子”，手动调整中心与边长后再运行对接。"
                )
            warning_lines = []
            for target in generated:
                for note in payload.get("box_warnings", {}).get(target.stem, []):
                    warning_lines.append(f"· {target.display_name}：{note}")
            if warning_lines:
                sections.append("盒子生成提示：\n\n" + "\n".join(warning_lines[:10]))
            if sections:
                QMessageBox.information(self, "自动盒子提示", "\n\n".join(sections))
            elif not errors:
                self.statusBar().showMessage("已为所有靶点生成并保存盒子。", 7000)

        def failed(message: str) -> None:
            QApplication.restoreOverrideCursor()
            self._set_busy(False)
            QMessageBox.warning(self, "自动盒子失败", message)

        self._set_busy(True)
        self._run_task(job, None, done, failed)

    # ------------------------------------------------------------------
    # 对接
    # ------------------------------------------------------------------
    def _start_docking(self, params: dict) -> None:
        if self.workspace is None or self._busy_notice():
            return
        workspace = self.workspace
        ready_targets = [entry for entry in workspace.targets if entry.status == STATUS_READY]
        ready_ligands = [entry for entry in workspace.ligands if entry.status == STATUS_READY]
        if not ready_targets or not ready_ligands:
            QMessageBox.information(self, "还不能开始", "请先准备至少一个靶点和一个候选分子。")
            return
        engine_key = str(params.get("engine") or self.settings.docking_engine)
        engine = self._get_engine(engine_key)
        if not engine.available:
            QMessageBox.warning(
                self,
                f"未找到 {engine.label}",
                f"当前无法启动 {engine.label}。请在“设置”中指定它的可执行文件，"
                f"或把 {engine_key} 放到程序目录的 resources/{engine_key} 下。\n\n"
                "也可以回到本页把“对接引擎”改为 AutoDock Vina。",
            )
            return

        self.settings.docking_engine = engine_key
        self.settings.exhaustiveness = int(params["exhaustiveness"])
        self.settings.num_modes = int(params["num_modes"])
        self.settings.seed = int(params["seed"])
        self.settings.cpu = int(params["cpu"])
        self.settings.save()
        options = DockingOptions(
            exhaustiveness=self.settings.exhaustiveness,
            num_modes=self.settings.num_modes,
            energy_range=self.settings.energy_range,
            seed=self.settings.seed,
            cpu=self.settings.cpu,
            engine=engine_key,
            fallback=self.settings.engine_fallback,
            skip_existing=False,
            make_complex=True,
            make_pymol=True,
        )
        fallback_engines = self._fallback_engines(engine)
        supported = modes.supported_pairs(ready_targets, ready_ligands, workspace)
        if not supported:
            reasons: list[str] = []
            for target in ready_targets:
                for ligand in ready_ligands:
                    mode = modes.pair_mode(target, ligand, workspace)
                    if not mode.supported:
                        reasons.append(
                            f"{target.display_name} + {ligand.display_name}：{mode.label}"
                        )
            QMessageBox.information(
                self,
                "没有可运行的组合",
                "当前组合都属于不支持的对接类型，不会执行 Vina 计算：\n\n"
                + "\n".join(f"· {item}" for item in reasons[:8])
                + "\n\n蛋白–蛋白、蛋白–多肽等大分子配体对接请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。",
            )
            return
        pairs = [(target, ligand) for target, ligand, _mode in supported]
        ligands_by_target: dict[str, list] = {}
        for target, ligand, _mode in supported:
            ligands_by_target.setdefault(target.stem, []).append(
                workspace.absolute(ligand.prepared)
            )
        runnable_targets = [target for target in ready_targets if target.stem in ligands_by_target]

        # 自动盒子无法可靠定位时（盲对接、按分子尺寸估算），先让用户确认，
        # 而不是静默使用覆盖整个结构的盒子。
        unconfirmed: list[tuple[ProjectFile, str]] = []
        for target in runnable_targets:
            saved = workspace.load_box(target.stem)
            if saved is None:
                # 蛋白/核酸通常能自动找到几何口袋；多肽常只有盲对接，
                # 小分子靶点只能按尺寸估算，先请用户确认。
                kind = modes.target_kind_for(target, workspace)
                if kind in {"peptide", "small_molecule"}:
                    unconfirmed.append((target, "尚未设置盒子，自动推荐可能不可靠"))
            elif saved.needs_review:
                unconfirmed.append((target, f"{saved.source_label}，无法可靠定位"))
        if unconfirmed:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Information)
            dialog.setWindowTitle("请先确认搜索盒子")
            dialog.setText("以下靶点的盒子不是手动设置的：")
            dialog.setInformativeText(
                "\n".join(f"· {item.display_name}（{reason}）" for item, reason in unconfirmed[:8])
                + "\n\n自动盒子可能无法覆盖当前对接场景：多肽靶点常没有可靠口袋，"
                "小分子靶点只能按两侧分子尺寸估算。建议先手动设置中心与边长。"
            )
            edit_button = dialog.addButton("去设置盒子", QMessageBox.ButtonRole.ActionRole)
            auto_button = dialog.addButton("自动计算并继续", QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is edit_button:
                self._edit_box(unconfirmed[0][0].stem)
                return
            if dialog.clickedButton() is not auto_button:
                return

        obabel = self._get_obabel()
        settings = self.settings

        self._navigate(4)
        self.results_page.start_running()
        self._set_busy(True)

        def job(progress, cancel, line):
            boxes = {}
            box_errors: dict[str, str] = {}
            for target in runnable_targets:
                box = workspace.load_box(target.stem)
                if box is None:
                    try:
                        box, _ = suggest_box(
                            target_source=workspace.absolute(target.source),
                            target_prepared_pdb=workspace.prepared_target_pdb(target.stem),
                            ligand_paths=ligands_by_target[target.stem],
                            settings=settings,
                            target_kind=modes.target_kind_for(target, workspace),
                            chains=target.meta.get("chains", []),
                            obabel=obabel,
                        )
                    except Exception as exc:
                        box_errors[target.stem] = f"无法生成对接盒子：{exc}"
                        line(f"{target.display_name}：无法生成盒子（{exc}），该靶点将跳过")
                        continue
                    workspace.save_box(target.stem, box)
                    workspace.update_entry(target, box=workspace.relative(workspace.box_path(target.stem)))
                boxes[target.stem] = box

            runnable_pairs = [(target, ligand) for target, ligand in pairs if target.stem in boxes]

            def on_line(target_stem: str, ligand_stem: str, text: str) -> None:
                line(f"{target_stem} / {ligand_stem}  {text}")

            results = run_batch(
                workspace,
                runnable_pairs,
                boxes,
                options=options,
                settings=settings,
                vina=engine,
                fallback_engines=fallback_engines,
                obabel=obabel,
                progress=progress,
                cancel=cancel,
                on_line=on_line,
            )
            for target, ligand in pairs:
                if target.stem in box_errors:
                    mode = modes.pair_mode(target, ligand, workspace)
                    results.append(
                        DockingResult(
                            target=target.stem,
                            ligand=ligand.stem,
                            ok=False,
                            error=box_errors[target.stem],
                            mode_key=mode.key,
                            mode_label=mode.label,
                            mode_supported=mode.supported,
                        )
                    )
            # 取消时 run_batch 会提前结束；只有确实还有组合没跑完才算取消，
            # 避免用户在最后一组刚好结束时点取消却得到“已取消”的提示。
            cancelled = cancel.is_set() and len(results) < len(pairs)
            report_path = ""
            report_error = ""
            if not cancelled:
                try:
                    report_path = str(generate_report(workspace, results, settings=settings))
                except Exception as exc:
                    report_error = str(exc)
                    log.warning("自动生成报告失败：%s", exc, exc_info=True)
            return {
                "results": results,
                "report": report_path,
                "report_error": report_error,
                "cancelled": cancelled,
            }

        self._run_task(job, self.results_page.log_panel, self._docking_done, self._docking_failed)

    def _docking_done(self, payload: dict) -> None:
        self._set_busy(False)
        workspace = self.workspace
        results = payload.get("results", [])
        cancelled = bool(payload.get("cancelled"))
        success = [item for item in results if item.ok]
        # 取消时未完成的组合会被 Vina 标记为“已取消”，不应再提示“部分对接失败”。
        failed = len([item for item in results if not item.ok and item.error != "已取消"])
        if cancelled:
            message = f"对接已取消：完成 {len(success)} 组，其余组合未运行。"
            self.results_page.finish_running(message, False)
        else:
            message = f"对接完成：成功 {len(success)} 组，失败 {failed} 组。"
            self.results_page.finish_running(message, failed == 0)
        if workspace is not None:
            self._refresh_all()
            self.results_page.show_results(workspace, results)
        report_path = payload.get("report", "")
        if cancelled:
            self.statusBar().showMessage(message, 8000)
        elif report_path:
            self.statusBar().showMessage(f"对接完成，报告已生成：{report_path}", 10000)
        else:
            self.statusBar().showMessage(message, 8000)
        if failed and not cancelled:
            QMessageBox.warning(
                self,
                "部分对接失败",
                "失败原因已写在结果表的“说明”列。常见原因包括盒子位置不合适、"
                "配体原子类型不支持或计算资源不足。",
            )

    def _docking_failed(self, message: str) -> None:
        self._set_busy(False)
        self.results_page.finish_running(f"对接失败：{message}", False)
        QMessageBox.critical(self, "对接失败", message)

    # ------------------------------------------------------------------
    # 结果与报告
    # ------------------------------------------------------------------
    def _refresh_results(self) -> None:
        if self.workspace is None or self._busy_notice():
            return
        results = collect_existing_results(self.workspace)
        self.results_page.show_results(self.workspace, results)
        self.statusBar().showMessage(f"已刷新：{len(results)} 组结果。", 5000)

    def _make_report(self) -> None:
        if self.workspace is None or self._busy_notice():
            return
        workspace = self.workspace
        settings = self.settings
        self._set_busy(True)
        self.statusBar().showMessage("正在生成报告…")

        def job(progress, cancel, line):
            return str(generate_report(workspace, None, settings=settings))

        def done(path: str) -> None:
            self._set_busy(False)
            self.results_page.show_results(workspace, collect_existing_results(workspace))
            self.statusBar().showMessage(f"报告已生成：{path}", 8000)
            answer = QMessageBox.question(
                self,
                "报告已生成",
                f"报告保存在：\n{path}\n\n是否立即用浏览器打开？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                open_path(path)

        def failed(message: str) -> None:
            self._set_busy(False)
            QMessageBox.critical(self, "报告生成失败", message)

        self._run_task(job, None, done, failed)

    def _edit_heatmap(self) -> None:
        if self.workspace is None or self._busy_notice():
            return
        workspace = self.workspace
        results = collect_existing_results(workspace)
        if not results or not (workspace.analysis_dir / "heatmap.png").is_file():
            QMessageBox.information(
                self,
                "还没有热图",
                "请先生成一次结果报告。生成热图后才能修改名称、配色与导出格式。",
            )
            return

        dialog = HeatmapDialog(workspace, results, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_config is None:
            return
        save_heatmap_config(workspace, dialog.result_config)
        self._set_busy(True)
        self.statusBar().showMessage("正在应用热图设置…")
        try:
            generate_report(workspace, results, settings=self.settings)
        except Exception as exc:
            log.warning("应用热图设置失败：%s", exc, exc_info=True)
            QMessageBox.critical(self, "热图生成失败", str(exc))
            return
        finally:
            self._set_busy(False)
        self.results_page.show_results(workspace, results)
        self.statusBar().showMessage("热图与报告已更新。", 8000)

    def _open_workspace_folder(self) -> None:
        if self.workspace is None:
            self.statusBar().showMessage("请先打开一个工作文件夹。", 4000)
            return
        open_path(self.workspace.root)

    def _open_pymol(self, target_stem: str, ligand_stem: str) -> None:
        if self.workspace is None:
            return
        pml = self.workspace.pymol_path(target_stem, ligand_stem)
        if not pml.is_file():
            QMessageBox.information(self, "还没有可视化脚本", "这组结果没有生成 PyMOL 脚本。")
            return
        ok, message = open_in_pymol(pml)
        if not ok:
            answer = QMessageBox.question(
                self,
                "无法启动 PyMOL",
                f"{message}\n\n是否打开脚本所在文件夹？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                reveal_path(pml)

    def _locate_result(self, target_stem: str, ligand_stem: str) -> None:
        if self.workspace is None:
            return
        complex_path = self.workspace.complex_path(target_stem, ligand_stem)
        if complex_path.is_file():
            reveal_path(complex_path)
        else:
            reveal_path(self.workspace.docking_dir(target_stem))

    def _show_interactions(self, target_stem: str, ligand_stem: str) -> None:
        """打开对接细节：多构象浏览 + 相互作用列表。"""
        if self.workspace is None:
            return
        complex_path = self.workspace.complex_path(target_stem, ligand_stem)
        data = None
        try:
            data = build_pose_browser(self.workspace, target_stem, ligand_stem)
        except Exception as exc:
            log.warning("构象浏览准备失败：%s", exc)
            self.statusBar().showMessage("构象列表生成失败，只显示最佳构象。", 6000)
        options = [
            option
            for option in (data.options if data else [])
            if option.complex_path is not None and Path(option.complex_path).is_file()
        ]
        if options:
            complex_path = options[0].complex_path
        if complex_path is None or not complex_path.is_file():
            QMessageBox.information(
                self,
                "还没有复合物",
                "这组结果没有生成复合物结构，暂时无法查看对接细节。",
            )
            return
        target = self.workspace.get(KIND_TARGET, target_stem)
        ligand = self.workspace.get(KIND_LIGAND, ligand_stem)
        target_label = target.display_name if target else target_stem
        ligand_label = ligand.display_name if ligand else ligand_stem
        # QtWebEngine 较重，只在用户真正打开细节时加载。
        from .interaction_dialog import InteractionDialog

        dialog = InteractionDialog(
            complex_path,
            target_label,
            ligand_label,
            self,
            pose_options=options if len(options) > 1 else [],
            convergence_message=data.message if data else "",
        )
        dialog.exec()

    # ------------------------------------------------------------------
    # PyMOL 渲染
    # ------------------------------------------------------------------
    def _remember_script_dir(self, path: str) -> None:
        if path and path != self.settings.pymol_script_dir:
            self.settings.pymol_script_dir = path
            self.settings.save()

    def _remember_render_size(self, size: int) -> None:
        if int(size) != self.settings.pymol_render_size:
            self.settings.pymol_render_size = int(size)
            self.settings.save()

    def _remember_label_scale(self, scale: float) -> None:
        scale = max(0.5, min(4.0, float(scale)))
        if abs(scale - self.settings.pymol_label_scale) > 1e-6:
            self.settings.pymol_label_scale = scale
            self.settings.save()

    def _refresh_render_page(self) -> None:
        if self.workspace is None or self._busy:
            return
        self.render_page.set_workspace(
            self.workspace, collect_existing_results(self.workspace)
        )

    def _start_render(self, payload: dict) -> None:
        if self.workspace is None or self._busy:
            return
        renderer = find_renderer(self.settings.pymol_path)
        if not renderer:
            QMessageBox.warning(
                self,
                "未检测到 PyMOL",
                "渲染需要安装 PyMOL。请在“设置与环境检查”中指定 pymol 可执行文件后重试。",
            )
            return
        scripts = payload.get("scripts") or []
        complex_path = Path(str(payload.get("complex_path", "")))
        if not scripts or not complex_path.is_file():
            QMessageBox.warning(
                self,
                "无法开始渲染",
                "没有找到所选复合物或风格脚本，请刷新后重新选择。",
            )
            return
        width = max(200, min(4000, int(payload.get("width") or 1200)))
        height = max(200, min(4000, int(payload.get("height") or width)))
        try:
            label_scale = float(payload.get("label_scale") or 1.5)
        except (TypeError, ValueError):
            label_scale = 1.5
        label_scale = max(0.5, min(4.0, label_scale))
        total = len(scripts)
        self.render_page.start_running(total)
        self._set_busy(True)
        self.statusBar().showMessage(f"正在渲染 {total} 张图片…")

        def job(progress, cancel, line):
            results = []
            for index, item in enumerate(scripts, start=1):
                if cancel.is_set():
                    break
                label = str(item.get("label") or Path(str(item.get("path"))).name)
                progress(index - 1, total, f"正在渲染：{label}")
                line(f"=== {label} ===")
                result = render_complex(
                    complex_path,
                    Path(str(item.get("path", ""))),
                    Path(str(item.get("output", ""))),
                    width=width,
                    height=height,
                    timeout=600.0,
                    renderer=renderer,
                    label_scale=label_scale,
                    cancel=cancel,
                    on_line=line,
                )
                results.append(
                    {"label": label, "result": result, "output": str(item.get("output", ""))}
                )
            progress(total, total, "渲染结束")
            return results

        self._run_task(
            job,
            self.render_page.log_panel,
            self._render_done,
            self._render_failed,
        )

    def _render_done(self, results) -> None:
        self._set_busy(False)
        results = results or []
        self.render_page.finish(results)
        failures = [
            item
            for item in results
            if item.get("result") is not None
            and not item["result"].ok
            and not item["result"].cancelled
        ]
        cancelled = any(
            item.get("result") is not None and item["result"].cancelled
            for item in results
        )
        if failures:
            messages = [
                f"· {item.get('label', '')}：{item['result'].error}"
                for item in failures[:6]
            ]
            QMessageBox.warning(
                self,
                "部分图片没有生成",
                "\n".join(messages)
                + "\n\n可以降低分辨率、减少同时选择的脚本，或在“查看运行日志”中检查详情。",
            )
        elif cancelled or not results:
            self.statusBar().showMessage("渲染已取消。", 6000)
        elif results:
            self.statusBar().showMessage(f"渲染完成：{len(results)} 张图片。", 8000)

    def _render_failed(self, message: str) -> None:
        self._set_busy(False)
        self.render_page.abort(f"渲染失败：{message}")
        QMessageBox.critical(self, "渲染失败", message)

    # ------------------------------------------------------------------
    # 设置与通用
    # ------------------------------------------------------------------
    def _apply_settings_to_controls(self) -> None:
        index = self.dock_page.exhaustiveness.findData(self.settings.exhaustiveness)
        if index >= 0:
            self.dock_page.exhaustiveness.setCurrentIndex(index)
        self.dock_page.num_modes.setValue(self.settings.num_modes)
        self.dock_page.seed.setValue(self.settings.seed)
        self.dock_page.cpu.setValue(self.settings.cpu)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.settings = dialog.settings
        self._engines.clear()
        self._obabel = None
        self._apply_settings_to_controls()
        self._configure_engine_choices()
        self._update_environment()
        self.render_page.set_renderer(find_renderer(self.settings.pymol_path))
        self.statusBar().showMessage("设置已保存。", 5000)

    def _navigate(self, index: int) -> None:
        if index < 0 or index >= self.stack.count():
            return
        if not self.step_buttons[index].isEnabled() and index != 0:
            return
        if self._busy and index != self.stack.currentIndex():
            return
        self.stack.setCurrentIndex(index)
        if index == 1 and self.workspace is not None:
            self.molecules_page.refresh(self.workspace)
        elif index == 2 and self.workspace is not None:
            self.prepare_page.refresh(self.workspace)
        elif index == 3 and self.workspace is not None:
            self.dock_page.refresh(self.workspace)
        elif index == 4 and self.workspace is not None and not self._busy:
            self.results_page.show_results(self.workspace, collect_existing_results(self.workspace))
        elif index == 5 and self.workspace is not None and not self._busy:
            self.render_page.set_renderer(find_renderer(self.settings.pymol_path))
            self.render_page.refresh_complexes()
        self._update_sidebar()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_sidebar()
        self.dock_page.set_busy(busy)
        self.results_page.set_busy(busy)
        if busy:
            self.statusBar().showMessage("任务运行中…")

    def _busy_notice(self) -> bool:
        """任务运行中时给出明确反馈，避免按钮点了没反应。"""
        if not self._busy:
            return False
        self.statusBar().showMessage(
            "任务运行中，请等待完成；需要中止时点击日志面板中的“取消”。", 5000
        )
        return True

    def _run_task(
        self,
        job: Callable,
        log_panel: LogPanel | None,
        on_done: Callable,
        on_failed: Callable,
    ) -> None:
        if self._task is not None:
            return
        task = Task(job, self)
        self._task = task
        if log_panel is not None:
            def handle_progress(current: int, total: int, message: str) -> None:
                log_panel.set_progress(current, total, message)
                if message:
                    log_panel.append_line(message)

            task.progress.connect(handle_progress)
            task.line.connect(log_panel.append_line)
        task.done.connect(on_done)
        task.failed.connect(on_failed)
        task.finished.connect(self._task_finished)
        task.start()

    def _task_finished(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.deleteLater()
        self._set_busy(False)

    def _request_cancel(self) -> None:
        if self._task is None:
            return
        self._task.request_cancel()
        for page in (self.prepare_page, self.results_page, self.render_page):
            if page.log_panel.isVisible():
                page.log_panel.request_cancel()
        self.statusBar().showMessage("正在取消…", 5000)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {__app_name_zh__}",
            f"<b>DockFlowLab {__version__}</b><br><br>"
            "支持蛋白–小分子、多肽–小分子、核酸–小分子与小分子–小分子对接。<br>"
            "程序会根据结构类型自动选择模式；蛋白–蛋白、蛋白–多肽等大分子对接"
            "不在本地实现，请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。<br>"
            "对接计算可使用 AutoDock Vina、QuickVina 2 或 Smina；"
            "结构准备使用 RDKit、Meeko、PDBFixer 与 OpenBabel；"
            "分子渲染包含 PyMOL(TM)。<br><br>"
            "PyMOL is a trademark of Schrodinger, LLC.<br><br>"
            "结果仅用于科研参考，需结合实验验证。",
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._task is not None and self._task.isRunning():
            QMessageBox.information(
                self,
                "任务正在运行",
                "请先点击“取消”，等待任务停止后再关闭窗口。",
            )
            event.ignore()
            return
        # 记住窗口位置与大小，下次启动时恢复到同一块屏幕。
        self.settings.window_maximized = self.isMaximized()
        self.settings.window_geometry = save_window_geometry(self)
        self.settings.save()
        super().closeEvent(event)
