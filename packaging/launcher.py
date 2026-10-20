"""PyInstaller 入口脚本。

打包后该文件会被当作顶层 ``__main__`` 执行，因此必须使用绝对导入，
不能像 ``dockflowlab/__main__.py`` 那样使用相对导入。
"""

from __future__ import annotations

import multiprocessing
import os
import sys

from dockflowlab.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # 打包后没有独立的 CLI 入口时，用于发布前自检与远程排障
    if os.environ.get("DOCKFLOWLAB_SELF_CHECK") == "1":
        from dockflowlab.cli import main as cli_main

        raise SystemExit(cli_main(["doctor", "--json"]))
    # 允许安装包直接执行命令行子命令（需在终端/控制台构建中使用）
    if os.environ.get("DOCKFLOWLAB_CLI") == "1":
        from dockflowlab.cli import main as cli_main

        raise SystemExit(cli_main(sys.argv[1:]))
    raise SystemExit(main())
