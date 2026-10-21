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


def test_dock_page_offers_cancel_while_busy(qt_app):
    from dockflowlab.ui.pages import DockPage

    page = DockPage()
    page.set_busy(True)

    assert not page.run_button.isEnabled()
    assert not page.auto_button.isEnabled()
    assert not page.cancel_button.isHidden()

    page.set_busy(False)

    assert page.cancel_button.isHidden()


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
