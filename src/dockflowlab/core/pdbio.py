"""PDB / PDBQT 解析与结构处理工具。

设计原则：
* 只依赖标准库，方便单元测试与跨平台打包；
* 对真实世界的不规范 PDB 文件尽量宽容，但会记录问题；
* 所有坐标单位为 Å。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

AMINO_ACIDS = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "SEC", "PYL", "MSE", "HSD", "HSE", "HSP", "CYX", "ASH", "GLH", "LYN",
}

NUCLEIC_ACIDS = {
    "A", "C", "G", "U", "T", "I", "DA", "DC", "DG", "DT", "DU", "DI",
    "ADE", "CYT", "GUA", "THY", "URA",
}

WATERS = {"HOH", "WAT", "DOD", "H2O", "TIP", "TIP3", "SOL"}

IONS = {
    "NA", "CL", "K", "MG", "CA", "ZN", "FE", "FE2", "FE3", "MN", "CU",
    "CU1", "CO", "NI", "CD", "HG", "BR", "IOD", "F", "LI", "CS", "RB",
    "SR", "BA", "AL", "SI", "AG", "AU", "PB", "PT", "MO", "W", "SE",
}

# 结晶添加剂、缓冲液、冷冻保护剂、常见糖链
ADDITIVES = {
    "SO4", "PO4", "GOL", "EDO", "PEG", "PG4", "P6G", "1PE", "2PE", "MPD",
    "DMS", "ACT", "ACE", "FMT", "CIT", "NO3", "NH4", "MES", "TRS", "HEP",
    "EPE", "TLA", "EOH", "BOG", "LDA", "SDS", "IMD", "BME", "DTT", "AZI",
    "NAG", "MAN", "BMA", "GAL", "FUC", "GLC", "NDG", "SIA", "XYP", "ARA",
    "MAL", "LAT", "SUC", "TRE", "MOH", "ACY", "EOX", "PGE", "PGF", "PLM",
    "OLA", "MYR", "STE", "PAL", "UNX", "UNL",
}

# 常见修饰氨基酸：它们仍是聚合物链的一部分，不能当成共晶配体整链删除。
MODIFIED_AMINO_ACIDS = {
    "MSE", "SEP", "TPO", "PTR", "CSO", "CME", "KCX", "LLP", "MLY", "M3L",
    "HYP", "PCA", "FME", "NEP", "HIC", "TYS", "PHD", "SAC", "CSD", "CSX",
    "OCS", "CAS", "ALY", "SAR", "NLE", "ORN", "DAB", "ABA", "AIB", "BMT",
    "DAL", "DLE", "DVA", "DTH", "DTR", "DTY", "TMD", "UNK",
}

# 短于该残基数、且含有非标准残基的链，若明显短于结构中的主要聚合物链，
# 视为肽类/多残基配体链（例如 6LU7 的 N3 抑制剂链 C）。
LIGAND_CHAIN_MAX_RESIDUES = 20

_HYDROGEN_NAMES = {"H", "H1", "H2", "H3", "HA", "HB", "HG", "HD", "HE", "HZ"}


@dataclass
class Atom:
    record: str
    serial: int
    name: str
    altloc: str
    resname: str
    chain: str
    resseq: int
    icode: str
    x: float
    y: float
    z: float
    occupancy: float = 1.0
    bfactor: float = 0.0
    element: str = ""
    charge: str = ""
    atom_type: str = ""

    @property
    def coord(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def is_hydrogen(self) -> bool:
        element = self.element.upper()
        if element in {"H", "D"}:
            return True
        if element:
            return False
        return self.name.upper().lstrip("0123456789")[:1] == "H"

    @property
    def residue_key(self) -> tuple[str, int, str, str]:
        return (self.chain, self.resseq, self.icode, self.resname)

    @property
    def residue_label(self) -> str:
        chain = f" {self.chain}" if self.chain else ""
        return f"{self.resname}{chain}{self.resseq}{self.icode}".strip()


@dataclass
class LigandSite:
    resname: str
    chain: str
    resseq: int
    icode: str
    atoms: list[Atom]
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    # 多残基配体（肽类抑制剂等）的完整残基列表：(resname, resseq, icode)
    residues: tuple[tuple[str, int, str], ...] = ()
    kind: str = "residue"  # residue / chain

    @property
    def residue_count(self) -> int:
        return len(self.residues) if self.residues else 1

    @property
    def display_name(self) -> str:
        if self.kind == "chain":
            return _format_chain_ligand(self.chain, self.residues)
        return self.resname

    @property
    def label(self) -> str:
        if self.kind == "chain":
            return self.display_name
        chain = f"链 {self.chain} " if self.chain else ""
        return f"{self.resname}（{chain}序号 {self.resseq}）"

    @property
    def reference(self) -> str:
        if self.kind == "chain":
            return f"链 {self.chain or '?'} 配体"
        return self.resname

    @property
    def ligand_key(self) -> str:
        """用于识别同一配体在不同链中的拷贝。"""
        if self.kind == "chain":
            names = sorted({name for name, _, _ in self.residues})
            return "chain:" + "/".join(names)
        return f"residue:{self.resname}"

    @property
    def heavy_atom_count(self) -> int:
        return sum(1 for atom in self.atoms if not atom.is_hydrogen)


@dataclass
class LigandChain:
    """一条由配体残基组成的短链（肽类抑制剂、糖链等）。"""

    chain: str
    residues: tuple[tuple[str, int, str], ...]
    atoms: list[Atom]

    @property
    def residue_count(self) -> int:
        return len(self.residues)

    @property
    def heavy_atom_count(self) -> int:
        return sum(1 for atom in self.atoms if not atom.is_hydrogen)

    @property
    def label(self) -> str:
        return _format_chain_ligand(self.chain, self.residues)


def is_polymer_residue(resname: str) -> bool:
    """残基是否属于蛋白/多肽或核酸聚合物链。"""
    resname = resname.upper()
    return (
        resname in AMINO_ACIDS
        or resname in NUCLEIC_ACIDS
        or resname in MODIFIED_AMINO_ACIDS
    )


def _is_polymer_residue(resname: str) -> bool:
    return is_polymer_residue(resname)


def _is_ligand_residue(resname: str) -> bool:
    resname = resname.upper()
    return (
        resname not in WATERS
        and resname not in IONS
        and resname not in ADDITIVES
        and not _is_polymer_residue(resname)
    )


def _format_chain_ligand(
    chain: str,
    residues: Sequence[tuple[str, int, str]],
) -> str:
    names = [name for name, _, _ in residues if _is_ligand_residue(name)]
    if not names:
        names = [name for name, _, _ in residues]
    unique = list(dict.fromkeys(names))
    shown = "/".join(unique[:3]) + ("…" if len(unique) > 3 else "")
    return f"链 {chain or '?'} 配体（{shown}，{len(residues)} 个残基）"


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _first_model_text(text: str) -> str:
    """只保留 PDB 的第一个 MODEL。没有 MODEL 记录时原样返回。"""
    lines: list[str] = []
    seen_model = False
    for line in text.splitlines():
        record = line[0:6].strip().upper()
        if record == "MODEL":
            if seen_model:
                break
            seen_model = True
            continue
        if record == "ENDMDL" and seen_model:
            break
        lines.append(line)
    return "\n".join(lines)


def infer_element(atom_name: str, resname: str, record: str) -> str:
    """在没有元素列时推断元素。对蛋白原子优先按首字母判断。"""
    stripped = atom_name.strip()
    if not stripped:
        return ""
    # PDB 原子名以数字开头时（如 1HG），去掉数字
    letters = re.sub(r"^[0-9]+", "", stripped)
    if not letters:
        return ""
    two = letters[:2].upper()
    # 常见双字母元素（注意蛋白 CA 是 α 碳，不是钙）
    if two in {"CL", "BR", "FE", "ZN", "MG", "NA", "CA", "MN", "CU", "CO", "NI", "CD", "HG", "SE"}:
        if record == "HETATM" and resname not in AMINO_ACIDS:
            return two.title()
        if two in {"FE", "ZN", "MG", "MN", "CU", "CO", "NI", "CD", "HG"}:
            return two.title()
    first = letters[0].upper()
    if first.isalpha():
        return first
    return ""


# PDBQT 的元素列实际写的是 AutoDock 原子类型（如芳香碳 A、受体氧 OA）。
# 这些类型不是元素符号：直接交给 OpenBabel/PLIP 会让它们把芳香碳当成未知
# 元素，盐桥、疏水作用等随之漏判。读入与写出 PDB 时统一还原成元素符号。
_AUTODOCK_ELEMENTS = {
    "A": "C",
    "OA": "O",
    "OS": "O",
    "NA": "N",
    "NS": "N",
    "SA": "S",
    "HD": "H",
    "HS": "H",
}


def normalize_element(
    element: str,
    atom_name: str = "",
    resname: str = "",
    record: str = "HETATM",
) -> str:
    """把元素列规范成元素符号；空白时按原子名推断。"""
    raw = (element or "").strip()
    if not raw:
        return infer_element(atom_name, resname, record)
    upper = raw.upper()
    if upper in _AUTODOCK_ELEMENTS:
        residue = (resname or "").strip().upper()
        # ``NA`` 既是 AutoDock 的受体氮，也可能是钠离子，用残基名区分。
        if upper == "NA" and (residue in IONS or residue in {"NA", "SOD"}):
            return "Na"
        return _AUTODOCK_ELEMENTS[upper]
    return raw


def parse_atoms(path: str | Path, *, first_model: bool = False) -> list[Atom]:
    """解析 PDB 的 ATOM/HETATM 记录，保留第一个 altloc。

    ``first_model=True`` 时只读取第一个 MODEL，避免 NMR 多模型结构把同一
    个原子重复计入几何计算。
    """
    atoms: list[Atom] = []
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return atoms
    if first_model:
        text = _first_model_text(text)
    for line in text.splitlines():
        record = line[0:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        altloc = line[16:17].strip()
        if altloc and altloc not in {"A", "1"}:
            continue
        atom_name = line[12:16].strip()
        resname = line[17:20].strip().upper()
        element = normalize_element(line[76:78].strip(), atom_name, resname, record)
        atoms.append(
            Atom(
                record=record,
                serial=_to_int(line[6:11]),
                name=atom_name,
                altloc=altloc,
                resname=resname,
                chain=line[21:22].strip(),
                resseq=_to_int(line[22:26]),
                icode=line[26:27].strip(),
                x=_to_float(line[30:38]),
                y=_to_float(line[38:46]),
                z=_to_float(line[46:54], 0.0),
                occupancy=_to_float(line[54:60], 1.0),
                bfactor=_to_float(line[60:66], 0.0),
                element=element,
                charge=line[78:80].strip(),
            )
        )
    return atoms


def parse_pdbqt_atoms(path: str | Path) -> list[Atom]:
    """解析 PDBQT 的 ATOM/HETATM 记录（可含多个 MODEL）。"""
    atoms: list[Atom] = []
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return atoms
    for line in text.splitlines():
        record = line[0:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        atom_name = line[12:16].strip()
        resname = line[17:20].strip().upper()
        element = normalize_element(line[76:78].strip(), atom_name, resname, record)
        atoms.append(
            Atom(
                record=record,
                serial=_to_int(line[6:11]),
                name=atom_name,
                altloc=line[16:17].strip(),
                resname=resname,
                chain=line[21:22].strip(),
                resseq=_to_int(line[22:26]),
                icode=line[26:27].strip(),
                x=_to_float(line[30:38]),
                y=_to_float(line[38:46]),
                z=_to_float(line[46:54]),
                occupancy=_to_float(line[54:60], 1.0),
                bfactor=_to_float(line[60:66], 0.0),
                element=element,
                charge=line[70:76].strip() or line[66:70].strip(),
                atom_type=line[77:79].strip(),
            )
        )
    return atoms


def parse_first_pose_atoms(path: str | Path) -> list[Atom]:
    """只解析 PDBQT 中的第一个 MODEL（用于生成复合物）。"""
    path = Path(path)
    atoms: list[Atom] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return atoms
    has_model = any(line.startswith("MODEL") for line in lines)
    in_first_model = not has_model
    for line in lines:
        if line.startswith("MODEL"):
            if atoms:
                break
            in_first_model = True
            continue
        if line.startswith("ENDMDL"):
            if in_first_model and atoms:
                break
            continue
        if not in_first_model:
            continue
        record = line[0:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        atom_name = line[12:16].strip()
        resname = line[17:20].strip().upper()
        element = normalize_element(line[76:78].strip(), atom_name, resname, record)
        atoms.append(
            Atom(
                record=record,
                serial=_to_int(line[6:11]),
                name=atom_name,
                altloc=line[16:17].strip(),
                resname=resname,
                chain=line[21:22].strip(),
                resseq=_to_int(line[22:26]),
                icode=line[26:27].strip(),
                x=_to_float(line[30:38]),
                y=_to_float(line[38:46]),
                z=_to_float(line[46:54]),
                occupancy=_to_float(line[54:60], 1.0),
                bfactor=_to_float(line[60:66], 0.0),
                element=element,
                charge=line[70:76].strip() or line[66:70].strip(),
                atom_type=line[77:79].strip(),
            )
        )
    return atoms


def detect_format(path: str | Path) -> str:
    """根据扩展名与内容判断结构文件格式。"""
    path = Path(path)
    suffix = path.suffix.lower()
    mapping = {
        ".pdbqt": "pdbqt",
        ".pdb": "pdb",
        ".ent": "pdb",
        ".cif": "cif",
        ".mmcif": "cif",
        ".sdf": "sdf",
        ".mol": "mol",
        ".mol2": "mol2",
        ".smi": "smi",
        ".smiles": "smi",
        ".inchi": "inchi",
    }
    if suffix in mapping:
        return mapping[suffix]
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return "unknown"
    if head.startswith("data_") or "_atom_site." in head:
        return "cif"
    if "$$$$" in head or "V2000" in head or "V3000" in head:
        return "sdf"
    if head.lstrip().startswith("@<TRIPOS>"):
        return "mol2"
    if re.search(r"^(ATOM|HETATM|HEADER|REMARK|CRYST1)", head, re.M):
        return "pdb"
    return "unknown"


# ----------------------------------------------------------------------
# 几何
# ----------------------------------------------------------------------
def bounds(coords: Iterable[Sequence[float]]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """返回 (min_xyz, max_xyz)。空输入抛 ValueError。"""
    points = list(coords)
    if not points:
        raise ValueError("没有可用于计算边界的坐标")
    xs, ys, zs = zip(*[(float(p[0]), float(p[1]), float(p[2])) for p in points])
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def bounds_center_size(
    coords: Iterable[Sequence[float]], padding: float = 0.0
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    low, high = bounds(coords)
    center = tuple((low[i] + high[i]) / 2.0 for i in range(3))
    size = tuple((high[i] - low[i]) + 2.0 * padding for i in range(3))
    return center, size  # type: ignore[return-value]


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


# ----------------------------------------------------------------------
# 结构分类
# ----------------------------------------------------------------------
def protein_chains(path: str | Path) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    residues: dict[str, set[tuple[int, str]]] = {}
    for atom in parse_atoms(path, first_model=True):
        if atom.resname not in AMINO_ACIDS:
            continue
        if atom.is_hydrogen:
            continue
        counts[atom.chain] = counts.get(atom.chain, 0) + 1
        residues.setdefault(atom.chain, set()).add((atom.resseq, atom.icode))
    result = [
        {"chain": chain or " ", "atoms": counts[chain], "residues": len(residues.get(chain, set()))}
        for chain in sorted(counts)
    ]
    return result


def polymer_chains(path: str | Path) -> list[dict[str, object]]:
    """返回蛋白/多肽/核酸链摘要，供界面展示与模式判断使用。"""
    fmt = detect_format(path)
    atoms = (
        parse_first_pose_atoms(path)
        if fmt == "pdbqt"
        else parse_atoms(path, first_model=True)
    )
    chain_atoms: dict[str, int] = {}
    protein_residues: dict[str, set[tuple[int, str, str]]] = {}
    nucleic_residues: dict[str, set[tuple[int, str, str]]] = {}
    for atom in atoms:
        if atom.is_hydrogen:
            continue
        resname = atom.resname.upper()
        if resname in AMINO_ACIDS or resname in MODIFIED_AMINO_ACIDS:
            protein_residues.setdefault(atom.chain, set()).add(
                (atom.resseq, atom.icode, resname)
            )
        elif resname in NUCLEIC_ACIDS:
            nucleic_residues.setdefault(atom.chain, set()).add(
                (atom.resseq, atom.icode, resname)
            )
        else:
            continue
        chain_atoms[atom.chain] = chain_atoms.get(atom.chain, 0) + 1

    result: list[dict[str, object]] = []
    for chain in sorted(chain_atoms):
        protein_count = len(protein_residues.get(chain, set()))
        nucleic_count = len(nucleic_residues.get(chain, set()))
        if protein_count and nucleic_count:
            chain_type = "mixed"
        elif nucleic_count:
            chain_type = "nucleic_acid"
        else:
            chain_type = "protein"
        result.append(
            {
                "chain": chain or " ",
                "type": chain_type,
                "atoms": chain_atoms[chain],
                "residues": protein_count + nucleic_count,
                "protein_residues": protein_count,
                "nucleic_residues": nucleic_count,
            }
        )
    return result


def classify_atoms(
    atoms: Sequence[Atom],
    *,
    peptide_max_residues: int = 20,
    peptide_max_heavy_atoms: int = 500,
) -> tuple[str, str]:
    """按原子记录判断结构类型。

    ``peptide_max_*`` 控制区分“多肽/短肽”与“完整蛋白质”的阈值，调用方
    可以按场景调整；默认值面向靶点结构。
    """
    if not atoms:
        return "unknown", "文件中没有可识别的原子记录"
    heavy = [a for a in atoms if not a.is_hydrogen]
    protein_atoms = [
        a for a in heavy
        if a.resname in AMINO_ACIDS or a.resname in MODIFIED_AMINO_ACIDS
    ]
    nucleic_atoms = [a for a in heavy if a.resname in NUCLEIC_ACIDS]
    protein_residues = {a.residue_key for a in protein_atoms}

    def _polymer_kind() -> str:
        if (
            len(protein_residues) <= max(1, int(peptide_max_residues))
            and len(protein_atoms) <= max(1, int(peptide_max_heavy_atoms))
        ):
            return "peptide"
        return "protein"

    if len(protein_residues) >= 5 and len(protein_atoms) >= 20:
        kind = _polymer_kind()
        return kind, f"检测到 {len(protein_residues)} 个氨基酸残基"
    if len(protein_atoms) >= 20 and len(protein_atoms) >= 0.3 * len(heavy):
        kind = _polymer_kind()
        return kind, f"检测到 {len(protein_residues)} 个氨基酸残基"
    if len(nucleic_atoms) >= 20:
        return "nucleic_acid", "检测到核酸结构"
    if 3 <= len(heavy) <= 300:
        return "small_molecule", f"检测到 {len(heavy)} 个重原子的小分子"
    return "unknown", f"共 {len(heavy)} 个重原子，无法判断结构类型"


def structure_kind(
    path: str | Path,
    *,
    peptide_max_residues: int = 20,
    peptide_max_heavy_atoms: int = 500,
) -> tuple[str, str]:
    """返回 (类别, 说明)。类别：protein / peptide / nucleic_acid / small_molecule / unknown。

    NMR 等结构常含多个 MODEL。准备与对接只使用第一个模型，因此识别类型时
    也必须只统计第一个模型，否则 20 个残基的多肽会被 38 个模型累加成蛋白质。
    """
    fmt = detect_format(path)
    atoms = (
        parse_first_pose_atoms(path)
        if fmt == "pdbqt"
        else parse_atoms(path, first_model=True)
    )
    return classify_atoms(
        atoms,
        peptide_max_residues=peptide_max_residues,
        peptide_max_heavy_atoms=peptide_max_heavy_atoms,
    )


def looks_like_protein(path: str | Path) -> bool:
    kind, _ = structure_kind(path)
    return kind in {"protein", "peptide"}


# ----------------------------------------------------------------------
# 结合位点
# ----------------------------------------------------------------------
def _residue_groups(
    atoms: Sequence[Atom],
) -> dict[tuple[str, int, str, str], list[Atom]]:
    """按残基分组，忽略水、离子与结晶添加剂。"""
    groups: dict[tuple[str, int, str, str], list[Atom]] = {}
    for atom in atoms:
        resname = atom.resname.upper()
        if resname in WATERS or resname in IONS or resname in ADDITIVES:
            continue
        groups.setdefault(atom.residue_key, []).append(atom)
    return groups


def _parse_link_pairs(
    path: str | Path,
) -> list[tuple[tuple[str, str, int, str], tuple[str, str, int, str]]]:
    """解析 PDB LINK 记录，返回残基对 ((resname, chain, resseq, icode), ...)。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pairs = []
    for line in text.splitlines():
        if not line.upper().startswith("LINK"):
            continue
        first = (
            line[17:20].strip().upper(),
            line[21:22].strip(),
            _to_int(line[22:26]),
            line[26:27].strip(),
        )
        second = (
            line[47:50].strip().upper(),
            line[51:52].strip(),
            _to_int(line[52:56]),
            line[56:57].strip(),
        )
        if first[0] and second[0]:
            pairs.append((first, second))
    return pairs


def find_ligand_chains(
    path: str | Path,
    *,
    atoms: Sequence[Atom] | None = None,
) -> list[LigandChain]:
    """找出由配体残基组成的短链（肽类抑制剂、糖链等）。

    很多 PDB 把肽类抑制剂拆成“标准氨基酸 ATOM + 非标准残基 HETATM”，
    例如 6LU7 的 N3 抑制剂（链 C：02J ALA VAL LEU PJE 010）。如果只按
    单个 HETATM 残基识别，会漏掉整个配体，还会把标准残基留在受体里。
    """
    atoms = (
        list(atoms)
        if atoms is not None
        else parse_atoms(path, first_model=True)
    )
    groups = _residue_groups(atoms)
    by_chain: dict[str, list[tuple[int, str, str, list[Atom]]]] = {}
    for (chain, resseq, icode, resname), residue_atoms in groups.items():
        by_chain.setdefault(chain, []).append((resseq, icode, resname, residue_atoms))

    standard_counts = {
        chain: sum(1 for _, _, resname, _ in residues if _is_polymer_residue(resname))
        for chain, residues in by_chain.items()
    }
    max_standard = max(standard_counts.values(), default=0)
    links = _parse_link_pairs(path)

    chains: list[LigandChain] = []
    for chain, residues in by_chain.items():
        residues.sort(key=lambda item: (item[0], item[1]))
        if len(residues) > LIGAND_CHAIN_MAX_RESIDUES:
            continue
        if not any(_is_ligand_residue(resname) for _, _, resname, _ in residues):
            continue
        chain_atoms = [atom for _, _, _, residue_atoms in residues for atom in residue_atoms]
        heavy = [atom for atom in chain_atoms if not atom.is_hydrogen]
        carbons = sum(1 for atom in heavy if atom.element.upper() == "C")
        if len(heavy) < 6 or carbons < 3:
            continue

        standard_count = standard_counts.get(chain, 0)
        if standard_count:
            # 只有当结构中存在明显更长的聚合物链时，才把这条短链当成配体；
            # 否则它可能就是用户想对接的肽类靶点本身。
            if max_standard < 2 * standard_count and max_standard - standard_count < 20:
                continue
            # 真正蛋白链上的修饰残基：标准残基占绝对多数，且没有跨链连接。
            standard_heavy = sum(1 for atom in heavy if _is_polymer_residue(atom.resname))
            nonstandard_heavy = sum(
                1 for atom in heavy if _is_ligand_residue(atom.resname)
            )
            cross_linked = any(
                (first[1] == chain) != (second[1] == chain)
                for first, second in links
            )
            if (
                standard_count > 6
                and standard_heavy > nonstandard_heavy
                and not cross_linked
            ):
                continue
        else:
            # 全部为 HETATM 的链：要求 LINK 把链内残基连接起来，
            # 避免把同一条链里的多个独立小分子合并成一个配体。
            chain_keys = {(resseq, icode, resname) for resseq, icode, resname, _ in residues}
            connected = any(
                (first[1], first[2], first[3]) in chain_keys
                and (second[1], second[2], second[3]) in chain_keys
                for first, second in links
            )
            if not connected:
                continue

        chains.append(
            LigandChain(
                chain=chain,
                residues=tuple(
                    (resname, resseq, icode)
                    for resseq, icode, resname, _ in residues
                ),
                atoms=chain_atoms,
            )
        )
    return chains


def find_ligand_sites(path: str | Path) -> list[LigandSite]:
    """查找适合作为结合位点参考的共晶配体，按重原子数降序。

    既识别单个 HETATM 小分子，也识别由多个残基组成的肽类配体链。
    """
    atoms = parse_atoms(path, first_model=True)
    ligand_chains = find_ligand_chains(path, atoms=atoms)
    grouped_chains = {chain.chain for chain in ligand_chains}

    groups: dict[tuple[str, int, str, str], list[Atom]] = {}
    for atom in atoms:
        if atom.record != "HETATM":
            continue
        if not _is_ligand_residue(atom.resname):
            continue
        if atom.chain in grouped_chains:
            continue
        groups.setdefault(atom.residue_key, []).append(atom)

    sites: list[LigandSite] = []
    for ligand_chain in ligand_chains:
        heavy = [atom for atom in ligand_chain.atoms if not atom.is_hydrogen]
        if len(heavy) < 6:
            continue
        try:
            center, size = bounds_center_size([atom.coord for atom in heavy])
        except ValueError:
            continue
        counts: dict[str, int] = {}
        for atom in ligand_chain.atoms:
            if atom.is_hydrogen or not _is_ligand_residue(atom.resname):
                continue
            resname = atom.resname.upper()
            counts[resname] = counts.get(resname, 0) + 1
        primary = (
            max(counts, key=counts.get)
            if counts
            else ligand_chain.residues[0][0]
        )
        first_resname, first_resseq, first_icode = ligand_chain.residues[0]
        sites.append(
            LigandSite(
                resname=primary,
                chain=ligand_chain.chain,
                resseq=first_resseq,
                icode=first_icode,
                atoms=ligand_chain.atoms,
                center=center,
                size=size,
                residues=ligand_chain.residues,
                kind="chain",
            )
        )

    for (chain, resseq, icode, resname), residue_atoms in groups.items():
        heavy = [a for a in residue_atoms if not a.is_hydrogen]
        carbons = sum(1 for a in heavy if a.element.upper() == "C")
        if len(heavy) < 6 or carbons < 3:
            continue
        try:
            center, size = bounds_center_size([a.coord for a in heavy])
        except ValueError:
            continue
        sites.append(
            LigandSite(
                resname=resname,
                chain=chain,
                resseq=resseq,
                icode=icode,
                atoms=residue_atoms,
                center=center,
                size=size,
                residues=((resname, resseq, icode),),
                kind="residue",
            )
        )
    sites.sort(key=lambda site: site.heavy_atom_count, reverse=True)
    return sites


def remove_chains(
    path: str | Path,
    output_path: str | Path,
    chains: Iterable[str],
) -> int:
    """写出移除指定链后的 PDB，返回移除的原子行数。

    同时删除这些链的 SEQRES / LINK / SSBOND 记录，避免修复工具
    根据序列记录把配体肽链又补回受体。
    """
    removed_chains = {str(chain).strip() for chain in chains if str(chain).strip()}
    removed_atoms = 0
    lines: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        record = line[0:6].strip().upper()
        if record in {"ATOM", "HETATM", "TER"}:
            if line[21:22].strip() in removed_chains:
                if record != "TER":
                    removed_atoms += 1
                continue
        elif record == "SEQRES":
            if line[11:12].strip() in removed_chains:
                continue
        elif record == "LINK":
            first_chain = line[21:22].strip()
            second_chain = line[51:52].strip()
            if first_chain in removed_chains or second_chain in removed_chains:
                continue
        elif record == "SSBOND":
            first_chain = line[15:16].strip()
            second_chain = line[29:30].strip()
            if first_chain in removed_chains or second_chain in removed_chains:
                continue
        lines.append(line)
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return removed_atoms


@dataclass
class SiteResidue:
    resname: str
    chain: str
    resseq: int
    icode: str


def parse_site_records(path: str | Path) -> list[list[SiteResidue]]:
    """解析 PDB SITE 记录，按 siteID 合并多行，返回每个位点的残基列表。"""
    grouped: dict[str, list[SiteResidue]] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    # SITE 记录的列对齐在实际文件中并不统一（不同软件写出的空格数不同），
    # 因此优先用正则提取 "残基名 链 序号" 三元组。
    pattern = re.compile(r"([A-Z][A-Z0-9]{0,2})\s+([A-Za-z0-9])\s+(-?\d+)([A-Z]?)")
    for line in lines:
        if not line.startswith("SITE"):
            continue
        site_id = line[11:14].strip() or f"site{len(grouped) + 1}"
        body = line[18:].rstrip()
        residues = [
            SiteResidue(
                resname=match.group(1).upper(),
                chain=match.group(2).strip(),
                resseq=int(match.group(3)),
                icode=match.group(4).strip(),
            )
            for match in pattern.finditer(body)
        ]
        if not residues:
            # 回退：按 PDB 规范列位置解析
            count = _to_int(line[15:17])
            for index in range(count):
                chunk = line[18 + index * 9 : 27 + index * 9]
                if len(chunk) < 4:
                    break
                resname = chunk[0:3].strip().upper()
                chain = chunk[3].strip()
                resseq = _to_int(chunk[4:8])
                icode = chunk[8].strip() if len(chunk) > 8 else ""
                if resname:
                    residues.append(SiteResidue(resname, chain, resseq, icode))
        if residues:
            grouped.setdefault(site_id, []).extend(residues)
    return list(grouped.values())


def site_residue_coords(path: str | Path, site: Sequence[SiteResidue]) -> list[tuple[float, float, float]]:
    """把 SITE 残基映射到实际原子坐标（优先 CA 原子）。"""
    atoms = parse_atoms(path, first_model=True)
    lookup: dict[tuple[str, int, str, str], list[Atom]] = {}
    for atom in atoms:
        lookup.setdefault(atom.residue_key, []).append(atom)
    coords: list[tuple[float, float, float]] = []
    for residue in site:
        matches = lookup.get((residue.chain, residue.resseq, residue.icode, residue.resname), [])
        if not matches:
            # 忽略插入码不匹配的情况
            matches = [
                a for a in atoms
                if a.chain == residue.chain and a.resseq == residue.resseq and a.resname == residue.resname
            ]
        ca = [a for a in matches if a.name.upper() == "CA"]
        if ca:
            coords.append(ca[0].coord)
        elif matches:
            coords.append(matches[0].coord)
    return coords


# ----------------------------------------------------------------------
# 文件写出
# ----------------------------------------------------------------------
def _element_for_pdb(atom: Atom) -> str:
    element = normalize_element(
        atom.element, atom.name, atom.resname, atom.record
    )
    return element[:2].upper().rjust(2)[-2:]


def make_pdb_atom_line(
    atom: Atom, serial: int, resname: str, chain: str, resseq: int = 1
) -> str:
    name = atom.name if len(atom.name) <= 4 else atom.name[:4]
    # PDB 原子名对齐：单字母元素从第 14 列开始
    if len(name) < 4 and name[:1].isalpha() and not name[:1].isdigit():
        name_field = f" {name:<3}"[:4]
    else:
        name_field = f"{name:<4}"[:4]
    element = _element_for_pdb(atom)
    return (
        f"HETATM{serial:5d} {name_field}{'':1}{resname:>3} {chain:1}{resseq:4d}{'':1}   "
        f"{atom.x:8.3f}{atom.y:8.3f}{atom.z:8.3f}{1.00:6.2f}{0.00:6.2f}"
        f"          {element:>2}"
    )[:78]


def write_pdb_atoms(
    atoms: Sequence[Atom],
    output_path: str | Path,
    *,
    resname: str = "LIG",
    chain: str = "L",
    start_serial: int = 1,
    records: str = "HETATM",
) -> int:
    lines: list[str] = []
    serial = start_serial
    for atom in atoms:
        if atom.is_hydrogen:
            continue
        modified = Atom(**{**atom.__dict__, "record": records})
        lines.append(make_pdb_atom_line(modified, serial, resname, chain))
        serial += 1
    lines.append("END")
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return serial - start_serial


def make_complex(
    receptor_pdb: str | Path,
    pose_pdbqt: str | Path,
    output_pdb: str | Path,
    *,
    ligand_resname: str = "LIG",
    ligand_chain: str = "L",
) -> int:
    """把受体 PDB 与对接构象合并为一个复合物 PDB，返回配体原子数。"""
    receptor_path = Path(receptor_pdb)
    pose_atoms = parse_first_pose_atoms(pose_pdbqt)
    if not pose_atoms:
        raise ValueError("对接构象文件为空，无法生成复合物")

    receptor_lines: list[str] = []
    max_serial = 0
    if receptor_path.is_file():
        for line in receptor_path.read_text(encoding="utf-8", errors="replace").splitlines():
            record = line[0:6].strip()
            if record in {"ATOM", "HETATM"}:
                max_serial = max(max_serial, _to_int(line[6:11]))
                receptor_lines.append(line)
            elif record in {"TER", "HEADER", "TITLE", "REMARK", "CRYST1", "MODEL", "ENDMDL"}:
                if record not in {"MODEL", "ENDMDL", "END"}:
                    receptor_lines.append(line)

    ligand_lines: list[str] = []
    serial = max_serial + 1
    for atom in pose_atoms:
        if atom.is_hydrogen:
            continue
        ligand_lines.append(make_pdb_atom_line(atom, serial, ligand_resname, ligand_chain, 1))
        serial += 1

    content = "\n".join(receptor_lines + ligand_lines + ["TER", "END"]) + "\n"
    Path(output_pdb).write_text(content, encoding="utf-8")
    return len(ligand_lines)


# ----------------------------------------------------------------------
# PDBQT 验证与结果解析
# ----------------------------------------------------------------------
_VINA_RESULT_RE = re.compile(
    r"^REMARK\s+VINA\s+RESULT:\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Smina 直接写 minimizedAffinity，不提供每个构象的 RMSD。
_SMINA_AFFINITY_RE = re.compile(
    r"^REMARK\s+minimizedAffinity\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE
)


def parse_pdbqt_poses(path: str | Path) -> list[Pose]:  # noqa: F821 - Pose 在下方延迟导入避免循环
    """解析 PDBQT 中的结合能备注，按出现顺序返回。

    兼容 Vina / QuickVina 的 ``REMARK VINA RESULT`` 与 Smina 的
    ``REMARK minimizedAffinity``；后者没有 RMSD 信息，按 0 记录。
    """
    from .models import Pose

    poses: list[Pose] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return poses
    for line in text.splitlines():
        match = _VINA_RESULT_RE.match(line)
        if match:
            poses.append(
                Pose(
                    rank=len(poses) + 1,
                    affinity=float(match.group(1)),
                    rmsd_lb=float(match.group(2)),
                    rmsd_ub=float(match.group(3)),
                )
            )
            continue
        match = _SMINA_AFFINITY_RE.match(line)
        if match:
            poses.append(
                Pose(
                    rank=len(poses) + 1,
                    affinity=float(match.group(1)),
                    rmsd_lb=0.0,
                    rmsd_ub=0.0,
                )
            )
    return poses


def validate_pdbqt(path: str | Path, *, receptor: bool = False) -> tuple[bool, str, dict[str, object]]:
    """检查 PDBQT 是否可用于 Vina。"""
    path = Path(path)
    stats: dict[str, object] = {"path": str(path), "atoms": 0, "size": 0}
    if not path.is_file():
        return False, "文件不存在", stats
    size = path.stat().st_size
    stats["size"] = size
    if size < 80:
        return False, "文件内容过小，可能生成失败", stats
    text = path.read_text(encoding="utf-8", errors="replace")
    atoms = parse_pdbqt_atoms(path)
    stats["atoms"] = len(atoms)
    if len(atoms) < 3:
        return False, f"原子数量过少（{len(atoms)}），无法对接", stats
    non_finite = [
        a for a in atoms
        if not all(math.isfinite(v) and abs(v) < 10000 for v in (a.x, a.y, a.z))
    ]
    if non_finite:
        return False, f"存在 {len(non_finite)} 个异常坐标", stats
    if receptor:
        if re.search(r"^ROOT\b", text, re.M):
            return False, "受体文件包含柔性扭转树（ROOT），Vina 需要刚性受体", stats
        if re.search(r"^TORSDOF\b", text, re.M):
            return False, "受体文件包含 TORSDOF，Vina 需要刚性受体", stats
        if not all(a.atom_type for a in atoms[: min(10, len(atoms))]):
            stats["warning"] = "部分原子缺少 AutoDock 类型"
    else:
        if not re.search(r"^(ROOT|TORSDOF)\b", text, re.M):
            stats["warning"] = "未检测到扭转树，配体将按刚性分子处理"
    return True, "OK", stats


def pdbqt_pose_block(path: str | Path, rank: int = 1) -> str:
    """提取 PDBQT 中第 rank 个 MODEL 的文本。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    models = re.split(r"^MODEL\s+\d+\s*$", text, flags=re.M)
    if len(models) <= 1:
        return text
    blocks = []
    for chunk in models[1:]:
        block = re.split(r"^ENDMDL\s*$", chunk, maxsplit=1, flags=re.M)[0]
        blocks.append(block)
    index = max(0, min(rank - 1, len(blocks) - 1))
    return blocks[index]


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")
