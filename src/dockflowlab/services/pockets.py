"""基于几何包埋的空腔（口袋）检测。

对接盒子最重要的信息是“配体应该放在哪里”。有实验结构时，共晶配体或
PDB SITE 记录是最直接的证据；没有实验信息时，需要从蛋白质表面寻找
足够深、足够封闭、能容纳小分子的空腔。

本模块使用网格化的 LIGSITE 思路：

1. 以 1 Å 网格表示蛋白质的范德华体积；
2. 用 1.4 Å 探针判断哪些空腔与外部溶剂连通（排除完全封闭的内部空腔）；
3. 沿 7 个方向（x/y/z 与 4 条体对角线）扫描“蛋白质-溶剂-蛋白质”
   （P-S-P）事件，统计每个空腔点的包埋方向数；
4. 对高包埋点做三维连通聚类；
5. 用包埋度、体积、紧密度、深度和疏水性排序。

算法只依赖 NumPy/SciPy，不需要额外的 fpocket/P2Rank 可执行文件，因而
可以随应用一起跨平台打包。检测结果会与共晶配体、PDB SITE 记录做一致性
校验：几何口袋与实验位点一致时使用几何口袋并扩大盒子以覆盖实验配体；
找不到可靠口袋时再回退到实验位点。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from ..core import pdbio
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import work_directory

log = get_logger("pockets")

# 常见重原子的范德华半径（Å）。缺失元素按碳处理。
VDW_RADII = {
    "H": 1.20,
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "S": 1.80,
    "P": 1.80,
    "F": 1.47,
    "CL": 1.75,
    "BR": 1.85,
    "I": 1.98,
    "SE": 1.90,
    "FE": 1.80,
    "ZN": 1.39,
    "MG": 1.73,
    "CA": 1.95,
    "MN": 1.75,
    "CU": 1.40,
}

PROBE_RADIUS = 1.4
SURFACE_LIMIT = 5.0
SCAN_RADIUS = 9.0
MIN_SCAN_DIRECTIONS = 4
MIN_POCKET_POINTS = 20
MAX_POCKET_SPAN = 35.0
MIN_POCKET_DENSITY = 0.02
MAX_GRID_CELLS = 3_000_000


@dataclass(frozen=True)
class Pocket:
    """一个候选结合口袋。坐标与尺寸单位均为 Å。"""

    center: tuple[float, float, float]
    size: tuple[float, float, float]
    score: float
    volume: float
    enclosure: float
    depth: float
    hydrophobicity: float
    points: int
    residues: tuple[str, ...] = ()
    coords: tuple[tuple[float, float, float], ...] = field(default=(), repr=False)

    @property
    def residue_text(self) -> str:
        if not self.residues:
            return "未识别"
        text = "、".join(self.residues[:6])
        return f"{text}…" if len(self.residues) > 6 else text


def detect_pockets(
    path: str | Path,
    *,
    chains: Sequence[str] = (),
    spacing: float = 1.0,
    max_pockets: int = 8,
    obabel: OpenBabelAdapter | None = None,
) -> list[Pocket]:
    """检测蛋白质结构中的候选口袋，按可信度降序返回。"""
    atoms = _load_protein_atoms(Path(path), chains, obabel)
    if len(atoms) < 30:
        return []
    return detect_pockets_from_atoms(atoms, spacing=spacing, max_pockets=max_pockets)


def detect_pockets_from_atoms(
    atoms: Sequence[pdbio.Atom],
    *,
    spacing: float = 1.0,
    max_pockets: int = 8,
) -> list[Pocket]:
    """从已解析的蛋白质原子检测口袋（便于测试与复用）。"""
    try:
        import numpy as np
        from scipy import ndimage
        from scipy.spatial import cKDTree
    except ImportError as exc:  # pragma: no cover - 依赖缺失时优雅降级
        log.warning("几何口袋检测不可用（缺少 NumPy/SciPy）：%s", exc)
        return []

    heavy = [atom for atom in atoms if not atom.is_hydrogen]
    if len(heavy) < 30:
        return []
    xyz = np.asarray([atom.coord for atom in heavy], dtype=float)
    radii = np.asarray(
        [VDW_RADII.get((atom.element or "").upper(), 1.70) for atom in heavy],
        dtype=float,
    )

    # 网格范围留出足够的扫描余量，保证边界不会影响 P-S-P 判断。
    low = xyz.min(axis=0) - (SCAN_RADIUS + 1.0)
    high = xyz.max(axis=0) + (SCAN_RADIUS + 1.0)
    spacing = _grid_spacing(low, high, float(spacing))
    dims = np.maximum(1, np.ceil((high - low) / spacing).astype(int))
    if int(np.prod(dims)) > MAX_GRID_CELLS * 1.2:
        return []

    axes = [
        low[i] + (np.arange(dims[i]) + 0.5) * spacing for i in range(3)
    ]
    mesh = np.meshgrid(*axes, indexing="ij")
    grid_points = np.column_stack([part.ravel() for part in mesh])
    tree = cKDTree(xyz)
    neighbours = min(4, len(xyz))
    distances, indices = tree.query(grid_points, k=neighbours, workers=-1)
    if neighbours == 1:
        distances = distances[:, None]
        indices = indices[:, None]
    # 网格中心到最近原子范德华表面的“净空”；<=0 表示被蛋白质占据。
    clearance = (distances - radii[indices]).min(axis=1)
    occupied = (clearance <= 0.0).reshape(dims)

    # 距离范德华表面的距离，用于探针可及性和口袋深度。
    surface_distance = ndimage.distance_transform_edt(
        ~occupied, sampling=spacing
    )

    # 只有与外部溶剂连通的空腔才可能被配体到达。
    probe = surface_distance >= PROBE_RADIUS
    probe_labels, _ = ndimage.label(probe, structure=np.ones((3, 3, 3), dtype=bool))
    boundary_labels = _boundary_labels(probe_labels)
    solvent = np.isin(probe_labels, np.fromiter(boundary_labels, dtype=int))

    scan_count = _psp_scan(occupied, spacing, ndimage, np)
    near_protein = ndimage.binary_dilation(
        occupied, iterations=max(1, int(round(SURFACE_LIMIT / spacing)))
    )
    candidates = (
        (scan_count >= MIN_SCAN_DIRECTIONS)
        & probe
        & solvent
        & near_protein
        & (surface_distance <= SURFACE_LIMIT)
    )
    labels, count = ndimage.label(candidates, structure=np.ones((3, 3, 3), dtype=bool))
    if count == 0:
        return []

    point_counts = np.bincount(labels.ravel())
    scan_sums = np.bincount(labels.ravel(), weights=scan_count.ravel())
    depth_sums = np.bincount(labels.ravel(), weights=surface_distance.ravel())
    objects = ndimage.find_objects(labels)

    pockets: list[Pocket] = []
    for label in range(1, count + 1):
        points = int(point_counts[label])
        if points < MIN_POCKET_POINTS:
            continue
        slices = objects[label - 1]
        if slices is None:
            continue
        spans = tuple(
            max(0.0, (sl.stop - sl.start) * spacing) for sl in slices
        )
        if max(spans) > MAX_POCKET_SPAN:
            continue
        bbox_cells = max(
            1,
            math.prod(max(1, sl.stop - sl.start) for sl in slices),
        )
        density = points / bbox_cells
        if density < MIN_POCKET_DENSITY:
            continue

        mean_scan = float(scan_sums[label] / points)
        mean_depth = float(depth_sums[label] / points)
        center = tuple(
            float(low[i] + (slices[i].start + slices[i].stop) / 2.0 * spacing)
            for i in range(3)
        )

        local = np.argwhere(labels[slices] == label)
        offset = np.asarray([sl.start for sl in slices])
        local = local + offset
        world = low + (local + 0.5) * spacing
        sampled = _sample_rows(world, 1200)
        nearest = tree.query(sampled, k=1)[1]
        elements = [(heavy[int(index)].element or "C").upper() for index in nearest]
        hydrophobic = sum(1 for element in elements if element in {"C", "S"}) / max(1, len(elements))
        residues = tuple(
            residue
            for residue, _ in Counter(
                heavy[int(index)].residue_label for index in nearest
            ).most_common(8)
        )

        enclosure = mean_scan / 7.0
        size_score = min(1.0, math.log1p(points / 50.0) / math.log1p(10.0))
        density_score = min(1.0, density / 0.15)
        depth_score = min(1.0, mean_depth / 2.5)
        score = (
            0.45 * enclosure
            + 0.20 * size_score
            + 0.15 * density_score
            + 0.10 * depth_score
            + 0.10 * hydrophobic
        )

        pockets.append(
            Pocket(
                center=center,  # type: ignore[arg-type]
                size=tuple(round(value, 2) for value in spans),  # type: ignore[arg-type]
                score=round(float(score), 4),
                volume=round(float(points * spacing**3), 1),
                enclosure=round(float(enclosure), 3),
                depth=round(mean_depth, 2),
                hydrophobicity=round(float(hydrophobic), 3),
                points=points,
                residues=residues,
                coords=tuple(
                    (float(x), float(y), float(z)) for x, y, z in sampled
                ),
            )
        )

    pockets.sort(key=lambda pocket: (pocket.score, pocket.points), reverse=True)
    return pockets[: max(0, max_pockets)]


def detect_pockets_cached(
    path: str | Path,
    *,
    chains: Sequence[str] = (),
    obabel: OpenBabelAdapter | None = None,
) -> list[Pocket]:
    """带文件时间戳缓存的版本，避免 UI 重复计算。"""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return []
    return list(
        _detect_cached(
            str(path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            tuple(str(chain) for chain in chains),
        )
    )


@lru_cache(maxsize=8)
def _detect_cached(
    path: str,
    mtime_ns: int,
    size: int,
    chains: tuple[str, ...],
) -> tuple[Pocket, ...]:
    del mtime_ns, size  # 仅用于缓存键
    return tuple(detect_pockets(path, chains=chains))


def _grid_spacing(low, high, requested: float) -> float:
    requested = max(0.75, requested)
    volume = float(
        max(1.0, (high[0] - low[0]) * (high[1] - low[1]) * (high[2] - low[2]))
    )
    if volume / requested**3 <= MAX_GRID_CELLS:
        return requested
    return max(requested, (volume / MAX_GRID_CELLS) ** (1.0 / 3.0))


def _boundary_labels(labels) -> set[int]:
    import numpy as np

    return {
        int(value)
        for value in np.unique(
            np.concatenate(
                [
                    labels[0].ravel(),
                    labels[-1].ravel(),
                    labels[:, 0].ravel(),
                    labels[:, -1].ravel(),
                    labels[:, :, 0].ravel(),
                    labels[:, :, -1].ravel(),
                ]
            )
        )
        if int(value) > 0
    }


def _psp_scan(occupied, spacing: float, ndimage, np):
    """统计每个网格点沿 7 个方向同时看到蛋白质的次数。"""
    directions = (
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 1, 1),
        (1, 1, -1),
        (1, -1, 1),
        (1, -1, -1),
    )
    steps = max(1, int(round(SCAN_RADIUS / spacing)))
    result = np.zeros(occupied.shape, dtype=np.uint8)
    for direction in directions:
        ahead = np.zeros(occupied.shape, dtype=bool)
        behind = np.zeros(occupied.shape, dtype=bool)
        for step in range(1, steps + 1):
            shift = tuple(int(round(value * step)) for value in direction)
            ahead |= np.roll(occupied, shift=shift, axis=(0, 1, 2))
            behind |= np.roll(
                occupied,
                shift=tuple(-value for value in shift),
                axis=(0, 1, 2),
            )
        result += (ahead & behind).astype(np.uint8)
    return result


def _sample_rows(rows, limit: int):
    import numpy as np

    if len(rows) <= limit:
        return np.asarray(rows, dtype=float)
    step = max(1, len(rows) // limit)
    return np.asarray(rows[::step][:limit], dtype=float)


def _load_protein_atoms(
    path: Path,
    chains: Sequence[str],
    obabel: OpenBabelAdapter | None,
) -> list[pdbio.Atom]:
    if not path.is_file():
        return []
    fmt = pdbio.detect_format(path)
    if fmt == "cif":
        adapter = obabel or get_openbabel()
        if not adapter.available:
            return []
        with work_directory("pocket_cif") as temp:
            converted = temp / "pocket.pdb"
            if not adapter.convert(path, converted, out_format="pdb")[0]:
                return []
            atoms = pdbio.parse_atoms(converted, first_model=True)
    elif fmt == "pdbqt":
        atoms = pdbio.parse_pdbqt_atoms(path)
    else:
        atoms = pdbio.parse_atoms(path, first_model=True)

    allowed = {str(chain).strip() for chain in chains if str(chain).strip()}
    protein = [
        atom
        for atom in atoms
        if atom.resname in pdbio.AMINO_ACIDS
        and not atom.is_hydrogen
        and (not allowed or atom.chain in allowed)
    ]
    return protein
