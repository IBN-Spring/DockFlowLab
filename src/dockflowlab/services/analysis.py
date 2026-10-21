"""结果汇总、热图与统计图表。

图表遵循统一的可视化规范：
* 热图使用单一蓝色顺序色阶（越深 = 结合越强），缺失值为灰色；
* 柱状图只使用一种主色，直接标注数值，不依赖颜色区分对象；
* 所有文字使用系统中文字体，保证 Windows / macOS 均可正常显示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")  # 必须在 pyplot 之前设置，保证 GUI 中也能安全生成图片
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.font_manager import FontProperties, fontManager  # noqa: E402

from ..core.models import DockingResult, ProjectFile  # noqa: E402
from ..logging_setup import get_logger  # noqa: E402
from ..workspace import Workspace  # noqa: E402
from .docking import collect_existing_results  # noqa: E402

log = get_logger("analysis")

# 可视化配色（来自经过验证的参考色板）
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_BLUE = "#2a78d6"
MISSING_CELL = "#e8e7e3"

BLUE_STEPS = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]

# 热图配色都是单一色相由浅到深：越深 = 打分越低（结合越有利）。
# 不使用彩虹或红蓝色阶，避免读者误以为颜色代表正负两类数值。
HEATMAP_SCHEMES: dict[str, tuple[str, list[str]]] = {
    "blue": ("蓝色（默认）", BLUE_STEPS),
    "green": (
        "绿色",
        ["#e3f4e3", "#c8e9c9", "#a8dbaa", "#84c98a",
         "#5fb56d", "#3f9e55", "#2a8144", "#1d6536", "#14532d"],
    ),
    "teal": (
        "青色",
        ["#dcf3f2", "#b9e6e5", "#92d7d6", "#67c5c4",
         "#3fb0b0", "#249798", "#187d7e", "#106365", "#0f4c4c"],
    ),
    "orange": (
        "橙色",
        ["#fde9d9", "#f9d2b5", "#f4b78c", "#ed9a61",
         "#e37d3c", "#cf6222", "#b14b16", "#8f3812", "#7c2d12"],
    ),
    "purple": (
        "紫色",
        ["#ede4fb", "#dccbf6", "#c7adef", "#b08ce6",
         "#986bdb", "#804dcc", "#6936b5", "#54279a", "#3b0764"],
    ),
    "grey": (
        "灰度",
        ["#f0f0ef", "#ddddda", "#c6c6c2", "#adada8",
         "#93938e", "#787874", "#5e5e5a", "#444441", "#27272a"],
    ),
}
DEFAULT_SCHEME = "blue"
DEFAULT_HEATMAP_TITLE = "结合能热图"
HEATMAP_FORMATS: dict[str, tuple[str, tuple[str, ...]]] = {
    "png": ("PNG · 报告与预览", (".png",)),
    "png+svg": ("PNG + SVG · 矢量可编辑", (".png", ".svg")),
    "png+pdf": ("PNG + PDF · 打印", (".png", ".pdf")),
}
DEFAULT_HEATMAP_FORMAT = "png"
HEATMAP_CONFIG_NAME = "heatmap_config.json"


def heatmap_cmap(scheme: str) -> LinearSegmentedColormap:
    """按名称取顺序色阶；未知名称回退到默认蓝色。"""
    _, steps = HEATMAP_SCHEMES.get(scheme, HEATMAP_SCHEMES[DEFAULT_SCHEME])
    cmap = LinearSegmentedColormap.from_list(f"dockflow_{scheme}", steps[::-1])
    cmap.set_bad(MISSING_CELL)
    return cmap

_CJK_FONT_CANDIDATES = [
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
]


@dataclass
class AnalysisBundle:
    results: list[DockingResult]
    dataframe: pd.DataFrame
    matrix: pd.DataFrame
    summary: dict[str, Any]
    csv_path: Path
    matrix_path: Path
    summary_path: Path
    heatmap_path: Path | None = None
    bar_path: Path | None = None
    warnings: list[str] | None = None


@dataclass
class HeatmapConfig:
    """热图的自定义文字、配色与导出格式，随项目保存。"""

    target_labels: dict[str, str] = field(default_factory=dict)
    ligand_labels: dict[str, str] = field(default_factory=dict)
    scheme: str = DEFAULT_SCHEME
    title: str = DEFAULT_HEATMAP_TITLE
    subtitle: str = ""
    format: str = DEFAULT_HEATMAP_FORMAT


def heatmap_config_path(workspace: Workspace) -> Path:
    return workspace.analysis_dir / HEATMAP_CONFIG_NAME


def load_heatmap_config(workspace: Workspace) -> HeatmapConfig:
    """读取项目热图设置；文件缺失或损坏时返回默认值。"""
    path = heatmap_config_path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return HeatmapConfig()
    if not isinstance(data, dict):
        return HeatmapConfig()

    def _labels(key: str) -> dict[str, str]:
        value = data.get(key)
        if not isinstance(value, dict):
            return {}
        return {
            str(stem): str(label).strip()
            for stem, label in value.items()
            if str(stem).strip() and str(label).strip()
        }

    scheme = str(data.get("scheme") or DEFAULT_SCHEME)
    if scheme not in HEATMAP_SCHEMES:
        scheme = DEFAULT_SCHEME
    fmt = str(data.get("format") or DEFAULT_HEATMAP_FORMAT)
    if fmt not in HEATMAP_FORMATS:
        fmt = DEFAULT_HEATMAP_FORMAT
    title = str(data.get("title") or DEFAULT_HEATMAP_TITLE).strip()
    return HeatmapConfig(
        target_labels=_labels("target_labels"),
        ligand_labels=_labels("ligand_labels"),
        scheme=scheme,
        title=title or DEFAULT_HEATMAP_TITLE,
        subtitle=str(data.get("subtitle") or "").strip(),
        format=fmt,
    )


def save_heatmap_config(workspace: Workspace, config: HeatmapConfig) -> Path:
    path = heatmap_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_labels": config.target_labels,
        "ligand_labels": config.ligand_labels,
        "scheme": config.scheme,
        "title": config.title,
        "subtitle": config.subtitle,
        "format": config.format,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def heatmap_output_paths(workspace: Workspace, fmt: str) -> list[Path]:
    suffixes = HEATMAP_FORMATS.get(fmt, HEATMAP_FORMATS[DEFAULT_HEATMAP_FORMAT])[1]
    return [workspace.analysis_dir / f"heatmap{suffix}" for suffix in suffixes]


# ----------------------------------------------------------------------
# 字体与格式
# ----------------------------------------------------------------------
def configure_chinese_font() -> str:
    """选择可用的中文字体并设为 matplotlib 默认字体。"""
    available = {font.name for font in fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["axes.unicode_minus"] = False
            return name
    # 尝试直接查找字体文件
    for name in _CJK_FONT_CANDIDATES:
        try:
            path = fontManager.findfont(FontProperties(family=name), fallback_to_default=False)
            if path and Path(path).is_file():
                plt.rcParams["font.family"] = "sans-serif"
                plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
                plt.rcParams["axes.unicode_minus"] = False
                return name
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    return ""


def unique_labels(entries: Iterable[ProjectFile]) -> dict[str, str]:
    """把内部代号映射为界面显示名；重名时附带代号以便区分。"""
    entries = list(entries)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.display_name] = counts.get(entry.display_name, 0) + 1
    labels: dict[str, str] = {}
    for entry in entries:
        if counts.get(entry.display_name, 0) > 1:
            labels[entry.stem] = f"{entry.display_name} ({entry.stem})"
        else:
            labels[entry.stem] = entry.display_name
    return labels


def _status_label(result: DockingResult) -> str:
    if result.ok:
        return "成功（已有结果）" if result.skipped else "成功"
    if not result.mode_supported:
        return "不支持"
    return "失败"


def build_binding_matrix(results: Sequence[DockingResult]) -> pd.DataFrame:
    """成功结果的最佳打分矩阵，行列仍使用内部代号。"""
    successes = [result for result in results if result.ok and result.poses]
    best_rows = [
        {
            "target": result.target,
            "ligand": result.ligand,
            "affinity": result.poses[0].affinity,
        }
        for result in successes
    ]
    best_df = pd.DataFrame(best_rows)
    if best_df.empty:
        return pd.DataFrame()
    pivot = best_df.pivot_table(
        index="target", columns="ligand", values="affinity", aggfunc="min"
    )
    # 按最佳结合能排序，读者可以立即看到最强/最弱的靶点与配体
    return pivot.loc[
        pivot.min(axis=1).sort_values().index,
        pivot.min(axis=0).sort_values().index,
    ]


def heatmap_data(
    workspace: Workspace,
    results: Sequence[DockingResult] | None = None,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, str]]:
    """供热图编辑器使用：原始矩阵、默认靶点名、默认候选分子名。"""
    results = list(results) if results is not None else collect_existing_results(workspace)
    return (
        build_binding_matrix(results),
        unique_labels(workspace.targets),
        unique_labels(workspace.ligands),
    )


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def analyze(
    workspace: Workspace,
    results: Sequence[DockingResult] | None = None,
    *,
    make_heatmap: bool = True,
    make_bar: bool = True,
) -> AnalysisBundle:
    results = list(results) if results is not None else collect_existing_results(workspace)
    warnings: list[str] = []
    font = configure_chinese_font()
    if not font:
        warnings.append("未找到中文字体，图表中的中文可能显示为方块")

    target_labels = unique_labels(workspace.targets)
    ligand_labels = unique_labels(workspace.ligands)

    rows: list[dict[str, Any]] = []
    for result in results:
        target_label = target_labels.get(result.target, result.target)
        ligand_label = ligand_labels.get(result.ligand, result.ligand)
        base = {
            "靶点": target_label,
            "靶点代号": result.target,
            "配体": ligand_label,
            "配体代号": result.ligand,
            "对接类型": result.mode_label or "—",
            "盒子来源": result.box_source or "—",
            "耗时（秒）": round(result.runtime_seconds, 1) if result.runtime_seconds else None,
        }
        if result.ok and result.poses:
            for index, pose in enumerate(result.poses, start=1):
                rows.append(
                    {
                        **base,
                        "排名": index,
                        "结合能 (kcal/mol)": round(pose.affinity, 3),
                        "RMSD 下限 (Å)": round(pose.rmsd_lb, 3),
                        "RMSD 上限 (Å)": round(pose.rmsd_ub, 3),
                        "状态": _status_label(result),
                        "说明": "；".join(result.warnings) if result.warnings else "",
                    }
                )
        else:
            rows.append(
                {
                    **base,
                    "排名": None,
                    "结合能 (kcal/mol)": None,
                    "RMSD 下限 (Å)": None,
                    "RMSD 上限 (Å)": None,
                    "状态": _status_label(result),
                    "说明": result.error or "；".join(result.warnings),
                }
            )

    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        dataframe = pd.DataFrame(
            columns=[
                "靶点", "靶点代号", "配体", "配体代号", "对接类型", "盒子来源",
                "耗时（秒）", "排名", "结合能 (kcal/mol)", "RMSD 下限 (Å)",
                "RMSD 上限 (Å)", "状态", "说明",
            ]
        )

    # 仅成功结果的最佳结合能矩阵
    successes = [result for result in results if result.ok and result.poses]
    config = load_heatmap_config(workspace)
    raw_matrix = build_binding_matrix(results)
    if raw_matrix.empty:
        matrix = pd.DataFrame()
    else:
        matrix = raw_matrix.rename(
            index={
                stem: config.target_labels.get(stem)
                or target_labels.get(stem, stem)
                for stem in raw_matrix.index
            },
            columns={
                stem: config.ligand_labels.get(stem)
                or ligand_labels.get(stem, stem)
                for stem in raw_matrix.columns
            },
        )

    workspace.analysis_dir.mkdir(parents=True, exist_ok=True)
    csv_path = workspace.analysis_dir / "binding_energies.csv"
    matrix_path = workspace.analysis_dir / "binding_matrix.csv"
    summary_path = workspace.analysis_dir / "summary.json"
    heatmap_path = workspace.analysis_dir / "heatmap.png"
    bar_path = workspace.analysis_dir / "top_affinities.png"

    dataframe.to_csv(csv_path, index=False, encoding="utf-8-sig")
    if not matrix.empty:
        matrix.to_csv(matrix_path, encoding="utf-8-sig")

    summary = _build_summary(workspace, results, target_labels, ligand_labels)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    mode_labels = sorted(
        {result.mode_label for result in results if result.ok and result.mode_label}
    )
    if len(mode_labels) > 1:
        warnings.append(
            "结果包含多种对接类型（" + "、".join(mode_labels) + "）。"
            "对接分数适合在同一对接类型内部排序，不宜跨类型直接比较。"
        )
    parameter_signatures = {
        (
            result.parameters.get("exhaustiveness"),
            result.parameters.get("num_modes"),
            result.parameters.get("energy_range"),
            result.parameters.get("seed"),
            result.parameters.get("cpu"),
            result.parameters.get("engine")
            or ("vina" if result.parameters.get("vina_version") else None),
            result.parameters.get("engine_version") or result.parameters.get("vina_version"),
        )
        for result in successes
        if (result.parameters or {}).get("exhaustiveness") is not None
    }
    if len(parameter_signatures) > 1:
        warnings.append(
            "不同结果使用了不同的对接参数（搜索强度、随机种子、对接引擎或版本不同），"
            "分数差异可能来自参数设置而非分子本身。"
        )
    elif parameter_signatures and any(
        (result.parameters or {}).get("exhaustiveness") is None for result in successes
    ):
        warnings.append("部分结果没有记录对接参数，无法确认它们是否使用相同设置。")
    if any(not result.mode_supported for result in results):
        warnings.append("部分组合属于不支持的对接类型，未执行计算；详见结果表中的“不支持”状态。")

    actual_heatmap: Path | None = None
    if make_heatmap and not matrix.empty and matrix.size >= 2:
        try:
            output_paths = heatmap_output_paths(workspace, config.format)
            selected = {path.suffix for path in output_paths}
            for suffix in (".svg", ".pdf"):
                if suffix not in selected:
                    (workspace.analysis_dir / f"heatmap{suffix}").unlink(missing_ok=True)
            plot_heatmap(
                matrix,
                output_paths,
                scheme=config.scheme,
                title=config.title,
                subtitle=config.subtitle,
            )
            actual_heatmap = heatmap_path
        except Exception as exc:
            log.warning("生成热图失败：%s", exc, exc_info=True)
            warnings.append(f"生成热图失败：{exc}")

    actual_bar: Path | None = None
    if make_bar and len(successes) >= 2:
        try:
            _plot_top_affinities(successes, target_labels, ligand_labels, bar_path)
            actual_bar = bar_path
        except Exception as exc:
            log.warning("生成柱状图失败：%s", exc, exc_info=True)
            warnings.append(f"生成柱状图失败：{exc}")

    return AnalysisBundle(
        results=results,
        dataframe=dataframe,
        matrix=matrix,
        summary=summary,
        csv_path=csv_path,
        matrix_path=matrix_path,
        summary_path=summary_path,
        heatmap_path=actual_heatmap,
        bar_path=actual_bar,
        warnings=warnings,
    )


def _build_summary(
    workspace: Workspace,
    results: Sequence[DockingResult],
    target_labels: dict[str, str],
    ligand_labels: dict[str, str],
) -> dict[str, Any]:
    successes = [result for result in results if result.ok and result.poses]
    failures = [result for result in results if not result.ok]
    best = min(successes, key=lambda item: item.poses[0].affinity) if successes else None
    per_target: dict[str, Any] = {}
    for result in successes:
        label = target_labels.get(result.target, result.target)
        current = per_target.get(label)
        if current is None or result.poses[0].affinity < current["affinity"]:
            per_target[label] = {
                "ligand": ligand_labels.get(result.ligand, result.ligand),
                "affinity": round(result.poses[0].affinity, 3),
            }
    per_ligand: dict[str, Any] = {}
    for result in successes:
        label = ligand_labels.get(result.ligand, result.ligand)
        current = per_ligand.get(label)
        if current is None or result.poses[0].affinity < current["affinity"]:
            per_ligand[label] = {
                "target": target_labels.get(result.target, result.target),
                "affinity": round(result.poses[0].affinity, 3),
            }
    modes: dict[str, Any] = {}
    for result in results:
        label = result.mode_label or "未识别"
        current = modes.setdefault(label, {"total": 0, "success": 0, "failed": 0})
        current["total"] += 1
        if result.ok and result.poses:
            current["success"] += 1
        else:
            current["failed"] += 1
    unsupported_modes = [
        {
            "target": target_labels.get(result.target, result.target),
            "ligand": ligand_labels.get(result.ligand, result.ligand),
            "mode": result.mode_label,
            "reason": result.error,
        }
        for result in results
        if not result.mode_supported
    ]
    return {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": workspace.name,
        "workspace": str(workspace.root),
        "counts": {
            "total": len(results),
            "success": len(successes),
            "failed": len(failures),
        },
        "best": None
        if best is None
        else {
            "target": target_labels.get(best.target, best.target),
            "ligand": ligand_labels.get(best.ligand, best.ligand),
            "affinity": round(best.poses[0].affinity, 3),
        },
        "per_target": dict(sorted(per_target.items(), key=lambda item: item[1]["affinity"])),
        "per_ligand": dict(sorted(per_ligand.items(), key=lambda item: item[1]["affinity"])),
        "modes": dict(sorted(modes.items(), key=lambda item: (-item[1]["total"], item[0]))),
        "unsupported_modes": unsupported_modes,
        "failures": [
            {
                "target": target_labels.get(result.target, result.target),
                "ligand": ligand_labels.get(result.ligand, result.ligand),
                "error": result.error,
            }
            for result in failures
        ],
    }


# ----------------------------------------------------------------------
# 图表
# ----------------------------------------------------------------------
def _style_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK_SECONDARY, labelsize=9, length=0)


def _needs_light_text(rgba) -> bool:
    """按 WCAG 相对亮度选择单元格文字颜色，保证可读性。"""
    red, green, blue = (float(channel) for channel in rgba[:3])

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    contrast_white = 1.05 / (luminance + 0.05)
    contrast_black = (luminance + 0.05) / 0.05
    return contrast_white > contrast_black


def plot_heatmap(
    matrix: pd.DataFrame,
    output_paths: Sequence[Path],
    *,
    scheme: str = DEFAULT_SCHEME,
    title: str = DEFAULT_HEATMAP_TITLE,
    subtitle: str = "",
) -> None:
    """按指定文字、配色与格式绘制热图；同一张图可保存多个格式。"""
    paths = [Path(path) for path in output_paths]
    if not paths:
        raise ValueError("没有指定热图输出路径")
    rows, columns = matrix.shape
    width = min(16.0, max(6.0, 1.15 * columns + 3.4))
    height = min(12.0, max(3.8, 0.62 * rows + 2.2))
    fig, ax = plt.subplots(figsize=(width, height), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    cmap = heatmap_cmap(scheme)

    values = matrix.to_numpy(dtype=float)
    mask = pd.isna(values)
    finite = values[~mask]
    if finite.size == 0:
        plt.close(fig)
        raise ValueError("没有可用的结合能数据")
    vmin = float(finite.min())
    vmax = float(finite.max())
    if abs(vmax - vmin) < 1e-9:
        vmax = vmin + 1.0

    image = ax.imshow(
        values,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        aspect="auto",
        interpolation="nearest",
    )
    # 单元格之间留出表面色间隙（用显式线条，避免坐标轴网格被图像遮挡）
    if columns > 1:
        ax.vlines(
            [x + 0.5 for x in range(columns - 1)],
            -0.5,
            rows - 0.5,
            colors=SURFACE,
            linewidth=1.0,
            zorder=5,
        )
    if rows > 1:
        ax.hlines(
            [y + 0.5 for y in range(rows - 1)],
            -0.5,
            columns - 0.5,
            colors=SURFACE,
            linewidth=1.0,
            zorder=5,
        )
    ax.set_xlim(-0.5, columns - 0.5)
    ax.set_ylim(rows - 0.5, -0.5)
    ax.set_xticks(range(columns))
    ax.set_yticks(range(rows))
    ax.set_xticklabels(matrix.columns, rotation=35 if columns > 5 else 0, ha="right" if columns > 5 else "center")
    ax.set_yticklabels(matrix.index)
    ax.tick_params(axis="x", pad=6)
    ax.tick_params(axis="y", pad=6)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for row in range(rows):
        for column in range(columns):
            value = values[row, column]
            if mask[row, column]:
                ax.text(column, row, "—", ha="center", va="center", color=INK_SECONDARY, fontsize=10)
                continue
            normalized = (value - vmin) / (vmax - vmin) if vmax > vmin else 0.0
            color = "#ffffff" if _needs_light_text(cmap(normalized)) else INK
            ax.text(column, row, f"{value:.1f}", ha="center", va="center", color=color, fontsize=10, fontweight="semibold")

    available = int(matrix.notna().sum().sum())
    total_cells = rows * columns
    title = (title or DEFAULT_HEATMAP_TITLE).strip() or DEFAULT_HEATMAP_TITLE
    auto_subtitle = (
        f"数值越低表示结合越强 · 单位 kcal/mol · "
        f"{available}/{total_cells} 个组合有结果 · 行列按最佳结合能排序"
    )
    caption = subtitle.strip() or auto_subtitle
    fig.text(0.012, 0.965, title, fontsize=14, fontweight="bold", color=INK, ha="left")
    fig.text(0.012, 0.915, caption, fontsize=9.5, color=INK_SECONDARY, ha="left")

    colorbar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02)
    colorbar.set_label("结合能 (kcal/mol)", color=INK_SECONDARY, fontsize=9)
    colorbar.outline.set_visible(False)
    colorbar.ax.tick_params(colors=INK_SECONDARY, labelsize=8, length=0)

    fig.subplots_adjust(left=0.16, right=0.93, top=0.84, bottom=0.2)
    # SVG 保留真实文字而不是轮廓路径，便于在 Illustrator / PPT 中继续改字。
    with matplotlib.rc_context({"svg.fonttype": "none"}):
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def _plot_top_affinities(
    successes: Sequence[DockingResult],
    target_labels: dict[str, str],
    ligand_labels: dict[str, str],
    output_path: Path,
    top_n: int = 15,
) -> None:
    ranked = sorted(successes, key=lambda item: item.poses[0].affinity)[:top_n]
    labels = [
        f"{target_labels.get(item.target, item.target)} · {ligand_labels.get(item.ligand, item.ligand)}"
        for item in ranked
    ]
    labels = [label if len(label) <= 34 else label[:31] + "…" for label in labels]
    values = [item.poses[0].affinity for item in ranked]

    height = max(3.0, 0.42 * len(ranked) + 1.9)
    fig, ax = plt.subplots(figsize=(9.2, height), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)

    y_positions = list(range(len(ranked)))
    ax.barh(y_positions, values, height=0.4, color=SERIES_BLUE, edgecolor="none", zorder=3)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.axvline(0, color=BASELINE, linewidth=0.8, zorder=2)
    ax.yaxis.grid(False)
    ax.xaxis.grid(False)
    ax.spines["left"].set_visible(False)
    ax.set_axisbelow(True)

    span = max(abs(min(values)), 1e-6)
    offset = span * 0.025
    for y, value in zip(y_positions, values):
        ax.text(
            value - offset,
            y,
            f"{value:.1f}",
            va="center",
            ha="right",
            fontsize=8.5,
            color=INK_SECONDARY,
        )
    ax.set_xlim(min(values) - span * 0.22, 0)
    ax.set_xlabel("结合能 (kcal/mol) · 越靠左越强", color=INK_SECONDARY, fontsize=9)
    ax.tick_params(axis="x", colors=INK_MUTED, labelsize=8)

    if len(successes) > len(ranked):
        count_text = f"显示最强的 {len(ranked)} 个，共 {len(successes)} 个有结果的复合物"
    else:
        count_text = f"共 {len(successes)} 个有结果的复合物"
    fig.text(0.012, 0.975, "结合能最低的复合物", fontsize=13.5, fontweight="bold", color=INK, ha="left")
    fig.text(
        0.012,
        0.928,
        f"按最佳构象排序 · {count_text} · 单位 kcal/mol",
        fontsize=9,
        color=INK_SECONDARY,
        ha="left",
    )
    fig.subplots_adjust(left=0.34, right=0.97, top=0.9, bottom=0.16)
    fig.savefig(output_path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
