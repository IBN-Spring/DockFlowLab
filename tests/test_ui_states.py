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


def test_render_abort_clears_busy_state(qt_app):
    from dockflowlab.ui.render_page import RenderPage

    page = RenderPage()
    page._busy = True

    page.abort("渲染失败：测试")

    assert page._busy is False
