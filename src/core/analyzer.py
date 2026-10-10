#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 对接结果分析模块
"""
import os
import re
import glob
import warnings
from datetime import datetime

from .config import CONFIG
from .logger import log
from .utils import ensure_dir, get_conda_env
from .docking import convert_pdbqt_to_pdb_preserve_conformation


def add_history_entry(*args, **kwargs):
    """历史记录占位（待实现）"""
    pass


def evaluate_docking_results(env, log_text=None):
    """评估对接结果并生成报告（Windows专用）"""
    log("=== 开始评估对接结果 ===", log_text=log_text)
    eval_report_dir = os.path.join(CONFIG["7.分析结果"], "evaluation_reports")
    ensure_dir(eval_report_dir, log_text)
    eval_report_path = os.path.join(eval_report_dir, "对接结果评估报告.txt")
    log_files = glob.glob(os.path.join(CONFIG["6.对接结果"], "*.log"))
    if not log_files:
        log(f"❌ 未在 {CONFIG['6.对接结果']} 找到log文件！", "ERROR", log_text)
        return False
    report_content = f"对接结果评估报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report_content += "=" * 80 + "\n\n"
    good_results = []
    problematic_results = []
    for log_file in log_files:
        log_basename = os.path.basename(log_file)
        report_content += f"【对接结果】{log_basename}\n"
        report_content += "-" * 60 + "\n"
        receptor = "未知受体"
        ligand = "未知配体"
        if "_vs_" in log_basename:
            receptor, ligand = log_basename.replace(".log", "").split("_vs_", 1)
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                binding_energies = []
                for line in content.splitlines():
                    line_strip = line.strip()
                    if line_strip.startswith(tuple(str(i) for i in range(1, 11))):
                        match = re.search(r'(-?\d+\.\d+)', line_strip)
                        if match:
                            binding_energies.append(float(match.group(1)))
                rmsd_vals = []
                rmsd_pattern = re.compile(r'RMSD\(ligand\):\s+(\d+\.\d+)\s+(\d+\.\d+)', re.MULTILINE)
                for match in rmsd_pattern.finditer(content):
                    rmsd_vals.append((float(match.group(1)), float(match.group(2))))
                if not binding_energies:
                    report_content += "⚠️ 未找到结合能数据，结果无效\n"
                    problematic_results.append(f"{receptor}_vs_{ligand}")
                    continue
                best_energy = binding_energies[0]
                energy_range = max(binding_energies) - min(binding_energies)
                report_content += f"1. 结合能分析：\n"
                report_content += f"   - 最佳结合能：{best_energy:.2f} kcal/mol\n"
                report_content += f"   - 结合能范围：{energy_range:.2f} kcal/mol\n"
                if best_energy < -8:
                    report_content += "   - 评估：结合能优秀，表明配体与受体亲和力强\n"
                elif best_energy < -5:
                    report_content += "   - 评估：结合能良好，配体与受体有较好的亲和力\n"
                elif best_energy < -3:
                    report_content += "   - 评估：结合能一般，配体与受体亲和力适中\n"
                else:
                    report_content += "   - 评估：结合能较差，配体与受体亲和力弱\n"
                    problematic_results.append(f"{receptor}_vs_{ligand}")
                if rmsd_vals:
                    report_content += f"2. RMSD分析：\n"
                    report_content += f"   - 最佳构象RMSD：{rmsd_vals[0][0]:.2f} Å (结合态), {rmsd_vals[0][1]:.2f} Å (整体)\n"
                    if rmsd_vals[0][0] < 1.0:
                        report_content += "   - 评估：构象一致性极好\n"
                    elif rmsd_vals[0][0] < 2.0:
                        report_content += "   - 评估：构象一致性良好\n"
                    else:
                        report_content += "   - 评估：构象差异较大，建议检查对接参数\n"
                pdb_file = os.path.join(CONFIG["8.转换PDB"], f"{receptor}_vs_{ligand}.pdb")
                if os.path.exists(pdb_file):
                    with open(pdb_file, "r", encoding="utf-8", errors="ignore") as pdb_f:
                        ligand_coords = []
                        for line in pdb_f:
                            if line.startswith("HETATM"):
                                try:
                                    x = float(line[30:38].strip())
                                    y = float(line[38:46].strip())
                                    z = float(line[46:54].strip())
                                    ligand_coords.append((x, y, z))
                                except (ValueError, IndexError):
                                    continue
                        if ligand_coords:
                            box_params = None
                            config_file = os.path.join(CONFIG["3.受体"], receptor, "config.txt")
                            if os.path.exists(config_file):
                                with open(config_file, "r", encoding="utf-8") as cfg_f:
                                    config_content = cfg_f.read()
                                    cx_match = re.search(r'center_x\s*=\s*(\S+)', config_content)
                                    cy_match = re.search(r'center_y\s*=\s*(\S+)', config_content)
                                    cz_match = re.search(r'center_z\s*=\s*(\S+)', config_content)
                                    sx_match = re.search(r'size_x\s*=\s*(\S+)', config_content)
                                    sy_match = re.search(r'size_y\s*=\s*(\S+)', config_content)
                                    sz_match = re.search(r'size_z\s*=\s*(\S+)', config_content)
                                    if all([cx_match, cy_match, cz_match, sx_match, sy_match, sz_match]):
                                        cx = float(cx_match.group(1))
                                        cy = float(cy_match.group(1))
                                        cz = float(cz_match.group(1))
                                        sx = float(sx_match.group(1))
                                        sy = float(sy_match.group(1))
                                        sz = float(sz_match.group(1))
                                        box_params = (cx, cy, cz, sx, sy, sz)
                            if box_params:
                                cx, cy, cz, sx, sy, sz = box_params
                                half_sx, half_sy, half_sz = sx/2, sy/2, sz/2
                                outside_count = 0
                                for x, y, z in ligand_coords:
                                    if (x < cx - half_sx or x > cx + half_sx or
                                        y < cy - half_sy or y > cy + half_sy or
                                        z < cz - half_sz or z > cz + half_sz):
                                        outside_count += 1
                                if outside_count > 0:
                                    report_content += f"3. 盒子位置评估：\n"
                                    report_content += f"   - 警告：配体有{outside_count}个原子在盒子外\n"
                                    report_content += f"   - 建议：调整盒子中心或增大盒子尺寸\n"
                                    problematic_results.append(f"{receptor}_vs_{ligand}")
                                else:
                                    report_content += f"3. 盒子位置评估：\n"
                                    report_content += f"   - 配体完全在盒子内，位置合适\n"
                else:
                    report_content += "3. 盒子位置评估：\n"
                    report_content += "   - 无法评估：未找到对应的PDB文件\n"
                report_content += "\n"
                if best_energy < -7:
                    good_results.append(f"{receptor}_vs_{ligand} (结合能: {best_energy:.2f} kcal/mol)")
        except Exception as e:
            report_content += f"❌ 分析出错：{str(e)}\n\n"
            problematic_results.append(f"{receptor}_vs_{ligand}")
            continue
    report_content += "\n" + "=" * 80 + "\n"
    report_content += "【总结评估】\n\n"
    if good_results:
        report_content += f"1. 推荐的高亲和力组合 ({len(good_results)}个)：\n"
        for i, res in enumerate(good_results[:10], 1):
            report_content += f"   {i}. {res}\n"
        if len(good_results) > 10:
            report_content += f"   ... 等{len(good_results)}个结果\n"
    if problematic_results:
        unique_problematic = list(set(problematic_results))
        report_content += f"\n2. 需要关注的问题结果 ({len(unique_problematic)}个)：\n"
        for i, res in enumerate(unique_problematic[:10], 1):
            report_content += f"   {i}. {res}\n"
        if len(unique_problematic) > 10:
            report_content += f"   ... 等{len(unique_problematic)}个结果\n"
    report_content += "\n【改进建议】\n"
    report_content += "1. 对于结合能较差的结果，可尝试：\n"
    report_content += "   - 扩大对接盒子尺寸\n"
    report_content += "   - 增加exhaustiveness参数值（如32或64）\n"
    report_content += "   - 考虑受体柔性对接\n\n"
    report_content += "2. 对于构象差异大的结果，可尝试：\n"
    report_content += "   - 增加num_modes参数值\n"
    report_content += "   - 放宽energy_range参数\n\n"
    report_content += "3. 对于配体在盒子外的情况：\n"
    report_content += "   - 调整盒子中心坐标\n"
    report_content += "   - 适当增大盒子尺寸\n"
    with open(eval_report_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(report_content)
    log(f"✅ 对接结果评估报告生成：{eval_report_path}", log_text=log_text)
    return True
def analyze_results(log_text=None):
    """分析对接结果：生成CSV汇总、热图、评估报告。同步执行，由调用方负责线程管理。"""
    import pandas as pd

    try:
        log("=== 开始结果分析 ===", log_text=log_text)
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)
        env = get_conda_env(log_text)
        if not env:
            raise Exception("Conda环境激活失败，无法执行分析")
        log("=== 【1/2】转换对接结果为PDB格式 ===", log_text=log_text)
        if not convert_pdbqt_to_pdb_preserve_conformation(env, log_text):
            log("警告：PDB转换失败，可能影响后续分析", "WARNING", log_text)
        log("=== 【2/2】生成结合能统计数据 ===", log_text=log_text)
        LOG_DIR = CONFIG["6.对接结果"]
        RESULTS_DIR = CONFIG["7.分析结果"]
        ensure_dir(RESULTS_DIR, log_text)
        CSV_PATH = os.path.join(RESULTS_DIR, "结合能汇总.csv")
        result_paths = [CSV_PATH]
        def parse_log(log_file):
            log_basename = os.path.basename(log_file)
            log(f"【解析log】{log_basename}", log_text=log_text)
            receptor = "未知受体"
            ligand = "未知配体"
            best_energy = pd.NA
            if "_vs_" in log_basename:
                receptor, ligand = log_basename.replace(".log", "").split("_vs_", 1)
                log(f"  受体：{receptor} | 配体：{ligand}", log_text=log_text)
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line_strip = line.strip()
                        if re.match(r'^1\s+', line_strip):
                            parts = line_strip.split()
                            if len(parts) >= 2:
                                try:
                                    best_energy = float(parts[1])
                                    log(f"  ✅ 最优结合能：{best_energy} kcal/mol", log_text=log_text)
                                except ValueError:
                                    log(f"  ⚠️ 无法解析结合能行：{line_strip}", "WARNING", log_text=log_text)
                            break
            except Exception as e:
                log(f"  ❌ 读取log错误：{str(e)}", log_text=log_text)
            return receptor, ligand, best_energy
        log_files = glob.glob(os.path.join(LOG_DIR, "*.log"))
        if not log_files:
            log(f"❌ 未在 {LOG_DIR} 找到log文件！", "ERROR", log_text)
            return False
        data = []
        for log_file in log_files:
            rec, lig, ene = parse_log(log_file)
            data.append({"受体": rec, "配体": lig, "最优结合能(kcal/mol)": ene})
        df = pd.DataFrame(data)
        df_valid = df[~((df["受体"] == "未知受体") & (df["配体"] == "未知配体"))]
        if df_valid.empty:
            log(f"❌ 无有效数据（log文件名需含'_vs_'）", "ERROR", log_text)
            return False
        df_wide = df_valid.pivot_table(
            index="配体",
            columns="受体",
            values="最优结合能(kcal/mol)",
            aggfunc="first",
            fill_value="NA"
        ).reset_index().rename(columns={"配体": "Gene"})
        df_wide.to_csv(CSV_PATH, index=False, encoding="utf-8", na_rep="NA")
        log(f"\n🎉 CSV生成成功：{CSV_PATH}", log_text=log_text)
        log(f"【CSV预览（前5行）】\n{df_wide.head().to_string(index=False)}", log_text=log_text)
        df_heat = df_wide.set_index("Gene").replace("NA", pd.NA)
        df_heat = df_heat.apply(pd.to_numeric, errors="coerce").dropna(how="all", axis=0).dropna(how="all", axis=1)
        # evaluation_reports 已禁用
        log(f"\n=== 结果分析完成 ===", log_text=log_text)
        add_history_entry("analysis", "success", result_paths)
        return True
    except Exception as e:
        error_msg = f"分析模块出错: {str(e)}"
        log(error_msg, "ERROR", log_text)
        add_history_entry("analysis", "error", [error_msg])
        return False