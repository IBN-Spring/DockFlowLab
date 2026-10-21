"""RDKit 文件读写适配。

RDKit 的 C++ 文件接口在 Windows 上不能可靠处理非 ASCII 路径（例如
``C:\\Users\\...\\OneDrive\\桌面\\项目\\ligand.sdf``），会抛出
``OSError: File error: Bad input file``。这里统一用 Python 的文件对象读入
文本，再交给 RDKit 的 ``*Block`` 解析器；写文件也先得到字符串再由 Python
写入。这样中文、空格和 OneDrive 路径都能正常工作。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 只吃掉分隔符所在行，不能把下一条记录的空标题行一起吞掉。
_SDF_DELIMITER = re.compile(r"(?m)^\$\$\$\$[ \t]*\r?\n?")


def _read_text(path: str | Path) -> str:
    # utf-8-sig 会去掉 Windows 编辑器可能写入的 BOM，避免首条记录解析失败。
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def _prepare_loose(molecule: Any) -> Any:
    """宽松解析后补齐隐式价态，避免后续 AddHs 触发 RDKit 前置条件错误。"""
    try:
        molecule.UpdatePropertyCache(strict=False)
    except Exception:
        pass
    return molecule


def _sdf_blocks(text: str) -> list[str]:
    blocks = [block for block in _SDF_DELIMITER.split(text) if block.strip()]
    result: list[str] = []
    for block in blocks:
        end = block.find("M  END")
        if end >= 0:
            block = block[: end + len("M  END")]
        # 空标题是合法 SDF：保留行首空行，RDF 的计数行必须仍在第 4 行。
        block = block.rstrip("\r\n")
        if block:
            result.append(block + "\n")
    return result


def read_sdf_molecules(
    path: str | Path, *, remove_hs: bool = False
) -> tuple[list[Any], bool]:
    """读取 SDF 中的全部有效记录。返回 (分子列表, 是否使用了宽松解析)。"""
    from rdkit import Chem

    blocks = _sdf_blocks(_read_text(path))
    if not blocks:
        return [], False
    for sanitize in (True, False):
        molecules: list[Any] = []
        for block in blocks:
            try:
                molecule = Chem.MolFromMolBlock(
                    block, removeHs=remove_hs, sanitize=sanitize
                )
            except Exception:
                molecule = None
            if molecule is not None:
                if not sanitize:
                    molecule = _prepare_loose(molecule)
                molecules.append(molecule)
        if molecules:
            return molecules, not sanitize
    return [], False


def _read_with_block_parser(
    path: str | Path,
    parser: Any,
    *,
    remove_hs: bool,
) -> tuple[Any | None, bool]:
    text = _read_text(path)
    for sanitize in (True, False):
        try:
            molecule = parser(text, removeHs=remove_hs, sanitize=sanitize)
        except Exception:
            molecule = None
        if molecule is not None:
            if not sanitize:
                molecule = _prepare_loose(molecule)
            return molecule, not sanitize
    return None, False


def read_mol(path: str | Path, *, remove_hs: bool = False) -> tuple[Any | None, bool]:
    from rdkit import Chem

    return _read_with_block_parser(Path(path), Chem.MolFromMolBlock, remove_hs=remove_hs)


def read_mol2(path: str | Path, *, remove_hs: bool = False) -> tuple[Any | None, bool]:
    from rdkit import Chem

    return _read_with_block_parser(Path(path), Chem.MolFromMol2Block, remove_hs=remove_hs)


def read_pdb(path: str | Path, *, remove_hs: bool = False) -> tuple[Any | None, bool]:
    from rdkit import Chem

    return _read_with_block_parser(Path(path), Chem.MolFromPDBBlock, remove_hs=remove_hs)


def write_sdf(molecule: Any, path: str | Path) -> None:
    """把单个分子写成 SDF；不经过 RDKit 的路径接口。"""
    from rdkit import Chem

    block = Chem.MolToMolBlock(molecule)
    if not block.endswith("\n"):
        block += "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block + "$$$$\n", encoding="utf-8")
