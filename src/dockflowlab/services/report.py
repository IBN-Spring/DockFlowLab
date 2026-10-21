"""生成自包含的 HTML 对接报告。

报告只呈现可核实的计算结果：Vina 打分、构象排名、参数与文件来源。
不把打分换算成 ΔG / Kd，也不从打分反推氢键数量等相互作用结论。
"""

from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Iterable, Sequence

from ..config import AppSettings
from ..core.models import DockingResult, Pose
from ..logging_setup import get_logger
from ..tools.runtime import resource_path
from ..version import __app_name__, __version__, component_version
from ..workspace import Workspace
from . import modes
from .analysis import AnalysisBundle, analyze, load_heatmap_config, unique_labels
from .docking import collect_existing_results

log = get_logger("report")

BOX_LABELS = {
    "pocket": "几何口袋",
    "cocrystal": "共晶配体",
    "site": "PDB 结合位点",
    "blind": "全结构盲对接",
    "manual": "手动设置",
    "reference": "参考分子",
    "ligand": "目标分子",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _version_of(distribution: str) -> str:
    return component_version(distribution)


def _software_line() -> str:
    from ..tools.obabel import get_openbabel
    from ..tools.vina import VinaAdapter

    parts = []
    try:
        vina = VinaAdapter().version
    except Exception:
        vina = ""
    if vina:
        parts.append(vina if vina.lower().startswith("vina") else f"AutoDock Vina {vina}")
    else:
        parts.append("AutoDock Vina（未检测到可执行文件）")

    try:
        obabel = get_openbabel().version_string()
    except Exception:
        obabel = ""
    if obabel:
        parts.append(obabel)

    for distribution, label in (("rdkit", "RDKit"), ("meeko", "Meeko"), ("openmm", "OpenMM"), ("pdbfixer", "PDBFixer")):
        found = _version_of(distribution)
        if found:
            parts.append(f"{label} {found}")
    return " · ".join(parts)


def _tiles(bundle: AnalysisBundle) -> str:
    results = bundle.results
    success = [item for item in results if item.ok and item.poses]
    best = min((item.poses[0].affinity for item in success), default=None)
    targets = len({item.target for item in results})
    ligands = len({item.ligand for item in results})
    cards = [
        (str(targets), "靶点结构"),
        (str(ligands), "候选分子"),
        (f"{len(success)} / {len(results)}", "成功 / 总任务"),
        (f"{best:.2f}" if best is not None else "—", "最佳打分 (kcal/mol)"),
    ]
    pieces = []
    for index, (value, label) in enumerate(cards):
        accent = " accent" if index == 3 and best is not None else ""
        pieces.append(
            f'<div class="tile{accent}"><div class="value">{_e(value)}</div>'
            f'<div class="label">{_e(label)}</div></div>'
        )
    return "".join(pieces)


def _embed_image(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{payload}"


def _charts(bundle: AnalysisBundle) -> str:
    figures = []
    if bundle.heatmap_path:
        figures.append(
            ('<figure><img alt="结合能热图" src="' + _embed_image(bundle.heatmap_path) + '">'
             '<figcaption>热图：行为靶点，列为候选分子；颜色越深表示 Vina 打分越低（预测结合越有利）。</figcaption></figure>')
        )
    if bundle.bar_path:
        figures.append(
            ('<figure><img alt="结合能最低的复合物" src="' + _embed_image(bundle.bar_path) + '">'
             '<figcaption>柱状图：按最佳构象打分排序的复合物。</figcaption></figure>')
        )
    return "".join(figures)


def _results_table(
    results: Sequence[DockingResult],
    target_labels: dict[str, str],
    ligand_labels: dict[str, str],
) -> str:
    if not results:
        return '<p class="empty">项目里还没有对接结果。完成至少一组对接后重新生成报告。</p>'

    ordered = sorted(
        results,
        key=lambda item: (
            0 if item.ok and item.poses else 1,
            item.poses[0].affinity if item.poses else 0.0,
        ),
    )
    rows = []
    for item in ordered:
        if item.ok and item.poses:
            if item.skipped:
                status = '<span class="badge skip">已有结果</span>'
            else:
                status = '<span class="badge ok">成功</span>'
            score = f"{item.poses[0].affinity:.2f}"
            runtime = f"{item.runtime_seconds:.1f}" if item.runtime_seconds else "—"
            note = "；".join(item.warnings)
        else:
            if item.mode_supported:
                status = '<span class="badge fail">失败</span>'
            else:
                status = '<span class="badge fail">不支持</span>'
            score = "—"
            runtime = "—"
            note = item.error or "未知错误"
        rows.append(
            "<tr>"
            f"<td>{_e(target_labels.get(item.target, item.target))}</td>"
            f"<td>{_e(ligand_labels.get(item.ligand, item.ligand))}</td>"
            f"<td>{_e(item.mode_label or '—')}</td>"
            f'<td class="num">{_e(score)}</td>'
            f"<td>{_e(BOX_LABELS.get(item.box_source, item.box_source or '自动'))}</td>"
            f'<td class="num">{_e(runtime)}</td>'
            f"<td>{status}</td>"
            f"<td>{_e(note)}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>靶点</th><th>候选分子</th><th>对接类型</th><th class='num'>最佳打分</th>"
        "<th>盒子来源</th><th class='num'>耗时 (秒)</th><th>状态</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _pose_table(
    results: Sequence[DockingResult],
    target_labels: dict[str, str],
    ligand_labels: dict[str, str],
    limit: int = 30,
) -> str:
    entries: list[tuple[DockingResult, Pose]] = []
    for item in results:
        if item.ok:
            entries.extend((item, pose) for pose in item.poses)
    if not entries:
        return '<p class="empty">没有可列出的构象评分。</p>'
    entries.sort(key=lambda pair: pair[1].affinity)
    rows = []
    for item, pose in entries[:limit]:
        rows.append(
            "<tr>"
            f"<td>{_e(target_labels.get(item.target, item.target))}</td>"
            f"<td>{_e(ligand_labels.get(item.ligand, item.ligand))}</td>"
            f'<td class="num">{pose.rank}</td>'
            f'<td class="num">{pose.affinity:.2f}</td>'
            f'<td class="num">{_fmt(pose.rmsd_lb, 2)}</td>'
            f'<td class="num">{_fmt(pose.rmsd_ub, 2)}</td>'
            "</tr>"
        )
    more = ""
    if len(entries) > limit:
        more = f'<p class="meta">仅列出打分最低的 {limit} 个构象，共 {len(entries)} 个。完整数据见 analysis/binding_energies.csv。</p>'
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>靶点</th><th>候选分子</th><th class='num'>构象</th>"
        "<th class='num'>打分 (kcal/mol)</th><th class='num'>RMSD 下限 (Å)</th>"
        "<th class='num'>RMSD 上限 (Å)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>{more}"
    )


def _methods(workspace: Workspace, results: Sequence[DockingResult], settings: AppSettings) -> str:
    box_sources = sorted({BOX_LABELS.get(item.box_source, item.box_source) for item in results if item.box_source})
    target_methods = sorted({
        str(entry.meta.get("prep_method"))
        for entry in workspace.targets
        if entry.meta.get("prep_method")
    })
    ligand_methods = sorted({
        str(entry.meta.get("prep_method"))
        for entry in workspace.ligands
        if entry.meta.get("prep_method")
    })
    mode_labels = sorted({item.mode_label for item in results if item.mode_label})
    mode_notes: list[str] = []
    seen_modes: set[str] = set()
    for item in results:
        if not item.mode_key or item.mode_key in seen_modes:
            continue
        seen_modes.add(item.mode_key)
        mode = modes.MODES_BY_KEY.get(item.mode_key)
        if mode is not None and mode.warnings:
            mode_notes.append(f"{mode.label}：" + " ".join(mode.warnings))
    unsupported = sorted({
        item.mode_label for item in results
        if not item.mode_supported and item.mode_label
    })
    items = [
        ("软件版本", _software_line()),
        ("靶点准备", "；".join(target_methods) or "去除水/离子/添加剂，补全缺失残基与原子，按 pH 7.4 加氢，生成含 Gasteiger 电荷的刚性 PDBQT。"),
        ("候选分子准备", "；".join(ligand_methods) or "保留最大有机片段，补氢并生成三维构象，能量最小化，按 pH 7.4 校正质子化，输出柔性 PDBQT。"),
        ("对接类型", "；".join(mode_labels) or "根据结构类型自动识别"),
        ("对接程序", "AutoDock Vina"),
        ("搜索参数", f"exhaustiveness = {settings.exhaustiveness}，num_modes = {settings.num_modes}，energy_range = {settings.energy_range:g} kcal/mol，seed = {settings.seed}"),
        ("盒子来源", "；".join(box_sources) or "自动"),
        ("评分含义", "Vina 打分是经验评分函数的输出，单位为 kcal/mol；数值越低表示该程序预测的结合越有利。它不等同于实验测得的 ΔG 或 Kd，也不能单独证明结合模式；分数还会随 Vina 版本、评分函数（如 Vinardo）、受体/配体预处理、是否保留水/离子以及盒子范围而变化，因此不宜直接与其他软件的分数比较。小分子片段通常只能得到较小的负值。"),
        ("类型适用性", "；".join(mode_notes) or "不同对接类型的分数不宜直接横向比较。"),
        ("可重复性", "在相同软件版本、相同输入与相同参数下，固定 seed 的结果可重复；升级软件后可能出现细微差异。"),
    ]
    if unsupported:
        items.append(
            (
                "未支持的组合",
                "；".join(unsupported) + " 未执行 Vina 计算。蛋白–蛋白等大分子对接请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。",
            )
        )
    return "".join(f"<dt>{_e(key)}</dt><dd>{_e(value)}</dd>" for key, value in items)


def _warnings(bundle: AnalysisBundle, workspace: Workspace) -> str:
    messages: list[str] = []
    messages.extend(bundle.warnings or [])
    for result in bundle.results:
        messages.extend(result.warnings)
        if not result.ok and result.error:
            messages.append(f"{result.target} + {result.ligand}：{result.error}")
    for entry in list(workspace.targets) + list(workspace.ligands):
        if entry.status == "failed" and entry.message:
            messages.append(f"{entry.display_name}：{entry.message}")
    seen: set[str] = set()
    unique = []
    for message in messages:
        text = str(message).strip()
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    items = "".join(f"<li>{_e(text)}</li>" for text in unique[:40])
    extra = (
        "<li>对接结果是计算预测。要判断可靠性与生物学意义，应结合重复实验、"
        "已知共晶结构、阳性对照与文献证据。</li>"
        "<li>本报告不提供结合自由能的实验换算，也不把打分差异直接解释为某种具体相互作用。</li>"
    )
    return (
        '<div class="note warn"><strong>请先阅读</strong><ul>'
        f"{items}{extra}</ul></div>"
    )


def generate_report(
    workspace: Workspace,
    results: Sequence[DockingResult] | None = None,
    *,
    output_path: str | Path | None = None,
    settings: AppSettings | None = None,
) -> Path:
    """汇总结果、生成图表并写出单个 HTML 文件。"""
    settings = settings or AppSettings.load()
    results = list(results) if results is not None else collect_existing_results(workspace)
    bundle = analyze(workspace, results)

    output = Path(output_path) if output_path else workspace.report_dir / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    template_path = resource_path("report_template.html")
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"找不到报告模板：{template_path}") from exc

    project_name = workspace.name or workspace.root.name
    title = f"{project_name} · 分子对接结果报告"
    target_labels = unique_labels(workspace.targets)
    ligand_labels = unique_labels(workspace.ligands)
    # 热图中自定义的显示名称也用于报告表格，避免同一份报告出现两套名称。
    heatmap_config = load_heatmap_config(workspace)
    target_labels.update(heatmap_config.target_labels)
    ligand_labels.update(heatmap_config.ligand_labels)
    payload = {
        "title": _e(title),
        "project": _e(project_name),
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "tiles": _tiles(bundle),
        "charts": _charts(bundle),
        "results": _results_table(bundle.results, target_labels, ligand_labels),
        "poses": _pose_table(bundle.results, target_labels, ligand_labels),
        "methods": _methods(workspace, bundle.results, settings),
        "warnings": _warnings(bundle, workspace),
        "version": _e(__version__),
        "app_name": _e(__app_name__),
    }
    document = Template(template_text).safe_substitute(payload)
    output.write_text(document, encoding="utf-8")
    log.info("报告已生成：%s", output)
    return output
