"""对接细节对话框：3D 相互作用视图 + 接触列表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..services.interactions import (
    KIND_LABELS,
    Interaction,
    InteractionAnalysis,
    analyze_complex,
)
from . import theme
from .geometry import ScreenFittedDialog
from .interaction_view import NGL_AVAILABLE, NGL_UNAVAILABLE_REASON, InteractionView

KIND_COLORS = {
    "hbond": "#2a78d6",
    "hydrophobic": "#d97706",
    "vdw": "#7c8799",
}
KIND_ORDER = ("hbond", "hydrophobic", "vdw")


class InteractionDialog(ScreenFittedDialog):
    """显示一个对接复合物的氢键、疏水作用与范德华接触。"""

    def __init__(
        self,
        complex_path: str | Path,
        target_label: str,
        ligand_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("对接细节")
        self.setModal(True)
        self._view: InteractionView | None = None
        self._shutdown_done = False
        self._complex_path = Path(complex_path)
        self._analysis: InteractionAnalysis = analyze_complex(self._complex_path)
        self._pdb_text = ""
        try:
            self._pdb_text = self._complex_path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            self._pdb_text = ""
        self.fit_to_screen(
            preferred=QSize(1180, 820), minimum=QSize(900, 620)
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        eyebrow = QLabel("对接细节")
        eyebrow.setObjectName("Eyebrow")
        root.addWidget(eyebrow)

        title = QLabel(f"{target_label} × {ligand_label}")
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        subtitle = QLabel(
            self._analysis.summary
            + " · 基于配体周围 4.4 Å 内的重原子几何接触"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addLayout(self._build_view_panel(), 1)
        content.addWidget(self._build_side_panel(), 0)
        root.addLayout(content, 1)

        if NGL_AVAILABLE:
            try:
                self._view = InteractionView(self)
                self._view_holder_layout.addWidget(self._view)
                self._view.failed.connect(self._on_view_failed)
                self._view.set_data(self._analysis, self._pdb_text)
                self._view.setVisible(True)
            except Exception as exc:  # pragma: no cover - 运行环境差异
                self._view = None
                self._show_view_error(str(exc))
        else:
            self._show_view_error(NGL_UNAVAILABLE_REASON)

    # ------------------------------------------------------------------
    # 左侧：3D 视图
    # ------------------------------------------------------------------
    def _build_view_panel(self) -> QVBoxLayout:
        panel = QVBoxLayout()
        panel.setSpacing(9)

        frame = QFrame()
        frame.setObjectName("Card")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 6, 6, 6)
        frame_layout.setSpacing(0)

        self._view_holder = QWidget()
        self._view_holder_layout = QVBoxLayout(self._view_holder)
        self._view_holder_layout.setContentsMargins(0, 0, 0, 0)
        self._view_holder_layout.setSpacing(0)
        frame_layout.addWidget(self._view_holder)

        self._view_error = QLabel("")
        self._view_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view_error.setWordWrap(True)
        self._view_error.setObjectName("Muted")
        self._view_error.setMinimumHeight(360)
        self._view_error.setVisible(False)
        frame_layout.addWidget(self._view_error)
        panel.addWidget(frame, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self._checks: dict[str, QCheckBox] = {}
        for kind in KIND_ORDER:
            count = self._analysis.counts.get(kind, 0)
            check = QCheckBox(f"{KIND_LABELS[kind]} {count}")
            check.setToolTip(f"显示或隐藏{KIND_LABELS[kind]}")
            check.setChecked(count > 0)
            check.setEnabled(count > 0)
            check.toggled.connect(
                lambda checked, key=kind: self._toggle_kind(key, checked)
            )
            self._checks[kind] = check
            controls.addWidget(check)
        controls.addStretch(1)

        self._labels_check = QCheckBox("显示残基标签")
        self._labels_check.setChecked(True)
        self._labels_check.toggled.connect(self._toggle_labels)
        controls.addWidget(self._labels_check)

        reset = QPushButton("重置视角")
        reset.setObjectName("Small")
        reset.clicked.connect(self._reset_view)
        controls.addWidget(reset)
        panel.addLayout(controls)

        hint = QLabel("左键旋转 · 滚轮缩放 · 右键平移 · 双击视图重置视角")
        hint.setObjectName("Muted")
        panel.addWidget(hint)
        return panel

    def _show_view_error(self, message: str) -> None:
        if self._view is not None:
            self._view.setVisible(False)
        detail = (message or "未知原因").strip()
        self._view_error.setText(
            "当前环境无法显示 3D 相互作用视图。\n"
            f"{detail}\n\n"
            "仍可在右侧查看接触列表，或回到结果表使用 PyMOL 查看复合物。"
        )
        self._view_error.setVisible(True)
        self._labels_check.setEnabled(False)

    def _on_view_failed(self, message: str) -> None:
        if self._view is not None:
            self._view.setVisible(False)
            self._view.shutdown()
            self._view = None
        self._show_view_error(message)

    # ------------------------------------------------------------------
    # 右侧：接触列表
    # ------------------------------------------------------------------
    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setMinimumWidth(310)
        panel.setMaximumWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 15, 16, 15)
        layout.setSpacing(10)

        heading = QLabel("相互作用")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)

        total = len(self._analysis.interactions)
        if total:
            summary = (
                f"{total} 条 · 涉及 {self._analysis.total_residues} 个残基"
            )
        else:
            summary = "未检测到可展示的接触"
        summary_label = QLabel(summary)
        summary_label.setObjectName("Muted")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        counts = QHBoxLayout()
        counts.setSpacing(10)
        for kind in KIND_ORDER:
            count = self._analysis.counts.get(kind, 0)
            chip = QLabel(f"● {KIND_LABELS[kind]} {count}")
            chip.setStyleSheet(
                f"color: {KIND_COLORS[kind]}; font-size: {theme.px(12)};"
            )
            counts.addWidget(chip)
        counts.addStretch(1)
        layout.addLayout(counts)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {theme.BORDER};")
        layout.addWidget(separator)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        body = QWidget()
        body.setObjectName("InteractionList")
        body.setStyleSheet("QWidget#InteractionList { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 2, 0)
        body_layout.setSpacing(7)
        if self._analysis.interactions:
            for interaction in self._analysis.interactions:
                body_layout.addWidget(self._interaction_row(interaction))
        else:
            empty = QLabel(
                "配体周围 4.4 Å 内没有可展示的蛋白残基接触。\n"
                "请检查对接构象或盒子位置，必要时重新对接。"
            )
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            body_layout.addWidget(empty)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        for warning in self._analysis.warnings:
            warning_label = QLabel(warning)
            warning_label.setObjectName("Warning")
            warning_label.setWordWrap(True)
            layout.addWidget(warning_label)

        caveat = QLabel(
            "判定方式：氢键按 N/O/S 重原子距离 ≤ 3.6 Å 判定，"
            "疏水作用与范德华接触按元素和距离近似。"
            "未计算氢键 D–H…A 角度，结果适合快速复核，"
            "正式分析建议使用 PLIP 等专门工具。"
        )
        caveat.setObjectName("Muted")
        caveat.setWordWrap(True)
        layout.addWidget(caveat)

        close_button = QPushButton("关闭")
        close_button.setObjectName("Primary")
        close_button.clicked.connect(self._close)
        layout.addWidget(close_button)
        return panel

    def _interaction_row(self, interaction: Interaction) -> QWidget:
        row = QFrame()
        row.setObjectName("InteractionRow")
        row.setStyleSheet(
            "QFrame#InteractionRow {"
            " background: #fbfbf9; border: 1px solid #ecebe5;"
            " border-radius: 8px; }"
        )
        row.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(9)

        bar = QLabel()
        bar.setFixedSize(4, 30)
        bar.setStyleSheet(
            f"background: {KIND_COLORS[interaction.kind]}; border-radius: 2px;"
        )
        layout.addWidget(bar)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        residue = QLabel(interaction.residue_label)
        residue.setStyleSheet(
            f"font-weight: 700; font-size: {theme.px(13)};"
        )
        detail = QLabel(
            f"{interaction.kind_label} · "
            f"{interaction.protein_atom}–{interaction.ligand_atom}"
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        text_box.addWidget(residue)
        text_box.addWidget(detail)
        layout.addLayout(text_box, 1)

        distance = QLabel(f"{interaction.distance:.2f} Å")
        distance.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        distance.setStyleSheet(
            "font-variant-numeric: tabular-nums; font-weight: 600;"
        )
        layout.addWidget(distance)
        return row

    # ------------------------------------------------------------------
    # 控件行为
    # ------------------------------------------------------------------
    def _toggle_kind(self, kind: str, checked: bool) -> None:
        if self._view is not None:
            self._view.set_kind_visible(kind, checked)

    def _toggle_labels(self, checked: bool) -> None:
        if self._view is not None:
            self._view.set_labels_visible(checked)

    def _reset_view(self) -> None:
        if self._view is not None:
            self._view.reset_view()

    def _close(self) -> None:
        self._shutdown_view()
        self.accept()

    def _shutdown_view(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if self._view is not None:
            self._view.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._shutdown_view()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # noqa: N802
        self._shutdown_view()
        super().done(result)
