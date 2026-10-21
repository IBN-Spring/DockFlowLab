"""用外部 PyMOL 无界面渲染复合物预览。

PyMOL 会初始化 OpenGL、Tcl 与自己的 Python 解释器。把它加载进界面进程
（或长期驻留）会增加原生崩溃与驱动冲突的风险，而且 PyMOL 命令不是线程
安全的。因此这里始终：

* 启动独立的 ``pymol -cq`` 进程完成一次渲染；
* 结构、脚本与输出都先放在 ASCII 临时目录，避免中文路径导致 PyMOL 失败；
* 为子进程设置超时，并在取消/超时时结束整棵进程树（PyMOL 启动器还会
  再生成一个 python.exe）。

主程序只接收最终 PNG 与日志，渲染失败不会带走界面。
"""

from __future__ import annotations

import hashlib
import locale
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..logging_setup import get_logger
from ..tools.runtime import (
    IS_MACOS,
    IS_WINDOWS,
    ProcessResult,
    find_executable,
    run_process,
    work_directory,
)

log = get_logger("pymol_render")

DEFAULT_SIZE = 900
MAX_SIZE = 4000
STYLE_SUFFIXES = {".pml", ".txt"}

# PyMOL 的标签字号按屏幕像素绘制，不随 ray 分辨率一起放大。以 900 像素
# 为基准补偿：1200 像素时字号需要 ×1.33，才能保持相同的视觉比例。
LABEL_REFERENCE_SIZE = 900
DEFAULT_LABEL_SCALE = 1.5


@dataclass(frozen=True)
class ComplexFile:
    """``complexes/<靶点>/<配体>.pdb`` 中的一个可渲染复合物。"""

    target: str
    ligand: str
    path: Path


@dataclass
class RenderResult:
    ok: bool
    image_path: Path | None = None
    output: str = ""
    elapsed: float = 0.0
    error: str = ""
    timed_out: bool = False
    cancelled: bool = False


def _local_scripts_pymol() -> Path | None:
    """当前 Python 环境自带的 PyMOL（conda 安装时位于 Scripts/ 或 bin/）。"""
    executable = Path(sys.executable).resolve()
    if IS_WINDOWS:
        candidates = [
            executable.parent / "pymol.exe",
            executable.parent / "Scripts" / "pymol.exe",  # conda：python 在环境根目录
            executable.parent.parent / "Scripts" / "pymol.exe",  # venv
        ]
    else:
        candidates = [
            executable.parent / "pymol",
            executable.parent / "bin" / "pymol",  # conda
            executable.parent.parent / "bin" / "pymol",  # venv
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _bundled_pymol() -> Path | None:
    """安装包内置的 ``PyMOLRunner``（与主程序同级）。"""
    if not getattr(sys, "frozen", False):
        return None
    roots = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    names = ("PyMOLRunner.exe",) if IS_WINDOWS else ("PyMOLRunner",)
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def find_renderer(configured: str = "") -> str:
    """查找可用于 ``-cq`` 渲染的 PyMOL。

    优先使用用户指定的路径，其次是 ``PYMOL_RENDER_EXE``；安装包内置的
    ``PyMOLRunner`` 排在当前环境与系统安装之前，用户无需另装 PyMOL。
    经验上同一 conda 环境里的 ``pymol.exe`` 最可靠。
    """
    local = _local_scripts_pymol()
    candidates: list[str | None] = [
        configured.strip() or None,
        os.environ.get("PYMOL_RENDER_EXE"),
    ]
    bundled = _bundled_pymol()
    if bundled is not None:
        candidates.append(str(bundled))
    if local is not None:
        candidates.append(str(local))
    if IS_MACOS:
        candidates.extend(
            [
                "/Applications/PyMOL.app/Contents/MacOS/PyMOL",
                str(Path.home() / "Applications/PyMOL.app/Contents/MacOS/PyMOL"),
            ]
        )
    return find_executable(*candidates, names=("pymol",))


def _system_ansi_encoding() -> str:
    """PyMOL 读取 ``@`` 脚本时使用的系统编码。

    不能用 ``locale.getpreferredencoding()`` 代替：在 Python UTF-8 模式下它
    会返回 UTF-8，而 PyMOL 仍按 Windows ANSI 代码页读取，中文注释会再次报错。
    """
    if IS_WINDOWS:
        try:
            import ctypes

            codepage = ctypes.windll.kernel32.GetACP()
            if codepage:
                return f"cp{codepage}"
        except (AttributeError, OSError):
            pass
    return locale.getpreferredencoding(False) or "utf-8"


def _script_encodings() -> list[str]:
    """返回临时风格脚本的候选编码，首个为首选。

    PyMOL 的 ``@`` 按自身解释器的默认编码读取文件。Windows 中文系统通常是
    ANSI（GBK），但 PyMOL 会继承 ``PYTHONUTF8=1``，此时它按 UTF-8 读取。
    两种都可能出现，因此渲染失败时还会用另一编码重试一次。
    """
    ansi = _system_ansi_encoding()
    inherited_utf8 = os.environ.get("PYTHONUTF8", "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }
    primary = "utf-8" if inherited_utf8 else ansi
    alternate = ansi if primary != ansi else "utf-8"
    encodings = [primary]
    if alternate.lower() != primary.lower():
        encodings.append(alternate)
    return encodings


def _looks_like_script_decode_error(output: str) -> bool:
    markers = (
        "UnicodeDecodeError",
        "codec can't decode",
        "invalid multibyte sequence",
        "Non-UTF-8 code",
    )
    return any(marker in output for marker in markers)


def _read_script_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def scale_label_sizes(text: str, factor: float) -> str:
    """放大脚本里的 ``label_size`` 数值，保留原有的对象/选择范围。

    PyMOL 的标签是固定像素大小：渲染分辨率提高时分子会变大，文字却不变，
    相对就越来越小。这里直接改写脚本中的字面数值，因此
    ``set label_size, 18, binding_labels`` 和
    ``set label_size, 16, hbonds`` 会各自按相同倍数放大，不会把残基标签和
    氢键距离标签压成同一字号。

    脚本完全没有设置 ``label_size`` 时，在最前面补一个放大后的默认值。
    """
    if factor <= 0 or abs(factor - 1.0) < 1e-6:
        return text

    def _grow(match: re.Match[str]) -> str:
        prefix, value = match.group(1), float(match.group(2))
        return f"{prefix}{value * factor:.1f}"

    text = re.sub(r"(label_size\s*,\s*)(\d+(?:\.\d+)?)", _grow, text)
    text = re.sub(r"""(label_size["']\s*,\s*)(\d+(?:\.\d+)?)""", _grow, text)
    # PyMOL 的默认字号是 14；放在最前面，让没有显式设置的标签也一起放大。
    return f"set label_size, {14.0 * factor:.1f}\n" + text


def _write_script(
    path: Path,
    source: Path,
    encoding: str = "",
    label_scale: float = 1.0,
) -> None:
    """把 UTF-8/GBK 风格脚本转成 PyMOL 能读取的编码。

    用户的风格脚本通常是 UTF-8；直接 ``@`` 会在中文注释处抛
    ``UnicodeDecodeError``。这里按目标编码转写，无法编码的字符只影响
    注释/提示文字，不影响命令。``label_scale`` 会同步放大字号。
    """
    text = scale_label_sizes(_read_script_text(source), label_scale)
    path.write_text(
        text,
        encoding=encoding or _script_encodings()[0],
        errors="replace",
    )


def render_key(
    complex_path: str | Path,
    style_path: str | Path,
    *,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
) -> str:
    digest = hashlib.sha1()
    for path in (Path(complex_path), Path(style_path)):
        try:
            stat = path.stat()
            digest.update(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode())
        except OSError:
            digest.update(str(path).encode("utf-8"))
    digest.update(f"{width}x{height}".encode())
    return digest.hexdigest()[:12]


def build_job_script(
    structure: str,
    style: str,
    output: str,
    *,
    width: int,
    height: int,
) -> str:
    """生成一次渲染的 PML。路径必须已是 ASCII。

    无界面（``-cq``）时 PyMOL 的 ``draw`` 也会走光线追踪，日志同样输出
    ``Ray: render time``，与 ``ray`` 的耗时和结果几乎一致，因此这里固定
    使用 ``ray``：透明表面与阴影更可靠，也不需要向用户暴露一个没有实际
    区别的开关。
    """
    lines = [
        "reinitialize",
        f'load "{structure}", complex',
        f"@{style}",
        "set ray_opaque_background, on",
        f"viewport {width}, {height}",
        f"ray {width}, {height}",
    ]
    # png 的文件名不要加引号：PyMOL 会把引号当作文件名的一部分并再补 .png。
    lines.extend([f"png {output}, dpi=100", "quit"])
    return "\n".join(lines) + "\n"


def render_complex(
    complex_path: str | Path,
    style_path: str | Path,
    output_png: str | Path,
    *,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
    timeout: float = 300.0,
    renderer: str = "",
    label_scale: float = DEFAULT_LABEL_SCALE,
    cancel=None,
    on_line: Callable[[str], None] | None = None,
) -> RenderResult:
    """渲染一张 PNG。返回结果始终不会抛出 PyMOL 的原生错误。

    ``label_scale`` 是相对 900 像素基准的标签放大倍数；服务会再乘以
    当前分辨率 / 900，保证不同输出尺寸下文字占比一致。
    """
    started = time.perf_counter()
    complex_path = Path(complex_path)
    style_path = Path(style_path)
    output_png = Path(output_png)
    if not complex_path.is_file():
        return RenderResult(False, error=f"找不到复合物文件：{complex_path}")
    if not style_path.is_file():
        return RenderResult(False, error=f"找不到风格脚本：{style_path}")
    renderer = renderer or os.environ.get("PYMOL_RENDER_EXE", "") or find_renderer()
    if not renderer:
        return RenderResult(
            False,
            error="未检测到可用的 PyMOL。请在设置中安装 PyMOL，或设置 PYMOL_RENDER_EXE。",
        )

    width = max(200, min(MAX_SIZE, int(width)))
    height = max(200, min(MAX_SIZE, int(height)))
    output_png.parent.mkdir(parents=True, exist_ok=True)
    scale = max(0.1, float(label_scale)) * (width / LABEL_REFERENCE_SIZE)

    with work_directory("pymol_render") as temp:
        structure = temp / "complex.pdb"
        style = temp / "style.pml"
        rendered = temp / "preview.png"
        shutil.copy2(complex_path, structure)
        job = temp / "render.pml"
        # 全部使用临时目录内的相对文件名：即使临时根目录含空格，PyMOL 的
        # png 命令也不需要处理带空格的路径。
        job.write_text(
            build_job_script(
                structure.name,
                style.name,
                rendered.name,
                width=width,
                height=height,
            ),
            encoding="ascii",
        )
        command = [renderer, "-cq", str(job)]
        encodings = _script_encodings()
        process: ProcessResult | None = None
        for attempt, encoding in enumerate(encodings):
            rendered.unlink(missing_ok=True)
            _write_script(style, style_path, encoding=encoding, label_scale=scale)
            process = run_process(
                command,
                cwd=temp,
                timeout=max(10.0, float(timeout)),
                cancel=cancel,
                on_line=on_line,
                kill_tree=True,
            )
            if process.cancelled or process.timed_out:
                break
            if rendered.is_file() and rendered.stat().st_size > 0:
                break
            next_encoding = encodings[attempt + 1] if attempt + 1 < len(encodings) else ""
            if next_encoding and _looks_like_script_decode_error(process.output):
                if on_line is not None:
                    try:
                        on_line(
                            f"风格脚本编码与 PyMOL 不一致，正在用 {next_encoding} 重试…"
                        )
                    except Exception:  # 用户回调不应中断渲染
                        pass
                continue
            break
        elapsed = time.perf_counter() - started
        if process is None:  # 编码列表不会为空，仅作防御
            return RenderResult(False, error="无法准备 PyMOL 作业脚本。", elapsed=elapsed)
        if process.cancelled:
            return RenderResult(
                False, output=process.output, elapsed=elapsed, cancelled=True,
                error="渲染已取消。",
            )
        if process.timed_out:
            return RenderResult(
                False, output=process.output, elapsed=elapsed, timed_out=True,
                error=f"PyMOL 渲染超过 {timeout:.0f} 秒，已终止。可降低分辨率后重试。",
            )
        if not rendered.is_file() or rendered.stat().st_size == 0:
            tail = "\n".join(process.output.splitlines()[-8:])
            return RenderResult(
                False,
                output=process.output,
                elapsed=elapsed,
                error=f"PyMOL 没有生成图片（退出码 {process.returncode}）。"
                + (f"\n{tail}" if tail else ""),
            )
        shutil.copy2(rendered, output_png)
        return RenderResult(
            True,
            image_path=output_png,
            output=process.output,
            elapsed=elapsed,
        )


def style_token(style_path: str | Path, folder: str | Path) -> str:
    """把脚本相对路径变成可用于输出文件名的短标签。

    保留中文与层级信息（例如 ``橙色风格_全局``），只替换文件系统不允许的
    字符。渲染完成后由 Python 复制图片，因此目标路径可以包含中文。
    """
    path = Path(style_path)
    try:
        relative = path.resolve().relative_to(Path(folder).resolve())
    except (OSError, ValueError):
        relative = Path(path.name)
    text = str(relative.with_suffix(""))
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._-")
    return text[:80] or "style"


def complexes_in(root: str | Path) -> list[ComplexFile]:
    """列出项目 ``complexes/`` 下的所有复合物，按靶点、配体排序。"""
    folder = Path(root) / "complexes"
    if not folder.is_dir():
        return []
    items: list[ComplexFile] = []
    for target_dir in sorted(
        (path for path in folder.iterdir() if path.is_dir()),
        key=lambda path: path.name.lower(),
    ):
        for path in sorted(target_dir.glob("*.pdb"), key=lambda item: item.name.lower()):
            if path.is_file():
                items.append(ComplexFile(target_dir.name, path.stem, path))
    return items


def styles_in(folder: str | Path) -> list[Path]:
    """递归列出文件夹中的 PyMOL/PML 风格脚本。"""
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in STYLE_SUFFIXES
        ),
        key=lambda path: (str(path.parent).lower(), path.name.lower()),
    )
