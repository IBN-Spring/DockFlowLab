"""应用配置与用户设置。

设置保存在用户配置目录，不写入项目目录。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .version import __app_name__

APP_DIR_NAME = "DockFlowLab"
SETTINGS_FILENAME = "settings.json"


def user_config_dir() -> Path:
    """返回当前平台的用户配置目录。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / APP_DIR_NAME


def user_cache_dir() -> Path:
    """返回缓存目录（下载缓存、图标缓存等）。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
    return root / APP_DIR_NAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class AppSettings:
    """全局用户偏好。字段全部可 JSON 序列化。"""

    # 外部程序（留空表示自动查找）
    vina_path: str = ""
    obabel_path: str = ""

    # 网络
    proxy: str = ""
    request_timeout: int = 60

    # 对接默认参数（面向科研的保守默认值）
    exhaustiveness: int = 16
    num_modes: int = 9
    energy_range: float = 3.0
    seed: int = 42
    cpu: int = 0  # 0 表示使用全部可用核心

    # 盒子
    box_padding: float = 5.0
    box_min_size: float = 18.0
    box_max_size: float = 60.0

    # 界面
    theme: str = "light"
    recent_workspaces: list[str] = field(default_factory=list)
    last_workspace: str = ""
    show_welcome: bool = True

    # 预处理
    keep_ligand_names: bool = True
    add_hydrogens_ph: float = 7.4

    def __post_init__(self) -> None:
        if self.exhaustiveness < 1:
            self.exhaustiveness = 16
        if self.num_modes < 1:
            self.num_modes = 9
        if self.box_min_size <= 0:
            self.box_min_size = 18.0
        if self.box_max_size < self.box_min_size:
            self.box_max_size = max(60.0, self.box_min_size)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    @classmethod
    def path(cls) -> Path:
        return user_config_dir() / SETTINGS_FILENAME

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        path = path or cls.path()
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        try:
            settings = cls(**kwargs)
        except TypeError:
            return cls()
        # 防御损坏的最近项目列表
        settings.recent_workspaces = [
            str(p) for p in settings.recent_workspaces if isinstance(p, str)
        ][:10]
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or self.path()
        _atomic_write_json(path, asdict(self))

    # ------------------------------------------------------------------
    # 最近项目
    # ------------------------------------------------------------------
    def remember_workspace(self, root: str | Path) -> None:
        root_str = str(Path(root).resolve())
        self.last_workspace = root_str
        recent = [p for p in self.recent_workspaces if Path(p) != Path(root_str)]
        recent.insert(0, root_str)
        self.recent_workspaces = recent[:10]
        self.save()

    def forget_workspace(self, root: str | Path) -> None:
        root_str = str(Path(root).resolve())
        self.recent_workspaces = [
            p for p in self.recent_workspaces if str(Path(p).resolve()) != root_str
        ]
        if self.last_workspace == root_str:
            self.last_workspace = ""
        self.save()

    def proxy_url(self) -> str:
        """把 ``7897`` 这样的端口或 ``127.0.0.1:7897`` 规范化为 URL。"""
        value = self.proxy.strip()
        if not value:
            return ""
        if value.isdigit():
            return f"http://127.0.0.1:{value}"
        if "://" not in value:
            return f"http://{value}"
        return value

    def proxies(self) -> dict[str, str] | None:
        url = self.proxy_url()
        if not url:
            return None
        return {"http": url, "https": url}


# 兼容旧代码的常量
APP_NAME = __app_name__
