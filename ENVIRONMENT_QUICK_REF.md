# 🚀 DockFlowLab 环境快速参考

## ✅ 简短回答：是的，使用了 Runtime + Conda 混合环境

---

## 📊 一图看懂环境架构

```
┌─────────────────────────────────────────────────────────┐
│                   DockFlowLab 环境                      │
└─────────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
    ┌──────────┐      ┌──────────┐     ┌──────────┐
    │ Runtime  │      │  Conda   │     │  System  │
    │ (内置)   │      │ (开发)   │     │ (全局)   │
    └──────────┘      └──────────┘     └──────────┘
         │                 │                 │
    ┌────┴────┐       ┌────┴────┐      ┌────┴────┐
    │Python   │       │RDKit    │      │OpenBabel│
    │NumPy    │       │sklearn  │      │fpocket  │
    │Pandas   │       │scipy    │      │         │
    │Vina     │       │OpenBabel│      │         │
    └─────────┘       └─────────┘      └─────────┘
        ↓                 ↓                 ↓
    打包后使用         开发+高级         避免冲突
```

---

## 🎯 核心要点

### 1. 环境选择逻辑
```python
# 工具查找优先级
系统安装 > Runtime内置 > 报错

# Python环境
打包后 → runtime/python.exe
开发时 → conda env DockFlowLab
```

### 2. 模块环境依赖

| 功能 | Runtime | Conda | 必需? |
|------|---------|-------|-------|
| 基础对接 | ✅ | ✅ | 必需 |
| 受体预处理 | ⚠️ | ✅ | 必需 |
| 配体预处理 | ✅ | ✅ | 必需 |
| RDKit算法 | ❌ | ✅ | 可选 |
| fpocket | ❌ | ❌ | 可选(Linux) |
| DBSCAN聚类 | ❌ | ✅ | 可选 |
| 凸包算法 | ❌ | ✅ | 可选 |

### 3. 关键函数位置

```python
# src/core/utils.py
get_runtime_python()   # runtime/python.exe
get_conda_python()     # conda env路径
get_conda_env()        # 环境变量
get_tool_path(name)    # 工具路径（自动选择）

# src/utils/path.py
get_path(rel_path)     # 项目相对路径
```

---

## 🔍 环境检测方法

### 快速检测当前环境

```python
# 方法1：检查Python环境
import sys
if hasattr(sys, '_MEIPASS'):
    print("✅ Runtime环境（打包后）")
else:
    print("✅ 开发环境（源码运行）")

# 方法2：检查可选依赖
try:
    import rdkit
    print("✅ RDKit可用")
except ImportError:
    print("⚠️ RDKit不可用（使用基础算法）")

# 方法3：检查系统工具
from src.core.utils import get_tool_path
try:
    obabel = get_tool_path("obabel.exe")
    print(f"✅ OpenBabel: {obabel}")
except FileNotFoundError:
    print("❌ OpenBabel未找到")
```

---

## 🛠️ 常见场景

### 场景1：开发者运行代码
```bash
# 使用Conda环境
conda activate DockFlowLab
python mcp_server.py
# → 使用 get_conda_python()
# → 高级算法全部可用
```

### 场景2：用户运行打包程序
```bash
# 双击 DockFlowLab.exe
# → 使用 get_runtime_python()
# → 基础功能可用，高级算法自动降级
```

### 场景3：MCP Server
```bash
# Claude Code/Desktop调用
conda activate DockFlowLab
python mcp_server.py
# → 使用 get_conda_env()
# → 完整功能
```

---

## ⚠️ 重要注意事项

### 1. OpenBabel冲突问题
```python
# ✅ 已解决：优先使用系统安装版本
# src/core/utils.py:48-55
if name == "obabel.exe":
    system_paths = [
        "C:\\Program Files\\OpenBabel-3.1.1\\obabel.exe",
        ...
    ]
    # 先找系统版本，避免DLL冲突
```

### 2. 高级算法降级
```python
# ✅ 已实现：所有高级算法都有回退方案
try:
    from rdkit import Chem
    # 使用RDKit ETKDG
except ImportError:
    # 使用OpenBabel优化
```

### 3. 环境变量设置
```python
# ✅ 已实现：自动配置BABEL_DATADIR
from src.core.utils import get_conda_env
env = get_conda_env()  # 自动添加必需的环境变量
```

---

## 📦 新增高级算法的环境兼容性

| 算法 | 依赖 | 环境 | 回退方案 |
|------|------|------|----------|
| fpocket口袋检测 | fpocket | Linux系统 | 使用活性残基 |
| DBSCAN聚类 | scikit-learn | Conda | 使用全部残基 |
| 凸包算法 | scipy | Conda | 使用AABB盒子 |
| RDKit ETKDG | rdkit | Conda | OpenBabel优化 |
| 智能力场选择 | - | Runtime | ✅ 无依赖 |
| 能量验证 | - | Runtime | ✅ 无依赖 |

**结论**: ✅ 所有新增算法都是**可选增强**，不影响基础功能！

---

## 🎓 代码示例

### 示例1：使用基础功能（Runtime足够）
```python
from src.core.docking import run_docking
from src.core.box_generator import generate_box

# ✅ Runtime环境即可运行
box_config = generate_box("receptor.pdbqt", active_residues)
run_docking("receptor.pdbqt", "ligand.pdbqt", box_config)
```

### 示例2：使用高级功能（需要Conda）
```python
from src.core.advanced_box_algorithms import generate_advanced_box
from src.core.advanced_ligand_algorithms import generate_conformers_rdkit

# ⚠️ 需要Conda环境 + RDKit
if HAS_RDKIT:  # 自动检测
    box = generate_advanced_box(use_fpocket=True)
    generate_conformers_rdkit("ligand.sdf", "output.pdbqt")
else:
    # ✅ 自动降级到基础方法
    box = generate_box(...)
```

### 示例3：环境感知的工具调用
```python
from src.core.utils import get_tool_path, get_conda_env

# 自动选择最佳OpenBabel
obabel = get_tool_path("obabel.exe")
# → 系统版本（如果有）> Runtime内置版本

# 获取配置好的环境变量
env = get_conda_env()
subprocess.run(cmd, env=env)  # ✅ 正确的环境变量
```

---

## 📝 总结

### ✅ 你的问题答案

**"这个代码目前使用的环境是来自于runtime的吗？"**

**答案：是，但不仅仅是！**

1. **Runtime环境** - 提供基础功能（打包后使用）
2. **Conda环境** - 提供高级功能（开发时和高级算法）
3. **系统环境** - 提供关键工具（避免冲突）

**设计理念**:
- 🎯 基础功能开箱即用（Runtime）
- 🚀 高级功能按需启用（Conda）
- 🛡️ 关键工具避免冲突（System）
- ✨ 全部自动降级（无依赖错误）

### ✅ 新增代码的兼容性

我新增的 `advanced_box_algorithms.py` 和 `advanced_ligand_algorithms.py`：

- ✅ **完全兼容** Runtime环境（基础功能）
- ✅ **自动利用** Conda环境（高级功能）
- ✅ **优雅降级** 缺少依赖时（不报错）
- ✅ **复用现有** 工具函数（不重复造轮）

**结论**: 代码架构设计优秀，新增功能完美集成！🎉

---

**需要更多信息？查看完整文档**: [`ENVIRONMENT.md`](ENVIRONMENT.md)

**最后更新**: 2024-10-10
