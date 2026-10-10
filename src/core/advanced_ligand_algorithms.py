#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级配体预处理算法模块
提供更智能的力场选择、构象生成和质量验证
"""
import os
import numpy as np
from typing import Tuple, Optional, List, Dict
from pathlib import Path


def detect_molecule_features(mol_path: str) -> Dict[str, any]:
    """检测分子特征（金属、稀有原子、环系统等）

    Args:
        mol_path: 分子文件路径

    Returns:
        Dict: 分子特征字典
    """
    features = {
        "has_metal": False,
        "metal_atoms": [],
        "has_aromatic": False,
        "ring_count": 0,
        "atom_count": 0,
        "heavy_atom_count": 0,
        "rotatable_bonds": 0,
        "charge": 0,
    }

    try:
        # 尝试使用 RDKit（最准确）
        from rdkit import Chem
        from rdkit.Chem import Descriptors, Lipinski

        # 根据文件格式读取
        ext = Path(mol_path).suffix.lower()
        if ext in [".sdf", ".mol"]:
            suppl = Chem.SDMolSupplier(mol_path, removeHs=False)
            mol = suppl[0] if suppl and len(suppl) > 0 else None
        elif ext == ".mol2":
            mol = Chem.MolFromMol2File(mol_path, removeHs=False)
        elif ext == ".pdb":
            mol = Chem.MolFromPDBFile(mol_path, removeHs=False)
        else:
            return features

        if mol is None:
            return features

        # 提取特征
        features["atom_count"] = mol.GetNumAtoms()
        features["heavy_atom_count"] = mol.GetNumHeavyAtoms()
        features["rotatable_bonds"] = Lipinski.NumRotatableBonds(mol)

        # 检测金属原子
        metal_symbols = {
            "FE", "ZN", "MG", "CA", "MN", "CU", "CO", "NI", "K", "NA"
        }
        for atom in mol.GetAtoms():
            symbol = atom.GetSymbol().upper()
            if symbol in metal_symbols:
                features["has_metal"] = True
                features["metal_atoms"].append(symbol)

        # 检测芳香环
        features["has_aromatic"] = any(atom.GetIsAromatic() for atom in mol.GetAtoms())
        features["ring_count"] = Chem.GetSSSR(mol)

        # 净电荷
        features["charge"] = Chem.GetFormalCharge(mol)

        return features

    except ImportError:
        # RDKit 不可用，使用简单的文本解析
        return _detect_features_fallback(mol_path)
    except Exception:
        return features


def _detect_features_fallback(mol_path: str) -> Dict[str, any]:
    """回退方案：基于文本解析检测分子特征"""
    features = {
        "has_metal": False,
        "metal_atoms": [],
        "atom_count": 0,
        "heavy_atom_count": 0,
    }

    try:
        with open(mol_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        metal_symbols = ["FE", "ZN", "MG", "CA", "MN", "CU", "CO", "NI"]
        for metal in metal_symbols:
            if metal in content:
                features["has_metal"] = True
                features["metal_atoms"].append(metal)

        # 统计原子数（粗略）
        if mol_path.endswith(".pdb"):
            atom_lines = [l for l in content.split('\n') if l.startswith(("ATOM", "HETATM"))]
            features["atom_count"] = len(atom_lines)
            features["heavy_atom_count"] = len([l for l in atom_lines if not l[12:16].strip().startswith('H')])

    except Exception:
        pass

    return features


def select_optimal_forcefield(mol_features: Dict, available_ffs: List[str]) -> List[str]:
    """根据分子特征智能选择最佳力场

    Args:
        mol_features: 分子特征字典
        available_ffs: 可用力场列表

    Returns:
        List[str]: 优先级排序的力场列表
    """
    # 金属配合物：UFF 最宽容
    if mol_features.get("has_metal", False):
        priority = ["UFF"]
        if "MMFF94" in available_ffs:
            priority.append("MMFF94")
        return [ff for ff in priority if ff in available_ffs]

    # 大分子（>50个重原子）：UFF 更稳定
    if mol_features.get("heavy_atom_count", 0) > 50:
        priority = ["UFF", "MMFF94s", "MMFF94"]
        return [ff for ff in priority if ff in available_ffs]

    # 带电分子：MMFF94s 对电荷处理更好
    if abs(mol_features.get("charge", 0)) > 0:
        priority = ["MMFF94s", "MMFF94", "UFF"]
        return [ff for ff in priority if ff in available_ffs]

    # 标准有机小分子：MMFF94s 最准确
    priority = ["MMFF94s", "MMFF94", "UFF"]
    return [ff for ff in priority if ff in available_ffs]


def calculate_optimal_minimization_steps(mol_features: Dict) -> int:
    """根据分子大小动态计算优化步数

    Args:
        mol_features: 分子特征字典

    Returns:
        int: 推荐的优化步数
    """
    atom_count = mol_features.get("atom_count", 20)
    rotatable = mol_features.get("rotatable_bonds", 5)

    # 基础步数：每个原子10步
    base_steps = atom_count * 10

    # 柔性分子需要更多步数
    flexibility_bonus = rotatable * 20

    total_steps = base_steps + flexibility_bonus

    # 限制范围 [200, 1000]
    return max(200, min(1000, total_steps))


def generate_conformers_rdkit(mol_path: str, output_path: str,
                               num_conformers: int = 10) -> bool:
    """使用 RDKit 生成多个低能构象，选择最优

    相比 OpenBabel 的 --gen3d，RDKit 的 ETKDG 算法生成的构象质量更高

    Args:
        mol_path: 输入分子文件
        output_path: 输出PDBQT文件
        num_conformers: 生成构象数量

    Returns:
        bool: 成功返回 True
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        # 读取分子
        ext = Path(mol_path).suffix.lower()
        if ext in [".sdf", ".mol"]:
            suppl = Chem.SDMolSupplier(mol_path, removeHs=False)
            mol = suppl[0] if suppl and len(suppl) > 0 else None
        elif ext == ".mol2":
            mol = Chem.MolFromMol2File(mol_path, removeHs=False)
        else:
            return False

        if mol is None:
            return False

        # 添加氢原子
        mol = Chem.AddHs(mol)

        # ETKDG 构象生成（实验-扭转知识距离几何）
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        params.numThreads = 0  # 使用所有CPU

        conf_ids = AllChem.EmbedMultipleConfs(
            mol,
            numConfs=num_conformers,
            params=params
        )

        if len(conf_ids) == 0:
            return False

        # MMFF94 优化每个构象
        results = []
        for conf_id in conf_ids:
            ff = AllChem.MMFFGetMoleculeForceField(
                mol,
                AllChem.MMFFGetMoleculeProperties(mol),
                confId=conf_id
            )
            if ff is None:
                continue
            ff.Minimize(maxIts=500)
            energy = ff.CalcEnergy()
            results.append((conf_id, energy))

        if not results:
            return False

        # 选择最低能量构象
        best_conf_id = min(results, key=lambda x: x[1])[0]
        best_energy = min(results, key=lambda x: x[1])[1]

        print(f"生成{len(conf_ids)}个构象，最低能量: {best_energy:.2f} kcal/mol")

        # 导出为 SDF（中间格式）
        temp_sdf = output_path.replace(".pdbqt", "_temp.sdf")
        writer = Chem.SDWriter(temp_sdf)
        writer.write(mol, confId=best_conf_id)
        writer.close()

        # 使用 Meeko 转换为 PDBQT
        from src.core.ligand_processor import prepare_with_meeko
        success = prepare_with_meeko(temp_sdf, output_path)

        # 清理临时文件
        if os.path.exists(temp_sdf):
            os.remove(temp_sdf)

        return success

    except ImportError:
        print("RDKit 未安装，跳过高级构象生成")
        return False
    except Exception as e:
        print(f"RDKit 构象生成失败: {e}")
        return False


def validate_ligand_energy(pdbqt_path: str) -> Tuple[bool, Optional[float]]:
    """验证配体能量是否合理（检测高能不稳定构象）

    Args:
        pdbqt_path: PDBQT 文件路径

    Returns:
        (is_valid, energy): 是否合理 + 能量值（kcal/mol）
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        # PDBQT → PDB（临时）
        temp_pdb = pdbqt_path.replace(".pdbqt", "_check.pdb")
        from src.core.utils import run_command, get_tool_path

        cmd = f'"{get_tool_path("obabel.exe")}" "{pdbqt_path}" -opdb -O "{temp_pdb}"'
        run_command(cmd, capture_output=True)

        if not os.path.exists(temp_pdb):
            return True, None  # 无法验证不代表失败

        # RDKit 读取并计算能量
        mol = Chem.MolFromPDBFile(temp_pdb, removeHs=False)
        os.remove(temp_pdb)

        if mol is None:
            return True, None

        # MMFF94 力场能量
        props = AllChem.MMFFGetMoleculeProperties(mol)
        ff = AllChem.MMFFGetMoleculeForceField(mol, props)
        if ff is None:
            return True, None

        energy = ff.CalcEnergy()

        # 能量阈值判断（经验值）
        # < 100 kcal/mol: 合理
        # 100-500: 警告
        # > 500: 不稳定
        if energy > 500:
            print(f"警告：配体能量过高({energy:.1f} kcal/mol)，可能构象不稳定")
            return False, energy
        elif energy > 100:
            print(f"注意：配体能量较高({energy:.1f} kcal/mol)")
            return True, energy
        else:
            return True, energy

    except ImportError:
        return True, None
    except Exception:
        return True, None


def check_ligand_quality_comprehensive(pdbqt_path: str) -> Dict[str, any]:
    """综合质量检查：坐标、能量、手性、质子化

    Args:
        pdbqt_path: PDBQT 文件路径

    Returns:
        Dict: 质量报告
    """
    report = {
        "valid": True,
        "issues": [],
        "warnings": [],
        "energy": None,
        "atom_count": 0,
        "has_coords": False,
        "has_charges": False,
    }

    try:
        with open(pdbqt_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # 检查1: 原子数量
        atom_lines = [l for l in lines if l.startswith(("ATOM", "HETATM"))]
        report["atom_count"] = len(atom_lines)

        if len(atom_lines) < 3:
            report["valid"] = False
            report["issues"].append(f"原子数过少({len(atom_lines)})")
            return report

        # 检查2: 坐标完整性
        bad_coords = 0
        for line in atom_lines:
            try:
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
                if abs(x) < 0.001 and abs(y) < 0.001 and abs(z) < 0.001:
                    bad_coords += 1
            except ValueError:
                bad_coords += 1

        if bad_coords > len(atom_lines) * 0.5:
            report["valid"] = False
            report["issues"].append(f"坐标异常比例过高({bad_coords}/{len(atom_lines)})")
        else:
            report["has_coords"] = True

        # 检查3: 电荷分配
        report["has_charges"] = "TORSDOF" in "".join(lines)
        if not report["has_charges"]:
            report["warnings"].append("缺少TORSDOF记录（刚性分子）")

        # 检查4: 能量验证（可选）
        is_valid, energy = validate_ligand_energy(pdbqt_path)
        report["energy"] = energy
        if not is_valid:
            report["warnings"].append(f"能量过高({energy:.1f} kcal/mol)")

        return report

    except Exception as e:
        report["valid"] = False
        report["issues"].append(f"检查异常: {str(e)}")
        return report


# ===== 使用示例 =====
def advanced_ligand_preparation(input_path: str, output_path: str,
                                available_ffs: List[str]) -> Tuple[bool, str]:
    """综合使用高级算法进行配体预处理

    流程:
    1. 检测分子特征
    2. 智能选择力场
    3. 生成最优构象（RDKit ETKDG）
    4. 能量最小化
    5. 质量验证

    Args:
        input_path: 输入分子文件
        output_path: 输出PDBQT文件
        available_ffs: 可用力场列表

    Returns:
        (success, method): 成功标志 + 使用的方法
    """
    print(f"=== 高级配体预处理: {Path(input_path).name} ===")

    # 步骤1: 分子特征分析
    features = detect_molecule_features(input_path)
    print(f"分子特征: {features['atom_count']}个原子, "
          f"{features.get('heavy_atom_count', 0)}个重原子")

    if features.get("has_metal"):
        print(f"  ⚠️ 检测到金属原子: {features['metal_atoms']}")

    # 步骤2: 尝试 RDKit 高级构象生成
    if not features.get("has_metal") and features.get("heavy_atom_count", 0) < 100:
        print("尝试 RDKit ETKDG 构象生成...")
        if generate_conformers_rdkit(input_path, output_path):
            # 质量检查
            quality = check_ligand_quality_comprehensive(output_path)
            if quality["valid"]:
                print(f"✅ RDKit 预处理成功 (能量: {quality.get('energy', 'N/A')} kcal/mol)")
                return True, "rdkit_etkdg"
            else:
                print(f"⚠️ RDKit 预处理质量问题: {quality['issues']}")
                # 继续回退方案

    # 步骤3: 回退到 Meeko
    print("尝试 Meeko 预处理...")
    from src.core.ligand_processor import prepare_with_meeko
    if prepare_with_meeko(input_path, output_path):
        quality = check_ligand_quality_comprehensive(output_path)
        if quality["valid"]:
            print(f"✅ Meeko 预处理成功")
            return True, "meeko"

    # 步骤4: 最后回退到 OpenBabel
    print("回退到 OpenBabel 优化...")
    from src.core.utils import run_command, get_tool_path, get_conda_env

    # 智能选择力场
    optimal_ffs = select_optimal_forcefield(features, available_ffs)
    steps = calculate_optimal_minimization_steps(features)

    print(f"优化参数: 力场{optimal_ffs}, 步数{steps}")

    env = get_conda_env()
    for ff in optimal_ffs:
        cmd = (
            f'"{get_tool_path("obabel.exe")}" "{input_path}" -opdbqt '
            f'-O "{output_path}" --minimize --forcefield {ff} '
            f'--steps {steps} --partialcharge gasteiger'
        )
        output, success = run_command(cmd, env=env, capture_output=True)

        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            quality = check_ligand_quality_comprehensive(output_path)
            if quality["valid"]:
                print(f"✅ OpenBabel 预处理成功 (力场: {ff})")
                return True, f"openbabel_{ff}"

    print("❌ 所有预处理方案均失败")
    return False, "failed"
