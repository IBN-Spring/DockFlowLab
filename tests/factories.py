"""用于单元测试的最小结构文件生成器。"""

from __future__ import annotations

from pathlib import Path

AMINO = ["ALA", "GLY", "SER", "LEU", "VAL", "THR"]


def atom_line(
    record: str,
    serial: int,
    name: str,
    resname: str,
    chain: str,
    resseq: int,
    x: float,
    y: float,
    z: float,
    element: str,
) -> str:
    name_field = f" {name:<3}" if len(name) < 4 else name[:4]
    return (
        f"{record:<6}{serial:5d} {name_field} {resname:>3} {chain}{resseq:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{1.00:6.2f}{0.00:6.2f}          {element:>2}"
    )


def make_protein_pdb(
    path: Path,
    *,
    residues: int = 6,
    ligand: bool = False,
    site: bool = False,
) -> Path:
    lines = ["HEADER    TEST PROTEIN"]
    serial = 1
    for index in range(residues):
        resname = AMINO[index % len(AMINO)]
        base = index * 3.8
        for atom_name, element, dx in (("N", "N", 0.0), ("CA", "C", 0.8), ("C", "C", 1.6), ("O", "O", 2.2)):
            lines.append(
                atom_line(
                    "ATOM", serial, atom_name, resname, "A", index + 1,
                    10.0 + base + dx, 20.0 + index * 0.5, 30.0 + dx, element,
                )
            )
            serial += 1
    if ligand:
        coords = [
            ("C1", "C", 12.0, 21.0, 31.0),
            ("C2", "C", 13.2, 21.0, 31.0),
            ("C3", "C", 13.8, 22.1, 31.0),
            ("C4", "C", 13.2, 23.2, 31.0),
            ("C5", "C", 12.0, 23.2, 31.0),
            ("C6", "C", 11.4, 22.1, 31.0),
            ("O1", "O", 10.4, 21.0, 31.0),
            ("O2", "O", 14.7, 22.1, 31.0),
        ]
        for atom_name, element, x, y, z in coords:
            lines.append(atom_line("HETATM", serial, atom_name, "LIG", "A", 100, x, y, z, element))
            serial += 1
    if site:
        lines.append("SITE     1 AC1  4 ALA A   1  GLY A   2  SER A   3  LEU A   4")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_peptide_ligand_pdb(path: Path, *, residues: int = 25) -> Path:
    """生成“蛋白链 A + 多残基肽类配体链 C”的测试结构。

    配体链模拟 6LU7 的 N3 抑制剂：标准氨基酸（ALA/VAL/LEU）以 ATOM
    记录出现，非标准残基（02J/PJE/010）以 HETATM 记录出现。
    """
    lines = [
        "HEADER    TEST PEPTIDE LIGAND COMPLEX",
        "SEQRES   1 C    6  02J ALA VAL LEU PJE 010",
    ]
    serial = 1
    for index in range(residues):
        resname = AMINO[index % len(AMINO)]
        base = index * 3.8
        for atom_name, element, dx in (("N", "N", 0.0), ("CA", "C", 0.8), ("C", "C", 1.6), ("O", "O", 2.2)):
            lines.append(
                atom_line(
                    "ATOM", serial, atom_name, resname, "A", index + 1,
                    10.0 + base + dx, 20.0 + index * 0.5, 30.0 + dx, element,
                )
            )
            serial += 1

    # 链 C 放在远离蛋白的位置，便于测试盒子是否覆盖完整配体。
    origin = (100.0, 100.0, 100.0)

    def add_residues(
        record: str,
        resname: str,
        resseq: int,
        atoms: list[tuple[str, str, float, float, float]],
    ) -> None:
        nonlocal serial
        for atom_name, element, dx, dy, dz in atoms:
            lines.append(
                atom_line(
                    record, serial, atom_name, resname, "C", resseq,
                    origin[0] + dx, origin[1] + dy, origin[2] + dz, element,
                )
            )
            serial += 1

    add_residues(
        "HETATM", "02J", 1,
        [
            ("C1", "C", 0.0, 0.0, 0.0),
            ("C2", "C", 1.3, 0.0, 0.0),
            ("C3", "C", 2.0, 1.1, 0.0),
            ("C4", "C", 1.4, 2.3, 0.0),
            ("C5", "C", 0.1, 2.4, 0.0),
            ("N", "N", -0.6, 1.2, 0.0),
            ("O", "O", -0.7, -0.9, 0.0),
            ("C6", "C", 3.2, 1.0, 0.0),
        ],
    )
    add_residues(
        "ATOM", "ALA", 2,
        [
            ("N", "N", 3.5, -0.8, 0.0),
            ("CA", "C", 4.2, -1.9, 0.0),
            ("C", "C", 5.7, -1.7, 0.0),
            ("O", "O", 6.2, -0.6, 0.0),
            ("CB", "C", 3.7, -3.2, 0.0),
        ],
    )
    add_residues(
        "ATOM", "VAL", 3,
        [
            ("N", "N", 6.2, -2.8, 0.0),
            ("CA", "C", 7.5, -2.8, 0.0),
            ("C", "C", 8.1, -4.2, 0.0),
            ("O", "O", 7.5, -5.2, 0.0),
            ("CB", "C", 8.1, -1.7, 0.0),
            ("CG1", "C", 9.5, -1.8, 0.0),
            ("CG2", "C", 7.6, -0.4, 0.0),
        ],
    )
    add_residues(
        "ATOM", "LEU", 4,
        [
            ("N", "N", 9.4, -4.3, 0.0),
            ("CA", "C", 10.0, -5.6, 0.0),
            ("C", "C", 11.5, -5.4, 0.0),
            ("O", "O", 12.1, -4.4, 0.0),
            ("CB", "C", 9.5, -6.9, 0.0),
            ("CG", "C", 10.1, -8.2, 0.0),
            ("CD1", "C", 9.4, -9.4, 0.0),
            ("CD2", "C", 11.6, -8.3, 0.0),
        ],
    )
    add_residues(
        "HETATM", "PJE", 5,
        [
            ("CA", "C", 12.0, -6.0, 0.0),
            ("C20", "C", 12.6, -7.4, 0.0),
            ("C21", "C", 11.8, -8.5, 0.0),
            ("C", "C", 10.3, -8.3, 0.0),
            ("C25", "C", 12.6, -5.0, 0.0),
            ("C26", "C", 12.0, -3.7, 0.0),
            ("C27", "C", 10.5, -3.7, 0.0),
            ("C28", "C", 10.2, -2.3, 0.0),
            ("N6", "N", 11.5, -1.9, 0.0),
            ("C29", "C", 12.5, -2.7, 0.0),
            ("O8", "O", 13.6, -2.6, 0.0),
            ("N", "N", 12.3, -5.6, 0.0),
            ("O", "O", 10.0, -9.4, 0.0),
        ],
    )
    add_residues(
        "HETATM", "010", 6,
        [
            ("C", "C", 13.8, -9.9, 0.0),
            ("O", "O", 12.8, -10.1, 0.0),
            ("C1", "C", 15.0, -11.0, 0.0),
            ("C2", "C", 15.7, -12.2, 0.0),
            ("C3", "C", 15.0, -13.4, 0.0),
            ("C4", "C", 13.7, -13.4, 0.0),
            ("C5", "C", 13.0, -12.2, 0.0),
            ("C6", "C", 13.7, -11.0, 0.0),
        ],
    )
    lines.append("LINK         C   02J C   1                 N   ALA C   2")
    lines.append("LINK         C   LEU C   4                 N   PJE C   5")
    lines.append("LINK         C   PJE C   5                 O   010 C   6")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_cage_pdb(
    path: Path,
    *,
    ligand: bool = False,
) -> Path:
    """生成一个带内部空腔的立方“笼子”蛋白，用于口袋检测测试。

    六个面由碳原子组成，顶部中央留一个开口，使内腔与外部溶剂连通。
    这是一个人工结构，仅用于验证几何算法的行为，不代表真实蛋白。
    """
    levels = (-7.0, -3.5, 0.0, 3.5, 7.0)
    coordinates: set[tuple[float, float, float]] = set()
    for fixed in (-7.0, 7.0):
        for a in levels:
            for b in levels:
                coordinates.add((fixed, a, b))
                coordinates.add((a, fixed, b))
                coordinates.add((a, b, fixed))
    coordinates.discard((0.0, 0.0, 7.0))  # 顶部开口
    lines = ["HEADER    TEST POCKET CAGE"]
    serial = 1
    for index, (x, y, z) in enumerate(sorted(coordinates), start=1):
        lines.append(
            atom_line("ATOM", serial, "C", "ALA", "A", index, x, y, z, "C")
        )
        serial += 1
    if ligand:
        ring = [
            ("C1", "C", 1.40, 0.00, 0.00),
            ("C2", "C", 0.70, 1.21, 0.00),
            ("C3", "C", -0.70, 1.21, 0.00),
            ("C4", "C", -1.40, 0.00, 0.00),
            ("C5", "C", -0.70, -1.21, 0.00),
            ("C6", "C", 0.70, -1.21, 0.00),
            ("O1", "O", 2.30, 0.00, 0.00),
            ("O2", "O", -2.30, 0.00, 0.00),
        ]
        for atom_name, element, x, y, z in ring:
            lines.append(
                atom_line("HETATM", serial, atom_name, "LIG", "A", 900, x, y, z, element)
            )
            serial += 1
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_ligand_pdbqt(path: Path) -> Path:
    text = """MODEL 1
REMARK VINA RESULT:    -7.500      0.000      0.000
ROOT
ATOM      1  C1  LIG L   1      12.000  21.000  31.000  1.00  0.00     0.000 C
ATOM      2  C2  LIG L   1      13.200  21.000  31.000  1.00  0.00     0.000 C
ATOM      3  O1  LIG L   1      10.400  21.000  31.000  1.00  0.00     0.000 OA
ENDROOT
TORSDOF 0
ENDMDL
MODEL 2
REMARK VINA RESULT:    -6.800      1.200      2.300
ROOT
ATOM      1  C1  LIG L   1      12.100  21.100  31.100  1.00  0.00     0.000 C
ATOM      2  C2  LIG L   1      13.300  21.100  31.100  1.00  0.00     0.000 C
ATOM      3  O1  LIG L   1      10.500  21.100  31.100  1.00  0.00     0.000 OA
ENDROOT
TORSDOF 0
ENDMDL
"""
    path.write_text(text, encoding="utf-8")
    return path
