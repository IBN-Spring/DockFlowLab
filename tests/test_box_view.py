"""3D 盒子编辑器：几何计算、结构加载与 PyMOL 预览脚本。"""

from __future__ import annotations

import locale

import numpy as np
import pytest

from dockflowlab.core.models import Box
from dockflowlab.services.pymol import write_box_pml
from dockflowlab.ui.box_view import (
    BOX_EDGES,
    BOX_FACES,
    box_corners,
    box_handle_positions,
    closest_axis_parameter,
    element_color,
    empty_structure,
    load_view_structure,
    nearest_handle,
    project_points,
    ray_plane_intersection,
    resize_box,
)

from .factories import make_ligand_pdbqt, make_protein_pdb


def test_box_corners_and_edges_are_consistent():
    corners = box_corners((1.0, 2.0, 3.0), (10.0, 20.0, 30.0))
    assert corners.shape == (8, 3)
    low = corners.min(axis=0)
    high = corners.max(axis=0)
    assert np.allclose(low, (-4.0, -8.0, -12.0))
    assert np.allclose(high, (6.0, 12.0, 18.0))

    # 每条边的两个端点必须位于同一面：只在一个轴向上变化。
    for start, end in BOX_EDGES:
        changed = corners[start] != corners[end]
        assert int(changed.sum()) == 1
    assert BOX_FACES.shape == (12, 3)


def test_handle_positions_include_center_and_six_faces():
    handles = box_handle_positions((0.0, 0.0, 0.0), (10.0, 20.0, 30.0))
    assert handles.shape == (7, 3)
    assert np.allclose(handles[0], (0.0, 0.0, 0.0))
    assert np.allclose(handles[1], (5.0, 0.0, 0.0))
    assert np.allclose(handles[2], (-5.0, 0.0, 0.0))
    assert np.allclose(handles[3], (0.0, 10.0, 0.0))
    assert np.allclose(handles[5], (0.0, 0.0, 15.0))


def test_resize_box_keeps_opposite_face_or_center():
    center, size = resize_box((0.0, 0.0, 0.0), (20.0, 20.0, 20.0), 0, 1, 4.0)
    assert size == (24.0, 20.0, 20.0)
    assert center == (2.0, 0.0, 0.0)  # +X 面外移，对侧不动

    center, size = resize_box(
        (0.0, 0.0, 0.0), (20.0, 20.0, 20.0), 0, 1, 4.0, symmetric=True
    )
    assert size == (28.0, 20.0, 20.0)
    assert center == (0.0, 0.0, 0.0)

    center, size = resize_box((0.0, 0.0, 0.0), (20.0, 20.0, 20.0), 1, -1, 4.0)
    assert size == (20.0, 24.0, 20.0)
    assert center == (0.0, -2.0, 0.0)


def test_resize_box_clamps_to_allowed_range():
    _, size = resize_box((0.0, 0.0, 0.0), (20.0, 20.0, 20.0), 2, 1, -100.0)
    assert size[2] == pytest.approx(5.0)
    _, size = resize_box((0.0, 0.0, 0.0), (20.0, 20.0, 20.0), 2, 1, 1000.0)
    assert size[2] == pytest.approx(150.0)


def test_ray_plane_intersection_and_axis_parameter():
    hit = ray_plane_intersection((0, 0, 10), (0, 0, -1), (0, 0, 0), (0, 0, 1))
    assert hit is not None and np.allclose(hit, (0, 0, 0))
    # 平行于平面、或交点在相机后方，都应返回 None。
    assert ray_plane_intersection((0, 0, 10), (1, 0, 0), (0, 0, 0), (0, 0, 1)) is None
    assert ray_plane_intersection((0, 0, 10), (0, 0, 1), (0, 0, 0), (0, 0, 1)) is None

    parameter = closest_axis_parameter((5, 0, 10), (0, 0, -1), (0, 0, 0), (1, 0, 0))
    assert parameter == pytest.approx(5.0)
    # 射线与轴平行时没有唯一最近点，稳定返回 0。
    assert closest_axis_parameter((5, 0, 10), (0, 0, 1), (0, 0, 0), (0, 0, 1)) == 0.0


def test_project_points_with_identity_matrix():
    points = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]])
    screen = project_points(np.eye(4), points, 200, 100)
    assert np.allclose(screen[0], (100.0, 50.0))
    assert np.allclose(screen[1], (200.0, 0.0))

    # w <= 0 的点（相机后方）标为 NaN。
    behind = np.eye(4)
    behind[3, 2] = -1.0
    screen = project_points(behind, np.array([[0.0, 0.0, 2.0]]), 200, 100)
    assert np.isnan(screen).all()


def test_nearest_handle_respects_radius():
    positions = np.array([[10.0, 10.0], [100.0, 100.0]])
    assert nearest_handle(positions, (12.0, 10.0), radius=16.0) == 0
    assert nearest_handle(positions, (50.0, 50.0), radius=16.0) is None
    assert nearest_handle(np.empty((0, 2)), (0.0, 0.0)) is None


def test_element_color_marks_hetero_carbon():
    assert element_color("O") != element_color("C")
    assert element_color("C", hetero=True) != element_color("C")


def test_load_view_structure_from_protein_with_ligand(tmp_path):
    path = make_protein_pdb(tmp_path / "complex.pdb", residues=6, ligand=True)
    structure = load_view_structure(path)

    assert not structure.is_empty
    assert structure.atom_count == 6 * 4 + 8
    assert structure.positions.shape == (structure.atom_count, 3)
    assert structure.trace.shape == (5, 2, 3)
    assert structure.hetero_positions.shape == (8, 3)
    assert structure.radius > 0
    assert np.all(np.isfinite(structure.positions))


def test_load_view_structure_reads_pdbqt(tmp_path):
    path = make_ligand_pdbqt(tmp_path / "ligand.pdbqt")
    structure = load_view_structure(path)
    assert not structure.is_empty
    assert structure.atom_count == 6  # 两个 MODEL，各 3 个重原子
    assert structure.trace.shape == (0, 2, 3)


def test_empty_structure_has_safe_defaults():
    structure = empty_structure()
    assert structure.is_empty
    assert structure.positions.shape == (0, 3)
    assert structure.radius > 0


def test_write_box_pml_contains_structure_and_box(tmp_path):
    path = make_protein_pdb(tmp_path / "protein.pdb", residues=3)
    box = Box(center=(1.0, 2.0, 3.0), size=(10.0, 20.0, 30.0))
    pml = write_box_pml(path, tmp_path / "box.pml", box, title="box check")

    text = pml.read_text(encoding=locale.getpreferredencoding(False), errors="replace")
    assert "box check" in text
    assert path.name in text
    assert text.isascii()
    assert "dockflowlab_box" in text
    assert "dockflowlab_center" in text
    assert "1.0, 0.58, 0.12" in text
    # 盒子范围为 (-4,-8,-12) 到 (6,12,18)，CGO 顶点中应出现这些极值。
    assert "-4.0" in text and "-8.0" in text and "-12.0" in text
    assert "6.0" in text and "12.0" in text and "18.0" in text


def test_write_pml_keeps_non_ascii_paths_out_of_script(tmp_path):
    from dockflowlab.services.pymol import write_pml

    root = tmp_path / "中文 项目"
    complex_pdb = root / "complexes" / "复合物.pdb"
    complex_pdb.parent.mkdir(parents=True)
    make_protein_pdb(complex_pdb, residues=3)
    pml = root / "pymol" / "复合物.pml"

    write_pml(complex_pdb, pml, title="靶点 + 候选分子")
    text = pml.read_text(encoding=locale.getpreferredencoding(False), errors="replace")

    # PyMOL 的 PML 解析器遇到非 ASCII 字符可能崩溃，脚本必须保持纯 ASCII。
    assert text.isascii()
    assert "中文" not in text and "复合物" not in text
    assert 'load "' in text


def test_shader_cache_is_reset_between_opengl_contexts():
    """重新打开编辑器会创建新上下文，旧的着色器程序 ID 必须被清空。"""
    from dockflowlab.ui import box_view

    if not box_view._HAS_GL:
        pytest.skip("缺少 pyqtgraph / PyOpenGL")
    from pyqtgraph.opengl import shaders as gl_shaders

    line_class = box_view.gl.GLLinePlotItem
    scatter_class = box_view.gl.GLScatterPlotItem
    default_program = gl_shaders.ShaderProgram.names[None]
    previous = (
        line_class._shaderProgram,
        scatter_class._shaderProgram,
        default_program.prog,
    )
    try:
        line_class._shaderProgram = 3
        scatter_class._shaderProgram = 6
        default_program.prog = 9

        box_view._reset_gl_shader_cache()

        assert line_class._shaderProgram is None
        assert scatter_class._shaderProgram is None
        assert default_program.prog is None
    finally:
        line_class._shaderProgram, scatter_class._shaderProgram, default_program.prog = previous


# ----------------------------------------------------------------------
# Qt 部件逻辑（不显示窗口，不创建 OpenGL 上下文）
# ----------------------------------------------------------------------
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


def test_viewer_tracks_manual_edits(qt_app):
    from dockflowlab.ui.box_view import BoxViewer

    viewer = BoxViewer()
    auto = Box(center=(0.0, 0.0, 0.0), size=(22.0, 22.0, 22.0), source="pocket")
    viewer.set_box(auto)
    assert viewer.has_box
    assert not viewer.is_dirty
    assert viewer.current_box() is auto

    viewer.center_inputs[0].setValue(5.0)
    assert viewer.is_dirty
    edited = viewer.current_box()
    assert edited is not None
    assert edited.source == "manual"
    assert edited.center == (5.0, 0.0, 0.0)

    viewer.use_auto(auto)
    assert not viewer.is_dirty
    assert viewer.current_box().source == "pocket"

    viewer.set_box(Box(center=(1.0, 2.0, 3.0), size=(10.0, 10.0, 10.0)), mark_manual=True)
    assert viewer.is_dirty
    viewer.deleteLater()
