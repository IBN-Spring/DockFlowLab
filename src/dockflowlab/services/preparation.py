"""把项目条目准备成可对接的 PDBQT。

这一层负责“条目 → 文件 → 状态”的编排，界面和命令行都复用它。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..config import AppSettings
from ..core import pdbio
from ..core.models import (
    KIND_LIGAND,
    KIND_TARGET,
    STATUS_FAILED,
    STATUS_PREPARING,
    STATUS_READY,
    PrepOutcome,
    ProjectFile,
)
from ..errors import DockFlowError, InputError, PreparationError
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..workspace import Workspace
from .ligand import LigandPrepOptions, prepare_ligand
from .receptor import ReceptorPrepOptions, prepare_target

log = get_logger("preparation")

Progress = Callable[[int, int, str], None] | None


@dataclass
class PrepareReport:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: bool = False
    outcomes: list[tuple[ProjectFile, PrepOutcome]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.cancelled


def _notify(progress: Progress, current: int, total: int, message: str) -> None:
    if progress is not None:
        try:
            progress(current, total, message)
        except Exception:
            pass


def _invalidate_auto_box(workspace: Workspace, entry: ProjectFile) -> None:
    """受体重新准备后，旧的自动盒子可能不再适用。

    手动盒子是用户明确指定的，保留；几何口袋/共晶配体/SITE 等自动盒子
    必须丢弃，否则会继续沿用基于旧受体或旧配体链计算的坐标。
    """
    if entry.kind != KIND_TARGET:
        return
    box = workspace.load_box(entry.stem)
    if box is None or box.source == "manual":
        return
    workspace.box_path(entry.stem).unlink(missing_ok=True)
    if entry.box:
        workspace.update_entry(entry, box="")


def _store_outcome(
    workspace: Workspace,
    entry: ProjectFile,
    outcome: PrepOutcome,
    prepared: Path,
) -> None:
    _invalidate_auto_box(workspace, entry)
    meta = {
        "prep_method": outcome.method,
        "prep_details": outcome.details,
        "prep_warnings": outcome.warnings,
    }
    kind = str(outcome.details.get("kind") or "").strip()
    if kind:
        meta["kind"] = kind
    workspace.update_entry(
        entry,
        status=STATUS_READY,
        prepared=workspace.relative(prepared),
        message=outcome.message or "已准备完成",
        meta=meta,
    )


def _store_failure(workspace: Workspace, entry: ProjectFile, message: str) -> None:
    # 准备失败后不要再沿用上一次成功时写入的结构类型，否则界面可能把
    # 一个已经损坏/被替换的文件继续当成可对接的小分子或核酸。
    entry.meta.pop("kind", None)
    workspace.update_entry(
        entry,
        status=STATUS_FAILED,
        prepared="",
        message=message,
    )


def prepare_entry(
    workspace: Workspace,
    entry: ProjectFile,
    settings: AppSettings | None = None,
    *,
    obabel: OpenBabelAdapter | None = None,
    force: bool = False,
    message_callback: Callable[[str], None] | None = None,
) -> PrepOutcome:
    """准备单个条目，并把结果写回 ``project.json``。"""
    settings = settings or AppSettings.load()
    obabel = obabel or get_openbabel()
    source = workspace.absolute(entry.source)
    if not source.is_file():
        message = "原始文件不存在，请重新添加"
        _store_failure(workspace, entry, message)
        return PrepOutcome(False, message=message)

    if not force and entry.is_ready:
        prepared = workspace.absolute(entry.prepared)
        if prepared.is_file():
            return PrepOutcome(True, method="已准备", message="已有可用文件，跳过")

    def notify(message: str) -> None:
        if message_callback is not None:
            message_callback(message)

    workspace.set_status(entry, STATUS_PREPARING, "正在准备…")
    try:
        if entry.kind == KIND_TARGET:
            pdbqt = workspace.prepared_target_pdbqt(entry.stem)
            pdb = workspace.prepared_target_pdb(entry.stem)
            pdbqt.parent.mkdir(parents=True, exist_ok=True)
            outcome = prepare_target(
                source,
                pdbqt,
                pdb,
                options=ReceptorPrepOptions(ph=settings.add_hydrogens_ph),
                ligand_options=LigandPrepOptions(ph=settings.add_hydrogens_ph),
                obabel=obabel,
                progress=notify,
            )
            if not pdbqt.is_file():
                raise PreparationError("准备完成但没有生成靶点 PDBQT 文件")
            _store_outcome(workspace, entry, outcome, pdbqt)
            entry.meta["prepared_pdb"] = workspace.relative(pdb) if pdb.is_file() else ""
            workspace.save()
            return outcome

        if entry.kind == KIND_LIGAND:
            if pdbio.detect_format(source) in {"pdb", "pdbqt"}:
                kind, detail = pdbio.structure_kind(source)
                if kind in {"protein", "peptide"}:
                    raise PreparationError(
                        f"这个文件包含蛋白质或多肽结构（{detail}），不能放在“候选分子”里。"
                        "本地版本暂不支持蛋白–蛋白、蛋白–多肽对接；"
                        "请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。"
                    )
                if kind == "nucleic_acid":
                    raise PreparationError(
                        "这个文件是核酸结构，不能作为候选分子。"
                        "核酸–小分子对接请把核酸放在“靶点结构”一栏。"
                    )
            multi_record = 0
            if source.suffix.lower() in {".sdf", ".sd"}:
                text = source.read_text(encoding="utf-8", errors="ignore")
                multi_record = len(re.findall(r"^\$\$\$\$$", text, flags=re.MULTILINE))
                if multi_record > 1:
                    notify(f"该 SDF 含 {multi_record} 个分子；当前只准备第一个")
            pdbqt = workspace.prepared_ligand_pdbqt(entry.stem)
            sdf = workspace.prepared_ligand_sdf(entry.stem)
            pdbqt.parent.mkdir(parents=True, exist_ok=True)
            outcome = prepare_ligand(
                source,
                pdbqt,
                sdf,
                options=LigandPrepOptions(ph=settings.add_hydrogens_ph),
                obabel=obabel,
                progress=notify,
            )
            if multi_record > 1:
                outcome.warnings.append(
                    f"该 SDF 含 {multi_record} 个分子，当前只准备了第一个；"
                    "如需全部导入，请在外部拆分为单个文件后分别添加。"
                )
            _store_outcome(workspace, entry, outcome, pdbqt)
            return outcome

        raise InputError(f"未知的条目类型：{entry.kind}")
    except (DockFlowError, OSError, ValueError) as exc:
        log.warning("准备失败 %s：%s", entry.display_name, exc)
        _store_failure(workspace, entry, str(exc))
        return PrepOutcome(False, message=str(exc))
    except Exception as exc:  # 防御第三方库的异常类型
        log.exception("准备失败 %s", entry.display_name)
        _store_failure(workspace, entry, f"未预期的错误：{exc}")
        return PrepOutcome(False, message=f"未预期的错误：{exc}")


def prepare_all(
    workspace: Workspace,
    settings: AppSettings | None = None,
    *,
    kinds: Iterable[str] = (KIND_TARGET, KIND_LIGAND),
    force: bool = False,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
    cancel: threading.Event | None = None,
) -> PrepareReport:
    """按顺序准备所有条目；单条失败不会中断其余条目。"""
    settings = settings or AppSettings.load()
    obabel = obabel or get_openbabel()
    entries: list[ProjectFile] = []
    for kind in kinds:
        entries.extend(workspace.entries(kind))

    report = PrepareReport(total=len(entries))
    if not entries:
        return report

    for index, entry in enumerate(entries, start=1):
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
        # 进度条只在条目真正完成后前进，否则单项任务一开始就显示 100%。
        _notify(progress, index - 1, report.total, f"[{index}/{report.total}] {entry.display_name}")
        if not force and entry.is_ready and workspace.absolute(entry.prepared).is_file():
            report.skipped += 1
            _notify(progress, index, report.total, "")
            continue
        outcome = prepare_entry(
            workspace,
            entry,
            settings,
            obabel=obabel,
            force=force,
            message_callback=lambda message, i=index - 1: _notify(progress, i, report.total, message),
        )
        report.outcomes.append((entry, outcome))
        if outcome.ok:
            report.succeeded += 1
        else:
            report.failed += 1
        _notify(progress, index, report.total, "")
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
    return report
