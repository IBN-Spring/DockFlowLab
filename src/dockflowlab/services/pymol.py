"""生成简洁、可读的 PyMOL 可视化脚本。"""

from __future__ import annotations

import hashlib
import locale
import os
import shutil
import subprocess
from pathlib import Path

from ..logging_setup import get_logger
from ..tools.runtime import ascii_temp_root, windows_short_path

log = get_logger("pymol")


def _ascii_comment(text: str) -> str:
    """PyMOL 的 PML 解析器在部分 Windows 构建中遇到非 ASCII 字符会崩溃。"""
    return text.encode("ascii", "replace").decode("ascii")


def _reference_path(path: Path, pml_path: Path) -> str:
    """返回可安全写入 PML 的结构路径。

    优先使用纯 ASCII 的绝对路径；工作目录含中文时改用相对路径（目录名通常
    是英文）；仍不行就复制到 ASCII 临时目录。这样 PyMOL 不会因为路径编码
    问题读不到结构。
    """
    resolved = path.resolve()
    pml_dir = pml_path.resolve().parent
    absolute = resolved.as_posix()
    if absolute.isascii():
        return absolute

    try:
        relative = os.path.relpath(resolved, pml_dir).replace(os.sep, "/")
        if relative.isascii():
            return relative
    except ValueError:
        pass

    short = windows_short_path(resolved).as_posix()
    if short.isascii() and Path(short).is_file():
        return short

    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
    suffix = _ascii_comment(resolved.suffix) or ".pdb"
    target = ascii_temp_root() / "pymol_assets" / f"structure_{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, target)
    log.info("结构路径含非 ASCII 字符，已复制到 %s 供 PyMOL 使用", target)
    return target.as_posix()


def _pml_encoding() -> str:
    """PyMOL 在 Windows 上按系统区域编码读取 ``.pml``（中文系统为 GBK）。

    如果强行写 UTF-8，含中文标题或中文路径的脚本会在 PyMOL 中报
    ``UnicodeDecodeError``。这里跟随系统编码，并对无法编码的字符做替换，
    保证脚本至少能加载结构与盒子。
    """
    try:
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:  # pragma: no cover - 极端区域设置
        return "utf-8"


def _write_pml(path: Path, script: str) -> None:
    path.write_text(script, encoding=_pml_encoding(), errors="replace")


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
    ligand_chain = _ascii_comment(str(ligand_chain)).strip() or "L"
    ligand_resname = _ascii_comment(str(ligand_resname)).strip() or "LIG"
    # PyMOL 在 Windows 上也能正确处理正斜杠
    complex_reference = _reference_path(complex_pdb, output_pml)
    title_line = f"# {_ascii_comment(title)}\n" if title else ""
    script = f"""{title_line}reinitialize
load "{complex_reference}", complex
hide everything

# Receptor: cartoon, light grey
show cartoon, polymer
color grey80, polymer
set cartoon_transparency, 0.15

# Ligand: yellow sticks
select ligand, (chain {ligand_chain} and resn {ligand_resname})
show sticks, ligand
color yellow, ligand
set stick_radius, 0.16, ligand

# Pocket residues within 4 A of the ligand
select pocket, byres (polymer within 4 of ligand)
show lines, pocket
color cyan, pocket
set line_width, 1.2

zoom ligand, 8
bg_color white
set ray_opaque_background, off
print "DockFlowLab: complex loaded. Yellow = docked ligand; cyan = pocket residues within 4 A."
"""
    _write_pml(output_pml, script)
    return output_pml


def write_box_pml(
    structure_pdb: str | Path,
    output_pml: str | Path,
    box,
    *,
    title: str = "",
) -> Path:
    """写一个只用于检查搜索盒子的 PyMOL 脚本。

    ``box`` 是 :class:`dockflowlab.core.models.Box`。盒子用 CGO 线框绘制，
    同时显示中心点和蛋白骨架，便于确认盒子是否覆盖目标位点。
    """
    structure_pdb = Path(structure_pdb).resolve()
    output_pml = Path(output_pml)
    output_pml.parent.mkdir(parents=True, exist_ok=True)
    structure_reference = _reference_path(structure_pdb, output_pml)

    center = [float(value) for value in box.center]
    half = [float(value) / 2.0 for value in box.size]
    signs = [
        (-1, -1, -1),
        (1, -1, -1),
        (-1, 1, -1),
        (1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (-1, 1, 1),
        (1, 1, 1),
    ]
    corners = [
        (
            center[0] + sx * half[0],
            center[1] + sy * half[1],
            center[2] + sz * half[2],
        )
        for sx, sy, sz in signs
    ]
    edges = [
        (0, 1), (0, 2), (1, 3), (2, 3),
        (4, 5), (4, 6), (5, 7), (6, 7),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    lines = []
    for start, end in edges:
        lines.extend([*corners[start], *corners[end]])

    title_line = f"# {_ascii_comment(title)}\n" if title else ""
    script = f"""{title_line}reinitialize
load "{structure_reference}", receptor
hide everything
show cartoon, polymer
color grey80, polymer
set cartoon_transparency, 0.15
show sticks, organic
color orange, organic

python
from pymol.cgo import BEGIN, LINES, COLOR, VERTEX, END
from pymol import cmd

vertices = {lines!r}
box = [BEGIN, LINES, COLOR, 1.0, 0.58, 0.12]
for index in range(0, len(vertices), 3):
    box.extend([VERTEX, vertices[index], vertices[index + 1], vertices[index + 2]])
box.append(END)
cmd.load_cgo(box, "dockflowlab_box")
cmd.set("cgo_line_width", 3.0)
cmd.pseudoatom("dockflowlab_center", pos={tuple(round(v, 3) for v in center)!r})
cmd.show("spheres", "dockflowlab_center")
cmd.set("sphere_scale", 0.35, "dockflowlab_center")
cmd.color("white", "dockflowlab_center")
cmd.zoom("dockflowlab_box", 2.0)
python end

bg_color white
set ray_opaque_background, off
print "DockFlowLab: orange wireframe = search box; white sphere = box center."
"""
    _write_pml(output_pml, script)
    return output_pml


def find_pymol() -> str:
    candidates = [
        os.environ.get("PYMOL_EXE", ""),
        shutil.which("pymol") or "",
        r"C:\ProgramData\open-source-pymol\pymol.exe",
        r"C:\ProgramData\open-source-pymol\Scripts\pymol.exe",
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
