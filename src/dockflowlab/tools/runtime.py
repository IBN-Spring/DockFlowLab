"""运行时工具：子进程、临时目录、平台路径。

Vina 1.2.x 在 Windows 上无法可靠处理包含中文的路径，因此所有外部计算都在
纯 ASCII 的临时目录中进行，计算完成后再把结果复制回项目目录。
"""

from __future__ import annotations

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

_CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_ascii_temp_cache: Path | None = None


# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
def package_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str | Path) -> Path:
    """返回打包资源路径（兼容 PyInstaller one-folder / one-file）。"""
    relative = Path(relative)
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "dockflowlab" / "resources" / relative)
        candidates.append(Path(meipass) / "resources" / relative)
    candidates.append(package_dir() / "resources" / relative)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def app_dir() -> Path:
    """可执行文件所在目录；开发环境下为仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return package_dir().parent.parent


def is_ascii_path(path: str | Path) -> bool:
    try:
        return str(path).isascii()
    except UnicodeEncodeError:
        return False


def windows_short_path(path: str | Path) -> Path:
    """通过 Win32 API 获取 8.3 短路径；失败时返回原路径。"""
    if not IS_WINDOWS:
        return Path(path)
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
        if result:
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    return Path(path)


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_test_{uuid.uuid4().hex[:6]}"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def ascii_temp_root() -> Path:
    """返回一个可写、路径为纯 ASCII 的临时根目录。"""
    global _ascii_temp_cache
    if _ascii_temp_cache is not None and _ascii_temp_cache.is_dir():
        return _ascii_temp_cache

    candidates: list[Path] = []
    if IS_WINDOWS:
        public = os.environ.get("PUBLIC")
        if public:
            candidates.append(Path(public) / "DockFlowLab" / "tmp")
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            candidates.append(Path(program_data) / "DockFlowLab" / "tmp")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "DockFlowLab" / "tmp")
        candidates.append(Path(tempfile.gettempdir()) / "DockFlowLab")
    else:
        candidates.append(Path("/tmp") / "dockflowlab")
        candidates.append(Path(tempfile.gettempdir()) / "dockflowlab")

    for candidate in candidates:
        if is_ascii_path(candidate) and _writable(candidate):
            _ascii_temp_cache = candidate
            return candidate

    # Windows 上退而求其次：使用 8.3 短路径
    if IS_WINDOWS:
        for candidate in candidates:
            short = windows_short_path(candidate)
            if is_ascii_path(short) and _writable(short):
                _ascii_temp_cache = short
                return short

    fallback = Path(tempfile.gettempdir()) / "dockflowlab"
    fallback.mkdir(parents=True, exist_ok=True)
    _ascii_temp_cache = fallback
    return fallback


@contextmanager
def work_directory(prefix: str = "job") -> Iterator[Path]:
    """创建并自动清理一个 ASCII 临时工作目录。"""
    root = ascii_temp_root()
    name = f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    directory = root / name
    directory.mkdir(parents=True, exist_ok=False)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def find_executable(*candidates: str | Path | None, names: Sequence[str] = ()) -> str:
    """按顺序查找可执行文件，最后搜索 PATH。返回空字符串表示未找到。"""
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            if not IS_WINDOWS or path.suffix.lower() == ".exe":
                return str(path.resolve())
            if os.access(path, os.X_OK):
                return str(path.resolve())
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


# ----------------------------------------------------------------------
# 子进程
# ----------------------------------------------------------------------
@dataclass
class ProcessResult:
    returncode: int
    output: str = ""
    timed_out: bool = False
    cancelled: bool = False
    stalled: bool = False
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.returncode == 0
            and not self.timed_out
            and not self.cancelled
            and not self.stalled
        )

    def tail(self, lines: int = 12) -> str:
        chunks = [line for line in self.output.splitlines() if line.strip()]
        return "\n".join(chunks[-lines:])


def _terminate(
    process: subprocess.Popen[bytes],
    grace: float = 2.0,
    *,
    kill_tree: bool = False,
) -> None:
    """终止外部进程；``kill_tree`` 用于会再生成子进程的启动器（如 PyMOL）。"""
    if process.poll() is not None:
        return
    if kill_tree and IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
                timeout=grace + 2,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif kill_tree and hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(process.pid), 15)  # SIGTERM
        except OSError:
            pass
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass


def run_process(
    command: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stall_timeout: float | None = None,
    log_path: str | Path | None = None,
    log_mode: str = "w",
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    stdin_data: bytes | None = None,
    kill_tree: bool = False,
) -> ProcessResult:
    """运行外部程序，实时收集输出，支持超时与取消。"""
    cmd = [str(part) for part in command]
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    stdin_pipe = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdin=stdin_pipe,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=_CREATE_NO_WINDOW,
            start_new_session=kill_tree and not IS_WINDOWS,
        )
    except FileNotFoundError as exc:
        return ProcessResult(returncode=127, output=str(exc), command=cmd)
    except OSError as exc:
        return ProcessResult(returncode=126, output=str(exc), command=cmd)

    if stdin_data is not None and process.stdin is not None:
        try:
            process.stdin.write(stdin_data)
            process.stdin.close()
        except OSError:
            pass

    lines: queue.Queue[bytes | None] = queue.Queue()

    def _read() -> None:
        try:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                lines.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    reader = threading.Thread(target=_read, name="dockflowlab-proc-reader", daemon=True)
    reader.start()

    collected: list[str] = []
    started = time.monotonic()
    last_activity = started
    timed_out = False
    stalled = False
    cancelled = False
    log_handle = None
    if log_path:
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_path, log_mode, encoding="utf-8", errors="replace")
        except OSError:
            log_handle = None

    try:
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                _terminate(process, kill_tree=kill_tree)
            if timeout is not None and not timed_out and (time.monotonic() - started) > timeout:
                timed_out = True
                _terminate(process, kill_tree=kill_tree)
            if (
                stall_timeout is not None
                and not stalled
                and not timed_out
                and (time.monotonic() - last_activity) > stall_timeout
            ):
                stalled = True
                _terminate(process, kill_tree=kill_tree)
            try:
                raw = lines.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive() and lines.empty():
                    break
                continue
            if raw is None:
                break
            last_activity = time.monotonic()
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            collected.append(text)
            if log_handle is not None:
                try:
                    log_handle.write(text + "\n")
                    log_handle.flush()
                except OSError:
                    pass
            if on_line is not None:
                try:
                    on_line(text)
                except Exception:  # 用户回调不应中断外部计算
                    pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _terminate(process, kill_tree=kill_tree)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if log_handle is not None:
            log_handle.close()
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        output="\n".join(collected),
        timed_out=timed_out,
        cancelled=cancelled,
        stalled=stalled,
        command=cmd,
    )


def run_capture(
    command: Sequence[str | Path], *, cwd: str | Path | None = None, timeout: float = 30
) -> ProcessResult:
    return run_process(command, cwd=cwd, timeout=timeout)


# ----------------------------------------------------------------------
# 系统集成
# ----------------------------------------------------------------------
def open_path(path: str | Path) -> bool:
    """用系统默认程序打开文件或文件夹。"""
    target = str(Path(path))
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        elif IS_MACOS:
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except (OSError, AttributeError):
        return False


def reveal_path(path: str | Path) -> bool:
    """在系统文件管理器中定位文件。"""
    target = Path(path)
    try:
        if IS_WINDOWS:
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", str(target)])
        else:
            folder = target.parent if target.is_file() else target
            subprocess.Popen(["xdg-open", str(folder)])
        return True
    except (OSError, AttributeError):
        return False


def cpu_count() -> int:
    try:
        import psutil  # 可选依赖，但 pyproject 已包含

        return max(1, psutil.cpu_count(logical=True) or os.cpu_count() or 1)
    except ImportError:
        return max(1, os.cpu_count() or 1)


def human_size(size: int | float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def dedupe_paths(paths: Iterable[str | Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for item in paths:
        path = Path(item)
        key = str(path).lower() if IS_WINDOWS else str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
