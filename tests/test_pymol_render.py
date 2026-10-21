"""PyMOL 无界面渲染：脚本生成、编码、枚举与真实进程。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from dockflowlab.services.pymol_render import (
    _read_script_text,
    _script_encodings,
    _system_ansi_encoding,
    _write_script,
    build_job_script,
    complexes_in,
    find_renderer,
    render_complex,
    scale_label_sizes,
    style_token,
    styles_in,
)
from dockflowlab.workspace import Workspace


def test_build_job_script_loads_style_and_ray_traces(tmp_path):
    text = build_job_script(
        "complex.pdb", "style.pml", "preview.png", width=900, height=900
    )
    assert 'load "complex.pdb", complex' in text
    assert "@style.pml" in text
    assert "viewport 900, 900" in text
    assert "ray 900, 900" in text
    assert "png preview.png, dpi=100" in text
    assert "draw " not in text
    assert text.rstrip().endswith("quit")


def test_styles_in_is_recursive_and_ignores_other_files(tmp_path):
    (tmp_path / "蓝色风格").mkdir()
    (tmp_path / "蓝色风格" / "全局.txt").write_text("show cartoon", encoding="utf-8")
    (tmp_path / "蓝色风格" / "细节.pml").write_text("show sticks", encoding="utf-8")
    (tmp_path / "说明.md").write_text("not a style", encoding="utf-8")
    (tmp_path / "忽略.SDF").write_text("not a style", encoding="utf-8")

    found = styles_in(tmp_path)
    assert [path.name for path in found] == ["全局.txt", "细节.pml"]
    assert all(path.is_file() for path in found)


def test_script_encoding_follows_inherited_python_utf8(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")
    assert _script_encodings()[0] == "utf-8"
    monkeypatch.delenv("PYTHONUTF8")
    assert _script_encodings()[0] == _system_ansi_encoding()


def test_script_is_converted_to_system_encoding(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("# 中文注释\nshow cartoon\n", encoding="utf-8-sig")
    target = tmp_path / "style.pml"

    _write_script(target, source)
    encoding = _script_encodings()[0]
    text = target.read_text(encoding=encoding, errors="replace")

    assert "show cartoon" in text
    assert _read_script_text(source).startswith("# 中文注释")
    try:
        "# 中文注释".encode(encoding)
    except UnicodeEncodeError:
        pass
    else:
        assert "中文注释" in text


def test_scale_label_sizes_keeps_each_scope():
    text = (
        "# 注释\n"
        "set label_size, 16, hbonds\n"
        "set label_size, 20, binding_labels\n"
        'python\ncmd.set("label_size", 18, "labels")\npython end\n'
    )
    scaled = scale_label_sizes(text, 2.0)

    assert "set label_size, 32.0, hbonds" in scaled
    assert "set label_size, 40.0, binding_labels" in scaled
    assert 'cmd.set("label_size", 36.0, "labels")' in scaled
    # 未显式设置的标签使用放大后的默认值。
    assert scaled.startswith("set label_size, 28.0")


def test_scale_label_sizes_is_noop_at_one():
    text = "set label_size, 20, binding_labels\n"
    assert scale_label_sizes(text, 1.0) == text


def test_style_token_keeps_chinese_and_folder_name(tmp_path):
    folder = tmp_path / "scripts"
    style = folder / "橙色风格" / "全局.txt"
    style.parent.mkdir(parents=True)
    style.write_text("show cartoon", encoding="utf-8")

    assert style_token(style, folder) == "橙色风格_全局"
    assert style_token(tmp_path / "outside.pml", folder) == "outside"


def test_complexes_in_lists_targets_and_ligands(tmp_path):
    first = tmp_path / "complexes" / "target_a" / "ligand_1.pdb"
    first.parent.mkdir(parents=True)
    first.write_text("END\n", encoding="ascii")
    second = first.parent / "ligand_2.pdb"
    second.write_text("END\n", encoding="ascii")
    other = tmp_path / "complexes" / "target_b" / "ligand_1.pdb"
    other.parent.mkdir(parents=True)
    other.write_text("END\n", encoding="ascii")
    (tmp_path / "complexes" / "notes.txt").write_text("ignore", encoding="ascii")

    items = complexes_in(tmp_path)
    assert [(item.target, item.ligand) for item in items] == [
        ("target_a", "ligand_1"),
        ("target_a", "ligand_2"),
        ("target_b", "ligand_1"),
    ]
    assert items[0].path == first


def test_find_renderer_honours_configured_executable():
    renderer = find_renderer(sys.executable)
    assert renderer
    assert Path(renderer).resolve() == Path(sys.executable).resolve()


def test_find_renderer_prefers_bundled_runner(monkeypatch, tmp_path):
    """安装包应在未另装 PyMOL 的机器上直接使用内置渲染器。"""
    app_dir = tmp_path / "DockFlowLab"
    internal = app_dir / "_internal"
    internal.mkdir(parents=True)
    runner = app_dir / ("PyMOLRunner.exe" if os.name == "nt" else "PyMOLRunner")
    runner.write_text("", encoding="ascii")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "DockFlowLab.exe"))
    monkeypatch.delenv("PYMOL_RENDER_EXE", raising=False)

    renderer = find_renderer()
    assert Path(renderer).resolve() == runner.resolve()


def test_render_complex_reports_missing_inputs(tmp_path):
    result = render_complex(
        tmp_path / "missing.pdb", tmp_path / "missing.pml", tmp_path / "out.png"
    )
    assert not result.ok
    assert "复合物" in result.error


def test_render_complex_with_real_pymol(tmp_path):
    if not find_renderer():
        pytest.skip("当前环境没有可用的 PyMOL")
    complex_path = tmp_path / "complex.pdb"
    complex_path.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
        "ATOM      2  CB  ALA A   1       1.500   0.000   0.000  1.00 20.00           C\n"
        "END\n",
        encoding="ascii",
    )
    style_path = tmp_path / "style.pml"
    style_path.write_text(
        "hide everything\nshow sticks\ncolor red\n", encoding="utf-8"
    )
    output = tmp_path / "preview.png"
    result = render_complex(
        complex_path, style_path, output, width=240, height=240, timeout=60
    )
    assert result.ok, result.error
    assert output.is_file()
    assert output.stat().st_size > 0
    assert "Ray: render time" in result.output


def test_render_path_preserves_style_label(tmp_path):
    workspace = Workspace.create(tmp_path / "render project")
    path = workspace.render_path("TARGET", "LIGAND", "橙色风格_全局")
    assert path.parent == workspace.root / "pymol" / "renders"
    assert path.name == "TARGET__LIGAND__橙色风格_全局.png"


@pytest.fixture(scope="module")
def qt_app():
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    try:
        app = qt_widgets.QApplication.instance()
        if app is None:
            app = qt_widgets.QApplication([])
    except Exception as exc:  # pragma: no cover - 无图形环境的 CI
        pytest.skip(f"无法创建 QApplication：{exc}")
    return app


def test_render_page_payload_and_selection_order(qt_app, tmp_path):
    from dockflowlab.ui.render_page import RenderPage

    workspace = Workspace.create(tmp_path / "render project")
    complex_file = workspace.complex_dir("TARGET") / "LIGAND.pdb"
    complex_file.parent.mkdir(parents=True, exist_ok=True)
    complex_file.write_text("END\n", encoding="ascii")
    scripts = tmp_path / "styles"
    (scripts / "橙色风格").mkdir(parents=True)
    (scripts / "橙色风格" / "全局.txt").write_text("show cartoon\n", encoding="utf-8")
    (scripts / "橙色风格" / "细节.pml").write_text("show sticks\n", encoding="utf-8")

    page = RenderPage()
    page.set_renderer("C:/fake/pymol.exe")
    page.set_script_dir(scripts)
    page.set_workspace(workspace, [])
    page.set_render_size(900)
    page.set_label_scale(2.0)
    page._select_all()
    assert [path.name for path in page.selected_scripts()] == ["全局.txt", "细节.pml"]
    assert page.render_button.isEnabled()

    captured: dict = {}
    page.render_requested.connect(captured.update)
    page._emit_render()

    assert captured["width"] == 900
    assert captured["label_scale"] == 2.0
    assert captured["complex_path"] == str(complex_file)
    assert [item["label"] for item in captured["scripts"]] == [
        "橙色风格/全局.txt",
        "橙色风格/细节.pml",
    ]
    assert [Path(item["output"]).name for item in captured["scripts"]] == [
        "TARGET__LIGAND__橙色风格_全局.png",
        "TARGET__LIGAND__橙色风格_细节.png",
    ]

    page.start_running(2)
    assert not page.render_button.isEnabled()
    assert not page.script_list.isEnabled()
    page.finish([])
    assert page.render_button.isEnabled()
