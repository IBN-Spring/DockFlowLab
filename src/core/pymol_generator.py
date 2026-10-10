#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pymol_generator —— PyMOL 脚本生成
"""
import os
import glob
from pathlib import Path

from .config import CONFIG
from .logger import log
from .utils import ensure_dir

# data/visual/ 下的风格模板目录
_VISUAL_DIR = Path(__file__).parent.parent.parent / "data" / "visual"


def _get_templates():
    """返回 data/visual/ 下所有 .pml 模板文件路径列表"""
    if _VISUAL_DIR.exists():
        return sorted(_VISUAL_DIR.glob("*.pml"))
    return []


def generate_pymol_script(receptor_name, ligand_name, pdb_path, output_dir,
                          log_text=None):
    """为单个对接结果生成 PyMOL 可视化脚本（每对一个文件夹，多风格）"""
    pair_name = f"{receptor_name}_vs_{ligand_name}"
    pair_dir = os.path.join(output_dir, pair_name)
    ensure_dir(pair_dir)

    pdb_abs = os.path.abspath(pdb_path).replace("\\", "/")
    load_line = f"load {pdb_abs}, complex\n"

    templates = _get_templates()
    if not templates:
        # 回退：无模板时生成简单脚本
        script_path = os.path.join(pair_dir, f"{pair_name}.pml")
        script = f"""# PyMOL visualization: {receptor_name} vs {ligand_name}
load {pdb_abs}, complex
hide everything
select receptor, complex and not hetatm
show cartoon, receptor
color marine, receptor
select ligand, complex and hetatm and not resn HOH
show sticks, ligand
color yellow, ligand
set stick_radius, 0.15
zoom ligand, 8
bg_color white
set ray_opaque_background, 1
set antialias, 2
"""
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        log(f"  PyMOL 脚本(简单): {pair_name}/{pair_name}.pml", log_text=log_text)
        return True

    for tpl in templates:
        content = tpl.read_text(encoding="gbk", errors="replace")
        out_path = os.path.join(pair_dir, tpl.name)
        with open(out_path, "w", encoding="gbk") as f:
            f.write(load_line)
            f.write(content)

    log(f"  PyMOL 脚本: {pair_name}/ ({len(templates)} 个风格)", log_text=log_text)
    return True


def generate_all_pymol_scripts(log_text=None):
    """批量生成所有对接结果的 PyMOL 脚本"""
    log("=== 开始生成 PyMOL 可视化脚本 ===", log_text=log_text)

    converted_dir = CONFIG.get("8.转换PDB")
    output_dir = CONFIG.get("9.PyMOL")
    ensure_dir(output_dir)

    if not os.path.exists(converted_dir):
        log(f"未找到转换后的 PDB 目录: {converted_dir}", "WARNING", log_text)
        return False

    pdb_files = glob.glob(os.path.join(converted_dir, "*_vs_*.pdb"))
    if not pdb_files:
        log("未找到对接结果 PDB 文件", "WARNING", log_text)
        return False

    count = 0
    for pdb_path in pdb_files:
        base = os.path.splitext(os.path.basename(pdb_path))[0]
        if "_vs_" in base:
            rec, lig = base.split("_vs_", 1)
            generate_pymol_script(rec, lig, pdb_path, output_dir, log_text)
            count += 1

    log(f"=== PyMOL 脚本生成完成: {count} 对 ===", log_text=log_text)
    return count > 0
