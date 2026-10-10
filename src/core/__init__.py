#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 核心功能模块
"""

# ── 基础模块（必须可用）──
from .config import CONFIG, get_config, set_config, update_config
from .logger import log, set_log_callback
from .utils import ensure_dir, run_command, get_conda_env, initialize_directories, get_tool_path, get_runtime_python
from .downloader import Downloader as MoleculeDownloader, download_ligands_from_list, download_receptors_from_list

__all__ = [
    "CONFIG", "get_config", "set_config", "update_config",
    "log", "set_log_callback",
    "ensure_dir", "run_command", "get_conda_env", "initialize_directories",
    "MoleculeDownloader", "download_ligands_from_list", "download_receptors_from_list",
]

# ── 可选模块（文件尚在重构中，缺失时不影响启动）──
try:
    from .receptor_processor import process_one_receptor, process_all_receptors
    __all__ += ["process_one_receptor", "process_all_receptors"]
except ImportError:
    pass

try:
    from .ligand_processor import process_one_ligand, process_all_ligands
    __all__ += ["process_one_ligand", "process_all_ligands"]
except ImportError:
    pass

try:
    from .box_generator import calculate_docking_box, process_single_receptor_box, run_box_generation as generate_all_docking_boxes
    __all__ += ["calculate_docking_box", "process_single_receptor_box", "generate_all_docking_boxes"]
except ImportError:
    pass

try:
    from .docking import validate_ligand_pdbqt, run_single_docking, run_docking, \
        run_lklight_pipeline, run_lklight_docking
    __all__ += ["validate_ligand_pdbqt", "run_single_docking", "run_docking",
                "run_lklight_pipeline", "run_lklight_docking"]
except ImportError:
    pass

try:
    from .docking import convert_pdbqt_to_pdb_preserve_conformation as convert_pdbqt_to_pdb
    __all__ += ["convert_pdbqt_to_pdb"]
except ImportError:
    pass

try:
    from .analyzer import analyze_results, evaluate_docking_results
    __all__ += ["analyze_results", "evaluate_docking_results"]
except ImportError:
    pass

try:
    from .pymol_generator import generate_pymol_script, generate_all_pymol_scripts
    __all__ += ["generate_pymol_script", "generate_all_pymol_scripts"]
except ImportError:
    pass

try:
    from .pipeline import (
        run_full_pipeline,
        run_preprocessing_only,
        run_docking_only,
        run_analysis_only,
    )
    __all__ += ["run_full_pipeline", "run_preprocessing_only",
                "run_docking_only", "run_analysis_only"]
except ImportError:
    pass

try:
    from .report_generator import generate_paper_content
    __all__ += ["generate_paper_content"]
except ImportError:
    pass
