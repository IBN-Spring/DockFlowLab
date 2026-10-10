#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline —— 组合流程入口
"""
from .logger import log
from .utils import initialize_directories, get_conda_env
from .downloader import download_ligands_from_list, download_receptors_from_list
from .receptor_processor import process_all_receptors
from .ligand_processor import process_all_ligands
from .box_generator import generate_all_docking_boxes
from .config import CONFIG
from .docking import run_docking, run_lklight_docking, convert_pdbqt_to_pdb
from .analyzer import analyze_results
from .pymol_generator import generate_all_pymol_scripts


def run_full_pipeline(log_text=None):
    """执行完整对接流程"""
    log("=== 启动完整对接流程 ===", log_text=log_text)
    env = get_conda_env()

    is_lklight = CONFIG.get("docking_engine", "") == "LKlight"

    if is_lklight:
        steps = [
            ("初始化目录", lambda: initialize_directories(log_text)),
            ("下载配体", lambda: download_ligands_from_list(log_text=log_text)),
            ("下载受体", lambda: download_receptors_from_list(log_text=log_text)),
            ("预处理受体", lambda: process_all_receptors(env, log_text)),
            ("预处理配体", lambda: process_all_ligands(log_text)),
            ("LKlight蛋白-蛋白对接", lambda: run_lklight_docking(env, log_text)),
            ("分析结果", lambda: analyze_results(log_text)),
            ("生成PyMOL脚本", lambda: generate_all_pymol_scripts(log_text)),
        ]
    else:
        steps = [
            ("初始化目录", lambda: initialize_directories(log_text)),
            ("下载配体", lambda: download_ligands_from_list(log_text=log_text)),
            ("下载受体", lambda: download_receptors_from_list(log_text=log_text)),
            ("预处理受体", lambda: process_all_receptors(env, log_text)),
            ("预处理配体", lambda: process_all_ligands(log_text)),
            ("生成对接盒子", lambda: generate_all_docking_boxes(log_text=log_text)),
            ("执行分子对接", lambda: run_docking(env, log_text)),
            ("转换结果格式", lambda: convert_pdbqt_to_pdb(env, log_text)),
            ("分析结果", lambda: analyze_results(log_text)),
            ("生成PyMOL脚本", lambda: generate_all_pymol_scripts(log_text)),
        ]

    for name, func in steps:
        log(f"\n>>> {name}", log_text=log_text)
        try:
            func()
        except Exception as e:
            log(f"{name} 出错: {e}", "ERROR", log_text)

    log("=== 完整流程结束 ===", log_text=log_text)
    return True


def run_preprocessing_only(log_text=None):
    """仅执行预处理"""
    env = get_conda_env()
    initialize_directories(log_text)
    process_all_receptors(env, log_text)
    process_all_ligands(log_text)
    return True


def run_docking_only(log_text=None):
    """仅执行对接（Vina 或 LKlight）"""
    env = get_conda_env()
    if CONFIG.get("docking_engine", "") == "LKlight":
        run_lklight_docking(env, log_text)
    else:
        generate_all_docking_boxes(log_text=log_text)
        run_docking(env, log_text)
    return True


def run_analysis_only(log_text=None):
    """仅执行分析"""
    env = get_conda_env()
    convert_pdbqt_to_pdb(env, log_text)
    analyze_results(log_text)
    generate_all_pymol_scripts(log_text)
    return True
