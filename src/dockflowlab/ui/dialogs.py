"""下载、盒子、设置等对话框。"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
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
    QPushButton,
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
from ..services.boxes import suggest_box, validate_manual_box
from ..services.download import DownloadClient
from ..services.pymol import open_in_pymol, write_box_pml
from ..tools.obabel import OpenBabelAdapter
from ..tools.runtime import ascii_temp_root
from ..tools.vina import VinaAdapter
from ..workspace import Workspace
from . import theme
from .box_view import BoxViewer
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
        noun = "靶点结构" if kind == KIND_TARGET else "候选分子"
        self.setWindowTitle(f"下载{noun}")
        self.setMinimumWidth(560)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        title = QLabel(f"从公共数据库获取{noun}")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
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
        self.cancel_button.setEnabled(False)
        self.status.setText("正在连接数据库并下载…")
        self.preview.setVisible(False)
        self._task = Task(lambda progress, cancel, line: download(query), self)
        self._task.done.connect(self._download_done)
        self._task.failed.connect(self._download_failed)
        self._task.finished.connect(self._task_finished)
        self._task.start()

    def _task_finished(self) -> None:
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def _download_done(self, result: DownloadResult) -> None:
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
        self.status.setText(f"下载失败：{message}")
        self.preview.setVisible(False)

    def reject(self) -> None:
        if self._task is not None and self._task.isRunning():
            self._task.request_cancel()
            self.status.setText("下载请求已发出，完成后窗口将关闭。")
            self._task.finished.connect(self.reject)
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._task is not None and self._task.isRunning():
            event.ignore()
            return
        super().closeEvent(event)


class BoxDialog(QDialog):
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
        self._auto_pending = False
        self.setWindowTitle(f"设置盒子 · {target.display_name}")
        self.setMinimumSize(1000, 680)
        self.resize(1160, 780)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        title = QLabel(f"搜索盒子 · {target.display_name}")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel(
            "盒子越小，搜索越集中、越快；但太小可能错过真实结合位置。"
            "可直接拖动盒子调整，或在右侧精确输入坐标。"
        )
        intro.setObjectName("CardHint")
        intro.setWordWrap(True)
        layout.addWidget(title)
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

        self.viewer = BoxViewer(self)
        self.viewer.auto_requested.connect(self._request_auto)
        layout.addWidget(self.viewer, 1)

        buttons = QHBoxLayout()
        self.pymol_button = QPushButton("在 PyMOL 中查看")
        self.pymol_button.setToolTip(
            "生成只含结构与盒子的 PyMOL 脚本，方便在 PyMOL 中旋转、放大检查。"
        )
        self.pymol_button.clicked.connect(self._open_pymol)
        buttons.addWidget(self.pymol_button)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存盒子")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self._load_structure()
        if self.box is not None:
            self.viewer.set_box(self.box, mark_manual=self.box.source == "manual")
            self.viewer.focus_box()
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
        finally:
            self._auto_pending = False
            self.recalc_button.setEnabled(True)

    def _use_auto(self) -> None:
        if self.auto_box is None:
            return
        self.viewer.use_auto(self.auto_box)
        self.viewer.focus_box()

    # ------------------------------------------------------------------
    # 保存 / PyMOL
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

    def _open_pymol(self) -> None:
        box = self.viewer.current_box() or self.auto_box
        if box is None:
            QMessageBox.information(self, "尚未设置盒子", "请先等待自动推荐，或手动设置盒子。")
            return
        structure = self._structure_path()
        if structure is None:
            QMessageBox.information(
                self, "无法预览", "还没有可用的 PDB/PDBQT 结构，请先准备靶点。"
            )
            return
        try:
            temp_dir = ascii_temp_root() / "pymol_preview"
            temp_dir.mkdir(parents=True, exist_ok=True)
            suffix = structure.suffix.lower() or ".pdb"
            temp_structure = temp_dir / f"{self.target.stem}{suffix}"
            shutil.copy2(structure, temp_structure)
            pml = temp_dir / f"{self.target.stem}.pml"
            write_box_pml(
                temp_structure,
                pml,
                box,
                title=f"{self.target.display_name} 搜索盒子",
            )
        except OSError as exc:
            QMessageBox.warning(self, "无法生成预览", f"写入临时文件失败：{exc}")
            return
        ok, message = open_in_pymol(pml)
        if not ok:
            hint = (
                f"{message}\n\n"
                f"盒子脚本已保存到：\n{pml}\n"
                "安装 PyMOL 后可直接打开该文件；也可以把可执行文件放到 PATH 中。"
            )
            QMessageBox.information(self, "无法打开 PyMOL", hint)


class SettingsDialog(QDialog):
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
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        hint = QLabel("这些设置只影响本机，不会写入项目文件夹。")
        hint.setObjectName("CardHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        self.vina_edit = QLineEdit(self.settings.vina_path)
        self.vina_edit.setPlaceholderText("留空自动查找（PATH、程序目录或 resources/vina）")
        vina_row = QWidget()
        vina_layout = QHBoxLayout(vina_row)
        vina_layout.setContentsMargins(0, 0, 0, 0)
        vina_layout.addWidget(self.vina_edit, 1)
        vina_browse = QPushButton("浏览")
        vina_browse.clicked.connect(lambda: self._browse_into(self.vina_edit))
        vina_layout.addWidget(vina_browse)
        form.addRow("Vina 可执行文件", vina_row)

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
        layout.addLayout(form)

        env_card = Card("运行环境")
        self.env_label = QLabel("点击下方按钮检查。")
        self.env_label.setObjectName("CardHint")
        self.env_label.setWordWrap(True)
        self.env_label.setTextFormat(Qt.TextFormat.RichText)
        check = QPushButton("检查运行环境")
        check.setMaximumWidth(160)
        check.clicked.connect(self._check_environment)
        env_card.add(self.env_label)
        env_card.add(check)
        layout.addWidget(env_card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._check_environment()
        hint = self.sizeHint()
        self.resize(max(self.minimumWidth(), hint.width()), hint.height())

    def _browse_into(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择可执行文件", edit.text() or str(Path.home()))
        if path:
            edit.setText(path)

    def _check_environment(self) -> None:
        try:
            vina = VinaAdapter(self.vina_edit.text().strip())
        except Exception:
            vina = VinaAdapter()
        try:
            obabel = OpenBabelAdapter(self.obabel_edit.text().strip())
        except Exception:
            obabel = None
        rows = []
        if vina.available:
            version = vina.version or ""
            label = version if "vina" in version.lower() else f"AutoDock Vina {version}".strip()
            rows.append(f"<span style='color:{theme.SUCCESS}'>✓</span> {label}")
        else:
            rows.append(f"<span style='color:{theme.DANGER}'>✗</span> 未找到 AutoDock Vina")
        if obabel is not None and obabel.available:
            version = obabel.version_string or ""
            label = version if "openbabel" in version.lower() else f"OpenBabel {version}".strip()
            rows.append(f"<span style='color:{theme.SUCCESS}'>✓</span> {label}")
        else:
            rows.append(f"<span style='color:{theme.DANGER}'>✗</span> 未找到 OpenBabel")
        for module, label in (("rdkit", "RDKit"), ("meeko", "Meeko"), ("openmm", "OpenMM"), ("pdbfixer", "PDBFixer")):
            found = importlib.util.find_spec(module) is not None
            color = theme.SUCCESS if found else theme.WARNING
            mark = "✓" if found else "!"
            rows.append(f"<span style='color:{color}'>{mark}</span> {label}{'' if found else '（缺失，将使用回退流程）'}")
        self.env_label.setText("<br>".join(rows))

    def _save(self) -> None:
        vina_path = self.vina_edit.text().strip()
        if vina_path and not Path(vina_path).is_file():
            QMessageBox.warning(self, "路径无效", "Vina 可执行文件不存在，请重新选择。")
            return
        obabel_path = self.obabel_edit.text().strip()
        if obabel_path and not Path(obabel_path).is_file():
            QMessageBox.warning(self, "路径无效", "OpenBabel 可执行文件不存在，请重新选择。")
            return
        self.settings.vina_path = vina_path
        self.settings.obabel_path = obabel_path
        self.settings.proxy = self.proxy_edit.text().strip()
        self.settings.exhaustiveness = int(self.exhaustiveness.currentData())
        self.settings.cpu = int(self.cpu.value())
        self.settings.save()
        self.accept()
