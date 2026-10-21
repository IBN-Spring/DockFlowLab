"""Qt 标准按钮中文化。"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from dockflowlab.ui.zh import install_chinese_buttons


def test_chinese_filter_localises_message_box_buttons():
    """QMessageBox 与 QDialogButtonBox 的枚举不通用，不能把前者传给 button()。"""
    app = QApplication.instance() or QApplication([])
    event_filter = install_chinese_buttons(app)

    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    event_filter.eventFilter(box, QEvent(QEvent.Type.Show))

    assert box.button(QMessageBox.StandardButton.Ok).text() == "确定"
    assert box.button(QMessageBox.StandardButton.Cancel).text() == "取消"


def test_chinese_filter_localises_dialog_button_box():
    app = QApplication.instance() or QApplication([])
    event_filter = install_chinese_buttons(app)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
    )
    event_filter.eventFilter(buttons, QEvent(QEvent.Type.Show))

    assert buttons.button(QDialogButtonBox.StandardButton.Yes).text() == "是"
    assert buttons.button(QDialogButtonBox.StandardButton.No).text() == "否"
