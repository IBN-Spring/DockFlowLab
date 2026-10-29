"""柔性残基选择对话框。

界面按“先看盒子、再选残基”的顺序组织：自动选择只挑盒子中心附近、侧链
确实可旋转的残基；用户也可以在列表中逐项勾选，最多 12 个。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import AppSettings
from ..core.models import ProjectFile
from ..logging_setup import get_logger
from ..services import flexible as flex_service
from ..workspace import Workspace
from . import theme
from .geometry import ScreenFittedDialog

log = get_logger("flex_dialog")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CardHint")
    label.setWordWrap(True)
    return label


class FlexResidueDialog(ScreenFittedDialog):
    """选择哪些氨基酸侧链在对接时保持可动。"""

    def __init__(
        self,
        workspace: Workspace,
        target: ProjectFile,
        settings: AppSettings | None = None,
        *,
        engine_key: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.target = target
        self.settings = settings
        self.engine_key = engine_key
        self.box = workspace.load_box(target.stem)
        self._candidates: list[flex_service.ResidueCandidate] = []
        self._selected: set[str] = {
            item.key for item in flex_service.selected_residues(target)
        }
        # 避免程序写入勾选状态时触发 itemChanged 造成递归。
        self._loading = False
        self.setWindowTitle(f"柔性残基 · {target.display_name}")
        self.setModal(True)
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)

        title = QLabel(f"柔性残基 · {target.display_name}")
        title.setStyleSheet(f"font-size: {theme.px(20)}; font-weight: 700;")
        layout.addWidget(title)
        layout.addWidget(
            _hint(
                "默认情况下受体按刚性处理。把结合口袋附近的氨基酸侧链设为柔性，"
                "可以让侧链与配体一起调整，缓解“口袋太紧”造成的假冲突。"
                "注意：每增加一个柔性残基，搜索空间都会与配体的可旋转键、"
                "搜索精细度和配体状态数相乘，耗时可能成倍增加（一组从几十秒"
                "变成十几分钟很常见）。建议只选真正挡住配体的 2–4 个侧链，"
                "先跑通再逐步增加，最多 12 个。"
            )
        )

        if self.box is None:
            blocker = QLabel(
                "这个靶点还没有保存搜索盒子。柔性残基需要按盒子中心挑选，"
                "请先在“每个靶点的搜索盒子”里设置盒子，再回来选择。"
            )
            blocker.setObjectName("Warning")
            blocker.setWordWrap(True)
            layout.addWidget(blocker)
        else:
            center = "、".join(f"{value:.1f}" for value in self.box.center)
            layout.addWidget(
                _hint(
                    f"盒子中心：{center} Å（{self.box.source_label}）。"
                    "列表按与盒子中心的距离排序。"
                )
            )
        if engine_key == "qvina2":
            engine_hint = QLabel(
                "当前选择的引擎 QuickVina 2 不支持柔性残基；"
                "选择会在切换到 AutoDock Vina 或 Smina 后才生效。"
            )
            engine_hint.setObjectName("Warning")
            engine_hint.setWordWrap(True)
            layout.addWidget(engine_hint)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.auto_button = QPushButton("自动选择盒子附近的侧链")
        self.auto_button.setObjectName("Small")
        self.auto_button.setToolTip(
            f"选取盒子中心 {flex_service.DEFAULT_AUTO_RADIUS:.0f} Å 内、"
            f"侧链可旋转的残基，最多 {flex_service.MAX_FLEX_RESIDUES} 个"
        )
        self.auto_button.clicked.connect(self._auto_select)
        toolbar.addWidget(self.auto_button)
        self.clear_button = QPushButton("全部清除")
        self.clear_button.setObjectName("Small")
        self.clear_button.clicked.connect(self._clear_selection)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("CardHint")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["选择", "残基", "距盒子中心", "侧链原子", "说明"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 56)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("CardHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save_button = QPushButton("保存选择")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self._load_candidates()

    # -- 构建 ----------------------------------------------------------
    def _prepared_pdb(self) -> Path | None:
        path = self.workspace.prepared_target_pdb(self.target.stem)
        if path.is_file():
            return path
        raw = str(self.target.meta.get("prepared_pdb") or "")
        if raw:
            candidate = self.workspace.absolute(raw)
            if candidate.is_file():
                return candidate
        return None

    def _load_candidates(self) -> None:
        pdb_path = self._prepared_pdb()
        if self.box is None or pdb_path is None:
            self.auto_button.setEnabled(False)
            self.table.setEnabled(False)
            self.save_button.setEnabled(False)
            self._update_count()
            if pdb_path is None and self.box is not None:
                self.status_label.setText(
                    "找不到准备好的靶点结构，请先在“检查与准备”中准备靶点。"
                )
            return
        self._candidates = flex_service.residue_candidates(
            pdb_path, self.box.center, radius=flex_service.LIST_RADIUS
        )
        self._loading = True
        try:
            self.table.setRowCount(len(self._candidates))
            for row, candidate in enumerate(self._candidates):
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                check.setCheckState(
                    Qt.CheckState.Checked
                    if candidate.residue.key in self._selected
                    else Qt.CheckState.Unchecked
                )
                check.setToolTip(candidate.residue.key)
                self.table.setItem(row, 0, check)
                self.table.setItem(row, 1, QTableWidgetItem(candidate.residue.label))
                distance = QTableWidgetItem(f"{candidate.distance:.1f} Å")
                distance.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row, 2, distance)
                atoms = QTableWidgetItem(str(candidate.sidechain_atoms))
                atoms.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row, 3, atoms)
                self.table.setItem(row, 4, QTableWidgetItem(candidate.note))
        finally:
            self._loading = False
        if not self._candidates:
            self.status_label.setText(
                "盒子中心 12 Å 内没有可选的氨基酸残基；请确认盒子位置是否合适。"
            )
        elif not self._selected:
            self.status_label.setText(
                "还没有选择柔性残基。可以直接点“自动选择”，或按距离从近到远手动勾选。"
            )
        self._update_count()

    # -- 交互 ----------------------------------------------------------
    def _set_row_checked(self, row: int, checked: bool) -> None:
        item = self.table.item(row, 0)
        if item is None:
            return
        previous = self._loading
        self._loading = True
        try:
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
        finally:
            self._loading = previous

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        candidate = self._candidates[item.row()]
        key = candidate.residue.key
        checked = item.checkState() == Qt.CheckState.Checked
        if not checked:
            self._selected.discard(key)
            self.status_label.setText("")
        elif not candidate.rotatable:
            self._set_row_checked(item.row(), False)
            self.status_label.setText(
                f"{candidate.residue.label}：{candidate.note}，不适合作为柔性残基。"
            )
        elif (
            key not in self._selected
            and len(self._selected) >= flex_service.MAX_FLEX_RESIDUES
        ):
            self._set_row_checked(item.row(), False)
            self.status_label.setText(
                f"最多只能选择 {flex_service.MAX_FLEX_RESIDUES} 个柔性残基；"
                "请先取消其他残基后再选。"
            )
        else:
            self._selected.add(key)
            self.status_label.setText("")
        self._update_count()

    def _update_count(self) -> None:
        count = len(self._selected)
        self.count_label.setText(
            f"已选 {count} / {flex_service.MAX_FLEX_RESIDUES} 个"
        )
        self.clear_button.setEnabled(count > 0)
        self.auto_button.setEnabled(bool(self._candidates))

    def _auto_select(self) -> None:
        residues = flex_service.auto_select(self._candidates)
        if not residues:
            self.status_label.setText(
                "盒子中心附近没有可旋转的侧链；请在下面的列表中手动选择。"
            )
            return
        self._selected = {item.key for item in residues}
        keys = {item.key for item in residues}
        for row, candidate in enumerate(self._candidates):
            self._set_row_checked(row, candidate.residue.key in keys)
        self.status_label.setText(
            "已按距离自动选择 "
            + "、".join(item.label for item in residues[:6])
            + (" 等" if len(residues) > 6 else "")
            + "；可以继续手动调整。"
        )
        self._update_count()

    def _clear_selection(self) -> None:
        self._selected.clear()
        for row in range(self.table.rowCount()):
            self._set_row_checked(row, False)
        self.status_label.setText("已清除全部选择，本次对接将按刚性受体处理。")
        self._update_count()

    def _save(self) -> None:
        residues = [
            candidate.residue
            for candidate in self._candidates
            if candidate.residue.key in self._selected
        ]
        # 选择里可能含有列表之外的旧残基（例如结构更新后残基被重编号），
        # 这些残基无法确认仍然有效，直接丢弃。
        if len(residues) < len(self._selected) and self._candidates:
            log.info("丢弃 %s 个不在列表中的柔性残基", len(self._selected) - len(residues))
        flex_service.save_selection(self.target, residues)
        self.workspace.update_entry(self.target, meta=dict(self.target.meta))
        try:
            flex_service.clear_cache(self.workspace, self.target.stem)
        except Exception:  # 清理失败不影响保存
            log.warning("清理柔性残基缓存失败", exc_info=True)
        self.accept()
