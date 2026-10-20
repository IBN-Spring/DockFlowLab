"""配体读取行为测试。"""

from __future__ import annotations

import pytest


def test_multirecord_sdf_uses_first_record(tmp_path, sdf_path):
    pytest.importorskip("rdkit")
    from dockflowlab.services.ligand import read_molecule

    first = sdf_path.read_text(encoding="utf-8")
    second = first.replace("2244", "9999", 1)
    combined = tmp_path / "two_records.sdf"
    combined.write_text(first + second, encoding="utf-8")

    mol, warnings = read_molecule(combined)

    assert mol.GetProp("_Name") == "2244"
    assert any("2 条记录" in message for message in warnings)
