"""柔性残基（flexible residues）选择与受体拆分。

Vina 与 Smina 允许把结合口袋附近的若干氨基酸侧链当作可动部分，与配体一起
采样。这样能缓解“受体按刚性处理”带来的假冲突，但每增加一个柔性残基都会
显著增加搜索空间，因此这里限制最多 ``MAX_FLEX_RESIDUES`` 个。

拆分工作交给 Meeko 的 ``mk_prepare_receptor``：它把选中的残基从受体中取出，
分别写成刚性受体 PDBQT 与柔性残基 PDBQT。程序内的调用是进程内的，打包后
同样可用（不需要额外的可执行文件）。
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core import pdbio
from ..core.models import ProjectFile
from ..errors import DockFlowError
from ..logging_setup import get_logger

log = get_logger("flexible")

#：一次对接中允许柔化的残基上限。超过后搜索空间与耗时增长过快，
#：而且 Vina 的评分函数并不适合同时柔化大量侧链。
MAX_FLEX_RESIDUES = 12
#：自动选择时只考虑盒子中心附近的残基（Å）。
DEFAULT_AUTO_RADIUS = 6.0
#：手动选择的列表半径（Å），比自动选择的半径宽一些。
LIST_RADIUS = 12.0

BACKBONE_ATOMS = {
    "N", "CA", "C", "O", "OXT", "H", "H1", "H2", "H3", "HA", "HA2", "HA3",
}
#：侧链没有可旋转重原子的残基，柔化没有意义。
_RIGID_SIDECHAINS = {
    "GLY": "没有侧链",
    "ALA": "侧链只有一个甲基，柔化意义不大",
    "PRO": "侧链成环，Meeko 通常不会产生可动原子",
}


class FlexibleResidueError(DockFlowError):
    """柔性残基准备失败（Meeko 不可用、模板缺失等）。"""


@dataclass(frozen=True)
class FlexResidue:
    """一个可选择的氨基酸残基。``key`` 与 Meeko 的 ``-f chain:num`` 对应。"""

    chain: str
    resseq: int
    icode: str = ""
    resname: str = ""

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.resseq}{self.icode}"

    @property
    def label(self) -> str:
        chain = self.chain.strip()
        number = f"{chain}{self.resseq}{self.icode}" if chain else f"{self.resseq}{self.icode}"
        return f"{self.resname} {number}".strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "resseq": int(self.resseq),
            "icode": self.icode,
            "resname": self.resname,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlexResidue | None":
        try:
            resseq = int(data.get("resseq"))
        except (TypeError, ValueError):
            return None
        return cls(
            chain=str(data.get("chain") or ""),
            resseq=resseq,
            icode=str(data.get("icode") or ""),
            resname=str(data.get("resname") or ""),
        )


@dataclass
class ResidueCandidate:
    """界面列表中展示的一条记录。"""

    residue: FlexResidue
    distance: float
    sidechain_atoms: int
    rotatable: bool
    note: str = ""

    @property
    def chain(self) -> str:
        return self.residue.chain

    @property
    def resname(self) -> str:
        return self.residue.resname

    @property
    def resseq(self) -> int:
        return self.residue.resseq


@dataclass
class FlexReceptor:
    """拆分后的刚性受体与柔性残基文件。"""

    rigid: Path
    flex: Path
    residues: tuple[FlexResidue, ...]
    omitted: tuple[str, ...] = ()
    cached: bool = False

    @property
    def labels(self) -> str:
        return "、".join(item.label for item in self.residues)

    @property
    def count(self) -> int:
        return len(self.residues)


def residue_key_list(residues: Iterable[FlexResidue]) -> str:
    return ",".join(sorted(item.key for item in residues))


def selected_residues(target: ProjectFile) -> list[FlexResidue]:
    """读取靶点 meta 中保存的柔性残基选择。"""
    raw = target.meta.get("flex_residues")
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[FlexResidue] = []
    for item in raw:
        if isinstance(item, dict):
            residue = FlexResidue.from_dict(item)
            if residue is not None:
                result.append(residue)
    return result


def save_selection(target: ProjectFile, residues: Sequence[FlexResidue]) -> None:
    """把选择写回 meta；为空时删除该字段，保持旧项目结构不变。"""
    if residues:
        target.meta["flex_residues"] = [item.as_dict() for item in residues]
    else:
        target.meta.pop("flex_residues", None)


def residue_candidates(
    pdb_path: str | Path,
    center: Sequence[float] | None = None,
    *,
    radius: float = LIST_RADIUS,
) -> list[ResidueCandidate]:
    """列出中心附近可用于柔化的氨基酸残基。

    ``center`` 为空时返回全部聚合物残基（按链、序号排序），供用户手动挑选。
    """
    pdb_path = Path(pdb_path)
    if not pdb_path.is_file():
        return []
    try:
        atoms = pdbio.parse_atoms(pdb_path, first_model=True)
    except Exception as exc:  # 结构损坏时宁可空列表，也不要阻断界面
        log.warning("读取残基列表失败：%s", exc)
        return []

    groups: dict[tuple[str, int, str, str], list[pdbio.Atom]] = {}
    for atom in atoms:
        if atom.is_hydrogen:
            continue
        if atom.altloc not in ("", " ", "A"):
            continue
        if not pdbio.is_polymer_residue(atom.resname):
            continue
        groups.setdefault(atom.residue_key, []).append(atom)

    candidates: list[ResidueCandidate] = []
    for key, residue_atoms in groups.items():
        chain, resseq, icode, resname = key
        sidechain = [
            atom for atom in residue_atoms if atom.name.strip().upper() not in BACKBONE_ATOMS
        ]
        if center is not None and len(center) == 3:
            distance = min(
                pdbio.distance(atom.coord, center) for atom in residue_atoms
            )
            if distance > radius:
                continue
        else:
            distance = 0.0
        note = _RIGID_SIDECHAINS.get(resname, "")
        rotatable = not note and len(sidechain) >= 2
        candidates.append(
            ResidueCandidate(
                residue=FlexResidue(chain=chain, resseq=resseq, icode=icode, resname=resname),
                distance=distance,
                sidechain_atoms=len(sidechain),
                rotatable=rotatable,
                note=note or ("侧链可旋转" if rotatable else "侧链原子过少"),
            )
        )
    candidates.sort(key=lambda item: (item.distance, item.residue.chain, item.residue.resseq))
    return candidates


def auto_select(
    candidates: Sequence[ResidueCandidate],
    *,
    limit: int = MAX_FLEX_RESIDUES,
    radius: float = DEFAULT_AUTO_RADIUS,
) -> list[FlexResidue]:
    """按到盒子中心的距离挑选可旋转侧链，最多 ``limit`` 个。"""
    pool = [
        item for item in candidates
        if item.rotatable and item.distance <= radius
    ]
    if not pool:
        # 盒子中心附近没有可旋转侧链时退一步：取最近的若干残基，
        # 由用户在列表里自行判断，而不是直接返回“无法自动选择”。
        pool = [item for item in candidates if item.rotatable]
    return [item.residue for item in pool[: max(0, limit)]]


def _cache_key(pdb_path: Path, residues: Sequence[FlexResidue]) -> str:
    digest = hashlib.sha1()
    try:
        stat = pdb_path.stat()
        digest.update(f"{stat.st_size}:{int(stat.st_mtime)}".encode("ascii"))
    except OSError:
        pass
    try:
        digest.update(pdb_path.read_bytes()[:4096])
    except OSError:
        pass
    digest.update(residue_key_list(residues).encode("utf-8"))
    return digest.hexdigest()[:12]


def _run_mk_prepare_receptor(work_dir: Path, arguments: list[str]) -> str:
    """在进程内调用 Meeko 的受体准备脚本，返回其标准输出。

    直接调用模块而不是外部可执行文件：打包后的程序没有 conda 的
    ``Scripts`` 目录，但 Meeko 已经作为依赖被打进程序里。
    """
    try:
        from meeko.cli import mk_prepare_receptor as mk
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise FlexibleResidueError(
            "未安装 Meeko，无法拆分柔性残基。请重新安装完整依赖后重试。"
        ) from exc

    buffer = io.StringIO()
    previous_argv = sys.argv
    previous_cwd = Path.cwd()
    try:
        os.chdir(work_dir)
        sys.argv = ["mk_prepare_receptor", *arguments]
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                mk.main()
            except SystemExit as exc:  # argparse 失败时以非零码退出
                if exc.code not in (0, None):
                    raise FlexibleResidueError(
                        "Meeko 无法拆分柔性残基：" + _tail(buffer.getvalue())
                    ) from exc
    except FlexibleResidueError:
        raise
    except Exception as exc:
        raise FlexibleResidueError(
            f"Meeko 拆分柔性残基失败：{exc}\n{_tail(buffer.getvalue())}"
        ) from exc
    finally:
        sys.argv = previous_argv
        os.chdir(previous_cwd)
    return buffer.getvalue()


def _tail(text: str, lines: int = 4) -> str:
    items = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return "；".join(items[-lines:])


def _omitted_residues(output: str) -> tuple[str, ...]:
    omitted: list[str] = []
    for line in output.splitlines():
        marker = "has no movable atoms"
        if marker in line:
            omitted.append(line.split()[2] if len(line.split()) > 2 else line.strip())
    return tuple(omitted)


def prepare_flexible_receptor(
    prepared_pdb: str | Path,
    residues: Sequence[FlexResidue],
    output_dir: str | Path,
    *,
    basename: str = "receptor",
) -> FlexReceptor:
    """把选中的残基从 ``prepared_pdb`` 中拆出，返回两组 PDBQT 路径。"""
    prepared_pdb = Path(prepared_pdb)
    output_dir = Path(output_dir)
    residues = list(dict.fromkeys(residues))
    if not residues:
        raise FlexibleResidueError("没有选择任何柔性残基")
    if len(residues) > MAX_FLEX_RESIDUES:
        raise FlexibleResidueError(
            f"柔性残基最多 {MAX_FLEX_RESIDUES} 个，当前选择了 {len(residues)} 个"
        )
    if not prepared_pdb.is_file():
        raise FlexibleResidueError("找不到准备好的靶点 PDB 文件，请重新准备靶点")

    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / f"work_{basename}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    local_pdb = work / "receptor.pdb"
    shutil.copy2(prepared_pdb, local_pdb)

    arguments = ["--read_pdb", "receptor.pdb", "-o", basename, "-p"]
    for residue in residues:
        arguments.extend(["-f", residue.key])
    output = _run_mk_prepare_receptor(work, arguments)

    rigid = work / f"{basename}_rigid.pdbqt"
    flex = work / f"{basename}_flex.pdbqt"
    valid, message, _ = pdbio.validate_pdbqt(rigid, receptor=True)
    if not valid:
        raise FlexibleResidueError(f"刚性受体生成失败：{message}")
    if not flex.is_file() or flex.stat().st_size < 80:
        raise FlexibleResidueError(
            "没有生成柔性残基文件。选中的残基可能都没有可移动的侧链原子，"
            "请换一些侧链可旋转的残基。"
        )
    return FlexReceptor(
        rigid=rigid,
        flex=flex,
        residues=tuple(residues),
        omitted=_omitted_residues(output),
    )


def flex_output_dir(workspace: Any, target_stem: str) -> Path:
    return workspace.prepared_target_flex_dir(target_stem)


def ensure_flexible_receptor(
    workspace: Any,
    target: ProjectFile,
    *,
    reload: bool = False,
) -> FlexReceptor | None:
    """按当前选择准备（并缓存）拆分好的受体；没有选择时返回 ``None``。"""
    residues = selected_residues(target)
    if not residues:
        return None
    prepared_pdb = workspace.prepared_target_pdb(target.stem)
    if not prepared_pdb.is_file():
        raw = str(target.meta.get("prepared_pdb") or "")
        if raw:
            candidate = workspace.absolute(raw)
            if candidate.is_file():
                prepared_pdb = candidate
    if not prepared_pdb.is_file():
        raise FlexibleResidueError("找不到准备好的靶点结构，请先在“检查与准备”中准备靶点")

    output_dir = flex_output_dir(workspace, target.stem)
    key = _cache_key(prepared_pdb, residues)
    rigid = output_dir / f"rigid_{key}.pdbqt"
    flex = output_dir / f"flex_{key}.pdbqt"
    if not reload and rigid.is_file() and flex.is_file():
        return FlexReceptor(rigid=rigid, flex=flex, residues=tuple(residues), cached=True)

    built = prepare_flexible_receptor(
        prepared_pdb, residues, output_dir, basename="receptor"
    )
    rigid.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(built.rigid), str(rigid))
    shutil.move(str(built.flex), str(flex))
    shutil.rmtree(output_dir / "work_receptor", ignore_errors=True)
    _prune_cache(output_dir, keep={rigid.name, flex.name})
    return FlexReceptor(
        rigid=rigid,
        flex=flex,
        residues=tuple(residues),
        omitted=built.omitted,
    )


def _prune_cache(output_dir: Path, keep: set[str]) -> None:
    """删除旧的拆分文件，避免项目目录随着每次改动累积。"""
    try:
        for path in output_dir.glob("*.pdbqt"):
            if path.name not in keep:
                path.unlink(missing_ok=True)
    except OSError:  # 清理失败不影响本次对接
        pass


def clear_cache(workspace: Any, target_stem: str) -> None:
    shutil.rmtree(flex_output_dir(workspace, target_stem), ignore_errors=True)
