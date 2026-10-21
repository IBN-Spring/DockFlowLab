"""支持的分子对接场景（模式）注册表。

用户不需要先选“模式”。程序根据每个靶点与候选分子的结构类型自动判断
这一组能不能对接、使用哪个引擎，以及需要提醒哪些科学限制。

当前使用 AutoDock Vina 的场景：

* 蛋白–小分子
* 多肽–小分子
* 核酸（DNA/RNA）–小分子
* 小分子–小分子（目标分子按刚性受体处理）
* 蛋白–多肽（多肽作为柔性配体）

本地版本不提供蛋白–蛋白对接：Vina 的评分函数与搜索算法并不适合大分子
界面，给出一个看似精确的分数反而会误导。此类组合会明确标记为不支持，
并引导到 ClusPro、HADDOCK、RosettaDock 等专门工具。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..core import pdbio
from ..core.models import KIND_LIGAND, ProjectFile

KIND_LABELS = {
    "protein": "蛋白质",
    "peptide": "多肽/短肽",
    "nucleic_acid": "核酸（DNA/RNA）",
    "small_molecule": "小分子",
    "macromolecule": "大分子",
    "unknown": "未识别结构",
}

# 配体角色下允许的多肽上限。超过上限按完整蛋白质处理，避免把蛋白–蛋白
# 对接伪装成“多肽对接”。目标角色仍使用 pdbio 的默认阈值（20 个残基）。
LIGAND_PEPTIDE_MAX_RESIDUES = 50
LIGAND_PEPTIDE_MAX_HEAVY_ATOMS = 600

_VALID_KINDS = set(KIND_LABELS)
_ALIASES = {
    "dna": "nucleic_acid",
    "rna": "nucleic_acid",
    "nucleic": "nucleic_acid",
    "nucleotide": "nucleic_acid",
    "protein_complex": "protein",
    "complex": "protein",
    "peptide_ligand": "peptide",
    "small": "small_molecule",
}


@dataclass(frozen=True)
class DockingMode:
    """一种靶点–配体组合的处理方式。"""

    key: str
    label: str
    target_kind: str
    ligand_kind: str
    supported: bool
    summary: str
    warnings: tuple[str, ...] = ()
    guidance: str = ""

    @property
    def target_label(self) -> str:
        return KIND_LABELS.get(self.target_kind, self.target_kind)

    @property
    def ligand_label(self) -> str:
        return KIND_LABELS.get(self.ligand_kind, self.ligand_kind)

    @property
    def error_text(self) -> str:
        if self.supported:
            return ""
        return f"不支持的对接类型：{self.label}。{self.guidance}"


def _mode(
    key: str,
    label: str,
    target_kind: str,
    ligand_kind: str,
    summary: str,
    *,
    warnings: Iterable[str] = (),
    guidance: str = "",
) -> DockingMode:
    return DockingMode(
        key=key,
        label=label,
        target_kind=target_kind,
        ligand_kind=ligand_kind,
        supported=True,
        summary=summary,
        warnings=tuple(warnings),
        guidance=guidance,
    )


PROTEIN_SMALL_MOLECULE = _mode(
    "protein_small_molecule",
    "蛋白–小分子",
    "protein",
    "small_molecule",
    "AutoDock Vina 将小分子对接到蛋白质的结合口袋。",
)

PEPTIDE_SMALL_MOLECULE = _mode(
    "peptide_small_molecule",
    "多肽–小分子",
    "peptide",
    "small_molecule",
    "AutoDock Vina 将小分子对接到多肽/短肽结构。",
    warnings=(
        "靶点是多肽/短肽，构象柔性通常高于完整蛋白；"
        "建议优先使用实验结构，并结合文献或实验验证结合位点。",
    ),
)

NUCLEIC_ACID_SMALL_MOLECULE = _mode(
    "nucleic_acid_small_molecule",
    "核酸–小分子",
    "nucleic_acid",
    "small_molecule",
    "AutoDock Vina 将小分子对接到 DNA/RNA 结构。",
    warnings=(
        "Vina 评分函数主要基于蛋白–配体数据训练，用于核酸靶点时应只做相对排序，"
        "不宜把分数解释为绝对结合亲和力。",
        "核酸的口袋较浅且高度溶剂化；建议优先参考共晶配体、NMR 结构或已知结合位点。",
    ),
)

SMALL_MOLECULE_SMALL_MOLECULE = _mode(
    "small_molecule_small_molecule",
    "小分子–小分子",
    "small_molecule",
    "small_molecule",
    "以一个小分子为刚性目标，将另一个小分子对接到它周围。",
    warnings=(
        "小分子目标按刚性受体处理，构象来自输入结构或能量最小化结果；"
        "本模式只适合快速筛选，结合模式与亲和力需要更高精度方法验证。",
    ),
)

PROTEIN_PEPTIDE = _mode(
    "protein_peptide",
    "蛋白–多肽",
    "protein",
    "peptide",
    "将多肽作为柔性配体对接到蛋白质表面。",
    warnings=(
        "多肽配体柔性很高，Vina 的搜索空间可能不足，结果应视为初步筛选；"
        "关键结论建议用 HADDOCK、ClusPro 或实验方法验证。",
    ),
)


def _unsupported(target_kind: str, ligand_kind: str) -> DockingMode:
    target_label = KIND_LABELS.get(target_kind, target_kind)
    ligand_label = KIND_LABELS.get(ligand_kind, ligand_kind)
    label = f"{target_label}–{ligand_label}"

    if target_kind == "protein" and ligand_kind == "protein":
        guidance = (
            "Vina 的评分函数和搜索算法不适用于蛋白–蛋白界面。"
            "请使用 ClusPro、HADDOCK、RosettaDock 等专门的蛋白对接工具。"
        )
    elif target_kind == "nucleic_acid" and ligand_kind in {"protein", "peptide", "nucleic_acid"}:
        guidance = (
            "核酸与蛋白/多肽/核酸之间的大分子对接需要专门的核酸–蛋白或核酸–核酸对接工具，"
            "本地 Vina 流程不提供这类计算。"
        )
    elif target_kind == "small_molecule" and ligand_kind != "small_molecule":
        guidance = (
            "当前只支持小分子作为目标时对接另一个小分子。"
            "若要做蛋白–蛋白或蛋白–核酸对接，请使用专门的工具。"
        )
    elif ligand_kind == "nucleic_acid":
        guidance = (
            "核酸不能作为本流程中的候选分子。"
            "小分子与核酸的对接请把核酸放在“靶点结构”一栏。"
        )
    elif target_kind == "peptide" and ligand_kind == "peptide":
        guidance = (
            "多肽–多肽对接的搜索空间远大于 Vina 能可靠处理的范围。"
            "请使用 HADDOCK、RosettaDock 等专门的蛋白/多肽对接工具。"
        )
    elif target_kind == "unknown" or ligand_kind == "unknown":
        guidance = (
            "至少一个结构无法识别类型。请确认文件包含有效的蛋白、核酸、多肽或小分子，"
            "并先完成“检查与准备”。"
        )
    else:
        guidance = (
            "该组合不在本地支持的对接场景内。"
            "可尝试把较小的一方作为候选分子，或使用专门的大分子对接工具。"
        )
    return DockingMode(
        key=f"unsupported_{target_kind}_{ligand_kind}",
        label=label,
        target_kind=target_kind,
        ligand_kind=ligand_kind,
        supported=False,
        summary=f"{label}组合在本地版本中不执行 Vina 对接。",
        guidance=guidance,
    )


_MODES: dict[tuple[str, str], DockingMode] = {
    (mode.target_kind, mode.ligand_kind): mode
    for mode in (
        PROTEIN_SMALL_MOLECULE,
        PEPTIDE_SMALL_MOLECULE,
        NUCLEIC_ACID_SMALL_MOLECULE,
        SMALL_MOLECULE_SMALL_MOLECULE,
        PROTEIN_PEPTIDE,
    )
}

MODES_BY_KEY = {mode.key: mode for mode in _MODES.values()}


def normalize_kind(kind: Any, *, role: str = "") -> str:
    """把元数据中的结构类型规范化为模式注册表使用的值。"""
    value = str(kind or "").strip().lower()
    value = _ALIASES.get(value, value)
    if value in _VALID_KINDS:
        if value == "macromolecule":
            # 旧项目/手工 PDBQT 没有更细的类型信息，按蛋白质处理；
            # 如果能从文件重新识别，entry_kind() 会优先使用识别结果。
            return "macromolecule"
        return value
    if not value:
        return ""
    return "unknown"


def _candidate_paths(entry: ProjectFile, workspace: Any | None) -> list[Path]:
    paths: list[Path] = []
    for raw in (entry.prepared, entry.source):
        if not raw:
            continue
        try:
            path = workspace.absolute(raw) if workspace is not None else Path(raw)
        except Exception:
            continue
        paths.append(Path(path))
    return paths


def _format_kind(entry: ProjectFile) -> str:
    fmt = str(entry.meta.get("format", "")).strip().lower()
    if fmt in {"sdf", "sd", "mol", "smi", "smiles", "inchi"}:
        return "small_molecule"
    if fmt == "mol2":
        # MOL2 既可能是小分子也可能来自大分子；MOL2 内容无法由轻量解析器
        # 直接分类，先留空，交给下面的结构识别或准备阶段。
        return ""
    return ""


@lru_cache(maxsize=256)
def _cached_kind(
    path: str,
    mtime_ns: int,
    size: int,
    peptide_residues: int,
    peptide_atoms: int,
) -> str:
    """按文件时间戳/大小缓存结构识别结果，避免界面重复解析大结构。"""
    del mtime_ns, size  # 仅用于缓存键
    try:
        kind, _ = pdbio.structure_kind(
            path,
            peptide_max_residues=peptide_residues,
            peptide_max_heavy_atoms=peptide_atoms,
        )
    except Exception:
        return "unknown"
    return normalize_kind(kind)


def entry_kind(entry: ProjectFile, workspace: Any | None = None) -> str:
    """返回条目的结构类型；优先使用准备时写入的 ``meta["kind"]``。"""
    stored = normalize_kind(entry.meta.get("kind"))
    if stored and stored not in {"macromolecule", "unknown"}:
        return stored

    is_ligand = entry.kind == KIND_LIGAND
    peptide_residues = LIGAND_PEPTIDE_MAX_RESIDUES if is_ligand else 20
    peptide_atoms = LIGAND_PEPTIDE_MAX_HEAVY_ATOMS if is_ligand else 500
    for path in _candidate_paths(entry, workspace):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        normalized = _cached_kind(
            str(path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            peptide_residues,
            peptide_atoms,
        )
        if normalized and normalized != "unknown":
            if normalized == "macromolecule":
                return "protein"
            # 配体角色下，小分子文件不应被误判成目标大分子。
            return normalized

    fmt_kind = _format_kind(entry)
    if fmt_kind:
        return fmt_kind
    if stored == "macromolecule":
        return "protein"
    return "unknown"


def target_kind_for(target: ProjectFile, workspace: Any | None = None) -> str:
    kind = entry_kind(target, workspace)
    return "protein" if kind == "macromolecule" else kind


def ligand_kind_for(ligand: ProjectFile, workspace: Any | None = None) -> str:
    kind = entry_kind(ligand, workspace)
    return "protein" if kind == "macromolecule" else kind


def resolve(target_kind: str, ligand_kind: str) -> DockingMode:
    """按靶点/配体类型解析出对接模式（包括不支持的组合）。"""
    target = normalize_kind(target_kind) or "unknown"
    ligand = normalize_kind(ligand_kind) or "unknown"
    if target == "macromolecule":
        target = "protein"
    if ligand == "macromolecule":
        ligand = "protein"
    mode = _MODES.get((target, ligand))
    if mode is not None:
        return mode
    return _unsupported(target, ligand)


def pair_mode(
    target: ProjectFile,
    ligand: ProjectFile,
    workspace: Any | None = None,
) -> DockingMode:
    return resolve(target_kind_for(target, workspace), ligand_kind_for(ligand, workspace))


def supported_pairs(
    targets: Iterable[ProjectFile],
    ligands: Iterable[ProjectFile],
    workspace: Any | None = None,
) -> list[tuple[ProjectFile, ProjectFile, DockingMode]]:
    pairs = []
    for target in targets:
        for ligand in ligands:
            mode = pair_mode(target, ligand, workspace)
            if mode.supported:
                pairs.append((target, ligand, mode))
    return pairs


def unsupported_pairs(
    targets: Iterable[ProjectFile],
    ligands: Iterable[ProjectFile],
    workspace: Any | None = None,
) -> list[tuple[ProjectFile, ProjectFile, DockingMode]]:
    pairs = []
    for target in targets:
        for ligand in ligands:
            mode = pair_mode(target, ligand, workspace)
            if not mode.supported:
                pairs.append((target, ligand, mode))
    return pairs
