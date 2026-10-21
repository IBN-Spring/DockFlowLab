"""对接引擎适配层：AutoDock Vina、QuickVina 2 与 Smina。

三种引擎都接受 AutoDock PDBQT 输入，并写出带结合能的 PDBQT 构象。
所有计算都在 ASCII 临时目录中完成：窗口标题、参数和输出文件名均为 ASCII，
计算结束后再复制回项目目录。这解决了 Vina 1.2.7 在 Windows 中文路径下
无法打开文件的问题。

AutoDock Vina 与 QuickVina 2 使用同一套 Vina 评分函数；Smina 的默认评分
在其基础上做了修正，因此不同引擎得到的分数不宜直接横向比较。
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from ..core.models import Box, Pose
from ..core.pdbio import parse_pdbqt_poses, validate_pdbqt
from ..errors import DependencyError, DockingError, InputError
from ..logging_setup import get_logger
from .runtime import (
    IS_MACOS,
    IS_WINDOWS,
    app_dir,
    find_executable,
    resource_path,
    run_process,
    work_directory,
)

log = get_logger("vina")

ENGINE_VINA = "vina"
ENGINE_QVINA2 = "qvina2"
ENGINE_SMINA = "smina"

# 顺序即“自动换引擎”时的优先级：先尝试评分与 Vina 兼容的 QuickVina 2，
# 再尝试评分略有差异的 Smina。
ENGINE_ORDER: tuple[str, ...] = (ENGINE_VINA, ENGINE_QVINA2, ENGINE_SMINA)

ENGINE_LABELS = {
    ENGINE_VINA: "AutoDock Vina",
    ENGINE_QVINA2: "QuickVina 2",
    ENGINE_SMINA: "Smina",
}

ENGINE_DESCRIPTIONS = {
    ENGINE_VINA: "标准引擎，使用 Vina 评分函数，结果可与大多数文献直接比较。",
    ENGINE_QVINA2: "基于 Vina 1.1.2 的加速版，评分相同、搜索更快，适合 Vina 停滞时改用。",
    ENGINE_SMINA: "Vina 分支，默认评分略有修正，能量最小化更稳健；分数不宜与 Vina 直接比较。",
}

_ENGINE_PATH_SETTINGS = {
    ENGINE_VINA: "vina_path",
    ENGINE_QVINA2: "qvina2_path",
    ENGINE_SMINA: "smina_path",
}


def engine_path_setting(settings, key: str) -> str:
    """读取设置中对应引擎的可执行文件路径（留空表示自动查找）。"""
    attribute = _ENGINE_PATH_SETTINGS.get(key, "")
    return str(getattr(settings, attribute, "") or "") if attribute else ""


def set_engine_path(settings, key: str, value: str) -> None:
    """把界面中选择的可执行文件路径写回设置（空字符串表示自动查找）。"""
    attribute = _ENGINE_PATH_SETTINGS.get(key, "")
    if attribute:
        setattr(settings, attribute, str(value or ""))


def engine_from_settings(settings, key: str) -> "EngineAdapter":
    return make_engine(key, engine_path_setting(settings, key))


def make_engine(key: str, executable: str = "") -> "EngineAdapter":
    cls = _ENGINE_CLASSES.get(key, VinaAdapter)
    return cls(executable)


def available_engines(settings=None) -> list["EngineAdapter"]:
    """按优先级返回当前机器上可用的引擎。"""
    engines = []
    for key in ENGINE_ORDER:
        executable = engine_path_setting(settings, key) if settings is not None else ""
        adapter = make_engine(key, executable)
        if adapter.available:
            engines.append(adapter)
    return engines


def engine_env() -> dict[str, str]:
    """补充引擎子进程的动态库搜索路径。

    随包附带的 QuickVina 2 / Smina 依赖 boost、OpenBabel 等 DLL。开发环境
    下它们在 conda 的 ``Library/bin``，打包后由 PyInstaller 放在 ``_MEIPASS``
    或程序根目录；这些目录不一定在用户 PATH 里，因此显式加进去。
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    candidates.append(app_dir())
    if IS_WINDOWS:
        candidates.extend(
            [
                Path(sys.prefix) / "Library" / "bin",
                Path(sys.prefix) / "Library" / "lib",
                Path(sys.executable).resolve().parent,
            ]
        )
    else:
        candidates.extend(
            [
                Path(sys.prefix) / "lib",
                Path(sys.executable).resolve().parent / "lib",
            ]
        )
    existing = os.environ.get("PATH", "")
    path = os.pathsep.join(
        [str(item) for item in candidates if item.is_dir()]
        + ([existing] if existing else [])
    )
    env = {"PATH": path}
    if not IS_WINDOWS:
        for variable in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            current = os.environ.get(variable, "")
            env[variable] = os.pathsep.join(
                [str(item) for item in candidates if item.is_dir()]
                + ([current] if current else [])
            )
    return env


def _platform_dir() -> str:
    return "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux")


def _binary_name(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def _resource_candidates(folder: str, binary: str) -> list[Path]:
    """程序目录与打包资源的候选位置（用户也可直接放入可执行文件）。"""
    platform_dir = _platform_dir()
    root = app_dir()
    return [
        root / binary,
        root / "vendor" / folder / platform_dir / binary,
        root / "resources" / folder / platform_dir / binary,
        resource_path(Path(folder) / platform_dir / binary),
        resource_path(Path(folder) / binary),
    ]


def find_vina(executable: str = "") -> str:
    candidates: list[str | Path | None] = [executable] if executable else []
    candidates.extend(
        [Path("/opt/homebrew/bin/vina"), Path("/usr/local/bin/vina")]
    )
    candidates.extend(_resource_candidates("vina", _binary_name("vina")))
    return find_executable(*candidates, names=("vina", "vina.exe"))


def find_qvina2(executable: str = "") -> str:
    candidates: list[str | Path | None] = [executable] if executable else []
    candidates.extend(_resource_candidates("qvina2", _binary_name("qvina2")))
    candidates.extend(
        [Path("/opt/homebrew/bin/qvina2"), Path("/usr/local/bin/qvina2")]
    )
    return find_executable(
        *candidates, names=("qvina2", "qvina2.exe", "qvina02", "qvina02.exe")
    )


def find_smina(executable: str = "") -> str:
    candidates: list[str | Path | None] = [executable] if executable else []
    candidates.extend(_resource_candidates("smina", _binary_name("smina")))
    candidates.extend(
        [Path("/opt/homebrew/bin/smina"), Path("/usr/local/bin/smina")]
    )
    return find_executable(*candidates, names=("smina", "smina.exe"))


@dataclass
class VinaRun:
    ok: bool
    poses: list[Pose] = field(default_factory=list)
    output_path: str = ""
    log_text: str = ""
    returncode: int = 0
    error: str = ""
    command: list[str] = field(default_factory=list)
    stalled: bool = False


class EngineAdapter:
    """Vina 系对接引擎的公共实现。"""

    key = ENGINE_VINA
    label = "AutoDock Vina"

    def __init__(self, executable: str = "") -> None:
        self.executable = self._find(executable)
        self._version: str | None = None

    # -- 查找与版本 ----------------------------------------------------
    def _find(self, executable: str) -> str:
        return find_vina(executable)

    @property
    def available(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file())

    @property
    def version(self) -> str:
        if self._version is None:
            if not self.available:
                self._version = ""
            else:
                result = run_process(
                    [self.executable, "--version"], timeout=20, env=engine_env()
                )
                text = (result.output or "").strip()
                self._version = text.splitlines()[0] if text else ""
        return self._version

    def require(self) -> None:
        if not self.available:
            raise DependencyError(
                f"未找到 {self.label}。请在“设置”中指定可执行文件，"
                f"或把 {self.key} 放到程序的 resources/{self.key} 目录中。"
            )

    # -- 命令构建 ------------------------------------------------------
    def _command(
        self,
        temp: Path,
        *,
        receptor: str,
        ligand: str,
        output: str,
        center: tuple[float, float, float],
        size: tuple[float, float, float],
        exhaustiveness: int,
        num_modes: int,
        energy_range: float,
        seed: int,
        cpu: int,
    ) -> list[str]:
        return [self.executable]

    def _parse_poses(self, output_path: Path, log_text: str) -> list[Pose]:
        return parse_pdbqt_poses(output_path)

    # -- 对接 ----------------------------------------------------------
    def dock(
        self,
        receptor_pdbqt: str | Path,
        ligand_pdbqt: str | Path,
        box: Box,
        output_pdbqt: str | Path,
        *,
        exhaustiveness: int = 16,
        num_modes: int = 9,
        energy_range: float = 3.0,
        seed: int = 42,
        cpu: int = 0,
        timeout: float | None = None,
        stall_timeout: float | None = None,
        log_path: str | Path | None = None,
        on_line: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> VinaRun:
        self.require()
        receptor_pdbqt = Path(receptor_pdbqt)
        ligand_pdbqt = Path(ligand_pdbqt)
        output_pdbqt = Path(output_pdbqt)
        log_path = Path(log_path) if log_path else output_pdbqt.with_suffix(".log")

        for path, label, is_receptor in (
            (receptor_pdbqt, "受体", True),
            (ligand_pdbqt, "配体", False),
        ):
            valid, message, _ = validate_pdbqt(path, receptor=is_receptor)
            if not valid:
                raise InputError(f"{label}文件不可用：{message}")

        center = tuple(float(v) for v in box.center)
        size = tuple(float(v) for v in box.size)
        if any(not math.isfinite(v) for v in center + size):
            raise InputError("盒子坐标包含无效数值")
        if min(size) < 4:
            raise InputError("盒子边长过小（至少 4 Å）")
        if max(size) > 200:
            raise InputError("盒子边长过大（最多 200 Å）")

        output_pdbqt.parent.mkdir(parents=True, exist_ok=True)
        with work_directory(self.key) as temp:
            shutil.copy2(receptor_pdbqt, temp / "receptor.pdbqt")
            shutil.copy2(ligand_pdbqt, temp / "ligand.pdbqt")
            command = self._command(
                temp,
                receptor="receptor.pdbqt",
                ligand="ligand.pdbqt",
                output="out.pdbqt",
                center=center,  # type: ignore[arg-type]
                size=size,  # type: ignore[arg-type]
                exhaustiveness=max(1, int(exhaustiveness)),
                num_modes=max(1, int(num_modes)),
                energy_range=float(energy_range),
                seed=int(seed),
                cpu=max(0, int(cpu)),
            )
            result = run_process(
                command,
                cwd=temp,
                env=engine_env(),
                timeout=timeout,
                stall_timeout=stall_timeout,
                log_path=log_path,
                log_mode="w",
                on_line=on_line,
                cancel=cancel,
            )
            local_output = temp / "out.pdbqt"
            if local_output.is_file() and local_output.stat().st_size > 0:
                shutil.copy2(local_output, output_pdbqt)

        log_text = result.output
        if result.cancelled:
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error="已取消", command=result.command)
        if result.stalled:
            minutes = max(1, round((stall_timeout or 0) / 60))
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error=f"超过 {minutes} 分钟没有输出，判定为卡死",
                           command=result.command, stalled=True)
        if result.timed_out:
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error="计算超时", command=result.command)
        if result.returncode != 0:
            tail = result.tail(15) or f"{self.label} 退出码 {result.returncode}"
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error=tail, command=result.command)
        if not output_pdbqt.is_file() or output_pdbqt.stat().st_size < 80:
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error=f"{self.label} 未产生有效的对接结果文件",
                           command=result.command)

        poses = self._parse_poses(output_pdbqt, log_text)
        if not poses:
            parser_error = _extract_error(log_text)
            return VinaRun(
                False,
                log_text=log_text,
                returncode=result.returncode,
                error=parser_error or "结果文件中没有可解析的结合能",
                command=result.command,
            )
        return VinaRun(
            True,
            poses=poses,
            output_path=str(output_pdbqt),
            log_text=log_text,
            returncode=result.returncode,
            command=result.command,
        )

    def doctor(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "available": self.available,
            "executable": self.executable,
            "version": self.version,
        }


class VinaAdapter(EngineAdapter):
    key = ENGINE_VINA
    label = "AutoDock Vina"

    def _find(self, executable: str) -> str:
        return find_vina(executable)

    def _command(self, temp, *, receptor, ligand, output, center, size,
                 exhaustiveness, num_modes, energy_range, seed, cpu):
        config = "\n".join(
            [
                f"receptor = {receptor}",
                f"ligand = {ligand}",
                f"out = {output}",
                f"center_x = {center[0]:.3f}",
                f"center_y = {center[1]:.3f}",
                f"center_z = {center[2]:.3f}",
                f"size_x = {size[0]:.3f}",
                f"size_y = {size[1]:.3f}",
                f"size_z = {size[2]:.3f}",
                f"exhaustiveness = {exhaustiveness}",
                f"num_modes = {num_modes}",
                f"energy_range = {energy_range:.2f}",
                f"seed = {seed}",
                f"cpu = {cpu}",
                "verbosity = 1",
                "",
            ]
        )
        (temp / "vina.conf").write_text(config, encoding="ascii")
        return [self.executable, "--config", "vina.conf"]


class CliEngineAdapter(EngineAdapter):
    """接受 Vina 1.1.2 风格命令行参数的引擎（QuickVina 2、Smina）。"""

    def _command(self, temp, *, receptor, ligand, output, center, size,
                 exhaustiveness, num_modes, energy_range, seed, cpu):
        return [
            self.executable,
            "--receptor", receptor,
            "--ligand", ligand,
            "--out", output,
            "--center_x", f"{center[0]:.3f}",
            "--center_y", f"{center[1]:.3f}",
            "--center_z", f"{center[2]:.3f}",
            "--size_x", f"{size[0]:.3f}",
            "--size_y", f"{size[1]:.3f}",
            "--size_z", f"{size[2]:.3f}",
            "--exhaustiveness", str(exhaustiveness),
            "--num_modes", str(num_modes),
            "--energy_range", f"{energy_range:.2f}",
            "--seed", str(seed),
            "--cpu", str(cpu),
        ]


class QuickVina2Adapter(CliEngineAdapter):
    key = ENGINE_QVINA2
    label = "QuickVina 2"

    def _find(self, executable: str) -> str:
        return find_qvina2(executable)


_SMINA_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(-?\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*$"
)
_SMINA_REMARK_RE = re.compile(
    r"^REMARK\s+minimizedAffinity\s+(-?\d+(?:\.\d+)?)"
)
_MODEL_RE = re.compile(r"^MODEL\s+(\d+)\s*$")


class SminaAdapter(CliEngineAdapter):
    key = ENGINE_SMINA
    label = "Smina"

    def _find(self, executable: str) -> str:
        return find_smina(executable)

    def _parse_poses(self, output_path: Path, log_text: str) -> list[Pose]:
        poses = _parse_smina_poses(log_text, output_path)
        if poses:
            # Smina 输出的是 REMARK minimizedAffinity；补一行标准的 Vina
            # 结果备注，使重新扫描结果、复合物与报告都能复用同一套解析。
            _annotate_smina_output(output_path, poses)
        return poses


def _parse_smina_poses(log_text: str, output_path: Path) -> list[Pose]:
    """Smina 在标准输出中打印构象表，输出文件里则是 minimizedAffinity。"""
    poses: list[Pose] = []
    for line in log_text.splitlines():
        match = _SMINA_ROW_RE.match(line)
        if not match:
            continue
        rank = int(match.group(1))
        if rank != len(poses) + 1 or rank > 1000:
            continue
        poses.append(
            Pose(
                rank=rank,
                affinity=float(match.group(2)),
                rmsd_lb=float(match.group(3)),
                rmsd_ub=float(match.group(4)),
            )
        )
    if poses:
        return poses
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return poses
    for line in text.splitlines():
        match = _SMINA_REMARK_RE.match(line)
        if match:
            poses.append(
                Pose(
                    rank=len(poses) + 1,
                    affinity=float(match.group(1)),
                    rmsd_lb=0.0,
                    rmsd_ub=0.0,
                )
            )
    return poses


def _annotate_smina_output(path: Path, poses: Sequence[Pose]) -> None:
    """把 Smina 的 minimizedAffinity 改写成标准 VINA RESULT 备注。

    改写而不是追加，否则同一个构象会同时带有两种备注，重新扫描结果时
    会被解析成两份。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "REMARK VINA RESULT:" in text:
        return
    original = text.splitlines()
    ranks = []
    for line in original:
        match = _MODEL_RE.match(line)
        if match:
            rank = int(match.group(1))
            if 1 <= rank <= len(poses):
                ranks.append(rank)
    if not ranks:
        return
    lines: list[str] = []
    for line in original:
        if _SMINA_REMARK_RE.match(line):
            continue
        lines.append(line)
        match = _MODEL_RE.match(line)
        if not match:
            continue
        rank = int(match.group(1))
        if 1 <= rank <= len(poses):
            pose = poses[rank - 1]
            lines.append(
                f"REMARK VINA RESULT:    {pose.affinity:8.3f}"
                f"    {pose.rmsd_lb:8.3f}    {pose.rmsd_ub:8.3f}"
            )
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("无法写入 Smina 标准结果备注：%s", exc)


_ENGINE_CLASSES = {
    ENGINE_VINA: VinaAdapter,
    ENGINE_QVINA2: QuickVina2Adapter,
    ENGINE_SMINA: SminaAdapter,
}


def _extract_error(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.lower().startswith(("error", "parse error", "exception")):
            return stripped
    return ""
