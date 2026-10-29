"""批量相互作用索引：让结果表能够按相互作用类型与残基筛选。

单条结果的细节视图已经会按需运行 PLIP；这里把同样的分析事先跑一遍，
缓存到 ``analysis/interactions/<靶点>/<配体>.json``，结果表只读缓存。
缓存记录复合物的 mtime 与大小，重新对接/重新生成复合物后自动失效。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from ..core.models import DockingResult
from ..logging_setup import get_logger
from ..workspace import Workspace
from .interactions import KIND_LABELS, KIND_ORDER
from .plip_analysis import analyze_complex_precise

log = get_logger("interaction_index")

CACHE_VERSION = 1
DEFAULT_MAX_INTERACTIONS = 64

Progress = Callable[[int, int, str], None]


@dataclass
class InteractionEntry:
    """一条对接结果的相互作用摘要（可来自缓存）。"""

    target: str
    ligand: str
    ok: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    interactions: tuple[dict[str, object], ...] = ()
    engine: str = ""
    warnings: tuple[str, ...] = ()
    error: str = ""
    cached: bool = False
    complex_path: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.target, self.ligand)

    def count(self, kind: str) -> int:
        return int(self.counts.get(kind, 0))

    @property
    def residue_labels(self) -> tuple[str, ...]:
        seen = {
            str(item.get("residue") or "")
            for item in self.interactions
            if item.get("residue")
        }
        return tuple(sorted(seen))

    @property
    def summary(self) -> str:
        if not self.ok:
            return "—"
        parts = [
            f"{KIND_LABELS.get(kind, kind)} {self.counts[kind]}"
            for kind in KIND_ORDER
            if self.counts.get(kind)
        ]
        return " · ".join(parts) if parts else "无可展示接触"

    @property
    def residue_text(self) -> str:
        return "、".join(self.residue_labels[:8])

    @property
    def detail_tip(self) -> str:
        lines = []
        if self.engine:
            engine = "PLIP" if self.engine == "plip" else "几何判定"
            lines.append(f"分析方式：{engine}")
        if self.residue_text:
            lines.append(f"关键残基：{self.residue_text}")
        for warning in self.warnings:
            lines.append(f"提示：{warning}")
        if self.error:
            lines.append(f"错误：{self.error}")
        return "\n".join(lines)

    def has_kind(self, kind: str) -> bool:
        return any(str(item.get("kind")) == kind for item in self.interactions)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "ligand": self.ligand,
            "ok": self.ok,
            "counts": dict(self.counts),
            "interactions": list(self.interactions),
            "engine": self.engine,
            "warnings": list(self.warnings),
            "error": self.error,
            "complex_path": self.complex_path,
        }


@dataclass
class InteractionFilter:
    """结果表的筛选条件；空条件表示不筛选。"""

    required_kinds: tuple[str, ...] = ()
    required_residues: tuple[str, ...] = ()
    exclude_kinds: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.required_kinds or self.required_residues or self.exclude_kinds)

    def matches(self, entry: InteractionEntry | None) -> bool:
        if not self.active:
            return True
        if entry is None or not entry.ok:
            return False
        for kind in self.required_kinds:
            if not entry.has_kind(kind):
                return False
        for kind in self.exclude_kinds:
            if entry.has_kind(kind):
                return False
        for token in self.required_residues:
            needle = token.casefold()
            if not any(needle in label.casefold() for label in entry.residue_labels):
                return False
        return True


@dataclass
class InteractionIndex:
    entries: dict[tuple[str, str], InteractionEntry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    analyzed: int = 0
    reused: int = 0

    def get(self, target: str, ligand: str) -> InteractionEntry | None:
        return self.entries.get((target, ligand))

    def filtered(self, filt: InteractionFilter) -> dict[tuple[str, str], InteractionEntry]:
        return {
            key: entry for key, entry in self.entries.items() if filt.matches(entry)
        }


def _notify(progress: Progress | None, current: int, total: int, message: str) -> None:
    if progress is None:
        return
    try:
        progress(current, total, message)
    except TypeError:
        progress(current, total)  # type: ignore[misc]


def _complex_path(workspace: Workspace, result: DockingResult) -> Path:
    if result.complex_path:
        path = Path(result.complex_path)
        if not path.is_absolute():
            path = workspace.absolute(result.complex_path)
        return path
    return workspace.complex_path(result.target, result.ligand)


def _cache_matches(
    path: Path, complex_path: Path, meta: dict[str, object]
) -> bool:
    if not path.is_file():
        return False
    try:
        stat = complex_path.stat()
    except OSError:
        return False
    payload = meta.get("complex") or {}
    if not isinstance(payload, dict):
        return False
    return (
        int(meta.get("version", 0)) == CACHE_VERSION
        and float(payload.get("mtime", -1.0)) == float(stat.st_mtime)
        and int(payload.get("size", -1)) == int(stat.st_size)
    )


def _read_cache(workspace: Workspace, target: str, ligand: str) -> tuple[InteractionEntry | None, dict[str, object]]:
    path = workspace.interaction_cache_path(target, ligand)
    if not path.is_file():
        return None, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    try:
        entry = InteractionEntry(
            target=str(data.get("target") or target),
            ligand=str(data.get("ligand") or ligand),
            ok=bool(data.get("ok")),
            counts={str(k): int(v) for k, v in (data.get("counts") or {}).items()},
            interactions=tuple(
                item for item in data.get("interactions") or [] if isinstance(item, dict)
            ),
            engine=str(data.get("engine") or ""),
            warnings=tuple(str(item) for item in data.get("warnings") or []),
            error=str(data.get("error") or ""),
            cached=True,
            complex_path=str(data.get("complex_path") or ""),
        )
    except (TypeError, ValueError):
        return None, {}
    return entry, data


def _write_cache(workspace: Workspace, entry: InteractionEntry, complex_path: Path) -> None:
    path = workspace.interaction_cache_path(entry.target, entry.ligand)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stat = complex_path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        mtime, size = -1.0, -1
    payload = {
        "version": CACHE_VERSION,
        "target": entry.target,
        "ligand": entry.ligand,
        "complex": {"mtime": mtime, "size": size},
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "ok": entry.ok,
        "counts": dict(entry.counts),
        "interactions": list(entry.interactions),
        "engine": entry.engine,
        "warnings": list(entry.warnings),
        "error": entry.error,
        "complex_path": entry.complex_path,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _entry_from_analysis(
    result: DockingResult, complex_path: Path, analysis
) -> InteractionEntry:
    interactions = tuple(item.to_dict() for item in analysis.interactions)
    counts = {kind: 0 for kind in KIND_LABELS}
    for item in analysis.interactions:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    warnings = list(analysis.warnings)
    if analysis.engine != "plip":
        warnings.append("PLIP 不可用或未识别配体，本结果使用几何判定")
    return InteractionEntry(
        target=result.target,
        ligand=result.ligand,
        ok=bool(interactions),
        counts=counts,
        interactions=interactions,
        engine=analysis.engine,
        warnings=tuple(dict.fromkeys(warnings)),
        complex_path=str(complex_path),
    )


def build_interaction_index(
    workspace: Workspace,
    results: Sequence[DockingResult],
    *,
    progress: Progress | None = None,
    cancel=None,
    force: bool = False,
    only_cached: bool = False,
    max_interactions: int = DEFAULT_MAX_INTERACTIONS,
) -> InteractionIndex:
    """为成功且有复合物的结果建立相互作用索引。

    ``only_cached=True`` 只读取已有缓存，适合打开结果页时快速回填列。
    """
    index = InteractionIndex()
    eligible = [
        result
        for result in results
        if result.ok and result.poses and _complex_path(workspace, result).is_file()
    ]
    total = len(eligible)
    for position, result in enumerate(eligible, start=1):
        if cancel is not None and cancel.is_set():
            index.cancelled = True
            break
        pair = (result.target, result.ligand)
        complex_path = _complex_path(workspace, result)
        _notify(
            progress,
            position - 1,
            total,
            f"[{position}/{total}] {result.target} + {result.ligand}",
        )
        # 进度回调可能由“取消”按钮触发，立即生效，别再分析当前这一条。
        if cancel is not None and cancel.is_set():
            index.cancelled = True
            break
        cached_entry, meta = _read_cache(workspace, *pair)
        if (
            not force
            and cached_entry is not None
            and _cache_matches(
                workspace.interaction_cache_path(*pair), complex_path, meta
            )
        ):
            index.entries[pair] = cached_entry
            index.reused += 1
            continue
        if only_cached:
            continue
        try:
            analysis = analyze_complex_precise(
                complex_path, max_interactions=max_interactions
            )
            entry = _entry_from_analysis(result, complex_path, analysis)
        except Exception as exc:  # 单条失败不影响其余结果
            log.warning("相互作用分析失败 %s + %s：%s", *pair, exc)
            entry = InteractionEntry(
                target=result.target,
                ligand=result.ligand,
                ok=False,
                error=f"相互作用分析失败：{exc}",
                complex_path=str(complex_path),
            )
        index.entries[pair] = entry
        index.analyzed += 1
        try:
            _write_cache(workspace, entry, complex_path)
        except OSError as exc:
            log.warning("写入相互作用缓存失败 %s：%s", pair, exc)
    _notify(progress, total, total, "相互作用分析结束")
    if total == 0:
        index.warnings.append("没有可分析的复合物")
    return index
