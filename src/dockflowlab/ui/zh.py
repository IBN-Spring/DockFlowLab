"""Qt 标准按钮的中文文案。

conda 版 PySide6 不附带 ``qtbase_zh_CN.qm``，系统对话框里的 Yes/No/OK
会显示英文。这里在控件显示前统一改写标准按钮文字，保证界面语言一致。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

# 按枚举数值索引：QMessageBox.StandardButton 与 QDialogButtonBox.StandardButton
# 是两套独立的 PySide6 枚举，彼此不相等，也不能直接互相传参。
_TEXTS = {
    QDialogButtonBox.StandardButton.Ok.value: "确定",
    QDialogButtonBox.StandardButton.Open.value: "打开",
    QDialogButtonBox.StandardButton.Save.value: "保存",
    QDialogButtonBox.StandardButton.Cancel.value: "取消",
    QDialogButtonBox.StandardButton.Close.value: "关闭",
    QDialogButtonBox.StandardButton.Yes.value: "是",
    QDialogButtonBox.StandardButton.No.value: "否",
    QDialogButtonBox.StandardButton.Abort.value: "中止",
    QDialogButtonBox.StandardButton.Retry.value: "重试",
    QDialogButtonBox.StandardButton.Ignore.value: "忽略",
    QDialogButtonBox.StandardButton.Help.value: "帮助",
    QDialogButtonBox.StandardButton.Apply.value: "应用",
    QDialogButtonBox.StandardButton.Reset.value: "重置",
    QDialogButtonBox.StandardButton.RestoreDefaults.value: "恢复默认",
    QDialogButtonBox.StandardButton.Discard.value: "放弃",
}


class ChineseButtonFilter(QObject):
    """在对话框显示时把标准按钮改为中文。"""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, (QMessageBox, QDialogButtonBox)):
                for button in obj.buttons():
                    standard = obj.standardButton(button)
                    text = _TEXTS.get(getattr(standard, "value", None))
                    if text:
                        button.setText(text)
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
