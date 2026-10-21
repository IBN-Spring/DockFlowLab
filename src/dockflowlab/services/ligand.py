"""配体（待对接分子）预处理。

主流程（可靠、可复现）：
    读取结构 → 只保留最大有机片段 → 补氢 → 2D 转 3D（ETKDGv3）
    → MMFF94s/UFF 能量最小化 → 生理 pH 质子化校正（可选）
    → Meeko 生成 PDBQT

回退流程：
    OpenBabel 读取、加氢、生成 3D、Gasteiger 电荷、写出 PDBQT。

所有中间文件都放在 ASCII 临时目录，输出写回项目。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core import pdbio
from ..core.models import PrepOutcome
from ..errors import DependencyError, InputError, PreparationError
from ..logging_setup import get_logger
from ..tools import rdkit_io
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import work_directory

log = get_logger("ligand")

Progress = Callable[[str], None] | None


def _classify_ligand(path: Path) -> tuple[str, str]:
    """按“候选分子”角色判断结构类型。

    SDF/MOL/SMILES 等格式天然是小分子；PDB/PDBQT 可能是小分子、短肽，
    也可能是误导入的完整蛋白质，需要区分。
    """
    fmt = pdbio.detect_format(path)
    if fmt in {"pdb", "pdbqt"}:
        return pdbio.structure_kind(path)
    if fmt in {"sdf", "mol", "mol2", "smi", "smiles", "inchi"}:
        return "small_molecule", f"{fmt.upper()} 分子文件"
    return "unknown", f"无法识别的格式：{fmt}"


def _check_ligand_kind(kind: str, detail: str, warnings: list[str]) -> None:
    if kind in {"protein", "peptide"}:
        raise PreparationError(
            f"该文件包含蛋白质或多肽结构（{detail}），不能放在“候选分子”里。"
            "本地版本暂不支持蛋白–蛋白、蛋白–多肽对接；"
            "请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。"
        )
    if kind == "nucleic_acid":
        raise PreparationError(
            "该文件是核酸结构，不能作为候选分子。"
            "核酸–小分子对接请把核酸放在“靶点结构”一栏。"
        )
    if kind == "unknown":
        raise PreparationError(
            f"无法识别这个结构的类型（{detail}）。请确认文件包含有效的小分子，"
            "或先转换为 SDF/MOL2 后再导入。"
        )


@dataclass
class LigandPrepOptions:
    ph: float = 7.4
    ph_correction: bool = True
    minimize: bool = True
    max_iters: int = 500
    random_seed: int = 42
    remove_salts: bool = True
    forcefield: str = "auto"  # auto / MMFF94s / UFF / none
    keep_names: bool = True
    extra_warnings: list[str] = field(default_factory=list)


def _notify(progress: Progress, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            pass


def _rdkit() -> tuple[Any, Any, Any]:
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors

        RDLogger.DisableLog("rdApp.warning")
        return Chem, AllChem, (Descriptors, rdMolDescriptors)
    except ImportError as exc:
        raise DependencyError(
            "未安装 RDKit，无法进行高质量配体预处理。请安装 rdkit。"
        ) from exc


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------
def read_molecule(path: str | Path, obabel: OpenBabelAdapter | None = None) -> tuple[Any, list[str]]:
    """用 RDKit 读取分子；必要时借助 OpenBabel 转换。返回 (mol, 警告)。"""
    Chem, _, _ = _rdkit()
    path = Path(path)
    fmt = pdbio.detect_format(path)
    warnings: list[str] = []
    mol = None

    if fmt == "sdf":
        mols, loose = rdkit_io.read_sdf_molecules(path, remove_hs=False)
        if loose and mols:
            warnings.append("SDF 结构存在化学价态问题，已按宽松模式读取")
        if mols:
            # 多记录 SDF 按文件顺序取第一条，与界面提示保持一致；需要批量
            # 筛选时请把记录拆成单独文件后分别导入。
            mol = mols[0]
            if len(mols) > 1:
                warnings.append(f"SDF 包含 {len(mols)} 条记录，已选择第一条")
    elif fmt == "mol":
        mol, loose = rdkit_io.read_mol(path, remove_hs=False)
        if loose and mol is not None:
            warnings.append("MOL 结构存在化学价态问题，已按宽松模式读取")
    elif fmt == "mol2":
        mol, loose = rdkit_io.read_mol2(path, remove_hs=False)
        if loose and mol is not None:
            warnings.append("MOL2 结构存在化学价态问题，已按宽松模式读取")
    elif fmt == "pdb":
        mol, _ = rdkit_io.read_pdb(path, remove_hs=False)
        if mol is not None and mol.GetNumAtoms() > 2 and mol.GetNumBonds() == 0:
            mol = None
    elif fmt == "smi":
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        first = next((line.split()[0] for line in text.splitlines() if line.strip()), "")
        if first:
            mol = Chem.MolFromSmiles(first)
    elif fmt == "inchi":
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        first = next((line.strip() for line in text.splitlines() if line.startswith("InChI=")), "")
        if first:
            mol = Chem.MolFromInchi(first)
    else:
        raise InputError(f"无法识别的配体格式：{path.suffix or '未知'}")

    if mol is None:
        # OpenBabel 兜底：先转成 SDF 再交给 RDKit
        adapter = obabel or get_openbabel()
        if adapter.available:
            with work_directory("ligand_read") as temp:
                converted = temp / "converted.sdf"
                ok, message = adapter.convert(path, converted, out_format="sdf")
                if ok:
                    mol, _ = rdkit_io.read_mol(converted, remove_hs=False)
                    if mol is not None:
                        warnings.append("已通过 OpenBabel 转换后读取结构")
        if mol is None:
            raise PreparationError(
                f"无法读取分子结构：{path.name}。请确认文件内容完整、格式正确。"
            )
    return mol, warnings


# ----------------------------------------------------------------------
# 结构整理
# ----------------------------------------------------------------------
def _largest_organic_fragment(mol: Any, warnings: list[str]) -> Any:
    Chem, _, _ = _rdkit()
    try:
        fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    except Exception:
        try:
            fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
        except Exception:
            return mol
    if len(fragments) <= 1:
        return mol
    organic = [
        fragment for fragment in fragments
        if any(atom.GetAtomicNum() == 6 for atom in fragment.GetAtoms())
    ]
    candidates = organic or list(fragments)
    largest = max(candidates, key=lambda fragment: fragment.GetNumHeavyAtoms())
    removed = sum(fragment.GetNumHeavyAtoms() for fragment in fragments) - largest.GetNumHeavyAtoms()
    if removed > 0:
        warnings.append(f"检测到盐/溶剂碎片，已保留最大的有机分子（移除 {removed} 个重原子）")
    return largest


def _has_3d(mol: Any) -> bool:
    if mol.GetNumConformers() == 0:
        return False
    try:
        conformer = mol.GetConformer(0)
        if not conformer.Is3D():
            return False
        return any(abs(conformer.GetAtomPosition(i).z) > 1e-3 for i in range(mol.GetNumAtoms()))
    except Exception:
        return False


def _ensure_3d(mol: Any, options: LigandPrepOptions, warnings: list[str]) -> Any:
    Chem, AllChem, _ = _rdkit()
    if _has_3d(mol):
        return mol
    mol = Chem.AddHs(mol, addCoords=mol.GetNumConformers() > 0)
    params = AllChem.ETKDGv3()
    params.randomSeed = int(options.random_seed)
    params.numThreads = 1
    params.maxIterations = 200
    params.useRandomCoords = False
    status = AllChem.EmbedMolecule(mol, params)
    if status != 0:
        params.useRandomCoords = True
        params.randomSeed = int(options.random_seed)
        status = AllChem.EmbedMolecule(mol, params)
    if status != 0:
        raise PreparationError(
            "无法为分子生成三维构象。请检查结构是否包含异常价态或金属原子，"
            "也可以尝试提供已含三维坐标的 SDF/MOL2 文件。"
        )
    warnings.append("输入为二维结构，已用 ETKDGv3 生成三维构象")
    return mol


def _minimize(mol: Any, options: LigandPrepOptions, warnings: list[str]) -> tuple[Any, str]:
    Chem, AllChem, (Descriptors, _) = _rdkit()
    if not options.minimize or options.forcefield == "none":
        return mol, "未最小化"
    prefers_uff = options.forcefield == "UFF"
    try:
        if not prefers_uff and AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol, maxIters=options.max_iters, mmffVariant="MMFF94s")
            return mol, "MMFF94s"
        if AllChem.UFFHasAllMoleculeParams(mol):
            AllChem.UFFOptimizeMolecule(mol, maxIters=options.max_iters)
            return mol, "UFF"
    except Exception as exc:
        warnings.append(f"能量最小化失败（{exc}），继续使用原始构象")
    warnings.append("当前分子缺少可用的 MMFF94s/UFF 参数，未进行能量最小化")
    return mol, "无可用力场"


def _ph_correction(mol: Any, options: LigandPrepOptions, obabel: OpenBabelAdapter, warnings: list[str]) -> tuple[Any, bool]:
    """用 OpenBabel 的 pH 模型校正质子化状态。失败时保持原分子。"""
    if not options.ph_correction or not obabel.available:
        return mol, False
    try:
        with work_directory("ph") as temp:
            source = temp / "input.sdf"
            output = temp / "output.sdf"
            rdkit_io.write_sdf(mol, source)
            ok, _ = obabel.convert(source, output, out_format="sdf", add_hydrogens=True, ph=options.ph)
            if not ok:
                warnings.append("生理 pH 质子化校正失败，保留输入结构的质子化状态")
                return mol, False
            corrected, _ = rdkit_io.read_mol(output, remove_hs=False)
            if corrected is None or corrected.GetNumHeavyAtoms() != mol.GetNumHeavyAtoms():
                warnings.append("质子化校正结果异常，保留原始结构")
                return mol, False
            return corrected, True
    except Exception as exc:
        warnings.append(f"生理 pH 质子化校正失败：{exc}")
        return mol, False


def _meeko_pdbqt(mol: Any) -> str:
    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
    except ImportError as exc:
        raise DependencyError(f"Meeko 导入失败：{exc}") from exc
    preparator = MoleculePreparation()
    setups = preparator.prepare(mol)
    if not setups:
        raise PreparationError("Meeko 未能生成可对接的构象")
    pdbqt_string, ok, error = PDBQTWriterLegacy.write_string(setups[0])
    if not ok or not pdbqt_string.strip():
        raise PreparationError(f"Meeko 写出 PDBQT 失败：{error or '未知错误'}")
    return pdbqt_string


def _prepare_with_openbabel(
    input_path: Path,
    output_pdbqt: Path,
    obabel: OpenBabelAdapter,
    *,
    add_hydrogens: bool,
    gen3d: bool,
    warnings: list[str],
) -> PrepOutcome:
    ok, message = obabel.ligand_to_pdbqt(
        input_path, output_pdbqt, add_hydrogens=add_hydrogens, gen3d=gen3d
    )
    if not ok:
        raise PreparationError(f"OpenBabel 生成 PDBQT 失败：{message}")
    valid, validation, stats = pdbio.validate_pdbqt(output_pdbqt)
    if not valid:
        raise PreparationError(f"OpenBabel 生成的 PDBQT 不可用：{validation}")
    warnings.append("已使用 OpenBabel 回退流程（未进行 RDKit 力场优化）")
    return PrepOutcome(
        ok=True,
        method="OpenBabel",
        message="已生成 PDBQT（OpenBabel 回退）",
        details={"atoms": stats.get("atoms", 0)},
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def prepare_ligand(
    input_path: str | Path,
    output_pdbqt: str | Path,
    output_sdf: str | Path | None = None,
    *,
    options: LigandPrepOptions | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
) -> PrepOutcome:
    options = options or LigandPrepOptions()
    input_path = Path(input_path)
    output_pdbqt = Path(output_pdbqt)
    obabel = obabel or get_openbabel()
    warnings: list[str] = []
    if not input_path.is_file():
        raise InputError(f"配体文件不存在：{input_path}")

    output_pdbqt.parent.mkdir(parents=True, exist_ok=True)
    if output_sdf is not None:
        Path(output_sdf).parent.mkdir(parents=True, exist_ok=True)

    kind, detail = _classify_ligand(input_path)
    _check_ligand_kind(kind, detail, warnings)

    # 已经是 PDBQT：直接校验并复用
    if pdbio.detect_format(input_path) == "pdbqt":
        _notify(progress, "检测到 PDBQT 文件，正在校验…")
        valid, message, stats = pdbio.validate_pdbqt(input_path)
        if not valid:
            raise PreparationError(f"提供的 PDBQT 不可用：{message}")
        shutil.copy2(input_path, output_pdbqt)
        if output_sdf and obabel.available:
            with work_directory("pdbqt2sdf") as temp:
                converted = temp / "ligand.sdf"
                if obabel.convert(input_path, converted, out_format="sdf")[0]:
                    shutil.copy2(converted, output_sdf)
        return PrepOutcome(
            ok=True,
            method="已有 PDBQT",
            message="已校验现有 PDBQT 文件",
            details={"atoms": stats.get("atoms", 0), "kind": kind},
            warnings=warnings,
        )

    Chem, AllChem, (Descriptors, rdMolDescriptors) = _rdkit()
    _notify(progress, "读取分子结构…")
    mol, read_warnings = read_molecule(input_path, obabel)
    warnings.extend(read_warnings)

    if options.remove_salts:
        mol = _largest_organic_fragment(mol, warnings)
    heavy_atoms = mol.GetNumHeavyAtoms()
    if heavy_atoms < 2:
        raise PreparationError("分子重原子数过少，无法作为配体")
    if heavy_atoms > 200:
        warnings.append(f"分子较大（{heavy_atoms} 个重原子），对接搜索空间和耗时都会显著增加")

    _notify(progress, "补全氢原子…")
    try:
        if mol.GetNumConformers() > 0:
            mol = Chem.AddHs(mol, addCoords=True)
        else:
            mol = Chem.AddHs(mol)
    except Exception as exc:
        raise PreparationError(
            f"无法为分子补全氢原子：{exc}。请检查结构中是否存在异常价态或金属原子，"
            "也可以用 OpenBabel 先转换为 SDF 后再导入。"
        ) from exc

    _notify(progress, "检查三维构象…")
    mol = _ensure_3d(mol, options, warnings)

    _notify(progress, "能量最小化…")
    mol, forcefield = _minimize(mol, options, warnings)

    ph_corrected = False
    if options.ph_correction:
        _notify(progress, "按生理 pH 校正质子化状态…")
        mol, ph_corrected = _ph_correction(mol, options, obabel, warnings)

    details = _molecule_details(mol, forcefield, ph_corrected, warnings)
    details["kind"] = kind
    if options.ph_correction:
        details["ph"] = float(options.ph)
    if details["rotatable_bonds"] > 20:
        warnings.append(
            f"可旋转键较多（{details['rotatable_bonds']} 个），Vina 的搜索空间与耗时会显著增加；"
            "结果应视为初步筛选，可缩小盒子或改用多肽/大分子对接工具"
        )

    pdbqt_text: str | None = None
    try:
        _notify(progress, "用 Meeko 生成对接格式…")
        pdbqt_text = _meeko_pdbqt(mol)
    except (DependencyError, PreparationError) as exc:
        warnings.append(f"Meeko 处理失败（{exc}），改用 OpenBabel")
        log.warning("Meeko 失败：%s", exc)

    if pdbqt_text:
        temp_path = output_pdbqt.with_suffix(output_pdbqt.suffix + ".tmp")
        temp_path.write_text(pdbqt_text, encoding="utf-8")
        valid, message, stats = pdbio.validate_pdbqt(temp_path)
        if valid:
            os.replace(temp_path, output_pdbqt)
            if output_sdf:
                rdkit_io.write_sdf(mol, output_sdf)
            return PrepOutcome(
                ok=True,
                method="RDKit + Meeko" + (" + pH 校正" if ph_corrected else ""),
                message="结构已准备完成",
                details={**details, "atoms": stats.get("atoms", 0)},
                warnings=warnings,
            )
        temp_path.unlink(missing_ok=True)
        warnings.append(f"Meeko 生成的 PDBQT 校验失败：{message}")

    # OpenBabel 回退
    _notify(progress, "使用 OpenBabel 回退流程…")
    if not obabel.available:
        raise DependencyError(
            "配体准备失败，且 OpenBabel 不可用。请安装 openbabel-wheel 或 conda 版 openbabel。"
        )
    with work_directory("ligand_fallback") as temp:
        source = temp / "ligand.sdf"
        try:
            rdkit_io.write_sdf(mol, source)
        except Exception as exc:
            raise PreparationError(f"无法写出中间结构：{exc}") from exc
        outcome = _prepare_with_openbabel(
            source,
            output_pdbqt,
            obabel,
            add_hydrogens=False,
            gen3d=False,
            warnings=warnings,
        )
        if output_sdf:
            shutil.copy2(source, output_sdf)
        outcome.details.update(details)
        return outcome


def prepare_rigid_target(
    input_path: str | Path,
    output_pdbqt: str | Path,
    output_pdb: str | Path,
    *,
    options: LigandPrepOptions | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
) -> PrepOutcome:
    """把一个小分子准备成刚性受体（用于分子-分子对接）。"""
    options = options or LigandPrepOptions()
    obabel = obabel or get_openbabel()
    input_path = Path(input_path)
    output_pdbqt = Path(output_pdbqt)
    output_pdb = Path(output_pdb)
    output_pdb.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if pdbio.detect_format(input_path) == "pdbqt":
        valid, message, stats = pdbio.validate_pdbqt(input_path, receptor=True)
        if not valid:
            raise PreparationError(f"提供的靶点 PDBQT 不是刚性格式：{message}")
        shutil.copy2(input_path, output_pdbqt)
        converted = False
        if obabel.available:
            converted, _ = obabel.convert(input_path, output_pdb, out_format="pdb")
        if not converted:
            atoms = pdbio.parse_pdbqt_atoms(input_path)
            pdbio.write_pdb_atoms(atoms, output_pdb, resname="TGT", chain="T")
        return PrepOutcome(
            True,
            "已有 PDBQT",
            "靶点分子已就绪",
            {"atoms": stats.get("atoms", 0), "kind": "small_molecule"},
            warnings,
        )

    with work_directory("rigid_target") as temp:
        flexible = temp / "flexible.pdbqt"
        prepared_sdf = temp / "prepared.sdf"
        outcome = prepare_ligand(
            input_path,
            flexible,
            prepared_sdf,
            options=options,
            obabel=obabel,
            progress=progress,
        )
        text = flexible.read_text(encoding="utf-8", errors="replace")
        rigid_lines = [
            line for line in text.splitlines()
            if not re.match(r"^(ROOT|ENDROOT|BRANCH|ENDBRANCH|TORSDOF)\b", line)
        ]
        rigid_text = "\n".join(rigid_lines) + "\n"
        candidate = temp / "rigid.pdbqt"
        candidate.write_text(rigid_text, encoding="utf-8")
        valid, message, stats = pdbio.validate_pdbqt(candidate, receptor=True)
        if valid:
            shutil.copy2(candidate, output_pdbqt)
        elif obabel.available:
            ok, msg = obabel.convert(
                prepared_sdf, output_pdbqt, out_format="pdbqt", gasteiger=True, rigid=True
            )
            if not ok:
                raise PreparationError(f"无法生成刚性靶点 PDBQT：{msg}")
        else:
            raise PreparationError(f"无法生成刚性靶点 PDBQT：{message}")

        if obabel.available:
            ok, msg = obabel.convert(output_pdbqt, output_pdb, out_format="pdb")
            if not ok:
                atoms = pdbio.parse_pdbqt_atoms(output_pdbqt)
                pdbio.write_pdb_atoms(atoms, output_pdb, resname="TGT", chain="T")
        else:
            atoms = pdbio.parse_pdbqt_atoms(output_pdbqt)
            pdbio.write_pdb_atoms(atoms, output_pdb, resname="TGT", chain="T")

    warnings.extend(outcome.warnings)
    warnings.append("靶点按刚性分子处理；其构象来自输入结构或能量最小化结果")
    return PrepOutcome(
        ok=True,
        method=outcome.method + "（刚性化）",
        message="小分子靶点已准备完成",
        details={
            **outcome.details,
            "atoms": stats.get("atoms", 0),
            "kind": "small_molecule",
        },
        warnings=warnings,
    )


def _molecule_details(mol: Any, forcefield: str, ph_corrected: bool, warnings: list[str]) -> dict[str, Any]:
    _, _, (Descriptors, rdMolDescriptors) = _rdkit()
    try:
        formula = rdMolDescriptors.CalcMolFormula(mol)
        molecular_weight = round(float(Descriptors.MolWt(mol)), 2)
        rotatable = int(rdMolDescriptors.CalcNumRotatableBonds(mol))
    except Exception:
        formula = ""
        molecular_weight = 0.0
        rotatable = 0
    return {
        "formula": formula,
        "molecular_weight": molecular_weight,
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "rotatable_bonds": rotatable,
        "formal_charge": sum(atom.GetFormalCharge() for atom in mol.GetAtoms()),
        "forcefield": forcefield,
        "ph_corrected": ph_corrected,
    }


def inspect_ligand(path: str | Path) -> dict[str, Any]:
    """返回用于界面展示的分子摘要。不会修改文件。"""
    info: dict[str, Any] = {"path": str(path), "format": pdbio.detect_format(path)}
    try:
        mol, warnings = read_molecule(path)
        _, _, (Descriptors, rdMolDescriptors) = _rdkit()
        info.update(
            {
                "formula": rdMolDescriptors.CalcMolFormula(mol),
                "molecular_weight": round(float(Descriptors.MolWt(mol)), 2),
                "heavy_atoms": mol.GetNumHeavyAtoms(),
                "rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(mol)),
                "formal_charge": sum(atom.GetFormalCharge() for atom in mol.GetAtoms()),
                "warnings": warnings,
                "ok": True,
            }
        )
    except Exception as exc:
        atoms = (
            pdbio.parse_first_pose_atoms(path)
            if info["format"] == "pdbqt"
            else pdbio.parse_atoms(path, first_model=True)
        )
        info.update({"ok": False, "error": str(exc), "heavy_atoms": sum(1 for a in atoms if not a.is_hydrogen)})
    return info
