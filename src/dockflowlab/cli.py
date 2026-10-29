"""命令行入口。

命令行与图形界面共用同一套服务层，适合批量任务、服务器环境和自动化测试：

    dockflowlab-cli doctor
    dockflowlab-cli init  D:/projects/demo
    dockflowlab-cli info  D:/projects/demo
    dockflowlab-cli prepare D:/projects/demo
    dockflowlab-cli dock D:/projects/demo --exhaustiveness 8
    dockflowlab-cli report D:/projects/demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .config import AppSettings, user_config_dir
from .core.models import KIND_LIGAND, KIND_TARGET, Box, ProjectFile
from .logging_setup import setup_logging
from .services import modes
from .version import __app_name__, __version__, component_version
from .workspace import Workspace

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


# ----------------------------------------------------------------------
# 参数
# ----------------------------------------------------------------------
def online_offline(value: str) -> str:
    value = value.lower()
    aliases = {"pdb": "pdb", "rcsb": "pdb", "pubchem": "pubchem", "alphafold": "alphafold", "chembl": "chembl"}
    if value not in aliases:
        raise argparse.ArgumentTypeError("来源应为 pdb、pubchem、alphafold 或 chembl")
    return aliases[value]


def _kind(value: str) -> str:
    value = value.lower()
    if value in {"target", "receptor", "protein", "靶点", "蛋白"}:
        return KIND_TARGET
    if value in {"ligand", "molecule", "配体", "分子"}:
        return KIND_LIGAND
    raise argparse.ArgumentTypeError("类型应为 target（靶点）或 ligand（候选分子）")


def _triplet(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.replace("，", ",").split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("需要三个用逗号分隔的数字，例如 12.5,-3.2,8.4")
    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无法解析数值：{value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dockflowlab-cli",
        description=f"{__app_name__} {__version__} · 分子对接命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  dockflowlab-cli doctor\n"
            "  dockflowlab-cli init  D:/projects/demo\n"
            "  dockflowlab-cli info  D:/projects/demo\n"
            "  dockflowlab-cli prepare D:/projects/demo\n"
            "  dockflowlab-cli dock D:/projects/demo --exhaustiveness 8\n"
            "  dockflowlab-cli report D:/projects/demo\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    doctor = sub.add_parser("doctor", help="检查运行环境与外部程序")
    doctor.add_argument("--json", action="store_true", help="以 JSON 输出")
    doctor.add_argument("--strict", action="store_true", help="缺少可选组件也返回失败")

    init = sub.add_parser("init", help="创建新的工作目录")
    init.add_argument("project", type=Path, help="工作目录")
    init.add_argument("--name", default="", help="项目名称（默认使用文件夹名）")

    info = sub.add_parser("info", help="查看项目概况")
    info.add_argument("project", type=Path, help="工作目录")
    info.add_argument("--json", action="store_true", help="以 JSON 输出")

    prepare = sub.add_parser("prepare", help="准备靶点与候选分子")
    prepare.add_argument("project", type=Path, help="工作目录")
    prepare.add_argument("--force", action="store_true", help="重新准备已有文件")
    prepare.add_argument(
        "--only", choices=("all", "targets", "ligands"), default="all", help="只准备某一类条目"
    )
    prepare.add_argument("--ph", type=float, default=None, help="加氢时的 pH（默认读取设置）")
    prepare.add_argument(
        "--states",
        type=int,
        choices=(1, 2, 4, 8),
        default=None,
        help="候选分子的互变异构/质子化状态数，1 表示只用原始结构（默认读取设置）",
    )

    dock = sub.add_parser("dock", help="运行对接并汇总结果")
    dock.add_argument("project", type=Path, help="工作目录")
    dock.add_argument("--target", action="append", metavar="名称", help="只对接指定靶点（可重复）")
    dock.add_argument("--ligand", action="append", metavar="名称", help="只对接指定候选分子（可重复）")
    dock.add_argument("--exhaustiveness", type=int, default=None, help="搜索强度，默认读取设置")
    dock.add_argument("--num-modes", type=int, default=None, help="每个配体保留的构象数")
    dock.add_argument("--energy-range", type=float, default=None, help="构象能量范围 kcal/mol")
    dock.add_argument("--seed", type=int, default=None, help="随机种子")
    dock.add_argument("--cpu", type=int, default=None, help="CPU 核心数，0 表示全部")
    dock.add_argument(
        "--engine",
        choices=("vina", "qvina2", "smina"),
        default=None,
        help="对接引擎（默认读取设置）：vina、qvina2 或 smina",
    )
    dock.add_argument(
        "--scoring",
        choices=("default", "vinardo"),
        default=None,
        help="Smina 评分函数：default（Vina 评分）或 vinardo；仅对 Smina 生效",
    )
    dock.add_argument(
        "--no-fallback",
        action="store_true",
        help="首选引擎失败时不自动改用其他可用引擎",
    )
    dock.add_argument("--force", action="store_true", help="已有结果也重新对接")
    dock.add_argument("--center", type=_triplet, default=None, metavar="X,Y,Z", help="手动盒子中心（Å）")
    dock.add_argument("--size", type=_triplet, default=None, metavar="X,Y,Z", help="手动盒子边长（Å）")
    dock.add_argument("--no-report", action="store_true", help="对接后不生成报告")

    report = sub.add_parser("report", help="根据已有结果生成 HTML 报告")
    report.add_argument("project", type=Path, help="工作目录")
    report.add_argument("--output", type=Path, default=None, help="输出文件路径")

    fetch = sub.add_parser("fetch", help="从公共数据库下载结构")
    fetch.add_argument("project", type=Path, help="工作目录")
    fetch.add_argument("source", type=online_offline, help="pdb / pubchem / alphafold / chembl")
    fetch.add_argument("query", help="PDB ID、化合物名称、CID、SMILES 或 UniProt 登录号")
    fetch.add_argument("--kind", type=_kind, default=KIND_TARGET, help="target（默认）或 ligand")

    imp = sub.add_parser("import", help="把结构文件导入项目")
    imp.add_argument("project", type=Path, help="工作目录")
    imp.add_argument("files", nargs="+", type=Path, help="结构文件")
    imp.add_argument("--kind", type=_kind, required=True, help="target 或 ligand")
    imp.add_argument("--name", action="append", default=[], help="显示名称（可重复，按文件顺序）")

    return parser


# ----------------------------------------------------------------------
# 公共
# ----------------------------------------------------------------------
def _open_workspace(path: Path) -> Workspace:
    workspace = Workspace.open(path)
    workspace.mark_missing_sources()
    setup_logging(workspace.logs_dir)
    return workspace


def _version_of(distribution: str) -> str:
    return component_version(distribution)


def _check_pocket_backend() -> dict[str, object]:
    """验证 NumPy/SciPy 编译扩展能真正加载（打包后常因缺少 DLL 失败）。"""
    try:
        import numpy as np
        from scipy import ndimage
        from scipy.spatial import cKDTree

        coords = np.zeros((2, 3), dtype=float)
        cKDTree(coords).query(coords)
        ndimage.distance_transform_edt(np.ones((2, 2, 2), dtype=bool))
        return {"ok": True, "error": ""}
    except Exception as exc:  # pragma: no cover - 依赖缺失时给出诊断
        return {"ok": False, "error": str(exc)}


def _select(entries: Iterable[ProjectFile], names: Sequence[str] | None, label: str) -> list[ProjectFile]:
    entries = list(entries)
    if not names:
        return entries
    selected: list[ProjectFile] = []
    missing: list[str] = []
    for name in names:
        matches = [
            entry
            for entry in entries
            if entry.stem == name or entry.display_name == name or entry.display_name.casefold() == name.casefold()
        ]
        if not matches:
            missing.append(name)
        for entry in matches:
            if entry not in selected:
                selected.append(entry)
    if missing:
        available = "、".join(entry.stem for entry in entries) or "无"
        raise ValueError(f"找不到{label}：{'、'.join(missing)}。可用条目：{available}")
    return selected


def _print_progress(current: int, total: int, message: str) -> None:
    prefix = f"[{current}/{total}] " if total else ""
    print(prefix + message, flush=True)


# ----------------------------------------------------------------------
# 命令
# ----------------------------------------------------------------------
def command_doctor(args: argparse.Namespace) -> int:
    from .services.pymol_render import find_renderer
    from .tools.obabel import get_openbabel
    from .tools.runtime import ascii_temp_root
    from .tools.vina import ENGINE_ORDER, engine_from_settings

    settings = AppSettings.load()
    pymol = find_renderer(settings.pymol_path)
    engines = [engine_from_settings(settings, key) for key in ENGINE_ORDER]
    obabel = get_openbabel()
    packages = {
        "rdkit": _version_of("rdkit"),
        "meeko": _version_of("meeko"),
        "openmm": _version_of("openmm"),
        "pdbfixer": _version_of("pdbfixer"),
        "PySide6": _version_of("PySide6"),
        "matplotlib": _version_of("matplotlib"),
        "numpy": _version_of("numpy"),
        "scipy": _version_of("scipy"),
    }
    pocket_backend = _check_pocket_backend()
    temp_root = ascii_temp_root()
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_ok = True
    except OSError:
        temp_ok = False
    report = {
        "app": {"name": __app_name__, "version": __version__},
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "executable": sys.executable,
        "engine": settings.docking_engine,
        "engines": {engine.key: engine.doctor() for engine in engines},
        # 保留旧字段，便于已有的脚本继续读取 Vina 的状态。
        "vina": engines[0].doctor(),
        "openbabel": obabel.doctor(),
        "pymol": {"available": bool(pymol), "path": pymol or ""},
        "packages": packages,
        "pocket_backend": pocket_backend,
        "temp_dir": {"path": str(temp_root), "writable": temp_ok},
        "settings": str(AppSettings.path()),
    }
    optional_ok = (
        all(packages.values())
        and bool(obabel.available)
        and bool(pocket_backend["ok"])
    )
    core_ok = any(engine.available for engine in engines) and bool(obabel.available) and temp_ok
    report["ok"] = core_ok and (optional_ok if args.strict else True)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{__app_name__} {__version__}")
        print(f"Python {report['python']} · {sys.platform}")
        print(f"配置目录：{user_config_dir()}")
        print(f"临时目录：{temp_root}（{'可写' if temp_ok else '不可写'}）")
        print()
        print(f"对接引擎（默认 {settings.docking_engine}）：")
        for engine in engines:
            mark = "✓" if engine.available else "✗"
            print(f"  {mark} {engine.label}：{engine.version or '未找到'}")
            print(f"    路径：{engine.executable or '—'}")
        print(f"OpenBabel：{obabel.version_string or '未找到'}")
        print(f"  Python API：{'可用' if obabel.has_python_api else '不可用'}")
        print(f"  路径：{obabel.executable or '—'}")
        print(f"PyMOL 渲染：{pymol or '未找到'}")
        if not pymol:
            print("  提示：安装包应内置 PyMOLRunner；源码运行可安装 pymol-open-source。")
        print("化学计算组件：" + "  ".join(f"{k} {v or '缺失'}" for k, v in packages.items()))
        print(
            "几何口袋检测："
            + ("可用" if pocket_backend["ok"] else f"不可用（{pocket_backend['error']}）")
        )
        print()
        if core_ok:
            print("核心环境检查通过，可以开始对接。")
        else:
            print("缺少核心组件：请先解决对接引擎 / OpenBabel / 临时目录问题。")
    return EXIT_OK if report["ok"] else EXIT_FAILURE


def command_init(args: argparse.Namespace) -> int:
    workspace = Workspace.create(args.project, name=args.name)
    print(f"项目已就绪：{workspace.name}")
    print(f"目录：{workspace.root}")
    print("下一步：用 import 或 fetch 添加靶点结构与候选分子。")
    return EXIT_OK


def command_info(args: argparse.Namespace) -> int:
    workspace = _open_workspace(args.project)
    from .services.docking import collect_existing_results

    results = collect_existing_results(workspace)
    payload = workspace.summary()
    payload["entries"] = {
        "targets": [
            {
                "stem": entry.stem,
                "name": entry.display_name,
                "status": entry.status,
                "message": entry.message,
                "kind": modes.entry_kind(entry, workspace),
            }
            for entry in workspace.targets
        ],
        "ligands": [
            {
                "stem": entry.stem,
                "name": entry.display_name,
                "status": entry.status,
                "message": entry.message,
                "kind": modes.entry_kind(entry, workspace),
            }
            for entry in workspace.ligands
        ],
    }
    payload["results"] = {
        "count": len(results),
        "success": sum(1 for item in results if item.ok),
        "best": min(
            (item.best_affinity for item in results if item.best_affinity is not None),
            default=None,
        ),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    print(f"项目：{payload['name']}")
    print(f"目录：{payload['root']}")
    print(
        f"靶点 {payload['ready_targets']}/{payload['targets']} 已就绪 · "
        f"候选分子 {payload['ready_ligands']}/{payload['ligands']} 已就绪"
    )
    print(f"已有结果：{payload['results']['success']} 个成功 / 共 {payload['results']['count']} 个")
    if payload["results"]["best"] is not None:
        print(f"最佳打分：{payload['results']['best']:.2f} kcal/mol")
    for label, key in (("靶点", "targets"), ("候选分子", "ligands")):
        entries = payload["entries"][key]
        if not entries:
            continue
        print(f"\n{label}：")
        for entry in entries:
            suffix = f" · {entry['message']}" if entry["message"] else ""
            kind_label = modes.KIND_LABELS.get(entry.get("kind", ""), "")
            kind_text = f" · {kind_label}" if kind_label else ""
            print(f"  [{entry['status']}] {entry['name']} ({entry['stem']}){kind_text}{suffix}")
    return EXIT_OK


def command_prepare(args: argparse.Namespace) -> int:
    from .services.preparation import prepare_all

    workspace = _open_workspace(args.project)
    settings = AppSettings.load()
    if args.ph is not None:
        settings.add_hydrogens_ph = float(args.ph)
    if args.states is not None:
        settings.enumeration_states = int(args.states)
    kinds = {
        "all": (KIND_TARGET, KIND_LIGAND),
        "targets": (KIND_TARGET,),
        "ligands": (KIND_LIGAND,),
    }[args.only]
    if not any(workspace.entries(kind) for kind in kinds):
        print("项目里没有需要准备的条目。", file=sys.stderr)
        return EXIT_FAILURE
    report = prepare_all(
        workspace, settings, kinds=kinds, force=args.force, progress=_print_progress
    )
    print(
        f"\n准备完成：成功 {report.succeeded}，跳过 {report.skipped}，失败 {report.failed}"
        + ("，已取消" if report.cancelled else "")
    )
    for entry, outcome in report.outcomes:
        if not outcome.ok:
            print(f"  ✗ {entry.display_name}：{outcome.message or '未知错误'}")
        elif outcome.warnings:
            print(f"  ! {entry.display_name}：" + "；".join(outcome.warnings))
    return EXIT_OK if report.ok else EXIT_FAILURE


def _manual_box(args: argparse.Namespace) -> Box | None:
    if args.center is None and args.size is None:
        return None
    if args.center is None or args.size is None:
        raise ValueError("手动盒子需要同时提供 --center 和 --size")
    from .services.boxes import validate_manual_box

    return validate_manual_box(args.center, args.size)


def command_dock(args: argparse.Namespace) -> int:
    from .services import boxes as box_service
    from .services.analysis import analyze
    from .services.docking import DockingOptions, run_batch
    from .services.report import generate_report

    workspace = _open_workspace(args.project)
    settings = AppSettings.load()
    try:
        targets = _select(workspace.targets, args.target, "靶点")
        ligands = _select(workspace.ligands, args.ligand, "候选分子")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    if not targets or not ligands:
        print("需要至少一个靶点和一个候选分子；请先添加并准备结构。", file=sys.stderr)
        return EXIT_USAGE
    unprepared = [
        entry.display_name
        for entry in targets + ligands
        if not entry.is_ready or not workspace.absolute(entry.prepared).is_file()
    ]
    if unprepared:
        print("以下条目尚未准备完成：" + "、".join(unprepared), file=sys.stderr)
        print("请先运行：dockflowlab-cli prepare <工作目录>", file=sys.stderr)
        return EXIT_USAGE

    supported = modes.supported_pairs(targets, ligands, workspace)
    unsupported = modes.unsupported_pairs(targets, ligands, workspace)
    for target, ligand, mode in unsupported:
        print(
            f"跳过 {target.display_name} + {ligand.display_name}：{mode.label} 不受支持。{mode.guidance}",
            file=sys.stderr,
        )
    if not supported:
        print(
            "没有可运行的组合：所选结构都属于不支持的对接类型。"
            "蛋白–蛋白等大分子对接请使用 ClusPro、HADDOCK 或 RosettaDock。",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    pairs = [(target, ligand) for target, ligand, _mode in supported]
    ligands_by_target: dict[str, list] = {}
    for target, ligand, _mode in supported:
        ligands_by_target.setdefault(target.stem, []).append(ligand)
    runnable_targets = [target for target in targets if target.stem in ligands_by_target]

    manual = _manual_box(args)
    if manual is not None and len(runnable_targets) > 1:
        print("提示：手动盒子会应用于本次全部靶点。", file=sys.stderr)

    options = DockingOptions.from_settings(settings)
    if args.exhaustiveness is not None:
        options.exhaustiveness = args.exhaustiveness
    if args.num_modes is not None:
        options.num_modes = args.num_modes
    if args.energy_range is not None:
        options.energy_range = args.energy_range
    if args.seed is not None:
        options.seed = args.seed
    if args.cpu is not None:
        options.cpu = args.cpu
    options.skip_existing = not args.force

    from .tools.obabel import get_openbabel
    from .tools.vina import available_engines, engine_from_settings

    engine_key = args.engine or settings.docking_engine
    engine = engine_from_settings(settings, engine_key)
    if not engine.available:
        print(
            f"未找到 {engine.label}。请先在设置中指定它的可执行文件，"
            f"或改用 --engine 选择其他已安装的引擎。",
            file=sys.stderr,
        )
        return EXIT_USAGE
    fallback_engines = []
    if not args.no_fallback and settings.engine_fallback:
        fallback_engines = [
            item for item in available_engines(settings) if item.key != engine.key
        ]
    options.engine = engine.key
    options.fallback = bool(fallback_engines)
    if args.scoring is not None:
        options.scoring = "" if args.scoring == "default" else args.scoring
    if options.scoring and engine.key != "smina":
        options.scoring = ""
    if args.scoring is not None and engine.key != "smina" and args.scoring != "default":
        print("提示：--scoring 仅对 Smina 生效，已忽略。")
    print(f"对接引擎：{engine.label}" + (
        "（失败时自动改用 " + "、".join(item.label for item in fallback_engines) + "）"
        if fallback_engines
        else ""
    ))
    if options.scoring:
        print("评分函数：Vinardo（与 Vina 默认评分不可直接比较）")

    obabel = get_openbabel()
    boxes: dict[str, Box] = {}
    for target in runnable_targets:
        if manual is not None:
            box = manual
        else:
            box = workspace.load_box(target.stem)
            if box is None:
                print(f"正在为 {target.display_name} 计算搜索盒子…", flush=True)
                box, warnings = box_service.suggest_box(
                    target_source=workspace.absolute(target.source),
                    target_prepared_pdb=workspace.prepared_target_pdb(target.stem),
                    ligand_paths=[
                        workspace.absolute(ligand.prepared)
                        for ligand in ligands_by_target[target.stem]
                        if ligand.prepared
                    ],
                    settings=settings,
                    target_kind=modes.target_kind_for(target, workspace),
                    chains=target.meta.get("chains", []),
                    obabel=obabel,
                )
                for warning in warnings:
                    print(f"  ! {warning}", file=sys.stderr)
                workspace.save_box(target.stem, box)
        boxes[target.stem] = box
        print(f"{target.display_name}：盒子中心 {box.center}，边长 {box.size} Å，来源 {box.source_label}")

    results = run_batch(
        workspace,
        pairs,
        boxes,
        options=options,
        settings=settings,
        vina=engine,
        fallback_engines=fallback_engines,
        obabel=obabel,
        progress=_print_progress,
    )

    success = sum(1 for item in results if item.ok)
    failed = len(results) - success
    print(f"\n对接完成：成功 {success}，失败 {failed}")
    for item in results:
        if item.ok:
            suffix = "（已有结果）" if item.skipped else ""
            print(f"  ✓ {item.target} + {item.ligand}：{item.affinity_text} kcal/mol{suffix}")
        else:
            print(f"  ✗ {item.target} + {item.ligand}：{item.error}")

    if not args.no_report:
        try:
            bundle = analyze(workspace, results)
            report_path = generate_report(workspace, results, settings=settings)
            print(f"\n结果表：{bundle.csv_path}")
            print(f"报告：{report_path}")
        except Exception as exc:  # 报告失败不应抹掉已完成的对接
            print(f"生成报告失败：{exc}", file=sys.stderr)
            return EXIT_FAILURE
    return EXIT_OK if failed == 0 else EXIT_FAILURE


def command_report(args: argparse.Namespace) -> int:
    from .services.docking import collect_existing_results
    from .services.report import generate_report

    workspace = _open_workspace(args.project)
    results = collect_existing_results(workspace)
    if not results:
        print("项目里还没有对接结果，无法生成报告。", file=sys.stderr)
        return EXIT_FAILURE
    output = Path(args.output) if args.output else None
    report_path = generate_report(workspace, results, output_path=output)
    print(f"报告已生成：{report_path}")
    return EXIT_OK


def command_fetch(args: argparse.Namespace) -> int:
    from .services.download import DownloadClient

    workspace = _open_workspace(args.project)
    client = DownloadClient(AppSettings.load())
    dispatch = {
        "pdb": client.pdb,
        "pubchem": client.pubchem,
        "alphafold": client.alphafold,
        "chembl": client.chembl,
    }
    try:
        result = dispatch[args.source](args.query)
        entry = workspace.import_bytes(
            args.kind, result.data, result.filename, result.display_name, result.meta
        )
    except Exception as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"已导入：{entry.display_name} ({entry.stem}) · {result.meta.get('source', args.source)}")
    for warning in result.meta.get("warnings", []) or []:
        print(f"  ! {warning}")
    return EXIT_OK


def command_import(args: argparse.Namespace) -> int:
    workspace = _open_workspace(args.project)
    names = list(args.name)
    if names and len(names) != len(args.files):
        print("--name 的数量必须与文件数量一致。", file=sys.stderr)
        return EXIT_USAGE
    imported = 0
    for index, path in enumerate(args.files):
        name = names[index] if names else ""
        try:
            entry = workspace.import_file(args.kind, path, display_name=name)
        except Exception as exc:
            print(f"  ✗ {path}：{exc}", file=sys.stderr)
            continue
        imported += 1
        print(f"  ✓ {entry.display_name} → {entry.stem}")
    print(f"共导入 {imported}/{len(args.files)} 个文件。")
    if args.kind == KIND_LIGAND:
        import re

        for path in args.files:
            if path.suffix.lower() not in {".sdf", ".sd"}:
                continue
            try:
                count = len(re.findall(r"^\$\$\$\$$", path.read_text(encoding="utf-8", errors="ignore"), re.M))
            except OSError:
                continue
            if count > 1:
                print(f"  ! {path.name} 含 {count} 个分子；请先拆分，每个文件只放一个分子。")
    return EXIT_OK if imported == len(args.files) else EXIT_FAILURE


COMMANDS = {
    "doctor": command_doctor,
    "init": command_init,
    "info": command_info,
    "prepare": command_prepare,
    "dock": command_dock,
    "report": command_report,
    "fetch": command_fetch,
    "import": command_import,
}


def main(argv: Sequence[str] | None = None) -> int:
    # Windows 控制台默认使用 GBK，统一为 UTF-8 可避免中文乱码。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:  # 统一错误出口，避免命令行打印整段堆栈
        from .errors import DockFlowError

        if isinstance(exc, (DockFlowError, OSError, ValueError)):
            print(f"错误：{exc}", file=sys.stderr)
            return EXIT_FAILURE
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
