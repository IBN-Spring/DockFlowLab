"""主窗口：侧栏导航 + 五步向导 + 后台任务编排。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
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
from ..core.models import KIND_LIGAND, KIND_TARGET, STATUS_READY, DockingResult
from ..errors import DockFlowError
from ..logging_setup import get_logger, setup_logging
from ..services import modes
from ..services.boxes import suggest_box
from ..services.docking import DockingOptions, collect_existing_results, run_batch
from ..services.download import DownloadResult
from ..services.preparation import prepare_all
from ..services.pymol import open_in_pymol
from ..services.report import generate_report
from ..tools.obabel import OpenBabelAdapter
from ..tools.runtime import open_path, reveal_path
from ..tools.vina import VinaAdapter
from ..version import __app_name_zh__, __version__
from ..workspace import Workspace
from . import theme
from .dialogs import BoxDialog, DownloadDialog, SettingsDialog
from .pages import (
    LIGAND_FILTER,
    TARGET_FILTER,
    DockPage,
    MoleculesPage,
    PreparePage,
    ResultsPage,
    WelcomePage,
)
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
        self._vina: VinaAdapter | None = None

        self.setWindowTitle(f"{__app_name_zh__} · DockFlowLab")
        self.resize(1280, 840)
        self.setMinimumSize(1080, 720)

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
        for page in (
            self.welcome_page,
            self.molecules_page,
            self.prepare_page,
            self.dock_page,
            self.results_page,
        ):
            self.stack.addWidget(page)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._connect_signals()
        self.welcome_page.set_recent(self.settings.recent_workspaces)
        self.prepare_page.ph_spin.setValue(self.settings.add_hydrogens_ph)
        self._apply_settings_to_controls()
        self._update_environment()
        self._update_sidebar()
        self.statusBar().showMessage("请选择一个工作文件夹开始。", 8000)

    # ------------------------------------------------------------------
    # 侧栏
    # ------------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(250)
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
        labels = ["1   工作目录", "2   添加分子", "3   检查与准备", "4   设置对接", "5   运行与结果"]
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
        ]
        current = self.stack.currentIndex()
        for index, button in enumerate(self.step_buttons):
            button.setEnabled(enabled[index] and not self._busy)
            button.setProperty("current", "true" if index == current else "false")
            button.style().unpolish(button)
            button.style().polish(button)

    def _update_environment(self) -> None:
        vina = self._get_vina()
        if vina.available:
            self.env_label.setText(
                f"<span style='color:#6ee7a8'>●</span> Vina 已就绪"
            )
            self.env_label.setToolTip(vina.version)
        else:
            self.env_label.setText(
                "<span style='color:#ffb4a8'>●</span> 未找到 Vina，请在设置中指定"
            )
            self.env_label.setToolTip("对接计算需要 AutoDock Vina 可执行文件")

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
        self.dock_page.run_requested.connect(self._start_docking)
        self.dock_page.back_requested.connect(lambda: self._navigate(2))
        self.dock_page.change_workspace_requested.connect(lambda: self._navigate(0))

        self.results_page.cancel_requested.connect(self._request_cancel)
        self.results_page.report_requested.connect(self._make_report)
        self.results_page.open_folder_requested.connect(self._open_workspace_folder)
        self.results_page.refresh_requested.connect(self._refresh_results)
        self.results_page.open_pymol_requested.connect(self._open_pymol)
        self.results_page.locate_result_requested.connect(self._locate_result)
        self.results_page.back_requested.connect(lambda: self._navigate(3))

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

    def _get_vina(self) -> VinaAdapter:
        if self._vina is None:
            self._vina = VinaAdapter(self.settings.vina_path)
        return self._vina

    def _start_prepare(self, force: bool) -> None:
        if self.workspace is None or self._busy:
            return
        self.settings.add_hydrogens_ph = float(self.prepare_page.ph_spin.value())
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
        self.prepare_page.finish_running(f"准备失败：{message}", False)
        QMessageBox.critical(self, "准备失败", message)

    # ------------------------------------------------------------------
    # 盒子
    # ------------------------------------------------------------------
    def _edit_box(self, stem: str) -> None:
        if self.workspace is None:
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
        if self.workspace is None or self._busy:
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
                "蛋白–蛋白等大分子对接请使用 ClusPro、HADDOCK 或 RosettaDock。",
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
            for index, target in enumerate(targets, start=1):
                if cancel.is_set():
                    break
                progress(index, len(targets), f"正在计算：{target.display_name}")
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
                    workspace.save_box(target.stem, box)
                    workspace.update_entry(
                        target, box=workspace.relative(workspace.box_path(target.stem))
                    )
                except Exception as exc:
                    errors.append(f"{target.display_name}：{exc}")
            return errors

        def done(errors) -> None:
            QApplication.restoreOverrideCursor()
            self._set_busy(False)
            self.dock_page.refresh(workspace)
            if errors:
                QMessageBox.warning(self, "部分盒子未生成", "\n".join(errors[:10]))
            else:
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
        if self.workspace is None or self._busy:
            return
        workspace = self.workspace
        ready_targets = [entry for entry in workspace.targets if entry.status == STATUS_READY]
        ready_ligands = [entry for entry in workspace.ligands if entry.status == STATUS_READY]
        if not ready_targets or not ready_ligands:
            QMessageBox.information(self, "还不能开始", "请先准备至少一个靶点和一个候选分子。")
            return
        vina = self._get_vina()
        if not vina.available:
            QMessageBox.warning(
                self,
                "未找到 Vina",
                "对接需要 AutoDock Vina。请在“设置”中指定 vina 可执行文件，"
                "或把 vina 放到程序目录的 resources/vina 下。",
            )
            return

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
            skip_existing=False,
            make_complex=True,
            make_pymol=True,
        )
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
                + "\n\n蛋白–蛋白等大分子对接请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。",
            )
            return
        pairs = [(target, ligand) for target, ligand, _mode in supported]
        ligands_by_target: dict[str, list] = {}
        for target, ligand, _mode in supported:
            ligands_by_target.setdefault(target.stem, []).append(
                workspace.absolute(ligand.prepared)
            )
        runnable_targets = [target for target in ready_targets if target.stem in ligands_by_target]
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
                vina=vina,
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
            report_path = ""
            report_error = ""
            try:
                report_path = str(generate_report(workspace, results, settings=settings))
            except Exception as exc:
                report_error = str(exc)
                log.warning("自动生成报告失败：%s", exc, exc_info=True)
            return {"results": results, "report": report_path, "report_error": report_error}

        self._run_task(job, self.results_page.log_panel, self._docking_done, self._docking_failed)

    def _docking_done(self, payload: dict) -> None:
        self._set_busy(False)
        workspace = self.workspace
        results = payload.get("results", [])
        success = [item for item in results if item.ok]
        failed = len(results) - len(success)
        message = f"对接完成：成功 {len(success)} 组，失败 {failed} 组。"
        self.results_page.finish_running(message, failed == 0)
        if workspace is not None:
            self.results_page.show_results(workspace, results)
            self._refresh_all()
            self.results_page.show_results(workspace, results)
        report_path = payload.get("report", "")
        if report_path:
            self.statusBar().showMessage(f"对接完成，报告已生成：{report_path}", 10000)
        else:
            self.statusBar().showMessage(message, 8000)
        if failed:
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
        if self.workspace is None or self._busy:
            return
        results = collect_existing_results(self.workspace)
        self.results_page.show_results(self.workspace, results)
        self.statusBar().showMessage(f"已刷新：{len(results)} 组结果。", 5000)

    def _make_report(self) -> None:
        if self.workspace is None or self._busy:
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

    def _open_workspace_folder(self) -> None:
        if self.workspace is None:
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
        self._vina = None
        self._obabel = None
        self._apply_settings_to_controls()
        self._update_environment()
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
        self._update_sidebar()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_sidebar()
        if busy:
            self.statusBar().showMessage("任务运行中…")

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
        for page in (self.prepare_page, self.results_page):
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
            "计算由 AutoDock Vina 完成；结构准备使用 RDKit、Meeko、PDBFixer 与 OpenBabel。<br><br>"
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
        super().closeEvent(event)
