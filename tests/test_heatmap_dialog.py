"""热图编辑对话框：名称收集、校验与预览。"""

from __future__ import annotations

import pytest

from dockflowlab.core.models import DockingResult, Pose


@pytest.fixture(scope="module")
def qt_app():
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    try:
        app = qt_widgets.QApplication.instance()
        if app is None:
            app = qt_widgets.QApplication([])
    except Exception as exc:  # pragma: no cover - 无图形环境的 CI
        pytest.skip(f"无法创建 QApplication：{exc}")
    return app


def _results(target, ligands):
    return [
        DockingResult(
            target=target.stem,
            ligand=ligand.stem,
            ok=True,
            poses=[Pose(rank=1, affinity=-7.0 - index, rmsd_lb=0.0, rmsd_ub=0.0)],
        )
        for index, ligand in enumerate(ligands)
    ]


def _dialog(workspace, pdb_path, sdf_path, tmp_path):
    from dockflowlab.ui.heatmap_dialog import HeatmapDialog

    target = workspace.import_file("target", pdb_path, display_name="1BCI")
    first = workspace.import_file("ligand", sdf_path, display_name="阿司匹林")
    second_source = tmp_path / "ligand_2.sdf"
    second_source.write_text(
        sdf_path.read_text(encoding="utf-8").replace("2244", "2245", 1),
        encoding="utf-8",
    )
    second = workspace.import_file("ligand", second_source, display_name="对照分子")
    results = _results(target, [first, second])
    return HeatmapDialog(workspace, results), target, first, second


def test_dialog_collects_custom_labels_and_format(
    qt_app, workspace, pdb_path, sdf_path, tmp_path
):
    dialog, target, first, _ = _dialog(workspace, pdb_path, sdf_path, tmp_path)
    try:
        dialog.table.item(0, 2).setText("Akt 激酶")
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 1).text() == first.stem:
                dialog.table.item(row, 2).setText("槲皮素")
                break
        dialog.title_edit.setText("Akt binding")
        dialog.subtitle_edit.setText("自定义说明")
        dialog.scheme_combo.setCurrentIndex(dialog.scheme_combo.findData("green"))
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("png+svg"))

        config, matrix = dialog._collect_config()

        assert config.target_labels == {target.stem: "Akt 激酶"}
        assert config.ligand_labels == {first.stem: "槲皮素"}
        assert config.scheme == "green"
        assert config.format == "png+svg"
        assert config.title == "Akt binding"
        assert config.subtitle == "自定义说明"
        assert list(matrix.index) == ["Akt 激酶"]
        assert "槲皮素" in matrix.columns
    finally:
        dialog.close()


def test_dialog_rejects_duplicate_labels(
    qt_app, workspace, pdb_path, sdf_path, tmp_path
):
    dialog, _, _, _ = _dialog(workspace, pdb_path, sdf_path, tmp_path)
    try:
        dialog.table.item(1, 2).setText("同一名称")
        dialog.table.item(2, 2).setText("同一名称")
        with pytest.raises(ValueError, match="重复"):
            dialog._collect_config()
    finally:
        dialog.close()
