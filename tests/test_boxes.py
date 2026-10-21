"""对接盒子计算与校验。"""

from __future__ import annotations

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.core.models import Box
from dockflowlab.errors import InputError
from dockflowlab.services import boxes

from .factories import make_protein_pdb


@pytest.fixture()
def settings():
    return AppSettings(box_min_size=18, box_max_size=60, box_padding=5.0)


def test_auto_box_review_flags():
    """盲对接与小分子靶点估算的盒子需要用户手动确认。"""
    size = (20.0, 20.0, 20.0)
    center = (0.0, 0.0, 0.0)
    assert Box(center, size, source="blind").needs_review
    assert Box(center, size, source="ligand").needs_review
    for source in ("pocket", "cocrystal", "site", "reference", "manual"):
        assert not Box(center, size, source=source).needs_review


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
    assert box.source in {"site", "pocket"}
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


def test_known_site_highlights_include_cocrystal_and_site(tmp_path):
    path = make_protein_pdb(tmp_path / "complex.pdb", residues=25, ligand=True, site=True)
    highlights = boxes.known_site_highlights(path)

    kinds = {item.kind for item in highlights}
    assert kinds == {"cocrystal", "site"}

    cocrystal = next(item for item in highlights if item.kind == "cocrystal")
    assert "LIG" in cocrystal.label
    assert len(cocrystal.atoms) == 8

    site = next(item for item in highlights if item.kind == "site")
    assert "PDB SITE" in site.label
    # SITE 记录列出 ALA/GLY/SER/LEU 四个残基，每个残基 4 个主链原子。
    assert len(site.atoms) == 16
    # 配体本身不重复出现在青色位点层里。
    assert all(atom.resname != "LIG" for atom in site.atoms)


def test_ligand_required_sizes_includes_margin(sdf_path):
    required = boxes.ligand_required_sizes([sdf_path])
    assert required is not None
    assert all(value > 2 * boxes.LIGAND_MARGIN for value in required)


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


def test_structure_coords_from_chinese_path(tmp_path, sdf_path):
    pytest.importorskip("rdkit")
    source = tmp_path / "桌面" / "对接 项目" / "阿司匹林.sdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(sdf_path.read_bytes())

    coords = boxes.structure_coords(source)

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
