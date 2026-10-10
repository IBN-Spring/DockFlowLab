#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 核心工具函数
"""
import os
import sys
import platform
import subprocess

from .config import CONFIG
from .logger import log


def get_runtime_python() -> str:
    """返回软件内置 runtime 的 Python 解释器路径"""
    from src.utils.path import get_path
    candidates = [
        get_path("runtime/python.exe"),
        get_path("runtime/python"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"未找到内置 runtime Python: {candidates}")


def get_conda_python() -> str:
    """返回 conda 环境中的 Python 解释器路径"""
    env_name = CONFIG.get("conda_env", "DockFlowLab")
    candidates = [
        os.path.join(os.path.expanduser("~"), "miniconda3", "envs", env_name, "python.exe"),
        os.path.join(os.path.expanduser("~"), "anaconda3", "envs", env_name, "python.exe"),
        os.path.join("C:\\", "ProgramData", "miniconda3", "envs", env_name, "python.exe"),
        os.path.join("C:\\", "miniconda3", "envs", env_name, "python.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"未找到 conda Python: 查找了 {candidates}")


def get_tool_path(name: str) -> str:
    """返回打包工具的绝对路径，优先使用系统安装版本"""
    from src.utils.path import get_path

    # obabel.exe 优先使用系统安装版本（避免 DLL 兼容问题）
    if name == "obabel.exe":
        system_paths = [
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "OpenBabel-3.1.1", "obabel.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "OpenBabel-3.1.1", "obabel.exe"),
        ]
        for sp in system_paths:
            if os.path.isfile(sp):
                return sp

    bundled = get_path(f"tools/{name}")
    if os.path.isfile(bundled):
        return bundled
    raise FileNotFoundError(f"未找到内置工具: {bundled}")


def ensure_dir(path, log_text=None):
    """确保目录存在"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        if log_text is not None:
            log(f"创建目录: {path}", log_text=log_text)
    return path


def run_command(cmd, env=None, capture_output=False):
    """执行系统命令"""
    try:
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            import shlex
            if isinstance(cmd, str):
                normalized = cmd.replace("2>&1", "").strip()
                cmd_list = shlex.split(normalized, posix=False)
            else:
                cmd_list = list(cmd)
            cmd_list = [arg[1:-1] if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"' else arg for arg in cmd_list]
            log(f"Windows执行参数: {cmd_list}", "DEBUG")
            result = subprocess.run(
                cmd_list, shell=False, env=env,
                capture_output=True, text=True,
                encoding='utf-8', errors='ignore',
                timeout=120,
                startupinfo=si,
            )
            log(f"命令返回码: {result.returncode}", "DEBUG")
            if result.stdout:
                log(f"STDOUT片段: {result.stdout[:500]}", "DEBUG")
            if result.stderr:
                log(f"STDERR片段: {result.stderr[:500]}", "DEBUG")
        else:
            result = subprocess.run(
                cmd, shell=True, env=env,
                capture_output=True, text=True,
                timeout=120,
            )
        if capture_output:
            return result.stdout + result.stderr, result.returncode == 0
        return result.returncode == 0
    except Exception as e:
        log(f"命令执行异常: {str(e)}", "WARNING")
        return ("", False) if capture_output else False


def get_conda_env(log_text=None):
    """获取环境变量（含 OpenBabel 数据目录和 tools/ 路径配置）"""
    env = os.environ.copy()

    # 设置 BABEL_DATADIR（只用内置数据目录）
    from src.utils.path import get_path
    babel_data = get_path("openbabel_data")
    if os.path.isdir(babel_data):
        env["BABEL_DATADIR"] = babel_data

    # 把 tools/ 目录加入 PATH，并作为 OpenBabel 插件目录
    tools_dir = get_path("tools")
    if os.path.isdir(tools_dir):
        env["PATH"] = tools_dir + os.pathsep + env.get("PATH", "")
        env["BABEL_LIBDIR"] = tools_dir

    return env


def initialize_directories(log_text=None):
    """初始化所有工作目录"""
    dir_keys = [
        "1.原始受体", "2.原始配体", "3.受体", "4.配体",
        "5.临时文件", "6.对接结果", "7.分析结果",
        "8.转换PDB", "9.PyMOL", "10.实验报告",
    ]
    for key in dir_keys:
        if key in CONFIG:
            os.makedirs(CONFIG[key], exist_ok=True)
            log(f"目录已就绪: {CONFIG[key]}", log_text=log_text)
    return True
