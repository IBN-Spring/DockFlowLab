"""对接模式识别：结构类型、支持矩阵与不支持的组合。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dockflowlab.core import pdbio
from dockflowlab.core.models import (
    KIND_LIGAND,
    KIND_TARGET,
    STATUS_FAILED,
    STATUS_READY,
    Box,
    PrepOutcome,
)
from dockflowlab.errors import PreparationError
from dockflowlab.services import docking, ligand, modes, preparation
from dockflowlab.services.receptor import inspect_target

from .factories import make_dna_pdb, make_protein_pdb


def test_structure_kind_and_chains_recognise_dna(tmp_path):
    path = make_dna_pdb(tmp_path / "dna.pdb")

    kind, detail = pdbio.structure_kind(path)
    assert kind == "nucleic_acid"
    assert "核酸" in detail

    chains = pdbio.polymer_chains(path)
    assert [chain["chain"] for chain in chains] == ["D"]
    assert chains[0]["type"] == "nucleic_acid"
    assert chains[0]["nucleic_residues"] == 8


def test_protein_chains_still_only_report_amino_acids(tmp_path):
    path = make_protein_pdb(tmp_path / "protein.pdb")
    dna = make_dna_pdb(tmp_path / "dna.pdb")

    assert pdbio.protein_chains(path)
    assert pdbio.protein_chains(dna) == []


@pytest.mark.parametrize(
    ("target", "ligand_kind", "supported", "label"),
    [
        ("protein", "small_molecule", True, "蛋白–小分子"),
        ("peptide", "small_molecule", True, "多肽–小分子"),
        ("nucleic_acid", "small_molecule", True, "核酸–小分子"),
        ("small_molecule", "small_molecule", True, "小分子–小分子"),
        ("protein", "peptide", False, "蛋白质–多肽/短肽"),
        ("protein", "protein", False, "蛋白质–蛋白质"),
        ("nucleic_acid", "protein", False, "核酸（DNA/RNA）–蛋白质"),
        ("small_molecule", "protein", False, "小分子–蛋白质"),
        ("peptide", "peptide", False, "多肽/短肽–多肽/短肽"),
    ],
)
def test_resolve_support_matrix(target, ligand_kind, supported, label):
    mode = modes.resolve(target, ligand_kind)
    assert mode.supported is supported
    assert mode.label == label
    if not supported:
        assert "不支持" in mode.error_text


def test_nucleic_acid_alias_is_normalised():
    mode = modes.resolve("DNA", "small")
    assert mode.key == "nucleic_acid_small_molecule"
    assert mode.supported


def test_entry_kind_prefers_prepared_metadata(workspace, tmp_path):
    source = make_dna_pdb(tmp_path / "dna.pdb")
    entry = workspace.import_file(KIND_TARGET, source)
    entry.meta["kind"] = "nucleic_acid"
    assert modes.entry_kind(entry, workspace) == "nucleic_acid"


def test_entry_kind_falls_back_to_file_classification(workspace, tmp_path):
    source = make_dna_pdb(tmp_path / "dna.pdb")
    entry = workspace.import_file(KIND_TARGET, source)
    entry.meta.pop("kind", None)
    assert modes.entry_kind(entry, workspace) == "nucleic_acid"


def test_pair_mode_with_workspace_entries(workspace, tmp_path):
    protein = workspace.import_file(
        KIND_TARGET, make_protein_pdb(tmp_path / "protein.pdb", residues=30)
    )
    dna = workspace.import_file(KIND_TARGET, make_dna_pdb(tmp_path / "dna.pdb"))
    small = workspace.import_file(KIND_LIGAND, make_protein_pdb(tmp_path / "small.pdb", residues=1))
    small.meta["kind"] = "small_molecule"
    other_protein = workspace.import_file(
        KIND_LIGAND, make_protein_pdb(tmp_path / "other.pdb", residues=30)
    )
    other_protein.meta["kind"] = "protein"

    assert modes.pair_mode(protein, small, workspace).key == "protein_small_molecule"
    assert modes.pair_mode(dna, small, workspace).key == "nucleic_acid_small_molecule"
    rejected = modes.pair_mode(protein, other_protein, workspace)
    assert not rejected.supported
    assert "ClusPro" in rejected.guidance


def test_ligand_kind_check_rejects_protein_and_peptide():
    warnings: list[str] = []
    with pytest.raises(PreparationError, match="ClusPro"):
        ligand._check_ligand_kind("protein", "检测到 120 个氨基酸残基", warnings)
    with pytest.raises(PreparationError, match="多肽"):
        ligand._check_ligand_kind("peptide", "检测到 12 个氨基酸残基", warnings)
    with pytest.raises(PreparationError, match="核酸"):
        ligand._check_ligand_kind("nucleic_acid", "检测到核酸结构", warnings)
    assert warnings == []


def test_store_outcome_persists_kind_and_failure_clears_it(workspace, tmp_path):
    entry = workspace.import_file(KIND_TARGET, make_dna_pdb(tmp_path / "dna.pdb"))
    prepared = workspace.prepared_target_pdb(entry.stem)
    prepared.parent.mkdir(parents=True, exist_ok=True)
    prepared.write_text("ATOM      1  P   DA D   1       0.000   0.000   0.000\n", encoding="utf-8")

    preparation._store_outcome(
        workspace,
        entry,
        PrepOutcome(True, method="test", details={"kind": "nucleic_acid"}),
        prepared,
    )
    assert entry.meta["kind"] == "nucleic_acid"
    assert entry.status == STATUS_READY

    preparation._store_failure(workspace, entry, "测试失败")
    assert "kind" not in entry.meta
    assert entry.status == STATUS_FAILED


def test_inspect_target_reports_dna_chains(tmp_path):
    info = inspect_target(make_dna_pdb(tmp_path / "dna.pdb"))
    assert info["ok"] is True
    assert info["kind"] == "nucleic_acid"
    assert info["chains"][0]["type"] == "nucleic_acid"


class _ForbiddenVina:
    available = True

    def __init__(self) -> None:
        self.called = False

    def dock(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        self.called = True
        raise AssertionError("不支持的组合不应调用 Vina")


def _ready_entry(workspace, kind: str, name: str, kind_value: str, tmp_path: Path):
    source = make_protein_pdb(
        tmp_path / f"{name}.pdb",
        residues=9 if kind == KIND_TARGET else 1,
    )
    entry = workspace.import_file(kind, source, display_name=name)
    prepared = (
        workspace.prepared_target_pdb(entry.stem)
        if kind == KIND_TARGET
        else workspace.prepared_ligand_pdbqt(entry.stem)
    )
    prepared.parent.mkdir(parents=True, exist_ok=True)
    prepared.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n", encoding="utf-8"
    )
    workspace.update_entry(
        entry,
        status=STATUS_READY,
        prepared=workspace.relative(prepared),
        meta={"kind": kind_value},
    )
    return entry


def test_dock_pair_does_not_call_vina_for_unsupported_pair(workspace, tmp_path):
    target = _ready_entry(workspace, KIND_TARGET, "target", "protein", tmp_path)
    other = _ready_entry(workspace, KIND_LIGAND, "other", "protein", tmp_path)
    box = Box(center=(0.0, 0.0, 0.0), size=(20.0, 20.0, 20.0))
    fake = _ForbiddenVina()

    result = docking.dock_pair(workspace, target, other, box, vina=fake)

    assert fake.called is False
    assert result.ok is False
    assert result.mode_supported is False
    assert result.mode_label == "蛋白质–蛋白质"
    assert "不支持" in result.error
