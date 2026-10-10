import os
import numpy as np
from datetime import datetime
from pathlib import Path
from src.core.config import CONFIG
from src.core.logger import log


def extract_all_atom_coords(pdbqt_path, log_text=None):
    """提取PDBQT文件中所有原子的坐标（Windows 兼容版）"""
    coords = []
    try:
        pdbqt_file = Path(pdbqt_path)
        for enc in ('utf-8', 'latin1'):
            try:
                with pdbqt_file.open('r', encoding=enc) as f:
                    for line in f:
                        if line.startswith(("ATOM", "HETATM")):
                            try:
                                x = float(line[30:38].strip())
                                y = float(line[38:46].strip())
                                z = float(line[46:54].strip())
                                coords.append([x, y, z])
                            except ValueError:
                                continue
                break
            except UnicodeDecodeError:
                continue
        return np.array(coords) if coords else None
    except Exception as e:
        log(f"提取原子坐标失败：{str(e)}", "ERROR", log_text=log_text)
        return None


def calculate_molecule_bounding_box(atom_coords, padding=8.0):
    """与原代码完全一致"""
    if atom_coords is None or len(atom_coords) == 0:
        return None, None
    min_coords = np.min(atom_coords, axis=0)
    max_coords = np.max(atom_coords, axis=0)
    center = (min_coords + max_coords) / 2.0
    size = (max_coords - min_coords) + padding
    return center, size


def generate_default_box(log_text=None):
    """与原代码完全一致"""
    default_center = [0.0, 0.0, 0.0]
    default_size = [30.0, 30.0, 30.0]
    log("⚠️ 使用默认对接盒子：中心[0,0,0]，尺寸30Å", "WARNING", log_text=log_text)
    log("💡 建议：对于小分子/核酸对接，可手动设置盒子参数", "INFO", log_text=log_text)
    return default_center, default_size


def process_small_molecule_box(receptor_pdbqt_path, log_text=None):
    """与原代码完全一致，仅内部用 Path 读取"""
    log("🔬 检测到小分子/核酸受体，使用分子边界成盒策略", log_text=log_text)
    try:
        atom_coords = extract_all_atom_coords(receptor_pdbqt_path, log_text=log_text)
        if atom_coords is None:
            log("❌ 无法提取原子坐标，使用默认盒子", "ERROR", log_text=log_text)
            return generate_default_box(log_text)
        center, size = calculate_molecule_bounding_box(atom_coords, padding=CONFIG["box_padding"])
        if center is None:
            return generate_default_box(log_text)
        if CONFIG.get("limit_volume", False):
            max_volume = 27000
            current_volume = np.prod(size)
            if current_volume > max_volume:
                scale_factor = (max_volume / current_volume) ** (1 / 3)
                size = size * scale_factor
                log(f"📦 体积限制：盒子从 {current_volume:.1f}Å³ 缩小到 {np.prod(size):.1f}Å³", log_text=log_text)
        min_size = 10.0
        size = np.maximum(size, min_size)
        log(f"✅ 小分子盒子：中心{np.round(center, 2)}Å，尺寸{np.round(size, 2)}Å", log_text=log_text)
        log(f"📊 分子信息：{len(atom_coords)}个原子，覆盖范围 {np.round(np.ptp(atom_coords, axis=0), 2)}Å", log_text=log_text)
        return center, size
    except Exception as e:
        log(f"❌ 小分子成盒失败：{str(e)}，使用默认盒子", "ERROR", log_text=log_text)
        return generate_default_box(log_text)


def estimate_protein_radius(pdb_path):
    """从PDB文件估算蛋白质半径（基于原子坐标的最大距离）"""
    try:
        from openbabel import openbabel as ob
        mol = ob.OBMol()
        conv = ob.OBConversion()
        conv.SetInFormat("pdb")
        if not conv.ReadFile(mol, pdb_path):
            raise Exception("无法读取PDB文件")
        coords = []
        for atom in ob.OBMolAtomIter(mol):
            coords.append((atom.GetX(), atom.GetY(), atom.GetZ()))
        if not coords:
            raise Exception("PDB文件中未找到原子坐标")
        centroid = np.mean(coords, axis=0)
        max_dist = max(np.linalg.norm(coord - centroid) for coord in coords)
        return max_dist
    except Exception as e:
        log(f"蛋白质半径估算失败: {str(e)}", "WARNING")
        return 15.0  # 默认半径


def calculate_protein_center(pdb_path):
    """计算蛋白质中心坐标（几何中心）"""
    try:
        from openbabel import openbabel as ob
        mol = ob.OBMol()
        conv = ob.OBConversion()
        conv.SetInFormat("pdb")
        if not conv.ReadFile(mol, pdb_path):
            raise Exception("无法读取PDB文件")
        x, y, z = 0.0, 0.0, 0.0
        count = 0
        for atom in ob.OBMolAtomIter(mol):
            x += atom.GetX()
            y += atom.GetY()
            z += atom.GetZ()
            count += 1
        if count == 0:
            raise Exception("PDB文件中未找到原子")
        return (x / count, y / count, z / count)
    except Exception as e:
        log(f"蛋白质中心计算失败: {str(e)}", "WARNING")
        return (0.0, 0.0, 0.0)  # 默认中心


def calculate_docking_box(protein_path, log_text=None):
    log(f"使用配置: 大小范围={CONFIG['box_size_range']}, 覆盖率={CONFIG['box_coverage']}", log_text=log_text)
    log("开始计算对接盒子参数...", log_text=log_text)
    try:
        center = calculate_protein_center(protein_path)
        radius = estimate_protein_radius(protein_path)
        if radius <= 0:
            radius = 15.0  # 默认半径15Å
            log(f"蛋白质半径计算异常，使用默认值: {radius}Å", "WARNING", log_text=log_text)
        log(f"蛋白质分析: 中心={[round(c, 2) for c in center]}, 半径={round(radius, 2)}Å", log_text=log_text)
    except Exception as e:
        log(f"蛋白质参数计算失败: {str(e)}，使用默认值", "WARNING", log_text=log_text)
        center = [0.0, 0.0, 0.0]  # 默认中心
        radius = 15.0  # 默认半径
    min_size, max_size = CONFIG["box_size_range"]
    coverage = CONFIG["box_coverage"]
    if coverage > 0:
        log(f"使用覆盖率模式: 覆盖率={coverage}", log_text=log_text)
        if coverage >= 0.99:
            box_size = 200.0  # 200Å足够覆盖绝大多数蛋白质
            log(f"覆盖率≥99%，使用超大盒子: {box_size}Å", log_text=log_text)
        else:
            base_size = 2 * radius * coverage + CONFIG["box_padding"]
            box_size = max(10.0, min(base_size, 100.0))
            box_size = round(box_size, 1)
            log(f"基于覆盖率计算的盒子大小: {box_size}Å", log_text=log_text)
    elif min_size > 0 and max_size > 0:
        log(f"使用盒子大小模式: 大小范围=[{min_size}, {max_size}]Å", log_text=log_text)
        box_size = max(min_size, min(2 * radius + CONFIG["box_padding"], max_size))
        box_size = round(box_size, 1)
        log(f"最终盒子大小: {box_size}Å", log_text=log_text)
    else:
        log("使用默认盒子大小: 30.0Å", log_text=log_text)
        box_size = 30.0
    if box_size <= 0:
        box_size = 30.0
        log(f"检测到无效盒子尺寸，强制使用默认值: {box_size}Å", "WARNING", log_text=log_text)
    return {
        "center_x": round(center[0], 2),
        "center_y": round(center[1], 2),
        "center_z": round(center[2], 2),
        "size_x": box_size,
        "size_y": box_size,
        "size_z": box_size
    }


def extract_cocrystallized_ligand_coords(receptor_pdbqt_path, log_text=None):
    """提取共晶配体坐标（优先策略）"""
    ligand_coords = []
    try:
        with open(receptor_pdbqt_path, "r", encoding="ISO-8859-1", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                if line.startswith("HETATM") and line[17:20].strip().upper() != "HOH":
                    try:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        ligand_coords.append([x, y, z])
                    except ValueError:
                        log(f"⚠️ 第{line_num}行HETATM坐标解析失败，跳过", log_text=log_text)
        if ligand_coords:
            log(f"✅ 提取到共晶配体（{len(ligand_coords)}个原子）", log_text=log_text)
            return np.array(ligand_coords)
        else:
            log("❌ 未检测到共晶配体，基于活性残基成盒", log_text=log_text)
            return None
    except Exception as e:
        log(f"提取配体出错：{str(e)[:60]}...", "ERROR", log_text=log_text)
        return None


def extract_active_residue_coords(receptor_pdbqt_path, log_text=None):
    """提取所有活性残基（基于关键残基类型）"""
    ACTIVE_RESIDUES = {"HIS", "ASP", "GLU", "SER", "THR", "LYS", "ARG"}
    res_data = []
    try:
        with open(receptor_pdbqt_path, "r", encoding="ISO-8859-1", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                if line.startswith("ATOM"):
                    res_name = line[17:20].strip().upper()
                    atom_name = line[13:15].strip()
                    if res_name in ACTIVE_RESIDUES and atom_name == "CA":  # 以CA原子为残基中心
                        try:
                            x = float(line[30:38].strip())
                            y = float(line[38:46].strip())
                            z = float(line[46:54].strip())
                            res_id = f"{res_name}{line[22:26].strip()}"
                            res_data.append({
                                "id": res_id,
                                "coords": [x, y, z],
                                "x": x, "y": y, "z": z
                            })
                        except ValueError:
                            log(f"⚠️ 第{line_num}行{res_name}残基坐标解析失败", log_text=log_text)
        if res_data:
            log(f"✅ 提取到{len(res_data)}个活性残基（用于成盒）", log_text=log_text)
            return res_data
        else:
            log("❌ 未检测到活性残基，需用FTMap预测口袋", "ERROR", log_text=log_text)
            return None
    except Exception as e:
        log(f"提取残基出错：{str(e)[:60]}...", "ERROR", log_text=log_text)
        return None


def calculate_initial_box(coord_array, ligand_flag=False, log_text=None):
    """计算初始盒子参数（基于配体/残基坐标，初始尺寸25Å）"""
    min_coords = np.min(coord_array, axis=0)
    max_coords = np.max(coord_array, axis=0)
    center = np.round((min_coords + max_coords) / 2, 3)
    if ligand_flag:
        base_size = np.round((max_coords - min_coords) + CONFIG["box_padding"], 1)  # 配体成盒
    else:
        base_size = np.round((max_coords - min_coords) + CONFIG["box_padding"], 1)  # 残基成盒
    init_size = np.array([25.0, 25.0, 25.0])
    init_size = np.maximum(init_size, base_size)
    min_size, max_size = CONFIG["box_size_range"]
    init_size = np.clip(init_size, min_size, max_size)
    log(f"初始盒子：中心{center}Å，尺寸{init_size}Å（体积：{np.prod(init_size):.1f}Å³）", log_text=log_text)
    return tuple(center), tuple(init_size)


def calculate_coverage(center, size, active_residues):
    """计算盒子对活性残基的覆盖率（覆盖残基数/总残基数）"""
    half_size = np.array(size) / 2.0
    x_min, x_max = center[0] - half_size[0], center[0] + half_size[0]
    y_min, y_max = center[1] - half_size[1], center[1] + half_size[1]
    z_min, z_max = center[2] - half_size[2], center[2] + half_size[2]
    in_box_count = sum(1 for res in active_residues
                       if x_min <= res["x"] <= x_max and
                       y_min <= res["y"] <= y_max and
                       z_min <= res["z"] <= z_max)
    coverage = (in_box_count / len(active_residues)) * 100 if active_residues else 0
    return coverage, in_box_count


def auto_adjust_box(center, size, active_residues, log_text=None):
    if not active_residues:
        log("⚠️ 无活性残基，跳过盒子调整", log_text=log_text)
        return center, size
    MIN_BOX_DIMENSION = 10.0
    max_attempts = 20
    min_size_cfg, max_size_cfg = CONFIG["box_size_range"]
    coverage_target = CONFIG["box_coverage"] * 100  # 转换为百分比
    limit_volume = CONFIG.get("limit_volume", False)
    max_volume = 27000
    current_center = np.array(center, dtype=np.float64)
    current_size = np.array(size, dtype=np.float64)
    current_coverage, _ = calculate_coverage(current_center, current_size, active_residues)
    log(f"初始覆盖率: {current_coverage:.1f}%，目标覆盖率: {coverage_target:.1f}%", log_text=log_text)
    if limit_volume:
        log(f"体积限制模式: 最大体积={max_volume}Å³", log_text=log_text)
        attempts = 0
        while np.prod(current_size) > max_volume and attempts < max_attempts:
            scale_factor = (max_volume / np.prod(current_size)) ** (1 / 3)
            current_size = current_size * scale_factor
            current_size = np.maximum(current_size, MIN_BOX_DIMENSION)
            current_coverage, _ = calculate_coverage(current_center, current_size, active_residues)
            log(f"体积限制: 缩小至{current_size}Å (体积: {np.prod(current_size):.1f}Å³, 覆盖率: {current_coverage:.1f}%)",
                log_text=log_text)
            attempts += 1
        return tuple(current_center), tuple(current_size)
    if coverage_target > 0:
        log(f"覆盖率模式: 强制达到{coverage_target:.1f}%覆盖率", log_text=log_text)
        if current_coverage > coverage_target:
            log(f"初始覆盖率高于目标，开始缩小盒子...", log_text=log_text)
            attempts = 0
            while current_coverage > coverage_target and attempts < max_attempts:
                scale_factor = 0.9  # 每次缩小10%
                new_size = current_size * scale_factor
                new_size = np.maximum(new_size, MIN_BOX_DIMENSION)
                new_coverage, _ = calculate_coverage(current_center, new_size, active_residues)
                if new_coverage >= coverage_target:
                    current_size = new_size
                    current_coverage = new_coverage
                    log(f"缩小盒子: {current_size}Å (覆盖率: {current_coverage:.1f}%)", log_text=log_text)
                else:
                    scale_factor = 0.95  # 缩小5%
                    new_size = current_size * scale_factor
                    new_size = np.maximum(new_size, MIN_BOX_DIMENSION)
                    new_coverage, _ = calculate_coverage(current_center, new_size, active_residues)
                    if new_coverage >= coverage_target:
                        current_size = new_size
                        current_coverage = new_coverage
                        log(f"微调缩小: {current_size}Å (覆盖率: {current_coverage:.1f}%)", log_text=log_text)
                    else:
                        log(f"已达到最小可行尺寸，无法进一步缩小", log_text=log_text)
                        break
                attempts += 1
        elif current_coverage < coverage_target:
            log(f"初始覆盖率低于目标，开始扩展盒子...", log_text=log_text)
            attempts = 0
            while current_coverage < coverage_target and attempts < max_attempts:
                scale_factor = 1.1  # 每次扩展10%
                new_size = current_size * scale_factor
                # 强制上限：不得超过 box_size_range 的最大值
                if max_size_cfg > 0:
                    new_size = np.minimum(new_size, max_size_cfg)
                new_coverage, _ = calculate_coverage(current_center, new_size, active_residues)
                current_size = new_size
                current_coverage = new_coverage
                log(f"扩展盒子: {current_size}Å (覆盖率: {current_coverage:.1f}%)", log_text=log_text)
                if current_coverage >= coverage_target:
                    break
                if max_size_cfg > 0 and np.all(current_size >= max_size_cfg):
                    log(f"已达到盒子大小上限 {max_size_cfg}Å，停止扩展", "WARNING", log_text=log_text)
                    break
                attempts += 1
        final_coverage, _ = calculate_coverage(current_center, current_size, active_residues)
        if abs(final_coverage - coverage_target) > 5.0:  # 如果偏差超过5%
            log(f"警告: 最终覆盖率({final_coverage:.1f}%)与目标({coverage_target:.1f}%)偏差较大", "WARNING",
                log_text=log_text)
        final_volume = np.prod(current_size)
        log(f"✅ 最终盒子尺寸: {current_size}Å", log_text=log_text)
        log(f"✅ 最终盒子体积: {final_volume:.1f}Å³", log_text=log_text)
        log(f"✅ 最终覆盖率: {final_coverage:.1f}%", log_text=log_text)
        return tuple(current_center), tuple(current_size)
    log(f"盒子大小模式: 限制范围=[{min_size_cfg}, {max_size_cfg}]Å", log_text=log_text)
    if np.any(current_size < min_size_cfg):
        current_size = np.maximum(current_size, min_size_cfg)
        log(f"调整到最小尺寸: {current_size}Å", log_text=log_text)
    if np.any(current_size > max_size_cfg):
        current_size = np.minimum(current_size, max_size_cfg)
        log(f"调整到最大尺寸: {current_size}Å", log_text=log_text)
    final_coverage, _ = calculate_coverage(current_center, current_size, active_residues)
    final_volume = np.prod(current_size)
    log(f"✅ 最终盒子尺寸: {current_size}Å", log_text=log_text)
    log(f"✅ 最终盒子体积: {final_volume:.1f}Å³", log_text=log_text)
    log(f"✅ 最终覆盖率: {final_coverage:.1f}%", log_text=log_text)
    return tuple(current_center), tuple(current_size)


def verify_and_adjust_box(receptor_pdbqt_path, center, size, log_text=None):
    active_residues = None
    final_coverage = 0.0
    active_residues = extract_active_residue_coords(receptor_pdbqt_path, log_text=log_text)
    if not active_residues:
        safe_size = np.maximum(size, 10.0)  # 强制不小于10Å
        log("未检测到活性残基，使用安全默认尺寸", "WARNING", log_text=log_text)
        return True, center, tuple(safe_size), 0
    adjusted_center, adjusted_size = auto_adjust_box(center, size, active_residues, log_text=log_text)
    MIN_BOX_DIMENSION = 10.0
    adjusted_size = np.maximum(adjusted_size, MIN_BOX_DIMENSION)  # 强制不小于安全值
    min_size_cfg, max_size_cfg = CONFIG["box_size_range"]
    max_size = max(MIN_BOX_DIMENSION, max_size_cfg) if max_size_cfg > 0 else None
    if max_size is not None:
        adjusted_size = np.minimum(adjusted_size, max_size)
    final_coverage, final_in_box = calculate_coverage(adjusted_center, adjusted_size, active_residues)
    volume = np.prod(adjusted_size)
    log(f"✅ 最终盒子体积: {volume:.1f}Å³", log_text=log_text)
    log(f"✅ 最终覆盖率: {final_coverage:.1f}%", log_text=log_text)
    pymol_cmds = [
        f"load {receptor_pdbqt_path};  # 加载受体",
        f"show cartoon, {os.path.basename(receptor_pdbqt_path).split('.')[0]};  # 显示受体结构",
        f"pseudoatom box_center, pos=[{adjusted_center[0]},{adjusted_center[1]},{adjusted_center[2]}];  # 盒子中心",
        f"show spheres, box_center; color green, box_center;  # 标记中心",
        f"distance box_x, box_center, box_center, {adjusted_size[0] / 2};  # X轴尺寸",
        f"distance box_y, box_center, box_center, {adjusted_size[1] / 2};  # Y轴尺寸",
        f"distance box_z, box_center, box_center, {adjusted_size[2] / 2};  # Z轴尺寸",
        "color red, box_x, box_y, box_z;  # 标记尺寸",
        f"# 活性残基标记：共{len(active_residues)}个",
        *[
            f"show sticks, resi {res['id'][3:]} and resn {res['id'][:3]}; color blue, resi {res['id'][3:]} and resn {res['id'][:3]}"
            for res in active_residues[:5]]
    ]
    log("\n📌 PyMOL验证命令（查看盒子与残基覆盖）：", log_text=log_text)
    for cmd in pymol_cmds:
        log(f"   {cmd}", log_text=log_text)
    return True, adjusted_center, tuple(adjusted_size), final_coverage


def is_residue_in_box(residue, center, size):
    half_size = np.array(size) / 2.0
    x_min, x_max = center[0] - half_size[0], center[0] + half_size[0]
    y_min, y_max = center[1] - half_size[1], center[1] + half_size[1]
    z_min, z_max = center[2] - half_size[2], center[2] + half_size[2]
    return (x_min <= residue["x"] <= x_max and
            y_min <= residue["y"] <= y_max and
            z_min <= residue["z"] <= z_max)


def generate_vina_config(receptor_pdbqt_path, center, size, log_text=None):
    """生成Vina配置文件"""
    receptor_name = os.path.splitext(os.path.basename(receptor_pdbqt_path))[0]
    config_path = os.path.join(os.path.dirname(receptor_pdbqt_path), "config.txt")
    config_content = f"""# 自动生成配置（{datetime.now().strftime('%Y-%m-%d %H:%M')}）
receptor = {os.path.abspath(receptor_pdbqt_path)}
ligand = {{ligand_full_path}}  # 配体路径占位符
out = {{output_full_path}}    # 输出路径占位符
center_x = {center[0]}
center_y = {center[1]}
center_z = {center[2]}
size_x = {size[0]}
size_y = {size[1]}
size_z = {size[2]}
exhaustiveness = {CONFIG.get("exhaustiveness", 32)}
num_modes = {CONFIG['num_modes']}
energy_range = {CONFIG['energy_range']}
"""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_content)
        log(f"✅ 生成config.txt：{config_path}（盒子尺寸{size}Å，体积{np.prod(size):.1f}Å³）", log_text=log_text)
        return config_path
    except Exception as e:
        log(f"❌ 生成config.txt失败：{str(e)}", "ERROR", log_text=log_text)
        return None


def process_single_receptor_box(receptor_name, log_text=None):
    """单受体成盒主函数（增强版：支持小分子/核酸）"""
    active_residues = None
    ligand_coords = None
    log(f"\n" + "=" * 60, log_text=log_text)
    log(f"===== 处理受体[{receptor_name}]成盒 =====", log_text=log_text)
    receptor_dir = Path(CONFIG["3.受体"]) / receptor_name
    receptor_pdbqt_path = receptor_dir / f"{receptor_name}.pdbqt"
    if not receptor_pdbqt_path.exists():
        log(f"❌ 未找到受体PDBQT：{receptor_pdbqt_path}", "ERROR", log_text=log_text)
        return False, 0
    log(f"🔍 分析受体类型：{receptor_name}", log_text=log_text)
    ligand_coords = extract_cocrystallized_ligand_coords(receptor_pdbqt_path, log_text=log_text)
    if ligand_coords is not None:
        log("🎯 使用策略：基于共晶配体成盒", log_text=log_text)
        init_center, init_size = calculate_initial_box(ligand_coords, ligand_flag=True, log_text=log_text)
    else:
        active_residues = extract_active_residue_coords(receptor_pdbqt_path, log_text=log_text)
        if active_residues is not None and len(active_residues) > 0:
            log("🎯 使用策略：基于活性残基成盒", log_text=log_text)
            coord_array = np.array([res["coords"] for res in active_residues])
            init_center, init_size = calculate_initial_box(coord_array, ligand_flag=False, log_text=log_text)
        else:
            log("🎯 使用策略：小分子/核酸边界成盒", log_text=log_text)
            init_center, init_size = process_small_molecule_box(receptor_pdbqt_path, log_text=log_text)
    if active_residues is not None and len(active_residues) > 0:
        valid, final_center, final_size, final_coverage = verify_and_adjust_box(
            receptor_pdbqt_path, init_center, init_size, log_text=log_text
        )
    else:
        final_center, final_size = init_center, init_size
        final_coverage = 0
        log("📝 小分子受体跳过覆盖率调整", log_text=log_text)
    volume = np.prod(final_size)
    log(f"\n📊 受体[{receptor_name}]最终盒子参数：", log_text=log_text)
    log(f"   中心坐标：({final_center[0]:.2f}, {final_center[1]:.2f}, {final_center[2]:.2f}) Å", log_text=log_text)
    log(f"   盒子尺寸：({final_size[0]:.2f}, {final_size[1]:.2f}, {final_size[2]:.2f}) Å", log_text=log_text)
    log(f"   体积：{volume:.1f} Å³", log_text=log_text)
    log(f"   覆盖率：{final_coverage:.1f}%", log_text=log_text)
    if not generate_vina_config(receptor_pdbqt_path, final_center, final_size, log_text=log_text):
        log(f"❌ 生成config.txt失败", "ERROR", log_text=log_text)
        return False, final_coverage
    log(f"\n===== 受体[{receptor_name}]成盒完成 =====", log_text=log_text)
    return True, final_coverage


def run_box_generation(env=None, log_text=None):
    """批量成盒主函数"""
    log(f"\n" + "=" * 80, log_text=log_text)
    log(f"===== 批量生成对接盒子 =====", log_text=log_text)
    receptor_dirs = [
        d for d in os.listdir(CONFIG["3.受体"])
        if os.path.isdir(os.path.join(CONFIG["3.受体"], d))
           and os.path.exists(os.path.join(CONFIG["3.受体"], d, f"{d}.pdbqt"))
    ]
    if not receptor_dirs:
        log("❌ 未找到有效受体目录", "ERROR", log_text=log_text)
        return False, []
    success_count = 0
    total_coverage = 0
    coverage_list = []
    for idx, rec_name in enumerate(receptor_dirs, 1):
        log(f"\n【进度 {idx}/{len(receptor_dirs)}】", log_text=log_text)
        success, coverage = process_single_receptor_box(rec_name, log_text=log_text)
        if success:
            success_count += 1
            total_coverage += coverage
            coverage_list.append(coverage)
    avg_coverage = total_coverage / success_count if success_count > 0 else 0
    log(f"\n" + "=" * 80, log_text=log_text)
    log(f"批量成盒总结：成功{success_count}/{len(receptor_dirs)}个受体", log_text=log_text)
    log(f"平均覆盖率：{avg_coverage:.1f}%", log_text=log_text)
    log(f"覆盖率范围：{min(coverage_list):.1f}%-{max(coverage_list):.1f}%" if coverage_list else "无成功案例",
        log_text=log_text)
    log("=" * 80 + "\n", log_text=log_text)
    return success_count > 0, coverage_list


def generate_box_for_receptor(receptor_name, log_text=None):
    success, coverage = process_single_receptor_box(receptor_name, log_text=log_text)
    return {"success": success, "coverage": coverage}


def clean_pdbqt_models(ligand_path):
    """
    最小化修复：确保清理不会破坏文件
    """
    try:
        ligand_path = os.path.normpath(ligand_path)

        # 读取原始内容
        with open(ligand_path, 'r', encoding='utf-8', errors='ignore') as f:
            original_lines = f.readlines()

        # 检查是否有原子行
        has_atoms = any(line.startswith(("ATOM", "HETATM")) for line in original_lines)
        if not has_atoms:
            return  # 没有原子行，跳过清理

        # 检查是否有MODEL标记
        has_models = any(line.startswith("MODEL") for line in original_lines)
        if not has_models:
            return  # 没有多模型，跳过清理

        # 安全清理：删除MODEL/ENDMDL，但确保保留原子行
        cleaned_lines = []
        for line in original_lines:
            if not line.startswith(("MODEL", "ENDMDL")):
                cleaned_lines.append(line)

        # 关键检查：清理后是否还有原子行
        cleaned_has_atoms = any(line.startswith(("ATOM", "HETATM")) for line in cleaned_lines)
        if not cleaned_has_atoms:
            # 如果清理后没有原子行，放弃清理，保留原始文件
            return

        # 只有内容确实改变时才写入
        if cleaned_lines != original_lines:
            with open(ligand_path, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(cleaned_lines)

    except Exception as e:
        # 发生任何异常都不破坏文件
        log(f"清理文件失败: {str(e)}", "WARNING")