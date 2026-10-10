"""
DockFlowLab - 专业分子对接工具
Version: 1.1.0-beta
"""

__version__ = "1.1.0-beta"
__author__ = "DockFlowLab Development Team"
__email__ = "dev@dockflowlab.com"
__license__ = "MIT"
__copyright__ = "Copyright 2024 DockFlowLab"

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "__license__",
    "__copyright__",
]

# 版本信息元组
VERSION = tuple(map(int, __version__.split("-")[0].split(".")))
VERSION_STRING = __version__

# 构建信息
BUILD_DATE = "2024-10-10"
GIT_COMMIT = "fba820a"  # 从git log获取

# 功能标记
FEATURES = {
    "basic_docking": True,
    "advanced_algorithms": True,
    "gpu_support": True,
    "lklight_support": True,
    "mcp_server": True,
    "gui": False,  # GUI功能开发中
}

# 最低依赖版本
MIN_PYTHON_VERSION = (3, 8)
REQUIRED_DEPENDENCIES = {
    "numpy": "1.20.0",
    "pandas": "1.3.0",
}

OPTIONAL_DEPENDENCIES = {
    "scikit-learn": "1.0.0",  # DBSCAN聚类
    "scipy": "1.7.0",          # 凸包算法
    "rdkit": "2022.3.1",       # 高级构象生成
}


def get_version_info():
    """获取完整版本信息"""
    return {
        "version": __version__,
        "build_date": BUILD_DATE,
        "git_commit": GIT_COMMIT,
        "python_version": f"{VERSION[0]}.{VERSION[1]}.{VERSION[2]}",
        "features": FEATURES,
    }


def check_dependencies():
    """检查依赖是否满足要求"""
    import sys
    import importlib.metadata

    errors = []
    warnings = []

    # 检查Python版本
    if sys.version_info < MIN_PYTHON_VERSION:
        errors.append(
            f"Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ required, "
            f"found {sys.version_info[0]}.{sys.version_info[1]}"
        )

    # 检查必需依赖
    for pkg, min_ver in REQUIRED_DEPENDENCIES.items():
        try:
            ver = importlib.metadata.version(pkg)
            if ver < min_ver:
                errors.append(f"{pkg}>={min_ver} required, found {ver}")
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"{pkg}>={min_ver} not installed")

    # 检查可选依赖
    for pkg, min_ver in OPTIONAL_DEPENDENCIES.items():
        try:
            importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            warnings.append(
                f"{pkg} not installed - advanced features disabled"
            )

    return errors, warnings


def print_banner():
    """打印启动横幅"""
    banner = f"""
╔═══════════════════════════════════════════════════════════════╗
║                        DockFlowLab                            ║
║                 分子对接自动化工具                             ║
║                                                               ║
║  Version: {__version__:<20s}                         ║
║  Build:   {BUILD_DATE}                                     ║
║  Python:  {'.'.join(map(str, VERSION)):<20s}                         ║
╚═══════════════════════════════════════════════════════════════╝
"""
    print(banner)


if __name__ == "__main__":
    print_banner()
    print("\n依赖检查:")
    errors, warnings = check_dependencies()

    if errors:
        print("\n❌ 错误:")
        for err in errors:
            print(f"  - {err}")

    if warnings:
        print("\n⚠️  警告:")
        for warn in warnings:
            print(f"  - {warn}")

    if not errors:
        print("\n✅ 所有必需依赖已满足")

    print("\n版本信息:")
    info = get_version_info()
    for key, value in info.items():
        print(f"  {key}: {value}")
