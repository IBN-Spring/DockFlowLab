"""结果汇总、图表与 HTML 报告测试。"""

from __future__ import annotations

import json

import pytest

from dockflowlab.core.models import DockingResult, Pose
from dockflowlab.services.analysis import (
    HeatmapConfig,
    analyze,
    load_heatmap_config,
    save_heatmap_config,
)
from dockflowlab.services.report import generate_report


@pytest.fixture()
def result_workspace(workspace, pdb_path, sdf_path, tmp_path):
    target = workspace.import_file("target", pdb_path, display_name="1BCI")
    first = workspace.import_file("ligand", sdf_path, display_name="阿司匹林")
    second_source = tmp_path / "aspirin_2.sdf"
    second_source.write_text(
        sdf_path.read_text(encoding="utf-8").replace("2244", "2245", 1), encoding="utf-8"
    )
    second = workspace.import_file("ligand", second_source, display_name="对照分子")
    for entry in (target, first, second):
        workspace.update_entry(
            entry,
            status="ready",
            prepared=f"prepared/{entry.kind}s/{entry.stem}/{entry.stem}.pdbqt",
        )
    return workspace, target, first, second


def make_results(target, ligands):
    return [
        DockingResult(
            target=target.stem,
            ligand=ligand.stem,
            ok=True,
            poses=[
                Pose(rank=1, affinity=-7.5 - index, rmsd_lb=0.0, rmsd_ub=0.0),
                Pose(rank=2, affinity=-6.2 - index, rmsd_lb=1.4, rmsd_ub=2.8),
            ],
            runtime_seconds=12.5,
            box_source="cocrystal",
            warnings=[],
        )
        for index, ligand in enumerate(ligands)
    ]


def test_analyze_bundle(result_workspace):
    workspace, target, first, second = result_workspace
    results = make_results(target, [first, second])
    bundle = analyze(workspace, results)

    assert len(bundle.dataframe) == 4
    assert bundle.matrix.shape == (1, 2)
    assert set(bundle.matrix.to_numpy().ravel()) == {-7.5, -8.5}
    assert bundle.summary["counts"] == {"total": 2, "success": 2, "failed": 0}
    assert bundle.summary["best"]["affinity"] == -8.5
    assert bundle.csv_path.is_file()
    assert bundle.matrix_path.is_file()
    assert bundle.summary_path.is_file()
    assert bundle.heatmap_path is not None and bundle.heatmap_path.is_file()
    assert bundle.bar_path is not None and bundle.bar_path.is_file()

    payload = json.loads(bundle.summary_path.read_text(encoding="utf-8"))
    assert payload["project"] == workspace.name


def test_analysis_includes_failure_and_empty(tmp_path):
    from dockflowlab.workspace import Workspace

    workspace = Workspace.create(tmp_path / "empty")
    bundle = analyze(workspace, [])
    assert bundle.dataframe.empty
    assert bundle.matrix.empty
    assert bundle.summary["counts"]["total"] == 0
    assert bundle.heatmap_path is None
    assert bundle.bar_path is None


def test_html_report_is_self_contained(result_workspace):
    workspace, target, first, second = result_workspace
    results = make_results(target, [first, second])
    path = generate_report(workspace, results)

    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "阿司匹林" in text
    assert "data:image/png;base64," in text
    assert "Vina 打分是经验评分函数的输出" in text
    assert "不等同于实验测得的" in text
    assert "<img" in text
    assert "http://" not in text.split("<style")[0]
    assert "-7.50" in text


def test_heatmap_custom_text_color_and_svg(result_workspace):
    workspace, target, first, second = result_workspace
    results = make_results(target, [first, second])
    save_heatmap_config(
        workspace,
        HeatmapConfig(
            target_labels={target.stem: "Akt 激酶"},
            ligand_labels={first.stem: "槲皮素"},
            scheme="green",
            title="Akt binding",
            subtitle="自定义副标题",
            format="png+svg",
        ),
    )

    bundle = analyze(workspace, results)

    assert list(bundle.matrix.index) == ["Akt 激酶"]
    assert "槲皮素" in bundle.matrix.columns
    svg = workspace.analysis_dir / "heatmap.svg"
    assert svg.is_file() and svg.stat().st_size > 0
    # SVG 保留文字元素，方便后续编辑。
    assert "Akt binding" in svg.read_text(encoding="utf-8")
    assert load_heatmap_config(workspace).scheme == "green"


def test_report_uses_custom_heatmap_labels(result_workspace):
    workspace, target, first, second = result_workspace
    results = make_results(target, [first, second])
    save_heatmap_config(
        workspace, HeatmapConfig(target_labels={target.stem: "Akt 激酶"})
    )

    path = generate_report(workspace, results)

    text = path.read_text(encoding="utf-8")
    assert "Akt 激酶" in text
    assert "颜色越深表示" in text


def test_heatmap_switching_back_to_png_removes_vector_files(result_workspace):
    workspace, target, first, second = result_workspace
    results = make_results(target, [first, second])
    save_heatmap_config(workspace, HeatmapConfig(format="png+pdf"))
    analyze(workspace, results)
    pdf = workspace.analysis_dir / "heatmap.pdf"
    assert pdf.is_file()

    save_heatmap_config(workspace, HeatmapConfig(format="png"))
    analyze(workspace, results)
    assert not pdf.exists()


def test_invalid_heatmap_config_falls_back_to_defaults(result_workspace):
    workspace, *_ = result_workspace
    workspace.analysis_dir.mkdir(parents=True, exist_ok=True)
    (workspace.analysis_dir / "heatmap_config.json").write_text(
        "{not json", encoding="utf-8"
    )
    config = load_heatmap_config(workspace)
    assert config.scheme == "blue"
    assert config.format == "png"
    assert config.title == "结合能热图"


def test_report_without_results_raises_nothing(result_workspace):
    workspace, *_ = result_workspace
    path = generate_report(workspace, [])
    assert path.is_file()
    assert "还没有对接结果" in path.read_text(encoding="utf-8")
