#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
import multiprocessing

# PyInstaller 多进程支持（必须在最前面）
multiprocessing.freeze_support()

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# PyQt5 平台插件路径
try:
    import PyQt5
    _qt_dir = os.path.dirname(PyQt5.__file__)
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(_qt_dir, 'Qt5', 'plugins', 'platforms')
except Exception:
    pass

from src.utils.path import get_path


def receptor_worker_main():
    import json
    import traceback
    debug_base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    debug_file = os.path.join(debug_base, 'receptor_worker_debug.log')
    plugin_dir = os.path.join(debug_base, '_internal', 'Library', 'lib', 'plugins') if getattr(sys, 'frozen', False) else ''
    if plugin_dir and os.path.isdir(plugin_dir):
        os.environ['OPENMM_PLUGIN_DIR'] = plugin_dir

    def dbg(msg):
        try:
            with open(debug_file, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')
        except Exception:
            pass

    try:
        with open(debug_file, 'w', encoding='utf-8') as f:
            f.write('')
    except Exception:
        pass

    try:
        dbg(f'worker:OPENMM_PLUGIN_DIR={os.environ.get("OPENMM_PLUGIN_DIR", "")}')
        dbg('worker:start')
        dbg(f'worker:argv={sys.argv}')
        from src.core.receptor_processor import process_one_receptor
        dbg('worker:imported process_one_receptor')
        env = json.loads(sys.argv[2])
        receptor_file = sys.argv[3]
        dbg(f'worker:loaded args receptor={receptor_file}')
        result = process_one_receptor(receptor_file, env)
        dbg('worker:process_one_receptor returned')
        print(json.dumps(result, ensure_ascii=False))
        dbg('worker:printed result')
    except Exception:
        dbg('worker:exception')
        dbg(traceback.format_exc())
        raise


def main():
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from src.Authentication.Authetication import LicenseGuard
    from src.Authentication.AutheticationUI import ActivationWindow, InitializationScreen

    app = QApplication(sys.argv)
    app.setApplicationName("DockFlowLab")
    app.setStyle("Fusion")

    # 设置应用图标
    from PyQt5.QtGui import QIcon
    icon_path = get_path("logo.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # 显示初始化画面
    init_screen = InitializationScreen()
    app.processEvents()

    # 验证许可证
    guard = LicenseGuard()
    is_valid, msg, _ = guard.verify_license()

    init_screen.close()

    if not is_valid:
        # 未激活：显示激活窗口，关闭后退出
        activation = ActivationWindow(guard)
        activation.exec_()

        # 用户输入激活码后重新验证
        is_valid, msg, _ = guard.verify_license()
        if not is_valid:
            QMessageBox.warning(None, "验证失败", msg)
            sys.exit(1)

    # 验证通过，进入主界面
    from src.ui import DockingMainWindow
    window = DockingMainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--receptor-worker":
        receptor_worker_main()
    else:
        main()
