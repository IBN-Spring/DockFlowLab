"""五个向导页面：工作目录、添加分子、准备、对接设置、结果。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import KIND_LIGAND, KIND_TARGET, STATUS_READY, DockingResult, ProjectFile
from ..services import modes
from ..workspace import Workspace
from . import theme
from .widgets import (
    Card,
    DropZone,
    EmptyState,
    EntityRow,
    LogPanel,
    PageHeader,
    StatTile,
    StatusChip,
)

TARGET_FILTER = (
    "结构文件 (*.pdb *.cif *.pdbqt *.mol2 *.sdf *.mol *.smi *.smiles);;所有文件 (*)"
)
LIGAND_FILTER = "分子文件 (*.sdf *.mol2 *.mol *.pdb *.pdbqt *.smi *.smiles);;所有文件 (*)"


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # 先脱离父控件，deleteLater 生效前旧控件也不会继续显示造成重叠
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _plain_number(spin: QAbstractSpinBox) -> QAbstractSpinBox:
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return spin


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _display_map(entries) -> dict[str, str]:
    from ..services.analysis import unique_labels

    return unique_labels(entries)


class Page(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("PageScroll")
        body_widget = QWidget()
        body_widget.setObjectName("ScrollBody")
        self.body = QVBoxLayout(body_widget)
        self.body.setContentsMargins(34, 28, 34, 32)
        self.body.setSpacing(18)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(body_widget)
        outer.addWidget(self.scroll)


def _header_row(eyebrow: str, title: str, subtitle: str, actions: list[QWidget] | None = None) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(PageHeader(eyebrow, title, subtitle), 1)
    if actions:
        for action in actions:
            layout.addWidget(action, alignment=Qt.AlignmentFlag.AlignTop)
    return container


# ----------------------------------------------------------------------
# 1. 工作目录
# ----------------------------------------------------------------------
class WelcomePage(Page):
    choose_requested = Signal()
    create_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        hero = QWidget()
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(0, 18, 0, 6)
        hero_layout.setSpacing(10)
        eyebrow = QLabel("DockFlowLab · 分子对接工作台")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("四步完成一次规范的分子对接")
        title.setStyleSheet("font-size: 34px; font-weight: 700;")
        title.setWordWrap(True)
        subtitle = QLabel(
            "不用记命令，也不用理解文件格式。选择工作文件夹，加入靶点结构（蛋白、核酸或多肽）"
            "和候选分子，程序会自动识别对接类型，并完成结构修复、格式转换与对接计算。"
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.choose_button = QPushButton("选择工作文件夹")
        self.choose_button.setObjectName("Primary")
        self.choose_button.setMinimumWidth(170)
        self.choose_button.clicked.connect(self.choose_requested.emit)
        create = QPushButton("新建工作文件夹")
        create.setMinimumWidth(150)
        create.clicked.connect(self.create_requested.emit)
        buttons.addWidget(self.choose_button)
        buttons.addWidget(create)
        buttons.addStretch(1)
        hero_layout.addSpacing(8)
        hero_layout.addLayout(buttons)
        self.body.addWidget(hero)

        steps = Card("它会替你完成这些事")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        items = [
            ("1", "选择文件夹", "所有输入、结果和报告都保存在这里，不会散落到别处。"),
            ("2", "添加结构", "拖入蛋白、DNA/RNA 或多肽靶点，再放入候选分子；也可以从公共数据库下载。"),
            ("3", "一键准备", "自动补全缺失原子、加氢、转换对接格式，并检查文件是否可用。"),
            ("4", "设置盒子并运行", "自动检测几何口袋，并用共晶配体或已知位点校验，确认后开始对接并生成报告。"),
        ]
        for index, (number, heading, text) in enumerate(items):
            tile = QFrame()
            tile.setObjectName("Tile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 14, 16, 14)
            tile_layout.setSpacing(4)
            number_label = QLabel(number)
            number_label.setStyleSheet(
                f"color: {theme.ACCENT}; font-size: 13px; font-weight: 700;"
            )
            heading_label = QLabel(heading)
            heading_label.setStyleSheet("font-size: 14px; font-weight: 600;")
            text_label = QLabel(text)
            text_label.setObjectName("Muted")
            text_label.setWordWrap(True)
            tile_layout.addWidget(number_label)
            tile_layout.addWidget(heading_label)
            tile_layout.addWidget(text_label)
            grid.addWidget(tile, index // 2, index % 2)
        steps.add_layout(grid)
        self.body.addWidget(steps)

        self.recent_card = Card("最近的文件夹")
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(6)
        self.recent_card.add_layout(self.recent_layout)
        self.recent_card.setVisible(False)
        self.body.addWidget(self.recent_card)

        note = Card("数据放在哪里？")
        note_text = QLabel(
            "只保存在你选择的文件夹里。除了“从数据库下载”需要联网，其余计算都在本机完成。"
            "不需要注册、登录或输入任何授权码。"
        )
        note_text.setObjectName("CardHint")
        note_text.setWordWrap(True)
        note.add(note_text)
        self.body.addWidget(note)
        self.body.addStretch(1)

    def set_recent(self, paths: list[str]) -> None:
        _clear_layout(self.recent_layout)
        existing = [path for path in paths if Path(path).is_dir()]
        if not existing:
            self.recent_card.setVisible(False)
            return
        for path in existing:
            button = QPushButton(path)
            button.setObjectName("Link")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("text-align: left; padding: 7px 4px;")
            button.clicked.connect(lambda _=False, p=path: self.recent_requested.emit(p))
            self.recent_layout.addWidget(button)
        self.recent_card.setVisible(True)


# ----------------------------------------------------------------------
# 2. 添加分子
# ----------------------------------------------------------------------
class MoleculesPage(Page):
    add_requested = Signal(str)
    download_requested = Signal(str)
    files_dropped = Signal(str, list)
    remove_requested = Signal(str, str)
    locate_requested = Signal(str, str)
    back_requested = Signal()
    next_requested = Signal()
    change_workspace_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        change = QPushButton("更换工作目录")
        change.setObjectName("Link")
        change.clicked.connect(self.change_workspace_requested.emit)
        self.body.addWidget(
            _header_row(
                "第 2 步 · 添加分子",
                "先放入靶点结构，再加入候选分子",
                "拖放文件到虚线框，或从电脑中选择。程序会自动识别蛋白、核酸、多肽与小分子。",
                [change],
            )
        )

        columns_widget = QWidget()
        columns = QHBoxLayout(columns_widget)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(16)
        columns.addWidget(self._build_target_card(), 3)
        columns.addWidget(self._build_ligand_card(), 2)
        self.body.addWidget(columns_widget, alignment=Qt.AlignmentFlag.AlignTop)

        nav = QHBoxLayout()
        back = QPushButton("返回")
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        nav.addStretch(1)
        self.next_button = QPushButton("下一步：检查并准备")
        self.next_button.setObjectName("Primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_requested.emit)
        nav.addWidget(self.next_button)
        self.body.addLayout(nav)
        self.body.addStretch(1)

    def _build_target_card(self) -> Card:
        card = Card(
            "靶点结构",
            "蛋白、DNA/RNA 或多肽的 PDB/mmCIF 结构；小分子也可以作为靶点，"
            "用于分子–分子对接。蛋白–蛋白、蛋白–多肽等大分子配体对接不在本地支持范围内。",
        )

        zone = DropZone(
            "拖放靶点结构到这里",
            "支持 PDB、mmCIF、PDBQT、MOL2、SDF",
            "选择结构文件",
        )
        zone.activated.connect(lambda: self.add_requested.emit(KIND_TARGET))
        zone.files_dropped.connect(lambda paths: self.files_dropped.emit(KIND_TARGET, paths))
        card.add(zone)

        buttons = QHBoxLayout()
        download = QPushButton("从数据库下载")
        download.clicked.connect(lambda: self.download_requested.emit(KIND_TARGET))
        buttons.addWidget(download)
        buttons.addStretch(1)
        self.target_count = QLabel("0 个")
        self.target_count.setObjectName("Muted")
        buttons.addWidget(self.target_count)
        card.add_layout(buttons)

        self.target_list = QVBoxLayout()
        self.target_list.setSpacing(0)
        card.add_layout(self.target_list)
        self.target_empty = QLabel("还没有靶点。先添加一个蛋白、核酸或多肽结构。")
        self.target_empty.setObjectName("Muted")
        card.add(self.target_empty)
        card.body.addStretch(1)
        return card

    def _build_ligand_card(self) -> Card:
        card = Card(
            "候选分子",
            "支持 SDF、MOL2、MOL、SMILES、PDB 与 PDBQT（仅限小分子；"
            "蛋白或多肽请放在靶点一栏）。SDF 中含多个分子时，当前只准备第一个。",
        )

        zone = DropZone(
            "拖放候选分子到这里",
            "支持 SDF、MOL2、SMILES、PDB",
            "选择分子文件",
        )
        zone.activated.connect(lambda: self.add_requested.emit(KIND_LIGAND))
        zone.files_dropped.connect(lambda paths: self.files_dropped.emit(KIND_LIGAND, paths))
        card.add(zone)

        buttons = QHBoxLayout()
        download = QPushButton("从数据库下载")
        download.clicked.connect(lambda: self.download_requested.emit(KIND_LIGAND))
        buttons.addWidget(download)
        buttons.addStretch(1)
        self.ligand_count = QLabel("0 个")
        self.ligand_count.setObjectName("Muted")
        buttons.addWidget(self.ligand_count)
        card.add_layout(buttons)

        self.ligand_list = QVBoxLayout()
        self.ligand_list.setSpacing(0)
        card.add_layout(self.ligand_list)
        self.ligand_empty = QLabel("还没有候选分子。可以一次加入多个。")
        self.ligand_empty.setObjectName("Muted")
        card.add(self.ligand_empty)
        card.body.addStretch(1)
        return card

    def set_workspace(self, workspace: Workspace) -> None:
        self.refresh(workspace)

    def refresh(self, workspace: Workspace | None) -> None:
        for layout in (self.target_list, self.ligand_list):
            _clear_layout(layout)
        if workspace is None:
            self.target_empty.setVisible(True)
            self.ligand_empty.setVisible(True)
            self.next_button.setEnabled(False)
            return

        targets = workspace.targets
        ligands = workspace.ligands
        self.target_count.setText(f"{len(targets)} 个")
        self.ligand_count.setText(f"{len(ligands)} 个")
        self.target_empty.setVisible(not targets)
        self.ligand_empty.setVisible(not ligands)
        for entry in targets:
            row = EntityRow(entry, workspace=workspace)
            row.remove_requested.connect(lambda stem, k=KIND_TARGET: self.remove_requested.emit(k, stem))
            row.open_requested.connect(lambda stem, k=KIND_TARGET: self.locate_requested.emit(k, stem))
            self.target_list.addWidget(row)
        for entry in ligands:
            row = EntityRow(entry, workspace=workspace)
            row.remove_requested.connect(lambda stem, k=KIND_LIGAND: self.remove_requested.emit(k, stem))
            row.open_requested.connect(lambda stem, k=KIND_LIGAND: self.locate_requested.emit(k, stem))
            self.ligand_list.addWidget(row)
        self.next_button.setEnabled(bool(targets and ligands))


# ----------------------------------------------------------------------
# 3. 检查与准备
# ----------------------------------------------------------------------
class PreparePage(Page):
    prepare_requested = Signal(bool)
    cancel_requested = Signal()
    back_requested = Signal()
    next_requested = Signal()
    change_workspace_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        change = QPushButton("更换工作目录")
        change.setObjectName("Link")
        change.clicked.connect(self.change_workspace_requested.emit)
        self.body.addWidget(
            _header_row(
                "第 3 步 · 检查与准备",
                "把结构整理成可以对接的格式",
                "程序会补全缺失的原子、按生理 pH 加氢，并检查生成的文件是否可用。",
                [change],
            )
        )

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_targets = StatTile("靶点结构")
        self.tile_ligands = StatTile("候选分子")
        self.tile_ready = StatTile("已就绪", accent=True)
        tiles.addWidget(self.tile_targets)
        tiles.addWidget(self.tile_ligands)
        tiles.addWidget(self.tile_ready)
        self.body.addLayout(tiles)

        options = Card("准备选项")
        option_row = QHBoxLayout()
        option_row.setSpacing(16)
        ph_label = QLabel("生理 pH")
        ph_label.setObjectName("CardHint")
        self.ph_spin = QDoubleSpinBox()
        self.ph_spin.setRange(4.0, 9.0)
        self.ph_spin.setSingleStep(0.1)
        self.ph_spin.setDecimals(1)
        self.ph_spin.setValue(7.4)
        self.ph_spin.setMaximumWidth(110)
        _plain_number(self.ph_spin)
        self.force_check = QCheckBox("重新准备所有条目")
        self.force_check.toggled.connect(self._update_action_state)
        option_row.addWidget(ph_label)
        option_row.addWidget(self.ph_spin)
        option_row.addSpacing(8)
        option_row.addWidget(self.force_check)
        option_row.addStretch(1)
        options.add_layout(option_row)
        note = QLabel(
            "默认会移除结晶水、离子和添加剂，保留聚合物链；缺失的残基与原子会被补全。"
            "蛋白、多肽与核酸使用同一套修复流程。若候选分子被识别为蛋白质或多肽，"
            "程序会提示改用专门的大分子对接工具，而不是给出不可靠的 Vina 分数。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        options.add(note)
        self.body.addWidget(options)

        self.entries_card = Card("条目状态")
        self.entries_layout = QVBoxLayout()
        self.entries_layout.setSpacing(0)
        self.entries_card.add_layout(self.entries_layout)
        self.entries_empty = QLabel("还没有条目，请先返回上一步添加分子。")
        self.entries_empty.setObjectName("Muted")
        self.entries_card.add(self.entries_empty)
        self.body.addWidget(self.entries_card)

        self.log_panel = LogPanel()
        self.log_panel.setVisible(False)
        self.log_panel.cancel_requested.connect(self.cancel_requested.emit)
        self.body.addWidget(self.log_panel)

        nav = QHBoxLayout()
        back = QPushButton("返回")
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        nav.addStretch(1)
        self.prepare_button = QPushButton("开始准备")
        self.prepare_button.setObjectName("Primary")
        self.prepare_button.clicked.connect(
            lambda: self.prepare_requested.emit(self.force_check.isChecked())
        )
        nav.addWidget(self.prepare_button)
        self.next_button = QPushButton("下一步：设置对接")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_requested.emit)
        nav.addWidget(self.next_button)
        self.body.addLayout(nav)
        self.body.addStretch(1)
        self._ready_entries = 0
        self._total_entries = 0
        self._can_continue = False
        self._update_action_state()

    def set_workspace(self, workspace: Workspace) -> None:
        self.refresh(workspace)

    def refresh(self, workspace: Workspace | None) -> None:
        _clear_layout(self.entries_layout)
        if workspace is None:
            self.tile_targets.set_value("—")
            self.tile_ligands.set_value("—")
            self.tile_ready.set_value("—")
            self.entries_empty.setVisible(True)
            self.prepare_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self._ready_entries = self._total_entries = 0
            self._can_continue = False
            self._update_action_state()
            return
        targets = workspace.targets
        ligands = workspace.ligands
        entries = targets + ligands
        ready = [entry for entry in entries if entry.status == STATUS_READY]
        self.tile_targets.set_value(str(len(targets)))
        self.tile_ligands.set_value(str(len(ligands)))
        self.tile_ready.set_value(f"{len(ready)}/{len(entries)}")
        self.entries_empty.setVisible(not entries)
        for entry in entries:
            row = EntityRow(entry, show_actions=False, workspace=workspace)
            self.entries_layout.addWidget(row)
        self.prepare_button.setEnabled(bool(entries))
        ready_targets = [entry for entry in targets if entry.status == STATUS_READY]
        ready_ligands = [entry for entry in ligands if entry.status == STATUS_READY]
        self._can_continue = bool(ready_targets and ready_ligands)
        self.next_button.setEnabled(self._can_continue)
        self._ready_entries = len(ready)
        self._total_entries = len(entries)
        self._update_action_state()

    def _update_action_state(self) -> None:
        """全部就绪时把“下一步”设为主按钮，避免两个蓝色按钮抢注意力。"""
        all_ready = self._total_entries > 0 and self._ready_entries == self._total_entries
        prepare_is_primary = not all_ready or self.force_check.isChecked()
        self.prepare_button.setText("重新准备全部" if all_ready else "开始准备")
        self.prepare_button.setObjectName("Primary" if prepare_is_primary else "")
        self.next_button.setObjectName("" if prepare_is_primary else "Primary")
        for button in (self.prepare_button, self.next_button):
            _refresh_style(button)

    def start_running(self) -> None:
        self.log_panel.setVisible(True)
        self.log_panel.start("正在准备结构…")
        self.prepare_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.ph_spin.setEnabled(False)
        self.force_check.setEnabled(False)

    def finish_running(self, message: str, ok: bool) -> None:
        self.log_panel.finish(message, ok)
        self.prepare_button.setEnabled(True)
        # 准备失败时工作区状态可能没有变化，但“下一步”不能一直保持禁用。
        self.next_button.setEnabled(self._can_continue)
        self.ph_spin.setEnabled(True)
        self.force_check.setEnabled(True)


# ----------------------------------------------------------------------
# 4. 对接设置
# ----------------------------------------------------------------------
class DockPage(Page):
    box_requested = Signal(str)
    auto_boxes_requested = Signal()
    run_requested = Signal(dict)
    cancel_requested = Signal()
    back_requested = Signal()
    change_workspace_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._actions_enabled = True
        self._has_workspace = False
        self._run_ready = False
        self._row_buttons: list[QPushButton] = []
        change = QPushButton("更换工作目录")
        change.setObjectName("Link")
        change.clicked.connect(self.change_workspace_requested.emit)
        self.body.addWidget(
            _header_row(
                "第 4 步 · 对接设置",
                "告诉程序“在哪里找结合位置”",
                "盒子是搜索区域。软件会先用几何算法寻找蛋白、核酸或多肽表面的口袋，"
                "再用共晶配体或已知位点校验；小分子靶点按两侧分子尺寸估算；"
                "都无法可靠定位时使用全结构盲对接，并提示你手动设置中心与边长。"
                "不支持的组合会被跳过，不会产生分数。",
                [change],
            )
        )

        self.box_card = Card("每个靶点的搜索盒子")
        box_tools = QHBoxLayout()
        self.auto_button = QPushButton("为所有靶点自动生成盒子")
        self.auto_button.clicked.connect(self.auto_boxes_requested.emit)
        box_tools.addWidget(self.auto_button)
        self.cancel_button = QPushButton("取消计算")
        self.cancel_button.setObjectName("Small")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        box_tools.addWidget(self.cancel_button)
        box_tools.addStretch(1)
        self.box_card.add_layout(box_tools)
        self.box_rows = QVBoxLayout()
        self.box_rows.setSpacing(6)
        self.box_card.add_layout(self.box_rows)
        self.box_empty = QLabel("还没有准备好的靶点。")
        self.box_empty.setObjectName("Muted")
        self.box_card.add(self.box_empty)
        self.body.addWidget(self.box_card)

        params = Card("搜索设置", "标准设置适合多数蛋白–小分子对接。提高精细度会增加耗时。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("精细度"), 0, 0)
        self.exhaustiveness = QComboBox()
        self.exhaustiveness.addItem("快速（8）", 8)
        self.exhaustiveness.addItem("标准（16，推荐）", 16)
        self.exhaustiveness.addItem("精细（32）", 32)
        self.exhaustiveness.addItem("极致（64，较慢）", 64)
        self.exhaustiveness.setCurrentIndex(1)
        self.exhaustiveness.setMaximumWidth(240)
        grid.addWidget(self.exhaustiveness, 0, 1)
        grid.addWidget(QLabel("输出构象数"), 0, 2)
        self.num_modes = QSpinBox()
        self.num_modes.setRange(1, 20)
        self.num_modes.setValue(9)
        _plain_number(self.num_modes)
        self.num_modes.setMaximumWidth(120)
        grid.addWidget(self.num_modes, 0, 3)
        grid.addWidget(QLabel("随机种子"), 1, 0)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(42)
        _plain_number(self.seed)
        self.seed.setMaximumWidth(240)
        grid.addWidget(self.seed, 1, 1)
        grid.addWidget(QLabel("CPU 核心（0 = 自动）"), 1, 2)
        self.cpu = QSpinBox()
        self.cpu.setRange(0, 256)
        self.cpu.setValue(0)
        _plain_number(self.cpu)
        self.cpu.setMaximumWidth(120)
        grid.addWidget(self.cpu, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnMinimumWidth(0, 110)
        grid.setColumnMinimumWidth(2, 130)
        params.add_layout(grid)
        self.body.addWidget(params)

        self.summary_card = Card("本次任务")
        self.summary_label = QLabel("—")
        self.summary_label.setObjectName("CardHint")
        self.summary_label.setWordWrap(True)
        self.summary_card.add(self.summary_label)
        self.body.addWidget(self.summary_card)

        nav = QHBoxLayout()
        back = QPushButton("返回")
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        nav.addStretch(1)
        self.run_button = QPushButton("开始对接")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self._emit_run)
        nav.addWidget(self.run_button)
        self.body.addLayout(nav)
        self.body.addStretch(1)

    def _emit_run(self) -> None:
        self.run_requested.emit(
            {
                "exhaustiveness": int(self.exhaustiveness.currentData()),
                "num_modes": int(self.num_modes.value()),
                "seed": int(self.seed.value()),
                "cpu": int(self.cpu.value()),
            }
        )

    def set_busy(self, busy: bool) -> None:
        """任务运行期间禁用会启动新计算的按钮，并提供可见的取消入口。"""
        self._actions_enabled = not busy
        self.auto_button.setEnabled(not busy and self._has_workspace)
        self.run_button.setEnabled(not busy and self._run_ready)
        for button in self._row_buttons:
            button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)

    def set_workspace(self, workspace: Workspace) -> None:
        self.refresh(workspace)

    def refresh(self, workspace: Workspace | None) -> None:
        _clear_layout(self.box_rows)
        self._row_buttons = []
        if workspace is None:
            self._has_workspace = False
            self._run_ready = False
            self.box_empty.setVisible(True)
            self.summary_label.setText("—")
            self.auto_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        self._has_workspace = True
        self.auto_button.setEnabled(self._actions_enabled)
        ready_targets = [entry for entry in workspace.targets if entry.status == STATUS_READY]
        ready_ligands = [entry for entry in workspace.ligands if entry.status == STATUS_READY]
        supported = modes.supported_pairs(ready_targets, ready_ligands, workspace)
        runnable_targets = []
        seen: set[str] = set()
        for target, _ligand, _mode in supported:
            if target.stem not in seen:
                seen.add(target.stem)
                runnable_targets.append(target)
        self.box_empty.setVisible(not runnable_targets)
        mode_labels: dict[str, list[str]] = {}
        for target, _ligand, mode in supported:
            labels = mode_labels.setdefault(target.stem, [])
            if mode.label not in labels:
                labels.append(mode.label)
        for target in runnable_targets:
            self.box_rows.addWidget(
                self._box_row(workspace, target, mode_labels.get(target.stem, []))
            )

        pair_count = len(supported)
        total_pairs = len(ready_targets) * len(ready_ligands)
        unsupported_count = total_pairs - pair_count
        missing_boxes = sum(1 for target in runnable_targets if workspace.load_box(target.stem) is None)
        if pair_count:
            text = (
                f"共 {len(runnable_targets)} 个可运行靶点 × 对应候选分子 = {pair_count} 组对接。"
            )
            if unsupported_count:
                text += f"另有 {unsupported_count} 组属于不支持的对接类型，将不会运行。"
            review_boxes = sum(
                1
                for target in runnable_targets
                if (saved := workspace.load_box(target.stem)) is not None and saved.needs_review
            )
            if missing_boxes:
                text += f"其中 {missing_boxes} 个靶点还没有保存盒子，运行时会自动推荐。"
            if review_boxes:
                text += (
                    f" 另有 {review_boxes} 个靶点的自动盒子无法可靠定位"
                    "（盲对接或按分子尺寸估算），建议手动设置盒子。"
                )
            text += " 每组耗时从几十秒到几分钟不等，取决于分子大小和精细度。"
        elif total_pairs:
            text = (
                "当前所有组合都属于不支持的对接类型，无法运行。"
                "例如把蛋白质或多肽放进了候选分子，或使用了核酸–大分子组合；"
                "请检查结构类型，或改用 ClusPro、HADDOCK、RosettaDock 等专门工具。"
            )
        else:
            text = "还没有可运行的组合。请先在“检查与准备”中准备至少一个靶点和一个候选分子。"
        self.summary_label.setText(text)
        self._run_ready = pair_count > 0
        self.run_button.setEnabled(self._actions_enabled and self._run_ready)

    def _box_row(
        self,
        workspace: Workspace,
        target: ProjectFile,
        mode_labels: list[str] | None = None,
    ) -> QWidget:
        row = QFrame()
        row.setObjectName("BoxRow")
        row.setMinimumHeight(58)
        # 说明行可能折成两行（类型 + 盒子来源 + 手动确认提示），允许随内容增高。
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 8, 2, 8)
        layout.setSpacing(10)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name = QLabel(target.display_name)
        name.setStyleSheet("font-weight: 600;")
        name.setWordWrap(False)
        text_box.addWidget(name)
        parts = []
        kind_label = modes.KIND_LABELS.get(modes.target_kind_for(target, workspace), "")
        if kind_label:
            parts.append(kind_label)
        if mode_labels:
            parts.append("对接类型：" + "、".join(mode_labels))
        prefix = " · ".join(parts)
        box = workspace.load_box(target.stem)
        needs_review = False
        if box is None:
            box_text = "尚未设置盒子，运行时会自动推荐"
        else:
            size = " × ".join(f"{value:.0f}" for value in box.size)
            box_text = f"{box.source_label} · 盒子 {size} Å"
            if box.needs_review:
                box_text += " · 自动结果不可靠，请手动确认"
                needs_review = True
        summary = f"{prefix} · {box_text}" if prefix else box_text
        detail = QLabel(summary)
        detail.setObjectName("Warning" if needs_review else "Muted")
        detail.setWordWrap(True)
        text_box.addWidget(detail)
        layout.addLayout(text_box, 1)
        edit = QPushButton("设置盒子")
        edit.setObjectName("Small")
        edit.setEnabled(self._actions_enabled)
        edit.clicked.connect(lambda _=False, stem=target.stem: self.box_requested.emit(stem))
        self._row_buttons.append(edit)
        layout.addWidget(edit)
        return row


# ----------------------------------------------------------------------
# 5. 运行与结果
# ----------------------------------------------------------------------
class ResultsPage(Page):
    run_requested = Signal(dict)
    cancel_requested = Signal()
    report_requested = Signal()
    open_folder_requested = Signal()
    refresh_requested = Signal()
    open_pymol_requested = Signal(str, str)
    locate_result_requested = Signal(str, str)
    heatmap_edit_requested = Signal()
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._has_results = False
        self.refresh_button = QPushButton("刷新结果")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.folder_button = QPushButton("打开结果文件夹")
        self.folder_button.clicked.connect(self.open_folder_requested.emit)
        self.report_button = QPushButton("生成结果报告")
        self.report_button.setObjectName("Primary")
        self.report_button.clicked.connect(self.report_requested.emit)
        self.body.addWidget(
            _header_row(
                "第 5 步 · 运行与结果",
                "对接结果",
                "分数越低，表示该程序预测的结合越有利。不同对接类型的分数不宜直接比较；"
                "请结合实验或文献证据判断。",
                [self.refresh_button, self.folder_button, self.report_button],
            )
        )

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_best = StatTile("最佳打分 (kcal/mol)", "—", accent=True)
        self.tile_success = StatTile("成功 / 总任务", "—")
        self.tile_time = StatTile("总耗时", "—")
        tiles.addWidget(self.tile_best)
        tiles.addWidget(self.tile_success)
        tiles.addWidget(self.tile_time)
        self.body.addLayout(tiles)

        self.log_panel = LogPanel()
        self.log_panel.setVisible(False)
        self.log_panel.cancel_requested.connect(self.cancel_requested.emit)
        self.body.addWidget(self.log_panel)

        self.table_card = Card("结果列表")
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["靶点", "候选分子", "对接类型", "最佳打分", "盒子来源", "状态", "说明", "操作"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 92)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(7, 140)
        self.table.setMinimumHeight(120)
        self.table.setVisible(False)
        self.table.setToolTip("选中行后按 Ctrl+C 或右键复制，可直接粘贴到 Excel")
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        self._copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self._copy_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._copy_shortcut.activated.connect(self._copy_selected)
        self.table_card.add(self.table)
        self.mode_note = QLabel("")
        self.mode_note.setObjectName("Muted")
        self.mode_note.setWordWrap(True)
        self.mode_note.setVisible(False)
        self.table_card.add(self.mode_note)
        self.empty_label = QLabel("还没有结果。完成准备后，在上一步点击“开始对接”。")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        self.table_card.add(self.empty_label)
        self.body.addWidget(self.table_card)

        self.chart_card = Card("结果图表")
        self.chart_label = QLabel()
        self.chart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart_label.setMinimumHeight(220)
        self.chart_card.add(self.chart_label)
        self.chart_hint = QLabel("生成报告后会在这里显示结合能热图或排名图。")
        self.chart_hint.setObjectName("Muted")
        self.chart_hint.setWordWrap(True)
        self.chart_card.add(self.chart_hint)
        chart_actions = QHBoxLayout()
        self.heatmap_button = QPushButton("编辑热图")
        self.heatmap_button.setObjectName("Small")
        self.heatmap_button.setToolTip("修改靶点与候选分子的显示名称、标题、配色和导出格式")
        self.heatmap_button.setVisible(False)
        self.heatmap_button.clicked.connect(self.heatmap_edit_requested.emit)
        chart_actions.addWidget(self.heatmap_button)
        chart_actions.addStretch(1)
        self.chart_card.add_layout(chart_actions)
        self.chart_card.setVisible(False)
        self.body.addWidget(self.chart_card)

        nav = QHBoxLayout()
        back = QPushButton("返回设置")
        back.clicked.connect(self.back_requested.emit)
        nav.addWidget(back)
        nav.addStretch(1)
        self.body.addLayout(nav)
        self.body.addStretch(1)

    def set_busy(self, busy: bool) -> None:
        """任务运行期间禁用会启动新操作的按钮，避免点击后没有任何反应。"""
        self.refresh_button.setEnabled(not busy)
        self.folder_button.setEnabled(not busy)
        self.report_button.setEnabled(not busy and self._has_results)
        self.heatmap_button.setEnabled(not busy)

    # -- 结果表：复制与右键菜单 ----------------------------------------
    def _copy_selected(self) -> None:
        """把选中行复制为制表符分隔文本，便于粘贴到 Excel。"""
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            return
        headers = []
        for column in range(self.table.columnCount() - 1):  # 最后一列是操作按钮
            item = self.table.horizontalHeaderItem(column)
            headers.append(item.text() if item is not None else "")
        lines = ["\t".join(headers)]
        for row in rows:
            cells = []
            for column in range(self.table.columnCount() - 1):
                item = self.table.item(row, column)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _show_table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        selected_rows = {item.row() for item in self.table.selectedIndexes()}
        if index.isValid() and index.row() not in selected_rows:
            self.table.clearSelection()
            self.table.selectRow(index.row())
        menu = QMenu(self)
        copy_action = menu.addAction("复制选中行")
        copy_action.setEnabled(bool(self.table.selectedIndexes()))
        copy_action.triggered.connect(self._copy_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def start_running(self) -> None:
        self.log_panel.setVisible(True)
        self.log_panel.start("正在运行对接…")
        self.report_button.setEnabled(False)
        self.table.setRowCount(0)
        self.table.setVisible(False)
        self.empty_label.setVisible(False)
        self.chart_card.setVisible(False)

    def finish_running(self, message: str, ok: bool) -> None:
        self.log_panel.finish(message, ok)
        self.report_button.setEnabled(True)

    def set_workspace(self, workspace: Workspace | None) -> None:
        if workspace is None:
            self.show_results(None, [])
            return
        self.show_results(workspace, [])

    def show_results(self, workspace: Workspace | None, results: list[DockingResult]) -> None:
        self._has_results = bool(results)
        self.table.setRowCount(0)
        self.table.setVisible(False)
        if workspace is None:
            self.empty_label.setVisible(True)
            self.empty_label.setText("还没有打开工作目录。")
            self.tile_best.set_value("—")
            self.tile_success.set_value("—")
            self.tile_time.set_value("—")
            self.chart_card.setVisible(False)
            self.report_button.setEnabled(False)
            return

        successes = [item for item in results if item.ok and item.poses]
        total = len(results)
        best = min((item.poses[0].affinity for item in successes), default=None)
        self.tile_best.set_value(f"{best:.2f}" if best is not None else "—")
        self.tile_success.set_value(f"{len(successes)}/{total}" if total else "—")
        runtime = sum(item.runtime_seconds for item in results if item.runtime_seconds)
        self.tile_time.set_value(self._format_seconds(runtime) if runtime else "—")
        self.empty_label.setVisible(not results)
        self.report_button.setEnabled(bool(results))
        if not results:
            self.chart_card.setVisible(False)
            return

        self.table.setVisible(True)
        self._fit_table_height(len(results))
        target_labels = _display_map(workspace.targets)
        ligand_labels = _display_map(workspace.ligands)
        ordered = sorted(
            results,
            key=lambda item: (
                0 if item.ok and item.poses else 1,
                item.poses[0].affinity if item.poses else 0.0,
            ),
        )
        self.table.setRowCount(len(ordered))
        for row, result in enumerate(ordered):
            self.table.setItem(row, 0, QTableWidgetItem(target_labels.get(result.target, result.target)))
            self.table.setItem(row, 1, QTableWidgetItem(ligand_labels.get(result.ligand, result.ligand)))
            self.table.setItem(row, 2, QTableWidgetItem(result.mode_label or "—"))
            score = f"{result.poses[0].affinity:.2f}" if result.poses else "—"
            score_item = QTableWidgetItem(score)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, score_item)
            self.table.setItem(row, 4, QTableWidgetItem(
                {"pocket": "几何口袋", "cocrystal": "共晶配体", "site": "PDB 位点", "blind": "全结构盲对接",
                 "manual": "手动", "reference": "参考分子", "ligand": "目标分子"}.get(
                    result.box_source, result.box_source or "自动"
                )
            ))
            if not result.mode_supported:
                chip = StatusChip("error", "不支持")
            else:
                chip = StatusChip(
                    "success" if result.ok else "error",
                    "已有结果" if result.skipped else ("成功" if result.ok else "失败"),
                )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(6, 0, 0, 0)
            holder_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            holder_layout.addStretch(1)
            self.table.setCellWidget(row, 5, holder)
            note = result.error if not result.ok else "；".join(result.warnings)
            note_item = QTableWidgetItem(note or "—")
            note_item.setToolTip(note)
            self.table.setItem(row, 6, note_item)
            if result.ok and result.pymol_path:
                actions = QWidget()
                action_layout = QHBoxLayout(actions)
                action_layout.setContentsMargins(0, 0, 0, 0)
                action_layout.setSpacing(4)
                pymol = QPushButton("PyMOL")
                pymol.setObjectName("Small")
                pymol.setToolTip("用 PyMOL 打开复合物（需要已安装 PyMOL）")
                pymol.clicked.connect(
                    lambda _=False, t=result.target, l=result.ligand: self.open_pymol_requested.emit(t, l)
                )
                locate = QPushButton("定位")
                locate.setObjectName("Small")
                locate.clicked.connect(
                    lambda _=False, t=result.target, l=result.ligand: self.locate_result_requested.emit(t, l)
                )
                action_layout.addWidget(pymol)
                action_layout.addWidget(locate)
                self.table.setCellWidget(row, 7, actions)
        mode_labels = sorted({result.mode_label for result in results if result.ok and result.mode_label})
        if len(mode_labels) > 1:
            self.mode_note.setText(
                "本表包含多种对接类型（" + "、".join(mode_labels) + "）。"
                "Vina 分数适合在同一类型内部排序，不宜跨类型直接比较。"
            )
            self.mode_note.setVisible(True)
        else:
            self.mode_note.setVisible(False)
            self.mode_note.setText("")
        self._load_chart(workspace)

    def _fit_table_height(self, rows: int) -> None:
        section = self.table.verticalHeader().defaultSectionSize()
        height = self.table.horizontalHeader().height() + rows * section + 6
        self.table.setFixedHeight(max(120, min(height, 480)))

    def _load_chart(self, workspace: Workspace) -> None:
        heatmap = workspace.analysis_dir / "heatmap.png"
        bars = workspace.analysis_dir / "top_affinities.png"
        path = heatmap if heatmap.is_file() else (bars if bars.is_file() else None)
        if path is None:
            self.heatmap_button.setVisible(False)
            self.chart_card.setVisible(False)
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.heatmap_button.setVisible(False)
            self.chart_card.setVisible(False)
            return
        self.chart_label.setPixmap(pixmap.scaledToWidth(860, Qt.TransformationMode.SmoothTransformation))
        self.heatmap_button.setVisible(heatmap.is_file())
        if heatmap.is_file():
            self.chart_hint.setText(
                "图表已生成。可点“编辑热图”修改靶点名称、标题、配色与导出格式；"
                "应用后会重新生成报告。"
            )
        else:
            self.chart_hint.setText("图表已生成。完整报告包含评分明细、方法与参数。")
        self.chart_card.setVisible(True)

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        seconds = int(round(seconds))
        if seconds < 60:
            return f"{seconds} 秒"
        minutes, rest = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} 分 {rest} 秒"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
