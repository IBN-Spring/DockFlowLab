"""晶体结构体检：在准备受体之前提示容易被忽略的结构问题。

解析对象是 PDB 文本，不依赖 PDBFixer/OpenBabel：

* ``REMARK 350``：生物学装配（BIOMT 变换），提示当前文件可能只是不对称单元；
* ``REMARK 465`` / ``REMARK 470``：缺失残基与缺失原子；
* altloc、occupancy、非标准残基、金属离子、水。

这里只做提示，不自动应用 BIOMT，也不修改结构。2.5.0 的策略是让用户
知道“下载到的坐标”和“真实生物学单元”可能不是一回事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import pdbio

_BIOMT_RE = re.compile(r"^REMARK\s+350\s+BIOMT[123]\s+(\d+)\b", re.IGNORECASE)
_BIOMOLECULE_RE = re.compile(
    r"^REMARK\s+350\s+BIOMOLECULE:\s*(.+)$", re.IGNORECASE
)
_BIOLOGICAL_UNIT_RE = re.compile(
    r"^REMARK\s+350\s+(?:AUTHOR|SOFTWARE)\s+DETERMINED\s+BIOLOGICAL\s+UNIT:\s*(.+)$",
    re.IGNORECASE,
)
_MISSING_RESIDUE_RE = re.compile(
    r"^REMARK\s+465\s+(\S{1,3})\s+(\S)\s+(-?\d+)([A-Za-z0-9]?)\s*$"
)
_MISSING_ATOM_RE = re.compile(
    r"^REMARK\s+470\s+(\S{1,3})\s+(\S)\s+(-?\d+)([A-Za-z0-9]?)\s+(.+)$"
)

_OLIGOMER_WORDS = (
    "DIMER",
    "TRIMER",
    "TETRAMER",
    "PENTAMER",
    "HEXAMER",
    "HEPTAMER",
    "OCTAMER",
    "MULTIMER",
    "OLIGOMER",
)


@dataclass(frozen=True)
class MissingResidue:
    resname: str
    chain: str
    resseq: int
    icode: str = ""

    @property
    def label(self) -> str:
        chain = f" {self.chain}" if self.chain else ""
        return f"{self.resname}{chain}{self.resseq}{self.icode}".strip()


@dataclass(frozen=True)
class MissingAtom:
    resname: str
    chain: str
    resseq: int
    icode: str
    atoms: tuple[str, ...]

    @property
    def label(self) -> str:
        chain = f" {self.chain}" if self.chain else ""
        return f"{self.resname}{chain}{self.resseq}{self.icode}".strip()


@dataclass
class StructureQC:
    """一个结构文件的体检结果。"""

    path: str = ""
    models: int = 1
    biomolecules: int = 0
    biomt_transforms: int = 0
    biological_unit: str = ""
    missing_residues: tuple[MissingResidue, ...] = ()
    missing_atoms: tuple[MissingAtom, ...] = ()
    altloc_atoms: int = 0
    altloc_labels: tuple[str, ...] = ()
    low_occupancy_atoms: int = 0
    nonstandard_residues: tuple[str, ...] = ()
    metals: tuple[str, ...] = ()
    waters: int = 0
    additives: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 派生信息
    # ------------------------------------------------------------------
    @property
    def missing_residue_count(self) -> int:
        return len(self.missing_residues)

    @property
    def missing_atom_count(self) -> int:
        return sum(len(item.atoms) for item in self.missing_atoms)

    @property
    def is_multimer(self) -> bool:
        if self.biomt_transforms > 1:
            return True
        unit = self.biological_unit.upper()
        return any(word in unit for word in _OLIGOMER_WORDS)

    @property
    def has_findings(self) -> bool:
        return bool(
            self.is_multimer
            or self.missing_residues
            or self.missing_atoms
            or self.altloc_atoms
            or self.low_occupancy_atoms
            or self.nonstandard_residues
            or self.metals
        )

    # ------------------------------------------------------------------
    # 文本输出
    # ------------------------------------------------------------------
    def warnings(self) -> list[str]:
        """面向准备流程的简短警告；只报告会影响解释结果的项。"""
        items: list[str] = []
        if self.is_multimer:
            unit = f"（标注为 {self.biological_unit}）" if self.biological_unit else ""
            transforms = (
                f"，共 {self.biomt_transforms} 个 BIOMT 变换"
                if self.biomt_transforms > 1
                else ""
            )
            items.append(
                f"结构可能是多聚体{unit}{transforms}；当前只使用文件中的不对称单元。"
                "如果功能单元是多聚体，请先下载/应用生物学装配后再对接。"
            )
        if self.missing_residues:
            names = "、".join(item.label for item in self.missing_residues[:6])
            more = " 等" if len(self.missing_residues) > 6 else ""
            items.append(
                f"结构标注了 {len(self.missing_residues)} 个缺失残基"
                f"（REMARK 465：{names}{more}），补全区域的结构可靠性有限。"
            )
        if self.missing_atoms:
            items.append(
                f"结构标注了 {self.missing_atom_count} 个缺失原子"
                f"（REMARK 470，涉及 {len(self.missing_atoms)} 个残基）。"
            )
        if self.altloc_atoms:
            items.append(
                f"有 {self.altloc_atoms} 个非首选 altloc 原子未被使用"
                "（默认保留 A/1 构象）；若关键残基存在多构象，请检查选择。"
            )
        if self.low_occupancy_atoms:
            items.append(
                f"有 {self.low_occupancy_atoms} 个原子 occupancy < 0.5，"
                "位置不确定性较高。"
            )
        if self.nonstandard_residues:
            names = "、".join(self.nonstandard_residues[:8])
            more = " 等" if len(self.nonstandard_residues) > 8 else ""
            items.append(
                f"结构含非标准/杂原子残基：{names}{more}；"
                "准备时默认会移除，除非勾选保留金属或辅因子。"
            )
        return items

    def summary_lines(self) -> list[str]:
        """报告/界面用的完整摘要。"""
        lines: list[str] = []
        lines.append(f"模型数：{self.models}")
        if self.is_multimer:
            unit = self.biological_unit or "未注明"
            lines.append(
                f"生物学装配：{unit}；REMARK 350 变换 {self.biomt_transforms} 个"
            )
        elif self.biomt_transforms:
            lines.append(
                f"生物学装配：{self.biological_unit or '与不对称单元一致'}"
                f"（{self.biomt_transforms} 个变换）"
            )
        lines.append(
            f"缺失残基：{len(self.missing_residues)}；"
            f"缺失原子：{self.missing_atom_count}"
        )
        lines.append(
            f"altloc 非首选原子：{self.altloc_atoms}；"
            f"低 occupancy 原子：{self.low_occupancy_atoms}"
        )
        if self.metals:
            lines.append("金属离子：" + "、".join(self.metals))
        if self.waters:
            lines.append(f"水分子：{self.waters} 个原子")
        if self.nonstandard_residues:
            lines.append("非标准/杂原子残基：" + "、".join(self.nonstandard_residues))
        if self.additives:
            lines.append("常见添加剂：" + "、".join(self.additives))
        lines.extend(self.notes)
        return lines

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "models": self.models,
            "biomolecules": self.biomolecules,
            "biomt_transforms": self.biomt_transforms,
            "biological_unit": self.biological_unit,
            "is_multimer": self.is_multimer,
            "missing_residues": [item.label for item in self.missing_residues],
            "missing_residue_count": len(self.missing_residues),
            "missing_atoms": [
                {"residue": item.label, "atoms": list(item.atoms)}
                for item in self.missing_atoms
            ],
            "missing_atom_count": self.missing_atom_count,
            "altloc_atoms": self.altloc_atoms,
            "altloc_labels": list(self.altloc_labels),
            "low_occupancy_atoms": self.low_occupancy_atoms,
            "nonstandard_residues": list(self.nonstandard_residues),
            "metals": list(self.metals),
            "waters": self.waters,
            "additives": list(self.additives),
            "notes": list(self.notes),
            "summary": self.summary_lines(),
        }


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def analyze_structure(path: str | Path) -> StructureQC:
    """解析 PDB 文件并返回体检结果；无法读取时返回空结果。"""
    path = Path(path)
    qc = StructureQC(path=str(path))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        qc.notes.append(f"无法读取结构文件：{exc}")
        return qc

    model_count = 0
    in_first_model = True
    missing_residues: list[MissingResidue] = []
    missing_atoms: dict[tuple[str, str, int, str], list[str]] = {}
    altloc_labels: set[str] = set()
    nonstandard: set[str] = set()
    metals: set[str] = set()
    additives: set[str] = set()
    waters = 0
    altloc_atoms = 0
    low_occupancy = 0
    biomolecules = 0
    biomt: set[str] = set()
    biological_unit = ""

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("MODEL"):
            model_count += 1
            in_first_model = model_count == 1
            continue
        if line.startswith("ENDMDL"):
            if in_first_model:
                in_first_model = False
            continue
        if line.startswith("REMARK"):
            upper = line.upper()
            if upper.startswith("REMARK 350"):
                match = _BIOMOLECULE_RE.match(line)
                if match:
                    biomolecules += 1
                    continue
                match = _BIOLOGICAL_UNIT_RE.match(line)
                if match:
                    biological_unit = match.group(1).strip()
                    continue
                match = _BIOMT_RE.match(line)
                if match:
                    biomt.add(match.group(1))
                continue
            if upper.startswith("REMARK 465"):
                match = _MISSING_RESIDUE_RE.match(line)
                if match:
                    resname, chain, resseq, icode = match.groups()
                    item = MissingResidue(
                        resname=resname.upper(),
                        chain=chain.strip(),
                        resseq=_to_int(resseq),
                        icode=icode.strip().upper(),
                    )
                    if item not in missing_residues:
                        missing_residues.append(item)
                continue
            if upper.startswith("REMARK 470"):
                match = _MISSING_ATOM_RE.match(line)
                if match:
                    resname, chain, resseq, icode, atoms = match.groups()
                    key = (
                        resname.upper(),
                        chain.strip(),
                        _to_int(resseq),
                        icode.strip().upper(),
                    )
                    names = missing_atoms.setdefault(key, [])
                    for name in atoms.replace(",", " ").split():
                        clean = name.strip()
                        if clean and clean not in names:
                            names.append(clean)
                continue

        record = line[0:6].strip().upper()
        if record not in {"ATOM", "HETATM"} or not in_first_model:
            continue
        altloc = line[16:17].strip()
        if altloc:
            altloc_labels.add(altloc)
            if altloc not in {"A", "1"}:
                altloc_atoms += 1
        occupancy = _to_float(line[54:60], 1.0)
        if 0.0 <= occupancy < 0.5:
            low_occupancy += 1
        resname = line[17:20].strip().upper()
        if resname in pdbio.WATERS:
            waters += 1
            continue
        if resname in pdbio.METAL_IONS:
            metals.add(resname)
            continue
        if resname in pdbio.IONS:
            continue
        if resname in pdbio.ADDITIVES:
            additives.add(resname)
            continue
        if record == "HETATM" and resname not in pdbio.MODIFIED_AMINO_ACIDS:
            nonstandard.add(resname)

    qc.models = max(1, model_count)
    qc.biomolecules = biomolecules
    qc.biomt_transforms = len(biomt)
    qc.biological_unit = biological_unit
    qc.missing_residues = tuple(missing_residues)
    qc.missing_atoms = tuple(
        MissingAtom(resname=name, chain=chain, resseq=resseq, icode=icode, atoms=tuple(names))
        for (name, chain, resseq, icode), names in sorted(missing_atoms.items())
    )
    qc.altloc_atoms = altloc_atoms
    qc.altloc_labels = tuple(sorted(altloc_labels))
    qc.low_occupancy_atoms = low_occupancy
    qc.nonstandard_residues = tuple(sorted(nonstandard))
    qc.metals = tuple(sorted(metals))
    qc.waters = waters
    qc.additives = tuple(sorted(additives))
    return qc
