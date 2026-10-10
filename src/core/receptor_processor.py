import os
import platform
import multiprocessing as mp
import json
import subprocess
import sys

from src.core.config import CONFIG
from src.core.logger import log
from src.core.utils import run_command, get_tool_path, get_runtime_python
from src.core.ligand_processor import prepare_with_meeko

# Windows 下使用 spawn 启动方式，避免多进程 fork 问题
if platform.system() == "Windows":
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass


def get_optimal_workers(n_tasks):
    """计算最优进程数"""
    cpu = mp.cpu_count()
    return max(1, min(n_tasks, cpu - 1))


def _strip_pdbqt_flex_tags(pdbqt_path):
    """移除 PDBQT 中的柔性标记（ROOT/ENDROOT/BRANCH），Vina 受体不支持"""
    with open(pdbqt_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    kept = [ln for ln in lines if not ln.startswith(("ROOT", "ENDROOT", "BRANCH", "ENDBRANCH", "TORSDOF"))]
    if len(kept) != len(lines):
        with open(pdbqt_path, "w", encoding="utf-8") as f:
            f.writelines(kept)


def detect_receptor_type(pdb_path):
    """更精确的受体类型检测，基于 PDB ATOM/HETATM 记录的残基名字段"""
    try:
        protein_residues = {
            'ALA', 'CYS', 'ASP', 'GLU', 'PHE', 'GLY', 'HIS', 'ILE',
            'LYS', 'LEU', 'MET', 'ASN', 'PRO', 'GLN', 'ARG', 'SER',
            'THR', 'VAL', 'TRP', 'TYR'
        }
        dna_residues = {'DA', 'DT', 'DC', 'DG'}
        rna_residues = {'A', 'U', 'G', 'C'}
        sugar_residues = {'GLC', 'MAN', 'GAL', 'FUC', 'XYL', 'NAG', 'BMA'}
        metal_atoms = {'ZN', 'MG', 'CA', 'MN', 'FE', 'CU', 'NA', 'K'}

        protein_count = 0
        dna_count = 0
        rna_count = 0
        sugar_count = 0
        metal_count = 0

        with open(pdb_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if not line.startswith(('ATOM', 'HETATM')):
                    continue
                # PDB 格式: 残基名在 columns 17-19 (0-indexed)
                resname = line[17:20].strip()
                # 元素符号在 columns 76-77, 若缺失则从原子名推断
                element = line[76:78].strip() if len(line) > 77 else ''

                if resname in protein_residues:
                    protein_count += 1
                elif resname in dna_residues:
                    dna_count += 1
                elif resname in rna_residues:
                    rna_count += 1
                elif resname in sugar_residues:
                    sugar_count += 1
                elif element in metal_atoms:
                    metal_count += 1

        total_residues = protein_count + dna_count + rna_count + sugar_count
        if total_residues == 0:
            return "small_molecule"
        if protein_count > 50:
            return "protein"
        if dna_count + rna_count > max(protein_count, sugar_count) * 2:
            return "nucleic_acid"
        if sugar_count > max(protein_count, dna_count + rna_count) * 2:
            return "carbohydrate"
        if metal_count > 0 and protein_count < 10:
            return "metal_complex"
        if protein_count > 0 and protein_count <= 50:
            return "peptide"
        return "protein"
    except Exception as e:
        log(f"受体类型检测异常: {str(e)}", "ERROR")
        return "unknown"


def process_special_receptor(raw_path, base_name, env, receptor_type):
    """处理特殊类型受体，增强错误处理和备选方案（Windows路径适配）"""
    try:
        output_dir = os.path.join(CONFIG["3.受体"], base_name)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{base_name}.pdbqt")
        task_log = []
        task_log.append(("INFO", f"🔬 检测到{receptor_type}受体，使用特殊处理流程"))
        raw_path_quoted = f'"{raw_path}"'
        output_path_quoted = f'"{output_path}"'

        # 小分子类型优先尝试 Meeko（保留手性 + 质子化）
        if receptor_type == "small_molecule":
            task_log.append(("INFO", "尝试方案0：Meeko 制备（保留手性 + 质子化 pH 7.4）"))
            meeko_ok = prepare_with_meeko(raw_path, output_path)
            if meeko_ok and os.path.exists(output_path):
                _strip_pdbqt_flex_tags(output_path)
            if meeko_ok and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                output_size = os.path.getsize(output_path) / 1024
                task_log.append(("SUCCESS", f"Meeko 制备成功（大小：{output_size:.2f}KB）"))
                return True, task_log
            task_log.append(("WARNING", "Meeko 失败，回退到 OpenBabel..."))

        task_log.append(("INFO", "尝试方案1：标准PDBQT转换"))
        cmd_standard = f'"{get_tool_path("obabel.exe")}" -ipdb {raw_path_quoted} -opdbqt -O {output_path_quoted} -xr --partialcharge gasteiger'
        task_log.append(("INFO", f"执行命令: {cmd_standard}"))
        output, success = run_command(cmd_standard, env=env, capture_output=True)
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            output_size = os.path.getsize(output_path) / 1024
            task_log.append(("SUCCESS", f"方案1成功（大小：{output_size:.2f}KB）"))
            return True, task_log
        task_log.append(("WARNING", "方案1失败，尝试方案2：不使用-xr参数"))
        cmd_no_xr = f'"{get_tool_path("obabel.exe")}" -ipdb {raw_path_quoted} -opdbqt -O {output_path_quoted} --partialcharge gasteiger'
        task_log.append(("INFO", f"执行命令: {cmd_no_xr}"))
        output, success = run_command(cmd_no_xr, env=env, capture_output=True)
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            output_size = os.path.getsize(output_path) / 1024
            task_log.append(("SUCCESS", f"方案2成功（大小：{output_size:.2f}KB）"))
            return True, task_log
        task_log.append(("WARNING", "方案2失败，尝试方案3：mol2中间转换"))
        temp_mol2 = os.path.join(CONFIG["5.临时文件"], f"{base_name}_temp.mol2")
        temp_mol2_quoted = f'"{temp_mol2}"'  # Windows路径引用
        cmd_to_mol2 = f'"{get_tool_path("obabel.exe")}" {raw_path_quoted} -O {temp_mol2_quoted} -omol2'
        task_log.append(("INFO", f"转换为MOL2: {cmd_to_mol2}"))
        output_mol2, success_mol2 = run_command(cmd_to_mol2, env=env, capture_output=True)
        if success_mol2 and os.path.exists(temp_mol2) and os.path.getsize(temp_mol2) > 100:
            cmd_mol2_to_pdbqt = f'"{get_tool_path("obabel.exe")}" {temp_mol2_quoted} -O {output_path_quoted} -opdbqt --partialcharge gasteiger'
            task_log.append(("INFO", f"MOL2转PDBQT: {cmd_mol2_to_pdbqt}"))
            output_pdbqt, success_pdbqt = run_command(cmd_mol2_to_pdbqt, env=env, capture_output=True)
            if os.path.exists(temp_mol2):
                try:
                    os.remove(temp_mol2)
                    task_log.append(("INFO", f"清理临时MOL2文件：{temp_mol2}"))
                except Exception as e:
                    task_log.append(("WARNING", f"清理临时文件失败: {str(e)}（可手动删除）"))
            if success_pdbqt and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                output_size = os.path.getsize(output_path) / 1024
                task_log.append(("SUCCESS", f"方案3成功（大小：{output_size:.2f}KB）"))
                return True, task_log
            else:
                task_log.append(("ERROR", f"MOL2转PDBQT失败: {output_pdbqt[:200]}"))
        else:
            task_log.append(("ERROR", f"转换为MOL2失败: {output_mol2[:200]}"))
        task_log.append(("WARNING", "方案3失败，尝试方案4：简化转换（无电荷计算）"))
        cmd_simple = f'"{get_tool_path("obabel.exe")}" -ipdb {raw_path_quoted} -opdbqt -O {output_path_quoted}'
        task_log.append(("INFO", f"执行命令: {cmd_simple}"))
        output, success = run_command(cmd_simple, env=env, capture_output=True)
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            output_size = os.path.getsize(output_path) / 1024
            task_log.append(("WARNING", f"方案4成功但无电荷计算（大小：{output_size:.2f}KB）"))
            return True, task_log
        task_log.append(("ERROR", f"所有转换方案均失败"))
        if output:
            task_log.append(("ERROR", f"   最后错误信息: {output[:500]}"))
        return False, task_log
    except Exception as e:
        task_log.append(("ERROR", f"{receptor_type}受体处理异常：{str(e)}"))
        return False, task_log


def validate_and_repair_pdb_file(pdb_path, log_text=None):
    """验证和修复PDB文件格式（Windows编码兼容）"""
    try:
        with open(pdb_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        has_atoms = any(line.startswith(('ATOM', 'HETATM')) for line in lines)
        has_end = any(line.startswith('END') for line in lines)
        if not has_atoms:
            log(f"❌ PDB文件无效：未找到ATOM/HETATM记录", "ERROR", log_text)
            return False
        if not has_end:
            lines.append('END\n')
            with open(pdb_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            log(f"✅ 修复PDB文件：添加END标记", "INFO", log_text)
        return True
    except Exception as e:
        log(f"❌ PDB文件验证失败：{str(e)}", "ERROR", log_text)
        return False


def _run_receptor_in_subprocess(receptor_file, env):
    """用当前程序的 worker 模式拉起子进程，避免 native 库崩溃带退出整个 GUI"""
    try:
        si = None
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

        runtime_python = get_runtime_python()
        main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'main.py'))
        cmd = [runtime_python, main_py, '--receptor-worker', json.dumps(env, ensure_ascii=False), receptor_file]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            startupinfo=si,
            timeout=300,
        )
        if proc.returncode != 0:
            debug_path = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else cmd[1]), 'receptor_worker_debug.log')
            debug_text = ''
            try:
                if os.path.exists(debug_path):
                    with open(debug_path, 'r', encoding='utf-8', errors='ignore') as f:
                        debug_text = f.read()[-1000:]
            except Exception:
                pass
            detail = f'子进程退出码 {proc.returncode}: {proc.stderr[:300]}'
            if debug_text:
                detail += f'\n[worker_debug]\n{debug_text}'
            return (receptor_file, '失败', detail, [('ERROR', proc.stderr[:300] or proc.stdout[:300] or '子进程无输出'), ('ERROR', debug_text or '无worker调试日志')])
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            return (receptor_file, '失败', '子进程无输出', [('ERROR', '子进程无输出')])
        return tuple(json.loads(lines[-1]))
    except Exception as e:
        return (receptor_file, '失败', f'子进程执行失败: {str(e)}', [('ERROR', f'子进程执行失败: {str(e)}')])


def _pdbfixer_stage2_worker(temp_clean, temp_fixed, result_queue):
    """PDBFixer 阶段2子进程入口（模块级函数，支持 Windows spawn）"""
    try:
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
        from openmm import Platform

        fixer2 = PDBFixer(filename=temp_clean)
        try:
            fixer2.platform = Platform.getPlatformByName('Reference')
        except Exception:
            pass

        fixer2.findMissingResidues()
        fixer2.findMissingAtoms()
        fixer2.addMissingAtoms()
        fixer2.addMissingHydrogens(7.4)
        with open(temp_fixed, 'w') as f:
            PDBFile.writeFile(fixer2.topology, fixer2.positions, f)
        result_queue.put(('ok', temp_fixed))
    except Exception as e:
        result_queue.put(('error', str(e)))


def process_one_receptor(receptor_file, env):
    """单受体处理（Windows路径和命令适配）"""
    debug_file = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__), 'receptor_worker_debug.log')
    def dbg(msg):
        try:
            with open(debug_file, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')
        except Exception:
            pass

    dbg('process_one:start')
    base_name = os.path.splitext(receptor_file)[0]
    raw_path = os.path.abspath(os.path.join(CONFIG["1.原始受体"], receptor_file))
    output_dir = os.path.abspath(os.path.join(CONFIG["3.受体"], base_name))
    output_path = os.path.join(output_dir, f"{base_name}.pdbqt")
    task_log = []
    task_log.append(("INFO", f"\n" + "="*50))
    task_log.append(("INFO", f"开始处理受体：{base_name}"))
    task_log.append(("INFO", f"原始文件路径：{raw_path}"))
    task_log.append(("INFO", f"目标输出路径：{output_path}"))
    task_log.append(("INFO", "-"*50))
    try:
        dbg('process_one:validate_start')
        task_log.append(("INFO", f"步骤1/5：验证输入文件..."))
        if not os.path.exists(raw_path):
            err_msg = "原始文件不存在"
            task_log.append(("ERROR", err_msg))
            return (receptor_file, "失败", err_msg, task_log)

        ext = os.path.splitext(raw_path)[1].lower()
        is_small_mol_input = ext in (".sdf", ".mol", ".mol2")

        if is_small_mol_input:
            # ========== 小分子受体：直接走 Meeko → PDBQT ==========
            task_log.append(("SUCCESS", f"小分子受体文件（{ext}），使用 Meeko 制备"))
            task_log.append(("INFO", f"步骤2/5：创建输出目录..."))
            try:
                os.makedirs(output_dir, exist_ok=True)
                task_log.append(("SUCCESS", f"输出目录创建成功：{output_dir}"))
            except Exception as e:
                err_msg = f"创建目录失败：{str(e)}"
                task_log.append(("ERROR", err_msg))
                return (receptor_file, "失败", err_msg, task_log)

            task_log.append(("INFO", f"步骤3/5：Meeko 制备（保留手性 + 质子化 pH 7.4）..."))
            meeko_ok = prepare_with_meeko(raw_path, output_path)
            # Meeko 生成的是柔性 PDBQT（含 ROOT/BRANCH/ENDROOT），受体需清理
            if meeko_ok and os.path.exists(output_path):
                _strip_pdbqt_flex_tags(output_path)
                if os.path.getsize(output_path) < 50:
                    meeko_ok = False
            if meeko_ok and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                output_size = os.path.getsize(output_path) / 1024
                task_log.append(("SUCCESS", f"Meeko 制备成功（大小：{output_size:.2f}KB）"))
                task_log.append(("INFO", "-"*50))
                task_log.append(("SUCCESS", f"受体处理成功：{base_name}（小分子）"))
                task_log.append(("INFO", "="*50 + "\n"))
                return (receptor_file, "成功", f"文件已保存至{output_path}", task_log)

            # Meeko 失败，回退到 OpenBabel
            task_log.append(("WARNING", "Meeko 制备失败，回退到 OpenBabel..."))
            cmd_ob = (
                f'"{get_tool_path("obabel.exe")}" "{raw_path}" '
                f'-O "{output_path}" -opdbqt --gen3d --partialcharge gasteiger'
            )
            task_log.append(("INFO", f"执行命令: {cmd_ob}"))
            output, success = run_command(cmd_ob, env=env, capture_output=True)
            if success and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                output_size = os.path.getsize(output_path) / 1024
                task_log.append(("SUCCESS", f"OpenBabel 制备成功（大小：{output_size:.2f}KB）"))
                task_log.append(("INFO", "-"*50))
                task_log.append(("SUCCESS", f"受体处理成功：{base_name}（小分子, Obabel回退）"))
                task_log.append(("INFO", "="*50 + "\n"))
                return (receptor_file, "成功", f"文件已保存至{output_path}", task_log)
            else:
                err_msg = f"小分子受体制备失败（Meeko + Obabel 均失败）"
                task_log.append(("ERROR", err_msg))
                return (receptor_file, "失败", err_msg, task_log)

        # ========== PDB 受体：走原有流程 ==========
        if not raw_path.lower().endswith(".pdb"):
            err_msg = f"不支持的受体文件格式：{ext}（支持 .pdb / .sdf / .mol / .mol2）"
            task_log.append(("ERROR", err_msg))
            return (receptor_file, "失败", err_msg, task_log)
        if not validate_and_repair_pdb_file(raw_path):
            err_msg = "PDB文件格式无效"
            task_log.append(("ERROR", err_msg))
            return (receptor_file, "失败", err_msg, task_log)
        file_size = os.path.getsize(raw_path) / 1024
        dbg('process_one:validate_done')
        task_log.append(("SUCCESS", f"输入文件验证通过（大小：{file_size:.2f}KB）"))
        task_log.append(("INFO", f"步骤2/5：创建输出目录..."))
        dbg('process_one:mkdir_start')
        try:
            os.makedirs(output_dir, exist_ok=True)
            dbg('process_one:mkdir_done')
            task_log.append(("SUCCESS", f"输出目录创建成功：{output_dir}"))
        except Exception as e:
            err_msg = f"创建目录失败：{str(e)}"
            task_log.append(("ERROR", err_msg))
            return (receptor_file, "失败", err_msg, task_log)
        task_log.append(("INFO", f"步骤3/5：检测受体类型..."))
        dbg('process_one:detect_type_start')
        receptor_type = detect_receptor_type(raw_path)
        dbg(f'process_one:detect_type_done:{receptor_type}')
        task_log.append(("SUCCESS", f"受体类型：{receptor_type}"))
        if receptor_type != "protein":
            success, special_log = process_special_receptor(raw_path, base_name, env, receptor_type)
            task_log.extend(special_log)
            if success:
                if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    task_log.append(("INFO", "-"*50))
                    task_log.append(("SUCCESS", f"受体处理成功：{base_name}（{receptor_type}）"))
                    task_log.append(("INFO", "="*50 + "\n"))
                    return (receptor_file, "成功", f"文件已保存至{output_path}", task_log)
                else:
                    task_log.append(("ERROR", "文件创建失败或为空"))
                    return (receptor_file, "失败", "文件创建失败", task_log)
            else:
                task_log.append(("ERROR", f"{receptor_type}受体处理失败"))
                return (receptor_file, "失败", f"{receptor_type}处理失败", task_log)
        task_log.append(("INFO", f"步骤3/5：结构净化..."))
        dbg('process_one:pdbfixer_import_start')
        temp_clean = os.path.join(CONFIG["5.临时文件"], f"{base_name}_clean.pdb")
        temp_fixed = os.path.join(CONFIG["5.临时文件"], f"{base_name}_fixed.pdb")
        pdbfixer_ok = False

        # 快速判定：如果之前 PDBFixer 阶段1成功但阶段2失败过，直接跳过
        previous_failed = (
            os.path.exists(temp_clean) and os.path.getsize(temp_clean) > 100
            and not os.path.exists(temp_fixed)
        )
        if previous_failed:
            task_log.append(("INFO", "检测到之前 PDBFixer 阶段2失败记录，跳过结构修复，直接使用已净化PDB转换"))

        if not previous_failed:
            # 阶段1：去异原子（在主进程跑，很快）
            stage1_ok = False
            try:
                from pdbfixer import PDBFixer
                from openmm.app import PDBFile
                from openmm import Platform
                import math

                nan_atoms = []
                with open(raw_path, 'r', encoding='utf-8', errors='ignore') as _pf:
                    for _line in _pf:
                        if _line.startswith(('ATOM', 'HETATM')):
                            try:
                                _x, _y, _z = float(_line[30:38]), float(_line[38:46]), float(_line[46:54])
                                if any(math.isnan(v) or math.isinf(v) for v in (_x, _y, _z)):
                                    nan_atoms.append(_line[:30].strip())
                            except ValueError:
                                nan_atoms.append(_line[:30].strip())
                if nan_atoms:
                    task_log.append(("WARNING", f"检测到 {len(nan_atoms)} 个原子有无效坐标"))
                    raise ValueError(f"PDB 文件含 {len(nan_atoms)} 个无效坐标的原子")

                dbg('process_one:pdbfixer_stage1_start')
                fixer = PDBFixer(filename=raw_path)
                fixer.removeHeterogens(keepWater=False)
                with open(temp_clean, 'w') as f:
                    PDBFile.writeFile(fixer.topology, fixer.positions, f)
                dbg('process_one:pdbfixer_stage1_done')
                task_log.append(("SUCCESS", f"结构净化完成（去异原子），临时文件：{temp_clean}"))
                stage1_ok = True
            except Exception as e:
                task_log.append(("WARNING", f"PDBFixer 阶段1失败 ({str(e)[:120]})，将直接使用原始PDB转换"))
                dbg(f'process_one:pdbfixer_stage1_error={e}')

            # 阶段2：残基补全+加氢（在子进程跑，防止 OpenMM 卡死）
            if stage1_ok:
                task_log.append(("INFO", f"步骤4/5：残基补全、加氢及质子化（pH 7.4，超时180秒）..."))
                try:
                    from multiprocessing import Process, Queue

                    q = Queue()
                    p = Process(target=_pdbfixer_stage2_worker, args=(temp_clean, temp_fixed, q), daemon=True)
                    p.start()
                    p.join(timeout=180)

                    if p.is_alive():
                        p.kill()
                        p.join()
                        task_log.append(("WARNING", "PDBFixer 阶段2 超时（>180秒），回退到 OpenBabel 直接转换"))
                        dbg('process_one:pdbfixer_stage2_timeout')
                    elif not q.empty():
                        status, info = q.get()
                        if status == 'ok':
                            task_log.append(("SUCCESS", f"残基补全和加氢完成，临时文件：{info}"))
                            pdbfixer_ok = True
                            dbg('process_one:pdbfixer_stage2_done')
                        else:
                            task_log.append(("WARNING", f"PDBFixer 阶段2失败 ({info[:150]})，回退到 OpenBabel 转换"))
                            dbg(f'process_one:pdbfixer_stage2_error={info}')
                    else:
                        task_log.append(("WARNING", "PDBFixer 阶段2 异常退出，回退到 OpenBabel 转换"))
                        dbg('process_one:pdbfixer_stage2_no_result')
                except Exception as e:
                    task_log.append(("WARNING", f"PDBFixer 阶段2 进程异常 ({str(e)[:120]})，回退"))
                    dbg(f'process_one:pdbfixer_stage2_proc_error={e}')
                finally:
                    # 清理不完整的 temp_fixed
                    if not pdbfixer_ok:
                        for _f in [temp_fixed]:
                            try:
                                if os.path.exists(_f):
                                    os.remove(_f)
                            except Exception:
                                pass

        if not pdbfixer_ok:
            # 回退方案：跳过 PDBFixer，直接用 OpenBabel 转换
            # 优先使用 temp_clean（阶段1已去异原子），其次用原始 PDB
            if os.path.exists(temp_clean) and os.path.getsize(temp_clean) > 100:
                task_log.append(("INFO", "使用备选方案：OpenBabel 转换已净化PDB（已去异原子）"))
                temp_fixed = temp_clean
            else:
                task_log.append(("INFO", "使用备选方案：OpenBabel 直接转换原始PDB（跳过结构修复）"))
                temp_fixed = raw_path
        step5_label = "步骤5/5：转换为PDBQT格式" + ("（含氢原子）..." if pdbfixer_ok else "（跳过加氢）...")
        task_log.append(("INFO", step5_label))
        dbg('process_one:obabel_start')
        temp_fixed_quoted = f'"{temp_fixed}"'
        output_path_quoted = f'"{output_path}"'
        cmd_pdbqt = (
            f'"{get_tool_path("obabel.exe")}" -ipdb {temp_fixed_quoted} -opdbqt -O {output_path_quoted} '
            "-xr --partialcharge gasteiger"
        )
        task_log.append(("INFO", f"执行命令：{cmd_pdbqt}"))
        task_log.append(("DEBUG", f"obabel路径: {get_tool_path('obabel.exe')}"))
        task_log.append(("DEBUG", f"BABEL_DATADIR: {env.get('BABEL_DATADIR', '')}"))
        task_log.append(("DEBUG", f"PATH前缀: {env.get('PATH', '')[:300]}"))
        output, success = run_command(cmd_pdbqt, env=env, capture_output=True)
        dbg(f'process_one:obabel_first_done success={success} exists={os.path.exists(output_path)}')
        if (not success or not os.path.exists(output_path)) and os.path.exists(temp_fixed):
            fallback_cmd = (
                f'"{get_tool_path("obabel.exe")}" -ipdb {temp_fixed_quoted} -opdbqt -O {output_path_quoted} -xr'
            )
            task_log.append(("WARNING", "首次 PDBQT 转换失败，尝试去掉 partialcharge 参数重试"))
            task_log.append(("INFO", f"回退命令：{fallback_cmd}"))
            output2, success2 = run_command(fallback_cmd, env=env, capture_output=True)
            dbg(f'process_one:obabel_fallback_done success={success2} exists={os.path.exists(output_path)}')
            if success2 and os.path.exists(output_path):
                output = output2
                success = True
            else:
                output = (output or "") + "\n[Fallback]\n" + (output2 or "")
        # 验证输出文件有效性（不只是存在，还要有内容）
        output_valid = (
            success and os.path.exists(output_path) and os.path.getsize(output_path) > 100
        )
        if not output_valid and os.path.exists(output_path) and os.path.getsize(output_path) <= 100:
            task_log.append(("WARNING", f"OpenBabel 生成了空文件（0分子转换），尝试备选格式..."))
            # 最后一招：尝试 mol2 → pdbqt 路径
            temp_mol2 = os.path.join(CONFIG["5.临时文件"], f"{base_name}_fallback.mol2")
            mol2_cmd = f'"{get_tool_path("obabel.exe")}" -ipdb {temp_fixed_quoted} -omol2 -O "{temp_mol2}" --gen3d'
            task_log.append(("INFO", f"尝试 PDB→MOL2: {mol2_cmd}"))
            _, mol2_ok = run_command(mol2_cmd, env=env, capture_output=True)
            if mol2_ok and os.path.exists(temp_mol2) and os.path.getsize(temp_mol2) > 50:
                pdbqt_cmd = f'"{get_tool_path("obabel.exe")}" -imol2 "{temp_mol2}" -opdbqt -O {output_path_quoted} --partialcharge gasteiger'
                task_log.append(("INFO", f"尝试 MOL2→PDBQT: {pdbqt_cmd}"))
                _, pdbqt_ok = run_command(pdbqt_cmd, env=env, capture_output=True)
                if pdbqt_ok and os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                    output_valid = True
                    success = True
                try:
                    os.remove(temp_mol2)
                except Exception:
                    pass
        if not output_valid:
            err_msg = f"PDBQT转换失败：{output[:200]}"
            task_log.append(("ERROR", err_msg))
            return (receptor_file, "失败", err_msg, task_log)
        output_size = os.path.getsize(output_path) / 1024
        task_log.append(("SUCCESS", f"PDBQT转换完成（{'含氢原子' if pdbfixer_ok else '未加氢'}，大小：{output_size:.2f}KB）"))
        # 只清理 PDBFixer 产生的临时文件，不删原始受体
        for temp_file in [temp_clean, temp_fixed]:
            if temp_file != raw_path and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    task_log.append(("INFO", f"清理临时文件：{temp_file}"))
                except Exception as e:
                    task_log.append(("WARNING", f"清理临时文件失败: {str(e)}（可手动删除）"))
        task_log.append(("INFO", "-"*50))
        dbg('process_one:success')
        task_log.append(("SUCCESS", f"受体处理成功：{base_name}"))
        task_log.append(("INFO", "="*50 + "\n"))
        return (receptor_file, "成功", f"文件已保存至{output_path}", task_log)
    except Exception as e:
        err_msg = f"处理过程异常：{str(e)}"
        task_log.append(("ERROR", err_msg))
        return (receptor_file, "失败", err_msg, task_log)


def process_all_receptors(env, log_text=None):
    """批量处理受体（Windows多进程适配）"""
    log("\n" + "="*70, log_text=log_text)
    log("===== 开始处理所有受体 =====", log_text=log_text)
    log("="*70, log_text=log_text)
    supported_exts = (".pdb", ".sdf", ".mol", ".mol2")
    receptor_files = [
        f for f in os.listdir(CONFIG["1.原始受体"])
        if not f.startswith(".") and f.lower().endswith(supported_exts)
    ]
    if not receptor_files:
        log("⚠️ 未发现任何受体文件（.pdb/.sdf/.mol/.mol2）", log_text=log_text)
        log("="*70 + "\n", log_text=log_text)
        return 0, 0, []
    log(f"发现{len(receptor_files)}个受体文件，准备处理...", log_text=log_text)
    for i, f in enumerate(receptor_files, 1):
        log(f"  {i}. {f}", log_text=log_text)
    results = []
    max_workers = get_optimal_workers(len(receptor_files))
    log(f"使用{max_workers}个进程处理受体（Windows资源适配）", log_text=log_text)
    results = [_run_receptor_in_subprocess(f, env) for f in receptor_files]
    success_count = 0
    failed_tasks = []
    for file_name, status, detail, task_log in results:
        for level, line in task_log:
            log(line, level, log_text=log_text)
        if status == "成功":
            success_count += 1
        else:
            failed_tasks.append(f"{file_name}：{detail}")
    log("\n" + "="*70, log_text=log_text)
    log(f"受体处理总结：成功{success_count}/{len(receptor_files)}", log_text=log_text)
    if failed_tasks:
        log("失败任务详情：", "WARNING", log_text=log_text)
        for task in failed_tasks:
            log(f"  - {task}", "WARNING", log_text=log_text)
    log(f"所有受体PDBQT文件保存目录：{os.path.abspath(CONFIG['3.受体'])}", log_text=log_text)
    log("="*70 + "\n", log_text=log_text)
    return success_count, len(receptor_files), failed_tasks