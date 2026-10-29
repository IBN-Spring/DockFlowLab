"""exhaustiveness 建议与配体效率测试。"""

from __future__ import annotations

import pytest

from dockflowlab.services.advice import (
    ligand_efficiency,
    recommend_exhaustiveness,
)


def test_rigid_small_ligand_gets_low_exhaustiveness():
    advice = recommend_exhaustiveness(rotatable_bonds=1, heavy_atoms=12)
    assert advice.recommended == 8
    assert "较刚性" in advice.reason
    assert advice.warning_for(8) == ""


def test_flexible_ligand_gets_higher_exhaustiveness():
    advice = recommend_exhaustiveness(rotatable_bonds=11, heavy_atoms=32)
    assert advice.recommended == 32
    assert "柔性较高" in advice.reason
    warning = advice.warning_for(16)
    assert "低于建议值 32" in warning
    assert advice.warning_for(32) == ""


def test_very_flexible_ligand_gets_max_tier():
    advice = recommend_exhaustiveness(rotatable_bonds=20, heavy_atoms=55)
    assert advice.recommended == 64


def test_large_ligand_bumps_tier():
    advice = recommend_exhaustiveness(rotatable_bonds=4, heavy_atoms=70)
    assert advice.recommended == 32
    huge = recommend_exhaustiveness(rotatable_bonds=4, heavy_atoms=120)
    assert huge.recommended == 64


def test_negative_and_zero_inputs_are_safe():
    advice = recommend_exhaustiveness(rotatable_bonds=-3, heavy_atoms=0)
    assert advice.recommended == 8
    assert advice.rotatable_bonds == 0


def test_ligand_efficiency_formula_and_missing_values():
    assert ligand_efficiency(-9.6, 24) == pytest.approx(0.4)
    assert ligand_efficiency(-7.5, 15) == pytest.approx(0.5)
    assert ligand_efficiency(None, 20) is None
    assert ligand_efficiency(-7.5, 0) is None
    assert ligand_efficiency(-7.5, None) is None
