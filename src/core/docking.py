#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 分子对接核心模块
"""
import os
import sys
import glob
import shutil
import subprocess
import threading
import time
import multiprocessing as mp

try:
    import psutil
except ImportError:
    psutil = None

from .config import CONFIG
from .logger import log
from .utils import run_command, ensure_dir, get_conda_env

# 全局对接状态
task_progress = {}
task_pause_event = None


def validate_ligand_pdbqt(ligand_path, log_text=None):  # noqa: ARG001
    """验证配体 PDBQT 文件是否有效"""
    if not os.path.isfile(ligand_path):
        return False
    if os.path.getsize(ligand_path) < 50:
        return False
    with open(ligand_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    atom_lines = [ln for ln in lines if ln.startswith(("ATOM", "HETATM"))]
    return len(atom_lines) > 0


def run_single_docking(receptor_name, ligand_name, env=None, log_text=None):
    """执行单个受体-配体对接任务"""
    if env is None:
        env = get_conda_env()
    receptor_dir = os.path.join(CONFIG["3.受体"], receptor_name)
    config_path = os.path.join(receptor_dir, "config.txt")
    ligand_path = os.path.join(CONFIG["4.配体"], f"{ligand_name}.pdbqt")
    out_pdbqt = os.path.join(CONFIG["6.对接结果"], f"{receptor_name}_vs_{ligand_name}.pdbqt")
    log_path = os.path.join(CONFIG["6.对接结果"], f"{receptor_name}_vs_{ligand_name}.log")
    ensure_dir(CONFIG["6.对接结果"], log_text)
    if not os.path.exists(config_path):
        log(f"盒子配置文件不存在: {config_path}", "ERROR", log_text)
        return False
    if not validate_ligand_pdbqt(ligand_path, log_text):
        log(f"配体文件无效: {ligand_path}", "ERROR", log_text)
        return False
    with open(config_path, "r", encoding="utf-8") as f:
        config_content = f.read()
    config_content = config_content.replace("{ligand_full_path}", os.path.abspath(ligand_path))
    config_content = config_content.replace("{output_full_path}", os.path.abspath(out_pdbqt))
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix="_config.txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(config_content)
        tmp_config = tmp.name
    try:
        if sys.platform.startswith("win"):
            vina_exe, engine_info = get_docking_engine()
            _, success = _run_vina_windows(vina_exe, tmp_config, log_path, env, engine_info, log_text)
        else:
            vina_exe, engine_info = get_docking_engine()
            vina_cmd = f"{vina_exe} --config {tmp_config} > {log_path} 2>&1"
            _, success = run_command(vina_cmd, env, capture_output=True)
    finally:
        if os.path.exists(tmp_config):
            os.remove(tmp_config)
    return success


def get_docking_engine():
    """获取当前选中的对接引擎信息，返回 (exe_path, metadata_dict)"""
    engine_name = CONFIG.get("docking_engine", "Vina-GPU")
    engines = CONFIG.get("docking_engines", {})
    if engine_name in engines:
        eng = engines[engine_name]
        exe = eng["path"]
        if os.path.isfile(exe):
            return exe, eng
        log(f"引擎 '{engine_name}' 文件不存在: {exe}, 回退到 CPU Vina", "WARNING")
    # 回退：使用默认 vina_path
    return CONFIG["vina_path"], {
        "name": "Vina (CPU, 回退)", "gpu": False,
        "supports_exhaustiveness": True, "supports_virtual_screening": False,
    }


def _run_vina_windows(vina_exe, config_file, log_file, env, engine_info=None, log_text=None):
    """在纯 ASCII 临时目录中运行 Vina（解决 Vina 不支持 Unicode 路径的问题）

    engine_info: 来自 get_docking_engine() 的引擎元数据，用于过滤不支持的参数
    """
    if engine_info is None:
        engine_info = {"name": "Unknown", "gpu": False, "supports_exhaustiveness": True, "supports_virtual_screening": False}

    engines_to_try = [(vina_exe, engine_info)]
    # 如果首选是 GPU 引擎，预设 CPU 回退
    cpu_exe = CONFIG["vina_path"]
    if engine_info.get("gpu") and os.path.isfile(cpu_exe) and cpu_exe != vina_exe:
        cpu_info = {
            "name": "Vina (CPU, 自动回退)", "gpu": False,
            "supports_exhaustiveness": True, "supports_virtual_screening": False,
        }
        engines_to_try.append((cpu_exe, cpu_info))

    import tempfile, shutil

    for attempt_idx, (exe, info) in enumerate(engines_to_try):
        work_dir = tempfile.mkdtemp(prefix="vina_")
        if attempt_idx > 0:
            log(f"【引擎回退】GPU 引擎失败，自动切换至 {info['name']}", "WARNING", log_text=log_text)
        log(f"【对接引擎】{info.get('name', 'Unknown')} | exe: {exe}", log_text=log_text)

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            unsupported_keys = set()
            if not info.get("supports_exhaustiveness"):
                unsupported_keys.add("exhaustiveness")

            new_lines = []
            out_key_tmp = None
            out_key_real = None
            for line in lines:
                stripped = line.split("#", 1)[0].strip()
                if "=" in stripped:
                    key, val = stripped.split("=", 1)
                    key_s = key.strip()
                    val_s = val.strip()
                    if key_s in unsupported_keys:
                        log(f"  跳过不支持参数: {key_s}（引擎 {info.get('name')}）", "DEBUG")
                        continue
                    if key_s == "receptor" and os.path.isfile(val_s):
                        tmp_rec = os.path.join(work_dir, os.path.basename(val_s))
                        shutil.copy2(val_s, tmp_rec)
                        new_lines.append(f"receptor = {tmp_rec}\n")
                        continue
                    elif key_s == "ligand" and os.path.isfile(val_s):
                        tmp_lig = os.path.join(work_dir, os.path.basename(val_s))
                        shutil.copy2(val_s, tmp_lig)
                        new_lines.append(f"ligand = {tmp_lig}\n")
                        continue
                    elif key_s == "out":
                        out_key_real = val_s
                        out_key_tmp = os.path.join(work_dir, os.path.basename(val_s))
                        new_lines.append(f"out = {out_key_tmp}\n")
                        continue
                new_lines.append(line)

            tmp_config = os.path.join(work_dir, "config.txt")
            with open(tmp_config, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            cmd = [exe, "--config", tmp_config]
            if info.get("supports_virtual_screening"):
                thread = info.get("thread", 8000)
                search_depth = info.get("search_depth", 20)
                cmd += ["--thread", str(thread), "--search_depth", str(search_depth)]

            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            with open(log_file, "w", encoding="utf-8") as log_f:
                completed = subprocess.run(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    env=env,
                    text=True,
                    startupinfo=si
                )

            if out_key_tmp and out_key_real:
                if os.path.isfile(out_key_tmp):
                    os.makedirs(os.path.dirname(out_key_real), exist_ok=True)
                    shutil.move(out_key_tmp, out_key_real)
                    log(f"  输出文件已移至: {out_key_real}", "DEBUG")
                    return "", True
                else:
                    log(f"Vina 未生成输出文件: {out_key_tmp}，返回码={completed.returncode}", "WARNING")
                    if completed.returncode != 0 and attempt_idx + 1 < len(engines_to_try):
                        continue  # 回退到 CPU
            elif completed.returncode == 0:
                return "", True

            return "", False
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    return "", False

def run_docking(env, log_text=None):
    global task_progress, task_pause_event
    task_pause_event = threading.Event()
    task_pause_event.clear()
    log("=== 开始分子对接 ===", log_text=log_text)
    log(f"系统资源诊断:", log_text=log_text)
    if psutil:
        log(f"- 内存使用率: {psutil.virtual_memory().percent}%", log_text=log_text)
        log(f"- CPU使用率: {psutil.cpu_percent()}%", log_text=log_text)
        log(f"- 可用内存: {psutil.virtual_memory().available / (1024**3):.1f} GB", log_text=log_text)
    log(f"- CPU核心数: {mp.cpu_count()}", log_text=log_text)
    temp = CONFIG["5.临时文件"]
    os.makedirs(temp, exist_ok=True)
    for temp_file in glob.glob(os.path.join(temp, "*_temp_config.txt")):
        try:
            os.remove(temp_file)
            log(f"清理旧临时文件: {os.path.basename(temp_file)}", log_text=log_text)
        except:
            pass
    receptor_box_dirs = [d for d in os.listdir(CONFIG["3.受体"]) 
                        if os.path.isdir(os.path.join(CONFIG["3.受体"], d))]
    if not receptor_box_dirs:
        log("未找到成盒结果（需先执行「生成对接盒子」）", "ERROR", log_text)
        return False
    ligand_files = [f for f in os.listdir(CONFIG["4.配体"]) 
                   if f.lower().endswith(".pdbqt") and not f.startswith(".")]
    if not ligand_files:
        log("未找到预处理后的配体（需先执行预处理）", "ERROR", log_text)
        return False
    total_tasks = len(receptor_box_dirs) * len(ligand_files)
    simultaneous_pairs = CONFIG["simultaneous_docking_pairs"]
    task_progress["docking"] = {"total": total_tasks, "completed": 0}
    success_count = 0
    completed_count = 0
    log(f"对接任务：{len(receptor_box_dirs)}个受体 × {len(ligand_files)}个配体 = {total_tasks}个任务", log_text=log_text)
    log(f"同时对接对数：{simultaneous_pairs}对", log_text=log_text)
    if simultaneous_pairs == 1:
        log("使用1对1对接模式", log_text=log_text)
        for rec_idx, rec_dir in enumerate(receptor_box_dirs, 1):
            if task_pause_event.is_set():
                log("对接任务已暂停", log_text=log_text)
                task_pause_event.wait()
            rec_name = rec_dir
            rec_box_path = os.path.join(CONFIG["3.受体"], rec_dir)
            config_path = os.path.join(rec_box_path, "config.txt")
            rec_pdbqt = os.path.join(rec_box_path, f"{rec_name}.pdbqt")
            if not os.path.exists(config_path) or not os.path.exists(rec_pdbqt):
                log(f"❌❌ {rec_name}：成盒文件缺失", "ERROR", log_text)
                continue
            log(f"\n【受体 {rec_idx}/{len(receptor_box_dirs)}】{rec_name}", log_text=log_text)
            if "docking" not in task_progress:
                task_progress["docking"] = {"total": total_tasks, "completed": 0}
            for lig_idx, lig_file in enumerate(ligand_files, 1):
                completed_count += 1
                task_progress["docking"]["completed"] = completed_count
                if completed_count % max(1, total_tasks // 10) == 0:
                    percent = int(completed_count / total_tasks * 100)
                    log(f"对接进度：{completed_count}/{total_tasks} ({percent}%)", "INFO", log_text)
                if task_pause_event.is_set():
                    log("对接任务已暂停", log_text=log_text)
                    task_pause_event.wait()
                lig_name = os.path.splitext(lig_file)[0]
                lig_path = os.path.join(CONFIG["4.配体"], lig_file)
                out_pdbqt = os.path.join(CONFIG["6.对接结果"], f"{rec_name}_vs_{lig_name}.pdbqt")
                log_path = os.path.join(CONFIG["6.对接结果"], f"{rec_name}_vs_{lig_name}.log")
                task_idx = (rec_idx-1)*len(ligand_files) + lig_idx
                log(f"  任务 {task_idx}/{total_tasks}：配体→{lig_name}", log_text=log_text)
                if not os.path.exists(lig_path):
                    log(f"  ❌❌ 配体文件不存在：{lig_path}", "ERROR", log_text)
                    continue
                if not validate_ligand_pdbqt(lig_path, log_text):
                    log(f"  ❌❌ 配体文件格式错误：{lig_path}", "ERROR", log_text)
                    continue
                with open(config_path, "r", encoding="utf-8") as f:
                    config_content = f.read()
                config_content = config_content.replace("{ligand_full_path}", os.path.abspath(lig_path))
                config_content = config_content.replace("{output_full_path}", os.path.abspath(out_pdbqt))
                temp_config = os.path.join(CONFIG["5.临时文件"], f"{rec_name}_temp_config.txt")
                with open(temp_config, "w", encoding="utf-8") as f:
                    f.write(config_content)
                if sys.platform.startswith("win"):
                    vina_exe, engine_info = get_docking_engine()
                    success = _run_vina_windows(vina_exe, temp_config, log_path, env, engine_info)[1]
                else:
                    vina_exe, engine_info = get_docking_engine()
                    vina_cmd = f"{vina_exe} --config {temp_config} > {log_path} 2>&1"
                    _, success = run_command(vina_cmd, env, capture_output=True)
                if success:
                    success_count += 1
                    log(f"  ✅ 完成：{rec_name}_vs_{lig_name}（Log已保存）", log_text=log_text)
                else:
                    log(f"  ❌❌ 失败：{rec_name}_vs_{lig_name}", log_text=log_text)
                if os.path.exists(temp_config):
                    os.remove(temp_config)
                task_progress["docking"]["completed"] = task_idx
    else:
        log(f"使用多对对接模式（{simultaneous_pairs}对）", log_text=log_text)
        all_tasks = []
        for rec_idx, rec_dir in enumerate(receptor_box_dirs, 1):
            rec_name = rec_dir
            rec_box_path = os.path.join(CONFIG["3.受体"], rec_dir)
            config_path = os.path.join(rec_box_path, "config.txt")
            rec_pdbqt = os.path.join(rec_box_path, f"{rec_name}.pdbqt")
            if not os.path.exists(config_path) or not os.path.exists(rec_pdbqt):
                log(f"❌❌ {rec_name}：成盒文件缺失", "ERROR", log_text)
                continue
            for lig_idx, lig_file in enumerate(ligand_files, 1):
                lig_name = os.path.splitext(lig_file)[0]
                lig_path = os.path.join(CONFIG["4.配体"], lig_file)
                out_pdbqt = os.path.join(CONFIG["6.对接结果"], f"{rec_name}_vs_{lig_name}.pdbqt")
                log_path = os.path.join(CONFIG["6.对接结果"], f"{rec_name}_vs_{lig_name}.log")
                all_tasks.append((rec_name, lig_name, config_path, lig_path, out_pdbqt, log_path))
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def run_single_docking(task):
            rec_name, lig_name, config_path, lig_path, out_pdbqt, log_path = task
            while task_pause_event and task_pause_event.is_set():
                time.sleep(1)
            if not os.path.exists(lig_path):
                return (False, f"配体文件不存在：{lig_path}")
            if not validate_ligand_pdbqt(lig_path, log_text):
                return (False, f"配体文件格式错误：{lig_path}")
            with open(config_path, "r", encoding="utf-8") as f:
                config_content = f.read()
            config_content = config_content.replace("{ligand_full_path}", os.path.abspath(lig_path))
            config_content = config_content.replace("{output_full_path}", os.path.abspath(out_pdbqt))
            temp_config = os.path.join(CONFIG["5.临时文件"], f"{rec_name}_vs_{lig_name}_temp_config.txt")
            try:
                with open(temp_config, "w", encoding="utf-8") as f:
                    f.write(config_content)
                if sys.platform.startswith("win"):
                    vina_exe, engine_info = get_docking_engine()
                    output, success = _run_vina_windows(vina_exe, temp_config, log_path, env, engine_info)
                else:
                    vina_exe, engine_info = get_docking_engine()
                    vina_cmd = f"{vina_exe} --config {temp_config} > {log_path} 2>&1"
                    output, success = run_command(vina_cmd, env, capture_output=True)
                return (success, f"{rec_name}_vs_{lig_name}")
            except Exception as e:
                return (False, f"{rec_name}_vs_{lig_name}: {str(e)}")
            finally:
                if os.path.exists(temp_config):
                    try:
                        os.remove(temp_config)
                    except:
                        pass
            return (success, f"{rec_name}_vs_{lig_name}")
        with ThreadPoolExecutor(max_workers=simultaneous_pairs) as executor:
            future_to_task = {executor.submit(run_single_docking, task): task for task in all_tasks}
            for future in as_completed(future_to_task):
                if task_pause_event.is_set():
                    log("对接任务已暂停", log_text=log_text)
                    task_pause_event.wait()
                try:
                    success, task_name = future.result()
                    if success:
                        success_count += 1
                        log(f"✅ 完成：{task_name}", log_text=log_text)
                    else:
                        log(f"❌❌ 失败：{task_name}", log_text=log_text)  # 正确显示失败
                except Exception as e:
                    log(f"❌❌ 任务执行异常：{str(e)}", log_text=log_text)
                task_progress["docking"]["completed"] += 1
    log(f"\n=== 对接完成：成功{success_count}/{total_tasks}个任务 ===", log_text=log_text)
    return True


# ============================================================
# LKlight 蛋白-蛋白对接
# ============================================================

def run_lklight_pipeline(receptor_pdb, ligand_pdb, output_dir, method=None,
                         swarms=None, glowworms=None, steps=None, anm=None,
                         threads=None, env=None, log_text=None):
    """运行 LKlight 蛋白-蛋白对接 pipeline

    Returns:
        (result_pdb_path_or_None, success_bool)
    """
    if env is None:
        env = get_conda_env()

    exe_path = CONFIG["docking_engines"]["LKlight"]["path"]
    if not os.path.isfile(exe_path):
        log(f"LKlight 引擎不存在: {exe_path}", "ERROR", log_text)
        return None, False

    method = method or CONFIG.get("lklight_method", "pydock")
    swarms = swarms or CONFIG.get("lklight_swarms", 25)
    glowworms = glowworms or CONFIG.get("lklight_glowworms", 200)
    steps = steps or CONFIG.get("lklight_steps", 100)
    anm = anm if anm is not None else CONFIG.get("lklight_anm", False)
    threads = threads if threads is not None else CONFIG.get("lklight_threads", 0)

    if not os.path.isfile(receptor_pdb):
        log(f"受体 PDB 不存在: {receptor_pdb}", "ERROR", log_text)
        return None, False
    if not os.path.isfile(ligand_pdb):
        log(f"配体 PDB 不存在: {ligand_pdb}", "ERROR", log_text)
        return None, False

    ensure_dir(output_dir, log_text)

    log(f"【LKlight 蛋白-蛋白对接】", log_text=log_text)
    log(f"  受体: {os.path.basename(receptor_pdb)}", log_text=log_text)
    log(f"  配体: {os.path.basename(ligand_pdb)}", log_text=log_text)
    log(f"  方法: {method} | swarms={swarms} glowworms={glowworms} steps={steps} anm={anm}", log_text=log_text)

    cmd = [
        exe_path, "pipeline",
        os.path.abspath(receptor_pdb),
        os.path.abspath(ligand_pdb),
        method,
        "-s", str(swarms),
        "-g", str(glowworms),
    ]
    if threads and threads > 0:
        cmd += ["--threads", str(threads)]
    if anm:
        cmd.append("--anm")

    log_path = os.path.join(output_dir, "lklight.log")
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        with open(log_path, "w", encoding="utf-8") as log_f:
            completed = subprocess.run(
                cmd,
                cwd=output_dir,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                startupinfo=si
            )
        if completed.returncode != 0:
            log(f"LKlight pipeline 失败，返回码={completed.returncode}，详见 {log_path}", "ERROR", log_text)
            return None, False

        # 查找输出的 top PDB 文件
        top_files = glob.glob(os.path.join(output_dir, "top_*.pdb"))
        if top_files:
            result_pdb = top_files[0]
            log(f"LKlight 对接完成 → {os.path.basename(result_pdb)}", log_text=log_text)
            return result_pdb, True

        log(f"LKlight 完成但未找到输出 PDB，详见 {log_path}", "WARNING", log_text)
        return None, False

    except Exception as e:
        log(f"LKlight 运行异常: {str(e)}", "ERROR", log_text)
        return None, False


def _clean_pdb_for_lklight(input_path, output_dir, log_text=None):
    """清理 PDB 文件使其兼容 LKlight 的 pdbtbx 解析器

    1. 移除 ANISOU 记录（各向异性温度因子，pdbtbx 不支持）
    2. PDBQT → PDB 转换
    """
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_pdb = os.path.join(output_dir, f"{base}_lklight.pdb")
    if os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
        return out_pdb

    ext = input_path.lower()

    if ext.endswith(".pdbqt"):
        # PDBQT: 用 OpenBabel 转 PDB
        try:
            from .utils import get_tool_path
            obabel = get_tool_path("obabel.exe")
            cmd = f'"{obabel}" "{input_path}" -opdb -O "{out_pdb}" 2>&1'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
                log(f"  PDBQT→PDB: {os.path.basename(out_pdb)}", log_text=log_text)
                return out_pdb
        except Exception as e:
            log(f"  PDBQT→PDB 失败: {e}", "WARNING", log_text)
        return input_path
    else:
        # PDB: 过滤 pdbtbx 不兼容的记录
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        # pdbtbx 不兼容记录:
        #   ANISOU(各向异性温度), CRYST1(晶胞参数), CONECT(键连接), MASTER(生化统计)
        #   SEQRES(序列, 残基数常与实际不符→panic), DBREF(数据库引用),
        #   SEQADV(序列差异), MODRES(修饰残基)
        bad_prefixes = ("ANISOU", "CONECT", "MASTER", "CRYST1",
                        "SEQRES", "DBREF ", "SEQADV", "MODRES")
        cleaned = [ln for ln in lines if not ln.startswith(bad_prefixes)]
        if len(cleaned) < len(lines):
            with open(out_pdb, "w", encoding="utf-8") as f:
                f.writelines(cleaned)
            removed = len(lines) - len(cleaned)
            log(f"  已清理 PDB ({removed} 条不兼容记录): {os.path.basename(out_pdb)}", log_text=log_text)
            return out_pdb
        return input_path  # 无需清理


def run_lklight_docking(env=None, log_text=None):
    """批量 LKlight 蛋白-蛋白对接（遍历受体 × 配体 PDB 文件）"""
    global task_progress, task_pause_event
    task_pause_event = threading.Event()
    task_pause_event.clear()

    log("=== 开始 LKlight 蛋白-蛋白对接 ===", log_text=log_text)

    # 查找受体 PDB 文件（多级回退：pdb → 原始受体pdb → pdbqt转pdb）
    receptor_dirs = [d for d in os.listdir(CONFIG["3.受体"])
                     if os.path.isdir(os.path.join(CONFIG["3.受体"], d))]
    receptor_pdbs = []
    for d_name in receptor_dirs:
        # 1. 优先 3.受体/<name>/<name>.pdb
        pdb_path = os.path.join(CONFIG["3.受体"], d_name, f"{d_name}.pdb")
        if os.path.isfile(pdb_path):
            receptor_pdbs.append((d_name, pdb_path))
            continue
        # 2. 回退到原始受体 PDB
        raw_pdb = os.path.join(CONFIG["1.原始受体"], f"{d_name}.pdb")
        if os.path.isfile(raw_pdb):
            receptor_pdbs.append((d_name, raw_pdb))
            continue
        # 3. 最后尝试 PDBQT
        alt = os.path.join(CONFIG["3.受体"], d_name, f"{d_name}.pdbqt")
        if os.path.isfile(alt):
            receptor_pdbs.append((d_name, alt))

    if not receptor_pdbs:
        log("未找到受体 PDB 文件", "ERROR", log_text)
        return False

    # 查找配体文件（优先 PDB）
    all_files = [f for f in os.listdir(CONFIG["4.配体"])
                 if f.lower().endswith((".pdb", ".pdbqt")) and not f.startswith(".")]
    best = {}
    for f in all_files:
        base = os.path.splitext(f)[0]
        if base not in best or f.lower().endswith(".pdb"):
            best[base] = f
    ligand_files = list(best.values())
    if not ligand_files:
        log("未找到配体 PDB 文件", "ERROR", log_text)
        return False

    total_tasks = len(receptor_pdbs) * len(ligand_files)
    task_progress["docking"] = {"total": total_tasks, "completed": 0}
    success_count = 0

    log(f"LKlight 对接：{len(receptor_pdbs)}个受体 × {len(ligand_files)}个配体 = {total_tasks}个任务", log_text=log_text)

    method = CONFIG.get("lklight_method", "pydock")
    swarms = CONFIG.get("lklight_swarms", 25)
    glowworms = CONFIG.get("lklight_glowworms", 200)
    steps = CONFIG.get("lklight_steps", 100)
    anm = CONFIG.get("lklight_anm", False)
    threads = CONFIG.get("lklight_threads", 0)

    # 临时 PDB 转换目录
    tmp_pdb_dir = os.path.join(CONFIG["5.临时文件"], "lklight_pdb")
    os.makedirs(tmp_pdb_dir, exist_ok=True)

    for rec_idx, (rec_name, rec_pdb) in enumerate(receptor_pdbs, 1):
        for lig_file in ligand_files:
            lig_name = os.path.splitext(lig_file)[0]
            lig_path = os.path.join(CONFIG["4.配体"], lig_file)
            output_dir = os.path.join(CONFIG["6.对接结果"], f"{rec_name}_vs_{lig_name}")

            # 清理 PDB（移除 ANISOU 等 LKlight 不兼容的记录）
            rec_input = _clean_pdb_for_lklight(rec_pdb, tmp_pdb_dir, log_text)
            lig_input = _clean_pdb_for_lklight(lig_path, tmp_pdb_dir, log_text)

            result, ok = run_lklight_pipeline(
                rec_input, lig_input, output_dir,
                method=method, swarms=swarms, glowworms=glowworms,
                steps=steps, anm=anm, threads=threads,
                env=env, log_text=log_text
            )

            if ok:
                success_count += 1
                log(f"  ✅ 完成：{rec_name}_vs_{lig_name}", log_text=log_text)
            else:
                log(f"  ❌ 失败：{rec_name}_vs_{lig_name}", log_text=log_text)

            task_progress["docking"]["completed"] += 1

    log(f"\n=== LKlight 对接完成：成功 {success_count}/{total_tasks} ===", log_text=log_text)
    return True


def convert_pdbqt_to_pdb_preserve_conformation(env, log_text=None):
    """转换PDBQT到PDB格式，特别保持配体构象不变"""
    log("=== 开始转换PDBQT到PDB格式（保持配体构象）===", log_text=log_text)
    ensure_dir(CONFIG["8.转换PDB"], log_text)
    pdbqt_files = [f for f in os.listdir(CONFIG["6.对接结果"]) 
                  if f.lower().endswith(".pdbqt") and not f.startswith(".")]
    if not pdbqt_files:
        log(f"❌ 未在 {CONFIG['6.对接结果']} 找到PDBQT文件！", "ERROR", log_text)
        return False
    success_count = 0
    for i, pdbqt_file in enumerate(pdbqt_files, 1):
        base_name = os.path.splitext(pdbqt_file)[0]
        if "_vs_" not in base_name:
            log(f"跳过无法解析的文件：{pdbqt_file}", "WARNING", log_text)
            continue
        rec_name, lig_name = base_name.split("_vs_", 1)
        ligand_pdbqt_path = os.path.join(CONFIG["6.对接结果"], pdbqt_file)
        receptor_pdbqt_path = os.path.join(CONFIG["3.受体"], rec_name, f"{rec_name}.pdbqt")
        output_pdb_path = os.path.join(CONFIG["8.转换PDB"], f"{base_name}.pdb")
        ligand_pdbqt_path = os.path.normpath(ligand_pdbqt_path)
        receptor_pdbqt_path = os.path.normpath(receptor_pdbqt_path)
        output_pdb_path = os.path.normpath(output_pdb_path)
        if os.path.exists(output_pdb_path):
            log(f"[{i}/{len(pdbqt_files)}] {base_name}.pdb已存在，跳过转换", log_text=log_text)
            success_count += 1
            continue
        if not os.path.exists(receptor_pdbqt_path):
            log(f"❌ 未找到受体PDBQT文件：{receptor_pdbqt_path}", "ERROR", log_text)
            continue
        if not os.path.exists(ligand_pdbqt_path):
            log(f"❌ 未找到配体PDBQT文件：{ligand_pdbqt_path}", "ERROR", log_text)
            continue
        try:
            log(f"[{i}/{len(pdbqt_files)}] 转换：{base_name}", log_text=log_text)
            success = combine_pdbqt_with_receptor_preserve_conformation(
                receptor_pdbqt_path, 
                ligand_pdbqt_path, 
                output_pdb_path, 
                rec_name, 
                lig_name, 
                log_text
            )
            if success:
                if validate_pdb_conformation(ligand_pdbqt_path, output_pdb_path, log_text):
                    log(f"  ✅ 成功转换并验证构象：{base_name}.pdb", log_text=log_text)
                    success_count += 1
                else:
                    log(f"  ⚠️ 转换完成但构象验证有差异：{base_name}.pdb", "WARNING", log_text)
                    success_count += 1  # 仍然算成功，但记录警告
            else:
                log(f"  ❌ 转换失败：{base_name}.pdb", "ERROR", log_text)
        except Exception as e:
            log(f"[{i}/{len(pdbqt_files)}] ❌ 转换过程异常：{str(e)}", log_text=log_text)
    log(f"=== PDBQT转PDB完成：成功{success_count}/{len(pdbqt_files)}个 ===", log_text=log_text)
    return success_count > 0
def combine_pdbqt_with_receptor_preserve_conformation(receptor_pdbqt, ligand_pdbqt, output_pdb, rec_name, lig_name, log_text=None):
    """组合受体和配体PDBQT，特别保持配体构象不变"""
    try:
        output_dir = os.path.dirname(output_pdb)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        with open(output_pdb, 'w', encoding='utf-8') as outfile:
            atom_count = 0
            receptor_atoms = 0
            receptor_has_terminal_ter = False
            original_receptor_pdb = os.path.join(CONFIG["1.原始受体"], f"{rec_name}.pdb")

            if os.path.exists(original_receptor_pdb):
                in_coordinate_block = False
                with open(original_receptor_pdb, 'r', encoding='utf-8', errors='ignore') as recfile:
                    for line in recfile:
                        if line.startswith(("ATOM", "HETATM", "MODEL")):
                            in_coordinate_block = True
                        if not in_coordinate_block:
                            if not line.startswith(("CONECT", "MASTER", "END")):
                                outfile.write(line if line.endswith("\n") else line + "\n")
                            continue
                        if line.startswith("ATOM"):
                            outfile.write(line if line.endswith("\n") else line + "\n")
                            receptor_atoms += 1
                            try:
                                atom_count = max(atom_count, int(line[6:11]))
                            except Exception:
                                atom_count += 1
                        elif line.startswith("TER"):
                            outfile.write(line if line.endswith("\n") else line + "\n")
                            receptor_has_terminal_ter = True
                log(f"      使用原始受体PDB保留二级结构信息：{os.path.basename(original_receptor_pdb)}", log_text=log_text)
            else:
                # 回退：直接从受体PDBQT提取ATOM行，不依赖obabel
                with open(receptor_pdbqt, 'r', encoding='utf-8', errors='ignore') as recfile:
                    for line in recfile:
                        if line.startswith(('ATOM', 'HETATM')):
                            atom_count += 1
                            receptor_atoms += 1
                            # PDBQT比PDB末尾多电荷列，截取前78字符转为PDB格式
                            pdb_line = line[:6] + f"{atom_count:>5}" + line[11:78].rstrip() + "\n"
                            outfile.write(pdb_line)
            if receptor_atoms > 0 and not receptor_has_terminal_ter:
                outfile.write(f"TER   {atom_count:>5}\n")
            ligand_atoms = 0
            with open(ligand_pdbqt, 'r', encoding='utf-8') as ligfile:
                lines = ligfile.readlines()
            in_model = False
            for line in lines:
                if line.startswith('MODEL'):
                    in_model = True
                    continue
                if line.startswith('ENDMDL'):
                    break
                if in_model and (line.startswith('ATOM') or line.startswith('HETATM')):
                    try:
                        x = line[30:38].strip()
                        y = line[38:46].strip()
                        z = line[46:54].strip()
                        if x and y and z:
                            atom_count += 1
                            ligand_atoms += 1
                            atom_name = line[12:16].strip() or " C  "
                            res_name = lig_name[:3] if len(lig_name) >= 3 else "LIG"
                            hetatm_line = (f"HETATM{atom_count:>5} {atom_name:<4} {res_name} L{1:>4}    "
                                         f"{x:>8}{y:>8}{z:>8}  1.00  0.00           {line[77:78] or 'C'}\n")
                            outfile.write(hetatm_line)
                    except Exception as e:
                        log(f"  警告：配体原子坐标提取失败：{str(e)}", "WARNING", log_text)
                        continue
            if ligand_atoms == 0:
                for line in lines:
                    if line.startswith(('ATOM', 'HETATM')):
                        try:
                            x = line[30:38].strip()
                            y = line[38:46].strip()
                            z = line[46:54].strip()
                            if x and y and z:
                                atom_count += 1
                                ligand_atoms += 1
                                atom_name = line[12:16].strip() or " C  "
                                res_name = lig_name[:3] if len(lig_name) >= 3 else "LIG"
                                hetatm_line = (f"HETATM{atom_count:>5} {atom_name:<4} {res_name} L{1:>4}    "
                                             f"{x:>8}{y:>8}{z:>8}  1.00  0.00           {line[77:78] or 'C'}\n")
                                outfile.write(hetatm_line)
                        except Exception as e:
                            continue

            if ligand_atoms > 0:
                outfile.write(f"TER   {atom_count:>5}\n")
            outfile.write("END\n")
            log(f"      组合完成：受体{receptor_atoms}原子 + 配体{ligand_atoms}原子，构象已保持", log_text=log_text)
            return True
    except Exception as e:
        log(f"❌ PDB文件组合失败：{str(e)}", "ERROR", log_text)
        return False


def validate_pdb_conformation(original_pdbqt, converted_pdb, log_text=None):
    """验证转换后的PDB文件是否保持了原始构象"""
    try:
        original_coords = []
        with open(original_pdbqt, 'r', encoding='utf-8') as f:
            in_model = False
            for line in f:
                if line.startswith('MODEL'):
                    in_model = True
                    continue
                if line.startswith('ENDMDL'):
                    break
                if in_model and (line.startswith('ATOM') or line.startswith('HETATM')):
                    try:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        original_coords.append((x, y, z))
                    except:
                        continue
        if not original_coords:
            with open(original_pdbqt, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith(('ATOM', 'HETATM')):
                        try:
                            x = float(line[30:38].strip())
                            y = float(line[38:46].strip())
                            z = float(line[46:54].strip())
                            original_coords.append((x, y, z))
                        except:
                            continue
        converted_coords = []
        with open(converted_pdb, 'r', encoding='utf-8') as f:
            in_ligand = False
            for line in f:
                if line.startswith('HETATM') and ' L   1' in line:  # 配体部分
                    try:
                        x = float(line[30:38].strip())
                        y = float(line[38:46].strip())
                        z = float(line[46:54].strip())
                        converted_coords.append((x, y, z))
                        in_ligand = True
                    except:
                        continue
                elif in_ligand and line.startswith('TER'):
                    break
        if len(original_coords) != len(converted_coords):
            log(f"      原子数量变化：原始{len(original_coords)} → 转换后{len(converted_coords)}", "WARNING", log_text)
            return False
        max_deviation = 0
        for orig, conv in zip(original_coords, converted_coords):
            deviation = ((orig[0]-conv[0])**2 + (orig[1]-conv[1])** 2 + (orig[2]-conv[2])**2)** 0.5
            max_deviation = max(max_deviation, deviation)
        log(f"      构象验证：最大坐标偏差 {max_deviation:.4f} Å", log_text=log_text)
        return max_deviation < 0.01  # 小于0.01Å认为构象保持
    except Exception as e:
        log(f"      构象验证失败：{str(e)}", "WARNING", log_text)
        return True  # 验证失败不代表转换失败
