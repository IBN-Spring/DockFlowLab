"""``python -m dockflowlab`` 入口。

同时兼容在 IDE 中直接运行本文件（此时 ``__package__`` 为空，相对导入不可用）。
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

if __package__ in (None, ""):
    # 直接执行 src/dockflowlab/__main__.py 时，把 src 加入模块搜索路径。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dockflowlab.app import main
else:
    from .app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
