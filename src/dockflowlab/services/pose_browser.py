"""构象浏览与收敛提示。

细节视图默认只显示最佳 pose。这里按需把其他 MODEL 也生成复合物，
并读取前几个 pose 的重原子坐标判断搜索是否可能未收敛。复合物会缓存在
``complexes/<靶点>/<配体>_poseN.pdb``；对接结果更新后按 mtime 重建。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..core import pdbio, rmsd
from ..core.models import Pose
from ..logging_setup import get_logger
from ..workspace import Workspace

log = get_logger("pose_browser")


@dataclass(frozen=True)
class PoseOption:
    index: int  # 从 0 开始，对应 PDBQT 的 MODEL 顺序
    rank: int  # 从 1 开始，供界面显示
    affinity: float | None
    rmsd_lb: float = 0.0
    rmsd_ub: float = 0.0
    complex_path: Path | None = None

    @property
    def label(self) -> str:
        score = f"{self.affinity:.2f} kcal/mol" if self.affinity is not None else "无分数"
        return f"构象 {self.rank} · {score}"


@dataclass(frozen=True)
class PoseBrowserData:
    options: list[PoseOption]
    convergence: rmsd.ConvergenceReport | None
    message: str = ""


def _pose_complex(
    workspace: Workspace,
    target_stem: str,
    ligand_stem: str,
    receptor_pdb: Path,
    output_pdbqt: Path,
    index: int,
) -> Path | None:
    if index == 0:
        standard = workspace.complex_path(target_stem, ligand_stem)
        if standard.is_file() and standard.stat().st_mtime >= output_pdbqt.stat().st_mtime:
            return standard
        if standard.is_file() and standard.stat().st_mtime < output_pdbqt.stat().st_mtime:
            # 旧复合物基于旧结果，重新生成到标准路径，保持细节视图一致。
            pass
        path = standard
    else:
        path = workspace.pose_complex_path(target_stem, ligand_stem, index)
        if (
            path.is_file()
            and path.stat().st_mtime >= output_pdbqt.stat().st_mtime
        ):
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pdbio.make_complex(receptor_pdb, output_pdbqt, path, pose_index=index)
    except Exception as exc:
        log.warning("生成 pose %s 复合物失败：%s", index + 1, exc)
        return None
    return path


def build_pose_browser(
    workspace: Workspace,
    target_stem: str,
    ligand_stem: str,
    *,
    poses: Sequence[Pose] | None = None,
    top_n: int = 5,
    with_convergence: bool = True,
) -> PoseBrowserData:
    """返回可切换的 pose 列表与收敛判断。"""
    output = workspace.docking_output(target_stem, ligand_stem)
    if not output.is_file():
        return PoseBrowserData([], None, "对接结果文件不存在")
    poses = list(poses) if poses is not None else pdbio.parse_pdbqt_poses(output)
    count = pdbio.count_poses(output)
    if count <= 0:
        return PoseBrowserData([], None, "对接结果中没有构象")
    if not poses:
        poses = [Pose(rank=index + 1, affinity=0.0) for index in range(count)]

    receptor_pdb = workspace.prepared_target_pdb(target_stem)
    options: list[PoseOption] = []
    for index in range(min(count, len(poses))):
        pose = poses[index]
        path = None
        if receptor_pdb.is_file():
            path = _pose_complex(
                workspace, target_stem, ligand_stem, receptor_pdb, output, index
            )
        options.append(
            PoseOption(
                index=index,
                rank=index + 1,
                affinity=pose.affinity,
                rmsd_lb=pose.rmsd_lb,
                rmsd_ub=pose.rmsd_ub,
                complex_path=path,
            )
        )

    convergence = None
    message = ""
    if with_convergence and len(options) > 1:
        atom_sets = [
            pdbio.parse_pose_atoms(output, option.index)
            for option in options[: max(2, min(top_n, len(options)))]
        ]
        convergence = rmsd.convergence_report(
            atom_sets,
            [option.affinity or 0.0 for option in options[: len(atom_sets)]],
        )
        message = convergence.message
    return PoseBrowserData(options, convergence, message)
