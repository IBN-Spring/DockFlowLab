"""3D 盒子编辑器。

在蛋白/核酸结构上显示 AutoDock Vina 的搜索盒子，并允许：

* 左键拖动旋转、滚轮缩放、右键拖动平移；
* 拖动白色中心点移动盒子；
* 拖动六个彩色面点调整对应方向的边长；
* 点击结构直接设置盒子中心；
* 在右侧精确输入中心与边长。

实现依赖 pyqtgraph.opengl（PyOpenGL）。缺少 OpenGL 时自动退化为
纯数值输入，不影响盒子保存与对接。所有几何计算都放在本模块的纯函数里，
便于单元测试。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# pyqtgraph 会按 PyQt5 → PySide2 → PyQt6 → PySide6 的顺序探测 Qt 绑定。
# 本项目统一使用 PySide6，必须在导入 pyqtgraph 之前固定，避免混用两套 Qt。
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import pdbio
from ..core.models import Box

try:  # pragma: no cover - 取决于运行环境是否安装 OpenGL 依赖
    import pyqtgraph.opengl as gl
    from OpenGL import GL as _GL
    from pyqtgraph import Vector

    _HAS_GL = True
    _GL_ERROR = ""
except Exception as exc:  # pragma: no cover - 仅用于降级提示
    gl = None  # type: ignore[assignment]
    _GL = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment]
    _HAS_GL = False
    _GL_ERROR = str(exc)


MIN_BOX_SIZE = 5.0
MAX_BOX_SIZE = 150.0
HANDLE_PICK_PX = 16.0
ATOM_PICK_PX = 14.0

_BACKGROUND = (0.045, 0.060, 0.085, 1.0)
_TRACE_COLOR = (0.52, 0.66, 0.86, 0.95)
_BOX_COLOR = (1.00, 0.66, 0.18, 1.0)
_BOX_FACE_COLOR = (1.00, 0.62, 0.15, 0.10)
_CENTER_COLOR = (1.00, 1.00, 1.00, 1.0)

_ELEMENT_COLORS = {
    "C": (0.66, 0.70, 0.78, 0.95),
    "N": (0.38, 0.58, 0.96, 1.00),
    "O": (0.94, 0.36, 0.33, 1.00),
    "S": (0.96, 0.80, 0.26, 1.00),
    "P": (0.98, 0.56, 0.20, 1.00),
    "F": (0.38, 0.86, 0.58, 1.00),
    "CL": (0.36, 0.84, 0.42, 1.00),
    "BR": (0.72, 0.36, 0.18, 1.00),
    "I": (0.56, 0.32, 0.82, 1.00),
    "H": (0.85, 0.85, 0.85, 0.55),
    "FE": (0.86, 0.56, 0.22, 1.00),
    "ZN": (0.62, 0.62, 0.70, 1.00),
    "MG": (0.42, 0.82, 0.42, 1.00),
    "CA": (0.42, 0.82, 0.42, 1.00),
    "NA": (0.62, 0.52, 0.92, 1.00),
    "K": (0.72, 0.46, 0.90, 1.00),
}

_AMINO = set(pdbio.AMINO_ACIDS) | set(pdbio.MODIFIED_AMINO_ACIDS)
_NUCLEIC = set(pdbio.NUCLEIC_ACIDS)


# ----------------------------------------------------------------------
# 纯数据与几何（可单元测试）
# ----------------------------------------------------------------------
def element_color(element: str, *, hetero: bool = False) -> tuple[float, float, float, float]:
    """元素颜色；异原子碳使用暖橙色，便于从蛋白中区分配体/辅因子。"""
    key = (element or "").strip().upper()
    if hetero and key in {"", "C"}:
        return (0.98, 0.62, 0.22, 1.0)
    return _ELEMENT_COLORS.get(key, _ELEMENT_COLORS["C"])


@dataclass
class ViewStructure:
    """3D 视图所需的轻量结构数据。"""

    positions: np.ndarray
    colors: np.ndarray
    trace: np.ndarray
    hetero_positions: np.ndarray
    hetero_colors: np.ndarray
    center: np.ndarray
    radius: float
    atom_count: int = 0
    is_empty: bool = False


def empty_structure() -> ViewStructure:
    return ViewStructure(
        positions=np.zeros((0, 3), dtype=np.float32),
        colors=np.zeros((0, 4), dtype=np.float32),
        trace=np.zeros((0, 2, 3), dtype=np.float32),
        hetero_positions=np.zeros((0, 3), dtype=np.float32),
        hetero_colors=np.zeros((0, 4), dtype=np.float32),
        center=np.zeros(3, dtype=np.float32),
        radius=20.0,
        atom_count=0,
        is_empty=True,
    )


def _trace_points(atoms: Sequence[pdbio.Atom]) -> np.ndarray:
    """为每个链提取骨架点：蛋白用 CA，核酸用 P（没有 P 时用 C4'）。"""
    protein: dict[str, list[pdbio.Atom]] = {}
    nucleic: dict[str, list[pdbio.Atom]] = {}
    nucleic_fallback: dict[str, list[pdbio.Atom]] = {}
    for atom in atoms:
        if atom.is_hydrogen:
            continue
        resname = atom.resname.upper()
        if resname in _AMINO and atom.name.upper() == "CA":
            protein.setdefault(atom.chain, []).append(atom)
        elif resname in _NUCLEIC:
            name = atom.name.upper()
            if name == "P":
                nucleic.setdefault(atom.chain, []).append(atom)
            elif name in {"C4'", "C4*"}:
                nucleic_fallback.setdefault(atom.chain, []).append(atom)

    for chain, fallback in nucleic_fallback.items():
        nucleic.setdefault(chain, fallback)

    segments: list[list[np.ndarray]] = []
    for group, max_gap in ((protein, 5.0), (nucleic, 9.0)):
        for chain_atoms in group.values():
            ordered = sorted(chain_atoms, key=lambda a: (a.resseq, a.icode))
            points = np.array([a.coord for a in ordered], dtype=np.float32)
            if len(points) < 2:
                continue
            for index in range(len(points) - 1):
                if float(np.linalg.norm(points[index + 1] - points[index])) <= max_gap:
                    segments.append([points[index], points[index + 1]])
    if not segments:
        return np.zeros((0, 2, 3), dtype=np.float32)
    return np.asarray(segments, dtype=np.float32)


def load_view_structure(path: str | Path) -> ViewStructure:
    """读取 PDB/PDBQT，生成显示用原子点与骨架线段。"""
    path = Path(path)
    fmt = pdbio.detect_format(path)
    if fmt == "pdbqt":
        atoms = pdbio.parse_pdbqt_atoms(path)
    elif fmt in {"pdb", "ent"}:
        atoms = pdbio.parse_atoms(path, first_model=True)
    else:
        atoms = []

    heavy = [
        atom
        for atom in atoms
        if not atom.is_hydrogen and atom.resname.upper() not in pdbio.WATERS
    ]
    if not heavy:
        return empty_structure()

    polymer = [atom for atom in heavy if pdbio.is_polymer_residue(atom.resname)]
    hetero = [atom for atom in heavy if not pdbio.is_polymer_residue(atom.resname)]

    positions = np.array([atom.coord for atom in heavy], dtype=np.float32)
    colors = np.array(
        [
            element_color(
                atom.element,
                hetero=not pdbio.is_polymer_residue(atom.resname),
            )
            for atom in heavy
        ],
        dtype=np.float32,
    )

    if hetero:
        hetero_positions = np.array([atom.coord for atom in hetero], dtype=np.float32)
        hetero_colors = np.array(
            [element_color(atom.element, hetero=True) for atom in hetero],
            dtype=np.float32,
        )
    else:
        hetero_positions = np.zeros((0, 3), dtype=np.float32)
        hetero_colors = np.zeros((0, 4), dtype=np.float32)

    trace = _trace_points(polymer)

    low = positions.min(axis=0)
    high = positions.max(axis=0)
    center = (low + high) / 2.0
    radius = float(np.max(np.linalg.norm(positions - center, axis=1)))
    return ViewStructure(
        positions=positions,
        colors=colors,
        trace=trace,
        hetero_positions=hetero_positions,
        hetero_colors=hetero_colors,
        center=center,
        radius=max(radius, 5.0),
        atom_count=len(heavy),
        is_empty=False,
    )


def box_corners(center: Sequence[float], size: Sequence[float]) -> np.ndarray:
    """返回盒子的 8 个顶点，顺序与 ``BOX_EDGES``/``BOX_FACES`` 对应。"""
    c = np.asarray(center, dtype=float)
    half = np.asarray(size, dtype=float) / 2.0
    signs = [
        (-1, -1, -1),
        (1, -1, -1),
        (-1, 1, -1),
        (1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (-1, 1, 1),
        (1, 1, 1),
    ]
    return np.array(
        [[c[0] + sx * half[0], c[1] + sy * half[1], c[2] + sz * half[2]] for sx, sy, sz in signs],
        dtype=np.float32,
    )


BOX_EDGES = np.array(
    [
        (0, 1), (0, 2), (1, 3), (2, 3),
        (4, 5), (4, 6), (5, 7), (6, 7),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ],
    dtype=np.uint32,
)

BOX_FACES = np.array(
    [
        (0, 2, 3), (0, 3, 1),  # -Z
        (4, 5, 7), (4, 7, 6),  # +Z
        (0, 1, 5), (0, 5, 4),  # -Y
        (2, 6, 7), (2, 7, 3),  # +Y
        (0, 4, 6), (0, 6, 2),  # -X
        (1, 3, 7), (1, 7, 5),  # +X
    ],
    dtype=np.uint32,
)


def box_handle_positions(center: Sequence[float], size: Sequence[float]) -> np.ndarray:
    """中心点 + 六个面中心点；索引 0 为中心，1..6 依次为 +X/-X/+Y/-Y/+Z/-Z。"""
    c = np.asarray(center, dtype=float)
    half = np.asarray(size, dtype=float) / 2.0
    return np.array(
        [
            c,
            c + (half[0], 0, 0),
            c - (half[0], 0, 0),
            c + (0, half[1], 0),
            c - (0, half[1], 0),
            c + (0, 0, half[2]),
            c - (0, 0, half[2]),
        ],
        dtype=np.float32,
    )


def resize_box(
    center: Sequence[float],
    size: Sequence[float],
    axis: int,
    sign: int,
    delta: float,
    *,
    symmetric: bool = False,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """按面拖动调整盒子。

    ``sign=+1`` 表示拖动 +X/+Y/+Z 面，``-1`` 表示拖动负方向面。
    默认保持对侧面不动（盒子中心随之移动）；``symmetric=True`` 时中心不动，
    两侧同时伸缩。
    """
    center_arr = np.asarray(center, dtype=float).copy()
    size_arr = np.asarray(size, dtype=float).copy()
    change = 2.0 * delta if symmetric else delta
    new_value = float(np.clip(size_arr[axis] + change, MIN_BOX_SIZE, MAX_BOX_SIZE))
    actual = new_value - size_arr[axis]
    size_arr[axis] = new_value
    if not symmetric:
        center_arr[axis] += sign * actual / 2.0
    return (
        tuple(round(float(v), 3) for v in center_arr),
        tuple(round(float(v), 2) for v in size_arr),
    )


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        return vector
    return vector / norm


def ray_plane_intersection(
    origin: Sequence[float],
    direction: Sequence[float],
    plane_point: Sequence[float],
    plane_normal: Sequence[float],
) -> np.ndarray | None:
    """射线与平面交点；平行或交点在射线后方时返回 None。"""
    origin_arr = np.asarray(origin, dtype=float)
    direction_arr = np.asarray(direction, dtype=float)
    normal = np.asarray(plane_normal, dtype=float)
    denom = float(np.dot(direction_arr, normal))
    if abs(denom) < 1e-8:
        return None
    t = float(np.dot(np.asarray(plane_point, dtype=float) - origin_arr, normal) / denom)
    if t <= 0:
        return None
    return origin_arr + t * direction_arr


def closest_axis_parameter(
    ray_origin: Sequence[float],
    ray_direction: Sequence[float],
    axis_origin: Sequence[float],
    axis_direction: Sequence[float],
) -> float:
    """射线与轴线的最近点在轴上的参数 t（轴点为 ``axis_origin + t * axis_direction``）。"""
    d = np.asarray(ray_direction, dtype=float)
    u = np.asarray(axis_direction, dtype=float)
    w0 = np.asarray(ray_origin, dtype=float) - np.asarray(axis_origin, dtype=float)
    a = float(np.dot(d, d))
    b = float(np.dot(d, u))
    c = float(np.dot(u, u))
    dd = float(np.dot(d, w0))
    e = float(np.dot(u, w0))
    denom = a * c - b * b
    if abs(denom) < 1e-8:
        return 0.0
    return (a * e - b * dd) / denom


def project_points(
    mvp: np.ndarray,
    points: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """把世界坐标投影到控件像素坐标；相机后方的点返回 NaN。"""
    points = np.asarray(points, dtype=float)
    if points.size == 0:
        return np.zeros((0, 2), dtype=float)
    homogeneous = np.c_[points, np.ones(len(points), dtype=float)]
    clip = homogeneous @ mvp.T
    w = clip[:, 3]
    result = np.full((len(points), 2), np.nan, dtype=float)
    valid = w > 1e-8
    if not np.any(valid):
        return result
    ndc = clip[valid, :3] / w[valid, None]
    result[valid, 0] = (ndc[:, 0] + 1.0) * 0.5 * max(1, width)
    result[valid, 1] = (1.0 - ndc[:, 1]) * 0.5 * max(1, height)
    return result


def nearest_handle(
    screen_positions: np.ndarray,
    position: tuple[float, float],
    radius: float = HANDLE_PICK_PX,
) -> int | None:
    if screen_positions.size == 0:
        return None
    distances = np.linalg.norm(screen_positions - np.asarray(position, dtype=float), axis=1)
    if not np.any(np.isfinite(distances)):
        return None
    index = int(np.nanargmin(distances))
    if not math.isfinite(float(distances[index])) or float(distances[index]) > radius:
        return None
    return index


# ----------------------------------------------------------------------
# OpenGL 视图
# ----------------------------------------------------------------------
if _HAS_GL:
    _OPAQUE_OPTIONS = {
        _GL.GL_DEPTH_TEST: True,
        _GL.GL_BLEND: False,
        _GL.GL_CULL_FACE: False,
        "glDepthMask": (True,),
    }
    _TRANSLUCENT_OPTIONS = {
        _GL.GL_DEPTH_TEST: True,
        _GL.GL_BLEND: True,
        _GL.GL_CULL_FACE: False,
        "glDepthMask": (False,),
        "glBlendFunc": (_GL.GL_SRC_ALPHA, _GL.GL_ONE_MINUS_SRC_ALPHA),
    }

    class BoxGLView(gl.GLViewWidget):
        """带盒子拖拽手柄的 OpenGL 视图。"""

        box_changed = Signal(object)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent=parent)
            self.setBackgroundColor(_BACKGROUND)
            self.opts["fov"] = 45
            self.setCameraPosition(distance=120, elevation=22, azimuth=32)
            self.setMinimumSize(460, 380)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.setMouseTracking(True)

            self._structure = empty_structure()
            self._box = Box(center=(0.0, 0.0, 0.0), size=(22.0, 22.0, 22.0))
            self._click_to_center = True
            self._show_atoms = True
            self._drag: dict | None = None
            self._press_pos = None
            self._press_moved = False
            self._handle_positions = box_handle_positions(self._box.center, self._box.size)

            self._build_items()
            self._refresh_box_items()
            self.reset_view()

        # -- items ------------------------------------------------------
        def _build_items(self) -> None:
            self._trace_item = gl.GLLinePlotItem(
                pos=np.zeros((0, 3), dtype=np.float32),
                color=_TRACE_COLOR,
                width=1.8,
                mode="lines",
                antialias=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._trace_item.setDepthValue(0)
            self.addItem(self._trace_item)

            self._atom_item = gl.GLScatterPlotItem(
                pos=np.zeros((0, 3), dtype=np.float32),
                color=np.zeros((0, 4), dtype=np.float32),
                size=2.4,
                pxMode=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._atom_item.setDepthValue(1)
            self.addItem(self._atom_item)

            self._hetero_item = gl.GLScatterPlotItem(
                pos=np.zeros((0, 3), dtype=np.float32),
                color=np.zeros((0, 4), dtype=np.float32),
                size=6.0,
                pxMode=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._hetero_item.setDepthValue(2)
            self.addItem(self._hetero_item)

            self._face_item = gl.GLMeshItem(
                meshdata=gl.MeshData(
                    vertexes=box_corners(self._box.center, self._box.size),
                    faces=BOX_FACES,
                ),
                color=_BOX_FACE_COLOR,
                smooth=False,
                drawEdges=False,
                drawFaces=True,
                glOptions=_TRANSLUCENT_OPTIONS,
            )
            self._face_item.setDepthValue(5)
            self.addItem(self._face_item)

            self._edge_item = gl.GLLinePlotItem(
                pos=np.zeros((0, 3), dtype=np.float32),
                color=_BOX_COLOR,
                width=2.4,
                mode="lines",
                antialias=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._edge_item.setDepthValue(10)
            self.addItem(self._edge_item)

            self._handle_item = gl.GLScatterPlotItem(
                pos=self._handle_positions,
                color=np.array(
                    [
                        _CENTER_COLOR,
                        (0.95, 0.30, 0.30, 1.0),
                        (0.62, 0.18, 0.18, 1.0),
                        (0.34, 0.88, 0.42, 1.0),
                        (0.16, 0.55, 0.24, 1.0),
                        (0.36, 0.58, 1.00, 1.0),
                        (0.18, 0.34, 0.68, 1.0),
                    ],
                    dtype=np.float32,
                ),
                size=np.array([16, 13, 13, 13, 13, 13, 13], dtype=np.float32),
                pxMode=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._handle_item.setDepthValue(20)
            self.addItem(self._handle_item)

        # -- data -------------------------------------------------------
        def set_structure(self, structure: ViewStructure) -> None:
            self._structure = structure
            self._trace_item.setData(
                pos=structure.trace.reshape(-1, 3),
                color=_TRACE_COLOR,
                width=1.8,
                mode="lines",
                antialias=True,
            )
            self._atom_item.setData(
                pos=structure.positions,
                color=structure.colors,
                size=2.4,
                pxMode=True,
            )
            self._hetero_item.setData(
                pos=structure.hetero_positions,
                color=structure.hetero_colors,
                size=6.0,
                pxMode=True,
            )
            self._atom_item.setVisible(self._show_atoms and not structure.is_empty)
            self.reset_view()

        def set_show_atoms(self, visible: bool) -> None:
            self._show_atoms = bool(visible)
            self._atom_item.setVisible(self._show_atoms and not self._structure.is_empty)

        def set_click_to_center(self, enabled: bool) -> None:
            self._click_to_center = bool(enabled)

        def set_box(self, box: Box, *, emit: bool = False) -> None:
            self._box = box
            self._refresh_box_items()
            if emit:
                self.box_changed.emit(box)

        def box(self) -> Box:
            return self._box

        def _refresh_box_items(self) -> None:
            corners = box_corners(self._box.center, self._box.size)
            self._edge_item.setData(
                pos=corners[BOX_EDGES].reshape(-1, 3),
                color=_BOX_COLOR,
                width=2.4,
                mode="lines",
                antialias=True,
            )
            self._face_item.setMeshData(
                meshdata=gl.MeshData(vertexes=corners, faces=BOX_FACES)
            )
            self._handle_positions = box_handle_positions(self._box.center, self._box.size)
            self._handle_item.setData(
                pos=self._handle_positions,
                size=np.array([16, 13, 13, 13, 13, 13, 13], dtype=np.float32),
                pxMode=True,
            )

        # -- camera -----------------------------------------------------
        def reset_view(self) -> None:
            structure = self._structure
            if structure.is_empty:
                center = np.asarray(self._box.center, dtype=float)
                radius = max(float(np.max(self._box.size)), 10.0)
            else:
                center = structure.center
                radius = structure.radius
            distance = max(12.0, radius / math.tan(math.radians(self.opts["fov"]) / 2.0) * 1.15)
            self.setCameraPosition(
                pos=Vector(float(center[0]), float(center[1]), float(center[2])),
                distance=distance,
                elevation=22,
                azimuth=32,
            )

        def focus_box(self) -> None:
            center = np.asarray(self._box.center, dtype=float)
            radius = max(float(np.max(self._box.size)) * 0.9, 8.0)
            distance = max(10.0, radius / math.tan(math.radians(self.opts["fov"]) / 2.0) * 1.5)
            self.setCameraPosition(
                pos=Vector(float(center[0]), float(center[1]), float(center[2])),
                distance=distance,
            )

        # -- projection / picking --------------------------------------
        def _mvp(self) -> np.ndarray:
            region = (0, 0, max(1, self.width()), max(1, self.height()))
            viewport = self.getViewport()
            matrix = self.projectionMatrix(region, viewport) * self.viewMatrix()
            return np.asarray(matrix.data(), dtype=float).reshape(4, 4).T

        def _project(self, points: np.ndarray) -> np.ndarray:
            return project_points(self._mvp(), points, self.width(), self.height())

        def _ray(self, position) -> tuple[np.ndarray, np.ndarray]:
            width = max(1, self.width())
            height = max(1, self.height())
            nx = 2.0 * float(position.x()) / width - 1.0
            ny = 1.0 - 2.0 * float(position.y()) / height
            inverse = np.linalg.inv(self._mvp())
            near = inverse @ np.array([nx, ny, -1.0, 1.0])
            far = inverse @ np.array([nx, ny, 1.0, 1.0])
            near = near[:3] / near[3]
            far = far[:3] / far[3]
            return near, _normalize(far - near)

        def _camera_forward(self) -> np.ndarray:
            center_vec = self.opts["center"]
            camera_vec = self.cameraPosition()
            center = np.array([center_vec.x(), center_vec.y(), center_vec.z()], dtype=float)
            camera = np.array([camera_vec.x(), camera_vec.y(), camera_vec.z()], dtype=float)
            return _normalize(center - camera)

        def _pick_handle(self, position) -> int | None:
            screen = self._project(self._handle_positions)
            return nearest_handle(screen, (position.x(), position.y()))

        def _pick_atom(self, position) -> np.ndarray | None:
            if self._structure.is_empty:
                return None
            screen = self._project(self._structure.positions)
            distances = np.linalg.norm(
                screen - np.array([position.x(), position.y()]), axis=1
            )
            if not np.any(np.isfinite(distances)):
                return None
            index = int(np.nanargmin(distances))
            if not math.isfinite(float(distances[index])) or float(distances[index]) > ATOM_PICK_PX:
                return None
            return self._structure.positions[index].astype(float)

        # -- drag -------------------------------------------------------
        def mousePressEvent(self, event) -> None:
            position = event.position()
            if event.button() == Qt.MouseButton.LeftButton:
                self.mousePos = position
                handle = self._pick_handle(position)
                if handle is not None:
                    self._start_drag(handle, position)
                    event.accept()
                    return
                self._press_pos = position
                self._press_moved = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._drag is not None:
                self._drag_to(event.position(), event.modifiers())
                self.mousePos = event.position()
                event.accept()
                return
            if self._press_pos is not None:
                distance = (event.position() - self._press_pos).manhattanLength()
                if distance > 4:
                    self._press_moved = True
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if self._drag is not None:
                self._drag = None
                self._press_pos = None
                self._press_moved = False
                self.mousePos = event.position()
                event.accept()
                return
            if (
                event.button() == Qt.MouseButton.LeftButton
                and self._press_pos is not None
                and not self._press_moved
                and self._click_to_center
            ):
                point = self._pick_atom(event.position())
                if point is not None:
                    center = tuple(round(float(value), 3) for value in point)
                    self.set_box(
                        Box(
                            center=center,
                            size=self._box.size,
                            source="manual",
                            note="在 3D 视图中点击结构设置中心",
                        ),
                        emit=True,
                    )
            self._press_pos = None
            self._press_moved = False
            super().mouseReleaseEvent(event)

        def _start_drag(self, handle: int, position) -> None:
            origin, direction = self._ray(position)
            center = np.asarray(self._box.center, dtype=float)
            size = np.asarray(self._box.size, dtype=float)
            if handle == 0:
                normal = self._camera_forward()
                hit = ray_plane_intersection(origin, direction, center, normal)
                if hit is None:
                    return
                self._drag = {
                    "kind": "move",
                    "center": center,
                    "size": size,
                    "normal": normal,
                    "hit": hit,
                }
            else:
                axis_index = (handle - 1) // 2
                sign = 1 if (handle - 1) % 2 == 0 else -1
                axis = np.zeros(3)
                axis[axis_index] = 1.0
                parameter = closest_axis_parameter(origin, direction, center, axis)
                self._drag = {
                    "kind": "resize",
                    "center": center,
                    "size": size,
                    "axis_index": axis_index,
                    "axis": axis,
                    "sign": sign,
                    "start": parameter,
                }
            self._press_pos = position
            self._press_moved = False

        def _drag_to(self, position, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
            if self._drag is None:
                return
            origin, direction = self._ray(position)
            data = self._drag
            center = data["center"]
            size = data["size"]
            if data["kind"] == "move":
                hit = ray_plane_intersection(origin, direction, center, data["normal"])
                if hit is None:
                    return
                new_center = center + (hit - data["hit"])
                new_center = tuple(round(float(value), 3) for value in new_center)
                new_size = tuple(round(float(value), 2) for value in size)
            else:
                axis = data["axis"]
                parameter = closest_axis_parameter(origin, direction, center, axis)
                delta = parameter - data["start"]
                symmetric = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
                new_center, new_size = resize_box(
                    center,
                    size,
                    data["axis_index"],
                    data["sign"],
                    delta,
                    symmetric=symmetric,
                )
            box = Box(
                center=new_center,
                size=new_size,
                source="manual",
                note="在 3D 视图中拖动调整",
            )
            self.set_box(box, emit=True)


else:  # pragma: no cover - 仅在缺少 pyqtgraph/PyOpenGL 时使用
    BoxGLView = None  # type: ignore[assignment,misc]


# ----------------------------------------------------------------------
# 带数值输入的面板
# ----------------------------------------------------------------------
class BoxViewer(QWidget):
    """3D 视图 + 数值输入。缺 OpenGL 时只显示数值输入。"""

    auto_requested = Signal()
    box_edited = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._box: Box | None = None
        self._dirty = False
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        if _HAS_GL:
            self.view: BoxGLView | None = BoxGLView(self)
            self.view.box_changed.connect(self._on_view_box_changed)
            layout.addWidget(self.view, 3)
        else:
            self.view = None
            placeholder = QLabel(
                "3D 视图不可用（缺少 pyqtgraph / PyOpenGL 或 OpenGL 驱动）。\n"
                "仍可在右侧输入盒子中心与边长，保存与对接不受影响。\n\n"
                f"技术信息：{_GL_ERROR}"
            )
            placeholder.setObjectName("Muted")
            placeholder.setWordWrap(True)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setFrameShape(QFrame.Shape.StyledPanel)
            placeholder.setMinimumWidth(420)
            placeholder.setStyleSheet(
                "background:#0b1016; color:#aab4c4; border:1px solid #1d2836;"
                "border-radius:8px; padding:24px;"
            )
            layout.addWidget(placeholder, 3)

        layout.addWidget(self._build_panel(), 0)

    # -- panel ----------------------------------------------------------
    def _build_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Tile")
        panel.setMinimumWidth(310)
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("盒子位置与大小")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        self.status_label = QLabel("—")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addWidget(self._section_label("中心 (Å)"))
        center_row = QHBoxLayout()
        center_row.setSpacing(6)
        self.center_inputs: list[QDoubleSpinBox] = []
        for axis, color in (("X", "#ff5a5a"), ("Y", "#43d46a"), ("Z", "#5f8dff")):
            center_row.addWidget(self._axis_label(axis, color))
            spin = QDoubleSpinBox()
            spin.setRange(-10000.0, 10000.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setMinimumWidth(78)
            spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            spin.valueChanged.connect(self._on_spin_changed)
            self.center_inputs.append(spin)
            center_row.addWidget(spin, 1)
        layout.addLayout(center_row)

        layout.addWidget(self._section_label("边长 (Å)"))
        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        self.size_inputs: list[QDoubleSpinBox] = []
        for axis, color in (("X", "#ff5a5a"), ("Y", "#43d46a"), ("Z", "#5f8dff")):
            size_row.addWidget(self._axis_label(axis, color))
            spin = QDoubleSpinBox()
            spin.setRange(MIN_BOX_SIZE, MAX_BOX_SIZE)
            spin.setDecimals(1)
            spin.setSingleStep(1.0)
            spin.setValue(22.0)
            spin.setMinimumWidth(78)
            spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            spin.valueChanged.connect(self._on_spin_changed)
            self.size_inputs.append(spin)
            size_row.addWidget(spin, 1)
        layout.addLayout(size_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        auto_button = QPushButton("自动推荐")
        auto_button.setObjectName("Small")
        auto_button.clicked.connect(self.auto_requested.emit)
        focus_button = QPushButton("聚焦盒子")
        focus_button.setObjectName("Small")
        focus_button.clicked.connect(self._focus_box)
        buttons.addWidget(auto_button)
        buttons.addWidget(focus_button)
        layout.addLayout(buttons)

        reset_button = QPushButton("重置视角")
        reset_button.setObjectName("Small")
        reset_button.clicked.connect(self._reset_view)
        layout.addWidget(reset_button)

        self.click_checkbox = QCheckBox("点击结构设置盒子中心")
        self.click_checkbox.setChecked(True)
        self.click_checkbox.toggled.connect(self._set_click_to_center)
        layout.addWidget(self.click_checkbox)

        self.simple_checkbox = QCheckBox("简化显示（隐藏原子点，仅保留骨架）")
        self.simple_checkbox.setChecked(False)
        self.simple_checkbox.toggled.connect(self._set_show_atoms)
        layout.addWidget(self.simple_checkbox)

        legend = QLabel(
            '<span style="color:#ffffff">●</span> 中心　'
            '<span style="color:#ff5a5a">●</span> X　'
            '<span style="color:#43d46a">●</span> Y　'
            '<span style="color:#5f8dff">●</span> Z'
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setObjectName("Muted")
        layout.addWidget(legend)

        hint = QLabel(
            "左键拖动旋转 · 滚轮缩放 · 右键拖动平移\n"
            "拖动白色中心点移动盒子，拖动彩色面点调整边长。\n"
            "按住 Shift 拖动面点时，盒子中心保持不变。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return panel

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600; margin-top: 4px;")
        return label

    @staticmethod
    def _axis_label(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color:{color}; font-weight: 700;")
        label.setFixedWidth(14)
        return label

    # -- data -----------------------------------------------------------
    def load_structure(self, path: str | Path) -> bool:
        structure = load_view_structure(path)
        if structure.is_empty:
            return False
        if self.view is not None:
            self.view.set_structure(structure)
        return True

    def set_box(self, box: Box, *, mark_manual: bool = False) -> None:
        self._box = box
        self._dirty = bool(mark_manual) or box.source == "manual"
        self._syncing = True
        try:
            for spin, value in zip(self.center_inputs, box.center):
                spin.setValue(float(value))
            for spin, value in zip(self.size_inputs, box.size):
                spin.setValue(float(value))
        finally:
            self._syncing = False
        if self.view is not None:
            self.view.set_box(box, emit=False)
        self._update_status()

    def use_auto(self, box: Box) -> None:
        self.set_box(box, mark_manual=False)

    def current_box(self) -> Box | None:
        if self._dirty or self._box is None:
            if self._box is None and not self._dirty:
                return None
            return Box(
                center=tuple(spin.value() for spin in self.center_inputs),
                size=tuple(spin.value() for spin in self.size_inputs),
                source="manual",
                note="在 3D 视图中手动调整",
            )
        return self._box

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def has_box(self) -> bool:
        return self._box is not None

    def focus_box(self) -> None:
        if self.view is not None:
            self.view.focus_box()

    def reset_view(self) -> None:
        if self.view is not None:
            self.view.reset_view()

    def show_placeholder(self, message: str) -> None:
        if self.view is None:
            return
        self.view.set_structure(empty_structure())
        self._update_status(message)

    # -- events ---------------------------------------------------------
    def _on_view_box_changed(self, box: Box) -> None:
        self._box = box
        self._dirty = True
        self._syncing = True
        try:
            for spin, value in zip(self.center_inputs, box.center):
                spin.setValue(float(value))
            for spin, value in zip(self.size_inputs, box.size):
                spin.setValue(float(value))
        finally:
            self._syncing = False
        self._update_status()
        self.box_edited.emit(box)

    def _on_spin_changed(self, _value: float) -> None:
        if self._syncing:
            return
        self._dirty = True
        center = tuple(spin.value() for spin in self.center_inputs)
        size = tuple(spin.value() for spin in self.size_inputs)
        box = Box(center=center, size=size, source="manual", note="在输入框中手动调整")
        self._box = box
        if self.view is not None:
            self.view.set_box(box, emit=False)
        self._update_status()
        self.box_edited.emit(box)

    def _update_status(self, override: str = "") -> None:
        if override:
            self.status_label.setText(override)
            return
        if self._box is None:
            self.status_label.setText("尚未设置盒子。可点击“自动推荐”或直接输入。")
            return
        if self._dirty:
            source = "手动调整"
        else:
            source = self._box.source_label
        size = " × ".join(f"{value:.1f}" for value in self._box.size)
        volume = self._box.volume
        text = f"当前：{source}\n尺寸 {size} Å · 体积 {volume:,.0f} Å³"
        if self._box.note:
            text += f"\n{self._box.note}"
        self.status_label.setText(text)

    def _set_click_to_center(self, enabled: bool) -> None:
        if self.view is not None:
            self.view.set_click_to_center(enabled)

    def _set_show_atoms(self, simplified: bool) -> None:
        if self.view is not None:
            self.view.set_show_atoms(not simplified)

    def _focus_box(self) -> None:
        if self.view is not None:
            self.view.focus_box()

    def _reset_view(self) -> None:
        if self.view is not None:
            self.view.reset_view()

    def sizeHint(self) -> QSize:
        return QSize(980, 620)
