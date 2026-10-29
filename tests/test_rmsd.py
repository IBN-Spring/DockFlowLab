"""对称校正 RMSD 与收敛启发式测试。"""

from __future__ import annotations

import math

import pytest

from dockflowlab.core import rmsd
from dockflowlab.core.pdbio import Atom


def _atoms(coords, elements=None) -> list[Atom]:
    elements = elements or ["C"] * len(coords)
    return [
        Atom(
            record="HETATM",
            serial=index + 1,
            name=f"{elements[index]}{index + 1}",
            altloc="",
            resname="LIG",
            chain="L",
            resseq=1,
            icode="",
            x=float(x),
            y=float(y),
            z=float(z),
            element=elements[index],
        )
        for index, ((x, y, z), _) in enumerate(zip(coords, elements))
    ]


def _ring_coords():
    # 规则六边形，便于构造等价原子重排。
    return [
        (math.cos(math.pi / 3 * i) * 3.0, math.sin(math.pi / 3 * i) * 3.0, 0.0)
        for i in range(6)
    ]


def test_rmsd_is_zero_for_permuted_equivalent_atoms():
    coords = _ring_coords()
    reference = _atoms(coords)
    probe = _atoms([coords[i] for i in (0, 2, 4, 1, 3, 5)])

    result = rmsd.symmetry_corrected_rmsd(reference, probe)

    assert result.ok, result.message
    assert result.value == pytest.approx(0.0, abs=1e-4)
    assert result.matched_atoms == 6


def test_rmsd_aligns_rotation_and_translation():
    coords = _ring_coords()
    reference = _atoms(coords)
    # 绕 z 轴旋转 90°，再平移。
    probe_coords = [(-y + 10.0, x - 5.0, z + 2.0) for x, y, z in coords]
    probe = _atoms(probe_coords)

    result = rmsd.symmetry_corrected_rmsd(reference, probe)

    assert result.ok, result.message
    assert result.value == pytest.approx(0.0, abs=1e-4)


def test_rmsd_reports_mismatch_instead_of_raising():
    reference = _atoms([(0, 0, 0), (1, 0, 0), (2, 0, 0)])
    probe = _atoms([(0, 0, 0), (1, 0, 0)], elements=["C", "C"])

    result = rmsd.symmetry_corrected_rmsd(reference, probe)

    # 两个碳仍可计算一个较低 RMSD，但必须说明只匹配了部分原子。
    assert result.ok
    assert result.matched_atoms == 2
    assert "仅匹配" in result.message


def test_convergence_flags_dispersed_equal_energy_poses():
    # 用弯曲程度不同的链模拟“形状不同”的 pose；纯平移/旋转会被叠合抵消。
    poses = [
        _atoms([(index * 1.5, amplitude * math.sin(index), 0.0) for index in range(6)])
        for amplitude in (0.0, 1.5, 3.0, 4.5, 6.0)
    ]

    report = rmsd.convergence_report(poses, [-8.0, -7.8, -7.7, -7.5, -7.4])

    assert report.likely_unconverged
    assert report.max_pairwise_rmsd is not None
    assert report.max_pairwise_rmsd > 2.0
    assert "可能没有收敛" in report.message


def test_convergence_quiet_for_local_cluster():
    coords = _ring_coords()
    poses = [
        _atoms([(x + shift, y, z) for x, y, z in coords])
        for shift in (0.0, 0.1, 0.2, 0.3, 0.4)
    ]

    report = rmsd.convergence_report(poses, [-8.0, -7.9, -7.8, -7.7, -7.6])

    assert not report.likely_unconverged
    assert report.message == ""


def test_convergence_ignores_single_pose():
    report = rmsd.convergence_report([_atoms(_ring_coords())], [-8.0])
    assert not report.likely_unconverged
    assert report.max_pairwise_rmsd is None
