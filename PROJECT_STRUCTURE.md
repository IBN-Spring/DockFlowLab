# DockFlowLab 项目结构规范

## 当前版本
- **Version**: 1.1.0-beta
- **Last Updated**: 2024-10-10
- **Target**: 可分发的专业分子对接软件

---

## 目录结构

```
DockFlowLab/
├── README.md                    # 项目主文档
├── LICENSE                      # 开源许可证（待添加）
├── setup.py                     # 安装脚本（待添加）
├── requirements.txt             # 依赖清单（待完善）
├── requirements-dev.txt         # 开发依赖（待添加）
├── .gitignore                   # Git忽略规则
├── CHANGELOG.md                 # 版本更新日志（待添加）
├── CONTRIBUTING.md              # 贡献指南（待添加）
│
├── src/                         # 源代码
│   ├── __init__.py
│   ├── __version__.py          # 版本信息（待添加）
│   │
│   ├── core/                    # ✅ 核心功能模块
│   │   ├── __init__.py
│   │   ├── config.py           # ✅ 配置管理
│   │   ├── logger.py           # ✅ 日志系统
│   │   ├── utils.py            # ✅ 工具函数
│   │   ├── downloader.py       # ✅ 分子下载
│   │   ├── receptor_processor.py  # ✅ 受体预处理
│   │   ├── ligand_processor.py    # ✅ 配体预处理
│   │   ├── box_generator.py       # ✅ 对接盒子生成（已修复）
│   │   ├── docking.py             # ✅ 分子对接（已修复）
│   │   ├── analyzer.py            # ✅ 结果分析
│   │   ├── pymol_generator.py     # ✅ PyMOL脚本生成
│   │   ├── report_generator.py    # ✅ 报告生成
│   │   ├── pipeline.py            # ✅ 流程管道
│   │   ├── advanced_box_algorithms.py      # ✅ 高级盒子算法（新增）
│   │   └── advanced_ligand_algorithms.py   # ✅ 高级配体算法（新增）
│   │
│   ├── utils/                   # ✅ 工具模块
│   │   ├── __init__.py
│   │   ├── path.py             # ✅ 路径管理
│   │   └── exceptions.py       # ⚠️ 异常定义（待完善）
│   │
│   ├── gui/                     # GUI界面（如有）
│   │   ├── __init__.py
│   │   └── main_window.py      # 主窗口
│   │
│   └── cli/                     # 命令行接口（待规范）
│       ├── __init__.py
│       └── commands.py         # CLI命令
│
├── tests/                       # ⚠️ 测试套件（待建立）
│   ├── __init__.py
│   ├── conftest.py             # pytest配置
│   ├── test_core/              # 核心模块测试
│   │   ├── test_box_generator.py
│   │   ├── test_docking.py
│   │   └── test_ligand_processor.py
│   ├── test_integration/       # 集成测试
│   │   └── test_full_pipeline.py
│   └── test_data/              # 测试数据
│       ├── receptors/
│       └── ligands/
│
├── docs/                        # ⚠️ 文档（待建立）
│   ├── index.md                # 文档首页
│   ├── installation.md         # 安装指南
│   ├── user_guide/             # 用户手册
│   │   ├── quickstart.md       # 快速开始
│   │   ├── basic_usage.md      # 基础使用
│   │   ├── advanced.md         # 高级功能
│   │   └── troubleshooting.md  # 故障排除
│   ├── api_reference/          # API文档
│   │   └── core.md
│   └── developer_guide/        # 开发者指南
│       ├── architecture.md     # 架构设计
│       └── contributing.md     # 贡献指南
│
├── data/                        # 数据文件
│   ├── templates/              # HTML模板
│   ├── visual/                 # PyMOL可视化模板
│   └── examples/               # 示例数据
│
├── scripts/                     # ⚠️ 实用脚本（待建立）
│   ├── build.py                # 构建脚本
│   ├── test.py                 # 测试脚本
│   └── release.py              # 发布脚本
│
├── dist/                        # 分发文件（打包后生成）
│   └── .gitkeep
│
└── tools/                       # ✅ 外部工具二进制
    ├── obabel.exe
    └── vina.exe
```

---

## 模块依赖关系

```
┌─────────────────────────────────────────────────────┐
│                    main.py / GUI                    │
└────────────────────┬────────────────────────────────┘
                     │
           ┌─────────▼──────────┐
           │   pipeline.py      │  # 流程编排
           └─────────┬──────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐
   │downloader│ │processor│ │ docking │
   └────┬────┘ └────┬────┘ └────┬────┘
        │           │           │
        └───────────┼───────────┘
                    ▼
            ┌───────────────┐
            │ config, logger │  # 基础设施
            └───────────────┘
```

---

## 代码规范

### Python 版本
- **最低要求**: Python 3.8
- **推荐版本**: Python 3.10+
- **类型注解**: 使用 typing 模块

### 编码风格
- **PEP 8**: 严格遵守
- **行长度**: 最大100字符
- **缩进**: 4空格
- **引号**: 双引号优先
- **命名**:
  - 模块/包: `lowercase_with_underscores`
  - 类: `CapitalizedWords`
  - 函数/变量: `lowercase_with_underscores`
  - 常量: `UPPERCASE_WITH_UNDERSCORES`
  - 私有: `_leading_underscore`

### 文档规范
```python
def function_name(param1: str, param2: int = 0) -> bool:
    """简短描述（一行）
    
    详细描述（如需要）
    
    Args:
        param1: 参数1的描述
        param2: 参数2的描述，默认值0
    
    Returns:
        返回值描述
    
    Raises:
        ValueError: 何时抛出此异常
    
    Examples:
        >>> function_name("test", 5)
        True
    """
    pass
```

---

## 版本管理

### 语义化版本
格式: `MAJOR.MINOR.PATCH[-LABEL]`

- **MAJOR**: 不兼容的API变更
- **MINOR**: 向后兼容的功能新增
- **PATCH**: 向后兼容的问题修复
- **LABEL**: alpha, beta, rc1 等

示例:
- `1.0.0`: 首个正式版
- `1.1.0-beta`: 1.1版本的beta测试版
- `1.1.1`: 1.1版本的修复版

### Git 分支策略
```
main (生产)
  ├── develop (开发)
  │   ├── feature/box-algorithm-v2
  │   ├── feature/gpu-acceleration
  │   └── bugfix/memory-leak
  └── hotfix/critical-bug
```

### Commit 消息规范
```
<type>(<scope>): <subject>

<body>

<footer>
```

类型:
- `feat`: 新功能
- `fix`: 修复bug
- `docs`: 文档
- `style`: 格式
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具

示例:
```
feat(box_generator): 添加fpocket口袋检测支持

- 集成fpocket算法
- 添加多级回退机制
- 提升准确度30%

Closes #123
```

---

## 质量保证

### 测试覆盖率目标
- **核心模块**: ≥ 80%
- **工具函数**: ≥ 90%
- **关键路径**: 100%

### CI/CD 流程
1. 代码提交 → Git Hook检查格式
2. 推送到develop → 自动运行测试
3. Pull Request → 代码审查 + 测试
4. 合并到main → 自动构建 + 发布

### 性能基准
- 单个对接任务: < 5分钟
- 100个配体批处理: < 2小时
- 内存占用: < 4GB

---

## 依赖管理

### 核心依赖（必需）
```
numpy >= 1.20.0
pandas >= 1.3.0
```

### 可选依赖
```
# 高级算法
scikit-learn >= 1.0.0  # DBSCAN聚类
scipy >= 1.7.0         # 凸包算法
rdkit >= 2022.3.1      # 高级构象生成

# GUI
PyQt5 >= 5.15.0        # 如果有GUI

# 开发工具
pytest >= 7.0.0
pytest-cov >= 3.0.0
black >= 22.0.0
mypy >= 0.950
flake8 >= 4.0.0
```

---

## 许可证信息

### 代码许可
**待定** - 建议使用:
- MIT License (宽松)
- Apache 2.0 (专利保护)
- GPL v3 (强制开源)

### 第三方依赖
- AutoDock Vina: Apache 2.0
- OpenBabel: GPL v2
- NumPy: BSD
- Pandas: BSD

---

## 发布检查清单

### 发布前
- [ ] 所有测试通过
- [ ] 代码覆盖率达标
- [ ] 文档已更新
- [ ] CHANGELOG.md已更新
- [ ] 版本号已更新
- [ ] 依赖版本已固定
- [ ] 安全漏洞扫描通过

### 发布包含
- [ ] Windows可执行文件 (.exe)
- [ ] Linux包 (.deb, .rpm)
- [ ] macOS包 (.dmg)
- [ ] 源码包 (.tar.gz, .zip)
- [ ] 用户手册 (PDF)
- [ ] 示例数据

### 发布后
- [ ] 创建Git标签
- [ ] 发布到GitHub Release
- [ ] 更新官网（如有）
- [ ] 通知用户

---

## 路线图

### v1.1.0 (当前) - 稳定性修复
- [x] 修复核心bug
- [x] 添加高级算法
- [ ] 完善文档
- [ ] 添加测试

### v1.2.0 - 用户体验
- [ ] GUI改进
- [ ] 进度可视化
- [ ] 批量操作优化
- [ ] 结果导出增强

### v1.3.0 - 性能优化
- [ ] GPU加速支持
- [ ] 并行化改进
- [ ] 内存优化
- [ ] 缓存机制

### v2.0.0 - 重大升级
- [ ] 插件系统
- [ ] 云端计算支持
- [ ] 机器学习集成
- [ ] API服务

---

## 贡献指南

### 如何贡献
1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

### 代码审查标准
- 遵守代码规范
- 添加单元测试
- 更新相关文档
- 通过CI检查

---

**最后更新**: 2024-10-10
**维护者**: DockFlowLab Team
