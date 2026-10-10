"""
DockFlowLab 安装脚本
"""
from setuptools import setup, find_packages
from pathlib import Path

# 读取 README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# 读取版本
version_file = Path(__file__).parent / "src" / "__version__.py"
version_dict = {}
if version_file.exists():
    exec(version_file.read_text(encoding="utf-8"), version_dict)
    version = version_dict.get("__version__", "1.1.0")
else:
    version = "1.1.0"

# 核心依赖
install_requires = [
    "numpy>=1.20.0,<2.0.0",
    "pandas>=1.3.0,<2.0.0",
]

# 可选依赖
extras_require = {
    "advanced": [
        "scikit-learn>=1.0.0",
        "scipy>=1.7.0",
        "rdkit>=2022.3.1",
    ],
    "dev": [
        "pytest>=7.0.0",
        "pytest-cov>=3.0.0",
        "black>=22.0.0",
        "flake8>=4.0.0",
        "mypy>=0.950",
    ],
    "docs": [
        "sphinx>=4.5.0",
        "sphinx-rtd-theme>=1.0.0",
    ],
}

# 完整安装（包含所有可选依赖）
extras_require["all"] = list(set(sum(extras_require.values(), [])))

setup(
    name="dockflowlab",
    version=version,
    description="专业的分子对接自动化工具",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="DockFlowLab Development Team",
    author_email="dev@dockflowlab.com",
    url="https://github.com/yourusername/DockFlowLab",
    project_urls={
        "Bug Reports": "https://github.com/yourusername/DockFlowLab/issues",
        "Documentation": "https://dockflowlab.readthedocs.io",
        "Source": "https://github.com/yourusername/DockFlowLab",
    },
    packages=find_packages(exclude=["tests", "tests.*", "docs", "tools"]),
    include_package_data=True,
    package_data={
        "src": [
            "data/模板/*.html",
            "data/visual/*.pml",
        ],
    },
    python_requires=">=3.8",
    install_requires=install_requires,
    extras_require=extras_require,
    entry_points={
        "console_scripts": [
            "dockflowlab=src.cli.commands:main",
            "dfl=src.cli.commands:main",  # 简短别名
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Chemistry",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
    ],
    keywords="molecular-docking autodock-vina drug-discovery virtual-screening",
    zip_safe=False,
)
