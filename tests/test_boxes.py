"""对接盒子计算与校验。"""

from __future__ import annotations

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.errors import InputError
from dockflowlab.services import boxes

from .factories import make_protein_pdb


@pytest.fixture()
def settings():
    return AppSettings(box_min_size=18, box_max_size=60, box_padding=5.0)


def test_manual_box_validation():
    box = boxes.validate_manual_box((1, 2, 3), (20, 21, 22))
    assert box.center == (1.0, 2.0, 3.0)
    assert box.size == (20.0, 21.0, 22.0)
    assert box.source == "manual"
    with pytest.raises(InputError):
        boxes.validate_manual_box((0, 0), (20, 20, 20))
    with pytest.raises(InputError):
        boxes.validate_manual_box((0, 0, 0), (4, 20, 20))


def test_manual_box_is_clamped_and_ligand_expanded(settings, sdf_path):
    box, warnings = boxes.suggest_box(
        target_source=sdf_path,
        target_prepared_pdb=None,
        ligand_paths=[sdf_path],
        settings=settings,
        manual=boxes.validate_manual_box((10, 20, 30), (8, 100, 22)),
        target_kind="small_molecule",
    )
    assert box.source == "manual"
    assert min(box.size) >= 18
    assert max(box.size) <= 60
    assert any("限制" in warning for warning in warnings)


def test_site_box_from_real_structure(pdb_path, settings):
    box, warnings = boxes.suggest_box(
        target_source=pdb_path,
        target_prepared_pdb=None,
        settings=settings,
    )
    assert box.source == "site"
    assert "SITE" in box.note
    assert all(18 <= value <= 60 for value in box.size)
    assert isinstance(warnings, list)


def test_cocrystal_ligand_has_priority(tmp_path, settings):
    path = make_protein_pdb(tmp_path / "with_ligand.pdb", residues=25, ligand=True)
    box, warnings = boxes.suggest_box(
        target_source=path,
        target_prepared_pdb=None,
        settings=settings,
    )
    assert box.source == "cocrystal"
    assert "LIG" in box.note
    assert box.reference == "LIG"


def test_small_molecule_target_box(sdf_path, settings):
    box, warnings = boxes.suggest_box(
        target_source=sdf_path,
        target_prepared_pdb=None,
        ligand_paths=[sdf_path],
        settings=settings,
        target_kind="small_molecule",
    )
    assert box.source == "ligand"
    assert box.size[0] >= 18
    assert any("分子-分子" in warning for warning in warnings)


def test_structure_coords_sdf(sdf_path):
    coords = boxes.structure_coords(sdf_path)
    assert len(coords) > 10
    assert all(len(point) == 3 for point in coords)


def test_bad_target_raises(tmp_path, settings):
    empty = tmp_path / "empty.pdb"
    empty.write_text("END\n", encoding="utf-8")
    with pytest.raises(InputError):
        boxes.suggest_box(
            target_source=empty,
            target_prepared_pdb=None,
            settings=settings,
        )
