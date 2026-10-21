"""热图编辑器：修改显示名称、标题、配色与导出格式。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import DockingResult
from ..services.analysis import (
    DEFAULT_HEATMAP_TITLE,
    HEATMAP_FORMATS,
    HEATMAP_SCHEMES,
    HeatmapConfig,
    configure_chinese_font,
    heatmap_data,
    load_heatmap_config,
    plot_heatmap,
)
from ..workspace import Workspace


def _scheme_icon(steps: list[str]) -> QIcon:
    pixmap = QPixmap(48, 14)
    painter = QPainter(pixmap)
    gradient = QLinearGradient(0, 0, pixmap.width(), 0)
    gradient.setColorAt(0.0, QColor(steps[0]))
    gradient.setColorAt(0.5, QColor(steps[len(steps) // 2]))
    gradient.setColorAt(1.0, QColor(steps[-1]))
    painter.fillRect(pixmap.rect(), gradient)
    painter.end()
    return QIcon(pixmap)


class _Preview(QLabel):
    """按控件宽度缩放预览图，避免大图撑开对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeatmapPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(240)
        self.setScaledContents(False)
        self.setText("正在生成预览…")
        self._source: QPixmap | None = None

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._rescale()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None:
            return
        width = max(320, self.width() - 12)
        height = max(180, self.height() - 12)
        self.setPixmap(
            self._source.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class HeatmapDialog(QDialog):
    """编辑热图文字与外观；接受后由主窗口写入项目设置。"""

    def __init__(
        self,
        workspace: Workspace,
        results: list[DockingResult] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑热图")
        self.resize(1000, 780)
        self._workspace = workspace
        self._config = load_heatmap_config(workspace)
        self._matrix, self._default_targets, self._default_ligands = heatmap_data(
            workspace, results
        )
        self._target_stems = [str(stem) for stem in self._matrix.index]
        self._ligand_stems = [str(stem) for stem in self._matrix.columns]
        self.result_config: HeatmapConfig | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "直接修改第三列即可改变图中显示的名称；双击单元格编辑。"
            "留空会恢复项目里的默认名称。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(
            len(self._target_stems) + len(self._ligand_stems), 3
        )
        self.table.setHorizontalHeaderLabels(
            ["类型", "内部代号", "图表显示名称（可修改）"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setMinimumHeight(160)
        self.table.setMaximumHeight(260)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._fill_table()
        layout.addWidget(self.table, 0)

        form = QFormLayout()
        form.setSpacing(8)
        self.title_edit = QLineEdit(self._config.title or DEFAULT_HEATMAP_TITLE)
        self.title_edit.setPlaceholderText(DEFAULT_HEATMAP_TITLE)
        form.addRow("标题", self.title_edit)

        self.subtitle_edit = QLineEdit(self._config.subtitle)
        self.subtitle_edit.setPlaceholderText(
            "留空则自动显示数值含义、单位与有效组合数"
        )
        form.addRow("副标题", self.subtitle_edit)

        self.scheme_combo = QComboBox()
        for key, (label, steps) in HEATMAP_SCHEMES.items():
            self.scheme_combo.addItem(_scheme_icon(steps), label, key)
        index = self.scheme_combo.findData(self._config.scheme)
        self.scheme_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("配色", self.scheme_combo)

        self.format_combo = QComboBox()
        for key, (label, _) in HEATMAP_FORMATS.items():
            self.format_combo.addItem(label, key)
        index = self.format_combo.findData(self._config.format)
        self.format_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("输出格式", self.format_combo)
        layout.addLayout(form)

        actions = QHBoxLayout()
        reset = QPushButton("恢复默认名称")
        reset.clicked.connect(self._reset_labels)
        preview = QPushButton("更新预览")
        preview.clicked.connect(self._update_preview)
        actions.addWidget(reset)
        actions.addStretch(1)
        actions.addWidget(preview)
        layout.addLayout(actions)

        self.preview = _Preview()
        layout.addWidget(self.preview, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("应用并生成")
        apply.setObjectName("Primary")
        apply.clicked.connect(self._apply)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._preview_timer.start(60)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _default_label(self, kind: str, stem: str) -> str:
        mapping = (
            self._default_targets if kind == "靶点" else self._default_ligands
        )
        return mapping.get(stem, stem)

    def _configured_label(self, kind: str, stem: str) -> str:
        mapping = (
            self._config.target_labels
            if kind == "靶点"
            else self._config.ligand_labels
        )
        return mapping.get(stem) or self._default_label(kind, stem)

    def _fill_table(self) -> None:
        self.table.setRowCount(0)
        rows: list[tuple[str, str]] = [
            ("靶点", stem) for stem in self._target_stems
        ] + [("候选分子", stem) for stem in self._ligand_stems]
        self.table.setRowCount(len(rows))
        for row, (kind, stem) in enumerate(rows):
            type_item = QTableWidgetItem(kind)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            code_item = QTableWidgetItem(stem)
            code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            code_item.setToolTip(f"项目内部代号：{stem}")
            name_item = QTableWidgetItem(self._configured_label(kind, stem))
            name_item.setToolTip(
                f"默认名称：{self._default_label(kind, stem)}"
            )
            self.table.setItem(row, 0, type_item)
            self.table.setItem(row, 1, code_item)
            self.table.setItem(row, 2, name_item)
        self.table.resizeRowsToContents()

    def _reset_labels(self) -> None:
        for row, (kind, stem) in enumerate(
            [("靶点", stem) for stem in self._target_stems]
            + [("候选分子", stem) for stem in self._ligand_stems]
        ):
            item = self.table.item(row, 2)
            if item is not None:
                item.setText(self._default_label(kind, stem))

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    def _labels_and_matrix(self) -> tuple[dict[str, str], dict[str, str], Any]:
        target_labels: dict[str, str] = {}
        ligand_labels: dict[str, str] = {}
        seen: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            kind = self.table.item(row, 0).text()
            stem = self.table.item(row, 1).text()
            raw = (self.table.item(row, 2).text() or "").strip()
            label = raw or self._default_label(kind, stem)
            duplicate_key = f"{kind}:{label}"
            if duplicate_key in seen:
                raise ValueError(
                    f"{kind}名称“{label}”重复（{seen[duplicate_key]} 与 {stem}）。"
                    "请改成不同的名称。"
                )
            seen[duplicate_key] = stem
            if kind == "靶点":
                target_labels[stem] = label
            else:
                ligand_labels[stem] = label
        matrix = self._matrix.rename(
            index=target_labels, columns=ligand_labels
        )
        return target_labels, ligand_labels, matrix

    def _collect_config(self) -> tuple[HeatmapConfig, Any]:
        target_labels, ligand_labels, matrix = self._labels_and_matrix()
        title = self.title_edit.text().strip() or DEFAULT_HEATMAP_TITLE
        subtitle = self.subtitle_edit.text().strip()
        scheme = str(self.scheme_combo.currentData() or "blue")
        fmt = str(self.format_combo.currentData() or "png")
        # 只保存与默认名称不同的项，项目改名后其余标签仍会自动跟随。
        config = HeatmapConfig(
            target_labels={
                stem: label
                for stem, label in target_labels.items()
                if label != self._default_label("靶点", stem)
            },
            ligand_labels={
                stem: label
                for stem, label in ligand_labels.items()
                if label != self._default_label("候选分子", stem)
            },
            scheme=scheme,
            title=title,
            subtitle=subtitle,
            format=fmt,
        )
        return config, matrix

    def _update_preview(self) -> None:
        try:
            config, matrix = self._collect_config()
        except ValueError as exc:
            QMessageBox.warning(self, "名称有误", str(exc))
            return
        try:
            configure_chinese_font()
            with tempfile.TemporaryDirectory(prefix="dockflow_heatmap_") as folder:
                path = Path(folder) / "preview.png"
                plot_heatmap(
                    matrix,
                    [path],
                    scheme=config.scheme,
                    title=config.title,
                    subtitle=config.subtitle,
                )
                pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise ValueError("无法读取预览图")
            self.preview.set_source(pixmap)
        except Exception as exc:  # 预览失败不应关闭对话框
            self.preview.setText(f"预览失败：{exc}")

    def _apply(self) -> None:
        try:
            config, _ = self._collect_config()
        except ValueError as exc:
            QMessageBox.warning(self, "名称有误", str(exc))
            return
        self.result_config = config
        self.accept()
