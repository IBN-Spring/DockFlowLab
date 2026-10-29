"""重对接验证：参考配体提取、RMSD、缓存与失败路径。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from dockflowlab.core import pdbio
from dockflowlab.core.models import Box, Pose
from dockflowlab.services.docking import DockingOptions
from dockflowlab.services.redocking import (
    find_reference_site,
    load_redocking,
    run_redocking,
)
from dockflowlab.tools.vina import VinaRun

from .factories import make_protein_pdb


class _RecordingEngine:
    key = "vina"
    label = "AutoDock Vina"
    available = True

    def __init__(self, atoms, *, shift=(0.0, 0.0, 0.0), succeed=True) -> None:
        self.atoms = [atom for atom in atoms if not atom.is_hydrogen]
        self.shift = shift
        self.succeed = succeed
        self.calls = 0

    def dock(self, receptor_pdbqt, ligand_pdbqt, box, output_pdbqt, **kwargs) -> VinaRun:
        self.calls += 1
        if not self.succeed:
            return VinaRun(False, error="模拟引擎失败")
        lines = [
            "MODEL 1",
            "REMARK VINA RESULT:    -7.200      0.000      0.000",
            "ROOT",
        ]
        dx, dy, dz = self.shift
        for index, atom in enumerate(self.atoms, start=1):
            moved = replace(
                atom,
                x=atom.x + dx,
                y=atom.y + dy,
                z=atom.z + dz,
            )
            lines.append(pdbio.make_pdb_atom_line(moved, index, "LIG", "L"))
        lines.extend(["ENDROOT", "TORSDOF 0", "ENDMDL", "END"])
        output = Path(output_pdbqt)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return VinaRun(
            True,
            poses=[Pose(rank=1, affinity=-7.2)],
            output_path=str(output),
        )


def _workspace_with_complex(tmp_path, workspace):
    source = make_protein_pdb(tmp_path / "complex.pdb", residues=12, ligand=True)
    workspace.import_file("target", source, display_name="测试靶点")
    target = workspace.targets[0]
    receptor = workspace.prepared_target_pdbqt(target.stem)
    receptor.parent.mkdir(parents=True, exist_ok=True)
    # 内容不必真实：测试引擎不读取受体，只验证路径与流程。
    receptor.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000"
        "  1.00  0.00     0.000 C\nEND\n",
        encoding="utf-8",
    )
    workspace.update_entry(target, status="ready", prepared=workspace.relative(receptor))
    return target, source


def _options():
    return DockingOptions(
        exhaustiveness=1,
        num_modes=1,
        seed=7,
        cpu=1,
        fallback=False,
    )


def test_find_reference_site_prefers_ligand_near_box(tmp_path, workspace):
    pytest.importorskip("rdkit")
    target, source = _workspace_with_complex(tmp_path, workspace)

    site, path, error = find_reference_site(
        workspace, target, box_center=(12.0, 22.0, 31.0)
    )

    assert error == ""
    assert path == workspace.absolute(target.source)
    assert site is not None
    assert site.resname == "LIG"
    assert site.heavy_atom_count == 8


def test_redocking_computes_low_rmsd_and_caches(tmp_path, workspace):
    pytest.importorskip("rdkit")
    target, _ = _workspace_with_complex(tmp_path, workspace)
    site, _, _ = find_reference_site(workspace, target)
    assert site is not None
    engine = _RecordingEngine(site.atoms)
    box = Box(center=site.center, size=(20.0, 20.0, 20.0), source="cocrystal")

    first = run_redocking(
        workspace, target, box, options=_options(), engine=engine
    )

    assert first.ok, first.error
    assert first.rmsd == pytest.approx(0.0, abs=1e-4)
    assert first.passed
    assert first.pose_count == 1
    assert first.best_affinity == pytest.approx(-7.2)
    assert first.output_path
    assert workspace.redocking_output(target.stem).is_file()
    assert workspace.redocking_meta(target.stem).is_file()

    second = run_redocking(
        workspace, target, box, options=_options(), engine=engine
    )

    assert second.cached
    assert engine.calls == 1
    assert load_redocking(workspace, target.stem).rmsd == pytest.approx(0.0)


def test_redocking_passes_and_records_smina_scoring(tmp_path, workspace):
    pytest.importorskip("rdkit")
    target, _ = _workspace_with_complex(tmp_path, workspace)
    site, _, _ = find_reference_site(workspace, target)
    engine = _RecordingEngine(site.atoms)
    engine.key = "smina"
    engine.label = "Smina"
    seen: dict[str, object] = {}
    original = engine.dock

    def dock(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    engine.dock = dock
    options = replace(_options(), scoring="vinardo")

    result = run_redocking(
        workspace,
        target,
        Box(center=site.center, size=(20.0, 20.0, 20.0), source="cocrystal"),
        options=options,
        engine=engine,
    )

    assert result.ok, result.error
    assert seen.get("scoring") == "vinardo"
    assert result.parameters["scoring"] == "vinardo"


def test_redocking_flags_pose_above_threshold(tmp_path, workspace):
    pytest.importorskip("rdkit")
    target, _ = _workspace_with_complex(tmp_path, workspace)
    site, _, _ = find_reference_site(workspace, target)
    engine = _RecordingEngine(site.atoms, shift=(4.0, 0.0, 0.0))
    box = Box(center=site.center, size=(20.0, 20.0, 20.0), source="cocrystal")

    result = run_redocking(
        workspace, target, box, options=_options(), engine=engine
    )

    assert result.ok, result.error
    assert result.rmsd is not None
    assert result.rmsd > 2.0
    assert not result.passed
    assert any("高于" in warning for warning in result.warnings)
    assert "未通过" in result.summary_text


def test_redocking_without_cocrystal_ligand_returns_error(tmp_path, workspace):
    pytest.importorskip("rdkit")
    source = make_protein_pdb(tmp_path / "protein_only.pdb", residues=12)
    workspace.import_file("target", source, display_name="无配体靶点")
    target = workspace.targets[0]
    receptor = workspace.prepared_target_pdbqt(target.stem)
    receptor.parent.mkdir(parents=True, exist_ok=True)
    receptor.write_text("ATOM\n", encoding="utf-8")
    workspace.update_entry(target, status="ready", prepared=workspace.relative(receptor))

    result = run_redocking(
        workspace,
        target,
        Box(center=(0.0, 0.0, 0.0), size=(20.0, 20.0, 20.0)),
        options=_options(),
        engine=_RecordingEngine([]),
    )

    assert not result.ok
    assert "没有可作为参考的共晶配体" in result.error


def test_redocking_engine_failure_is_reported(tmp_path, workspace):
    pytest.importorskip("rdkit")
    target, _ = _workspace_with_complex(tmp_path, workspace)
    site, _, _ = find_reference_site(workspace, target)
    engine = _RecordingEngine(site.atoms, succeed=False)

    result = run_redocking(
        workspace,
        target,
        Box(center=site.center, size=(20.0, 20.0, 20.0)),
        options=_options(),
        engine=engine,
    )

    assert not result.ok
    assert "模拟引擎失败" in result.error
    assert engine.calls == 1
