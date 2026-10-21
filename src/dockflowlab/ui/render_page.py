"""PyMOL 渲染页面：选择复合物与风格脚本，在独立进程中出图。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ..core.models import KIND_LIGAND, KIND_TARGET, DockingResult
from ..services.pymol_render import complexes_in, style_token, styles_in
from ..workspace import Workspace
from .pages import Page, _header_row
from .widgets import Card, LogPanel

SIZE_PRESETS = (
    (900, "900 × 900 · 快速预览"),
    (1200, "1200 × 1200 · 标准（推荐）"),
    (1800, "1800 × 1800 · 高清"),
    (2400, "2400 × 2400 · 打印（较慢）"),
)

# PyMOL 标签按固定像素绘制；900 像素为基准，放大倍数会再随分辨率补偿。
LABEL_PRESETS = (
    (1.0, "标准 · 随分辨率缩放"),
    (1.5, "大 · 推荐"),
    (2.0, "特大"),
    (3.0, "超大"),
)


class ImagePreview(QLabel):
    """按可用空间等比缩放的预览图，避免大图把页面撑开。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ImagePreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(320)
        self.setMaximumHeight(560)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._source: QPixmap | None = None
        self.setText("渲染结果会显示在这里")

    def set_image(self, path: str | Path) -> bool:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._source = pixmap
        self._rescale()
        return True

    def clear_image(self) -> None:
        self._source = None
        self.clear()
        self.setText("渲染结果会显示在这里")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None:
            return
        target = self.size()
        target.setWidth(max(120, target.width() - 8))
        target.setHeight(max(120, target.height() - 8))
        self.setPixmap(
            self._source.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class RenderPage(Page):
    render_requested = Signal(dict)
    cancel_requested = Signal()
    refresh_requested = Signal()
    open_image_requested = Signal(str)
    locate_image_requested = Signal(str)
    script_dir_changed = Signal(str)
    render_size_changed = Signal(int)
    label_scale_changed = Signal(float)
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._workspace: Workspace | None = None
        self._results: list[DockingResult] = []
        self._folder = Path()
        self._renderer = ""
        self._busy = False
        self._last_image = ""

        refresh = QPushButton("刷新复合物")
        refresh.clicked.connect(self.refresh_requested.emit)
        self.body.addWidget(
            _header_row(
                "附加功能 · PyMOL 渲染",
                "用你自己的风格脚本出图",
                "选择对接后的复合物和一组 .pml / .txt 风格脚本，程序会在独立的 "
                "PyMOL 进程中渲染 PNG。渲染失败或卡住不会影响主程序。",
                [refresh],
            )
        )

        self.env_label = QLabel("正在检查 PyMOL…")
        self.env_label.setObjectName("CardHint")
        self.env_label.setWordWrap(True)
        self.env_label.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(self.env_label)

        self.body.addWidget(self._build_complex_card())
        self.body.addWidget(self._build_style_card())
        self.body.addWidget(self._build_output_card())
        self.body.addWidget(self._build_preview_card())

        self.log_panel = LogPanel()
        self.log_panel.setVisible(False)
        self.log_panel.cancel_requested.connect(self.cancel_requested.emit)
        self.body.addWidget(self.log_panel)

        nav = QHBoxLayout()
        back = QPushButton("返回结果")
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        nav.addStretch(1)
        self.render_button = QPushButton("渲染所选脚本")
        self.render_button.setObjectName("Primary")
        self.render_button.clicked.connect(self._emit_render)
        nav.addWidget(self.render_button)
        self.body.addLayout(nav)
        self.body.addStretch(1)
        self._update_controls()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build_complex_card(self) -> Card:
        card = Card(
            "1. 选择复合物",
            "列出项目 complexes/ 目录中的对接复合物；分数来自已有结果。",
        )
        self.complex_combo = QComboBox()
        self.complex_combo.setMinimumWidth(420)
        self.complex_combo.currentIndexChanged.connect(self._update_controls)
        card.add(self.complex_combo)
        self.complex_hint = QLabel("还没有打开工作目录。")
        self.complex_hint.setObjectName("Muted")
        self.complex_hint.setWordWrap(True)
        card.add(self.complex_hint)
        return card

    def _build_style_card(self) -> Card:
        card = Card(
            "2. 选择风格脚本",
            "支持 .pml 与 .txt，会递归扫描子文件夹。脚本应只包含样式命令；"
            "加载结构、设置视角、渲染与保存图片由程序自动完成。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("选择存放 PyMOL 风格脚本的文件夹")
        self.folder_edit.editingFinished.connect(self._apply_folder_from_edit)
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_folder)
        row.addWidget(browse)
        scan = QPushButton("重新扫描")
        scan.clicked.connect(self._apply_folder_from_edit)
        row.addWidget(scan)
        card.add_layout(row)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        select_all = QPushButton("全选")
        select_all.setObjectName("Small")
        select_all.clicked.connect(self._select_all)
        clear = QPushButton("清空选择")
        clear.setObjectName("Small")
        clear.clicked.connect(self._clear_selection)
        tools.addWidget(select_all)
        tools.addWidget(clear)
        tools.addStretch(1)
        self.script_count = QLabel("0 个脚本")
        self.script_count.setObjectName("Muted")
        tools.addWidget(self.script_count)
        card.add_layout(tools)

        self.script_list = QListWidget()
        self.script_list.setObjectName("StyleList")
        self.script_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self.script_list.setMinimumHeight(170)
        self.script_list.setMaximumHeight(260)
        self.script_list.itemSelectionChanged.connect(self._update_controls)
        card.add(self.script_list)

        hint = QLabel(
            "可以一次选中多个脚本顺序渲染。中文路径和中文注释会在内部转码，"
            "不需要手动改成英文。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        card.add(hint)
        return card

    def _build_output_card(self) -> Card:
        card = Card("3. 输出设置", "图片保存在项目的 pymol/renders/ 目录。")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("分辨率"))
        self.size_combo = QComboBox()
        for size, label in SIZE_PRESETS:
            self.size_combo.addItem(label, size)
        self.size_combo.setMinimumWidth(280)
        self.size_combo.currentIndexChanged.connect(self._on_size_changed)
        row.addWidget(self.size_combo)
        row.addStretch(1)
        card.add_layout(row)

        label_row = QHBoxLayout()
        label_row.setSpacing(10)
        label_row.addWidget(QLabel("标签字号"))
        self.label_combo = QComboBox()
        for scale, label in LABEL_PRESETS:
            self.label_combo.addItem(label, scale)
        self.label_combo.setCurrentIndex(self.label_combo.findData(1.5))
        self.label_combo.setMinimumWidth(280)
        self.label_combo.currentIndexChanged.connect(self._on_label_scale_changed)
        label_row.addWidget(self.label_combo)
        label_row.addStretch(1)
        card.add_layout(label_row)

        note = QLabel(
            "PyMOL 的标签按固定像素绘制，分辨率越高相对越小；程序会按 900 像素"
            "基准自动补偿。残基编号和氢键距离都使用同一倍数。\n"
            "无界面渲染始终使用光线追踪：900–1200 像素通常每张 1–3 秒；"
            "带透明表面的脚本或 2400 像素可能需几十秒。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        card.add(note)
        return card

    def _build_preview_card(self) -> Card:
        card = Card("预览")
        self.preview = ImagePreview()
        card.add(self.preview)
        self.result_combo = QComboBox()
        self.result_combo.setMinimumWidth(360)
        self.result_combo.setVisible(False)
        self.result_combo.currentIndexChanged.connect(self._show_result)
        card.add(self.result_combo)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.open_button = QPushButton("打开图片")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(
            lambda: self._emit_image_path(self.open_image_requested)
        )
        self.locate_button = QPushButton("在文件夹中显示")
        self.locate_button.setEnabled(False)
        self.locate_button.clicked.connect(
            lambda: self._emit_image_path(self.locate_image_requested)
        )
        row.addWidget(self.open_button)
        row.addWidget(self.locate_button)
        row.addStretch(1)
        self.preview_caption = QLabel("尚未渲染。")
        self.preview_caption.setObjectName("Muted")
        self.preview_caption.setWordWrap(True)
        row.addWidget(self.preview_caption, 1)
        card.add_layout(row)
        return card

    # ------------------------------------------------------------------
    # 外部状态
    # ------------------------------------------------------------------
    def set_workspace(
        self,
        workspace: Workspace | None,
        results: list[DockingResult] | None = None,
    ) -> None:
        self._workspace = workspace
        self._results = results or []
        self.refresh_complexes()

    def refresh_complexes(self) -> None:
        previous = self.current_complex()
        self.complex_combo.blockSignals(True)
        self.complex_combo.clear()
        workspace = self._workspace
        if workspace is None:
            self.complex_combo.blockSignals(False)
            self.complex_hint.setText("还没有打开工作目录。")
            self._update_controls()
            return

        scores = {
            (item.target, item.ligand): item.best_affinity
            for item in self._results
            if item.best_affinity is not None
        }
        items = complexes_in(workspace.root)
        for item in items:
            target = workspace.get(KIND_TARGET, item.target)
            ligand = workspace.get(KIND_LIGAND, item.ligand)
            target_name = target.display_name if target else item.target
            ligand_name = ligand.display_name if ligand else item.ligand
            label = f"{target_name} + {ligand_name}"
            score = scores.get((item.target, item.ligand))
            if score is not None:
                label += f" · {score:.2f} kcal/mol"
            self.complex_combo.addItem(label, (item.target, item.ligand, str(item.path)))

        if previous:
            for index in range(self.complex_combo.count()):
                data = self.complex_combo.itemData(index)
                if data and data[0] == previous[0] and data[1] == previous[1]:
                    self.complex_combo.setCurrentIndex(index)
                    break
        self.complex_combo.blockSignals(False)
        self.complex_hint.setText(
            f"找到 {len(items)} 个复合物。"
            if items
            else "还没有复合物。请先在“运行与结果”中完成对接，生成靶点–配体复合物。"
        )
        self._update_controls()

    def set_renderer(self, renderer: str) -> None:
        self._renderer = renderer
        if renderer:
            self.env_label.setText(
                "<span style='color:#1f6b3a'>●</span> PyMOL 已就绪："
                f"<span style='color:#52514e'>{renderer}</span>"
            )
            self.env_label.setToolTip(renderer)
        else:
            self.env_label.setText(
                "<span style='color:#b3261e'>●</span> 未检测到 PyMOL。"
                "可在“设置与环境检查”中指定 pymol 可执行文件；"
                "渲染功能不可用，但对接与结果不受影响。"
            )
        self._update_controls()

    def set_script_dir(self, folder: str | Path) -> None:
        text = str(folder) if folder else ""
        self.folder_edit.setText(text)
        self._folder = Path(text).expanduser() if text else Path()
        self._populate_scripts()

    def set_render_size(self, size: int) -> None:
        index = self.size_combo.findData(int(size))
        self.size_combo.setCurrentIndex(index if index >= 0 else 1)

    def current_size(self) -> int:
        try:
            return int(self.size_combo.currentData() or 1200)
        except (TypeError, ValueError):
            return 1200

    def set_label_scale(self, scale: float) -> None:
        index = self.label_combo.findData(float(scale))
        if index < 0:
            closest = min(LABEL_PRESETS, key=lambda item: abs(item[0] - float(scale)))
            index = self.label_combo.findData(closest[0])
        self.label_combo.setCurrentIndex(index if index >= 0 else 1)

    def current_label_scale(self) -> float:
        try:
            return float(self.label_combo.currentData() or 1.5)
        except (TypeError, ValueError):
            return 1.5

    def current_complex(self) -> tuple[str, str, str] | None:
        data = self.complex_combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 3:
            return str(data[0]), str(data[1]), str(data[2])
        return None

    # ------------------------------------------------------------------
    # 脚本列表
    # ------------------------------------------------------------------
    def _browse_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择风格脚本文件夹", start)
        if not folder:
            return
        self.set_script_dir(folder)
        self.script_dir_changed.emit(folder)

    def _apply_folder_from_edit(self) -> None:
        text = self.folder_edit.text().strip()
        self._folder = Path(text).expanduser() if text else Path()
        self._populate_scripts()
        if text and self._folder.is_dir():
            self.script_dir_changed.emit(str(self._folder))

    def _populate_scripts(self) -> None:
        self.script_list.clear()
        folder = self._folder
        styles = styles_in(folder) if str(folder) else []
        for path in styles:
            try:
                label = str(path.relative_to(folder))
            except ValueError:
                label = path.name
            item = QListWidgetItem(label.replace("\\", "/"))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.script_list.addItem(item)
        if styles:
            self.script_list.item(0).setSelected(True)
            self.script_count.setText(f"{len(styles)} 个脚本")
        elif str(folder):
            self.script_count.setText("0 个脚本（文件夹不存在或没有 .pml/.txt）")
        else:
            self.script_count.setText("0 个脚本")
        self._update_controls()

    def _select_all(self) -> None:
        if self.script_list.count():
            self.script_list.selectAll()

    def _clear_selection(self) -> None:
        self.script_list.clearSelection()
        self._update_controls()

    def selected_scripts(self) -> list[Path]:
        """按列表顺序返回选中脚本，而非用户点选的先后顺序。"""
        rows = sorted(self.script_list.row(item) for item in self.script_list.selectedItems())
        paths = []
        for row in rows:
            value = self.script_list.item(row).data(Qt.ItemDataRole.UserRole)
            if value:
                paths.append(Path(str(value)))
        return paths

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _emit_render(self) -> None:
        workspace = self._workspace
        if workspace is None:
            QMessageBox.information(self, "还没有打开项目", "请先打开一个工作目录。")
            return
        if not self._renderer:
            QMessageBox.warning(
                self,
                "未检测到 PyMOL",
                "渲染需要安装 PyMOL。请在“设置与环境检查”中指定 pymol 可执行文件后重试。",
            )
            return
        complex_data = self.current_complex()
        if complex_data is None:
            QMessageBox.information(
                self,
                "没有可渲染的复合物",
                "请先在“运行与结果”中完成对接，生成靶点–配体复合物。",
            )
            return
        scripts = self.selected_scripts()
        if not scripts:
            QMessageBox.information(self, "没有选择脚本", "请在脚本列表中选择至少一个风格脚本。")
            return
        target, ligand, complex_path = complex_data
        size = self.current_size()
        items = []
        for script in scripts:
            token = style_token(script, self._folder)
            output = workspace.render_path(target, ligand, token)
            try:
                label = str(script.relative_to(self._folder))
            except ValueError:
                label = script.name
            items.append(
                {
                    "path": str(script),
                    "label": label.replace("\\", "/"),
                    "output": str(output),
                }
            )
        label_scale = self.current_label_scale()
        self.render_size_changed.emit(size)
        self.render_requested.emit(
            {
                "complex_path": complex_path,
                "target": target,
                "ligand": ligand,
                "width": size,
                "height": size,
                "label_scale": label_scale,
                "scripts": items,
            }
        )

    def start_running(self, total: int) -> None:
        self._busy = True
        self.result_combo.clear()
        self.result_combo.setVisible(False)
        self.log_panel.setVisible(True)
        self.log_panel.start(f"正在渲染 {total} 张图片…")
        self._update_controls()

    def finish(self, results: list[dict]) -> None:
        self._busy = False
        success = [
            item for item in results
            if item.get("result") is not None and item["result"].ok
        ]
        failures = len(results) - len(success)
        if results and not failures:
            message = f"渲染完成：{len(success)} 张。"
            ok = True
        elif success:
            message = f"渲染结束：成功 {len(success)} 张，失败 {failures} 张。"
            ok = False
        elif results:
            message = "渲染失败：没有生成图片。"
            ok = False
        else:
            message = "没有执行渲染。"
            ok = False
        self.log_panel.finish(message, ok)
        self._update_controls()

        self.result_combo.blockSignals(True)
        self.result_combo.clear()
        for item in success:
            result = item["result"]
            if result.image_path:
                self.result_combo.addItem(
                    f"{item.get('label', '')} · {result.elapsed:.1f} 秒",
                    str(result.image_path),
                )
        self.result_combo.blockSignals(False)
        self.result_combo.setVisible(self.result_combo.count() > 1)
        if self.result_combo.count():
            self.result_combo.setCurrentIndex(0)
            self._show_result(0)
        if failures:
            errors = [
                f"{item.get('label', '')}：{item['result'].error}"
                for item in results
                if item.get("result") is not None and not item["result"].ok
            ]
            if errors:
                if success:
                    self.preview_caption.setText(
                        self.preview_caption.text()
                        + f"（另有 {len(errors)} 张失败，见运行日志）"
                    )
                else:
                    self.preview_caption.setText(errors[0][:180])

    def _show_result(self, index: int) -> None:
        if index < 0:
            return
        path = self.result_combo.itemData(index)
        if not path:
            return
        if self.preview.set_image(str(path)):
            self._set_image_path(str(path))
            self.preview_caption.setText(self.result_combo.itemText(index))

    def _set_image_path(self, path: str) -> None:
        self._last_image = path
        self.open_button.setEnabled(bool(path))
        self.locate_button.setEnabled(bool(path))

    def _emit_image_path(self, signal) -> None:
        if self._last_image:
            signal.emit(self._last_image)

    def _on_size_changed(self) -> None:
        self.render_size_changed.emit(self.current_size())

    def _on_label_scale_changed(self) -> None:
        self.label_scale_changed.emit(self.current_label_scale())

    def _update_controls(self) -> None:
        has_renderer = bool(self._renderer)
        has_complex = self.current_complex() is not None
        has_scripts = bool(self.selected_scripts())
        enabled = not self._busy and has_renderer and has_complex and has_scripts
        self.render_button.setEnabled(enabled)
        for widget in (
            self.complex_combo,
            self.folder_edit,
            self.script_list,
            self.size_combo,
            self.label_combo,
            self.result_combo,
        ):
            widget.setEnabled(not self._busy)
        if not has_renderer:
            self.render_button.setToolTip("未检测到 PyMOL")
        elif not has_complex:
            self.render_button.setToolTip("请先完成对接并选择复合物")
        elif not has_scripts:
            self.render_button.setToolTip("请选择至少一个风格脚本")
        else:
            self.render_button.setToolTip("")
