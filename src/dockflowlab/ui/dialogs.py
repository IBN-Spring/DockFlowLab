"""下载、盒子、设置等对话框。"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import AppSettings
from ..core.models import KIND_LIGAND, KIND_TARGET, Box, DownloadResult, ProjectFile
from ..errors import DockFlowError
from ..services import modes
from ..services.boxes import suggest_box, validate_manual_box
from ..services.download import DownloadClient
from ..tools.obabel import OpenBabelAdapter
from ..tools.vina import VinaAdapter
from ..workspace import Workspace
from . import theme
from .widgets import Card, StatusChip
from .workers import Task


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
        self.setWindowTitle(f"设置盒子 · {target.display_name}")
        self.setMinimumWidth(600)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel(f"搜索盒子 · {target.display_name}")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel("盒子越小，搜索越集中、越快；但太小可能错过真实结合位置。")
        intro.setObjectName("CardHint")
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        self.auto_radio = QRadioButton("自动推荐（几何口袋检测，共晶配体 / 已知位点校验兜底）")
        self.manual_radio = QRadioButton("手动输入中心与边长")
        group = QVBoxLayout()
        group.setSpacing(6)
        group.addWidget(self.auto_radio)
        group.addWidget(self.manual_radio)
        layout.addLayout(group)

        self.auto_card = QFrame()
        self.auto_card.setObjectName("Tile")
        auto_layout = QVBoxLayout(self.auto_card)
        auto_layout.setContentsMargins(14, 12, 14, 12)
        self.auto_summary = QLabel("正在计算推荐位置…")
        self.auto_summary.setWordWrap(True)
        self.auto_warning = QLabel("")
        self.auto_warning.setObjectName("Muted")
        self.auto_warning.setWordWrap(True)
        recalc = QPushButton("重新计算推荐")
        recalc.setObjectName("Small")
        recalc.clicked.connect(self._compute_auto)
        auto_layout.addWidget(self.auto_summary)
        auto_layout.addWidget(self.auto_warning)
        auto_layout.addWidget(recalc, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.auto_card)

        self.manual_card = QFrame()
        self.manual_card.setObjectName("Tile")
        manual_layout = QGridLayout(self.manual_card)
        manual_layout.setContentsMargins(14, 12, 14, 12)
        manual_layout.setHorizontalSpacing(10)
        manual_layout.setVerticalSpacing(8)
        manual_layout.addWidget(QLabel("中心 X / Y / Z (Å)"), 0, 0)
        center_row = QHBoxLayout()
        self.center_inputs = []
        for _ in range(3):
            spin = QDoubleSpinBox()
            spin.setRange(-10000, 10000)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setMinimumWidth(86)
            _plain_number(spin)
            self.center_inputs.append(spin)
            center_row.addWidget(spin)
        manual_layout.addLayout(center_row, 0, 1)
        manual_layout.addWidget(QLabel("边长 X / Y / Z (Å)"), 1, 0)
        size_row = QHBoxLayout()
        self.size_inputs = []
        for _ in range(3):
            spin = QDoubleSpinBox()
            spin.setRange(5, 150)
            spin.setDecimals(1)
            spin.setSingleStep(1)
            spin.setValue(22)
            spin.setMinimumWidth(86)
            _plain_number(spin)
            self.size_inputs.append(spin)
            size_row.addWidget(spin)
        manual_layout.addLayout(size_row, 1, 1)
        layout.addWidget(self.manual_card)
        self.manual_card.setVisible(False)
        self.auto_radio.toggled.connect(self._toggle_manual)
        self.manual_radio.toggled.connect(self._toggle_manual)
        self.auto_radio.setChecked(True)

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

        if self.box is not None and self.box.source == "manual":
            self._fill_manual(self.box)
            self.manual_radio.setChecked(True)
        self._compute_auto()
        self._fit()

    def _fit(self) -> None:
        self.layout().invalidate()
        self.layout().activate()
        hint = self.sizeHint()
        self.resize(max(self.minimumWidth(), hint.width()), hint.height())

    def _toggle_manual(self) -> None:
        manual = self.manual_radio.isChecked()
        self.manual_card.setVisible(manual)
        self.auto_card.setVisible(not manual)
        self._fit()

    def _fill_manual(self, box: Box) -> None:
        for spin, value in zip(self.center_inputs, box.center):
            spin.setValue(float(value))
        for spin, value in zip(self.size_inputs, box.size):
            spin.setValue(float(value))

    def _compute_auto(self) -> None:
        self.auto_summary.setText("正在计算推荐位置…")
        self.auto_warning.setText("")
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
            self.auto_summary.setText(f"自动推荐失败：{exc}\n请改用手动输入。")
            self.auto_warnings = []
            self.auto_radio.setChecked(False)
            self.manual_radio.setChecked(True)
            return
        self.auto_box = box
        self.auto_warnings = warnings
        center = "，".join(f"{value:.2f}" for value in box.center)
        size = " × ".join(f"{value:.1f}" for value in box.size)
        summary = (
            f"来源：{box.source_label}　中心：({center})　尺寸：{size} Å\n"
            f"体积：{box.volume:,.0f} Å³"
        )
        if box.note:
            summary += f"\n判断依据：{box.note}"
        self.auto_summary.setText(summary)
        self.auto_warning.setText("；".join(warnings) if warnings else "")
        if self.box is None:
            self._fill_manual(box)

    def _save(self) -> None:
        if self.auto_radio.isChecked():
            if self.auto_box is None:
                QMessageBox.warning(self, "无法保存", "自动推荐没有成功，请改用手动输入或重新计算。")
                return
            box = self.auto_box
        else:
            try:
                box = validate_manual_box(
                    [spin.value() for spin in self.center_inputs],
                    [spin.value() for spin in self.size_inputs],
                )
            except DockFlowError as exc:
                QMessageBox.warning(self, "盒子参数无效", str(exc))
                return
        self.workspace.save_box(self.target.stem, box)
        self.workspace.update_entry(self.target, box=self.workspace.relative(self.workspace.box_path(self.target.stem)))
        self.box = box
        self.accept()


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
