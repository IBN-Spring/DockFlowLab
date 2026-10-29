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
from ..tools.vina import EngineAdapter, make_engine
from ..workspace import Workspace
from . import boxes as box_service
from . import flexible as flex_service
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
    engine: str = "vina"
    # Smina 评分函数："" / "default" 使用 Vina 评分，"vinardo" 使用 Vinardo。
    scoring: str = ""
    # 首选引擎卡死/失败时，自动换用其他已安装引擎重试一次。
    fallback: bool = True
    skip_existing: bool = True
    make_complex: bool = True
    make_pymol: bool = True
    # 单组对接的兜底超时。正常计算通常只需几分钟；设置上限是为了避免
    # 外部进程卡死时任务无限期挂起（用户仍然可以随时手动取消）。
    timeout_seconds: float | None = 6 * 3600.0
    # 对接引擎持续无输出超过该时长即判定为卡死并终止。Vina 系引擎会
    # 定期打印搜索进度，20 分钟无任何输出基本可以确认已经停滞。
    stall_timeout_seconds: float | None = 20 * 60.0

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "DockingOptions":
        return cls(
            exhaustiveness=settings.exhaustiveness,
            num_modes=settings.num_modes,
            energy_range=settings.energy_range,
            seed=settings.seed,
            cpu=settings.cpu,
            engine=settings.docking_engine,
            scoring=(
                ""
                if getattr(settings, "smina_scoring", "default") == "default"
                else str(settings.smina_scoring)
            ),
            fallback=settings.engine_fallback,
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


def ligand_states(workspace: Workspace, ligand: ProjectFile) -> list[tuple[str, Path]]:
    """返回 [(状态标签, PDBQT 路径)]，第一项始终是主结构。"""
    primary = workspace.absolute(ligand.prepared)
    states: list[tuple[str, Path]] = [("状态 1（原始）", primary)]
    details = ligand.meta.get("prep_details") or {}
    raw = details.get("states") if isinstance(details, dict) else None
    if not isinstance(raw, list):
        return states
    base = workspace.prepared_ligand_state_dir(ligand.stem)
    for item in raw[1:]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "")
        if not name:
            continue
        path = base / name
        if not path.is_file():
            continue
        label = str(item.get("label") or f"状态 {item.get('index') or len(states) + 1}")
        states.append((label, path))
    return states


def _state_output(output: Path, index: int) -> Path:
    if index <= 1:
        return output
    return output.with_name(f"{output.stem}_state{index}{output.suffix}")


def _state_log(log_path: Path, index: int) -> Path:
    if index <= 1:
        return log_path
    return log_path.with_name(f"{log_path.stem}_state{index}{log_path.suffix}")


def _state_summary(scores: list[dict[str, Any]]) -> str:
    parts = []
    for item in scores:
        value = item.get("affinity")
        if value is None:
            parts.append(f"{item['label']}：未完成")
        else:
            parts.append(f"{item['label']}：{float(value):.2f}")
    return "；".join(parts)


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
        # 多状态对接的最优结果可能不是标准输出文件（例如 *_state4.pdbqt），
        # 记录实际文件，重新打开项目时才能还原同一份构象与分数。
        "output": str(result.output_path or ""),
        "parameters": dict(result.parameters),
        "box_center": list(result.box_center) if result.box_center else None,
        "box_size": list(result.box_size) if result.box_size else None,
        "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _run_parameters(options: DockingOptions, engine: EngineAdapter) -> dict[str, Any]:
    """记录实际传给对接引擎的参数，而不是报告生成时的当前设置。"""
    try:
        version = str(engine.version or "")
    except Exception:
        version = ""
    parameters = {
        "exhaustiveness": int(options.exhaustiveness),
        "num_modes": int(options.num_modes),
        "energy_range": float(options.energy_range),
        "seed": int(options.seed),
        "cpu": int(options.cpu),
        "engine": engine.key,
        "engine_label": engine.label,
        "engine_version": version,
        # 旧版本只记录 vina_version；保留它以便报告与旧结果继续兼容。
        "vina_version": version if engine.key == "vina" else "",
    }
    if options.scoring and engine.key == "smina":
        parameters["scoring"] = str(options.scoring)
    return parameters


_PARAMETER_KEYS = (
    "exhaustiveness",
    "num_modes",
    "energy_range",
    "seed",
    "cpu",
    "engine",
    "engine_label",
    "engine_version",
    "vina_version",
    "scoring",
    # 2.5.0 起记录柔性残基与多状态枚举，便于复现与比较。
    "flex_residues",
    "flex_count",
    "ligand_state",
    "ligand_state_count",
    "ligand_state_scores",
)


def _stored_parameters(meta: dict[str, Any]) -> dict[str, Any]:
    """只取已知字段，避免把旧的/损坏的 JSON 原样带进报告。"""
    raw = meta.get("parameters")
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in _PARAMETER_KEYS if raw.get(key) not in (None, "")}


def _stored_output(workspace: Workspace, meta: dict[str, Any]) -> Path | None:
    """meta 中记录的对接结果文件；旧结果没有该字段时返回 ``None``。"""
    raw = str(meta.get("output") or "").strip()
    if not raw:
        return None
    path = workspace.absolute(raw)
    return path if path.is_file() else None


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
    overwrite: bool = False,
) -> None:
    if not options.make_complex:
        return
    try:
        if overwrite:
            # 重新计算后，旧的复合物/可视化脚本仍然是上一次的结果。
            complex_path.unlink(missing_ok=True)
            pml_path.unlink(missing_ok=True)
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
    vina: EngineAdapter | None = None,
    fallback_engines: Sequence[EngineAdapter] = (),
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
    on_line: LineCallback = None,
) -> DockingResult:
    options = options or DockingOptions()
    vina = vina or make_engine(options.engine)
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

    states = ligand_states(workspace, ligand)
    current_flex = "、".join(
        item.label for item in flex_service.selected_residues(target)
    )

    stored_meta = _read_meta(workspace, target.stem, ligand.stem)
    # 多状态对接的最优构象可能写在 *_stateN.pdbqt，优先沿用记录的那一份。
    stored_output = _stored_output(workspace, stored_meta) or output
    if options.skip_existing and stored_output.is_file():
        poses = pdbio.parse_pdbqt_poses(stored_output)
        valid, _, _ = pdbio.validate_pdbqt(stored_output)
        stored = _stored_parameters(stored_meta)
        # 状态数量或柔性残基选择变化后，旧结果不能直接复用。
        reusable = (
            int(stored.get("ligand_state_count") or 1) == len(states)
            and str(stored.get("flex_residues") or "") == current_flex
        )
        if valid and poses and reusable:
            meta = stored_meta
            result.ok = True
            result.poses = poses
            result.skipped = True
            result.output_path = workspace.relative(stored_output)
            result.runtime_seconds = float(meta.get("runtime_seconds") or 0.0)
            result.box_source = str(meta.get("box_source") or box.source)
            # 旧结果沿用生成时的参数与盒子，不能用当前设置覆盖。
            result.parameters = _stored_parameters(meta)
            result.box_center = _stored_point(meta.get("box_center"))
            result.box_size = _stored_point(meta.get("box_size"))
            warnings = [str(item) for item in meta.get("warnings") or []] + warnings
            result.warnings = warnings
            _ensure_complex_and_pymol(
                workspace, target, ligand, stored_output, complex_path, pml_path,
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

    # 首选引擎在前；仅在它失败（卡死、崩溃、结果无效）时才尝试其他引擎。
    # 输入错误不会触发回退，因为换引擎也无法解决文件本身的问题。
    engines: list[EngineAdapter] = [vina]
    if options.fallback:
        for candidate in fallback_engines:
            if candidate is None or candidate.key in {item.key for item in engines}:
                continue
            if getattr(candidate, "available", False):
                engines.append(candidate)

    # 柔性残基：按靶点的选择拆分受体，所有状态共用同一份。
    flex_residues = flex_service.selected_residues(target)
    flex_receptor = None
    if flex_residues:
        if not any(getattr(engine, "supports_flexible", False) for engine in engines):
            result.error = (
                f"选中的引擎（{'、'.join(item.label for item in engines)}）不支持柔性残基，"
                "请改用 AutoDock Vina 或 Smina"
            )
            return result
        try:
            flex_receptor = flex_service.ensure_flexible_receptor(workspace, target)
        except flex_service.FlexibleResidueError as exc:
            result.error = f"柔性残基准备失败：{exc}"
            return result
        except Exception as exc:  # Meeko 的异常类型不一定可预期
            log.exception("柔性残基拆分失败")
            result.error = f"柔性残基准备失败：{exc}"
            return result
    if flex_receptor is not None:
        warnings.append(
            f"本次柔化了 {flex_receptor.count} 个残基（{flex_receptor.labels}）；"
            "侧链会与配体一起采样，但复合物与可视化文件中的侧链仍是原始构象"
        )
        if flex_receptor.omitted:
            warnings.append(
                "以下残基没有可移动的侧链原子，已按刚性处理："
                + "、".join(flex_receptor.omitted)
            )

    states = ligand_states(workspace, ligand)
    if len(states) > 1:
        warnings.append(
            f"该分子已枚举 {len(states)} 个互变异构/质子化状态，将逐个对接并取最优"
        )

    result.box_center = tuple(float(value) for value in box.center)  # type: ignore[assignment]
    result.box_size = tuple(float(value) for value in box.size)  # type: ignore[assignment]
    started = time.perf_counter()

    state_scores: list[dict[str, Any]] = []
    best: tuple[float, Any, Path, EngineAdapter, int] | None = None
    first_error = ""
    used_engine: EngineAdapter | None = None

    for state_index, (state_label, state_path) in enumerate(states, start=1):
        if cancel is not None and cancel.is_set():
            break
        state_output = _state_output(output, state_index)
        state_log = _state_log(log_path, state_index)
        if len(states) > 1:
            _notify(
                progress, 0, 1,
                f"正在对接 {target.display_name} + {ligand.display_name}（{state_label}）…",
            )
        run = None
        state_error = ""
        for index, engine in enumerate(engines):
            if flex_receptor is not None and not getattr(engine, "supports_flexible", False):
                continue
            result.parameters = _run_parameters(options, engine)
            if flex_receptor is not None:
                result.parameters["flex_residues"] = flex_receptor.labels
                result.parameters["flex_count"] = flex_receptor.count
            if len(states) > 1:
                result.parameters["ligand_state"] = state_label
                result.parameters["ligand_state_count"] = len(states)
            try:
                run = engine.dock(
                    receptor_pdbqt,
                    state_path,
                    box,
                    state_output,
                    exhaustiveness=options.exhaustiveness,
                    num_modes=options.num_modes,
                    energy_range=options.energy_range,
                    seed=options.seed,
                    cpu=options.cpu,
                    scoring=options.scoring if engine.key == "smina" else "",
                    flexible_pdbqt=flex_receptor.flex if flex_receptor is not None else None,
                    timeout=options.timeout_seconds,
                    stall_timeout=options.stall_timeout_seconds,
                    log_path=state_log,
                    on_line=line_callback,
                    cancel=cancel,
                )
            except (InputError, DockingError) as exc:
                state_error = str(exc)
                break
            except Exception as exc:
                log.exception("%s 对接异常", engine.label)
                state_error = str(exc)
                break
            result.runtime_seconds = time.perf_counter() - started
            if run.ok:
                used_engine = engine
                break

            state_error = run.error or "对接失败"
            if not state_log.is_file():
                state_log.write_text(
                    f"[DockFlowLab] {state_error}\n{run.log_text}", encoding="utf-8"
                )
            if cancel is not None and cancel.is_set():
                break
            remaining = engines[index + 1:]
            if flex_receptor is not None:
                remaining = [
                    item for item in remaining
                    if getattr(item, "supports_flexible", False)
                ]
            next_engine = remaining[0] if remaining else None
            if next_engine is None:
                break
            message = (
                f"{engine.label} 未能完成（{state_error}），"
                f"已自动改用 {next_engine.label} 重试"
            )
            log.warning("%s + %s：%s", target.stem, ligand.stem, message)
            if message not in warnings:
                warnings.append(message)
            result.warnings = list(warnings)
            _notify(progress, 0, 1, message)

        success = run is not None and run.ok
        affinity = run.poses[0].affinity if success and run.poses else None
        state_scores.append(
            {
                "label": state_label,
                "affinity": affinity,
                "file": workspace.relative(state_output),
                "engine": used_engine.key if success and used_engine else "",
                "error": "" if success else (state_error or "对接失败"),
            }
        )
        if not success:
            first_error = first_error or state_error or "对接失败"
            continue
        if best is None or float(affinity) < best[0]:
            best = (float(affinity), run, state_output, used_engine, state_index)

    _merge_state_logs(log_path, len(states))

    if best is None:
        result.error = first_error or "对接失败"
        if len(states) > 1 and len({item["error"] for item in state_scores}) > 1:
            result.error = "所有状态都未能完成对接：" + "；".join(
                f"{item['label']}：{item['error']}" for item in state_scores
            )
        result.warnings = warnings
        return result

    _best_affinity, best_run, best_output, used_engine, best_index = best
    best_label = states[best_index - 1][0]
    valid, message, _ = pdbio.validate_pdbqt(best_output)
    if not valid:
        result.error = f"对接结果文件异常：{message}"
        result.warnings = warnings
        return result

    result.ok = True
    result.poses = best_run.poses or pdbio.parse_pdbqt_poses(best_output)
    result.output_path = workspace.relative(best_output)
    result.warnings = warnings
    if len(states) > 1:
        result.parameters["ligand_state"] = best_label
        result.parameters["ligand_state_count"] = len(states)
        result.parameters["ligand_state_scores"] = _state_summary(state_scores)
        if best_index != 1:
            warnings.append(
                f"最优结果来自 {best_label}（其余状态见 {result.output_path} 同目录）"
            )
    _ensure_complex_and_pymol(
        workspace, target, ligand, best_output, complex_path, pml_path,
        obabel, options, warnings, overwrite=True,
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


def _merge_state_logs(log_path: Path, state_count: int) -> None:
    """把各状态的日志合并到主日志，方便用户一次看到全部得分。"""
    if state_count <= 1:
        return
    extras = [
        log_path.with_name(f"{log_path.stem}_state{index}{log_path.suffix}")
        for index in range(2, state_count + 1)
    ]
    existing = [path for path in extras if path.is_file()]
    if not existing:
        return
    try:
        chunks: list[str] = []
        if log_path.is_file():
            chunks.append(log_path.read_text(encoding="utf-8", errors="replace"))
        for index, path in enumerate(extras, start=2):
            if not path.is_file():
                continue
            chunks.append(
                f"\n===== 状态 {index} =====\n"
                + path.read_text(encoding="utf-8", errors="replace")
            )
            path.unlink(missing_ok=True)
        log_path.write_text("\n".join(chunks), encoding="utf-8")
    except OSError as exc:  # 日志合并失败不影响对接结果
        log.warning("合并状态日志失败：%s", exc)


def run_batch(
    workspace: Workspace,
    pairs: Sequence[tuple[ProjectFile, ProjectFile]],
    boxes: dict[str, Box],
    *,
    options: DockingOptions | None = None,
    settings: AppSettings | None = None,
    vina: EngineAdapter | None = None,
    fallback_engines: Sequence[EngineAdapter] = (),
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
    on_line: LineCallback = None,
) -> list[DockingResult]:
    """依次执行若干 (靶点, 配体) 对接。单个失败不会中断其余任务。"""
    options = options or DockingOptions()
    settings = settings or AppSettings()
    vina = vina or make_engine(options.engine)
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
                fallback_engines=fallback_engines,
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
            meta = _read_meta(workspace, target.stem, ligand.stem)
            # 多状态对接的最优结果可能记录在 *_stateN.pdbqt，优先读取它。
            output = _stored_output(workspace, meta) or workspace.docking_output(
                target.stem, ligand.stem
            )
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
