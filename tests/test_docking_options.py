"""对接参数的默认值。"""

from __future__ import annotations

from dockflowlab.services.docking import DockingOptions


def test_docking_has_generous_default_timeout():
    """避免 Vina 进程卡死时任务无限期挂起；6 小时远高于常规单组计算。"""
    options = DockingOptions()
    assert options.timeout_seconds == 6 * 3600.0


def test_settings_keep_default_timeout():
    from dockflowlab.config import AppSettings

    options = DockingOptions.from_settings(AppSettings())
    assert options.timeout_seconds == 6 * 3600.0
