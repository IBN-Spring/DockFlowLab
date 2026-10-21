"""对接引擎适配：命令构建、Smina 解析、自动回退与卡死检测。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from dockflowlab.config import AppSettings
from dockflowlab.core import pdbio
from dockflowlab.core.models import Box
from dockflowlab.services.docking import DockingOptions, dock_pair
from dockflowlab.tools.runtime import run_process
from dockflowlab.tools.vina import (
    ENGINE_ORDER,
    ENGINE_QVINA2,
    ENGINE_SMINA,
    ENGINE_VINA,
    VinaRun,
    make_engine,
)


def _atom(serial: int, name: str, x: float, y: float, z: float) -> str:
    return (
        f"{'ATOM':<6s}{serial:5d} {name:<4s} {'':1s}{'UNL':>3s} {'A':1s}{1:4d}{'':1s}"
        f"   {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00     0.000 {'C':>2s}"
    )


def _pdbqt(*, models: int = 1) -> str:
    lines = []
    for index in range(models):
        if models > 1:
            lines.append(f"MODEL {index + 1}")
        lines.extend(
            [
                "REMARK minimizedAffinity %8.2f" % (-6.0 - index),
                _atom(1, "C1", 0.0, 0.0, 0.0),
                _atom(2, "C2", 1.4, 0.0, 0.0),
                _atom(3, "O1", 2.1, 1.2, 0.0),
                "ROOT",
                "ENDROOT",
                "TORSDOF 0",
            ]
        )
        if models > 1:
            lines.append("ENDMDL")
    return "\n".join(lines) + "\n"


def _receptor_pdbqt() -> str:
    return "\n".join(
        [
            _atom(1, "CA", 0.0, 0.0, 0.0),
            _atom(2, "CB", 1.5, 0.0, 0.0),
            _atom(3, "CG", 2.3, 1.1, 0.0),
            "END",
        ]
    ) + "\n"


def _command_args() -> dict[str, object]:
    return {
        "receptor": "receptor.pdbqt",
        "ligand": "ligand.pdbqt",
        "output": "out.pdbqt",
        "center": (1.0, 2.0, 3.0),
        "size": (20.0, 22.0, 24.0),
        "exhaustiveness": 8,
        "num_modes": 5,
        "energy_range": 3.0,
        "seed": 7,
        "cpu": 2,
    }


def test_engine_registry_order_and_keys():
    assert ENGINE_ORDER == (ENGINE_VINA, ENGINE_QVINA2, ENGINE_SMINA)
    for key in ENGINE_ORDER:
        engine = make_engine(key)
        assert engine.key == key
        assert engine.label


def test_vina_writes_config_and_uses_config_flag(tmp_path):
    command = make_engine(ENGINE_VINA)._command(tmp_path, **_command_args())
    assert command[-2:] == ["--config", "vina.conf"]
    config = (tmp_path / "vina.conf").read_text(encoding="ascii")
    assert "receptor = receptor.pdbqt" in config
    assert "center_x = 1.000" in config
    assert "size_z = 24.000" in config
    assert "exhaustiveness = 8" in config
    assert "cpu = 2" in config


def test_cli_engines_pass_arguments_directly(tmp_path):
    for key in (ENGINE_QVINA2, ENGINE_SMINA):
        command = make_engine(key)._command(tmp_path, **_command_args())
        assert "--config" not in command
        assert "--receptor" in command
        assert command[command.index("--center_y") + 1] == "2.000"
        assert command[command.index("--size_x") + 1] == "20.000"
        assert command[command.index("--num_modes") + 1] == "5"


def test_smina_parses_mode_table_and_annotates_output(tmp_path):
    output = tmp_path / "out.pdbqt"
    output.write_text(_pdbqt(models=2), encoding="utf-8")
    log_text = "1 -6.00 0.000 0.000\n2 -5.40 1.100 1.800\n"

    poses = make_engine(ENGINE_SMINA)._parse_poses(output, log_text)

    assert [pose.affinity for pose in poses] == [-6.0, -5.4]
    text = output.read_text(encoding="utf-8")
    assert text.count("REMARK VINA RESULT:") == 2
    # 补上的标准备注让重新扫描结果时也能解析出同样的结合能。
    rescanned = pdbio.parse_pdbqt_poses(output)
    assert [pose.affinity for pose in rescanned] == [-6.0, -5.4]


def test_smina_falls_back_to_remark_when_log_has_no_table(tmp_path):
    output = tmp_path / "out.pdbqt"
    output.write_text(_pdbqt(models=2), encoding="utf-8")

    poses = make_engine(ENGINE_SMINA)._parse_poses(output, "no table here")

    assert [pose.affinity for pose in poses] == [-6.0, -7.0]


def _write_prepared(workspace) -> tuple[object, object, Box]:
    target = workspace.targets[0]
    ligand = workspace.ligands[0]
    receptor = workspace.prepared_target_pdbqt(target.stem)
    ligand_path = workspace.prepared_ligand_pdbqt(ligand.stem)
    receptor.parent.mkdir(parents=True, exist_ok=True)
    ligand_path.parent.mkdir(parents=True, exist_ok=True)
    receptor.write_text(_receptor_pdbqt(), encoding="utf-8")
    ligand_path.write_text(_pdbqt(), encoding="utf-8")
    workspace.update_entry(
        target, status="ready", prepared=workspace.relative(receptor)
    )
    workspace.update_entry(
        ligand, status="ready", prepared=workspace.relative(ligand_path)
    )
    return target, ligand, Box(center=(0.0, 0.0, 0.0), size=(20.0, 20.0, 20.0))


class _FakeEngine:
    def __init__(self, key: str, label: str, succeeds: bool) -> None:
        self.key = key
        self.label = label
        self.succeeds = succeeds
        self.available = True
        self.calls = 0

    def dock(self, receptor_pdbqt, ligand_pdbqt, box, output_pdbqt, **kwargs) -> VinaRun:
        self.calls += 1
        if not self.succeeds:
            return VinaRun(False, error="超过 20 分钟没有输出，判定为卡死", stalled=True)
        output = Path(output_pdbqt)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_pdbqt(), encoding="utf-8")
        return VinaRun(True, poses=pdbio.parse_pdbqt_poses(output), output_path=str(output))


def _options(**kwargs) -> DockingOptions:
    return DockingOptions(
        exhaustiveness=1,
        num_modes=1,
        seed=7,
        cpu=1,
        make_complex=False,
        make_pymol=False,
        **kwargs,
    )


def test_failed_engine_falls_back_to_next(prepared_workspace):
    target, ligand, box = _write_prepared(prepared_workspace)
    primary = _FakeEngine("vina", "AutoDock Vina", succeeds=False)
    fallback = _FakeEngine("smina", "Smina", succeeds=True)

    result = dock_pair(
        prepared_workspace,
        target,
        ligand,
        box,
        options=_options(fallback=True),
        vina=primary,
        fallback_engines=[fallback],
    )

    assert result.ok, result.error
    assert primary.calls == 1
    assert fallback.calls == 1
    assert any("已自动改用 Smina 重试" in warning for warning in result.warnings)
    assert result.parameters["engine"] == "smina"
    assert result.parameters["engine_label"] == "Smina"


def test_fallback_can_be_disabled(prepared_workspace):
    target, ligand, box = _write_prepared(prepared_workspace)
    primary = _FakeEngine("vina", "AutoDock Vina", succeeds=False)
    fallback = _FakeEngine("smina", "Smina", succeeds=True)

    result = dock_pair(
        prepared_workspace,
        target,
        ligand,
        box,
        options=_options(fallback=False),
        vina=primary,
        fallback_engines=[fallback],
    )

    assert not result.ok
    assert "卡死" in result.error
    assert fallback.calls == 0


def test_cancel_does_not_trigger_fallback(prepared_workspace):
    target, ligand, box = _write_prepared(prepared_workspace)
    cancel = threading.Event()

    class _CancellingEngine(_FakeEngine):
        def dock(self, *args, **kwargs) -> VinaRun:
            cancel.set()
            return super().dock(*args, **kwargs)

    primary = _CancellingEngine("vina", "AutoDock Vina", succeeds=False)
    fallback = _FakeEngine("smina", "Smina", succeeds=True)

    result = dock_pair(
        prepared_workspace,
        target,
        ligand,
        box,
        options=_options(fallback=True),
        vina=primary,
        fallback_engines=[fallback],
        cancel=cancel,
    )

    assert not result.ok
    assert fallback.calls == 0


def test_run_process_stops_after_stall():
    started = time.monotonic()
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stall_timeout=0.8,
        timeout=30,
    )

    assert result.stalled is True
    assert result.ok is False
    assert time.monotonic() - started < 10


def test_engine_settings_are_normalized(tmp_path):
    import json

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "docking_engine": "unknown-engine",
                "engine_fallback": "yes",
                "qvina2_path": 123,
                "smina_path": None,
            }
        ),
        encoding="utf-8",
    )
    loaded = AppSettings.load(path)
    assert loaded.docking_engine == ENGINE_VINA
    assert loaded.engine_fallback is True
    assert loaded.qvina2_path == ""
    assert loaded.smina_path == ""
