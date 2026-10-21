# DockFlowLab 品牌与视觉素材

这个目录存放 README、文档和发布页使用的品牌素材。

## 标识

标识由两个几何元素组成：

- **D**：Dock，也代表蛋白受体的结合口袋；
- **青色六边形**：苯环/配体，嵌在 D 的口袋中，表示“配体进入结合位点”。

深海军蓝与蓝色的渐变传达可靠、冷静的科研工具气质；青色只用于配体，是整套视觉中唯一的强调色。

## 颜色

| 用途 | 色值 |
| --- | --- |
| 品牌深蓝 | `#0E2A4D` |
| 主色 / 链接 | `#2A78D6` |
| 配体青 | `#17A897` |
| 背景米白 | `#F4F3EF` |
| 正文墨色 | `#0B1B2B` |
| 次级文字 | `#52514E` |

## 字体

- 英文标识：Bahnschrift SemiBold（字距 +1.2）
- 中文标题/正文：Microsoft YaHei UI；macOS 回退 PingFang SC

## 文件

| 文件 | 用途 |
| --- | --- |
| `logo.svg` / `logo.png` | 1024×1024 应用图标，深色底 |
| `logo-512.png` / `logo-256.png` / `logo-128.png` | 常用尺寸 |
| `logo-mark-light.svg` | 浅色背景上的透明标识 |
| `logo-mark-dark.svg` | 深色背景上的透明标识 |
| `banner-light.png` | README 顶部横幅（浅色） |
| `banner-dark.png` | README 顶部横幅（深色） |
| `social-preview.png` | GitHub 仓库社交预览图（1280×640） |
| `app-icon.ico` | Windows 应用图标源文件 |
| `screenshots/01-workspace.png` | 工作目录页 |
| `screenshots/02-prepare.png` | 检查与准备页 |
| `screenshots/03-box-settings.png` | 设置对接页 |
| `screenshots/04-box-editor.png` | 3D 盒子编辑器 |
| `screenshots/05-results.png` | 运行与结果页（README 首图） |
| `screenshots/06-pymol-render.png` | PyMOL 渲染页 |
| `screenshots/07-pymol-styles.png` | 两种 PyMOL 渲染风格拼图 |

`packaging/DockFlowLab.icns` 与 `src/dockflowlab/resources/app.ico` 由这里的图标生成。

## 使用规范

- 标识最小显示尺寸 24px；小于 32px 时不要加文字。
- 留白至少为标识宽度的 1/4，不要紧贴其他元素。
- 不要拉伸、旋转、改色或给标识加投影。
- 深色背景使用 `logo-mark-dark.svg`，浅色背景使用 `logo-mark-light.svg`。
- 横幅不要二次压缩；README 中直接引用 PNG。

## 截图

截图放在 `screenshots/`，编号即 README 中的展示顺序。建议使用 1440px 以上的窗口宽度，
只截应用窗口，避免出现个人路径、用户名或无关窗口。示例截图已将项目名与路径替换为
`示例项目` / `D:\docking\demo` 等演示值；重新截图时请做同样的处理。
