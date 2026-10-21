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

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# pyqtgraph 会按 PyQt5 → PySide2 → PyQt6 → PySide6 的顺序探测 Qt 绑定。
# 本项目统一使用 PySide6，必须在导入 pyqtgraph 之前固定，避免混用两套 Qt。
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import pdbio
from ..core.models import MAX_BOX_SIZE, MIN_BOX_SIZE, Box
from . import theme

log = logging.getLogger("box_view")

# 参考层的配色，需与 resources/ngl/viewer.html 中的 colorValue 保持一致。
COCRYSTAL_COLOR = "#e052a0"  # 共晶配体（品红）
SITE_COLOR = "#1fb8c8"  # PDB SITE 残基（青色）

# NGL Viewer（QtWebEngine）优先；导入本模块时就会完成 QtWebEngine 的初始化，
# 这对 PySide6 是必需的（必须在 QApplication 创建之前导入）。
# 打包损坏、WebGL 不可用或缺少资源时自动回退到下面的 pyqtgraph 渲染。
try:  # pragma: no cover - 取决于运行环境是否包含 QtWebEngine
    from .ngl_view import NGL_AVAILABLE, NglBoxView
except Exception as _ngl_exc:  # pragma: no cover - 缺少 QtWebEngine 的降级路径
    NGL_AVAILABLE = False
    NglBoxView = None  # type: ignore[assignment]
    _NGL_ERROR = str(_ngl_exc)
else:
    _NGL_ERROR = ""

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


HANDLE_PICK_PX = 16.0
ATOM_PICK_PX = 14.0

_BACKGROUND = (0.045, 0.060, 0.085, 1.0)
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
class BackboneResidue:
    """卡通模型需要的主链信息；核酸残基用 P 作为 ``ca``。"""

    chain: str
    resseq: int
    icode: str
    resname: str
    n: np.ndarray | None
    ca: np.ndarray
    c: np.ndarray | None
    sse: str = "C"


@dataclass
class ViewStructure:
    """3D 视图所需的轻量结构数据。"""

    positions: np.ndarray
    colors: np.ndarray
    cartoon_vertices: np.ndarray
    cartoon_faces: np.ndarray
    cartoon_colors: np.ndarray
    hetero_positions: np.ndarray
    hetero_colors: np.ndarray
    hetero_bonds: np.ndarray
    center: np.ndarray
    radius: float
    atom_count: int = 0
    is_empty: bool = False
    # 原始 PDB 文本，供 NGL Viewer 加载；OpenGL 回退路径不使用。
    pdb_text: str = ""


def empty_structure() -> ViewStructure:
    return ViewStructure(
        positions=np.zeros((0, 3), dtype=np.float32),
        colors=np.zeros((0, 4), dtype=np.float32),
        cartoon_vertices=np.zeros((0, 3), dtype=np.float32),
        cartoon_faces=np.zeros((0, 3), dtype=np.uint32),
        cartoon_colors=np.zeros((0, 4), dtype=np.float32),
        hetero_positions=np.zeros((0, 3), dtype=np.float32),
        hetero_colors=np.zeros((0, 4), dtype=np.float32),
        hetero_bonds=np.zeros((0, 2, 3), dtype=np.float32),
        center=np.zeros(3, dtype=np.float32),
        radius=20.0,
        atom_count=0,
        is_empty=True,
        pdb_text="",
    )


# 卡通模型参数（单位 Å）。数值参考常见分子查看器的比例，便于在盒子
# 编辑器里一眼认出螺旋、折叠与 loop，而不追求与 PyMOL 完全一致。
_HELIX_RADIUS = 1.45
_HELIX_HALF_WIDTH = 1.05
_STRAND_HALF_WIDTH = 1.15
_ARROW_HALF_WIDTH = 2.15
_COIL_RADIUS = 0.42
_RIBBON_HALF_THICKNESS = 0.12
_HELIX_TURN_DEGREES = 100.0  # α-螺旋每残基约 100°

_SSE_COLORS = {
    "H": (0.78, 0.81, 0.87, 1.0),
    "E": (0.68, 0.73, 0.81, 1.0),
    "C": (0.60, 0.65, 0.73, 1.0),
}

_BOND_RADII = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "P": 1.07,
    "S": 1.05,
    "CL": 1.02,
    "BR": 1.20,
    "I": 1.39,
}


def _coerce_point(value) -> np.ndarray | None:
    if value is None:
        return None
    point = np.asarray(value, dtype=float).reshape(3)
    if not np.all(np.isfinite(point)):
        return None
    return point


def dihedral_angle(
    p0: Sequence[float],
    p1: Sequence[float],
    p2: Sequence[float],
    p3: Sequence[float],
) -> float | None:
    """四点二面角（度）；退化时返回 None。"""
    b0 = np.asarray(p0, dtype=float) - np.asarray(p1, dtype=float)
    b1 = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    b2 = np.asarray(p3, dtype=float) - np.asarray(p2, dtype=float)
    norm_b1 = float(np.linalg.norm(b1))
    if norm_b1 < 1e-9:
        return None
    b1 = b1 / norm_b1
    v = b0 - float(np.dot(b0, b1)) * b1
    w = b2 - float(np.dot(b2, b1)) * b1
    if float(np.linalg.norm(v)) < 1e-9 or float(np.linalg.norm(w)) < 1e-9:
        return None
    x = float(np.dot(v, w))
    y = float(np.dot(np.cross(b1, v), w))
    return math.degrees(math.atan2(y, x))


def classify_phi_psi(phi: float | None, psi: float | None) -> str:
    """按 φ/ψ 判断二级结构：H（螺旋）、E（折叠）、C（无规卷曲）。"""
    if phi is None or psi is None:
        return "C"
    if -160.0 <= phi <= -30.0 and -85.0 <= psi <= 15.0:
        return "H"
    if -175.0 <= phi <= -45.0 and (psi >= 80.0 or psi <= -155.0):
        return "E"
    return "C"


def collect_backbone_residues(atoms: Sequence[pdbio.Atom]) -> list[BackboneResidue]:
    """按残基汇总主链 N/CA/C；核酸使用 P（或 C4'）。"""
    groups: dict[tuple[str, int, str, str], dict[str, np.ndarray]] = {}
    order: list[tuple[str, int, str, str]] = []
    for atom in atoms:
        if atom.is_hydrogen:
            continue
        if not pdbio.is_polymer_residue(atom.resname):
            continue
        key = atom.residue_key
        if key not in groups:
            groups[key] = {}
            order.append(key)
        name = atom.name.upper()
        if name not in groups[key]:
            point = _coerce_point(atom.coord)
            if point is not None:
                groups[key][name] = point

    residues: list[BackboneResidue] = []
    for chain, resseq, icode, resname in order:
        atoms_by_name = groups[(chain, resseq, icode, resname)]
        if resname in _AMINO:
            ca = atoms_by_name.get("CA")
            if ca is None:
                continue
            residues.append(
                BackboneResidue(
                    chain=chain,
                    resseq=resseq,
                    icode=icode,
                    resname=resname,
                    n=atoms_by_name.get("N"),
                    ca=ca,
                    c=atoms_by_name.get("C"),
                )
            )
        elif resname in _NUCLEIC:
            ca = (
                atoms_by_name.get("P")
                if atoms_by_name.get("P") is not None
                else atoms_by_name.get("C4'", atoms_by_name.get("C4*"))
            )
            if ca is None:
                continue
            residues.append(
                BackboneResidue(
                    chain=chain,
                    resseq=resseq,
                    icode=icode,
                    resname=resname,
                    n=None,
                    ca=ca,
                    c=None,
                )
            )
    return residues


def _smooth_sse(labels: list[str], gap: float = 4.8) -> list[str]:
    """整理二级结构：短片段降级、单残基空档补齐、螺旋两端延伸。"""
    result = list(labels)
    size = len(result)
    if not size:
        return result

    # 同一链内按顺序处理；调用方保证 labels 已按链分好。
    index = 0
    while index < size:
        end = index + 1
        while end < size and result[end] == result[index]:
            end += 1
        run = end - index
        if result[index] == "H" and run < 4:
            for i in range(index, end):
                result[i] = "C"
        elif result[index] == "E" and run < 3:
            for i in range(index, end):
                result[i] = "C"
        index = end

    # 被两个相同结构夹住的单残基空档，归入该结构。
    for i in range(1, size - 1):
        if result[i] == "C" and result[i - 1] == result[i + 1] != "C":
            result[i] = result[i - 1]

    # 螺旋两端各延伸一个残基，覆盖边界处常被误判的残基。
    extension = [i for i, label in enumerate(result) if label == "H"]
    for i in extension:
        for neighbor in (i - 1, i + 1):
            if 0 <= neighbor < size and result[neighbor] == "C":
                result[neighbor] = "H"
    return result


def assign_secondary_structure(residues: Sequence[BackboneResidue]) -> None:
    """根据 φ/ψ 为残基写入 ``sse``（就地修改）。"""
    by_chain: dict[str, list[BackboneResidue]] = {}
    for residue in residues:
        by_chain.setdefault(residue.chain, []).append(residue)

    for chain_residues in by_chain.values():
        raw: list[str] = []
        for index, residue in enumerate(chain_residues):
            if residue.n is None or residue.c is None:
                raw.append("C")
                continue
            previous = chain_residues[index - 1] if index > 0 else None
            following = (
                chain_residues[index + 1] if index + 1 < len(chain_residues) else None
            )
            phi = None
            psi = None
            if previous is not None and previous.c is not None:
                if float(np.linalg.norm(previous.ca - residue.ca)) <= 5.0:
                    phi = dihedral_angle(previous.c, residue.n, residue.ca, residue.c)
            if following is not None and following.n is not None:
                if float(np.linalg.norm(following.ca - residue.ca)) <= 5.0:
                    psi = dihedral_angle(residue.n, residue.ca, residue.c, following.n)
            raw.append(classify_phi_psi(phi, psi))
        for residue, label in zip(chain_residues, _smooth_sse(raw)):
            residue.sse = label


# ----------------------------------------------------------------------
# 卡通几何
# ----------------------------------------------------------------------
_RECT_PROFILE = np.array([(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)])
_OCT_PROFILE = np.array(
    [
        (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
        for angle in range(0, 360, 45)
    ]
)


def _catmull_rom(points: np.ndarray, index: int, t: float) -> np.ndarray:
    count = len(points)
    p0 = points[max(index - 1, 0)]
    p1 = points[index]
    p2 = points[min(index + 1, count - 1)]
    p3 = points[min(index + 2, count - 1)]
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        2.0 * p1
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _split_segments(
    residues: Sequence[BackboneResidue],
) -> list[list[BackboneResidue]]:
    segments: list[list[BackboneResidue]] = []
    for residue in residues:
        max_gap = 8.5 if residue.resname in _NUCLEIC else 4.8
        if (
            not segments
            or residue.chain != segments[-1][-1].chain
            or float(np.linalg.norm(residue.ca - segments[-1][-1].ca)) > max_gap
        ):
            segments.append([residue])
        else:
            segments[-1].append(residue)
    return segments


def _split_sse_runs(
    segment: Sequence[BackboneResidue],
) -> list[tuple[int, int, str]]:
    runs: list[tuple[int, int, str]] = []
    start = 0
    for index in range(1, len(segment) + 1):
        if index == len(segment) or segment[index].sse != segment[start].sse:
            runs.append((start, index - 1, segment[start].sse))
            start = index
    return runs


def _loft(
    centers: np.ndarray,
    us: np.ndarray,
    vs: np.ndarray,
    half_widths: np.ndarray,
    half_thicknesses: np.ndarray,
    profile: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """把一串截面放样成两端封口的三角网格。"""
    if len(centers) < 2:
        return np.zeros((0, 3), dtype=np.float32), np.zeros(
            (0, 3), dtype=np.uint32
        )

    orientation = float(
        np.dot(np.cross(us[0], vs[0]), centers[1] - centers[0])
    )
    ring_profile = profile[::-1] if orientation < 0 else profile
    count = len(ring_profile)

    vertex_rings = []
    for center, u, v, half_width, half_thickness in zip(
        centers, us, vs, half_widths, half_thicknesses
    ):
        vertex_rings.append(
            np.array(
                [
                    center + du * half_width * u + dv * half_thickness * v
                    for du, dv in ring_profile
                ],
                dtype=np.float32,
            )
        )
    vertices = np.concatenate(vertex_rings, axis=0)

    faces = []
    for ring in range(len(vertex_rings) - 1):
        base = ring * count
        next_base = (ring + 1) * count
        for corner in range(count):
            following = (corner + 1) % count
            faces.append(
                (
                    base + corner,
                    base + following,
                    next_base + following,
                )
            )
            faces.append(
                (
                    base + corner,
                    next_base + following,
                    next_base + corner,
                )
            )

    # 端盖：避免从开口处看到带状模型的内部。
    last_base = (len(vertex_rings) - 1) * count
    front_center = len(vertices)
    back_center = front_center + 1
    vertices = np.vstack(
        [
            vertices,
            centers[0].astype(np.float32),
            centers[-1].astype(np.float32),
        ]
    )
    for corner in range(count):
        following = (corner + 1) % count
        faces.append((front_center, following, corner))
        faces.append(
            (back_center, last_base + corner, last_base + following)
        )
    return vertices, np.asarray(faces, dtype=np.uint32)


def _run_samples(
    points: np.ndarray,
    start: int,
    end: int,
    samples_per_residue: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """采样一段路径；返回位置和对应的残基浮点索引。"""
    if end <= start:
        return np.array([points[start]], dtype=float), np.array([float(start)])
    positions: list[np.ndarray] = []
    indices: list[float] = []
    for index in range(start, end):
        for step in range(samples_per_residue):
            t = step / samples_per_residue
            positions.append(_catmull_rom(points, index, t))
            indices.append(index + t)
    positions.append(points[end].astype(float))
    indices.append(float(end))
    return np.asarray(positions), np.asarray(indices)


def _sample_tangents(positions: np.ndarray) -> np.ndarray:
    tangents = np.gradient(positions, axis=0)
    norms = np.linalg.norm(tangents, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return tangents / norms


def _perpendicular(vector: np.ndarray, tangent: np.ndarray) -> np.ndarray | None:
    perpendicular = vector - float(np.dot(vector, tangent)) * tangent
    norm = float(np.linalg.norm(perpendicular))
    if norm < 1e-6:
        return None
    return perpendicular / norm


def _helix_geometry(
    points: np.ndarray,
    start: int,
    end: int,
    run_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    positions, indices = _run_samples(points, start, end)
    tangents = _sample_tangents(positions)

    centroid = run_points.mean(axis=0)
    centered = run_points - centroid
    if len(run_points) >= 3:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        axis = vh[0]
    else:
        axis = tangents[0]
    axis = axis / max(float(np.linalg.norm(axis)), 1e-9)
    if float(np.dot(axis, run_points[-1] - run_points[0])) < 0:
        axis = -axis

    radials = []
    for point in run_points:
        radial = _perpendicular(point - centroid, axis)
        radials.append(radial if radial is not None else np.zeros(3))
    radials = np.asarray(radials)

    handedness = 0.0
    for index in range(len(radials) - 1):
        handedness += float(np.dot(np.cross(radials[index], radials[index + 1]), axis))
    direction = 1.0 if handedness >= 0 else -1.0

    base_radial = _perpendicular(radials[0], tangents[0])
    if base_radial is None:
        base_radial = _perpendicular(np.array([1.0, 0.0, 0.0]), tangents[0])
    if base_radial is None:
        base_radial = _perpendicular(np.array([0.0, 1.0, 0.0]), tangents[0])
    base_radial = np.asarray(base_radial, dtype=float)

    centers = []
    us = []
    vs = []
    widths = []
    for position, tangent, index in zip(positions, tangents, indices):
        angle = math.radians(direction * _HELIX_TURN_DEGREES * (index - start))
        radial = _rotate_vector(base_radial, axis, angle)
        radial = _perpendicular(radial, tangent)
        if radial is None:
            radial = base_radial
        # 螺旋两端把偏移收回到主链，避免与 loop 之间出现断层。
        edge = min(index - start, end - index)
        taper = min(1.0, max(0.0, edge / 0.8))
        centers.append(position + radial * _HELIX_RADIUS * taper)
        # 带状面沿螺旋轴方向铺开、沿径向保持很薄；若把宽度放在切线
        # 方向，截面会顺路径展开，看起来像一根圆管而不是螺旋带。
        width_direction = _perpendicular(axis, tangent)
        us.append(width_direction if width_direction is not None else axis)
        vs.append(radial)
        widths.append(_HELIX_HALF_WIDTH)
    return (
        np.asarray(centers),
        np.asarray(us),
        np.asarray(vs),
        np.asarray(widths),
    )


def _rotate_vector(vector: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues 旋转。"""
    axis = axis / max(float(np.linalg.norm(axis)), 1e-9)
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    return (
        vector * cos_angle
        + np.cross(axis, vector) * sin_angle
        + axis * float(np.dot(axis, vector)) * (1.0 - cos_angle)
    )


def _strand_geometry(
    points: np.ndarray,
    start: int,
    end: int,
    run_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    positions, indices = _run_samples(points, start, end)
    tangents = _sample_tangents(positions)

    centroid = run_points.mean(axis=0)
    centered = run_points - centroid
    if len(run_points) >= 3:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1]
    else:
        normal = tangents[0]
    normal = normal / max(float(np.linalg.norm(normal)), 1e-9)

    us = []
    vs = []
    for tangent in tangents:
        plane_normal = _perpendicular(normal, tangent)
        if plane_normal is None:
            plane_normal = _perpendicular(np.array([0.0, 1.0, 0.0]), tangent)
        if plane_normal is None:
            plane_normal = np.array([0.0, 0.0, 1.0])
        width_direction = np.cross(plane_normal, tangent)
        width_direction /= max(float(np.linalg.norm(width_direction)), 1e-9)
        us.append(width_direction)
        vs.append(plane_normal)

    widths = np.full(len(positions), _STRAND_HALF_WIDTH)
    arrow = len(positions) >= 3
    if arrow:
        # 末端逐渐张开，再加一个收拢到尖端的箭头。
        for i, index in enumerate(indices):
            distance = end - index
            if distance <= 2.0:
                factor = 1.0 + 0.85 * (1.0 - distance / 2.0)
                widths[i] = _STRAND_HALF_WIDTH * factor
        tip_base = positions[-1]
        tangent = tangents[-1]
        positions = np.vstack(
            [
                positions,
                tip_base + tangent * 0.35,
                tip_base + tangent * 1.45,
            ]
        )
        us = np.vstack([us, us[-1], us[-1]])
        vs = np.vstack([vs, vs[-1], vs[-1]])
        widths = np.concatenate([widths, [_ARROW_HALF_WIDTH, 0.04]])

    return (
        np.asarray(positions),
        np.asarray(us),
        np.asarray(vs),
        widths,
    )


def _coil_geometry(
    points: np.ndarray, start: int, end: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    positions, _ = _run_samples(points, start, end)
    tangents = _sample_tangents(positions)

    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(tangents[0], reference))) > 0.9:
        reference = np.array([1.0, 0.0, 0.0])
    u = np.cross(tangents[0], reference)
    u /= max(float(np.linalg.norm(u)), 1e-9)

    us = []
    vs = []
    previous_tangent = tangents[0]
    for tangent in tangents:
        axis = np.cross(previous_tangent, tangent)
        sine = float(np.linalg.norm(axis))
        if sine > 1e-9:
            angle = math.atan2(sine, float(np.dot(previous_tangent, tangent)))
            u = _rotate_vector(u, axis / sine, angle)
        projected = _perpendicular(u, tangent)
        if projected is not None:
            u = projected
        us.append(u)
        vs.append(np.cross(tangent, u))
        previous_tangent = tangent
    return np.asarray(positions), np.asarray(us), np.asarray(vs)


def cartoon_geometry(
    residues: Sequence[BackboneResidue],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """生成卡通模型三角网格：顶点、面、顶点颜色。"""
    vertices_parts: list[np.ndarray] = []
    faces_parts: list[np.ndarray] = []
    colors_parts: list[np.ndarray] = []
    vertex_offset = 0

    for segment in _split_segments(residues):
        if len(segment) < 2:
            continue
        points = np.asarray([residue.ca for residue in segment], dtype=float)
        for start, end, sse in _split_sse_runs(segment):
            if end <= start:
                continue
            run_points = points[start : end + 1]
            color = _SSE_COLORS.get(sse, _SSE_COLORS["C"])
            if sse == "H":
                centers, us, vs, widths = _helix_geometry(
                    points, start, end, run_points
                )
                thicknesses = np.full(len(centers), _RIBBON_HALF_THICKNESS)
                profile = _RECT_PROFILE
            elif sse == "E":
                centers, us, vs, widths = _strand_geometry(
                    points, start, end, run_points
                )
                thicknesses = np.full(len(centers), _RIBBON_HALF_THICKNESS)
                profile = _RECT_PROFILE
            else:
                centers, us, vs = _coil_geometry(points, start, end)
                widths = np.full(len(centers), _COIL_RADIUS)
                thicknesses = np.full(len(centers), _COIL_RADIUS)
                profile = _OCT_PROFILE

            vertices, faces = _loft(
                centers, us, vs, widths, thicknesses, profile
            )
            if len(vertices) == 0:
                continue
            faces_parts.append(faces + vertex_offset)
            vertices_parts.append(vertices)
            colors_parts.append(
                np.tile(np.asarray(color, dtype=np.float32), (len(vertices), 1))
            )
            vertex_offset += len(vertices)

    if not vertices_parts:
        return (
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.uint32),
            np.zeros((0, 4), dtype=np.float32),
        )
    return (
        np.concatenate(vertices_parts).astype(np.float32),
        np.concatenate(faces_parts).astype(np.uint32),
        np.concatenate(colors_parts).astype(np.float32),
    )


def _hetero_bonds(
    positions: np.ndarray, elements: Sequence[str]
) -> np.ndarray:
    """按共价半径连接杂原子，生成配体棍状模型的线段。"""
    count = len(positions)
    if count < 2:
        return np.zeros((0, 2, 3), dtype=np.float32)
    radii = np.asarray(
        [_BOND_RADII.get((element or "").upper(), 0.8) for element in elements],
        dtype=np.float32,
    )
    limits = (radii[:, None] + radii[None, :]) * 1.3
    # 分块计算，避免大结构中杂原子较多时一次生成 N×N×3 的临时数组。
    block = max(1, min(512, 4_000_000 // max(count * 3, 1)))
    pairs: list[np.ndarray] = []
    for start in range(0, count, block):
        stop = min(start + block, count)
        delta = positions[start:stop, None, :] - positions[None, :, :]
        distances = np.linalg.norm(delta, axis=2)
        bonded = (distances > 0.4) & (distances <= limits[start:stop])
        local = np.argwhere(
            np.triu(bonded, k=start + 1)
        )
        if len(local):
            local[:, 0] += start
            pairs.append(local)
    if not pairs:
        return np.zeros((0, 2, 3), dtype=np.float32)
    return positions[np.concatenate(pairs)].astype(np.float32)


def atoms_to_pdb_text(atoms: Sequence[pdbio.Atom]) -> str:
    """把解析后的原子写成 PDB 文本，供 NGL 等只认 PDB 的查看器使用。

    PDBQT 的原子类型与电荷位于额外列，NGL 无法直接解析；这里保留原始
    ATOM/HETATM 记录、残基名与链，重新生成标准 PDB 行。
    """
    lines: list[str] = []
    for serial, atom in enumerate(atoms, start=1):
        line = pdbio.make_pdb_atom_line(
            atom, serial, atom.resname, atom.chain or "A", atom.resseq
        )
        record = atom.record if atom.record in {"ATOM", "HETATM"} else "HETATM"
        # 记录名固定占 6 列，不能用 4 字符的 "ATOM" 直接拼接，否则整行左移。
        lines.append(f"{record:<6}{line[6:]}")
    if not lines:
        return ""
    lines.append("END")
    return "\n".join(lines) + "\n"


def load_view_structure(path: str | Path) -> ViewStructure:
    """读取 PDB/PDBQT，生成卡通模型、原子点与配体线段。"""
    path = Path(path)
    fmt = pdbio.detect_format(path)
    pdb_text = ""
    if fmt == "pdbqt":
        atoms = pdbio.parse_pdbqt_atoms(path)
        pdb_text = atoms_to_pdb_text(atoms)
    elif fmt in {"pdb", "ent"}:
        atoms = pdbio.parse_atoms(path, first_model=True)
        try:
            pdb_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - 文件在解析后被删除
            pdb_text = atoms_to_pdb_text(atoms)
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
        hetero_bonds = _hetero_bonds(
            hetero_positions, [atom.element for atom in hetero]
        )
    else:
        hetero_positions = np.zeros((0, 3), dtype=np.float32)
        hetero_colors = np.zeros((0, 4), dtype=np.float32)
        hetero_bonds = np.zeros((0, 2, 3), dtype=np.float32)

    residues = collect_backbone_residues(polymer)
    assign_secondary_structure(residues)
    cartoon_vertices, cartoon_faces, cartoon_colors = cartoon_geometry(residues)

    low = positions.min(axis=0)
    high = positions.max(axis=0)
    center = (low + high) / 2.0
    radius = float(np.max(np.linalg.norm(positions - center, axis=1)))
    return ViewStructure(
        positions=positions,
        colors=colors,
        cartoon_vertices=cartoon_vertices,
        cartoon_faces=cartoon_faces,
        cartoon_colors=cartoon_colors,
        hetero_positions=hetero_positions,
        hetero_colors=hetero_colors,
        hetero_bonds=hetero_bonds,
        center=center,
        radius=max(radius, 5.0),
        atom_count=len(heavy),
        is_empty=False,
        pdb_text=pdb_text,
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
def _reset_gl_shader_cache() -> None:
    """清空 pyqtgraph 的全局着色器缓存。

    pyqtgraph 0.14 把编译好的着色器程序缓存在类属性（``GLLinePlotItem`` 等）
    和全局 ``ShaderProgram`` 对象上，不区分 OpenGL 上下文。``QOpenGLWidget``
    的上下文会随窗口销毁；再次打开盒子编辑器时，新上下文中的旧程序 ID 已失效，
    ``glUseProgram`` 会抛 ``GL_INVALID_VALUE``，严重时导致进程崩溃。每次新
    上下文初始化时清空缓存，让 pyqtgraph 在当前上下文中重新编译。
    """
    for name in ("GLLinePlotItem", "GLScatterPlotItem", "GLImageItem", "GLVolumeItem"):
        item_class = getattr(gl, name, None)
        if item_class is not None and hasattr(item_class, "_shaderProgram"):
            item_class._shaderProgram = None
    try:
        from pyqtgraph.opengl import shaders as gl_shaders
    except Exception:  # pragma: no cover - pyqtgraph 内部结构变化时的兜底
        return
    for program in gl_shaders.ShaderProgram.names.values():
        program.prog = None


_CARTOON_SHADER = "dockflowlab_cartoon"


def _install_cartoon_shader() -> None:
    """注册双面柔和光照着色器。

    pyqtgraph 自带的 ``shaded`` 只照亮朝向光源的一面，背面接近黑色；
    卡通模型由许多薄带组成，背面很常见，因此改用取绝对值的双面光照，
    既保留体积感，又不会出现大片死黑。
    """
    try:
        from pyqtgraph.opengl import shaders as gl_shaders
    except Exception:  # pragma: no cover - 缺少 OpenGL 时的降级路径
        return
    if _CARTOON_SHADER in gl_shaders.ShaderProgram.names:
        return
    gl_shaders.ShaderProgram(
        _CARTOON_SHADER,
        [
            gl_shaders.VertexShader(
                """
                uniform mat4 u_mvp;
                uniform mat3 u_normal;
                attribute vec4 a_position;
                attribute vec3 a_normal;
                attribute vec4 a_color;
                varying vec4 v_color;
                varying vec3 v_normal;
                void main() {
                    v_normal = normalize(u_normal * a_normal);
                    v_color = a_color;
                    gl_Position = u_mvp * a_position;
                }
                """
            ),
            gl_shaders.FragmentShader(
                """
                #ifdef GL_ES
                precision mediump float;
                #endif
                varying vec4 v_color;
                varying vec3 v_normal;
                void main() {
                    float p = abs(dot(v_normal, normalize(vec3(0.35, -0.45, 0.82))));
                    vec3 rgb = v_color.rgb * (0.58 + 0.42 * p);
                    gl_FragColor = vec4(rgb, v_color.a);
                }
                """
            ),
        ],
    )


if _HAS_GL:
    _install_cartoon_shader()
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

        def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
            super().__init__(parent=parent)
            self.setBackgroundColor(_BACKGROUND)
            self.opts["fov"] = 45
            self.setCameraPosition(distance=120, elevation=22, azimuth=32)
            if compact:
                self.setMinimumSize(360, 300)
            else:
                self.setMinimumSize(460, 380)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.setMouseTracking(True)

            self._structure = empty_structure()
            self._box = Box(center=(0.0, 0.0, 0.0), size=(22.0, 22.0, 22.0))
            self._click_to_center = True
            self._show_atoms = False
            self._drag: dict | None = None
            self._press_pos = None
            self._press_moved = False
            self._handle_positions = box_handle_positions(self._box.center, self._box.size)

            self._build_items()
            self._refresh_box_items()
            self.reset_view()

        def initializeGL(self) -> None:
            # 新窗口/新上下文：旧上下文中的着色器程序已失效，必须重新编译。
            _reset_gl_shader_cache()
            super().initializeGL()

        # -- items ------------------------------------------------------
        def _build_items(self) -> None:
            # 卡通模型：螺旋/折叠/loop 的三角网格，按二级结构着色。
            self._cartoon_item = gl.GLMeshItem(
                smooth=True,
                shader=_CARTOON_SHADER,
                computeNormals=True,
                drawEdges=False,
                drawFaces=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._cartoon_item.setDepthValue(0)
            self.addItem(self._cartoon_item)

            self._bond_item = gl.GLLinePlotItem(
                pos=np.zeros((0, 3), dtype=np.float32),
                color=(0.98, 0.72, 0.38, 1.0),
                width=2.2,
                mode="lines",
                antialias=True,
                glOptions=_OPAQUE_OPTIONS,
            )
            self._bond_item.setDepthValue(1)
            self.addItem(self._bond_item)

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
            self._cartoon_item.setMeshData(
                vertexes=structure.cartoon_vertices,
                faces=structure.cartoon_faces,
                vertexColors=structure.cartoon_colors,
            )
            has_cartoon = len(structure.cartoon_faces) > 0
            self._cartoon_item.setVisible(has_cartoon)
            self._bond_item.setData(
                pos=structure.hetero_bonds.reshape(-1, 3),
                color=(0.98, 0.72, 0.38, 1.0),
                width=2.2,
                mode="lines",
                antialias=True,
            )
            self._bond_item.setVisible(len(structure.hetero_bonds) > 0)
            self._atom_item.setData(
                pos=structure.positions,
                color=structure.colors,
                size=2.4,
                pxMode=True,
            )
            self._hetero_item.setData(
                pos=structure.hetero_positions,
                color=structure.hetero_colors,
                size=5.0,
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
    """3D 视图 + 数值输入。NGL 优先，失败时回退到 pyqtgraph。"""

    auto_requested = Signal()
    box_edited = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._box: Box | None = None
        self._dirty = False
        self._syncing = False
        self._structure: ViewStructure | None = None
        self._placeholder: QLabel | None = None
        self._placeholder_message = ""
        self._view_started = False
        self._sites_pdb: tuple[str, str] = ("", "")
        self._sites_visible = False
        self._show_atoms = False
        self._click_to_center = True
        self._focus_requested = False
        # NGL 页面就绪后仍要等结构解析完成；首帧渲染前继续显示占位页，
        # 避免用户看到一块全黑的画布。
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._show_view)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)

        # QtWebEngine 初始化约需半秒。必须在对话框显示前完成：如果等窗口
        # 可见后再创建 QWebEngineView，Qt 会销毁并重建这个顶层窗口，对话框
        # 会先消失再出现，主界面也会闪白。
        # 视图页初始是白底，所以先叠一张深色占位页，等 NGL 就绪后再切换。
        self.view = None
        self._view_stack = QStackedWidget()
        self._placeholder = self._make_loading_placeholder()
        self._view_stack.addWidget(self._placeholder)
        self._layout.addWidget(self._view_stack, 3)
        self._panel = self._build_panel()
        # 洞察模式会在上方增加参考信息卡片，右侧面板可能因此放不下。
        # 放进滚动区，保证文字保持可读高度，而不是被压扁或裁掉。
        self._panel_scroll = QScrollArea()
        self._panel_scroll.setObjectName("BoxPanelScroll")
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._panel_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._panel_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._panel_scroll.setMinimumWidth(270 if compact else 310)
        self._panel_scroll.setMaximumWidth(330 if compact else 376)
        self._panel_scroll.setWidget(self._panel)
        self._layout.addWidget(self._panel_scroll, 0)
        self.start_view()

    # -- 视图创建与降级 ---------------------------------------------------
    def _make_loading_placeholder(self) -> QLabel:
        placeholder = QLabel("正在加载 3D 视图…")
        placeholder.setObjectName("Muted")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setMinimumWidth(300 if self._compact else 420)
        placeholder.setStyleSheet(
            "background:#0b1016; color:#aab4c4; border:1px solid #1d2836;"
            "border-radius:8px; padding:24px;"
        )
        return placeholder

    def start_view(self) -> None:
        """创建 3D 视图并把已收到的结构、盒子与参考层应用上去。

        由 ``__init__`` 在对话框显示前调用；重复调用无副作用。
        """
        if self._view_started:
            return
        self._view_started = True
        view = self._create_ngl_view()
        if view is None and _HAS_GL:
            view = self._create_gl_view()
        if view is None:
            reason = _NGL_ERROR or _GL_ERROR or "缺少可用的 3D 渲染组件"
            self._replace_placeholder(self._make_placeholder(reason))
            return

        self.view = view
        self._view_stack.addWidget(view)
        if NglBoxView is not None and isinstance(view, NglBoxView):
            # 保留深色占位页，直到结构和首帧都准备好。
            view.ready.connect(self._on_ngl_ready)
            view.structure_ready.connect(self._on_structure_ready)
            self._view_stack.setCurrentWidget(self._placeholder)
        else:
            self._view_stack.setCurrentWidget(view)

        if self._structure is not None:
            view.set_structure(self._structure)
        elif self._placeholder_message:
            if NglBoxView is not None and isinstance(view, NglBoxView):
                view.show_placeholder(self._placeholder_message)
            else:
                view.set_structure(empty_structure())
        if self._box is not None:
            view.set_box(self._box, emit=False)
        if NglBoxView is not None and isinstance(view, NglBoxView):
            view.set_site_structures(*self._sites_pdb)
            view.set_sites_visible(self._sites_visible)
        view.set_show_atoms(self._show_atoms)
        view.set_click_to_center(self._click_to_center)
        if self._focus_requested or self._box is not None:
            view.focus_box()

    def _on_ngl_ready(self) -> None:
        """NGL 页面就绪：等首帧结构渲染完成后再揭开占位页。"""
        if self.view is None:
            return
        if self._structure is not None:
            # 结构已提交，等 structure_ready；大结构给足时间。
            self._show_timer.start(4000)
        elif self._placeholder_message:
            # 没有结构可显示，直接展示 NGL 的提示文字。
            self._show_view()
        else:
            # 结构可能还在解析，稍等一下；structure_ready 会提前揭开。
            self._show_timer.start(3000)

    def _on_structure_ready(self) -> None:
        # structureReady 在渲染请求发出时到达，Chromium 还要把第一帧合成到
        # 窗口上（首次渲染需编译着色器，约半秒）。先保留占位页，避免露出黑屏。
        self._show_timer.start(500)

    def _show_view(self) -> None:
        """用真正的 3D 视图替换占位页。"""
        if self.view is None:
            return
        if self._view_stack.currentWidget() is not self.view:
            self._view_stack.setCurrentWidget(self.view)
        if self._placeholder is not None:
            placeholder = self._placeholder
            self._placeholder = None
            self._view_stack.removeWidget(placeholder)
            placeholder.deleteLater()

    def _replace_placeholder(self, placeholder: QLabel) -> None:
        old = self._placeholder
        self._view_stack.addWidget(placeholder)
        self._view_stack.setCurrentWidget(placeholder)
        if old is not None:
            self._view_stack.removeWidget(old)
            old.deleteLater()
        self._placeholder = placeholder

    def _create_ngl_view(self):
        if not NGL_AVAILABLE or NglBoxView is None:
            return None
        try:
            view = NglBoxView(self, compact=self._compact)
        except Exception as exc:  # pragma: no cover - WebEngine 运行时异常
            log.warning("无法创建 NGL 视图：%s", exc)
            return None
        view.box_changed.connect(self._on_view_box_changed)
        view.failed.connect(self._on_ngl_failed)
        return view

    def _create_gl_view(self):
        if not _HAS_GL:
            return None
        try:
            view = BoxGLView(self, compact=self._compact)
        except Exception as exc:  # pragma: no cover - OpenGL 驱动异常
            log.warning("无法创建 OpenGL 视图：%s", exc)
            return None
        view.box_changed.connect(self._on_view_box_changed)
        return view

    def _make_placeholder(self, reason: str) -> QLabel:
        placeholder = QLabel(
            "3D 视图不可用（NGL / OpenGL 初始化失败）。\n"
            "仍可在右侧输入盒子中心与边长，保存与对接不受影响。\n\n"
            f"技术信息：{reason}"
        )
        placeholder.setObjectName("Muted")
        placeholder.setWordWrap(True)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setFrameShape(QFrame.Shape.StyledPanel)
        placeholder.setMinimumWidth(300 if self._compact else 420)
        placeholder.setStyleSheet(
            "background:#0b1016; color:#aab4c4; border:1px solid #1d2836;"
            "border-radius:8px; padding:24px;"
        )
        return placeholder

    def _on_ngl_failed(self, message: str) -> None:
        """NGL 初始化失败时换成 OpenGL 回退视图，保留当前结构与盒子。"""
        if NglBoxView is None or not isinstance(self.view, NglBoxView):
            return
        self._show_timer.stop()
        old = self.view
        self.view = None
        try:
            old.shutdown()
        except Exception:  # pragma: no cover - 关闭阶段信号已断开
            pass
        self._view_stack.removeWidget(old)
        old.setParent(None)
        old.deleteLater()

        fallback = self._create_gl_view()
        if fallback is not None:
            self.view = fallback
            self._view_stack.addWidget(fallback)
            self._view_stack.setCurrentWidget(fallback)
            if self._structure is not None:
                fallback.set_structure(self._structure)
            if self._box is not None:
                fallback.set_box(self._box, emit=False)
            fallback.set_show_atoms(self.atom_checkbox.isChecked())
            fallback.set_click_to_center(self.click_checkbox.isChecked())
            if self._box is not None:
                fallback.focus_box()
            return

        self._replace_placeholder(self._make_placeholder(message))

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
        title.setStyleSheet(f"font-size: {theme.px(16)}; font-weight: 700;")
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

        self.atom_checkbox = QCheckBox("显示原子点")
        self.atom_checkbox.setChecked(False)
        self.atom_checkbox.setToolTip("默认显示卡通模型；勾选后叠加所有重原子，便于检查细节。")
        self.atom_checkbox.toggled.connect(self._set_show_atoms)
        layout.addWidget(self.atom_checkbox)

        structure_legend = QLabel("结构：卡通骨架 · 彩色球棍表示配体、辅因子与离子")
        structure_legend.setObjectName("Muted")
        structure_legend.setWordWrap(True)
        layout.addWidget(structure_legend)

        legend = QLabel(
            '<span style="color:#ffffff">●</span> 中心　'
            '<span style="color:#ff5a5a">●</span> X　'
            '<span style="color:#43d46a">●</span> Y　'
            '<span style="color:#5f8dff">●</span> Z　'
            '<span style="color:#7a7a8a">（深色为负方向）</span>'
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setObjectName("Muted")
        layout.addWidget(legend)

        hint = QLabel(
            "左键拖动旋转 · 滚轮缩放 · 右键拖动平移\n"
            "拖动白色中心圆环移动盒子，拖动彩色面点调整边长。\n"
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
        self._structure = structure
        if self.view is not None:
            self.view.set_structure(structure)
        return True

    def set_site_structures(self, cocrystal_pdb: str = "", site_pdb: str = "") -> bool:
        """把实验位点作为独立参考层叠加到 3D 视图；仅 NGL 路径支持。"""
        self._sites_pdb = (cocrystal_pdb, site_pdb)
        if self.view is None:
            # 视图尚未创建：先缓存，start_view() 时再应用。
            return bool(NGL_AVAILABLE)
        if NglBoxView is None or not isinstance(self.view, NglBoxView):
            return False
        self.view.set_site_structures(cocrystal_pdb, site_pdb)
        return True

    def set_sites_visible(self, visible: bool) -> None:
        self._sites_visible = bool(visible)
        if NglBoxView is not None and isinstance(self.view, NglBoxView):
            self.view.set_sites_visible(visible)

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
        self._focus_requested = True
        if self.view is not None:
            self.view.focus_box()

    def reset_view(self) -> None:
        if self.view is not None:
            self.view.reset_view()

    def show_placeholder(self, message: str) -> None:
        self._placeholder_message = message
        if self._placeholder is not None:
            self._placeholder.setText(message)
        if self.view is not None:
            # NGL 尚未就绪时命令会排队，就绪后仍能显示这条提示。
            if NglBoxView is not None and isinstance(self.view, NglBoxView):
                self.view.show_placeholder(message)
            else:
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
        self._click_to_center = bool(enabled)
        if self.view is not None:
            self.view.set_click_to_center(enabled)

    def _set_show_atoms(self, visible: bool) -> None:
        self._show_atoms = bool(visible)
        if self.view is not None:
            self.view.set_show_atoms(visible)

    def _focus_box(self) -> None:
        if self.view is not None:
            self.view.focus_box()

    def _reset_view(self) -> None:
        if self.view is not None:
            self.view.reset_view()

    def sizeHint(self) -> QSize:
        return QSize(980, 620)
