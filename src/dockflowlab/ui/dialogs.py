"""下载、盒子、设置等对话框。"""

from __future__ import annotations

import html
import importlib.util
import math
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppSettings
from ..core import pdbio
from ..core.models import KIND_LIGAND, KIND_TARGET, Box, DownloadResult, ProjectFile
from ..errors import DockFlowError
from ..logging_setup import get_logger
from ..services import modes
from ..services.boxes import (
    known_site_highlights,
    ligand_required_sizes,
    suggest_box,
    validate_manual_box,
)
from ..services.download import DownloadClient
from ..services.pymol_render import find_renderer
from ..tools.obabel import OpenBabelAdapter
from ..tools.vina import (
    ENGINE_DESCRIPTIONS,
    ENGINE_LABELS,
    ENGINE_ORDER,
    ENGINE_VINA,
    engine_path_setting,
    make_engine,
    set_engine_path,
)
from ..workspace import Workspace
from . import theme
from .box_view import COCRYSTAL_COLOR, SITE_COLOR, BoxViewer, atoms_to_pdb_text
from .geometry import ScreenFittedDialog, available_geometry
from .widgets import Card, StatusChip
from .workers import Task

log = get_logger("box_dialog")


def _plain_number(spin: QAbstractSpinBox) -> QAbstractSpinBox:
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return spin


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} MB"


class DownloadDialog(QDialog):
    SOURCES = {
        KIND_TARGET: [
            ("RCSB PDB（实验结构）", "pdb", "输入 4 位 PDB ID，例如 1BCI、6LU7", "1BCI"),
            ("AlphaFold（预测结构）", "alphafold", "输入 UniProt 登录号，例如 P00533", "P00533"),
        ],
        KIND_LIGAND: [
            ("PubChem（小分子）", "pubchem", "支持名称、CID 或 SMILES，例如 aspirin、2244", "aspirin"),
            ("ChEMBL（药物分子）", "chembl", "支持 ChEMBL ID 或名称，例如 CHEMBL25", "CHEMBL25"),
        ],
    }

    def __init__(self, kind: str, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.settings = settings
        self.result: DownloadResult | None = None
        self._task: Task | None = None
        self._cancel_requested = False
        self._closing = False
        noun = "靶点结构" if kind == KIND_TARGET else "候选分子"
        self.setWindowTitle(f"下载{noun}")
        self.setMinimumWidth(560)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        title = QLabel(f"从公共数据库获取{noun}")
        title.setStyleSheet(f"font-size: {theme.px(20)}; font-weight: 700;")
        hint = QLabel("下载的文件会保存到当前工作目录，并自动加入项目。")
        hint.setObjectName("CardHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        self.source_combo = QComboBox()
        for label, data, _, _ in self.SOURCES[kind]:
            self.source_combo.addItem(label, data)
        self.query_edit = QLineEdit()
        self.query_edit.returnPressed.connect(self._primary_clicked)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("Muted")
        self.hint_label.setWordWrap(True)
        form.addRow("来源", self.source_combo)
        form.addRow("查询", self.query_edit)
        form.addRow("", self.hint_label)
        layout.addLayout(form)
        self.source_combo.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

        proxy_note = QLabel(
            "如果下载失败，请检查网络；需要代理时可在“设置”中填写代理地址（例如 127.0.0.1:7897）。"
        )
        proxy_note.setObjectName("Muted")
        proxy_note.setWordWrap(True)
        layout.addWidget(proxy_note)

        self.preview = QFrame()
        self.preview.setObjectName("Tile")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_layout.setSpacing(4)
        self.preview_title = QLabel("")
        self.preview_title.setStyleSheet("font-weight: 600;")
        self.preview_detail = QLabel("")
        self.preview_detail.setObjectName("CardHint")
        self.preview_detail.setWordWrap(True)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_detail)
        self.preview.setVisible(False)
        layout.addWidget(self.preview)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        # 网络请求期间显示不确定进度条，明确告诉用户程序仍在工作。
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.primary_button = QPushButton("下载")
        self.primary_button.setObjectName("Primary")
        self.primary_button.clicked.connect(self._primary_clicked)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.primary_button)
        layout.addLayout(buttons)
        self.query_edit.setFocus()
        self._fit()

    def _fit(self) -> None:
        self.layout().invalidate()
        self.layout().activate()
        hint = self.sizeHint()
        self.resize(max(self.minimumWidth(), hint.width()), hint.height())

    def _update_hint(self) -> None:
        index = self.source_combo.currentIndex()
        _, _, hint, example = self.SOURCES[self.kind][index]
        self.hint_label.setText(f"{hint}。")
        self.query_edit.setPlaceholderText(example)

    def _primary_clicked(self) -> None:
        if self.result is not None:
            self.accept()
            return
        query = self.query_edit.text().strip()
        if not query:
            self.status.setText("请先输入要查询的内容。")
            return
        source = str(self.source_combo.currentData())
        client = DownloadClient(self.settings)
        download = {
            "pubchem": client.pubchem,
            "pdb": client.pdb,
            "alphafold": client.alphafold,
            "chembl": client.chembl,
        }[source]
        self.primary_button.setEnabled(False)
        # 取消按钮保持可用：网络卡住时用户至少能发出取消请求并得到反馈。
        self.cancel_button.setEnabled(True)
        self._cancel_requested = False
        self._closing = False
        self.status.setText("正在连接数据库并下载…")
        self.preview.setVisible(False)
        self.progress.setVisible(True)
        self._task = Task(
            lambda progress, cancel, line: download(query, cancel=cancel), self
        )
        self._task.done.connect(self._download_done)
        self._task.failed.connect(self._download_failed)
        self._task.finished.connect(self._task_finished)
        self._task.start()

    def _task_finished(self) -> None:
        self.progress.setVisible(False)
        if self._closing:
            return  # finished 已连接到 reject，窗口随后关闭
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def _download_done(self, result: DownloadResult) -> None:
        if self._cancel_requested:
            return
        self.result = result
        self.status.setText("下载完成。确认无误后点击“添加到项目”。")
        self.preview_title.setText(result.display_name)
        details = [f"文件：{result.filename} · {_format_size(len(result.data))}"]
        if result.meta.get("formula"):
            details.append(f"分子式：{result.meta['formula']}")
        if result.meta.get("resolution"):
            details.append(f"分辨率：{result.meta['resolution']} Å")
        if result.meta.get("source"):
            details.append(f"来源：{result.meta['source']}")
        warnings = result.meta.get("warnings") or []
        if warnings:
            details.append("注意：" + "；".join(str(item) for item in warnings))
        self.preview_detail.setText("\n".join(details))
        self.preview.setVisible(True)
        self.primary_button.setText("添加到项目")
        self._fit()

    def _download_failed(self, message: str) -> None:
        if self._cancel_requested or message.strip() == "已取消下载。":
            self.status.setText("已取消下载。")
            self.preview.setVisible(False)
            return
        self.status.setText(f"下载失败：{message}")
        self.preview.setVisible(False)

    def _request_cancel(self) -> None:
        """请求取消并给出明确提示；已发出的请求会在返回后自动关闭窗口。"""
        self._cancel_requested = True
        if self._task is not None:
            self._task.request_cancel()
        self.status.setText("正在取消下载，当前请求返回后窗口会自动关闭…")
        self.primary_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    def _cancel_and_close(self) -> None:
        self._request_cancel()
        if self._task is not None and not self._closing:
            # 后台线程还没结束，等它退出后再关闭，避免销毁仍在运行的页面。
            self._closing = True
            self._task.finished.connect(self.reject)

    def reject(self) -> None:
        if self._task is not None and self._task.isRunning():
            self._cancel_and_close()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._task is not None and self._task.isRunning():
            self._cancel_and_close()
            event.ignore()
            return
        super().closeEvent(event)


class BoxDialog(ScreenFittedDialog):
    """3D 盒子编辑器：自动推荐为默认，拖动或输入可手动微调。"""

    def __init__(
        self,
        workspace: Workspace,
        target: ProjectFile,
        settings: AppSettings,
        *,
        obabel: OpenBabelAdapter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.target = target
        self.settings = settings
        self.obabel = obabel
        self.box: Box | None = workspace.load_box(target.stem)
        self.auto_box: Box | None = None
        self.auto_warnings: list[str] = []
        self._required_size: tuple[float, float, float] | None = None
        self._auto_pending = False
        self._content_loaded = False
        self.setWindowTitle(f"设置盒子 · {target.display_name}")
        self.setModal(True)
        # 竖屏或小尺寸屏幕上收紧 3D 视图与右侧面板的最小宽度。
        area = available_geometry(parent)
        self._compact = bool(
            area is not None and (area.width() < 1000 or area.height() < 760)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        title = QLabel(f"搜索盒子 · {target.display_name}")
        title.setStyleSheet(f"font-size: {theme.px(20)}; font-weight: 700;")
        self.insight_button = QPushButton("洞察模式")
        self.insight_button.setObjectName("Insight")
        self.insight_button.setCheckable(True)
        self.insight_button.setToolTip(
            "叠加显示共晶配体与 SITE 位点，并列出候选分子的重原子、柔性键与建议盒子尺寸"
        )
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.insight_button)
        intro = QLabel(
            "盒子越小，搜索越集中、越快；但太小可能错过真实结合位置。"
            "可直接拖动盒子调整，或在右侧精确输入坐标。"
        )
        intro.setObjectName("CardHint")
        intro.setWordWrap(True)
        layout.addLayout(header)
        layout.addWidget(intro)

        self.auto_card = QFrame()
        self.auto_card.setObjectName("Tile")
        auto_layout = QVBoxLayout(self.auto_card)
        auto_layout.setContentsMargins(14, 10, 14, 10)
        auto_layout.setSpacing(6)
        auto_head = QHBoxLayout()
        auto_title = QLabel("自动推荐")
        auto_title.setStyleSheet("font-weight: 700;")
        auto_head.addWidget(auto_title)
        auto_head.addStretch(1)
        self.use_auto_button = QPushButton("使用推荐盒子")
        self.use_auto_button.setObjectName("Small")
        self.use_auto_button.setEnabled(False)
        self.use_auto_button.clicked.connect(self._use_auto)
        self.recalc_button = QPushButton("重新计算")
        self.recalc_button.setObjectName("Small")
        self.recalc_button.clicked.connect(self._request_auto)
        auto_head.addWidget(self.use_auto_button)
        auto_head.addWidget(self.recalc_button)
        auto_layout.addLayout(auto_head)
        self.auto_summary = QLabel("正在计算推荐位置…")
        self.auto_summary.setWordWrap(True)
        self.auto_warning = QLabel("")
        self.auto_warning.setObjectName("Muted")
        self.auto_warning.setWordWrap(True)
        auto_layout.addWidget(self.auto_summary)
        auto_layout.addWidget(self.auto_warning)
        layout.addWidget(self.auto_card)

        self.reference_card = QFrame()
        self.reference_card.setObjectName("Tile")
        ref_layout = QVBoxLayout(self.reference_card)
        ref_layout.setContentsMargins(14, 10, 14, 10)
        ref_layout.setSpacing(5)
        ref_head = QHBoxLayout()
        ref_title = QLabel("参考信息")
        ref_title.setStyleSheet("font-weight: 700;")
        ref_head.addWidget(ref_title)
        ref_hint = QLabel("用于判断盒子中心与边长")
        ref_hint.setObjectName("CardHint")
        ref_head.addWidget(ref_hint)
        ref_head.addStretch(1)
        ref_layout.addLayout(ref_head)
        self.sites_label = QLabel("正在读取共晶配体与 PDB SITE 记录…")
        self.sites_label.setObjectName("CardHint")
        self.sites_label.setWordWrap(True)
        self.sites_label.setTextFormat(Qt.TextFormat.RichText)
        ref_layout.addWidget(self.sites_label)
        self.ligand_label = QLabel("")
        self.ligand_label.setWordWrap(True)
        self.ligand_label.setTextFormat(Qt.TextFormat.RichText)
        ref_layout.addWidget(self.ligand_label)
        self.fit_label = QLabel("")
        self.fit_label.setWordWrap(True)
        self.fit_label.setStyleSheet(f"color:{theme.MUTED}; font-size:{theme.px(12)};")
        ref_layout.addWidget(self.fit_label)
        self.reference_card.setVisible(False)  # 洞察模式默认关闭，保持原有 3D 显示
        layout.addWidget(self.reference_card)

        self.viewer = BoxViewer(self, compact=self._compact)
        self.viewer.auto_requested.connect(self._request_auto)
        self.viewer.box_edited.connect(self._update_box_fit)
        self.insight_button.toggled.connect(self._toggle_insight)
        layout.addWidget(self.viewer, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存盒子")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        if self.box is not None:
            self.viewer.set_box(self.box, mark_manual=self.box.source == "manual")
            self.viewer.focus_box()
            self._update_box_fit(self.box)

        scale = theme.ui_scale()
        minimum = QSize(720, 560) if self._compact else QSize(1000, 720)
        self.fit_to_screen(
            preferred=QSize(round(1160 * scale), round(820 * scale)),
            minimum=QSize(
                round(minimum.width() * math.sqrt(scale)),
                round(minimum.height() * math.sqrt(scale)),
            ),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._content_loaded or not self.isVisible():
            return
        self._content_loaded = True
        # 等这一轮绘制（含子控件）结束后再读取结构、计算推荐盒子，避免在
        # 首帧之前阻塞事件循环。
        QTimer.singleShot(0, self._initialize_content)

    def _initialize_content(self) -> None:
        if not self.isVisible():
            return
        self._load_structure()
        self._load_reference_info()
        self._request_auto()

    # ------------------------------------------------------------------
    # 结构
    # ------------------------------------------------------------------
    def _structure_path(self) -> Path | None:
        prepared = self.workspace.prepared_target_pdb(self.target.stem)
        if prepared.is_file():
            return prepared
        source = self.workspace.absolute(self.target.source)
        if source.is_file() and pdbio.detect_format(source) in {"pdb", "ent", "pdbqt"}:
            return source
        return None

    def _load_structure(self) -> None:
        path = self._structure_path()
        if path is None:
            self.viewer.show_placeholder(
                "还没有可显示的 PDB/PDBQT 结构。请先准备靶点，或在右侧手动输入盒子参数。"
            )
            return
        try:
            if not self.viewer.load_structure(path):
                self.viewer.show_placeholder(
                    "结构中没有可用于显示的原子，仍可在右侧手动输入盒子参数。"
                )
        except Exception as exc:  # pragma: no cover - 损坏文件的兜底
            log.warning("读取 3D 结构失败：%s", exc)
            self.viewer.show_placeholder(f"无法读取结构：{exc}\n仍可在右侧手动输入盒子参数。")

    # ------------------------------------------------------------------
    # 参考信息（共晶配体 / SITE / 候选分子柔性）
    # ------------------------------------------------------------------
    def _toggle_insight(self, enabled: bool) -> None:
        """打开/关闭洞察模式：文字参考 + 3D 彩色位点层。"""
        self.reference_card.setVisible(bool(enabled))
        self.viewer.set_sites_visible(bool(enabled))
        self.insight_button.setText("关闭洞察" if enabled else "洞察模式")

    def _load_reference_info(self) -> None:
        source = self.workspace.absolute(self.target.source)
        highlights = []
        if source.is_file():
            try:
                highlights = known_site_highlights(
                    source,
                    self.target.meta.get("chains", []),
                    obabel=self.obabel,
                )
            except Exception as exc:  # pragma: no cover - 损坏文件的兜底
                log.warning("读取已知位点失败：%s", exc)

        self.sites_label.setText(self._site_legend(highlights))
        cocrystal_atoms = [
            atom for item in highlights if item.kind == "cocrystal" for atom in item.atoms
        ]
        site_atoms = [
            atom for item in highlights if item.kind == "site" for atom in item.atoms
        ]
        overlaid = self.viewer.set_site_structures(
            atoms_to_pdb_text(cocrystal_atoms),
            atoms_to_pdb_text(site_atoms),
        )
        if not overlaid:
            self.sites_label.setToolTip(
                "当前 3D 视图为 OpenGL 回退模式，洞察模式只显示文字信息，"
                "不会叠加彩色位点"
            )

        entries = [
            entry
            for entry in self.workspace.ligands
            if entry.is_ready
            and modes.pair_mode(self.target, entry, self.workspace).supported
        ]
        self._required_size = ligand_required_sizes(
            self.workspace.absolute(entry.prepared) for entry in entries
        )
        self.ligand_label.setText(self._ligand_summary(entries, self._required_size))
        if self.viewer.has_box and self.viewer.current_box() is not None:
            self._update_box_fit(self.viewer.current_box())

    @staticmethod
    def _site_legend(highlights) -> str:
        if not highlights:
            return (
                f'<span style="color:{theme.MUTED}">未找到共晶配体或 PDB SITE 记录；'
                "可结合几何口袋与候选分子尺寸设置盒子。</span>"
            )
        lines: list[str] = []
        for kind, prefix, color in (
            ("cocrystal", "共晶配体 ", COCRYSTAL_COLOR),
            ("site", "", SITE_COLOR),
        ):
            group = [item for item in highlights if item.kind == kind]
            if not group:
                continue
            dot = f'<span style="color:{color}; font-weight:700;">●</span>'
            for item in group[:3]:
                lines.append(f"{dot} {prefix}{html.escape(item.label)}")
            if len(group) > 3:
                lines.append(f"{dot} 等共 {len(group)} 处")
        return "<br>".join(lines)

    @staticmethod
    def _ligand_summary(entries, required) -> str:
        if not entries:
            return (
                f'<span style="color:{theme.MUTED}">尚无已准备且支持对接的候选分子。</span>'
            )
        lines: list[str] = []
        for entry in entries[:3]:
            details = entry.meta.get("prep_details") or {}
            parts = [html.escape(entry.display_name)]
            heavy = details.get("heavy_atoms")
            if heavy:
                parts.append(f"{heavy} 个重原子")
            rotatable = details.get("rotatable_bonds")
            if rotatable is not None:
                if rotatable == 0:
                    parts.append("刚性（无可旋转键）")
                elif rotatable > 20:
                    parts.append(
                        f'<span style="color:{theme.WARNING}; font-weight:600;">'
                        f"{rotatable} 个可旋转键（柔性键）</span>"
                    )
                else:
                    parts.append(f"{rotatable} 个可旋转键（柔性键）")
            lines.append("候选分子：" + " · ".join(parts))
        if len(entries) > 3:
            lines.append(f"另有 {len(entries) - 3} 个候选分子")
        if required is not None:
            size = " × ".join(f"{value:.1f}" for value in required)
            lines.append(
                f'<span style="color:{theme.WARNING}; font-weight:600;">'
                f"建议盒子至少 {size} Å（含两侧各 4 Å 余量）</span>"
            )
        return "<br>".join(lines)

    def _update_box_fit(self, box: Box | None) -> None:
        if box is None or self._required_size is None:
            self.fit_label.setText("")
            return
        required = self._required_size
        size_text = " × ".join(f"{value:.1f}" for value in required)
        short = [
            axis
            for axis, value, need in zip("XYZ", box.size, required)
            if value + 1e-6 < need
        ]
        if short:
            self.fit_label.setStyleSheet(
                f"color:{theme.WARNING}; font-size:{theme.px(12)}; font-weight:600;"
            )
            self.fit_label.setText(
                f"当前盒子在 {'、'.join(short)} 方向小于建议的 {size_text} Å，"
                "可能截断候选分子的构象。"
            )
        else:
            self.fit_label.setStyleSheet(f"color:{theme.MUTED}; font-size:{theme.px(12)};")
            self.fit_label.setText(f"当前盒子可容纳候选分子（建议最小 {size_text} Å）。")

    # ------------------------------------------------------------------
    # 自动推荐
    # ------------------------------------------------------------------
    def _request_auto(self) -> None:
        if self._auto_pending:
            return
        self._auto_pending = True
        self.recalc_button.setEnabled(False)
        self.use_auto_button.setEnabled(False)
        self.auto_summary.setText("正在计算推荐位置…")
        self.auto_warning.setText("")
        # 让对话框先完成绘制，再执行可能较慢的口袋检测。
        QTimer.singleShot(0, self._compute_auto)

    def _compute_auto(self) -> None:
        try:
            ligands = [
                self.workspace.absolute(entry.prepared)
                for entry in self.workspace.ligands
                if entry.is_ready
                and modes.pair_mode(self.target, entry, self.workspace).supported
            ]
            try:
                box, warnings = suggest_box(
                    target_source=self.workspace.absolute(self.target.source),
                    target_prepared_pdb=self.workspace.prepared_target_pdb(self.target.stem),
                    ligand_paths=ligands,
                    settings=self.settings,
                    target_kind=modes.target_kind_for(self.target, self.workspace),
                    chains=self.target.meta.get("chains", []),
                    obabel=self.obabel,
                )
            except Exception as exc:
                self.auto_box = None
                self.auto_warnings = []
                self.auto_summary.setText(
                    f"自动推荐失败：{exc}\n请在右侧拖动或输入盒子参数。"
                )
                self.use_auto_button.setEnabled(False)
                return

            self.auto_box = box
            self.auto_warnings = warnings
            center = "，".join(f"{value:.2f}" for value in box.center)
            size = " × ".join(f"{value:.1f}" for value in box.size)
            summary = (
                f"来源：{box.source_label}　中心：({center})　尺寸：{size} Å　"
                f"体积：{box.volume:,.0f} Å³"
            )
            if box.note:
                summary += f"\n判断依据：{box.note}"
            self.auto_summary.setText(summary)
            self.auto_warning.setText("；".join(warnings) if warnings else "")
            self.use_auto_button.setEnabled(True)
            if not self.viewer.has_box:
                self.viewer.use_auto(box)
                self.viewer.focus_box()
                self._update_box_fit(box)
        finally:
            self._auto_pending = False
            self.recalc_button.setEnabled(True)

    def _use_auto(self) -> None:
        if self.auto_box is None:
            return
        self.viewer.use_auto(self.auto_box)
        self.viewer.focus_box()
        self._update_box_fit(self.auto_box)

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def _save(self) -> None:
        box = self.viewer.current_box()
        if box is None:
            if self.auto_box is None:
                QMessageBox.warning(
                    self,
                    "尚未设置盒子",
                    "请等待自动推荐完成，或在右侧输入中心与边长。",
                )
                return
            box = self.auto_box
        if self.viewer.is_dirty or box.source == "manual":
            try:
                box = validate_manual_box(box.center, box.size)
            except DockFlowError as exc:
                QMessageBox.warning(self, "盒子参数无效", str(exc))
                return
        self.workspace.save_box(self.target.stem, box)
        self.workspace.update_entry(
            self.target,
            box=self.workspace.relative(self.workspace.box_path(self.target.stem)),
        )
        self.box = box
        self.accept()


class SettingsDialog(ScreenFittedDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.original = settings
        self.settings = replace(settings)
        self.setWindowTitle("设置")
        self.setMinimumWidth(620)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        title = QLabel("设置与运行环境")
        title.setStyleSheet(f"font-size: {theme.px(20)}; font-weight: 700;")
        hint = QLabel("这些设置只影响本机，不会写入项目文件夹。")
        hint.setObjectName("CardHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        # 设置项较多（三种引擎 + 路径 + 环境检查），放进滚动区，
        # 免得在字号放大后超出屏幕高度、底部按钮被裁掉。
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 视口与内容都用主题浅色，避免 Windows 深色模式下滚动区变黑。
        scroll.viewport().setObjectName("SettingsViewport")
        content = QWidget()
        content.setObjectName("SettingsBody")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        # 小屏幕或字号放大后，宁可把标签换到输入框上方，也不要把“浏览”
        # 按钮挤出对话框。
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.engine_combo = QComboBox()
        for key in ENGINE_ORDER:
            self.engine_combo.addItem(ENGINE_LABELS[key], key)
            self.engine_combo.setItemData(
                self.engine_combo.count() - 1,
                ENGINE_DESCRIPTIONS[key],
                Qt.ItemDataRole.ToolTipRole,
            )
        engine_index = self.engine_combo.findData(self.settings.docking_engine)
        self.engine_combo.setCurrentIndex(engine_index if engine_index >= 0 else 0)
        form.addRow("默认对接引擎", self.engine_combo)

        self.engine_path_edits: dict[str, QLineEdit] = {}
        for key, label, placeholder in (
            (ENGINE_VINA, "Vina 可执行文件", "留空自动查找（PATH、程序目录或 resources/vina）"),
            ("qvina2", "QuickVina 2 可执行文件", "留空自动使用程序目录中的 qvina2"),
            ("smina", "Smina 可执行文件", "留空自动使用程序目录中的 smina"),
        ):
            edit = QLineEdit(engine_path_setting(self.settings, key))
            edit.setPlaceholderText(placeholder)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(edit, 1)
            browse = QPushButton("浏览")
            browse.clicked.connect(lambda _checked=False, target=edit: self._browse_into(target))
            row_layout.addWidget(browse)
            form.addRow(label, row)
            self.engine_path_edits[key] = edit

        self.fallback_check = QCheckBox("首选引擎卡死或失败时，自动改用其他可用引擎重试")
        self.fallback_check.setChecked(bool(self.settings.engine_fallback))
        form.addRow("引擎回退", self.fallback_check)

        self.obabel_edit = QLineEdit(self.settings.obabel_path)
        self.obabel_edit.setPlaceholderText("留空自动查找；通常无需修改")
        obabel_row = QWidget()
        obabel_layout = QHBoxLayout(obabel_row)
        obabel_layout.setContentsMargins(0, 0, 0, 0)
        obabel_layout.addWidget(self.obabel_edit, 1)
        obabel_browse = QPushButton("浏览")
        obabel_browse.clicked.connect(lambda: self._browse_into(self.obabel_edit))
        obabel_layout.addWidget(obabel_browse)
        form.addRow("OpenBabel 可执行文件", obabel_row)

        self.pymol_edit = QLineEdit(self.settings.pymol_path)
        self.pymol_edit.setPlaceholderText("留空自动查找；用于“PyMOL 渲染”页面")
        pymol_row = QWidget()
        pymol_layout = QHBoxLayout(pymol_row)
        pymol_layout.setContentsMargins(0, 0, 0, 0)
        pymol_layout.addWidget(self.pymol_edit, 1)
        pymol_browse = QPushButton("浏览")
        pymol_browse.clicked.connect(lambda: self._browse_into(self.pymol_edit))
        pymol_layout.addWidget(pymol_browse)
        form.addRow("PyMOL 可执行文件", pymol_row)

        self.proxy_edit = QLineEdit(self.settings.proxy)
        self.proxy_edit.setPlaceholderText("例如 127.0.0.1:7897；不需要代理时留空")
        form.addRow("网络代理", self.proxy_edit)

        self.exhaustiveness = QComboBox()
        self.exhaustiveness.addItem("快速（8）", 8)
        self.exhaustiveness.addItem("标准（16）", 16)
        self.exhaustiveness.addItem("精细（32）", 32)
        index = self.exhaustiveness.findData(self.settings.exhaustiveness)
        self.exhaustiveness.setCurrentIndex(index if index >= 0 else 1)
        form.addRow("默认精细度", self.exhaustiveness)

        self.cpu = QSpinBox()
        self.cpu.setRange(0, 256)
        self.cpu.setValue(self.settings.cpu)
        self.cpu.setSpecialValueText("自动")
        _plain_number(self.cpu)
        self.cpu.setMaximumWidth(140)
        form.addRow("默认 CPU 核心", self.cpu)
        content_layout.addLayout(form)

        env_card = Card("运行环境")
        self.env_label = QLabel("点击下方按钮检查。")
        self.env_label.setObjectName("CardHint")
        self.env_label.setWordWrap(True)
        self.env_label.setTextFormat(Qt.TextFormat.RichText)
        # RichText + 自动换行时，QLabel 的 minimumSizeHint 仍可能按整行计算，
        # 导致滚动区被横向撑开；忽略水平最小宽度，让文字按视口换行。
        self.env_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.env_label.setMinimumWidth(0)
        check = QPushButton("检查运行环境")
        check.setMaximumWidth(160)
        check.clicked.connect(self._check_environment)
        env_card.add(self.env_label)
        env_card.add(check)
        content_layout.addWidget(env_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._check_environment()
        hint = self.sizeHint()
        scale = theme.ui_scale()
        self.fit_to_screen(
            preferred=QSize(
                max(round(620 * scale), round(hint.width() * scale)),
                round(hint.height() * scale),
            ),
            minimum=QSize(
                round(520 * math.sqrt(scale)), round(420 * math.sqrt(scale))
            ),
        )

    def _browse_into(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择可执行文件", edit.text() or str(Path.home()))
        if path:
            edit.setText(path)

    def _check_environment(self) -> None:
        rows = []
        for key in ENGINE_ORDER:
            try:
                engine = make_engine(key, self.engine_path_edits[key].text().strip())
            except Exception:
                engine = None
            label = ENGINE_LABELS[key]
            if engine is not None and engine.available:
                version = (engine.version or "").strip()
                text = label
                if version and version.lower() not in label.lower():
                    text = f"{label}：{version}"
                rows.append(
                    f"<span style='color:{theme.SUCCESS}'>✓</span> {html.escape(text)}"
                )
            else:
                rows.append(f"<span style='color:{theme.DANGER}'>✗</span> 未找到 {label}")
        try:
            obabel = OpenBabelAdapter(self.obabel_edit.text().strip())
        except Exception:
            obabel = None
        if obabel is not None and obabel.available:
            version = obabel.version_string or ""
            label = version if "openbabel" in version.lower() else f"OpenBabel {version}".strip()
            rows.append(f"<span style='color:{theme.SUCCESS}'>✓</span> {label}")
        else:
            rows.append(f"<span style='color:{theme.DANGER}'>✗</span> 未找到 OpenBabel")
        renderer = find_renderer(self.pymol_edit.text().strip())
        if renderer:
            rows.append(
                f"<span style='color:{theme.SUCCESS}'>✓</span> PyMOL 渲染可用："
                f"<span style='color:{theme.SECONDARY}'>{html.escape(renderer)}</span>"
            )
        else:
            rows.append(
                f"<span style='color:{theme.WARNING}'>!</span> 未找到 PyMOL，"
                "仅影响“PyMOL 渲染”页面"
            )
        for module, label in (("rdkit", "RDKit"), ("meeko", "Meeko"), ("openmm", "OpenMM"), ("pdbfixer", "PDBFixer")):
            found = importlib.util.find_spec(module) is not None
            color = theme.SUCCESS if found else theme.WARNING
            mark = "✓" if found else "!"
            rows.append(f"<span style='color:{color}'>{mark}</span> {label}{'' if found else '（缺失，将使用回退流程）'}")
        self.env_label.setText("<br>".join(rows))

    def _save(self) -> None:
        for key in ENGINE_ORDER:
            path = self.engine_path_edits[key].text().strip()
            if path and not Path(path).is_file():
                QMessageBox.warning(
                    self,
                    "路径无效",
                    f"{ENGINE_LABELS[key]} 可执行文件不存在，请重新选择。",
                )
                return
        obabel_path = self.obabel_edit.text().strip()
        if obabel_path and not Path(obabel_path).is_file():
            QMessageBox.warning(self, "路径无效", "OpenBabel 可执行文件不存在，请重新选择。")
            return
        pymol_path = self.pymol_edit.text().strip()
        if pymol_path and not Path(pymol_path).is_file():
            QMessageBox.warning(self, "路径无效", "PyMOL 可执行文件不存在，请重新选择。")
            return
        for key in ENGINE_ORDER:
            set_engine_path(self.settings, key, self.engine_path_edits[key].text().strip())
        self.settings.docking_engine = str(self.engine_combo.currentData() or ENGINE_VINA)
        self.settings.engine_fallback = bool(self.fallback_check.isChecked())
        self.settings.obabel_path = obabel_path
        self.settings.pymol_path = pymol_path
        self.settings.proxy = self.proxy_edit.text().strip()
        self.settings.exhaustiveness = int(self.exhaustiveness.currentData())
        self.settings.cpu = int(self.cpu.value())
        self.settings.save()
        self.accept()
