"""生成自包含的 HTML 对接报告。

报告只呈现可核实的计算结果：对接打分、构象排名、参数与文件来源。
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


def _software_line(settings: AppSettings | None = None) -> str:
    from ..tools.obabel import get_openbabel
    from ..tools.vina import ENGINE_ORDER, engine_from_settings, make_engine

    parts = []
    for key in ENGINE_ORDER:
        try:
            engine = (
                engine_from_settings(settings, key)
                if settings is not None
                else make_engine(key)
            )
        except Exception:
            continue
        if not engine.available:
            continue
        version = engine.version or engine.label
        if key == "vina" and not version.lower().startswith(("vina", "autodock")):
            version = f"AutoDock Vina {version}"
        if key != "vina" and not version.lower().startswith(engine.label.lower()):
            version = f"{engine.label} {version}"
        parts.append(version)
    if not parts:
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


_PARAM_KEYS = (
    "exhaustiveness",
    "num_modes",
    "energy_range",
    "seed",
    "cpu",
    "engine",
    "engine_label",
    "engine_version",
    "vina_version",
    "scoring",
)


def _engine_text(parameters: dict[str, Any]) -> str:
    """返回“引擎 + 版本”；兼容只有 vina_version 的旧结果。"""
    label = str(parameters.get("engine_label") or "").strip()
    version = str(parameters.get("engine_version") or "").strip()
    if not version:
        version = str(parameters.get("vina_version") or "").strip()
    if not label:
        label = "AutoDock Vina" if version or parameters.get("engine") in (None, "", "vina") else ""
    if version and label and label.lower() not in version.lower():
        return f"{label} {version}"
    return version or label


def _engine_summary(results: Sequence[DockingResult]) -> str:
    """列出本次结果实际使用的引擎；不同引擎的结果会并列呈现。"""
    names: list[str] = []
    for item in results:
        if not item.ok:
            continue
        text = _engine_text(item.parameters or {}) or "AutoDock Vina"
        if text not in names:
            names.append(text)
    return "；".join(names) or "AutoDock Vina"


def _run_parameters_text(results: Sequence[DockingResult]) -> str:
    """列出结果实际使用的参数，而不是报告生成时的当前设置。"""
    successful = [item for item in results if item.ok and item.poses]
    if not successful:
        return "尚无成功结果。"
    groups: dict[tuple[Any, ...], int] = {}
    missing = 0
    for item in successful:
        parameters = item.parameters or {}
        if parameters.get("exhaustiveness") in (None, ""):
            missing += 1
            continue
        signature = tuple(parameters.get(key) for key in _PARAM_KEYS)
        groups[signature] = groups.get(signature, 0) + 1
    parts: list[str] = []
    for signature, count in groups.items():
        (exhaustiveness, num_modes, energy_range, seed, cpu, engine,
         engine_label, engine_version, vina_version, scoring) = signature
        try:
            text = (
                f"exhaustiveness = {int(exhaustiveness)}，"
                f"num_modes = {int(num_modes)}，"
                f"energy_range = {float(energy_range):g} kcal/mol，"
                f"seed = {int(seed)}，cpu = {int(cpu)}"
                + ("（全部核心）" if int(cpu) == 0 else "")
            )
        except (TypeError, ValueError):
            text = "参数记录不完整"
        engine_text = _engine_text(
            {
                "engine": engine,
                "engine_label": engine_label,
                "engine_version": engine_version,
                "vina_version": vina_version,
            }
        )
        if engine_text:
            text += f"，{engine_text}"
        if scoring:
            label = "Vinardo 评分" if str(scoring) == "vinardo" else f"scoring = {scoring}"
            text += f"，{label}"
        if len(groups) > 1 or count != len(successful):
            text += f"（{count} 组）"
        parts.append(text)
    if missing:
        parts.append(f"{missing} 组结果未记录运行参数（由旧版本生成）")
    flex_text = _flex_state_text(results)
    if flex_text:
        parts.append(flex_text)
    return "；".join(parts) or "未记录运行参数"


def _flex_state_text(results: Sequence[DockingResult]) -> str:
    """说明本次是否柔化了侧链、是否枚举了配体状态。"""
    flex: list[str] = []
    state_count = 0
    for item in results:
        if not item.ok:
            continue
        parameters = item.parameters or {}
        residues = str(parameters.get("flex_residues") or "").strip()
        if residues and residues not in flex:
            flex.append(residues)
        try:
            state_count = max(state_count, int(parameters.get("ligand_state_count") or 1))
        except (TypeError, ValueError):
            continue
    parts: list[str] = []
    if flex:
        parts.append("柔性残基：" + "；".join(flex))
    if state_count > 1:
        parts.append(
            f"配体按 {state_count} 个互变异构/质子化状态分别对接，此处只展示最优状态"
        )
    return "；".join(parts)


def _box_detail(item: DockingResult) -> str:
    if not item.box_center or not item.box_size:
        return ""
    try:
        cx, cy, cz = (float(value) for value in item.box_center)
        sx, sy, sz = (float(value) for value in item.box_size)
    except (TypeError, ValueError):
        return ""
    return f"中心 ({cx:.1f}, {cy:.1f}, {cz:.1f}) · 尺寸 {sx:.1f} × {sy:.1f} × {sz:.1f} Å"


def _prep_ph(workspace: Workspace) -> str:
    """准备时实际使用的 pH；旧项目可能没有记录。"""
    values: set[float] = set()
    for entry in list(workspace.targets) + list(workspace.ligands):
        details = entry.meta.get("prep_details")
        if not isinstance(details, dict) or details.get("ph") is None:
            continue
        try:
            values.add(round(float(details["ph"]), 2))
        except (TypeError, ValueError):
            continue
    return "、".join(f"{value:g}" for value in sorted(values))


def _entry_prep_warnings(entry: Any) -> list[str]:
    raw = entry.meta.get("prep_warnings") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _prep_warnings(workspace: Workspace) -> list[str]:
    """把准备阶段产生的科学提示汇总到报告，而不是只留在 project.json 里。"""
    grouped: dict[str, list[str]] = {}
    for entry in list(workspace.targets) + list(workspace.ligands):
        for text in _entry_prep_warnings(entry):
            names = grouped.setdefault(text, [])
            if entry.display_name not in names:
                names.append(entry.display_name)
    messages: list[str] = []
    for text, names in grouped.items():
        if len(names) > 3:
            label = "、".join(names[:3]) + f" 等 {len(names)} 个条目"
        else:
            label = "、".join(names)
        messages.append(f"{label}（准备）：{text}")
    return messages


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
    interaction_index=None,
    heavy_atoms: dict[str, int] | None = None,
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
        box_cell = _e(BOX_LABELS.get(item.box_source, item.box_source or "自动"))
        box_detail = _box_detail(item)
        if box_detail:
            box_cell += f'<br><span class="meta">{_e(box_detail)}</span>'
        le_cell = ""
        if heavy_atoms is not None:
            heavy = heavy_atoms.get(item.ligand) if item.ok and item.poses else None
            if heavy:
                le_cell = (
                    f'<td class="num">{-item.poses[0].affinity / heavy:.2f}</td>'
                )
            else:
                le_cell = '<td class="num">—</td>'
        interaction_cell = ""
        if interaction_index is not None:
            entry = interaction_index.get(item.target, item.ligand)
            if entry is None:
                interaction_cell = "<td>—</td>"
            elif not entry.ok:
                interaction_cell = f'<td class="meta">{_e(entry.error or "分析失败")}</td>'
            else:
                detail = entry.residue_text
                engine = "PLIP" if entry.engine == "plip" else "几何判定"
                interaction_cell = (
                    f"<td>{_e(entry.summary)}"
                    f'<br><span class="meta">{_e(engine)}'
                    + (f" · {_e(detail)}" if detail else "")
                    + "</span></td>"
                )
        rows.append(
            "<tr>"
            f"<td>{_e(target_labels.get(item.target, item.target))}</td>"
            f"<td>{_e(ligand_labels.get(item.ligand, item.ligand))}</td>"
            f"<td>{_e(item.mode_label or '—')}</td>"
            f'<td class="num">{_e(score)}</td>'
            f"{le_cell}"
            f"<td>{box_cell}</td>"
            f'<td class="num">{_e(runtime)}</td>'
            f"<td>{status}</td>"
            f"{interaction_cell}"
            f"<td>{_e(note)}</td>"
            "</tr>"
        )
    interaction_head = "<th>相互作用</th>" if interaction_index is not None else ""
    note = ""
    if heavy_atoms is not None:
        note += (
            '<p class="meta">LE = −ΔG / 重原子数，单位 kcal/mol/重原子；'
            "便于比较不同大小的分子，但不同靶点的 LE 不宜直接对比。</p>"
        )
    if interaction_index is not None:
        note += (
            '<p class="meta">“相互作用”来自 PLIP（不可用时回退几何判定），'
            "只统计当前展示的接触；筛选请在软件结果页进行。"
            "对接输出的极性氢位置未优化，氢键的供体–氢–受体角度与距离只作参考，"
            "不宜据此判定氢键强弱。</p>"
        )
    le_head = "<th class='num'>LE</th>" if heavy_atoms is not None else ""
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>靶点</th><th>候选分子</th><th>对接类型</th><th class='num'>最佳打分</th>"
        f"{le_head}"
        f"<th>盒子来源</th><th class='num'>耗时 (秒)</th><th>状态</th>{interaction_head}<th>说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>{note}"
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
    note = (
        '<p class="meta">RMSD 下限/上限是 Vina 输出的构象相似度估计'
        "（相对打分最低的构象），不是与实验结构或共晶配体的 RMSD。</p>"
    )
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>靶点</th><th>候选分子</th><th class='num'>构象</th>"
        "<th class='num'>打分 (kcal/mol)</th><th class='num'>RMSD 下限 (Å)</th>"
        "<th class='num'>RMSD 上限 (Å)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>{more}{note}"
    )


def _redocking_section(workspace: Workspace) -> str:
    """列出已完成的靶点重对接验证；没有记录时返回空字符串。"""
    from .redocking import load_redocking

    rows: list[str] = []
    for target in workspace.targets:
        result = load_redocking(workspace, target.stem)
        if result is None:
            continue
        name = _e(target.display_name)
        if not result.ok:
            rows.append(
                "<tr>"
                f"<td>{name}</td><td>—</td><td>—</td><td>—</td>"
                f'<td class="badge fail">未完成</td>'
                f"<td>{_e(result.error or '未知错误')}</td>"
                "</tr>"
            )
            continue
        if result.rmsd is None:
            verdict = '<span class="badge skip">无法计算</span>'
            rmsd_text = "—"
        else:
            verdict = (
                '<span class="badge ok">通过</span>'
                if result.passed
                else '<span class="badge fail">未通过</span>'
            )
            rmsd_text = f"{result.rmsd:.2f}"
        parameters = result.parameters or {}
        exhaustiveness = parameters.get("exhaustiveness", "—")
        seed = parameters.get("seed", "—")
        rows.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{_e(result.site_label or '—')}</td>"
            f"<td>{_e(BOX_LABELS.get(result.box_source, result.box_source or '自动'))}</td>"
            f'<td class="num">{_e(rmsd_text)}</td>'
            f"<td>{verdict}</td>"
            f"<td>exhaustiveness={_e(exhaustiveness)}，seed={_e(seed)}"
            f'<br><span class="meta">{_e(result.rmsd_method or "")}</span></td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        "<section>"
        "<h2>重对接验证</h2>"
        '<div class="table-wrap"><table>'
        "<thead><tr><th>靶点</th><th>参考配体</th><th>盒子来源</th>"
        "<th class='num'>RMSD (Å)</th><th>判定</th><th>参数</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        '<p class="meta">RMSD 为对接构象与实验晶体构象之间的对称校正重原子 RMSD'
        "（阈值 2.0 Å），在受体坐标系中计算，不做叠合；"
        "它与 Vina 输出的 pose 间 RMSD 含义不同。</p>"
        "</section>"
    )


def _exhaustiveness_advice_text(
    workspace: Workspace, results: Sequence[DockingResult]
) -> str:
    """按配体复杂度给出 exhaustiveness 建议，并标注实际运行是否偏低。"""
    from .advice import recommend_exhaustiveness

    best = None
    for entry in workspace.ligands:
        details = entry.meta.get("prep_details") or {}
        if not isinstance(details, dict):
            continue
        try:
            rotatable = int(details.get("rotatable_bonds") or 0)
            heavy = int(details.get("heavy_atoms") or 0)
        except (TypeError, ValueError):
            continue
        if heavy <= 0:
            continue
        advice = recommend_exhaustiveness(rotatable, heavy)
        if best is None or advice.recommended > best.recommended:
            best = advice
    if best is None:
        return ""
    text = best.reason
    used = [
        int(item.parameters["exhaustiveness"])
        for item in results
        if item.ok and isinstance(item.parameters, dict)
        and str(item.parameters.get("exhaustiveness", "")).isdigit()
    ]
    if used and min(used) < best.recommended:
        text += (
            f" 本次实际使用 exhaustiveness={min(used)}，低于建议值；"
            "低搜索深度可能产生看似合理但未收敛的构象。"
        )
    return text


def _methods(workspace: Workspace, results: Sequence[DockingResult]) -> str:
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
        ("报告生成环境", _software_line()),
        ("靶点准备", "；".join(target_methods) or "去除水/离子/添加剂，补全缺失残基与原子，按设定 pH 加氢，生成含 Gasteiger 电荷的刚性 PDBQT。"),
        ("候选分子准备", "；".join(ligand_methods) or "保留最大有机片段，补氢并生成三维构象，能量最小化，按设定 pH 校正质子化，输出柔性 PDBQT。"),
    ]
    ph_value = _prep_ph(workspace)
    if ph_value:
        items.append(("质子化 pH", f"{ph_value}（准备时用于加氢或质子化校正）"))
    qc_lines: list[str] = []
    for entry in workspace.targets:
        details = entry.meta.get("prep_details") or {}
        qc = details.get("structure_qc") if isinstance(details, dict) else None
        if not isinstance(qc, dict) or not qc:
            continue
        summary = qc.get("summary")
        text = "；".join(str(item) for item in summary) if isinstance(summary, list) else ""
        if text:
            qc_lines.append(f"{entry.display_name}：{text}")
    if qc_lines:
        items.append(("结构体检", " ".join(qc_lines)))
    advice_text = _exhaustiveness_advice_text(workspace, results)
    items.extend(
        [
            ("对接类型", "；".join(mode_labels) or "根据结构类型自动识别"),
            ("对接程序", _engine_summary(results)),
            ("对接运行参数", _run_parameters_text(results)),
        ]
        + ([("搜索深度建议", advice_text)] if advice_text else [])
        + [
            ("盒子来源", "；".join(box_sources) or "自动"),
            ("评分含义", "对接打分是经验评分函数的输出，单位为 kcal/mol；数值越低表示该程序预测的结合越有利。它不等同于实验测得的 ΔG 或 Kd，也不能单独证明结合模式；分数还会随引擎与版本、评分函数（如 Vinardo）、受体/配体预处理、是否保留水/离子以及盒子范围而变化，因此不宜直接与其他软件或不同引擎（AutoDock Vina / QuickVina 2 / Smina）的分数比较。同一分子的分数也会随盒子大小、exhaustiveness 与随机种子变化，比较时应确认这些条件一致。小分子片段通常只能得到较小的负值。"),
            ("类型适用性", "；".join(mode_notes) or "不同对接类型的分数不宜直接横向比较。"),
            ("可重复性", "在相同软件版本、相同输入与相同参数下，固定 seed 的结果可重复；参数与版本已按每次计算记录在上方。升级软件后可能出现细微差异。"),
        ]
    )
    if unsupported:
        items.append(
            (
                "未支持的组合",
                "；".join(unsupported) + " 未执行对接计算。蛋白–蛋白等大分子对接请使用 ClusPro、HADDOCK 或 RosettaDock 等专门工具。",
            )
        )
    return "".join(f"<dt>{_e(key)}</dt><dd>{_e(value)}</dd>" for key, value in items)


def _trust_section(
    workspace: Workspace,
    results: Sequence[DockingResult],
    interaction_index=None,
) -> str:
    """结果可信度小结：盒子、搜索深度、体检、重对接与相互作用来源。"""
    successes = [item for item in results if item.ok and item.poses]
    if not successes:
        return ""
    from .advice import ligand_efficiency, recommend_exhaustiveness
    from .redocking import load_redocking

    items: list[str] = []
    review = 0
    for item in successes:
        box = workspace.load_box(item.target)
        if box is not None and box.needs_review:
            review += 1
    if review:
        items.append(
            f"{review} 组结果使用自动定位的盒子（盲对接或按分子尺寸估算），"
            "建议在软件中人工确认中心与边长。"
        )
    advice = None
    for entry in workspace.ligands:
        details = entry.meta.get("prep_details") or {}
        if not isinstance(details, dict):
            continue
        try:
            rotatable = int(details.get("rotatable_bonds") or 0)
            heavy = int(details.get("heavy_atoms") or 0)
        except (TypeError, ValueError):
            continue
        if heavy <= 0:
            continue
        candidate = recommend_exhaustiveness(rotatable, heavy)
        if advice is None or candidate.recommended > advice.recommended:
            advice = candidate
    used = [
        int(item.parameters["exhaustiveness"])
        for item in successes
        if isinstance(item.parameters, dict)
        and str(item.parameters.get("exhaustiveness", "")).isdigit()
    ]
    if advice is not None and used and min(used) < advice.recommended:
        items.append(
            f"实际 exhaustiveness={min(used)}，低于按配体复杂度给出的建议值 "
            f"{advice.recommended}；低搜索深度可能产生未收敛的构象。"
        )
    ligand_by_stem = {entry.stem: entry for entry in workspace.ligands}
    best = min(successes, key=lambda item: item.poses[0].affinity)
    entry = ligand_by_stem.get(best.ligand)
    details = (entry.meta.get("prep_details") if entry is not None else None) or {}
    try:
        heavy = int(details.get("heavy_atoms") or 0)
    except (TypeError, ValueError):
        heavy = 0
    efficiency = ligand_efficiency(best.poses[0].affinity, heavy)
    if efficiency is not None:
        name = entry.display_name if entry is not None else best.ligand
        items.append(
            f"最佳配体效率 LE = {efficiency:.2f} kcal/mol/重原子"
            f"（{name}，{heavy} 个重原子）；LE 便于比较不同大小的分子，"
            "但不同靶点之间不宜直接对比。"
        )
    validated = []
    for stem in sorted({item.target for item in successes}):
        result = load_redocking(workspace, stem)
        if result is None:
            continue
        if result.ok and result.rmsd is not None:
            validated.append(
                f"{stem}：{result.rmsd:.2f} Å（{'通过' if result.passed else '未通过'}）"
            )
        else:
            validated.append(f"{stem}：未完成")
    if validated:
        items.append("重对接验证：" + "；".join(validated) + "。")
    else:
        items.append("尚未进行重对接验证；无法确认当前盒子与参数能复现实验构象。")
    vinardo = sum(
        1
        for item in successes
        if isinstance(item.parameters, dict)
        and str(item.parameters.get("scoring", "")) == "vinardo"
    )
    if vinardo:
        items.append(
            f"{vinardo} 组结果使用 Vinardo 评分函数；其分值与 Vina 默认评分不可直接比较，"
            "请勿与其他批次的分数混排。"
        )
    if interaction_index is not None:
        geometric = sum(
            1
            for entry in interaction_index.entries.values()
            if entry.ok and entry.engine != "plip"
        )
        if geometric:
            items.append(
                f"{geometric} 条相互作用由几何判定生成（PLIP 不可用或未识别配体），"
                "类型与距离需谨慎解读。"
            )
    if not items:
        return ""
    body = "".join(f"<li>{_e(text)}</li>" for text in items)
    return (
        "<section><h2>结果可信度</h2>"
        f"<ul>{body}</ul>"
        '<p class="meta">这些因素会直接影响分数的解释；'
        "对接分数本身不等于实验结合亲和力。</p></section>"
    )


def _warnings(bundle: AnalysisBundle, workspace: Workspace) -> str:
    messages: list[str] = []
    messages.extend(bundle.warnings or [])
    messages.extend(_prep_warnings(workspace))
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
        "<li>本程序只运行所选对接引擎的构象搜索与打分，未进行重打分、MM/PBSA 或分子动力学验证。</li>"
        "<li>结合位点由自动检测或用户指定，不等同于实验证实的结合位点；盒子范围会直接影响打分。</li>"
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
    interaction_index=None,
) -> Path:
    """汇总结果、生成图表并写出单个 HTML 文件。

    ``settings`` 保留在签名中以兼容调用方；报告中的参数取自结果自身记录，
    不使用报告生成时的当前设置，避免用户改过设置后报告与实际计算不符。
    ``interaction_index`` 为空时尝试读取已有的批量相互作用缓存（不重新分析）。
    """
    results = list(results) if results is not None else collect_existing_results(workspace)
    if interaction_index is None:
        try:
            from .interaction_index import build_interaction_index

            cached = build_interaction_index(workspace, results, only_cached=True)
            interaction_index = cached if cached.entries else None
        except Exception as exc:
            log.debug("读取相互作用缓存失败：%s", exc)
            interaction_index = None
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
    heavy_atoms: dict[str, int] = {}
    for entry in workspace.ligands:
        details = entry.meta.get("prep_details") or {}
        if not isinstance(details, dict):
            continue
        try:
            heavy = int(details.get("heavy_atoms") or 0)
        except (TypeError, ValueError):
            continue
        if heavy > 0:
            heavy_atoms[entry.stem] = heavy
    payload = {
        "title": _e(title),
        "project": _e(project_name),
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "tiles": _tiles(bundle),
        "charts": _charts(bundle),
        "results": _results_table(
            bundle.results,
            target_labels,
            ligand_labels,
            interaction_index,
            heavy_atoms,
        ),
        "poses": _pose_table(bundle.results, target_labels, ligand_labels),
        "redocking": _redocking_section(workspace),
        "methods": _methods(workspace, bundle.results),
        "trust": _trust_section(workspace, bundle.results, interaction_index),
        "warnings": _warnings(bundle, workspace),
        "version": _e(__version__),
        "app_name": _e(__app_name__),
    }
    document = Template(template_text).safe_substitute(payload)
    output.write_text(document, encoding="utf-8")
    log.info("报告已生成：%s", output)
    return output
