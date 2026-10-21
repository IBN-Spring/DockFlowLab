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
