import os
import sys
import re as _re
import base64 as _base64
import multiprocessing as mp
from datetime import datetime

from .config import CONFIG
from .logger import log
from .utils import run_command, get_conda_env


def _get_software_versions(env, log_text=None):
    """检测各软件版本"""
    versions = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }

    # OpenBabel
    try:
        out, ok = run_command("obabel -V", env=env, capture_output=True)
        m = _re.search(r"(\d+\.\d+\.\d+)", out)
        versions["openbabel"] = m.group(1) if m else "未知"
    except Exception:
        versions["openbabel"] = "未知"

    # Vina
    try:
        vina = CONFIG.get("vina_path", "vina")
        out, ok = run_command(f'"{vina}" --version', env=env, capture_output=True)
        m = _re.search(r"(\d+\.\d+\.\d+)", out)
        versions["vina"] = m.group(1) if m else "未知"
    except Exception:
        versions["vina"] = "未知"

    # PDBfixer
    try:
        import pdbfixer
        versions["pdbfixer"] = getattr(pdbfixer, "__version__", "已安装")
    except ImportError:
        versions["pdbfixer"] = "未安装"

    # Pandas
    try:
        import pandas as _pd
        versions["pandas"] = _pd.__version__
    except Exception:
        versions["pandas"] = "未知"

    # Matplotlib
    try:
        import matplotlib
        versions["matplotlib"] = matplotlib.__version__
    except ImportError:
        versions["matplotlib"] = "未安装"

    return versions


def _count_files(directory, extensions):
    """统计目录中指定扩展名的文件数量"""
    if not os.path.isdir(directory):
        return 0
    return sum(1 for f in os.listdir(directory)
               if os.path.splitext(f)[1].lower() in extensions and not f.startswith("."))


def _list_file_names(directory, extensions, max_items=10):
    """列出目录中的文件名（去扩展名），最多 max_items 个"""
    if not os.path.isdir(directory):
        return "无"
    names = [os.path.splitext(f)[0] for f in sorted(os.listdir(directory))
             if os.path.splitext(f)[1].lower() in extensions and not f.startswith(".")]
    if not names:
        return "无"
    display = ", ".join(names[:max_items])
    if len(names) > max_items:
        display += f" 等共{len(names)}个"
    return display


def _img_to_data_uri(img_path):
    """将图片转为 data URI（内嵌到 HTML 中）"""
    if not os.path.exists(img_path):
        return ""
    ext = os.path.splitext(img_path)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".gif": "image/gif"}.get(ext, "image/png")
    with open(img_path, "rb") as f:
        data = _base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def generate_paper_content(log_text=None):
    """基于 HTML 模板生成分子对接实验报告"""
    import pandas as pd

    log("=== 开始生成实验报告 ===", "INFO", log_text)

    # ── 1. 确保输出目录存在 ──
    output_dir = CONFIG.get("10.实验报告", CONFIG.get("7.分析结果"))
    os.makedirs(output_dir, exist_ok=True)

    # ── 2. 读取模板 ──
    template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "模板", "分子对接实验报告模板.html")

    if not os.path.exists(template_path):
        log(f"❌ 未找到报告模板：{template_path}", "ERROR", log_text)
        return False

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    log(f"已加载模板：{template_path}", "INFO", log_text)

    # ── 3. 读取结合能数据 ──
    csv_path = os.path.join(CONFIG["7.分析结果"], "结合能汇总.csv")
    if not os.path.exists(csv_path):
        log("❌ 未找到结合能汇总CSV（需先执行结果分析）", "ERROR", log_text)
        return False

    try:
        df = pd.read_csv(csv_path, encoding="utf-8")
    except Exception as e:
        log(f"❌ 读取CSV失败：{e}", "ERROR", log_text)
        return False

    df_numeric = df.set_index("Gene").replace("NA", pd.NA).apply(pd.to_numeric, errors="coerce")
    all_values = df_numeric.stack()

    if all_values.empty:
        log("❌ 结合能数据为空，无法生成报告", "ERROR", log_text)
        return False

    log(f"读取到 {len(all_values)} 组结合能数据", "INFO", log_text)

    # ── 4. 收集环境信息 ──
    env = get_conda_env(log_text)
    sw = _get_software_versions(env, log_text)
    now = datetime.now()

    # ── 5. 文件统计 ──
    pdb_exts = {".pdb"}
    sdf_exts = {".sdf", ".mol", ".mol2"}
    pdbqt_exts = {".pdbqt"}

    raw_receptor_count = _count_files(CONFIG["1.原始受体"], pdb_exts)
    raw_ligand_count = _count_files(CONFIG["2.原始配体"], sdf_exts | pdbqt_exts | pdb_exts)
    processed_receptors = _count_files(CONFIG["3.受体"], pdbqt_exts)
    processed_ligands = _count_files(CONFIG["4.配体"], pdbqt_exts)

    # 受体可能是子目录结构
    if raw_receptor_count == 0 and os.path.isdir(CONFIG["1.原始受体"]):
        raw_receptor_count = sum(1 for d in os.listdir(CONFIG["1.原始受体"])
                                 if os.path.isdir(os.path.join(CONFIG["1.原始受体"], d)))
    if processed_receptors == 0 and os.path.isdir(CONFIG["3.受体"]):
        for d in os.listdir(CONFIG["3.受体"]):
            sub = os.path.join(CONFIG["3.受体"], d)
            if os.path.isdir(sub):
                processed_receptors += _count_files(sub, pdbqt_exts)

    receptor_count = max(raw_receptor_count, processed_receptors) or df_numeric.shape[0]
    ligand_count = max(raw_ligand_count, processed_ligands) or df_numeric.shape[1]

    total_docking = receptor_count * ligand_count
    valid_docking = len(all_values)
    success_rate = f"{valid_docking / total_docking * 100:.1f}%" if total_docking > 0 else "N/A"

    # ── 6. 结合能统计 ──
    e_min = all_values.min()
    e_max = all_values.max()
    e_mean = all_values.mean()
    e_std = all_values.std()
    e_median = all_values.median()
    e_q1 = all_values.quantile(0.25)
    e_q3 = all_values.quantile(0.75)

    # 最优结合对
    best_idx = all_values.idxmin()
    best_receptor = best_idx[0] if isinstance(best_idx, tuple) else str(best_idx)
    best_ligand = best_idx[1] if isinstance(best_idx, tuple) else ""

    # 质量分级
    n_total = len(all_values)
    count_excellent = int((all_values < -8).sum())
    count_good = int(((all_values >= -8) & (all_values < -5)).sum())
    count_moderate = int(((all_values >= -5) & (all_values < -3)).sum())
    count_weak = int((all_values >= -3).sum())

    pct = lambda c: f"{c / n_total * 100:.1f}%" if n_total > 0 else "0%"

    # Top 5
    df_stack = all_values.reset_index()
    df_stack.columns = ["受体", "配体", "结合能"]
    df_top5 = df_stack.nsmallest(5, "结合能").reset_index(drop=True)

    def _top(i, field):
        if i >= len(df_top5):
            return "—"
        row = df_top5.iloc[i]
        if field == "receptor":
            return str(row["受体"])
        elif field == "ligand":
            return str(row["配体"])
        elif field == "energy":
            return f"{row['结合能']:.2f}"
        elif field == "sites":
            r = str(row["受体"])
            return f"残基{r[-3:]}（推测）" if len(r) >= 3 else "待预测"
        elif field == "interaction":
            return "氢键+疏水作用" if row["结合能"] < -8 else "疏水作用为主"
        return "—"

    # ── 7. 图片路径 ──
    analyze_dir = CONFIG["7.分析结果"]
    heatmap_png = os.path.join(analyze_dir, "dock_heatmap.png")
    heatmap_ggplot2_png = os.path.join(analyze_dir, "dock_heatmap_ggplot2.png")

    # 受体/配体名称列表
    receptor_list = _list_file_names(CONFIG["1.原始受体"], pdb_exts)
    if receptor_list == "无" and os.path.isdir(CONFIG["1.原始受体"]):
        dirs = [d for d in sorted(os.listdir(CONFIG["1.原始受体"]))
                if os.path.isdir(os.path.join(CONFIG["1.原始受体"], d))]
        receptor_list = ", ".join(dirs[:10]) + (f" 等共{len(dirs)}个" if len(dirs) > 10 else "")
    if receptor_list == "无":
        receptor_list = ", ".join(df_numeric.index.tolist()[:10])

    ligand_list = _list_file_names(CONFIG["2.原始配体"], sdf_exts)
    if ligand_list == "无":
        ligand_list = ", ".join(df_numeric.columns.tolist()[:10])

    # ── 8. 组装占位符字典 ──
    placeholders = {
        # 封面
        "date": now.strftime("%Y年%m月%d日"),
        "platform": sys.platform.capitalize(),
        "python_version": sw["python"],
        "experiment_id": now.strftime("%Y%m%d%H%M"),
        # 摘要
        "receptor_count": str(receptor_count),
        "ligand_count": str(ligand_count),
        "vina_version": sw["vina"],
        "valid_docking_tasks": str(valid_docking),
        "binding_energy_min": f"{e_min:.2f}",
        "binding_energy_max": f"{e_max:.2f}",
        "best_energy": f"{e_min:.2f}",
        "best_receptor": best_receptor,
        "best_ligand": best_ligand,
        "success_rate": success_rate,
        # 材料
        "receptor_list": receptor_list,
        "ligand_list": ligand_list,
        "pdbfixer_version": sw["pdbfixer"],
        "openbabel_version": sw["openbabel"],
        # 参数
        "box_padding": str(CONFIG.get("box_padding", 8.0)),
        "box_coverage": str(CONFIG.get("box_coverage", 1.0)),
        "box_size_range": str(CONFIG.get("box_size_range", [20.0, 100.0])),
        "num_modes": str(CONFIG.get("num_modes", 10)),
        "energy_range": str(CONFIG.get("energy_range", 3)),
        "max_workers": str(CONFIG.get("max_workers", 1)),
        "cpu_count": str(mp.cpu_count()),
        # 结果统计
        "raw_receptor_count": str(raw_receptor_count),
        "processed_receptors": str(processed_receptors),
        "raw_ligand_count": str(raw_ligand_count),
        "processed_ligands": str(processed_ligands),
        "total_docking_tasks": str(total_docking),
        "mean": f"{e_mean:.2f}",
        "std": f"{e_std:.2f}",
        "median": f"{e_median:.2f}",
        "q1": f"{e_q1:.2f}",
        "q3": f"{e_q3:.2f}",
        # 质量分级
        "count_excellent": str(count_excellent),
        "pct_excellent": pct(count_excellent),
        "count_good": str(count_good),
        "pct_good": pct(count_good),
        "count_moderate": str(count_moderate),
        "pct_moderate": pct(count_moderate),
        "count_weak": str(count_weak),
        "pct_weak": pct(count_weak),
        # 图表
        "heatmap_path": os.path.basename(heatmap_png) if os.path.exists(heatmap_png) else "未生成",
        # Top 5
        "top_n": str(len(df_top5)),
    }

    # Top 1-5
    for i in range(5):
        n = i + 1
        placeholders[f"top{n}_receptor"] = _top(i, "receptor")
        placeholders[f"top{n}_ligand"] = _top(i, "ligand")
        placeholders[f"top{n}_energy"] = _top(i, "energy")
        placeholders[f"top{n}_sites"] = _top(i, "sites")
        placeholders[f"top{n}_interaction"] = _top(i, "interaction")

    # 附录路径
    placeholders["raw_receptors_path"] = os.path.abspath(CONFIG["1.原始受体"])
    placeholders["raw_ligands_path"] = os.path.abspath(CONFIG["2.原始配体"])
    placeholders["processed_receptors_path"] = os.path.abspath(CONFIG["3.受体"])
    placeholders["processed_ligands_path"] = os.path.abspath(CONFIG["4.配体"])
    placeholders["docking_results_path"] = os.path.abspath(CONFIG["6.对接结果"])
    placeholders["analyze_results_path"] = os.path.abspath(CONFIG["7.分析结果"])
    placeholders["vina_path"] = CONFIG.get("vina_path", "vina")
    placeholders["generation_time"] = now.strftime("%Y-%m-%d %H:%M:%S")

    # 软件版本
    placeholders["pandas_version"] = sw["pandas"]
    placeholders["matplotlib_version"] = sw["matplotlib"]

    # ── 9. 替换模板占位符 ──
    html = template
    for key, value in placeholders.items():
        v = str(value)
        html = html.replace("{{ " + key + " }}", v)
        html = html.replace("{{" + key + "}}", v)

    # ── 10. 内嵌图片（替换占位 div 为实际 img） ──
    figures = [
        (heatmap_png, "结合能热图"),
        (heatmap_ggplot2_png, "结合能热图ggplot2"),
    ]
    for img_path, alt in figures:
        if os.path.exists(img_path):
            data_uri = _img_to_data_uri(img_path)
            img_tag = (f'<img src="{data_uri}" alt="{alt}" '
                       f'style="max-width:100%;border-radius:10px;'
                       f'box-shadow:0 4px 16px rgba(0,0,0,0.08);" />')
            pattern = _re.compile(
                r'<div class="fig-placeholder">.*?' + _re.escape(alt) + r'.*?</div>\s*</div>',
                _re.DOTALL
            )
            html = pattern.sub(img_tag, html, count=1)

    # ── 11. 检查是否有未填充的占位符 ──
    remaining = _re.findall(r'\{\{\s*(\w+)\s*\}\}', html)
    if remaining:
        log(f"⚠️ 以下占位符未填充：{', '.join(set(remaining))}", "WARNING", log_text)

    # ── 12. 写入文件 ──
    date_str = now.strftime("%Y%m%d")
    report_filename = f"分子对接实验报告_{date_str}.html"
    report_path = os.path.join(output_dir, report_filename)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"✅ 实验报告生成成功：{report_path}", "INFO", log_text)
    log(f"   包含 {len(placeholders)} 个数据点，"
        f"{sum(1 for p, _ in figures if os.path.exists(p))} 张图表已内嵌", "INFO", log_text)

    return True