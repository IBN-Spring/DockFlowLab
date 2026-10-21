"""几何口袋检测与自动盒子决策。"""

from __future__ import annotations

import math

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.core import pdbio
from dockflowlab.core.models import Box
from dockflowlab.services import boxes, pockets

from .factories import make_cage_pdb, make_peptide_ligand_pdb, make_protein_pdb


@pytest.fixture()
def settings():
    return AppSettings(box_min_size=18, box_max_size=60, box_padding=5.0)


@pytest.mark.parametrize("ligand", [False, True])
def test_detect_pocket_in_synthetic_cage(tmp_path, ligand):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_cage_pdb(tmp_path / "cage.pdb", ligand=ligand)
    found = pockets.detect_pockets(path)

    assert found
    best = found[0]
    assert math.dist(best.center, (0.0, 0.0, 0.0)) < 3.0
    assert best.enclosure >= 0.5
    assert best.score >= 0.5
    assert best.volume > 500


def test_box_uses_geometry_pocket(tmp_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_cage_pdb(tmp_path / "cage.pdb")
    box, warnings = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "pocket"
    assert "几何口袋" in box.note
    assert math.dist(box.center, (0.0, 0.0, 0.0)) < 3.0
    assert all(18 <= value <= 60 for value in box.size)
    assert isinstance(warnings, list)


def test_geometry_consensus_with_cocrystal(tmp_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_cage_pdb(tmp_path / "cage_ligand.pdb", ligand=True)
    box, _ = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "pocket"
    assert box.reference == "LIG"
    assert "一致" in box.note
    # 盒子必须覆盖共晶配体，避免联合盒子漏掉实验构象。
    low = [box.center[i] - box.size[i] / 2.0 for i in range(3)]
    high = [box.center[i] + box.size[i] / 2.0 for i in range(3)]
    for x, y, z in (
        (2.30, 0.0, 0.0),
        (-2.30, 0.0, 0.0),
        (0.0, 1.21, 0.0),
        (0.0, -1.21, 0.0),
    ):
        assert low[0] <= x <= high[0]
        assert low[1] <= y <= high[1]
        assert low[2] <= z <= high[2]


def test_flat_protein_has_no_pocket_and_uses_blind(tmp_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_protein_pdb(tmp_path / "flat.pdb", residues=25)

    assert pockets.detect_pockets(path) == []
    box, warnings = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )
    assert box.source == "blind"
    assert any("盲对接" in warning for warning in warnings)


def test_tiny_pocket_is_ignored_without_evidence(tmp_path, settings, monkeypatch):
    path = make_protein_pdb(tmp_path / "flat.pdb", residues=25)
    tiny = pockets.Pocket(
        center=(56.0, 26.0, 31.0),
        size=(5.0, 5.0, 5.0),
        score=0.9,
        volume=50.0,
        enclosure=0.9,
        depth=2.0,
        hydrophobicity=1.0,
        points=50,
    )
    monkeypatch.setattr(
        boxes, "detect_pockets_cached", lambda *args, **kwargs: [tiny]
    )

    box, warnings = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "blind"
    assert any("体积过小" in warning for warning in warnings)


def test_cocrystal_fallback_when_no_pocket(tmp_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_protein_pdb(tmp_path / "flat_ligand.pdb", residues=25, ligand=True)
    box, _ = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "cocrystal"
    assert box.reference == "LIG"


def test_site_fallback_when_no_pocket(tmp_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    path = make_protein_pdb(tmp_path / "flat_site.pdb", residues=25, site=True)
    box, _ = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "site"
    assert "SITE" in box.note


def test_geometry_consensus_with_site(pdb_path, settings):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    box, _ = boxes.suggest_box(
        target_source=pdb_path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source == "pocket"
    assert "SITE" in box.note


def test_box_uses_full_peptide_ligand_chain(tmp_path, settings):
    """盒子必须覆盖整条肽类配体，而不是只覆盖其中某个 HETATM 片段。"""
    path = make_peptide_ligand_pdb(tmp_path / "peptide_complex.pdb")
    site = next(site for site in pdbio.find_ligand_sites(path) if site.chain == "C")

    box, _ = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )

    assert box.source in {"cocrystal", "pocket"}
    assert "链 C" in box.note
    low = [box.center[i] - box.size[i] / 2.0 for i in range(3)]
    high = [box.center[i] + box.size[i] / 2.0 for i in range(3)]
    for atom in site.atoms:
        if atom.is_hydrogen:
            continue
        assert low[0] <= atom.x <= high[0]
        assert low[1] <= atom.y <= high[1]
        assert low[2] <= atom.z <= high[2]


def test_pocket_source_label():
    box = Box(center=(0, 0, 0), size=(20, 20, 20), source="pocket")
    assert box.source_label == "几何口袋"
