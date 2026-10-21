"""靶点（受体）预处理。

蛋白/多肽/核酸流程：
    PDBFixer 替换非标准残基 → 移除水和小分子配体 → 补全缺失残基和原子
    → 按 pH 7.4 加氢 → OpenBabel 生成刚性 PDBQT（Gasteiger 电荷）

小分子靶点（分子-分子对接）走 :func:`prepare_rigid_target`。

若 PDBFixer 不可用，会退化到 OpenBabel 的加氢与格式转换，并在结果中明确警告。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from ..core import pdbio
from ..core.models import PrepOutcome
from ..errors import DependencyError, InputError, PreparationError
from ..logging_setup import get_logger
from ..tools.obabel import OpenBabelAdapter, get_openbabel
from ..tools.runtime import work_directory
from .ligand import LigandPrepOptions, prepare_rigid_target

log = get_logger("receptor")

Progress = Callable[[str], None] | None


@dataclass
class ReceptorPrepOptions:
    ph: float = 7.4
    add_hydrogens: bool = True
    remove_heterogens: bool = True
    keep_cofactors: bool = False
    chains: tuple[str, ...] = ()
    seed: int = 42
    minimize: bool = True
    extra_warnings: list[str] = field(default_factory=list)


def _notify(progress: Progress, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            pass


def _first_model(pdb_path: Path, output_path: Path) -> Path:
    """如果 PDB 含多个 MODEL，只保留第一个（NMR 结构常见）。"""
    text = pdb_path.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"^MODEL\b", text, re.M):
        return pdb_path
    model_index = 0
    lines: list[str] = []
    in_model = False
    for line in text.splitlines():
        if line.startswith("MODEL"):
            model_index += 1
            if model_index == 1:
                in_model = True
            continue
        if line.startswith("ENDMDL"):
            if in_model:
                break
            continue
        if in_model:
            lines.append(line)
    if not lines:
        return pdb_path
    output_path.write_text("\n".join(lines + ["END"]) + "\n", encoding="utf-8")
    return output_path


def _filter_heterogens_for_cofactors(pdb_path: Path, output_path: Path) -> tuple[Path, int]:
    """保留非水、非离子的小分子辅因子，供高级用户使用。"""
    removed = 0
    lines: list[str] = []
    for line in pdb_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("HETATM"):
            resname = line[17:20].strip().upper()
            if resname in pdbio.WATERS or resname in pdbio.IONS or resname in pdbio.ADDITIVES:
                removed += 1
                continue
        lines.append(line)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path, removed


def _prepare_with_pdbfixer(
    source: Path,
    output_pdb: Path,
    options: ReceptorPrepOptions,
    warnings: list[str],
    progress: Progress,
) -> dict[str, Any]:
    try:
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
    except ImportError as exc:
        raise DependencyError("未安装 PDBFixer / OpenMM") from exc

    _notify(progress, "加载结构并检查完整性…")
    fixer = PDBFixer(filename=str(source))

    # 链选择（按 index，避免重复链 ID 或空链 ID 造成误删）
    chains = list(fixer.topology.chains())
    available_chains = [chain.id for chain in chains]
    unique_chains = sorted(set(available_chains))
    if options.chains:
        wanted = set(options.chains)
        missing = wanted - set(available_chains)
        if missing:
            raise InputError(
                f"结构中没有这些链：{', '.join(sorted(missing))}。"
                f"可用链：{', '.join(chain or '(空)' for chain in unique_chains)}"
            )
        remove_indices = [
            index for index, chain in enumerate(chains) if chain.id not in wanted
        ]
        if remove_indices:
            fixer.removeChains(chainIndices=remove_indices)
            warnings.append(f"已只保留链 {', '.join(sorted(wanted))}")

    nonstandard = []
    try:
        fixer.findNonstandardResidues()
        nonstandard = list(fixer.nonstandardResidues)
        if nonstandard:
            fixer.replaceNonstandardResidues()
            warnings.append(f"已替换 {len(nonstandard)} 个非标准残基（如 MSE）")
    except Exception as exc:
        warnings.append(f"非标准残基处理失败：{exc}")

    removed_metals = 0
    removed_ligands = 0
    if options.keep_cofactors:
        warnings.append("已保留非水小分子辅因子；请确认它们确实应作为受体的一部分")
    elif options.remove_heterogens:
        for atom in pdbio.parse_atoms(source):
            if atom.record == "HETATM":
                if atom.resname in pdbio.IONS:
                    removed_metals += 1
                elif atom.resname not in pdbio.WATERS and atom.resname not in pdbio.ADDITIVES:
                    removed_ligands += 1
        fixer.removeHeterogens(keepWater=False)
        if removed_metals:
            warnings.append(f"已移除 {removed_metals} 个金属离子/离子原子")
        if removed_ligands:
            warnings.append(f"已移除 {removed_ligands} 个非蛋白原子（共晶配体、辅因子等）")

    final_chains = sorted({chain.id for chain in fixer.topology.chains()})
    if len(final_chains) > 1 and not options.chains:
        warnings.append(
            f"结构包含 {len(final_chains)} 条链（{', '.join(c or '(空)' for c in final_chains)}），"
            "已全部保留。若是二聚体/多聚体，建议只选择与配体结合的那条链。"
        )

    _notify(progress, "补全缺失的残基和原子…")
    added_residues = 0
    added_atoms = 0
    try:
        fixer.findMissingResidues()
        added_residues = sum(len(items) for items in fixer.missingResidues.values())
        fixer.findMissingAtoms()
        added_atoms = sum(len(items) for items in fixer.missingAtoms.values())
        fixer.addMissingAtoms(seed=options.seed)
    except Exception as exc:
        warnings.append(f"补全缺失原子时出现问题：{exc}")
        log.warning("PDBFixer addMissingAtoms 失败：%s", exc, exc_info=True)

    if added_residues:
        warnings.append(f"补全了 {added_residues} 个缺失残基")
        if added_residues > 30:
            warnings.append("补全的残基较多，模型可靠性可能下降，建议检查关键区域")
    if added_atoms:
        warnings.append(f"补全了 {added_atoms} 个缺失原子")

    if options.add_hydrogens:
        _notify(progress, f"按 pH {options.ph:.1f} 添加氢原子…")
        try:
            fixer.addMissingHydrogens(options.ph)
        except Exception as exc:
            warnings.append(f"按 pH 加氢失败（{exc}），将使用 OpenBabel 补氢")

    _notify(progress, "写出修复后的结构…")
    output_pdb.parent.mkdir(parents=True, exist_ok=True)
    with open(output_pdb, "w", encoding="utf-8") as handle:
        PDBFile.writeFile(fixer.topology, fixer.positions, handle, keepIds=True)

    atom_count = fixer.topology.getNumAtoms()
    residue_count = sum(1 for _ in fixer.topology.residues())
    if atom_count < 10:
        raise PreparationError("修复后的结构原子数过少，可能不是有效的蛋白结构")
    return {
        "atoms": atom_count,
        "residues": residue_count,
        "added_residues": added_residues,
        "added_atoms": added_atoms,
        "chains": final_chains,
        "method": "PDBFixer",
        "nonstandard": len(nonstandard),
    }


def _prepare_with_openbabel(
    source: Path,
    output_pdb: Path,
    output_pdbqt: Path,
    options: ReceptorPrepOptions,
    obabel: OpenBabelAdapter,
    warnings: list[str],
    progress: Progress,
) -> PrepOutcome:
    _notify(progress, "使用 OpenBabel 修复并转换结构…")
    ok, message = obabel.convert(
        source,
        output_pdb,
        out_format="pdb",
        add_hydrogens=options.add_hydrogens,
        ph=options.ph,
    )
    if not ok:
        raise PreparationError(f"OpenBabel 无法读取受体结构：{message}")
    ok, message = obabel.receptor_to_pdbqt(output_pdb, output_pdbqt)
    if not ok:
        raise PreparationError(f"OpenBabel 生成受体 PDBQT 失败：{message}")
    valid, validation, stats = pdbio.validate_pdbqt(output_pdbqt, receptor=True)
    if not valid:
        raise PreparationError(f"生成的受体 PDBQT 不可用：{validation}")
    warnings.append("未使用 PDBFixer：缺失残基/原子不会被补全，建议安装 PDBFixer")
    return PrepOutcome(
        ok=True,
        method="OpenBabel（未修复）",
        message="已转换为对接格式，但结构未经过完整修复",
        details={"atoms": stats.get("atoms", 0)},
        warnings=warnings,
    )


def _from_existing_pdbqt(
    input_path: Path,
    output_pdbqt: Path,
    output_pdb: Path,
    obabel: OpenBabelAdapter,
    warnings: list[str],
) -> PrepOutcome:
    text = input_path.read_text(encoding="utf-8", errors="replace")
    has_tree = bool(re.search(r"^(ROOT|TORSDOF)\b", text, re.M))
    if has_tree:
        stripped = [
            line for line in text.splitlines()
            if not re.match(r"^(ROOT|ENDROOT|BRANCH|ENDBRANCH|TORSDOF)\b", line)
        ]
        output_pdbqt.parent.mkdir(parents=True, exist_ok=True)
        output_pdbqt.write_text("\n".join(stripped) + "\n", encoding="utf-8")
        warnings.append("输入 PDBQT 含柔性扭转树，已移除并作为刚性受体使用")
    else:
        shutil.copy2(input_path, output_pdbqt)
    valid, message, stats = pdbio.validate_pdbqt(output_pdbqt, receptor=True)
    if not valid:
        raise PreparationError(f"提供的受体 PDBQT 不可用：{message}")
    converted = False
    if obabel.available:
        converted, _ = obabel.convert(output_pdbqt, output_pdb, out_format="pdb")
    if not converted:
        atoms = pdbio.parse_pdbqt_atoms(output_pdbqt)
        pdbio.write_pdb_atoms(atoms, output_pdb, resname="REC", chain="A")
    return PrepOutcome(
        ok=True,
        method="已有 PDBQT",
        message="已校验现有受体文件",
        details={"atoms": stats.get("atoms", 0)},
        warnings=warnings,
    )


def _exclude_ligand_chains(
    source: Path,
    temp: Path,
    options: ReceptorPrepOptions,
    warnings: list[str],
) -> tuple[Path, ReceptorPrepOptions]:
    """移除共晶配体所在的短链。

    肽类抑制剂常被拆成标准氨基酸（ATOM）和非标准残基（HETATM），
    PDBFixer 的 ``removeHeterogens`` 只能删掉后者，标准残基会留在受体里
    并占据结合位点。这里按链整体移除，保证受体干净。
    """
    if not options.remove_heterogens:
        return source, options
    try:
        ligand_chains = pdbio.find_ligand_chains(source)
    except Exception as exc:
        warnings.append(f"配体链检测失败：{exc}")
        return source, options
    if not ligand_chains:
        return source, options

    ligand_ids = {chain.chain for chain in ligand_chains}
    labels = "、".join(chain.label for chain in ligand_chains)
    if options.chains:
        wanted = set(options.chains)
        kept = wanted - ligand_ids
        if not kept:
            raise InputError(
                f"选择的链 {', '.join(sorted(wanted))} 是共晶配体（{labels}），不能作为受体。"
                "请选择蛋白链，或把配体作为待对接分子导入。"
            )
        if kept != wanted:
            warnings.append(f"已从受体链选择中排除共晶配体链（{labels}）")
            options = replace(options, chains=tuple(sorted(kept)))
    else:
        warnings.append(f"已自动排除共晶配体链（{labels}），避免其占据结合位点")

    output = temp / "receptor_without_ligand_chains.pdb"
    if pdbio.remove_chains(source, output, ligand_ids) <= 0:
        return source, options
    return output, options


def prepare_receptor(
    input_path: str | Path,
    output_pdbqt: str | Path,
    output_pdb: str | Path,
    *,
    options: ReceptorPrepOptions | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
) -> PrepOutcome:
    options = options or ReceptorPrepOptions()
    input_path = Path(input_path)
    output_pdbqt = Path(output_pdbqt)
    output_pdb = Path(output_pdb)
    obabel = obabel or get_openbabel()
    warnings: list[str] = list(options.extra_warnings)
    if not input_path.is_file():
        raise InputError(f"靶点文件不存在：{input_path}")
    output_pdbqt.parent.mkdir(parents=True, exist_ok=True)
    output_pdb.parent.mkdir(parents=True, exist_ok=True)

    fmt = pdbio.detect_format(input_path)
    if fmt == "pdbqt":
        return _from_existing_pdbqt(input_path, output_pdbqt, output_pdb, obabel, warnings)

    _notify(progress, "检查结构类型…")
    kind, detail = pdbio.structure_kind(input_path) if fmt in {"pdb", "mol2"} else ("macromolecule", "mmCIF 结构")
    if kind == "small_molecule":
        raise InputError(
            "该文件是小分子，应作为“待对接分子”处理。"
            "如果要做分子-分子对接，请把其中一个设为目标分子。"
        )
    if kind == "unknown" and fmt != "cif":
        raise InputError(f"无法识别该结构：{detail}")

    with work_directory("receptor") as temp:
        source = input_path
        if fmt == "pdb":
            source = _first_model(input_path, temp / "first_model.pdb")
            if options.keep_cofactors:
                source, removed = _filter_heterogens_for_cofactors(source, temp / "with_cofactors.pdb")
                if removed:
                    warnings.append(f"保留辅因子时移除了 {removed} 个水/离子原子")
            elif options.remove_heterogens:
                pass  # PDBFixer 内部移除

        prepared_pdb: Path | None = None
        details: dict[str, Any] = {}
        if obabel.available:
            # 提前把 mmCIF 转成 PDB，便于统一处理与输出
            if fmt == "cif":
                converted = temp / "converted.pdb"
                ok, message = obabel.convert(source, converted, out_format="pdb")
                if ok:
                    source = converted
                else:
                    warnings.append(f"mmCIF 转 PDB 失败（{message}），将直接交给 PDBFixer")

        # 肽类共晶配体可能同时包含 ATOM 与 HETATM 残基，必须在修复前整链移除。
        source, options = _exclude_ligand_chains(source, temp, options, warnings)

        try:
            _notify(progress, "修复结构（PDBFixer）…")
            details = _prepare_with_pdbfixer(
                source, output_pdb, options, warnings, progress
            )
            prepared_pdb = output_pdb
        except DependencyError:
            warnings.append("未安装 PDBFixer/OpenMM，改用 OpenBabel 的简化流程")
        except Exception as exc:
            warnings.append(f"PDBFixer 修复失败（{exc}），改用 OpenBabel 的简化流程")
            log.warning("PDBFixer 失败：%s", exc, exc_info=True)

        if prepared_pdb is None:
            if not obabel.available:
                raise DependencyError(
                    "受体准备需要 OpenBabel；当前既没有可用的 PDBFixer，也没有 OpenBabel。"
                )
            outcome = _prepare_with_openbabel(
                source, output_pdb, output_pdbqt, options, obabel, warnings, progress
            )
            outcome.warnings = warnings
            return outcome

        _notify(progress, "转换为受体 PDBQT（Gasteiger 电荷）…")
        ok, message = obabel.receptor_to_pdbqt(prepared_pdb, output_pdbqt)
        if not ok:
            # 某些结构 PDBFixer 加氢后 OpenBabel 仍无法直接写出 PDBQT
            raise PreparationError(f"OpenBabel 生成受体 PDBQT 失败：{message}")
        valid, validation, stats = pdbio.validate_pdbqt(output_pdbqt, receptor=True)
        if not valid:
            raise PreparationError(f"生成的受体 PDBQT 不可用：{validation}")

        details.update(
            {
                "atoms": stats.get("atoms", details.get("atoms", 0)),
                "ph": options.ph,
                "chains_selected": list(options.chains),
            }
        )
        return PrepOutcome(
            ok=True,
            method=details.get("method", "PDBFixer") + " + OpenBabel",
            message="受体结构已准备完成",
            details=details,
            warnings=warnings,
        )


def prepare_target(
    input_path: str | Path,
    output_pdbqt: str | Path,
    output_pdb: str | Path,
    *,
    options: ReceptorPrepOptions | None = None,
    ligand_options: LigandPrepOptions | None = None,
    obabel: OpenBabelAdapter | None = None,
    progress: Progress = None,
) -> PrepOutcome:
    """自动判断大分子/小分子靶点并准备。"""
    options = options or ReceptorPrepOptions()
    obabel = obabel or get_openbabel()
    input_path = Path(input_path)
    fmt = pdbio.detect_format(input_path)
    if fmt in {"pdb", "mol2"}:
        kind, _ = pdbio.structure_kind(input_path)
        if kind == "small_molecule":
            ligand_options = ligand_options or LigandPrepOptions(ph=options.ph)
            return prepare_rigid_target(
                input_path,
                output_pdbqt,
                output_pdb,
                options=ligand_options,
                obabel=obabel,
                progress=progress,
            )
    if fmt in {"sdf", "mol", "smi", "smiles", "inchi"}:
        ligand_options = ligand_options or LigandPrepOptions(ph=options.ph)
        return prepare_rigid_target(
            input_path,
            output_pdbqt,
            output_pdb,
            options=ligand_options,
            obabel=obabel,
            progress=progress,
        )
    return prepare_receptor(
        input_path,
        output_pdbqt,
        output_pdb,
        options=options,
        obabel=obabel,
        progress=progress,
    )


def inspect_target(path: str | Path, obabel: OpenBabelAdapter | None = None) -> dict[str, Any]:
    """返回用于界面展示的靶点摘要。"""
    path = Path(path)
    fmt = pdbio.detect_format(path)
    info: dict[str, Any] = {"path": str(path), "format": fmt, "ok": False}
    if fmt == "pdbqt":
        atoms = pdbio.parse_pdbqt_atoms(path)
        heavy = [a for a in atoms if not a.is_hydrogen]
        info.update(
            {
                "ok": True,
                "kind": "macromolecule",
                "atoms": len(heavy),
                "chains": sorted({a.chain for a in heavy if a.chain}),
                "residues": len({a.residue_key for a in heavy}),
                "co_crystal": [],
                "sites": [],
            }
        )
        return info
    if fmt == "cif":
        adapter = obabel or get_openbabel()
        if adapter.available:
            try:
                with work_directory("inspect_cif") as temp:
                    converted = temp / "inspect.pdb"
                    ok, _ = adapter.convert(path, converted, out_format="pdb")
                    if ok:
                        nested = inspect_target(converted, adapter)
                        nested.update({"path": str(path), "format": "cif", "converted": True})
                        return nested
            except Exception:
                pass
        info.update({"kind": "macromolecule", "note": "mmCIF 结构将在准备时转换"})
        return info
    if fmt not in {"pdb", "mol2"}:
        info.update({"kind": "unknown", "note": f"不支持的格式：{fmt}"})
        return info

    atoms = pdbio.parse_atoms(path)
    heavy = [a for a in atoms if not a.is_hydrogen]
    kind, detail = pdbio.structure_kind(path)
    chains = pdbio.protein_chains(path)
    sites = pdbio.find_ligand_sites(path)
    site_records = pdbio.parse_site_records(path)
    info.update(
        {
            "ok": True,
            "kind": kind,
            "detail": detail,
            "atoms": len(heavy),
            "residues": len({a.residue_key for a in heavy}),
            "chains": chains,
            "co_crystal": [
                {"label": site.label, "resname": site.resname, "atoms": site.heavy_atom_count}
                for site in sites[:8]
            ],
            "sites": len(site_records),
        }
    )
    return info
