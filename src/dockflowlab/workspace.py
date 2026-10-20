"""工作目录（项目）管理。

用户选择一个文件夹后，DockFlowLab 只在该文件夹内读写：

    <工作目录>/
      project.json
      inputs/targets/            用户提供的靶点结构
      inputs/ligands/            用户提供的配体结构
      prepared/targets/<名称>/   预处理后的受体
      prepared/ligands/<名称>/   预处理后的配体
      boxes/                     对接盒子参数
      docking/<靶点>/            对接输出与日志
      complexes/<靶点>/          受体-配体复合物
      analysis/                  汇总表与热图
      report/                    HTML 报告
      pymol/                     可视化脚本
      logs/                      运行日志

内部文件名统一使用 ASCII 安全字符，显示名称保留用户原始名称。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .core.models import (
    KIND_LIGAND,
    KIND_TARGET,
    STATUS_FAILED,
    STATUS_NEW,
    STATUS_READY,
    Box,
    ProjectFile,
)
from .errors import InputError

PROJECT_FILENAME = "project.json"
SCHEMA_VERSION = 1

TARGET_EXTENSIONS = {".pdb", ".ent", ".cif", ".mmcif", ".pdbqt", ".mol2"}
LIGAND_EXTENSIONS = {".sdf", ".mol", ".mol2", ".pdb", ".pdbqt", ".smi", ".smiles", ".inchi"}

SUBDIRS = (
    "inputs/targets",
    "inputs/ligands",
    "prepared/targets",
    "prepared/ligands",
    "boxes",
    "docking",
    "complexes",
    "analysis",
    "report",
    "pymol",
    "logs",
)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_stem(name: str, fallback: str = "item", max_length: int = 48) -> str:
    """把任意名称转换为跨平台安全的 ASCII 文件主干。"""
    raw = Path(str(name)).stem if name else ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _UNSAFE_CHARS.sub("_", ascii_text).strip("._-")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned or len(cleaned) < 2:
        cleaned = fallback
    return cleaned[:max_length].strip("._-") or fallback


def file_sha1(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Workspace:
    """一个 DockFlowLab 项目。"""

    def __init__(self, root: str | Path, data: dict[str, Any] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.data: dict[str, Any] = data or {}
        self.data.setdefault("schema", SCHEMA_VERSION)
        self.data.setdefault("name", self.root.name or "未命名项目")
        self.data.setdefault("created", utc_now())
        self.data.setdefault("updated", utc_now())
        self.data.setdefault("targets", [])
        self.data.setdefault("ligands", [])
        self.data.setdefault("notes", "")
        self._normalize_entries()

    # ------------------------------------------------------------------
    # 创建 / 打开 / 保存
    # ------------------------------------------------------------------
    @classmethod
    def create(cls, root: str | Path, name: str = "") -> "Workspace":
        root = Path(root).expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise InputError(f"所选路径不是文件夹：{root}")
        root.mkdir(parents=True, exist_ok=True)
        project = root / PROJECT_FILENAME
        if project.is_file():
            return cls.open(root)
        data = {
            "schema": SCHEMA_VERSION,
            "name": name or root.name or "未命名项目",
            "created": utc_now(),
            "updated": utc_now(),
            "targets": [],
            "ligands": [],
            "notes": "",
        }
        workspace = cls(root, data)
        workspace.ensure_structure()
        workspace.save()
        return workspace

    @classmethod
    def open(cls, root: str | Path) -> "Workspace":
        root = Path(root).expanduser().resolve()
        project = root / PROJECT_FILENAME
        if not project.is_file():
            raise InputError(f"该文件夹不是 DockFlowLab 项目（缺少 {PROJECT_FILENAME}）")
        try:
            data = json.loads(project.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InputError(f"项目文件损坏，无法读取：{exc}") from exc
        if not isinstance(data, dict):
            raise InputError("项目文件格式不正确")
        workspace = cls(root, data)
        missing = workspace.ensure_structure()
        if missing:
            workspace.save()
        return workspace

    @classmethod
    def open_or_create(cls, root: str | Path) -> "Workspace":
        if (Path(root) / PROJECT_FILENAME).is_file():
            return cls.open(root)
        return cls.create(root)

    @property
    def project_path(self) -> Path:
        return self.root / PROJECT_FILENAME

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.root.name)

    @name.setter
    def name(self, value: str) -> None:
        self.data["name"] = value.strip() or self.root.name

    def ensure_structure(self) -> list[str]:
        """创建缺失的标准子目录，返回创建过的相对路径。"""
        created: list[str] = []
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in SUBDIRS:
            directory = self.root / relative
            if not directory.is_dir():
                directory.mkdir(parents=True, exist_ok=True)
                created.append(relative)
        return created

    def save(self) -> None:
        self.data["updated"] = utc_now()
        payload = dict(self.data)
        payload["targets"] = [entry.to_dict() for entry in self.targets]
        payload["ligands"] = [entry.to_dict() for entry in self.ligands]
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.project_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.project_path)

    def _normalize_entries(self) -> None:
        for kind in (KIND_TARGET, KIND_LIGAND):
            entries = []
            seen: set[str] = set()
            for raw in self.data.get(kind + "s", []):
                if not isinstance(raw, dict):
                    continue
                try:
                    entry = ProjectFile.from_dict(raw)
                except (TypeError, ValueError):
                    continue
                entry.kind = kind
                if not entry.stem or entry.stem in seen:
                    continue
                seen.add(entry.stem)
                entries.append(entry)
            self.data[kind + "s"] = entries

    # ------------------------------------------------------------------
    # 条目
    # ------------------------------------------------------------------
    @property
    def targets(self) -> list[ProjectFile]:
        return self.data["targets"]  # type: ignore[return-value]

    @targets.setter
    def targets(self, value: list[ProjectFile]) -> None:
        self.data["targets"] = value

    @property
    def ligands(self) -> list[ProjectFile]:
        return self.data["ligands"]  # type: ignore[return-value]

    @ligands.setter
    def ligands(self, value: list[ProjectFile]) -> None:
        self.data["ligands"] = value

    def entries(self, kind: str) -> list[ProjectFile]:
        return self.targets if kind == KIND_TARGET else self.ligands

    def get(self, kind: str, stem: str) -> ProjectFile | None:
        for entry in self.entries(kind):
            if entry.stem == stem:
                return entry
        return None

    def require(self, kind: str, stem: str) -> ProjectFile:
        entry = self.get(kind, stem)
        if entry is None:
            label = "靶点" if kind == KIND_TARGET else "配体"
            raise InputError(f"找不到{label}：{stem}")
        return entry

    def add_entry(self, entry: ProjectFile) -> ProjectFile:
        entries = self.entries(entry.kind)
        if any(item.stem == entry.stem for item in entries):
            raise InputError(f"名称重复：{entry.stem}")
        entries.append(entry)
        self.save()
        return entry

    def update_entry(
        self,
        entry: ProjectFile,
        *,
        status: str | None = None,
        prepared: str | None = None,
        box: str | None = None,
        message: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ProjectFile:
        if status is not None:
            entry.status = status
        if prepared is not None:
            entry.prepared = prepared
        if box is not None:
            entry.box = box
        if message is not None:
            entry.message = message
        if meta is not None:
            entry.meta.update(meta)
        self.save()
        return entry

    def set_status(self, entry: ProjectFile, status: str, message: str = "") -> None:
        entry.status = status
        entry.message = message
        self.save()

    # ------------------------------------------------------------------
    # 导入文件
    # ------------------------------------------------------------------
    def _extensions_for(self, kind: str) -> set[str]:
        return TARGET_EXTENSIONS if kind == KIND_TARGET else LIGAND_EXTENSIONS

    def _allocate_stem(
        self,
        kind: str,
        proposed: str,
        digest: str,
        *,
        fallback: str = "",
    ) -> tuple[str, ProjectFile | None]:
        entries = self.entries(kind)
        for entry in entries:
            if entry.meta.get("sha1") == digest:
                return entry.stem, entry
        default = "target" if kind == KIND_TARGET else "ligand"
        # 显示名可能是纯中文，此时用原始文件名生成更易辨识的内部代号。
        base = safe_stem(proposed, safe_stem(fallback, default) if fallback else default)
        if base == default and fallback:
            base = safe_stem(fallback, default)
        existing = {entry.stem for entry in entries}
        if base not in existing:
            return base, None
        index = 2
        while f"{base}_{index}" in existing:
            index += 1
        return f"{base}_{index}", None

    def import_file(
        self,
        kind: str,
        source: str | Path,
        display_name: str = "",
        meta: dict[str, Any] | None = None,
    ) -> ProjectFile:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise InputError(f"文件不存在：{source}")
        suffix = source.suffix.lower()
        if suffix not in self._extensions_for(kind):
            allowed = "、".join(sorted(self._extensions_for(kind)))
            raise InputError(f"不支持的文件格式 {suffix or '(无扩展名)'}。可用格式：{allowed}")
        if source.stat().st_size < 20:
            raise InputError("文件内容为空或过小")

        digest = file_sha1(source)
        stem, duplicate = self._allocate_stem(
            kind, display_name or source.stem, digest, fallback=source.stem
        )
        if duplicate is not None:
            return duplicate

        folder = self.root / ("inputs/targets" if kind == KIND_TARGET else "inputs/ligands")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stem}{suffix}"
        shutil.copy2(source, target)

        entry_meta = {
            "format": suffix.lstrip("."),
            "original_filename": source.name,
            "sha1": digest,
            "added": utc_now(),
            "size": target.stat().st_size,
        }
        entry_meta.update(meta or {})
        entry = ProjectFile(
            kind=kind,
            stem=stem,
            display_name=display_name.strip() or source.stem,
            source=self.relative(target),
            status=STATUS_NEW,
            meta=entry_meta,
        )
        return self.add_entry(entry)

    def import_bytes(
        self,
        kind: str,
        data: bytes,
        filename: str,
        display_name: str = "",
        meta: dict[str, Any] | None = None,
    ) -> ProjectFile:
        if len(data) < 20:
            raise InputError("下载内容为空或过小")
        suffix = Path(filename).suffix.lower()
        if suffix not in self._extensions_for(kind):
            raise InputError(f"不支持的文件格式：{suffix}")
        digest = bytes_sha1(data)
        source_stem = Path(filename).stem
        stem, duplicate = self._allocate_stem(
            kind, display_name or source_stem, digest, fallback=source_stem
        )
        if duplicate is not None:
            return duplicate
        folder = self.root / ("inputs/targets" if kind == KIND_TARGET else "inputs/ligands")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stem}{suffix}"
        target.write_bytes(data)
        entry_meta = {
            "format": suffix.lstrip("."),
            "original_filename": filename,
            "sha1": digest,
            "added": utc_now(),
            "size": len(data),
        }
        entry_meta.update(meta or {})
        entry = ProjectFile(
            kind=kind,
            stem=stem,
            display_name=display_name.strip() or Path(filename).stem,
            source=self.relative(target),
            status=STATUS_NEW,
            meta=entry_meta,
        )
        return self.add_entry(entry)

    def sync_inputs(self, kind: str) -> list[ProjectFile]:
        """扫描 inputs 目录，把用户手动放入的文件登记到项目中。"""
        folder = self.root / ("inputs/targets" if kind == KIND_TARGET else "inputs/ligands")
        if not folder.is_dir():
            return []
        known = {Path(entry.source).name for entry in self.entries(kind)}
        added: list[ProjectFile] = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in self._extensions_for(kind) or path.name in known:
                continue
            try:
                digest = file_sha1(path)
                stem, duplicate = self._allocate_stem(kind, path.stem, digest)
                if duplicate is not None:
                    continue
                entry = ProjectFile(
                    kind=kind,
                    stem=stem,
                    display_name=path.stem,
                    source=self.relative(path),
                    status=STATUS_NEW,
                    meta={
                        "format": path.suffix.lstrip("."),
                        "original_filename": path.name,
                        "sha1": digest,
                        "added": utc_now(),
                        "size": path.stat().st_size,
                        "imported_by_scan": True,
                    },
                )
                added.append(self.add_entry(entry))
            except InputError:
                continue
        return added

    def remove_entry(self, entry: ProjectFile, *, purge_results: bool = False) -> None:
        kind = entry.kind
        entries = self.entries(kind)
        if entry in entries:
            entries.remove(entry)
        # 输入文件
        source = self.absolute(entry.source) if entry.source else None
        if source and source.is_file() and self._is_inside(source):
            source.unlink(missing_ok=True)
        # 预处理产物
        prep_dir = self.prepared_dir(kind, entry.stem)
        if prep_dir.is_dir() and self._is_inside(prep_dir):
            shutil.rmtree(prep_dir, ignore_errors=True)
        box = self.box_path(entry.stem)
        if kind == KIND_TARGET and box.is_file():
            box.unlink(missing_ok=True)
        if purge_results and kind == KIND_TARGET:
            for folder in (self.docking_dir(entry.stem), self.complex_dir(entry.stem)):
                if folder.is_dir() and self._is_inside(folder):
                    shutil.rmtree(folder, ignore_errors=True)
        self.save()

    # ------------------------------------------------------------------
    # 路径布局
    # ------------------------------------------------------------------
    def relative(self, path: str | Path) -> str:
        path = Path(path).resolve()
        if not self._is_inside(path):
            raise InputError(f"路径不在工作目录内：{path}")
        return path.relative_to(self.root).as_posix()

    def absolute(self, relative: str | Path) -> Path:
        candidate = (self.root / Path(relative)).resolve()
        if not self._is_inside(candidate):
            raise InputError(f"路径越出工作目录：{relative}")
        return candidate

    def _is_inside(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def prepared_dir(self, kind: str, stem: str) -> Path:
        group = "targets" if kind == KIND_TARGET else "ligands"
        return self.root / "prepared" / group / stem

    def prepared_target_pdb(self, stem: str) -> Path:
        return self.prepared_dir(KIND_TARGET, stem) / f"{stem}_prepared.pdb"

    def prepared_target_pdbqt(self, stem: str) -> Path:
        return self.prepared_dir(KIND_TARGET, stem) / f"{stem}.pdbqt"

    def prepared_ligand_pdbqt(self, stem: str) -> Path:
        return self.prepared_dir(KIND_LIGAND, stem) / f"{stem}.pdbqt"

    def prepared_ligand_sdf(self, stem: str) -> Path:
        return self.prepared_dir(KIND_LIGAND, stem) / f"{stem}_prepared.sdf"

    def box_path(self, target_stem: str) -> Path:
        return self.root / "boxes" / f"{target_stem}.json"

    def save_box(self, target_stem: str, box: Box) -> Path:
        path = self.box_path(target_stem)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(box.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load_box(self, target_stem: str) -> Box | None:
        path = self.box_path(target_stem)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Box.from_dict(data)
        except (OSError, ValueError, TypeError):
            return None

    def docking_dir(self, target_stem: str) -> Path:
        return self.root / "docking" / target_stem

    def docking_output(self, target_stem: str, ligand_stem: str) -> Path:
        return self.docking_dir(target_stem) / f"{ligand_stem}.pdbqt"

    def docking_log(self, target_stem: str, ligand_stem: str) -> Path:
        return self.docking_dir(target_stem) / f"{ligand_stem}.log"

    def docking_meta(self, target_stem: str, ligand_stem: str) -> Path:
        """对接结果的元数据（耗时、盒子来源、警告），便于重新打开项目后复原。"""
        return self.docking_dir(target_stem) / f"{ligand_stem}.json"

    def complex_dir(self, target_stem: str) -> Path:
        return self.root / "complexes" / target_stem

    def complex_path(self, target_stem: str, ligand_stem: str) -> Path:
        return self.complex_dir(target_stem) / f"{ligand_stem}.pdb"

    def pymol_path(self, target_stem: str, ligand_stem: str) -> Path:
        return self.root / "pymol" / f"{target_stem}__{ligand_stem}.pml"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def report_dir(self) -> Path:
        return self.root / "report"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    # ------------------------------------------------------------------
    # 状态与统计
    # ------------------------------------------------------------------
    def mark_missing_sources(self) -> list[ProjectFile]:
        missing: list[ProjectFile] = []
        for entry in list(self.targets) + list(self.ligands):
            if entry.source and not self.absolute(entry.source).is_file():
                entry.status = STATUS_FAILED
                entry.message = "源文件已被移动或删除"
                missing.append(entry)
        if missing:
            self.save()
        return missing

    def count_ready(self, kind: str) -> int:
        return sum(1 for entry in self.entries(kind) if entry.is_ready)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": str(self.root),
            "targets": len(self.targets),
            "ligands": len(self.ligands),
            "ready_targets": self.count_ready(KIND_TARGET),
            "ready_ligands": self.count_ready(KIND_LIGAND),
            "updated": self.data.get("updated", ""),
        }

    def clear_prepared(self, entry: ProjectFile) -> None:
        folder = self.prepared_dir(entry.kind, entry.stem)
        if folder.is_dir() and self._is_inside(folder):
            shutil.rmtree(folder, ignore_errors=True)
        entry.prepared = ""
        entry.status = STATUS_NEW
        entry.message = ""
        self.save()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Workspace({self.root!s})"
