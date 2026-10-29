"""重对接验证（redocking）。

从原始结构中提取共晶配体，按当前盒子和对接参数重新对接，再与实验构象
比较对称校正的重原子 RMSD。RMSD ≤ 2 Å 视为通过，说明盒子与搜索参数
至少能复现这个已知答案；它不能证明对接结果在数学上是全局最优。

验证结果单独写入 ``validation/<靶点>/``，不覆盖用户自己的对接结果。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from ..core import pdbio, rmsd
from ..core.models import Box, ProjectFile
from ..core.pdbio import LigandSite
from ..errors import DockingError, InputError, PreparationError
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.vina import EngineAdapter, make_engine
from ..workspace import Workspace
from .docking import DockingOptions
from .ligand import LigandPrepOptions, prepare_ligand

log = get_logger("redocking")

Progress = Callable[[str], None] | None
RMSD_THRESHOLD = 2.0


@dataclass
class RedockingResult:
    target: str
    ok: bool
    passed: bool = False
    rmsd: float | None = None
    rmsd_method: str = ""
    rmsd_message: str = ""
    threshold: float = RMSD_THRESHOLD
    reference_atoms: int = 0
    pose_atoms: int = 0
    matched_atoms: int = 0
    site_label: str = ""
    site_resname: str = ""
    site_chain: str = ""
    site_resseq: int = 0
    pose_count: int = 0
    best_affinity: float | None = None
    output_path: str = ""
    log_path: str = ""
    reference_path: str = ""
    box_source: str = ""
    box_center: tuple[float, float, float] | None = None
    box_size: tuple[float, float, float] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    runtime_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    cached: bool = False

    @property
    def summary_text(self) -> str:
        if self.error:
            return self.error
        if self.rmsd is None:
            return self.rmsd_message or "重对接完成，但无法计算 RMSD"
        verdict = "通过" if self.passed else "未通过"
        return (
            f"重对接 RMSD {self.rmsd:.2f} Å（对称校正），"
            f"{verdict}；阈值 {self.threshold:.1f} Å"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ok": self.ok,
            "passed": self.passed,
            "rmsd": self.rmsd,
            "rmsd_method": self.rmsd_method,
            "rmsd_message": self.rmsd_message,
            "threshold": self.threshold,
            "reference_atoms": self.reference_atoms,
            "pose_atoms": self.pose_atoms,
            "matched_atoms": self.matched_atoms,
            "site_label": self.site_label,
            "site_resname": self.site_resname,
            "site_chain": self.site_chain,
            "site_resseq": self.site_resseq,
            "pose_count": self.pose_count,
            "best_affinity": self.best_affinity,
            "output_path": self.output_path,
            "log_path": self.log_path,
            "reference_path": self.reference_path,
            "box_source": self.box_source,
            "box_center": list(self.box_center) if self.box_center else None,
            "box_size": list(self.box_size) if self.box_size else None,
            "parameters": dict(self.parameters),
            "runtime_seconds": round(self.runtime_seconds, 2),
            "warnings": list(self.warnings),
            "error": self.error,
            "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RedockingResult":
        return cls(
            target=str(data.get("target", "")),
            ok=bool(data.get("ok")),
            passed=bool(data.get("passed")),
            rmsd=data.get("rmsd"),
            rmsd_method=str(data.get("rmsd_method", "")),
            rmsd_message=str(data.get("rmsd_message", "")),
            threshold=float(data.get("threshold", RMSD_THRESHOLD)),
            reference_atoms=int(data.get("reference_atoms", 0)),
            pose_atoms=int(data.get("pose_atoms", 0)),
            matched_atoms=int(data.get("matched_atoms", 0)),
            site_label=str(data.get("site_label", "")),
            site_resname=str(data.get("site_resname", "")),
            site_chain=str(data.get("site_chain", "")),
            site_resseq=int(data.get("site_resseq", 0)),
            pose_count=int(data.get("pose_count", 0)),
            best_affinity=data.get("best_affinity"),
            output_path=str(data.get("output_path", "")),
            log_path=str(data.get("log_path", "")),
            reference_path=str(data.get("reference_path", "")),
            box_source=str(data.get("box_source", "")),
            box_center=_point(data.get("box_center")),
            box_size=_point(data.get("box_size")),
            parameters=dict(data.get("parameters") or {}),
            runtime_seconds=float(data.get("runtime_seconds") or 0.0),
            warnings=[str(item) for item in data.get("warnings") or []],
            error=str(data.get("error", "")),
            cached=True,
        )


def _point(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _notify(progress: Progress, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            pass


def _source_pdb(
    workspace: Workspace,
    target: ProjectFile,
    obabel: OpenBabelAdapter,
) -> tuple[Path | None, str]:
    """返回可解析共晶配体的 PDB；mmCIF 会转换到 validation 目录。"""
    source = workspace.absolute(target.source)
    fmt = pdbio.detect_format(source)
    if fmt == "pdb":
        return source, ""
    if fmt == "cif":
        if not obabel.available:
            return None, "读取 mmCIF 需要 OpenBabel，当前不可用"
        converted = workspace.validation_dir(target.stem) / "source.pdb"
        converted.parent.mkdir(parents=True, exist_ok=True)
        if not converted.is_file() or converted.stat().st_mtime < source.stat().st_mtime:
            ok, message = obabel.convert(source, converted, out_format="pdb")
            if not ok:
                return None, f"mmCIF 转换失败：{message}"
        return converted, ""
    if fmt == "pdbqt":
        return None, "原始结构是 PDBQT，无法从中提取共晶配体；请使用 PDB/mmCIF 原始结构验证"
    return None, f"不支持从 {fmt.upper()} 结构提取共晶配体"


def find_reference_site(
    workspace: Workspace,
    target: ProjectFile,
    *,
    box_center: Sequence[float] | None = None,
    obabel: OpenBabelAdapter | None = None,
) -> tuple[LigandSite | None, Path | None, str]:
    """找到最适合作参考的共晶配体。

    有盒子中心时选离中心最近的配体，避免在多配体结构里选中远处的
    添加剂或第二个拷贝；没有中心时按重原子数降序。
    """
    obabel = obabel or get_openbabel()
    source, error = _source_pdb(workspace, target, obabel)
    if source is None:
        return None, None, error
    try:
        sites = pdbio.find_ligand_sites(source)
    except Exception as exc:
        return None, source, f"共晶配体检测失败：{exc}"
    if not sites:
        return None, source, "原始结构中没有可作为参考的共晶配体"
    if box_center is not None and len(box_center) == 3:
        center = tuple(float(value) for value in box_center[:3])
        sites = sorted(
            sites,
            key=lambda site: pdbio.distance(site.center, center),
        )
    site = sites[0]
    if site.heavy_atom_count < 6:
        return None, source, "检测到的共晶配体太小，不适合做重对接参考"
    return site, source, ""


def _receptor_pdbqt(workspace: Workspace, target: ProjectFile) -> tuple[Path | None, str]:
    if not target.is_ready or not target.prepared:
        return None, "靶点尚未准备"
    receptor = workspace.absolute(target.prepared)
    if not receptor.is_file():
        return None, "靶点预处理文件缺失，请重新准备"
    return receptor, ""


def _cache_matches(
    meta: dict[str, Any],
    receptor: Path,
    box: Box,
    site: LigandSite,
    output: Path,
) -> bool:
    if not meta.get("ok") or not output.is_file():
        return False
    try:
        stat = receptor.stat()
    except OSError:
        return False
    if int(meta.get("receptor_mtime", -1)) != int(stat.st_mtime):
        return False
    if int(meta.get("receptor_size", -1)) != int(stat.st_size):
        return False
    if meta.get("site_label") != site.label:
        return False
    if _point(meta.get("box_center")) != tuple(float(value) for value in box.center):
        return False
    if _point(meta.get("box_size")) != tuple(float(value) for value in box.size):
        return False
    return True


def run_redocking(
    workspace: Workspace,
    target: ProjectFile,
    box: Box,
    *,
    options: DockingOptions | None = None,
    engine: EngineAdapter | None = None,
    fallback_engines: Sequence[EngineAdapter] = (),
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
    force: bool = False,
) -> RedockingResult:
    """对指定靶点执行一次重对接验证。"""
    options = options or DockingOptions()
    obabel = obabel or get_openbabel()
    engine = engine or make_engine(options.engine)
    result = RedockingResult(target=target.stem, ok=False)

    site, source, site_error = find_reference_site(
        workspace, target, box_center=box.center, obabel=obabel
    )
    if site is None or source is None:
        result.error = site_error
        return result

    result.site_label = site.label
    result.site_resname = site.resname
    result.site_chain = site.chain
    result.site_resseq = site.resseq
    result.reference_atoms = site.heavy_atom_count
    result.box_source = box.source
    result.box_center = tuple(float(value) for value in box.center)  # type: ignore[assignment]
    result.box_size = tuple(float(value) for value in box.size)  # type: ignore[assignment]

    validation_dir = workspace.validation_dir(target.stem)
    validation_dir.mkdir(parents=True, exist_ok=True)
    output = workspace.redocking_output(target.stem)
    log_path = validation_dir / "redocking.log"
    meta_path = workspace.redocking_meta(target.stem)
    reference_pdb = validation_dir / "reference_ligand.pdb"
    prepared_pdbqt = validation_dir / "reference_ligand.pdbqt"
    prepared_sdf = validation_dir / "reference_ligand_prepared.sdf"

    receptor, receptor_error = _receptor_pdbqt(workspace, target)
    if receptor is None:
        result.error = receptor_error
        return result

    if not force and meta_path.is_file():
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if isinstance(cached, dict) and _cache_matches(
            cached, receptor, box, site, output
        ):
            restored = RedockingResult.from_dict(cached)
            restored.cached = True
            return restored

    # 1. 提取实验构象。只写重原子，避免 PDB 中的氢影响 RDKit 读入。
    try:
        pdbio.write_pdb_atoms(
            site.atoms,
            reference_pdb,
            resname=site.resname or "LIG",
            chain="L",
        )
    except Exception as exc:
        result.error = f"写出参考配体失败：{exc}"
        return result
    result.reference_path = workspace.relative(reference_pdb)

    # 2. 用与用户配体相同的流程准备 PDBQT。不做力场最小化，尽量保留
    # 晶体构象的内部几何；Vina 本身会重新采样可旋转键。
    _notify(progress, "准备参考配体…")
    try:
        prep = prepare_ligand(
            reference_pdb,
            prepared_pdbqt,
            prepared_sdf,
            options=LigandPrepOptions(minimize=False, keep_names=True),
            obabel=obabel,
        )
    except (InputError, PreparationError, DockingError) as exc:
        result.error = f"参考配体制备失败：{exc}"
        return result
    except Exception as exc:
        log.exception("参考配体制备异常")
        result.error = f"参考配体制备失败：{exc}"
        return result
    result.warnings.extend(prep.warnings)
    if not prep.ok:
        result.error = f"参考配体制备失败：{prep.message}"
        return result
    if not prepared_pdbqt.is_file():
        result.error = "参考配体 PDBQT 未生成"
        return result

    if cancel is not None and cancel.is_set():
        result.error = "已取消重对接验证"
        return result

    # 3. 用当前参数对接。与主流程一样，仅在引擎失败时换用其他可用引擎。
    engines: list[EngineAdapter] = [engine]
    if options.fallback:
        for candidate in fallback_engines:
            if candidate is None or candidate.key in {item.key for item in engines}:
                continue
            if getattr(candidate, "available", False):
                engines.append(candidate)

    _notify(progress, f"重对接 {target.display_name}（{site.label}）…")
    started = time.perf_counter()
    run = None
    for index, current in enumerate(engines):
        result.parameters = {
            "exhaustiveness": int(options.exhaustiveness),
            "num_modes": int(options.num_modes),
            "energy_range": float(options.energy_range),
            "seed": int(options.seed),
            "cpu": int(options.cpu),
            "engine": current.key,
            "engine_label": current.label,
        }
        try:
            run = current.dock(
                receptor,
                prepared_pdbqt,
                box,
                output,
                exhaustiveness=options.exhaustiveness,
                num_modes=options.num_modes,
                energy_range=options.energy_range,
                seed=options.seed,
                cpu=options.cpu,
                timeout=options.timeout_seconds,
                stall_timeout=options.stall_timeout_seconds,
                log_path=log_path,
                cancel=cancel,
            )
        except (InputError, DockingError) as exc:
            result.error = str(exc)
            return result
        except Exception as exc:
            log.exception("%s 重对接异常", current.label)
            result.error = str(exc)
            return result
        result.runtime_seconds = time.perf_counter() - started
        if run.ok:
            break
        result.error = run.error or "重对接失败"
        if cancel is not None and cancel.is_set():
            return result
        next_engine = engines[index + 1] if index + 1 < len(engines) else None
        if next_engine is None:
            return result
        message = (
            f"{current.label} 未能完成（{result.error}），"
            f"已自动改用 {next_engine.label} 重试"
        )
        result.warnings.append(message)
        _notify(progress, message)

    if run is None or not run.ok:
        result.error = result.error or "重对接失败"
        return result

    valid, message, _ = pdbio.validate_pdbqt(output)
    if not valid:
        result.error = f"重对接结果文件异常：{message}"
        return result
    result.ok = True
    result.output_path = workspace.relative(output)
    result.log_path = workspace.relative(log_path) if log_path.is_file() else ""
    poses = run.poses or pdbio.parse_pdbqt_poses(output)
    result.pose_count = len(poses)
    result.best_affinity = poses[0].affinity if poses else None

    # 4. 比较最佳 pose 与实验构象。
    pose_atoms = pdbio.parse_pose_atoms(output, 0)
    result.pose_atoms = sum(1 for atom in pose_atoms if not atom.is_hydrogen)
    comparison = rmsd.symmetry_corrected_rmsd(site.atoms, pose_atoms)
    result.rmsd = comparison.value
    result.rmsd_method = comparison.method
    result.rmsd_message = comparison.message
    result.matched_atoms = comparison.matched_atoms
    result.passed = bool(
        comparison.value is not None and comparison.value <= RMSD_THRESHOLD
    )
    if comparison.value is None:
        result.warnings.append(
            f"重对接完成，但无法计算 RMSD：{comparison.message}"
        )
    elif not result.passed:
        result.warnings.append(
            f"重对接 RMSD {comparison.value:.2f} Å，高于 {RMSD_THRESHOLD:.1f} Å 阈值。"
            "请检查盒子是否覆盖实验结合位点、配体质子化状态和搜索深度。"
        )

    _write_meta(meta_path, result, receptor)
    _notify(progress, result.summary_text)
    return result


def _write_meta(path: Path, result: RedockingResult, receptor: Path) -> None:
    payload = result.to_dict()
    try:
        stat = receptor.stat()
        payload["receptor_mtime"] = int(stat.st_mtime)
        payload["receptor_size"] = int(stat.st_size)
    except OSError:
        payload["receptor_mtime"] = -1
        payload["receptor_size"] = -1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_redocking(workspace: Workspace, target_stem: str) -> RedockingResult | None:
    """读取已保存的验证结果；不检查是否过期。"""
    path = workspace.redocking_meta(target_stem)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return RedockingResult.from_dict(data)
