"""真实工具链集成测试：1BCI + 阿司匹林。

这些测试会实际调用 PDBFixer / OpenBabel / RDKit / Meeko / Vina，标记为 slow。
缺少 Vina 时自动跳过，便于在普通开发机上只跑单元测试。
"""

from __future__ import annotations

import json

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.core import pdbio
from dockflowlab.services import boxes as box_service
from dockflowlab.services.analysis import analyze
from dockflowlab.services.docking import DockingOptions, collect_existing_results, dock_pair
from dockflowlab.services.preparation import prepare_all
from dockflowlab.services.report import generate_report
from dockflowlab.tools.obabel import get_openbabel
from dockflowlab.tools.vina import VinaAdapter


@pytest.mark.slow
def test_prepare_dock_analyze_and_report(prepared_workspace, tmp_path):
    vina = VinaAdapter()
    if not vina.available:
        pytest.skip("未安装 AutoDock Vina，跳过真实对接集成测试")
    obabel = get_openbabel()
    if not obabel.available:
        pytest.skip("OpenBabel 不可用，跳过结构准备集成测试")

    summary = prepare_all(prepared_workspace, AppSettings(), obabel=obabel)
    assert summary.total == 2
    assert summary.failed == 0, [
        (entry.display_name, outcome.message) for entry, outcome in summary.outcomes if not outcome.ok
    ]
    assert summary.succeeded == 2

    target = prepared_workspace.targets[0]
    ligand = prepared_workspace.ligands[0]
    receptor_pdbqt = prepared_workspace.absolute(target.prepared)
    ligand_pdbqt = prepared_workspace.absolute(ligand.prepared)
    valid, message, _ = pdbio.validate_pdbqt(receptor_pdbqt, receptor=True)
    assert valid, message
    valid, message, _ = pdbio.validate_pdbqt(ligand_pdbqt)
    assert valid, message

    settings = AppSettings(exhaustiveness=1, num_modes=1, seed=7, cpu=1)
    box, warnings = box_service.suggest_box(
        target_source=prepared_workspace.absolute(target.source),
        target_prepared_pdb=prepared_workspace.prepared_target_pdb(target.stem),
        ligand_paths=[ligand_pdbqt],
        settings=settings,
        target_kind=str(target.meta.get("kind", "macromolecule")),
        chains=target.meta.get("chains", []),
        obabel=obabel,
    )
    assert box.source in {"cocrystal", "site", "blind"}
    assert all(value >= 18 for value in box.size)

    result = dock_pair(
        prepared_workspace,
        target,
        ligand,
        box,
        options=DockingOptions.from_settings(settings),
        vina=vina,
        obabel=obabel,
    )
    assert result.ok, result.error
    assert result.poses
    assert result.best_affinity is not None
    assert -20 < result.best_affinity < 5
    assert prepared_workspace.absolute(result.output_path).is_file()
    assert prepared_workspace.absolute(result.complex_path).is_file()
    assert prepared_workspace.absolute(result.pymol_path).is_file()

    meta_path = prepared_workspace.docking_meta(target.stem, ligand.stem)
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["ok"] is True
    assert meta["runtime_seconds"] > 0
    assert meta["box_source"] == box.source

    restored = collect_existing_results(prepared_workspace)
    assert len(restored) == 1
    assert restored[0].runtime_seconds > 0
    assert restored[0].box_source == box.source

    bundle = analyze(prepared_workspace, restored)
    assert bundle.csv_path.is_file()
    assert bundle.matrix.shape == (1, 1)
    report = generate_report(prepared_workspace, restored, settings=settings)
    assert report.is_file()
    assert str(result.best_affinity)[:4] in report.read_text(encoding="utf-8") or "成功" in report.read_text(encoding="utf-8")
