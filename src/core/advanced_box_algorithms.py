#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级对接盒子算法模块
提供比传统几何中心更准确的盒子生成算法
"""
import os
import numpy as np
from typing import Tuple, List, Dict, Optional
from pathlib import Path

from .logger import log


def detect_binding_pocket_fpocket(receptor_pdb: str, log_text=None) -> Optional[Tuple[np.ndarray, float]]:
    """使用 fpocket 检测结合口袋（需要安装 fpocket）

    fpocket 是基于 Alpha Sphere 理论的几何口袋检测工具，准确度高于简单几何中心

    Args:
        receptor_pdb: 受体PDB文件路径
        log_text: 日志输出

    Returns:
        (center, radius) 或 None（如果fpocket不可用或失败）
    """
    try:
        import subprocess
        import tempfile
        import shutil

        # 检查 fpocket 是否安装
        check = subprocess.run(["fpocket", "-h"], capture_output=True, timeout=5)
        if check.returncode != 0:
            log("fpocket 未安装，跳过口袋检测", "INFO", log_text)
            return None

        # 创建临时目录运行 fpocket
        with tempfile.TemporaryDirectory() as tmpdir:
            # 复制PDB到临时目录
            tmp_pdb = os.path.join(tmpdir, "protein.pdb")
            shutil.copy2(receptor_pdb, tmp_pdb)

            # 运行 fpocket
            result = subprocess.run(
                ["fpocket", "-f", tmp_pdb],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                log(f"fpocket 执行失败: {result.stderr[:200]}", "WARNING", log_text)
                return None

            # 解析结果文件
            pocket_file = os.path.join(tmpdir, "protein_out", "protein_pocket.pdb")
            if not os.path.exists(pocket_file):
                log("fpocket 未生成口袋文件", "WARNING", log_text)
                return None

            # 读取第一个口袋的原子坐标
            coords = []
            with open(pocket_file, 'r') as f:
                for line in f:
                    if line.startswith("ATOM") or line.startswith("HETATM"):
                        try:
                            x = float(line[30:38])
                            y = float(line[38:46])
                            z = float(line[46:54])
                            coords.append([x, y, z])
                        except ValueError:
                            continue

            if len(coords) < 10:
                log(f"fpocket 检测到的口袋过小({len(coords)}个原子)", "WARNING", log_text)
                return None

            coords = np.array(coords)
            center = np.mean(coords, axis=0)
            radius = np.max(np.linalg.norm(coords - center, axis=1))

            log(f"✅ fpocket 检测到结合口袋: 中心{center}, 半径{radius:.2f}Å", log_text=log_text)
            return center, radius

    except FileNotFoundError:
        log("fpocket 未安装（可选功能）", "INFO", log_text)
        return None
    except Exception as e:
        log(f"fpocket 口袋检测异常: {str(e)}", "WARNING", log_text)
        return None


def cluster_active_residues_dbscan(residues: List[Dict], eps: float = 8.0,
                                   min_samples: int = 3, log_text=None) -> List[List[Dict]]:
    """使用 DBSCAN 聚类算法将活性残基分组为不同的结合位点

    适用场景：蛋白有多个潜在结合位点时，自动识别主要口袋

    Args:
        residues: 活性残基列表 [{"x", "y", "z", "id"}, ...]
        eps: DBSCAN 邻域半径（Å），默认8Å
        min_samples: 最小簇大小
        log_text: 日志输出

    Returns:
        List[List[Dict]]: 残基簇列表，按大小排序（最大簇在前）
    """
    if len(residues) < min_samples:
        log(f"活性残基数({len(residues)})不足以聚类", "WARNING", log_text)
        return [residues]

    try:
        from sklearn.cluster import DBSCAN

        # 提取坐标
        coords = np.array([[r["x"], r["y"], r["z"]] for r in residues])

        # DBSCAN 聚类
        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric='euclidean')
        labels = clustering.fit_predict(coords)

        # 按簇分组
        clusters = {}
        for i, label in enumerate(labels):
            if label == -1:  # 噪声点
                continue
            if label not in clusters:
                clusters[label] = []
            clusters[label].append(residues[i])

        if not clusters:
            log("DBSCAN 未检测到明显簇，使用全部残基", "INFO", log_text)
            return [residues]

        # 按簇大小排序
        sorted_clusters = sorted(clusters.values(), key=len, reverse=True)

        log(f"DBSCAN 检测到 {len(sorted_clusters)} 个结合位点:", log_text=log_text)
        for i, cluster in enumerate(sorted_clusters, 1):
            log(f"  位点 {i}: {len(cluster)} 个残基", log_text=log_text)

        return sorted_clusters

    except ImportError:
        log("sklearn 未安装，跳过聚类（可选功能）", "INFO", log_text)
        return [residues]
    except Exception as e:
        log(f"DBSCAN 聚类异常: {str(e)}", "WARNING", log_text)
        return [residues]


def calculate_convex_hull_box(residues: List[Dict], padding: float = 8.0,
                               log_text=None) -> Tuple[np.ndarray, np.ndarray]:
    """基于凸包（Convex Hull）计算最优盒子

    比简单边界框更紧凑，减少不必要的搜索空间

    Args:
        residues: 活性残基列表
        padding: 边界扩展值（Å）
        log_text: 日志输出

    Returns:
        (center, size): 盒子中心和尺寸
    """
    coords = np.array([[r["x"], r["y"], r["z"]] for r in residues])

    try:
        from scipy.spatial import ConvexHull

        # 计算凸包
        hull = ConvexHull(coords)
        hull_points = coords[hull.vertices]

        # 凸包的最小边界框
        min_coords = np.min(hull_points, axis=0)
        max_coords = np.max(hull_points, axis=0)

        center = (min_coords + max_coords) / 2
        size = (max_coords - min_coords) + padding

        # 凸包体积 vs 边界框体积
        hull_volume = hull.volume
        box_volume = np.prod(size)
        efficiency = (hull_volume / box_volume) * 100

        log(f"凸包盒子: 中心{center}, 尺寸{size}", log_text=log_text)
        log(f"  空间效率: {efficiency:.1f}% (凸包{hull_volume:.1f}Å³ / 盒子{box_volume:.1f}Å³)", log_text=log_text)

        return center, size

    except ImportError:
        log("scipy 未安装，回退到简单边界框", "INFO", log_text)
        # 回退到简单边界框
        min_coords = np.min(coords, axis=0)
        max_coords = np.max(coords, axis=0)
        center = (min_coords + max_coords) / 2
        size = (max_coords - min_coords) + padding
        return center, size
    except Exception as e:
        log(f"凸包计算异常: {str(e)}, 回退到简单边界框", "WARNING", log_text)
        min_coords = np.min(coords, axis=0)
        max_coords = np.max(coords, axis=0)
        center = (min_coords + max_coords) / 2
        size = (max_coords - min_coords) + padding
        return center, size


def optimize_box_with_ligand_library(receptor_pdbqt: str,
                                      ligand_library: List[str],
                                      initial_center: np.ndarray,
                                      initial_size: np.ndarray,
                                      log_text=None) -> Tuple[np.ndarray, np.ndarray]:
    """基于配体库的尺寸分布优化盒子大小

    策略：分析配体库中分子的平均尺寸，确保盒子足够容纳95%的配体

    Args:
        receptor_pdbqt: 受体文件路径
        ligand_library: 配体PDBQT文件路径列表
        initial_center: 初始中心坐标
        initial_size: 初始盒子尺寸
        log_text: 日志输出

    Returns:
        (optimized_center, optimized_size): 优化后的中心和尺寸
    """
    if not ligand_library:
        log("配体库为空，跳过盒子优化", "INFO", log_text)
        return initial_center, initial_size

    ligand_sizes = []

    for lig_path in ligand_library[:50]:  # 最多分析50个配体
        try:
            coords = []
            with open(lig_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.startswith(("ATOM", "HETATM")):
                        try:
                            x = float(line[30:38])
                            y = float(line[38:46])
                            z = float(line[46:54])
                            coords.append([x, y, z])
                        except ValueError:
                            continue

            if len(coords) > 3:
                coords = np.array(coords)
                span = np.ptp(coords, axis=0)  # 每个维度的跨度
                ligand_sizes.append(span)

        except Exception:
            continue

    if not ligand_sizes:
        log("无法分析配体尺寸，保持原盒子", "WARNING", log_text)
        return initial_center, initial_size

    ligand_sizes = np.array(ligand_sizes)

    # 计算配体尺寸的95百分位
    percentile_95 = np.percentile(ligand_sizes, 95, axis=0)
    mean_size = np.mean(ligand_sizes, axis=0)

    log(f"配体库分析({len(ligand_sizes)}个分子):", log_text=log_text)
    log(f"  平均尺寸: {mean_size}", log_text=log_text)
    log(f"  95%分位: {percentile_95}", log_text=log_text)

    # 优化策略：确保盒子至少能容纳95%配体 + 旋转空间（×1.5）
    min_required_size = percentile_95 * 1.5
    optimized_size = np.maximum(initial_size, min_required_size)

    # 限制最大尺寸
    optimized_size = np.minimum(optimized_size, 100.0)

    if np.any(optimized_size != initial_size):
        log(f"盒子尺寸已优化: {initial_size} → {optimized_size}", log_text=log_text)
    else:
        log("盒子尺寸无需优化", log_text=log_text)

    return initial_center, optimized_size


def adaptive_box_for_flexible_docking(residues: List[Dict],
                                       flexibility_factor: float = 1.3,
                                       log_text=None) -> Tuple[np.ndarray, np.ndarray]:
    """柔性对接的自适应盒子（考虑侧链柔性）

    柔性对接需要更大的盒子以容纳侧链运动

    Args:
        residues: 活性残基列表
        flexibility_factor: 柔性系数（推荐1.2-1.5）
        log_text: 日志输出

    Returns:
        (center, size): 盒子中心和尺寸
    """
    coords = np.array([[r["x"], r["y"], r["z"]] for r in residues])

    # 基础盒子
    min_coords = np.min(coords, axis=0)
    max_coords = np.max(coords, axis=0)
    center = (min_coords + max_coords) / 2
    base_size = max_coords - min_coords

    # 考虑柔性的扩展
    flexible_size = base_size * flexibility_factor + 10.0  # 额外10Å容纳侧链

    log(f"柔性对接盒子（扩展系数{flexibility_factor}）:", log_text=log_text)
    log(f"  刚性尺寸: {base_size}", log_text=log_text)
    log(f"  柔性尺寸: {flexible_size}", log_text=log_text)

    return center, flexible_size


# ===== 使用示例 =====
def generate_advanced_box(receptor_pdbqt: str,
                          active_residues: List[Dict],
                          ligand_library: Optional[List[str]] = None,
                          use_fpocket: bool = True,
                          use_clustering: bool = True,
                          use_convex_hull: bool = True,
                          flexible_docking: bool = False,
                          log_text=None) -> Dict:
    """综合使用高级算法生成对接盒子

    Args:
        receptor_pdbqt: 受体PDBQT文件路径
        active_residues: 活性残基列表
        ligand_library: 配体库路径列表（可选）
        use_fpocket: 是否尝试fpocket口袋检测
        use_clustering: 是否使用DBSCAN聚类
        use_convex_hull: 是否使用凸包算法
        flexible_docking: 是否为柔性对接
        log_text: 日志输出

    Returns:
        Dict: {"center": (x,y,z), "size": (sx,sy,sz), "method": str}
    """
    log("=== 高级盒子生成算法 ===", log_text=log_text)

    # 方案1: fpocket 口袋检测（最准确）
    if use_fpocket:
        pocket_result = detect_binding_pocket_fpocket(receptor_pdbqt, log_text)
        if pocket_result is not None:
            center, radius = pocket_result
            size = np.array([radius*2, radius*2, radius*2]) + 8.0
            return {
                "center": tuple(center),
                "size": tuple(size),
                "method": "fpocket"
            }

    # 方案2: DBSCAN 聚类 + 凸包（次优）
    if use_clustering and len(active_residues) >= 5:
        clusters = cluster_active_residues_dbscan(active_residues, log_text=log_text)
        main_cluster = clusters[0]  # 使用最大簇
        log(f"使用主要结合位点({len(main_cluster)}个残基)", log_text=log_text)
    else:
        main_cluster = active_residues

    if use_convex_hull:
        center, size = calculate_convex_hull_box(main_cluster, log_text=log_text)
        method = "convex_hull"
    else:
        coords = np.array([[r["x"], r["y"], r["z"]] for r in main_cluster])
        center = np.mean(coords, axis=0)
        size = np.ptp(coords, axis=0) + 8.0
        method = "bounding_box"

    # 柔性对接调整
    if flexible_docking:
        center, size = adaptive_box_for_flexible_docking(main_cluster, log_text=log_text)
        method += "_flexible"

    # 配体库优化
    if ligand_library:
        center, size = optimize_box_with_ligand_library(
            receptor_pdbqt, ligand_library, center, size, log_text
        )
        method += "_optimized"

    return {
        "center": tuple(center),
        "size": tuple(size),
        "method": method
    }
