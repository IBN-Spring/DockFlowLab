"""应用程序入口。"""

from __future__ import annotations

import multiprocessing
import sys
import traceback

from .logging_setup import get_logger, setup_logging
from .version import __app_name__, __app_name_zh__, __version__

log = get_logger("app")


def _install_exception_hook() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical(
            "未捕获的异常：%s",
            exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
        )
        app = QApplication.instance()
        if app is not None:
            details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("程序遇到问题")
            box.setText("发生了一个未预期的错误，界面可能无法继续操作。")
            box.setInformativeText("可以复制下方技术信息用于排查，或重启程序。")
            box.setDetailedText(details)
            box.exec()

    sys.excepthook = hook


def gui_main(argv: list[str] | None = None) -> int:
    """启动图形界面。"""
    multiprocessing.freeze_support()
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    setup_logging()
    _install_exception_hook()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name_zh__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("DockFlowLab")
    QLocale.setDefault(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    translator = QTranslator(app)
    translations = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_zh_CN", translations):
        app.installTranslator(translator)
    apply_theme(app)
    window = MainWindow()
    window.show()
    log.info("启动 DockFlowLab %s", __version__)
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    return gui_main(argv)


def cli_main() -> int:
    from .cli import main as cli

    return cli()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
