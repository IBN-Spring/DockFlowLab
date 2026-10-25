"""任务状态切换：失败后恢复控件、运行中禁用操作、提供取消入口。"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as exc:  # pragma: no cover - 无图形环境的 CI
            pytest.skip(f"无法创建 QApplication：{exc}")
    return app


def test_prepare_failure_restores_next_button(qt_app):
    from dockflowlab.ui.pages import PreparePage

    page = PreparePage()
    page._can_continue = True
    page.start_running()
    assert not page.next_button.isEnabled()

    page.finish_running("准备失败：测试", False)

    assert page.next_button.isEnabled()


def test_prepare_failure_keeps_next_disabled_without_pairs(qt_app):
    from dockflowlab.ui.pages import PreparePage

    page = PreparePage()
    page._can_continue = False
    page.start_running()
    page.finish_running("准备失败：测试", False)

    assert not page.next_button.isEnabled()


def test_results_busy_disables_header_actions(qt_app):
    from dockflowlab.ui.pages import ResultsPage

    page = ResultsPage()
    page._has_results = True
    page.set_busy(True)

    assert not page.refresh_button.isEnabled()
    assert not page.folder_button.isEnabled()
    assert not page.report_button.isEnabled()

    page.set_busy(False)

    assert page.refresh_button.isEnabled()
    assert page.folder_button.isEnabled()
    assert page.report_button.isEnabled()


def test_results_offer_interaction_details(qt_app, tmp_path):
    from PySide6.QtWidgets import QPushButton

    from dockflowlab.core.models import DockingResult, Pose
    from dockflowlab.ui.pages import ResultsPage
    from dockflowlab.workspace import Workspace

    workspace = Workspace.create(tmp_path / "结果")
    result = DockingResult(
        target="target",
        ligand="ligand",
        ok=True,
        poses=[Pose(rank=1, affinity=-7.5)],
        pymol_path="demo.pml",
        complex_path="demo.pdb",
    )
    page = ResultsPage()
    page.show_results(workspace, [result])

    actions = page.table.cellWidget(0, 7)
    assert actions is not None
    buttons = {button.text(): button for button in actions.findChildren(QPushButton)}
    assert set(buttons) == {"细节", "PyMOL", "定位"}

    emitted: list[tuple[str, str]] = []
    page.interaction_requested.connect(lambda t, l: emitted.append((t, l)))
    buttons["细节"].click()

    assert emitted == [("target", "ligand")]


def test_dock_page_offers_cancel_while_busy(qt_app):
    from dockflowlab.ui.pages import DockPage

    page = DockPage()
    page.set_busy(True)

    assert not page.run_button.isEnabled()
    assert not page.auto_button.isEnabled()
    assert not page.cancel_button.isHidden()

    page.set_busy(False)

    assert page.cancel_button.isHidden()


def test_dock_page_engine_choices_and_run_payload(qt_app):
    from PySide6.QtCore import Qt

    from dockflowlab.ui.pages import DockPage

    page = DockPage()
    page.set_engine_choices(
        [
            ("vina", "AutoDock Vina", True, "标准引擎"),
            ("qvina2", "QuickVina 2", False, "加速版"),
        ],
        selected="qvina2",
    )

    assert page.engine.count() == 2
    assert page.engine.currentData() == "qvina2"
    assert "未找到" in page.engine.itemText(1)
    assert page.engine.itemData(1) == "qvina2"
    assert "加速版" in page.engine.itemData(1, Qt.ItemDataRole.ToolTipRole)

    emitted: list[dict] = []
    page.run_requested.connect(emitted.append)
    page._emit_run()
    assert emitted[0]["engine"] == "qvina2"


def test_entity_row_shows_prep_warnings(qt_app):
    from PySide6.QtWidgets import QLabel

    from dockflowlab.core.models import ProjectFile
    from dockflowlab.ui.widgets import EntityRow

    entry = ProjectFile(
        kind="target",
        stem="t",
        display_name="靶点",
        source="x.pdb",
        status="ready",
        meta={"prep_warnings": ["补全了 12 个缺失残基", "未使用 PDBFixer"]},
    )
    row = EntityRow(entry, show_actions=False)

    texts = [label.text() for label in row.findChildren(QLabel)]
    assert any("补全了 12 个缺失残基" in text and "另有 1 条" in text for text in texts)


def test_fit_window_respects_screen(qt_app):
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from dockflowlab.ui.geometry import available_geometry, fit_window_to_screen

    widget = QWidget()
    area = available_geometry(widget)
    if area is None:
        pytest.skip("没有可用的图形屏幕")

    size = fit_window_to_screen(
        widget, preferred=QSize(6000, 4000), minimum=QSize(3000, 2000)
    )

    assert size.width() <= area.width()
    assert size.height() <= area.height()
    assert widget.minimumWidth() <= area.width()
    assert widget.minimumHeight() <= area.height()
    widget.deleteLater()


def test_ui_scale_is_clamped(qt_app):
    from dockflowlab.ui import theme

    original = theme.ui_scale()
    try:
        theme.set_ui_scale(5.0)
        assert theme.ui_scale() == pytest.approx(theme.MAX_UI_SCALE)
        assert theme.px(14) == f"{round(14 * theme.MAX_UI_SCALE)}px"

        theme.set_ui_scale(0.2)
        assert theme.ui_scale() == pytest.approx(theme.MIN_UI_SCALE)
        assert theme.px(14) == "14px"
    finally:
        theme.set_ui_scale(original)


def test_ui_scale_env_override(monkeypatch, qt_app):
    from dockflowlab.ui import theme

    monkeypatch.setenv("DOCKFLOWLAB_UI_SCALE", "1.25")
    assert theme.detect_ui_scale() == pytest.approx(1.25)

    monkeypatch.setenv("DOCKFLOWLAB_UI_SCALE", "not-a-number")
    assert theme.MIN_UI_SCALE <= theme.detect_ui_scale() <= theme.MAX_UI_SCALE


def test_qss_fonts_follow_ui_scale(qt_app):
    from dockflowlab.ui import theme

    original = theme.ui_scale()
    try:
        theme.set_ui_scale(1.3)
        scaled = theme._scale_qss_fonts("QLabel#PageTitle { font-size: 20px; }")
        assert "font-size: 26px" in scaled
    finally:
        theme.set_ui_scale(original)


def test_window_geometry_roundtrip(qt_app):
    from PySide6.QtWidgets import QWidget

    from dockflowlab.ui.geometry import restore_window_geometry, save_window_geometry

    widget = QWidget()
    widget.resize(640, 480)
    widget.move(120, 90)
    saved = save_window_geometry(widget)

    assert saved
    assert restore_window_geometry(widget, saved) is True
    assert restore_window_geometry(widget, "%%%") is False
    widget.deleteLater()


def test_render_abort_clears_busy_state(qt_app):
    from dockflowlab.ui.render_page import RenderPage

    page = RenderPage()
    page._busy = True

    page.abort("渲染失败：测试")

    assert page._busy is False
