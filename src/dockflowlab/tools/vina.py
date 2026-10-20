"""AutoDock Vina 适配层。

所有计算都在 ASCII 临时目录中完成：窗口标题、参数和输出文件名均为 ASCII，
计算结束后再复制回项目目录。这解决了 Vina 1.2.7 在 Windows 中文路径下
无法打开文件的问题。
"""

from __future__ import annotations

import math
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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


def find_vina(executable: str = "") -> str:
    candidates: list[str | Path | None] = []
    if executable:
        candidates.append(executable)
    platform_dir = "windows" if IS_WINDOWS else ("macos" if IS_MACOS else "linux")
    binary = "vina.exe" if IS_WINDOWS else "vina"
    root = app_dir()
    candidates.extend(
        [
            root / "vina.exe" if IS_WINDOWS else root / "vina",
            root / "vendor" / "vina" / platform_dir / binary,
            root / "resources" / "vina" / platform_dir / binary,
            resource_path(Path("vina") / platform_dir / binary),
            Path("/opt/homebrew/bin/vina"),
            Path("/usr/local/bin/vina"),
        ]
    )
    return find_executable(*candidates, names=("vina", "vina.exe"))


@dataclass
class VinaRun:
    ok: bool
    poses: list[Pose] = field(default_factory=list)
    output_path: str = ""
    log_text: str = ""
    returncode: int = 0
    error: str = ""
    command: list[str] = field(default_factory=list)


class VinaAdapter:
    def __init__(self, executable: str = "") -> None:
        self.executable = find_vina(executable)
        self._version: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file())

    @property
    def version(self) -> str:
        if self._version is None:
            if not self.available:
                self._version = ""
            else:
                result = run_process([self.executable, "--version"], timeout=20)
                text = (result.output or "").strip()
                self._version = text.splitlines()[0] if text else ""
        return self._version

    def require(self) -> None:
        if not self.available:
            raise DependencyError(
                "未找到 AutoDock Vina。请在“设置”中指定 vina 可执行文件，"
                "或把 vina 放到程序的 resources/vina 目录中。"
            )

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

        config = "\n".join(
            [
                "receptor = receptor.pdbqt",
                "ligand = ligand.pdbqt",
                "out = out.pdbqt",
                f"center_x = {center[0]:.3f}",
                f"center_y = {center[1]:.3f}",
                f"center_z = {center[2]:.3f}",
                f"size_x = {size[0]:.3f}",
                f"size_y = {size[1]:.3f}",
                f"size_z = {size[2]:.3f}",
                f"exhaustiveness = {max(1, int(exhaustiveness))}",
                f"num_modes = {max(1, int(num_modes))}",
                f"energy_range = {float(energy_range):.2f}",
                f"seed = {int(seed)}",
                f"cpu = {max(0, int(cpu))}",
                "verbosity = 1",
                "",
            ]
        )

        output_pdbqt.parent.mkdir(parents=True, exist_ok=True)
        with work_directory("vina") as temp:
            shutil.copy2(receptor_pdbqt, temp / "receptor.pdbqt")
            shutil.copy2(ligand_pdbqt, temp / "ligand.pdbqt")
            (temp / "vina.conf").write_text(config, encoding="ascii")
            result = run_process(
                [self.executable, "--config", "vina.conf"],
                cwd=temp,
                timeout=timeout,
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
        if result.timed_out:
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error="计算超时", command=result.command)
        if result.returncode != 0:
            tail = result.tail(15) or f"Vina 退出码 {result.returncode}"
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error=tail, command=result.command)
        if not output_pdbqt.is_file() or output_pdbqt.stat().st_size < 80:
            return VinaRun(False, log_text=log_text, returncode=result.returncode,
                           error="Vina 未产生有效的对接结果文件", command=result.command)

        poses = parse_pdbqt_poses(output_pdbqt)
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
            "available": self.available,
            "executable": self.executable,
            "version": self.version,
        }


def _extract_error(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.lower().startswith(("error", "parse error", "exception")):
            return stripped
    return ""
