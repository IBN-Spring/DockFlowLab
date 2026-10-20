"""设置持久化与代理规范化。"""

from __future__ import annotations

import json

from dockflowlab.config import AppSettings


def test_proxy_normalization():
    settings = AppSettings(proxy="7897")
    assert settings.proxy_url() == "http://127.0.0.1:7897"
    assert settings.proxies() == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }
    assert AppSettings(proxy="127.0.0.1:7890").proxy_url() == "http://127.0.0.1:7890"
    assert AppSettings(proxy="socks5://127.0.0.1:1080").proxy_url() == "socks5://127.0.0.1:1080"
    assert AppSettings(proxy="").proxies() is None


def test_settings_roundtrip_and_unknown_fields(tmp_path):
    path = tmp_path / "settings.json"
    original = AppSettings(
        exhaustiveness=8,
        num_modes=3,
        proxy="7897",
        recent_workspaces=["/tmp/a", 123, "/tmp/b"],
        last_workspace="/tmp/a",
    )
    original.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unknown_future_field"] = "ignored"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = AppSettings.load(path)
    assert loaded.exhaustiveness == 8
    assert loaded.num_modes == 3
    assert loaded.proxy_url() == "http://127.0.0.1:7897"
    assert loaded.recent_workspaces == ["/tmp/a", "/tmp/b"]


def test_corrupt_settings_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    settings = AppSettings.load(path)
    assert settings.exhaustiveness == 16


def test_invalid_numbers_are_normalized():
    settings = AppSettings(exhaustiveness=0, num_modes=0, box_min_size=0, box_max_size=5)
    assert settings.exhaustiveness == 16
    assert settings.num_modes == 9
    assert settings.box_min_size == 18
    assert settings.box_max_size >= settings.box_min_size


def test_remember_and_forget_workspace(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings = AppSettings()
    settings.save = lambda path=None: None  # type: ignore[method-assign]
    settings.remember_workspace(tmp_path / "one")
    settings.remember_workspace(tmp_path / "two")
    settings.remember_workspace(tmp_path / "one")
    assert settings.recent_workspaces[0] == str((tmp_path / "one").resolve())
    assert len(settings.recent_workspaces) == 2
    settings.forget_workspace(tmp_path / "one")
    assert all("one" not in path for path in settings.recent_workspaces)
    assert settings.last_workspace == ""
