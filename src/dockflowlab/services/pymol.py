"""生成简洁、可读的 PyMOL 可视化脚本。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("pymol")


def write_pml(
    complex_pdb: str | Path,
    output_pml: str | Path,
    *,
    title: str = "",
    ligand_resname: str = "LIG",
    ligand_chain: str = "L",
) -> Path:
    complex_pdb = Path(complex_pdb).resolve()
    output_pml = Path(output_pml)
    output_pml.parent.mkdir(parents=True, exist_ok=True)
    # PyMOL 在 Windows 上也能正确处理正斜杠
    complex_posix = complex_pdb.as_posix()
    title_line = f"# {title}\n" if title else ""
    script = f"""{title_line}reinitialize
load "{complex_posix}", complex
hide everything

# 受体：卡通模型，浅灰色
show cartoon, polymer
color grey80, polymer
set cartoon_transparency, 0.15

# 配体：黄色棍状模型
select ligand, (chain {ligand_chain} and resn {ligand_resname})
show sticks, ligand
color yellow, ligand
set stick_radius, 0.16, ligand

# 结合口袋 4 Å 内的残基
select pocket, byres (polymer within 4 of ligand)
show lines, pocket
color cyan, pocket
set line_width, 1.2

zoom ligand, 8
bg_color white
set ray_opaque_background, off
print "DockFlowLab: 已载入复合物。黄色为对接配体，青色为 4 Å 内的口袋残基。"
"""
    output_pml.write_text(script, encoding="utf-8")
    return output_pml


def find_pymol() -> str:
    candidates = [
        shutil.which("pymol") or "",
        "/Applications/PyMOL.app/Contents/MacOS/PyMOL",
        str(Path.home() / "Applications/PyMOL.app/Contents/MacOS/PyMOL"),
        r"C:\Program Files\PyMOL\PyMOL.exe",
        r"C:\Program Files (x86)\PyMOL\PyMOL.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


def open_in_pymol(pml_path: str | Path, executable: str = "") -> tuple[bool, str]:
    """尝试用 PyMOL 打开脚本；未安装时返回失败原因。"""
    executable = executable or find_pymol()
    if not executable:
        return False, "未检测到 PyMOL。可先手动打开生成的 .pml 文件。"
    try:
        subprocess.Popen([executable, str(pml_path)], cwd=str(Path(pml_path).parent))
        return True, ""
    except OSError as exc:
        log.warning("启动 PyMOL 失败：%s", exc)
        return False, str(exc)
