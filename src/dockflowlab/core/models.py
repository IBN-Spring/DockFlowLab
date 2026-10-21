"""领域数据模型。

这些模型只依赖标准库，便于在服务层、CLI 和界面层之间传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KIND_TARGET = "target"
KIND_LIGAND = "ligand"

STATUS_NEW = "new"
STATUS_PREPARING = "preparing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_NEW: "待准备",
    STATUS_PREPARING: "准备中",
    STATUS_READY: "已就绪",
    STATUS_FAILED: "需要处理",
}

# Vina 搜索盒的边长范围（Å）。界面输入、3D 视图拖拽与校验共用。
MIN_BOX_SIZE = 5.0
MAX_BOX_SIZE = 150.0


@dataclass
class ProjectFile:
    """项目中的一个靶点或配体。路径均相对于工作目录。"""

    kind: str
    stem: str
    display_name: str
    source: str
    status: str = STATUS_NEW
    prepared: str = ""
    box: str = ""
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == STATUS_READY and bool(self.prepared)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "stem": self.stem,
            "display_name": self.display_name,
            "source": self.source,
            "status": self.status,
            "prepared": self.prepared,
            "box": self.box,
            "message": self.message,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectFile":
        return cls(
            kind=str(data.get("kind", "")),
            stem=str(data.get("stem", "")),
            display_name=str(data.get("display_name", data.get("stem", ""))),
            source=str(data.get("source", "")),
            status=str(data.get("status", STATUS_NEW)),
            prepared=str(data.get("prepared", "")),
            box=str(data.get("box", "")),
            message=str(data.get("message", "")),
            meta=dict(data.get("meta") or {}),
        )


@dataclass(frozen=True)
class Box:
    """Vina 搜索盒。单位：Å。"""

    center: tuple[float, float, float]
    size: tuple[float, float, float]
    source: str = "auto"
    note: str = ""
    reference: str = ""

    @property
    def volume(self) -> float:
        return self.size[0] * self.size[1] * self.size[2]

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": list(self.center),
            "size": list(self.size),
            "source": self.source,
            "note": self.note,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Box":
        center = tuple(float(v) for v in data.get("center", (0, 0, 0)))[:3]
        size = tuple(float(v) for v in data.get("size", (20, 20, 20)))[:3]
        return cls(
            center=center,  # type: ignore[arg-type]
            size=size,  # type: ignore[arg-type]
            source=str(data.get("source", "auto")),
            note=str(data.get("note", "")),
            reference=str(data.get("reference", "")),
        )

    @property
    def source_label(self) -> str:
        return {
            "pocket": "几何口袋",
            "cocrystal": "共晶配体",
            "site": "PDB 结合位点",
            "blind": "全结构盲对接",
            "manual": "手动设置",
            "reference": "参考分子",
            "ligand": "目标分子",
        }.get(self.source, "自动")

    @property
    def needs_review(self) -> bool:
        """自动盒子是否缺少可靠的结合位点信息，需要用户手动确认。

        ``blind`` 覆盖整个结构，``ligand`` 只按分子尺寸估算；两者都无法
        像几何口袋或实验位点那样定位结合位置。界面应提示用户设置中心与
        边长，而不是静默使用。
        """
        return self.source in {"blind", "ligand"}


@dataclass
class Pose:
    rank: int
    affinity: float
    rmsd_lb: float = 0.0
    rmsd_ub: float = 0.0

    @property
    def label(self) -> str:
        return f"构象 {self.rank}"


@dataclass
class DockingResult:
    target: str
    ligand: str
    ok: bool
    poses: list[Pose] = field(default_factory=list)
    log_path: str = ""
    output_path: str = ""
    complex_path: str = ""
    pymol_path: str = ""
    error: str = ""
    skipped: bool = False
    runtime_seconds: float = 0.0
    box_source: str = ""
    warnings: list[str] = field(default_factory=list)
    mode_key: str = ""
    mode_label: str = ""
    mode_supported: bool = True

    @property
    def best_affinity(self) -> float | None:
        return self.poses[0].affinity if self.poses else None

    @property
    def affinity_text(self) -> str:
        value = self.best_affinity
        return f"{value:.2f}" if value is not None else "—"


@dataclass
class PrepOutcome:
    ok: bool
    method: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DownloadResult:
    data: bytes
    filename: str
    display_name: str
    meta: dict[str, Any] = field(default_factory=dict)
