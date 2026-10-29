"""构象浏览与收敛提示测试。"""

from __future__ import annotations

import pytest

from dockflowlab.services.pose_browser import build_pose_browser

RECEPTOR_PDB = (
    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
    "TER\nEND\n"
)


def _atom(serial: int, x: float, y: float, z: float) -> str:
    return (
        f"ATOM  {serial:5d}  C{serial}  LIG L   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00     0.000 C"
    )


def _pose(affinity: float, offset: float, *, atoms: int = 3) -> list[str]:
    lines = [f"REMARK VINA RESULT:    {affinity:.3f}      0.000      0.000"]
    for index in range(atoms):
        lines.append(
            _atom(index + 1, offset + index * 1.4, 0.0 + index * 1.2, 0.0)
        )
    lines.append("ENDMDL")
    return lines


def _write_output(workspace, target_stem: str, ligand_stem: str, *poses) -> str:
    path = workspace.docking_output(target_stem, ligand_stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, pose in enumerate(poses, start=1):
        lines.append(f"MODEL {index}")
        lines.extend(pose)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _write_receptor(workspace, target_stem: str) -> None:
    path = workspace.prepared_target_pdb(target_stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RECEPTOR_PDB, encoding="utf-8")


def test_build_options_and_pose_complexes(workspace):
    _write_receptor(workspace, "t1")
    _write_output(workspace, "t1", "l1", _pose(-8.0, 0.0), _pose(-7.5, 12.0))

    data = build_pose_browser(workspace, "t1", "l1")

    assert [option.rank for option in data.options] == [1, 2]
    assert data.options[0].affinity == pytest.approx(-8.0)
    assert data.options[0].label == "构象 1 · -8.00 kcal/mol"
    assert all(option.complex_path and option.complex_path.is_file() for option in data.options)
    assert workspace.complex_path("t1", "l1").is_file()
    assert workspace.pose_complex_path("t1", "l1", 1).is_file()
    # 第二个复合物来自第二个 MODEL，配体坐标已经平移。
    assert "  12.000" in workspace.pose_complex_path("t1", "l1", 1).read_text(
        encoding="utf-8"
    )


def test_convergence_message_when_poses_dispersed(workspace):
    _write_receptor(workspace, "t1")
    _write_output(
        workspace, "t1", "l1", _pose(-8.0, 0.0), _pose(-7.9, 12.0), _pose(-7.8, -15.0)
    )

    data = build_pose_browser(workspace, "t1", "l1")

    assert data.convergence is not None
    assert data.convergence.likely_unconverged
    assert "搜索可能没有收敛" in data.message


def test_no_message_when_poses_agree(workspace):
    _write_receptor(workspace, "t1")
    _write_output(workspace, "t1", "l1", _pose(-8.0, 0.0), _pose(-7.6, 0.1))

    data = build_pose_browser(workspace, "t1", "l1")

    assert data.convergence is not None
    assert not data.convergence.likely_unconverged
    assert data.message == ""


def test_missing_output_returns_empty(workspace):
    data = build_pose_browser(workspace, "t1", "l1")

    assert data.options == []
    assert data.convergence is None
    assert "不存在" in data.message


def test_single_pose_has_no_convergence_and_no_complex_without_receptor(workspace):
    _write_output(workspace, "t1", "l1", _pose(-7.0, 0.0))

    data = build_pose_browser(workspace, "t1", "l1")

    assert len(data.options) == 1
    assert data.options[0].complex_path is None
    assert data.convergence is None
    assert data.message == ""


def test_pose_complex_cache_is_reused(workspace):
    _write_receptor(workspace, "t1")
    _write_output(workspace, "t1", "l1", _pose(-8.0, 0.0), _pose(-7.5, 12.0))
    first = build_pose_browser(workspace, "t1", "l1")
    path = first.options[1].complex_path
    stamp = path.stat().st_mtime

    second = build_pose_browser(workspace, "t1", "l1")

    assert second.options[1].complex_path == path
    assert second.options[1].complex_path.stat().st_mtime == stamp
