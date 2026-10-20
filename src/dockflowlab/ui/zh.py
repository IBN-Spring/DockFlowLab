"""Qt 标准按钮的中文文案。

conda 版 PySide6 不附带 ``qtbase_zh_CN.qm``，系统对话框里的 Yes/No/OK
会显示英文。这里在控件显示前统一改写标准按钮文字，保证界面语言一致。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

_TEXTS = {
    QDialogButtonBox.StandardButton.Ok: "确定",
    QDialogButtonBox.StandardButton.Open: "打开",
    QDialogButtonBox.StandardButton.Save: "保存",
    QDialogButtonBox.StandardButton.Cancel: "取消",
    QDialogButtonBox.StandardButton.Close: "关闭",
    QDialogButtonBox.StandardButton.Yes: "是",
    QDialogButtonBox.StandardButton.No: "否",
    QDialogButtonBox.StandardButton.Abort: "中止",
    QDialogButtonBox.StandardButton.Retry: "重试",
    QDialogButtonBox.StandardButton.Ignore: "忽略",
    QDialogButtonBox.StandardButton.Help: "帮助",
    QDialogButtonBox.StandardButton.Apply: "应用",
    QDialogButtonBox.StandardButton.Reset: "重置",
    QDialogButtonBox.StandardButton.RestoreDefaults: "恢复默认",
    QDialogButtonBox.StandardButton.Discard: "放弃",
}


class ChineseButtonFilter(QObject):
    """在对话框显示时把标准按钮改为中文。"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, QMessageBox):
                for standard, text in _TEXTS.items():
                    button = obj.button(standard)
                    if button is not None:
                        button.setText(text)
            elif isinstance(obj, QDialogButtonBox):
                for button in obj.buttons():
                    standard = obj.standardButton(button)
                    if standard in _TEXTS:
                        button.setText(_TEXTS[standard])
        return super().eventFilter(obj, event)


def install_chinese_buttons(app: QApplication) -> ChineseButtonFilter:
    """安装全局按钮中文化过滤器。"""
    existing = getattr(app, "_dockflowlab_zh_buttons", None)
    if isinstance(existing, ChineseButtonFilter):
        return existing
    event_filter = ChineseButtonFilter(app)
    app.installEventFilter(event_filter)
    app._dockflowlab_zh_buttons = event_filter  # type: ignore[attr-defined]
    return event_filter
