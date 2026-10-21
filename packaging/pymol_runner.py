"""打包版 PyMOL 渲染入口。

主程序以 ``PyMOLRunner.exe -cq render.pml`` 调用本入口。它只启动 PyMOL2
的无界面会话，不创建 Qt 窗口，因此不必再打包一套完整的 PyMOL GUI。
退出码与 PyMOL 命令行一致：0 成功，非 0 失败。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _script_from_args(argv: list[str]) -> str:
    """从 ``-cq render.pml`` 之类的参数中取出 PML 脚本路径。"""
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    script = _script_from_args(args)
    if not script:
        print("usage: PyMOLRunner -cq render.pml", file=sys.stderr)
        return 2
    try:
        import pymol2
    except Exception:
        traceback.print_exc()
        return 1

    session = None
    try:
        session = pymol2.PyMOL()
        session.start()
        # 渲染作业内的相对路径（风格脚本、结构文件、输出 PNG）仍以调用方的
        # 工作目录为准；这里只把作业脚本本身转成绝对路径。
        session.cmd.do("@" + str(Path(script).resolve()))
        return 0
    except SystemExit as exc:  # PyMOL 的 quit 会触发
        return int(exc.code or 0)
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
