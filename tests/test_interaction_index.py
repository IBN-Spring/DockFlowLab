"""批量相互作用索引：缓存、失效与筛选。"""

from __future__ import annotations

import os

import pytest

from dockflowlab.core.models import DockingResult, Pose
from dockflowlab.services import interaction_index as module
from dockflowlab.services.interactions import Interaction, InteractionAnalysis
from dockflowlab.services.interaction_index import (
    InteractionFilter,
    InteractionIndex,
    build_interaction_index,
)


def _interaction(kind: str, resname: str, resseq: int) -> Interaction:
    return Interaction(
        kind=kind,
        residue_label=f"{resname}{resseq}",
        chain="A",
        resseq=resseq,
        icode="",
        resname=resname,
        protein_atom="O",
        ligand_atom="N1",
        protein_coord=(0.0, 0.0, 0.0),
        ligand_coord=(1.0, 0.0, 0.0),
        distance=3.0,
    )


def _analysis(kinds: tuple[str, ...], engine: str = "plip") -> InteractionAnalysis:
    interactions = [
        _interaction(kind, "MET", 318 + index) for index, kind in enumerate(kinds)
    ]
    counts: dict[str, int] = {}
    for item in interactions:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return InteractionAnalysis(
        interactions=interactions,
        counts=counts,
        total_residues=len(interactions),
        ligand_selection="LIG",
        ligand_atoms=10,
        receptor_residues=200,
        engine=engine,
    )


def _result(workspace, target: str, ligand: str, *, make_complex: bool = True) -> DockingResult:
    path = workspace.complex_path(target, ligand)
    if make_complex:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n",
            encoding="utf-8",
        )
    return DockingResult(
        target=target,
        ligand=ligand,
        ok=True,
        poses=[Pose(rank=1, affinity=-7.0)],
        complex_path=str(path),
    )


@pytest.fixture()
def analyzer(monkeypatch):
    calls: list[str] = []

    def fake(path, *, max_interactions=12):
        calls.append(str(path))
        return _analysis(("hbond", "hydrophobic"), engine="plip")

    monkeypatch.setattr(module, "analyze_complex_precise", fake)
    return calls


def test_builds_cache_and_reuses_it(workspace, analyzer):
    results = [_result(workspace, "t1", "l1")]

    first = build_interaction_index(workspace, results)
    second = build_interaction_index(workspace, results)

    assert len(analyzer) == 1
    assert first.analyzed == 1 and first.reused == 0
    assert second.analyzed == 0 and second.reused == 1
    entry = second.get("t1", "l1")
    assert entry is not None and entry.ok
    assert entry.count("hbond") == 1
    assert entry.cached
    assert workspace.interaction_cache_path("t1", "l1").is_file()


def test_cache_invalidates_when_complex_changes(workspace, analyzer):
    results = [_result(workspace, "t1", "l1")]
    build_interaction_index(workspace, results)
    path = workspace.complex_path("t1", "l1")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))

    again = build_interaction_index(workspace, results)

    assert len(analyzer) == 2
    assert again.analyzed == 1


def test_only_cached_skips_missing_entries(workspace, analyzer):
    results = [_result(workspace, "t1", "l1")]

    index = build_interaction_index(workspace, results, only_cached=True)

    assert index.entries == {}
    assert analyzer == []


def test_single_failure_does_not_stop_others(workspace, monkeypatch):
    def fake(path, *, max_interactions=12):
        if "bad" in str(path):
            raise RuntimeError("PLIP 崩溃")
        return _analysis(("saltbridge",))

    monkeypatch.setattr(module, "analyze_complex_precise", fake)
    results = [_result(workspace, "t1", "bad"), _result(workspace, "t1", "good")]

    index = build_interaction_index(workspace, results)

    bad = index.get("t1", "bad")
    good = index.get("t1", "good")
    assert bad is not None and not bad.ok and "PLIP 崩溃" in bad.error
    assert good is not None and good.ok and good.count("saltbridge") == 1


def test_progress_and_cancel(workspace, analyzer):
    import threading

    results = [_result(workspace, "t1", f"l{index}") for index in range(3)]
    cancel = threading.Event()
    seen: list[tuple[int, int]] = []

    def progress(current, total, message=""):
        seen.append((current, total))
        if current >= 1:
            cancel.set()

    index = build_interaction_index(
        workspace, results, progress=progress, cancel=cancel
    )

    assert index.cancelled
    assert len(index.entries) == 1
    assert seen[0] == (0, 3)


def test_geometric_fallback_is_marked(workspace, monkeypatch):
    monkeypatch.setattr(
        module,
        "analyze_complex_precise",
        lambda path, *, max_interactions=12: _analysis(("hbond",), engine="geometric"),
    )
    index = build_interaction_index(workspace, [_result(workspace, "t1", "l1")])

    entry = index.get("t1", "l1")
    assert entry is not None and entry.engine == "geometric"
    assert any("几何判定" in warning for warning in entry.warnings)


def test_filter_by_kind_residue_and_exclusion():
    entries = {
        ("t", "a"): _entry(("hbond", "hydrophobic"), ("MET318", "LEU100")),
        ("t", "b"): _entry(("hbond",), ("ALA200",)),
        ("t", "c"): _entry(("saltbridge",), ("MET318",)),
    }
    index = InteractionIndex(entries=entries)

    assert set(index.filtered(InteractionFilter())) == set(entries)
    assert set(index.filtered(InteractionFilter(required_kinds=("hbond",)))) == {
        ("t", "a"),
        ("t", "b"),
    }
    assert set(index.filtered(InteractionFilter(required_residues=("met318",)))) == {
        ("t", "a"),
        ("t", "c"),
    }
    assert set(
        index.filtered(
            InteractionFilter(required_kinds=("hbond",), required_residues=("MET318",))
        )
    ) == {("t", "a")}
    assert set(index.filtered(InteractionFilter(exclude_kinds=("hbond",)))) == {
        ("t", "c")
    }
    assert index.filtered(InteractionFilter(required_kinds=("metal",))) == {}


def _entry(kinds: tuple[str, ...], residues: tuple[str, ...]):
    from dockflowlab.services.interaction_index import InteractionEntry

    interactions = tuple(
        {"kind": kind, "residue": residues[index % len(residues)]}
        for index, kind in enumerate(kinds)
    )
    counts: dict[str, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    return InteractionEntry(
        target="t",
        ligand=residues[0],
        ok=True,
        counts=counts,
        interactions=interactions,
        engine="plip",
    )
