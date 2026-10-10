#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 核心配置模块
"""
import os
import platform
import multiprocessing as mp

from src.utils.path import get_path

# ============== 全局配置 ==============
CONFIG = {
    # 目录配置
    "1.原始受体": get_path("1.原始受体"),
    "2.原始配体": get_path("2.原始配体"),
    "3.受体": get_path("3.受体"),
    "4.配体": get_path("4.配体"),
    "5.临时文件": get_path("5.临时文件"),
    "6.对接结果": get_path("6.对接结果"),
    "7.分析结果": get_path("7.分析结果"),
    "8.转换PDB": get_path("8.转换PDB"),
    "9.PyMOL": get_path("9.PyMOL"),
    "10.实验报告": get_path("10.实验报告"),

    # 可执行文件
    "vina_path": get_path("vina.exe" if platform.system() == "Windows" else "vina"),

    # 对接引擎配置（engine/ 目录下的加速引擎）
    "docking_engine": "Vina-CPU",  # 固定使用 Vina (CPU) 引擎
    "docking_engines": {
        "Vina-CPU": {
            "path": get_path("engine/vina.exe"),
            "name": "Vina (CPU)",
            "description": "标准 AutoDock Vina，CPU 运算",
            "gpu": False,
            "supports_exhaustiveness": True,
            "supports_virtual_screening": False,
        },
        "Vina-GPU": {
            "path": get_path("engine/Vina-GPU/Vina-GPU.exe"),
            "name": "Vina-GPU",
            "description": "GPU 加速版 Vina，速度提升 10-50 倍",
            "gpu": True,
            "supports_exhaustiveness": False,
            "supports_virtual_screening": False,
        },
        "Vina-GPU+": {
            "path": get_path("engine/Vina-GPU+/Vina-GPU+.exe"),
            "name": "Vina-GPU+",
            "description": "GPU 加速版 Vina，支持批量虚拟筛选 (--ligand_directory)",
            "gpu": True,
            "supports_exhaustiveness": False,
            "supports_virtual_screening": True,
            "thread": 8000,       # 并行通道数
            "search_depth": 20,   # 蒙特卡洛搜索深度
        },
        "Qvina2-GPU": {
            "path": get_path("engine/Qvina2_GPU/Qvina2_GPU.exe"),
            "name": "QuickVina 2 (GPU)",
            "description": "QuickVina 2 GPU 版，快速全局搜索",
            "gpu": True,
            "supports_exhaustiveness": False,
            "supports_virtual_screening": False,
        },
        "QVina-W-GPU": {
            "path": get_path("engine/QVina-W-GPU/QVina-W-GPU.exe"),
            "name": "QuickVina-W (GPU)",
            "description": "QuickVina-W GPU 版，支持 --exhaustiveness 全局搜索深度",
            "gpu": True,
            "supports_exhaustiveness": True,
            "supports_virtual_screening": False,
        },
        "LKlight": {
            "path": get_path("engine/LKlight/LKlight.exe"),
            "name": "LKlight",
            "description": "蛋白-蛋白对接引擎，GSO 算法，12 类评分函数，rayon 并行",
            "gpu": False,
            "supports_exhaustiveness": False,
            "supports_virtual_screening": False,
            "engine_type": "lklight",
        },
    },

    # LKlight 蛋白-蛋白对接参数
    "lklight_method": "pydock",       # 评分方法: dfire/fastdfire/dfire2/dna/mj3h/pydock/cpydock/sd/vdw/pisa/sipper/tobi/ddna
    "lklight_swarms": 25,             # swarm 数量
    "lklight_glowworms": 200,         # 每 swarm 萤火虫数量
    "lklight_steps": 100,             # 优化步数
    "lklight_anm": False,             # 是否开启 ANM 柔性对接
    "lklight_threads": 0,             # 并行线程数 (0=自动)

    # 对接参数
    "box_padding": 8.0,
    "box_size_range": [20.0, 100.0],
    "box_coverage": 1.0,
    "exhaustiveness": 32,
    "energy_range": 3,
    "num_modes": 10,
    "simultaneous_docking_pairs": 1,
    "max_workers": max(1, mp.cpu_count() - 1),
    "limit_volume": False,

    # 下载配置
    "max_retries": 3,
    "retry_delay": 5,
    "timeout": 30,

    # Conda环境
    "conda_path": None,
    "conda_env": "DockFlowLab",

    # PyMOL 设置
    "pymol_settings": {},
}


def get_config(key, default=None):
    """获取配置项"""
    return CONFIG.get(key, default)


def set_config(key, value):
    """设置配置项"""
    CONFIG[key] = value


def update_config(updates: dict):
    """批量更新配置"""
    CONFIG.update(updates)
