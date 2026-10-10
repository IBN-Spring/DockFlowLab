# DockFlowLab 环境配置说明

## 📦 环境架构

DockFlowLab 使用**混合环境策略**，确保最大的兼容性和灵活性。

---

## 🎯 三层环境体系

### 1️⃣ Runtime 环境（内置，打包后使用）

**位置**: `runtime/`

**包含**:
- Python 解释器 (`python.exe`)
- 基础科学计算库 (NumPy, Pandas)
- 内置工具 (`tools/`)
  - obabel.exe (OpenBabel)
  - vina.exe (AutoDock Vina)
  - prepare_ligand4.py, prepare_receptor4.py

**用途**:
- ✅ 打包后的可执行程序
- ✅ 无需用户安装Python
- ✅ 确保环境一致性

**限制**:
- ❌ 不包含RDKit（体积大）
- ❌ 不包含fpocket（Linux专用）

**优先级**: 🥇 打包后的首选

---

### 2️⃣ Conda 环境（开发和高级功能）

**环境名**: `DockFlowLab`

**创建命令**:
```bash
conda create -n DockFlowLab python=3.10
conda activate DockFlowLab

# 核心依赖
pip install numpy pandas

# OpenBabel（推荐conda安装）
conda install -c conda-forge openbabel

# 高级功能（可选）
pip install rdkit scikit-learn scipy
```

**用途**:
- ✅ 开发环境
- ✅ 高级算法（RDKit ETKDG、DBSCAN聚类）
- ✅ MCP Server
- ✅ 受体预处理（PDBFixer）

**检测函数**:
```python
from src.core.utils import get_conda_python, get_conda_env

# 获取conda Python路径
python_path = get_conda_python()

# 获取conda环境变量
env = get_conda_env()
```

**优先级**: 🥈 开发时的首选，打包后的回退

---

### 3️⃣ 系统环境（全局安装）

**典型工具**:
- **OpenBabel**: `C:\Program Files\OpenBabel-3.1.1\obabel.exe`
- **fpocket**: `/usr/bin/fpocket` (Linux)
- **AutoDock Tools**: 系统安装的MGLTools

**用途**:
- ✅ 避免DLL冲突（OpenBabel特别需要）
- ✅ Linux工具（fpocket）
- ✅ 用户自定义工具

**检测函数**:
```python
from src.core.utils import get_tool_path

# 自动检测：系统安装 > runtime内置
obabel = get_tool_path("obabel.exe")
```

**优先级**: 🥇 工具的首选（避免冲突）

---

## 🔄 环境选择策略

### 工具查找顺序

```python
# src/core/utils.py:43-60
def get_tool_path(name: str) -> str:
    """
    优先级：
    1. 系统安装版本（特别是obabel.exe）
    2. Runtime内置版本（tools/）
    3. 抛出异常
    """
    
    # 特殊处理：obabel优先用系统版本
    if name == "obabel.exe":
        system_paths = [
            "C:\\Program Files\\OpenBabel-3.1.1\\obabel.exe",
            "C:\\Program Files (x86)\\OpenBabel-3.1.1\\obabel.exe",
        ]
        for path in system_paths:
            if os.path.isfile(path):
                return path  # ✅ 找到系统版本
    
    # 回退到runtime内置
    bundled = get_path(f"tools/{name}")
    if os.path.isfile(bundled):
        return bundled  # ✅ 使用内置版本
    
    raise FileNotFoundError(f"未找到: {name}")
```

### Python环境选择

```python
# src/core/utils.py:15-40

# 打包后运行
def get_runtime_python() -> str:
    return get_path("runtime/python.exe")

# 开发时或高级功能
def get_conda_python() -> str:
    candidates = [
        "~/miniconda3/envs/DockFlowLab/python.exe",
        "~/anaconda3/envs/DockFlowLab/python.exe",
        ...
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("未找到conda环境")
```

---

## 🧩 模块环境依赖表

| 模块 | Runtime | Conda | 系统 | 备注 |
|------|---------|-------|------|------|
| **downloader.py** | ✅ | ✅ | - | 仅需Python标准库 |
| **receptor_processor.py** | ⚠️ | ✅ | - | 需PDBFixer（conda） |
| **ligand_processor.py** | ✅ | ✅ | ✅ | OpenBabel优先用系统版本 |
| **box_generator.py** | ✅ | ✅ | - | NumPy足够 |
| **docking.py** | ✅ | ✅ | - | 调用vina.exe |
| **analyzer.py** | ✅ | ✅ | - | Pandas足够 |
| **advanced_box_algorithms.py** | ⚠️ | ✅ | ✅ | fpocket需系统安装（Linux） |
| **advanced_ligand_algorithms.py** | ⚠️ | ✅ | - | RDKit需conda |

**图例**:
- ✅ 完全支持
- ⚠️ 部分功能受限（会自动降级）
- - 不适用

---

## 🚀 高级算法环境需求

### fpocket口袋检测
```python
# advanced_box_algorithms.py:36-85

# 需要：Linux系统 + fpocket安装
# 安装：sudo apt install fpocket

# 检测逻辑：
try:
    result = subprocess.run(["fpocket", "-h"], ...)
    # ✅ fpocket可用
except FileNotFoundError:
    # ⚠️ 自动跳过，使用活性残基方法
    log("fpocket未安装，跳过口袋检测")
```

### RDKit构象生成
```python
# advanced_ligand_algorithms.py:196-265

# 需要：conda环境 + RDKit
# 安装：pip install rdkit

# 检测逻辑：
try:
    from rdkit import Chem
    # ✅ RDKit可用，使用ETKDG算法
except ImportError:
    # ⚠️ 回退到OpenBabel优化
    log("RDKit未安装，使用OpenBabel回退方案")
```

### DBSCAN聚类
```python
# advanced_box_algorithms.py:168-210

# 需要：scikit-learn
# 安装：pip install scikit-learn

try:
    from sklearn.cluster import DBSCAN
    # ✅ sklearn可用
except ImportError:
    # ⚠️ 使用全部残基（不聚类）
    return [active_residues]
```

### 凸包算法
```python
# advanced_box_algorithms.py:213-258

# 需要：scipy
# 安装：pip install scipy

try:
    from scipy.spatial import ConvexHull
    # ✅ scipy可用
except ImportError:
    # ⚠️ 回退到AABB盒子
    return calculate_initial_box(...)
```

---

## 🔍 环境检测与验证

### 自动检测脚本

```python
# 可以添加到 src/__version__.py

def check_environment():
    """检测当前环境配置"""
    report = {
        "runtime_available": False,
        "conda_available": False,
        "system_tools": {},
        "optional_libs": {},
    }
    
    # 检测Runtime
    try:
        from src.core.utils import get_runtime_python
        python = get_runtime_python()
        report["runtime_available"] = os.path.isfile(python)
    except:
        pass
    
    # 检测Conda
    try:
        from src.core.utils import get_conda_python
        python = get_conda_python()
        report["conda_available"] = os.path.isfile(python)
    except:
        pass
    
    # 检测系统工具
    tools = ["obabel.exe", "vina.exe", "fpocket"]
    for tool in tools:
        try:
            from src.core.utils import get_tool_path
            path = get_tool_path(tool)
            report["system_tools"][tool] = path
        except:
            report["system_tools"][tool] = None
    
    # 检测可选库
    optional = ["rdkit", "sklearn", "scipy"]
    for lib in optional:
        try:
            __import__(lib)
            report["optional_libs"][lib] = True
        except ImportError:
            report["optional_libs"][lib] = False
    
    return report
```

### 使用示例

```python
from src.__version__ import check_environment

report = check_environment()

print("环境检测报告:")
print(f"Runtime: {'✅' if report['runtime_available'] else '❌'}")
print(f"Conda: {'✅' if report['conda_available'] else '❌'}")
print("\n系统工具:")
for tool, path in report['system_tools'].items():
    status = '✅' if path else '❌'
    print(f"  {tool}: {status} {path or ''}")
print("\n可选库:")
for lib, available in report['optional_libs'].items():
    status = '✅' if available else '⚠️'
    print(f"  {lib}: {status}")
```

---

## 📋 环境配置检查清单

### 基础功能（必需）
- [ ] Python 3.8+
- [ ] NumPy
- [ ] Pandas
- [ ] OpenBabel (conda或系统安装)
- [ ] AutoDock Vina

### 高级功能（可选）
- [ ] RDKit (conda环境)
- [ ] scikit-learn (聚类)
- [ ] scipy (凸包)
- [ ] fpocket (Linux，口袋检测)

### 开发环境（开发者）
- [ ] pytest
- [ ] black
- [ ] mypy
- [ ] sphinx

---

## 🔧 常见环境问题

### 问题1：OpenBabel DLL冲突

**症状**: `obabel.exe` 运行报错 "找不到DLL"

**原因**: Runtime内置的OpenBabel与系统版本冲突

**解决**:
```python
# utils.py 已实现：优先使用系统安装版本
def get_tool_path(name: str):
    if name == "obabel.exe":
        # 先找系统版本
        system_path = "C:\\Program Files\\OpenBabel-3.1.1\\obabel.exe"
        if os.path.isfile(system_path):
            return system_path  # ✅ 避免冲突
```

### 问题2：RDKit未安装

**症状**: 高级配体算法报错

**原因**: Runtime不包含RDKit

**解决**:
```python
# advanced_ligand_algorithms.py 已实现：自动降级
try:
    from rdkit import Chem
except ImportError:
    # ✅ 使用OpenBabel回退方案
    log("RDKit不可用，使用标准方法")
```

### 问题3：Conda环境找不到

**症状**: `get_conda_python()` 抛出异常

**原因**: 环境名不是 `DockFlowLab`

**解决**:
```python
# config.py
CONFIG = {
    "conda_env": "your_env_name",  # 自定义环境名
    ...
}
```

---

## 🎯 推荐配置

### 开发者配置（最完整）
```bash
# 1. 创建Conda环境
conda create -n DockFlowLab python=3.10
conda activate DockFlowLab

# 2. 安装核心依赖
conda install -c conda-forge openbabel
pip install numpy pandas

# 3. 安装高级依赖
pip install rdkit scikit-learn scipy

# 4. 安装开发工具
pip install -r requirements-dev.txt

# 5. Linux额外工具
sudo apt install fpocket  # 仅Linux
```

### 用户配置（打包后）
```
无需配置！
- 解压即用
- 内置runtime环境
- 自动检测系统工具
```

### 服务器配置（MCP Server）
```bash
# 使用Conda环境
conda create -n DockFlowLab python=3.10
conda activate DockFlowLab
conda install -c conda-forge openbabel
pip install numpy pandas fastmcp

# 启动
python mcp_server.py
```

---

## 📊 环境依赖总结

```
DockFlowLab
├── Runtime（打包后）
│   ├── python.exe          # Python 3.10
│   ├── numpy              # 核心计算
│   ├── pandas             # 数据处理
│   └── tools/
│       ├── obabel.exe     # OpenBabel（回退）
│       └── vina.exe       # AutoDock Vina
│
├── Conda（开发时）
│   ├── python             # Python 3.10
│   ├── openbabel          # 首选OpenBabel
│   ├── rdkit              # 高级构象
│   ├── scikit-learn       # 聚类算法
│   ├── scipy              # 凸包算法
│   └── pdbfixer           # 受体修复
│
└── System（全局安装）
    ├── OpenBabel          # 避免DLL冲突
    ├── fpocket            # Linux口袋检测
    └── MGLTools           # AutoDock工具
```

---

## ✅ 结论

**DockFlowLab的环境策略确保**:

1. ✅ **打包后开箱即用** - Runtime环境
2. ✅ **开发时功能完整** - Conda环境
3. ✅ **高级功能可选** - 优雅降级
4. ✅ **避免环境冲突** - 优先系统工具
5. ✅ **跨平台兼容** - Windows/Linux/macOS

**当前状态**: 所有环境依赖已正确实现，新增的高级算法完全兼容现有架构！

---

**文档版本**: v1.0  
**最后更新**: 2024-10-10  
**维护者**: DockFlowLab Development Team
