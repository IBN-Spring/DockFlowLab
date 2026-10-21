"""分子对接执行服务。"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..config import AppSettings
from ..core import pdbio
from ..core.models import Box, DockingResult, ProjectFile
from ..errors import DockingError, InputError
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import open_path
from ..tools.vina import VinaAdapter
from ..workspace import Workspace
from . import boxes as box_service
from . import modes
from .pymol import write_pml

log = get_logger("docking")

Progress = Callable[[int, int, str], None] | None
LineCallback = Callable[[str, str, str], None] | None


@dataclass
class DockingOptions:
    exhaustiveness: int = 16
    num_modes: int = 9
    energy_range: float = 3.0
    seed: int = 42
    cpu: int = 0
    skip_existing: bool = True
    make_complex: bool = True
    make_pymol: bool = True
    # 单组对接的兜底超时。正常计算通常只需几分钟；设置上限是为了避免
    # 外部进程卡死时任务无限期挂起（用户仍然可以随时手动取消）。
    timeout_seconds: float | None = 6 * 3600.0

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "DockingOptions":
        return cls(
            exhaustiveness=settings.exhaustiveness,
            num_modes=settings.num_modes,
            energy_range=settings.energy_range,
            seed=settings.seed,
            cpu=settings.cpu,
        )


def _notify(progress: Progress, current: int, total: int, message: str) -> None:
    if progress is not None:
        try:
            progress(current, total, message)
        except Exception:
            pass


def _result_paths(workspace: Workspace, target: ProjectFile, ligand: ProjectFile) -> tuple[Path, Path, Path, Path]:
    output = workspace.docking_output(target.stem, ligand.stem)
    log_path = workspace.docking_log(target.stem, ligand.stem)
    complex_path = workspace.complex_path(target.stem, ligand.stem)
    pml_path = workspace.pymol_path(target.stem, ligand.stem)
    return output, log_path, complex_path, pml_path


def _read_meta(workspace: Workspace, target_stem: str, ligand_stem: str) -> dict[str, Any]:
    path = workspace.docking_meta(target_stem, ligand_stem)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(workspace: Workspace, result: DockingResult) -> None:
    path = workspace.docking_meta(result.target, result.ligand)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "target": result.target,
        "ligand": result.ligand,
        "ok": result.ok,
        "box_source": result.box_source,
        "runtime_seconds": round(result.runtime_seconds, 2),
        "warnings": list(result.warnings),
        "error": result.error,
        "mode_key": result.mode_key,
        "mode_label": result.mode_label,
        "mode_supported": result.mode_supported,
        "parameters": dict(result.parameters),
        "box_center": list(result.box_center) if result.box_center else None,
        "box_size": list(result.box_size) if result.box_size else None,
        "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _run_parameters(options: DockingOptions, vina: VinaAdapter) -> dict[str, Any]:
    """记录实际传给 Vina 的参数，而不是报告生成时的当前设置。"""
    try:
        version = str(vina.version or "")
    except Exception:
        version = ""
    return {
        "exhaustiveness": int(options.exhaustiveness),
        "num_modes": int(options.num_modes),
        "energy_range": float(options.energy_range),
        "seed": int(options.seed),
        "cpu": int(options.cpu),
        "vina_version": version,
    }


def _stored_parameters(meta: dict[str, Any]) -> dict[str, Any]:
    """只取已知字段，避免把旧的/损坏的 JSON 原样带进报告。"""
    raw = meta.get("parameters")
    if not isinstance(raw, dict):
        return {}
    keys = ("exhaustiveness", "num_modes", "energy_range", "seed", "cpu", "vina_version")
    return {key: raw[key] for key in keys if raw.get(key) not in (None, "")}


def _stored_point(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _target_pdb(workspace: Workspace, target: ProjectFile, obabel: OpenBabelAdapter) -> Path:
    """返回用于生成复合物的受体 PDB。"""
    prepared = target.meta.get("prepared_pdb", "")
    if prepared:
        path = workspace.absolute(prepared)
        if path.is_file():
            return path
    standard = workspace.prepared_target_pdb(target.stem)
    if standard.is_file():
        return standard
    source = workspace.absolute(target.source)
    fmt = pdbio.detect_format(source)
    if fmt in {"pdb", "pdbqt"}:
        if fmt == "pdb":
            return source
        converted = workspace.prepared_target_pdb(target.stem)
        converted.parent.mkdir(parents=True, exist_ok=True)
        if obabel.available and obabel.convert(source, converted, out_format="pdb")[0]:
            return converted
        atoms = pdbio.parse_pdbqt_atoms(source)
        pdbio.write_pdb_atoms(atoms, converted, resname="REC", chain="A")
        return converted
    if fmt == "cif" and obabel.available:
        converted = workspace.prepared_target_pdb(target.stem)
        converted.parent.mkdir(parents=True, exist_ok=True)
        if obabel.convert(source, converted, out_format="pdb")[0]:
            return converted
    raise DockingError(f"无法找到靶点 {target.display_name} 的结构文件")


def _ensure_complex_and_pymol(
    workspace: Workspace,
    target: ProjectFile,
    ligand: ProjectFile,
    output: Path,
    complex_path: Path,
    pml_path: Path,
    obabel: OpenBabelAdapter,
    options: DockingOptions,
    warnings: list[str],
) -> None:
    if not options.make_complex:
        return
    try:
        if not complex_path.is_file():
            receptor_pdb = _target_pdb(workspace, target, obabel)
            complex_path.parent.mkdir(parents=True, exist_ok=True)
            pdbio.make_complex(receptor_pdb, output, complex_path)
        if options.make_pymol and not pml_path.is_file():
            write_pml(
                complex_path,
                pml_path,
                title=f"{target.display_name} + {ligand.display_name}",
            )
    except Exception as exc:
        log.warning("生成复合物失败：%s", exc, exc_info=True)
        warnings.append(f"复合物/可视化文件生成失败：{exc}")


def dock_pair(
    workspace: Workspace,
    target: ProjectFile,
    ligand: ProjectFile,
    box: Box,
    *,
    options: DockingOptions | None = None,
    vina: VinaAdapter | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
    on_line: LineCallback = None,
) -> DockingResult:
    options = options or DockingOptions()
    vina = vina or VinaAdapter()
    obabel = obabel or get_openbabel()
    mode = modes.pair_mode(target, ligand, workspace)
    warnings: list[str] = list(mode.warnings)
    output, log_path, complex_path, pml_path = _result_paths(workspace, target, ligand)
    result = DockingResult(
        target=target.stem,
        ligand=ligand.stem,
        ok=False,
        log_path=workspace.relative(log_path),
        output_path=workspace.relative(output),
        complex_path=workspace.relative(complex_path),
        pymol_path=workspace.relative(pml_path),
        box_source=box.source,
        mode_key=mode.key,
        mode_label=mode.label,
        mode_supported=mode.supported,
    )

    # 不支持的组合直接返回明确原因，绝不调用 Vina 产生误导性的分数。
    if not mode.supported:
        result.error = mode.error_text
        return result

    if not target.prepared:
        result.error = "靶点尚未准备"
        return result
    if not ligand.prepared:
        result.error = "配体尚未准备"
        return result
    receptor_pdbqt = workspace.absolute(target.prepared)
    ligand_pdbqt = workspace.absolute(ligand.prepared)
    if not receptor_pdbqt.is_file():
        result.error = "靶点预处理文件缺失，请重新准备"
        return result
    if not ligand_pdbqt.is_file():
        result.error = "配体预处理文件缺失，请重新准备"
        return result

    output.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if options.skip_existing and output.is_file():
        poses = pdbio.parse_pdbqt_poses(output)
        valid, _, _ = pdbio.validate_pdbqt(output)
        if valid and poses:
            meta = _read_meta(workspace, target.stem, ligand.stem)
            result.ok = True
            result.poses = poses
            result.skipped = True
            result.runtime_seconds = float(meta.get("runtime_seconds") or 0.0)
            result.box_source = str(meta.get("box_source") or box.source)
            # 旧结果沿用生成时的参数与盒子，不能用当前设置覆盖。
            result.parameters = _stored_parameters(meta)
            result.box_center = _stored_point(meta.get("box_center"))
            result.box_size = _stored_point(meta.get("box_size"))
            warnings = [str(item) for item in meta.get("warnings") or []] + warnings
            result.warnings = warnings
            _ensure_complex_and_pymol(
                workspace, target, ligand, output, complex_path, pml_path,
                obabel, options, warnings,
            )
            _write_meta(workspace, result)
            _notify(progress, 1, 1, f"{target.display_name} + {ligand.display_name}：已有结果，跳过")
            return result

    _notify(progress, 0, 1, f"正在对接 {target.display_name} + {ligand.display_name}…")
    if on_line is not None:
        def line_callback(line: str) -> None:
            try:
                on_line(target.stem, ligand.stem, line)
            except Exception:
                pass
    else:
        line_callback = None

    result.parameters = _run_parameters(options, vina)
    result.box_center = tuple(float(value) for value in box.center)  # type: ignore[assignment]
    result.box_size = tuple(float(value) for value in box.size)  # type: ignore[assignment]
    started = time.perf_counter()
    try:
        run = vina.dock(
            receptor_pdbqt,
            ligand_pdbqt,
            box,
            output,
            exhaustiveness=options.exhaustiveness,
            num_modes=options.num_modes,
            energy_range=options.energy_range,
            seed=options.seed,
            cpu=options.cpu,
            timeout=options.timeout_seconds,
            log_path=log_path,
            on_line=line_callback,
            cancel=cancel,
        )
    except (InputError, DockingError) as exc:
        result.error = str(exc)
        return result
    except Exception as exc:
        log.exception("Vina 对接异常")
        result.error = str(exc)
        return result
    result.runtime_seconds = time.perf_counter() - started

    if not run.ok:
        result.error = run.error or "对接失败"
        if not log_path.is_file():
            log_path.write_text(f"[DockFlowLab] {result.error}\n{run.log_text}", encoding="utf-8")
        return result

    valid, message, _ = pdbio.validate_pdbqt(output)
    if not valid:
        result.error = f"对接结果文件异常：{message}"
        return result

    result.ok = True
    result.poses = run.poses or pdbio.parse_pdbqt_poses(output)
    result.warnings = warnings
    _ensure_complex_and_pymol(
        workspace, target, ligand, output, complex_path, pml_path,
        obabel, options, warnings,
    )
    _write_meta(workspace, result)
    _notify(
        progress,
        1,
        1,
        f"{target.display_name} + {ligand.display_name} 完成，最佳结合能 "
        f"{result.best_affinity:.2f} kcal/mol",
    )
    return result


def run_batch(
    workspace: Workspace,
    pairs: Sequence[tuple[ProjectFile, ProjectFile]],
    boxes: dict[str, Box],
    *,
    options: DockingOptions | None = None,
    settings: AppSettings | None = None,
    vina: VinaAdapter | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
    on_line: LineCallback = None,
) -> list[DockingResult]:
    """依次执行若干 (靶点, 配体) 对接。单个失败不会中断其余任务。"""
    options = options or DockingOptions()
    settings = settings or AppSettings()
    vina = vina or VinaAdapter()
    obabel = obabel or get_openbabel()
    total = len(pairs)
    results: list[DockingResult] = []
    if total == 0:
        return results

    for index, (target, ligand) in enumerate(pairs, start=1):
        if cancel is not None and cancel.is_set():
            break
        # 只在每组完成后推进进度，避免刚开始就显示 100%。
        _notify(progress, index - 1, total, f"[{index}/{total}] {target.display_name} + {ligand.display_name}")
        mode = modes.pair_mode(target, ligand, workspace)
        if not mode.supported:
            results.append(
                DockingResult(
                    target=target.stem,
                    ligand=ligand.stem,
                    ok=False,
                    error=mode.error_text,
                    mode_key=mode.key,
                    mode_label=mode.label,
                    mode_supported=False,
                )
            )
            continue
        box = boxes.get(target.stem)
        if box is None:
            try:
                box, warnings = box_service.suggest_box(
                    target_source=workspace.absolute(target.source),
                    target_prepared_pdb=workspace.prepared_target_pdb(target.stem),
                    ligand_paths=[workspace.absolute(ligand.prepared)] if ligand.prepared else [],
                    settings=settings,
                    target_kind=modes.target_kind_for(target, workspace),
                    chains=target.meta.get("chains", []),
                    obabel=obabel,
                )
                for warning in warnings:
                    log.info("%s：%s", target.display_name, warning)
            except Exception as exc:
                results.append(
                    DockingResult(
                        target=target.stem,
                        ligand=ligand.stem,
                        ok=False,
                        error=f"无法生成对接盒子：{exc}",
                        mode_key=mode.key,
                        mode_label=mode.label,
                        mode_supported=True,
                    )
                )
                continue

        try:
            result = dock_pair(
                workspace,
                target,
                ligand,
                box,
                options=options,
                vina=vina,
                obabel=obabel,
                progress=None,
                cancel=cancel,
                on_line=on_line,
            )
        except Exception as exc:
            log.error("对接任务异常：%s\n%s", exc, traceback.format_exc())
            result = DockingResult(
                target=target.stem,
                ligand=ligand.stem,
                ok=False,
                error=str(exc),
                box_source=box.source,
                mode_key=mode.key,
                mode_label=mode.label,
                mode_supported=True,
            )
        results.append(result)
        _notify(
            progress,
            index,
            total,
            (
                f"[{index}/{total}] 完成：{target.display_name} + {ligand.display_name}，"
                + (f"{result.best_affinity:.2f} kcal/mol" if result.best_affinity is not None else result.error)
            ),
        )
    return results


def collect_existing_results(workspace: Workspace) -> list[DockingResult]:
    """扫描项目目录中已存在的对接结果。"""
    results: list[DockingResult] = []
    for target in workspace.targets:
        if not target.is_ready:
            continue
        for ligand in workspace.ligands:
            if not ligand.is_ready:
                continue
            output = workspace.docking_output(target.stem, ligand.stem)
            if not output.is_file():
                continue
            mode = modes.pair_mode(target, ligand, workspace)
            if not mode.supported:
                # 旧版本可能留下了不受支持的组合结果；不要把它们当作有效
                # 分数展示，避免用户误用蛋白–蛋白等不适用的 Vina 结果。
                continue
            poses = pdbio.parse_pdbqt_poses(output)
            if not poses:
                continue
            meta = _read_meta(workspace, target.stem, ligand.stem)
            box = workspace.load_box(target.stem)
            results.append(
                DockingResult(
                    target=target.stem,
                    ligand=ligand.stem,
                    ok=True,
                    poses=poses,
                    mode_key=mode.key,
                    mode_label=mode.label,
                    mode_supported=True,
                    log_path=workspace.relative(workspace.docking_log(target.stem, ligand.stem))
                    if workspace.docking_log(target.stem, ligand.stem).is_file() else "",
                    output_path=workspace.relative(output),
                    complex_path=workspace.relative(workspace.complex_path(target.stem, ligand.stem))
                    if workspace.complex_path(target.stem, ligand.stem).is_file() else "",
                    pymol_path=workspace.relative(workspace.pymol_path(target.stem, ligand.stem))
                    if workspace.pymol_path(target.stem, ligand.stem).is_file() else "",
                    runtime_seconds=float(meta.get("runtime_seconds") or 0.0),
                    box_source=str(meta.get("box_source") or (box.source if box else "")),
                    warnings=[str(item) for item in meta.get("warnings") or []],
                    parameters=_stored_parameters(meta),
                    box_center=_stored_point(meta.get("box_center")),
                    box_size=_stored_point(meta.get("box_size")),
                )
            )
    return results
