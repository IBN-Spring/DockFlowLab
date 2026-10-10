import os
import shutil
import subprocess
import time
import threading

from src.core.config import CONFIG
from src.core.logger import log
from src.core.utils import run_command, get_conda_env, get_tool_path, get_conda_python


def run_command_with_timeout(cmd, env=None, timeout=120, capture_output=False):
    """带超时的命令执行"""
    try:
        result = subprocess.run(
            cmd, shell=True, env=env,
            capture_output=True, text=True,
            encoding='utf-8', errors='ignore',
            timeout=timeout,
        )
        if capture_output:
            return result.stdout + result.stderr, result.returncode == 0
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"命令超时 ({timeout}s): {cmd[:100]}")
    except Exception as e:
        return ("", False) if capture_output else False


# 模块级全局状态
ligand_is_running = False
ligand_lock = threading.Lock()


def check_forcefields(env, log_text=None):
    """快速检测可用力场（Windows路径适配）"""
    available = []
    test_ff = ["UFF", "MMFF94s", "MMFF94", "GAFF", "Ghemical"]
    temp_sdf = os.path.join(CONFIG["5.临时文件"], "ff_test.sdf")
    with open(temp_sdf, "w", encoding='utf-8') as f:
        f.write("""\n     RDKit          3D\n\n  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
M  END
$$$$
""")
    log("开始检测 OpenBabel 可用力场...", "INFO", log_text)
    for index, ff in enumerate(test_ff, 1):
        temp_sdf_quoted = f'"{temp_sdf}"'
        temp_output_quoted = f'"{temp_sdf}.mol2"'
        cmd = (
            f'"{get_tool_path("obabel.exe")}" {temp_sdf_quoted} -omol2 '
            f"-O {temp_output_quoted} "
            f"--minimize --forcefield {ff} --steps 50 2>&1"
        )
        log(f"检测力场 {index}/{len(test_ff)}: {ff}", "INFO", log_text)
        try:
            output, success = run_command_with_timeout(cmd, env=env, timeout=20, capture_output=True)
            if success:
                available.append(ff)
                log(f"✅ 力场可用: {ff}", "INFO", log_text)
            else:
                snippet = output[:200] if output else "无输出"
                log(f"力场不可用: {ff}，返回: {snippet}", "WARNING", log_text)
        except TimeoutError:
            log(f"力场检测超时: {ff}（已跳过）", "WARNING", log_text)
    for f_path in [temp_sdf, f"{temp_sdf}.mol2"]:
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except Exception as e:
                log(f"⚠️  清理力场测试文件失败: {str(e)}", "WARNING", log_text)
    return available if available else ["UFF"]


def win_quote(path: str) -> str:
    """Windows 命令行最简安全转义：只要含空格就整体加双引号"""
    return f'"{path}"' if ' ' in path else path


def is_2d_structure(file_path):
    """快速判断分子是否为2D结构（基于Z坐标）"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if file_path.lower().endswith(('.sdf', '.mol')):
            lines = content.split('\n')
            if len(lines) > 4:
                atom_count = 0
                try:
                    atom_count = int(lines[3][0:3].strip())
                except:
                    return True  # 如果无法解析，默认按2D处理
                for i in range(4, min(4 + atom_count, len(lines))):
                    if len(lines[i]) >= 30:
                        try:
                            z_coord = float(lines[i][32:40].strip())
                            if abs(z_coord) > 0.1:  # 如果Z坐标不为0，可能是3D
                                return False
                        except:
                            continue
                return True  # 所有原子Z坐标接近0，判定为2D
        elif file_path.lower().endswith('.pdb'):
            for line in content.split('\n'):
                if line.startswith(('ATOM', 'HETATM')):
                    try:
                        z_coord = float(line[46:54].strip())
                        if abs(z_coord) > 0.1:
                            return False
                    except:
                        continue
            return True
    except Exception as e:
        print(f"结构判断失败: {str(e)}")
        return True  # 出错时默认按2D处理
    return True  # 默认按2D处理

def prepare_with_meeko(input_path, output_path):
    """使用 Meeko (RDKit) 进行配体制备：保留手性 + 质子化状态分配 + PDBQT输出"""

    try:
        conda_python = get_conda_python()
    except FileNotFoundError as e:
        log(f"Meeko 不可用: {e}", "WARNING")
        return False

    import hashlib
    import shutil

    # RDKit 在 subprocess 中可能无法处理中文路径，使用项目根目录下的 ASCII 临时目录
    from src.utils.path import APP_ROOT
    temp_dir = os.path.join(APP_ROOT, "_meeko_tmp")
    os.makedirs(temp_dir, exist_ok=True)
    uid = hashlib.md5(input_path.encode()).hexdigest()[:8]
    temp_input = os.path.join(temp_dir, f"_mk_in_{uid}{os.path.splitext(input_path)[1]}")
    temp_output = os.path.join(temp_dir, f"_mk_out_{uid}.pdbqt")
    script_path = os.path.join(temp_dir, f"_mk_{uid}.py")

    try:
        shutil.copy2(input_path, temp_input)
    except OSError as e:
        log(f"Meeko 临时文件拷贝失败: {e}", "WARNING")
        return False

    script = f'''
from meeko import MoleculePreparation, PDBQTWriterLegacy
from rdkit import Chem
import os, sys

mol = None
inpath = r"{temp_input}"
fmt = os.path.splitext(inpath)[1].lower()

if fmt in (".sdf", ".mol"):
    suppl = Chem.SDMolSupplier(inpath, removeHs=False)
    mol = suppl[0] if suppl else None
elif fmt == ".mol2":
    mol = Chem.MolFromMol2File(inpath, removeHs=False)
elif fmt == ".pdb":
    mol = Chem.MolFromPDBFile(inpath, removeHs=False)

if mol is None:
    print("MEKO_FAIL:RDKit无法读取分子")
    sys.exit(1)

# 确保有显式氢
mol = Chem.AddHs(mol)

# 分配质子化状态 (pH 7.4, 保留手性)
prep = MoleculePreparation(
    keep_chorded_rings=True,
    keep_equivalent_rings=True,
)
try:
    setups = prep.prepare(mol)
except Exception as e:
    print(f"MEKO_FAIL:MoleculePreparation: {{e}}")
    sys.exit(1)

if not setups:
    print("MEKO_FAIL:未生成分子设置")
    sys.exit(1)

# 写 PDBQT
pdbqt_str, ok, err = PDBQTWriterLegacy.write_string(setups[0])
if not ok:
    print(f"MEKO_FAIL:PDBQT写入失败: {{err}}")
    sys.exit(1)

with open(r"{temp_output}", "w", encoding="utf-8") as f:
    f.write(pdbqt_str)

print("MEKO_SUCCESS")
'''

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    import subprocess
    import platform
    try:
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [conda_python, script_path],
                capture_output=True, text=True,
                encoding='utf-8', errors='ignore',
                timeout=180,
                startupinfo=si,
            )
        else:
            result = subprocess.run(
                [conda_python, script_path],
                capture_output=True, text=True,
                timeout=180,
            )
        output_text = (result.stdout or "") + (result.stderr or "")
        log(f"Meeko 输出: {output_text[:300]}", "DEBUG")
        if "MEKO_SUCCESS" in output_text and os.path.exists(temp_output):
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                shutil.copy2(temp_output, output_path)
            except OSError:
                return False
            return True
        return False
    except subprocess.TimeoutExpired:
        log("Meeko 预处理超时", "WARNING")
        return False
    except Exception as e:
        log(f"Meeko 执行异常: {e}", "WARNING")
        return False
    finally:
        for f in (script_path, temp_input, temp_output):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass


def process_one_ligand(args, CONFIG):
    ligand_file, env, available_ffs, temp = args
    start_time = time.time()
    base_name = os.path.splitext(ligand_file)[0].replace(" ", "_")  # 处理空格
    raw_path = os.path.abspath(os.path.join(CONFIG["2.原始配体"], ligand_file))
    output_dir = os.path.abspath(CONFIG["4.配体"])
    os.makedirs(output_dir, exist_ok=True)
    output_pdbqt = os.path.join(output_dir, f"{base_name}.pdbqt")
    task_log = []
    task_log.append(("INFO", f"\n" + "="*50))
    task_log.append(("INFO", f"开始处理配体：{base_name}"))
    task_log.append(("INFO", f"原始文件路径：{raw_path}"))
    task_log.append(("INFO", f"目标输出路径：{output_pdbqt}"))
    task_log.append(("INFO", "-"*50))
    if os.path.exists(output_pdbqt) and os.path.getsize(output_pdbqt) > 100:
        task_log.append(("SUCCESS", f"配体已存在，跳过处理：{output_pdbqt}"))
        return (ligand_file, "成功", "已存在文件", task_log, 0.0, 0.0)
    file_ext = os.path.splitext(ligand_file)[1].lower()
    supported_formats = {'.sdf', '.mol', '.mol2', '.pdbqt', '.pdb'}
    tmp_3d = None  # 用于清理临时3D文件
    try:
        task_log.append(("INFO", f"步骤1/2：验证输入文件..."))
        if not os.path.exists(raw_path):
            err_msg = "原始文件不存在"
            task_log.append(("ERROR", err_msg))
            return (ligand_file, "失败", err_msg, task_log, 0.0, 0.0)
        if file_ext not in supported_formats:
            err_msg = f"文件格式不支持（支持格式：{', '.join(supported_formats)}）"
            task_log.append(("ERROR", err_msg))
            return (ligand_file, "失败", err_msg, task_log, 0.0, 0.0)
        file_size = os.path.getsize(raw_path) / 1024
        task_log.append(("SUCCESS", f"输入文件验证通过（格式：{file_ext[1:]}, 大小：{file_size:.2f}KB）"))

        # ── 主路径：Meeko（保留手性 + 质子化 + PDBQT）──
        meeko_supported = {'.sdf', '.mol', '.mol2', '.pdb'}
        if file_ext in meeko_supported:
            task_log.append(("INFO", "步骤2/2 (Meeko)：智能配体制备（手性保留 + 质子化 + PDBQT）..."))
            meeko_ok = prepare_with_meeko(raw_path, output_pdbqt)
            if meeko_ok and os.path.exists(output_pdbqt) and os.path.getsize(output_pdbqt) > 100:
                pdbqt_size = os.path.getsize(output_pdbqt) / 1024
                proc_time = time.time() - start_time
                task_log.append(("SUCCESS", f"Meeko 处理完成（大小：{pdbqt_size:.2f}KB，耗时: {proc_time:.2f}秒）"))
                task_log.append(("INFO", "="*50))
                return (ligand_file, "成功", "Meeko", task_log, proc_time, 0.0)
            else:
                task_log.append(("WARNING", "Meeko 预处理失败，回退到 OpenBabel 管道..."))

        # ── 回退路径：OpenBabel ──
        need_3d_conversion = is_2d_structure(raw_path)
        if need_3d_conversion:
            task_log.append(("INFO", "步骤1.5/2：检测到2D结构，开始2D→3D转换..."))
            tmp_3d = os.path.join(CONFIG["5.临时文件"], f"{base_name}_3d.sdf")
            raw_path_quoted = win_quote(raw_path)
            tmp_3d_quoted = win_quote(tmp_3d)
            cmd_3d = (
                f'"{get_tool_path("obabel.exe")}" {raw_path_quoted} -osdf '
                f"-O {tmp_3d_quoted} "
                f"--gen3d --fast 2>&1"
            )
            task_log.append(("INFO", f"执行快速2D→3D命令：{cmd_3d}"))
            try:
                output_3d, success_3d = run_command_with_timeout(cmd_3d, env=env,
                                                                 timeout=120,
                                                                 capture_output=True)
                if success_3d and os.path.exists(tmp_3d) and os.path.getsize(tmp_3d) > 50:
                    task_log.append(("SUCCESS", "2D→3D 结构生成完成"))
                    raw_path = tmp_3d
                    file_ext = '.sdf'
                else:
                    task_log.append(("WARNING",
                                   f"2D→3D转换失败，跳过该步骤：{output_3d[:200] if output_3d else '无输出'}"))
                    if tmp_3d and os.path.exists(tmp_3d):
                        try:
                            os.remove(tmp_3d)
                            tmp_3d = None
                        except:
                            pass
            except TimeoutError:
                task_log.append(("WARNING",
                                 "2D→3D转换超时（>2分钟），跳过该步骤，直接进行能量最小化"))
                if tmp_3d and os.path.exists(tmp_3d):
                    try:
                        os.remove(tmp_3d)
                    except:
                        pass
                tmp_3d = None
            except Exception as e:
                task_log.append(("WARNING", f"2D→3D转换异常：{str(e)}，跳过该步骤"))
                if tmp_3d and os.path.exists(tmp_3d):
                    try:
                        os.remove(tmp_3d)
                    except:
                        pass
                tmp_3d = None
        else:
            task_log.append(("INFO", "步骤1.5/2：检测到3D结构，跳过2D→3D转换"))
        task_log.append(("INFO", f"步骤2/2：能量最小化并转换为PDBQT..."))
        input_file = raw_path
        input_file_quoted = win_quote(input_file)
        output_pdbqt_quoted = win_quote(output_pdbqt)
        quick_forcefields = ["MMFF94s", "UFF", "MMFF94"]  # 快速力场优先
        available_quick_ffs = [ff for ff in quick_forcefields if ff in available_ffs]
        final_output = ""
        success = False
        used_forcefield = ""

        if available_quick_ffs:
            for forcefield in available_quick_ffs:
                task_log.append(("INFO", f"尝试力场: {forcefield}"))
                cmd_process = (
                    f'"{get_tool_path("obabel.exe")}" {input_file_quoted} -opdbqt '
                    f"-O {output_pdbqt_quoted} "
                    f"--minimize --forcefield {forcefield} "
                    f"--steps 200 --convergence 1e-4 "
                    f"--partialcharge gasteiger -xl 2>&1"
                )
                task_log.append(("INFO", f"执行快速优化命令：{cmd_process}"))
                output, command_success = run_command(cmd_process, env=env, capture_output=True)
                final_output = output
                output_valid = os.path.exists(output_pdbqt) and os.path.getsize(output_pdbqt) > 100
                if command_success and output_valid:
                    success = True
                    used_forcefield = forcefield
                    break
                snippet = output[:200] if output else "无输出"
                task_log.append(("WARNING", f"力场 {forcefield} 失败：{snippet}"))
                if os.path.exists(output_pdbqt):
                    try:
                        os.remove(output_pdbqt)
                    except Exception as e:
                        task_log.append(("WARNING", f"⚠️ 清理失败文件失败: {str(e)}"))
        else:
            task_log.append(("WARNING", "无快速力场可用，直接尝试简单转换"))

        if not success:
            task_log.append(("WARNING", "快速力场失败，尝试简单转换（不进行能量最小化）"))
            cmd_simple = (
                f'"{get_tool_path("obabel.exe")}" {input_file_quoted} -opdbqt '
                f"-O {output_pdbqt_quoted} "
                f"--partialcharge gasteiger 2>&1"
            )
            task_log.append(("INFO", f"执行简单转换命令：{cmd_simple}"))
            output, command_success = run_command(cmd_simple, env=env, capture_output=True)
            final_output = output
            output_valid = os.path.exists(output_pdbqt) and os.path.getsize(output_pdbqt) > 100
            if command_success and output_valid:
                success = True
                used_forcefield = "simple_conversion"
            else:
                snippet = output[:200] if output else "无输出"
                task_log.append(("WARNING", f"简单转换失败：{snippet}"))
                if os.path.exists(output_pdbqt):
                    try:
                        os.remove(output_pdbqt)
                    except Exception as e:
                        task_log.append(("WARNING", f"⚠️ 清理失败文件失败: {str(e)}"))

        if not success:
            task_log.append(("WARNING", "简单转换失败，尝试最基本转换"))
            cmd_basic = (
                f'"{get_tool_path("obabel.exe")}" {input_file_quoted} -opdbqt '
                f"-O {output_pdbqt_quoted} 2>&1"
            )
            task_log.append(("INFO", f"执行基本转换命令：{cmd_basic}"))
            output, command_success = run_command(cmd_basic, env=env, capture_output=True)
            final_output = output
            output_valid = os.path.exists(output_pdbqt) and os.path.getsize(output_pdbqt) > 100
            if command_success and output_valid:
                success = True
                used_forcefield = "basic_conversion"
            else:
                success = False
        if not success or not os.path.exists(output_pdbqt) or os.path.getsize(output_pdbqt) < 100:
            output_exists = os.path.exists(output_pdbqt)
            output_size = os.path.getsize(output_pdbqt) if output_exists else 0
            err_msg = f"处理失败：未生成有效PDBQT（exists={output_exists}, size={output_size}），命令输出：{final_output[:500] if final_output else '未知错误'}"
            task_log.append(("ERROR", err_msg))
            return (ligand_file, "失败", err_msg, task_log, 0.0, 0.0)
        pdbqt_size = os.path.getsize(output_pdbqt) / 1024
        task_log.append(("SUCCESS", f"处理完成（大小：{pdbqt_size:.2f}KB，力场：{used_forcefield}）"))
        proc_time = time.time() - start_time
        task_log.append(("INFO", "-"*50))
        task_log.append(("SUCCESS", f"配体处理成功：{base_name}（耗时: {proc_time:.2f}秒）"))
        task_log.append(("INFO", "="*50 + "\n"))
        return (ligand_file, "成功", f"力场: {used_forcefield}", task_log, proc_time, 0.0)
    except Exception as e:
        err_msg = f"处理过程异常：{str(e)}"
        task_log.append(("ERROR", err_msg))
        return (ligand_file, "失败", err_msg, task_log, 0.0, 0.0)
    finally:
        if tmp_3d and os.path.exists(tmp_3d):
            try:
                os.remove(tmp_3d)
                task_log.append(("INFO", f"清理临时3D文件: {os.path.basename(tmp_3d)}"))
            except Exception as e:
                task_log.append(("WARNING", f"清理临时文件失败: {str(e)}"))


def _process_ligands_as_proteins(log_text=None):
    """LKlight 模式：配体即为蛋白，与受体采用相同的蛋白制备流程（去水、加氢、PDB输出）"""
    log("=== LKlight 模式：配体蛋白制备（去水、加氢、除杂原子）===", log_text=log_text)

    supported_exts = (".pdb", ".sdf", ".mol", ".mol2")
    ligand_files = [f for f in os.listdir(CONFIG["2.原始配体"])
                    if f.lower().endswith(supported_exts) and not f.startswith(".")]

    if not ligand_files:
        log("未在 2.原始配体 中找到支持的配体文件", "ERROR", log_text)
        return 0, 0, []

    output_dir = CONFIG["4.配体"]
    os.makedirs(output_dir, exist_ok=True)
    obabel = get_tool_path("obabel.exe")
    success = 0
    failed = []

    for f in ligand_files:
        raw_path = os.path.join(CONFIG["2.原始配体"], f)
        ext = os.path.splitext(f)[1].lower()
        base = os.path.splitext(f)[0]
        out_pdb = os.path.join(output_dir, f"{base}.pdb")
        out_pdbqt = os.path.join(output_dir, f"{base}.pdbqt")

        if os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
            log(f"  配体已存在: {base}.pdb，跳过", log_text=log_text)
            success += 1
            continue

        log(f"  处理配体蛋白: {f}", log_text=log_text)
        prepared = False

        # 方案1: 去除非标准残基（水、溶剂、离子）+ 加氢 → PDB
        cmd1 = f'"{obabel}" -ipdb "{raw_path}" -opdb -O "{out_pdb}" -xr -h 2>&1'
        try:
            r = subprocess.run(cmd1, shell=True, capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
                log(f"    → {base}.pdb (去杂原子+加氢)", log_text=log_text)
                prepared = True
        except Exception as e:
            log(f"    方案1失败: {e}", "WARNING", log_text)

        # 方案2: 只去水 + 加氢
        if not prepared:
            log(f"    方案1失败，尝试方案2（仅去水+加氢）...", log_text=log_text)
            cmd2 = f'"{obabel}" -ipdb "{raw_path}" -opdb -O "{out_pdb}" -h --filter "not resname HOH" 2>&1'
            try:
                r = subprocess.run(cmd2, shell=True, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
                    log(f"    → {base}.pdb (去水+加氢)", log_text=log_text)
                    prepared = True
            except Exception as e:
                log(f"    方案2失败: {e}", "WARNING", log_text)

        # 方案3: 最简转换 + 单独加氢
        if not prepared:
            log(f"    方案2失败，尝试方案3（基础转换+加氢）...", log_text=log_text)
            tmp = os.path.join(CONFIG["5.临时文件"], f"{base}_tmp.pdb")
            cmd3 = f'"{obabel}" "{raw_path}" -opdb -O "{tmp}" -h 2>&1'
            try:
                r = subprocess.run(cmd3, shell=True, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 100:
                    shutil.move(tmp, out_pdb)
                    log(f"    → {base}.pdb (基础制备)", log_text=log_text)
                    prepared = True
            except Exception as e:
                log(f"    方案3失败: {e}", "ERROR", log_text)

        # 方案4: 纯复制
        if not prepared:
            if ext == ".pdb":
                shutil.copy2(raw_path, out_pdb)
                log(f"    → {base}.pdb (原始复制)", log_text=log_text)
                prepared = True
            elif ext in (".sdf", ".mol", ".mol2"):
                cmd4 = f'"{obabel}" "{raw_path}" -opdb -O "{out_pdb}" 2>&1'
                try:
                    r = subprocess.run(cmd4, shell=True, capture_output=True, text=True, timeout=60)
                    if r.returncode == 0 and os.path.exists(out_pdb) and os.path.getsize(out_pdb) > 100:
                        log(f"    → {base}.pdb (基础转换)", log_text=log_text)
                        prepared = True
                except Exception:
                    pass

        if prepared:
            # 生成 PDBQT 副本（兼容后续分析步骤）
            try:
                cmd_qt = f'"{obabel}" "{out_pdb}" -opdbqt -O "{out_pdbqt}" 2>&1'
                subprocess.run(cmd_qt, shell=True, capture_output=True, timeout=60)
            except Exception:
                pass
            success += 1
        else:
            log(f"    ❌ 所有方案失败: {f}", "ERROR", log_text)
            failed.append(f"{f}: 所有制备方案均失败")

    log(f"LKlight 配体蛋白制备完成：成功 {success}/{len(ligand_files)}", "SUCCESS", log_text)
    return success, len(ligand_files), failed


def process_all_ligands(log_text=None):
    """批量处理配体 - 根据引擎类型自动选择小分子或蛋白处理策略"""
    global ligand_is_running

    is_lklight = CONFIG.get("docking_engine", "") == "LKlight"
    if is_lklight:
        return _process_ligands_as_proteins(log_text)

    with ligand_lock:
        if ligand_is_running:
            log("任务已在运行中", "WARNING", log_text)
            return 0, 0, []
        ligand_is_running = True
    success_count = 0
    total_count = 0
    failed_tasks = []
    try:
        env = get_conda_env(log_text)
        if not env:
            log("无法获取环境，终止", "ERROR", log_text)
            return 0, 0, []
        available_ffs = check_forcefields(env, log_text)
        log(f"可用力场: {available_ffs}", "INFO", log_text)
        supported_extensions = {'.sdf', '.mol', '.mol2', '.pdbqt', '.pdb'}
        ligand_files = [
            f for f in os.listdir(CONFIG["2.原始配体"])
            if os.path.splitext(f)[1].lower() in supported_extensions
            and not f.startswith(".")
        ]
        if not ligand_files:
            log(f"未找到支持的配体文件", "ERROR", log_text)
            return 0, 0, []
        total_count = len(ligand_files)
        log(f"发现{total_count}个配体，开始处理", "INFO", log_text)
        temp = CONFIG["5.临时文件"]
        os.makedirs(temp, exist_ok=True)
        tasks = [(f, env, available_ffs, temp) for f in ligand_files]
        log("使用串行处理模式（Windows系统推荐，避免多进程冲突）", "INFO", log_text)
        for index, task in enumerate(tasks, 1):
            ligand_file, env, available_ffs, temp = task
            log(f"配体初始化进度 {index}/{total_count}: {ligand_file}", "INFO", log_text)
            res = process_one_ligand((ligand_file, env, available_ffs, temp), CONFIG)
            ligand_file, status, msg, task_log, _, _ = res
            for level, line in task_log:
                log(line, level, log_text)
            if status == "成功":
                success_count += 1
            else:
                failed_tasks.append(f"{ligand_file}: {msg}")
        log(f"处理完成：成功{success_count}/{total_count}个", "SUCCESS", log_text)
        return success_count, total_count, failed_tasks
    except Exception as e:
        log(f"批量处理异常：{str(e)}", "ERROR", log_text)
        return success_count, total_count, failed_tasks
    finally:
        with ligand_lock:
            ligand_is_running = False