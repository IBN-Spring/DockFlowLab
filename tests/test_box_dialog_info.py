"""盒子编辑器参考信息的文案生成。"""

from __future__ import annotations

from types import SimpleNamespace

from dockflowlab.services.boxes import KnownSiteHighlight
from dockflowlab.ui.box_view import COCRYSTAL_COLOR, SITE_COLOR
from dockflowlab.ui.dialogs import BoxDialog


def test_site_legend_without_evidence():
    text = BoxDialog._site_legend([])
    assert "未找到共晶配体或 PDB SITE 记录" in text


def test_site_legend_uses_distinct_colors_and_labels():
    highlights = [
        KnownSiteHighlight("cocrystal", "LIG（链 A 序号 100）", ()),
        KnownSiteHighlight("site", "PDB SITE 1（ASP A40、THR A41）", ()),
    ]
    text = BoxDialog._site_legend(highlights)
    assert COCRYSTAL_COLOR in text
    assert SITE_COLOR in text
    assert "共晶配体" in text
    assert "PDB SITE 1" in text


def test_ligand_summary_reports_flexibility_and_minimum_size():
    entry = SimpleNamespace(
        display_name="阿司匹林",
        meta={"prep_details": {"heavy_atoms": 13, "rotatable_bonds": 2}},
    )
    text = BoxDialog._ligand_summary([entry], (16.0, 14.0, 12.0))
    assert "阿司匹林" in text
    assert "13 个重原子" in text
    assert "2 个可旋转键" in text
    assert "建议盒子至少 16.0 × 14.0 × 12.0 Å" in text


def test_ligand_summary_marks_rigid_molecule():
    entry = SimpleNamespace(
        display_name="刚性分子",
        meta={"prep_details": {"heavy_atoms": 10, "rotatable_bonds": 0}},
    )
    text = BoxDialog._ligand_summary([entry], None)
    assert "刚性（无可旋转键）" in text


def test_ligand_summary_without_candidates():
    text = BoxDialog._ligand_summary([], None)
    assert "尚无已准备且支持对接的候选分子" in text
