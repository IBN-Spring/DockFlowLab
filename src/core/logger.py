#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 日志模块
"""
import sys
import time
import threading

_log_lock = threading.Lock()
_log_callback = None


def set_log_callback(callback):
    """设置外部日志回调（如 UI 显示）"""
    global _log_callback
    _log_callback = callback


def log(message, level="INFO", log_text=None):
    """
    统一日志函数

    Args:
        message: 日志内容
        level: 日志级别  INFO / SUCCESS / WARNING / ERROR / DEBUG
        log_text: 可选的 list，日志会追加到其中
    """
    level = level.upper()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    prefix = {
        "INFO": "[INFO]",
        "SUCCESS": "[OK]",
        "WARNING": "[WARN]",
        "ERROR": "[ERR]",
        "DEBUG": "[DBG]",
    }.get(level, "")
    formatted = f"[{timestamp}] {prefix} {message}"

    with _log_lock:
        try:
            sys.stdout.buffer.write((formatted + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            print(formatted)

    if log_text is not None and hasattr(log_text, "append"):
        log_text.append(f"[{level}] {message}")

    if _log_callback is not None:
        try:
            _log_callback(formatted)
        except Exception:
            pass


def get_log_callback():
    return _log_callback
