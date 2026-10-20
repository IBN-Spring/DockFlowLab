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

    @property
    def label(self) -> str:
        chain = f"链 {self.chain} " if self.chain else ""
        return f"{self.resname}（{chain}序号 {self.resseq}）"

    @property
    def heavy_atom_count(self) -> int:
        return sum(1 for atom in self.atoms if not atom.is_hydrogen)


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


def parse_atoms(path: str | Path) -> list[Atom]:
    """解析 PDB 的 ATOM/HETATM 记录，保留第一个 altloc。"""
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
        altloc = line[16:17].strip()
        if altloc and altloc not in {"A", "1"}:
            continue
        atom_name = line[12:16].strip()
        resname = line[17:20].strip().upper()
        element = line[76:78].strip()
        if not element:
            element = infer_element(atom_name, resname, record)
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
        element = line[76:78].strip()
        if not element:
            element = infer_element(atom_name, resname, record)
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
        element = line[76:78].strip() or infer_element(atom_name, resname, record)
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
    for atom in parse_atoms(path):
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


def structure_kind(path: str | Path) -> tuple[str, str]:
    """返回 (类别, 说明)。类别：protein / peptide / nucleic_acid / small_molecule / unknown。"""
    atoms = parse_atoms(path)
    if not atoms:
        return "unknown", "文件中没有可识别的原子记录"
    heavy = [a for a in atoms if not a.is_hydrogen]
    protein_atoms = [a for a in heavy if a.resname in AMINO_ACIDS]
    nucleic_atoms = [a for a in heavy if a.resname in NUCLEIC_ACIDS]
    protein_residues = {a.residue_key for a in protein_atoms}
    if len(protein_residues) >= 5 and len(protein_atoms) >= 20:
        kind = "peptide" if len(protein_residues) < 20 else "protein"
        return kind, f"检测到 {len(protein_residues)} 个氨基酸残基"
    if len(protein_atoms) >= 20 and len(protein_atoms) >= 0.3 * len(heavy):
        kind = "peptide" if len(protein_residues) < 20 else "protein"
        return kind, f"检测到 {len(protein_residues)} 个氨基酸残基"
    if len(nucleic_atoms) >= 20:
        return "nucleic_acid", "检测到核酸结构"
    if 3 <= len(heavy) <= 300:
        return "small_molecule", f"检测到 {len(heavy)} 个重原子的小分子"
    return "unknown", f"共 {len(heavy)} 个重原子，无法判断结构类型"


def looks_like_protein(path: str | Path) -> bool:
    kind, _ = structure_kind(path)
    return kind in {"protein", "peptide"}


# ----------------------------------------------------------------------
# 结合位点
# ----------------------------------------------------------------------
def find_ligand_sites(path: str | Path) -> list[LigandSite]:
    """查找适合作为结合位点参考的共晶小分子，按重原子数降序。"""
    groups: dict[tuple[str, int, str, str], list[Atom]] = {}
    for atom in parse_atoms(path):
        if atom.record != "HETATM":
            continue
        resname = atom.resname.upper()
        if resname in WATERS or resname in IONS or resname in ADDITIVES:
            continue
        if resname in AMINO_ACIDS or resname in NUCLEIC_ACIDS:
            continue
        groups.setdefault(atom.residue_key, []).append(atom)

    sites: list[LigandSite] = []
    for (chain, resseq, icode, resname), atoms in groups.items():
        heavy = [a for a in atoms if not a.is_hydrogen]
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
                atoms=atoms,
                center=center,
                size=size,
            )
        )
    sites.sort(key=lambda s: len(s.atoms), reverse=True)
    return sites


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
    atoms = parse_atoms(path)
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
    element = atom.element.strip()
    if not element:
        element = infer_element(atom.name, atom.resname, atom.record)
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


def parse_pdbqt_poses(path: str | Path) -> list[Pose]:  # noqa: F821 - Pose 在下方延迟导入避免循环
    """解析 PDBQT 中的 VINA RESULT 备注，按出现顺序返回。"""
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
