"""
分子对接结合能热图生成器 (Python版本)
替代R的pheatmap/ggplot2，使用matplotlib/seaborn
轻量、快速、高度可自定义
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from typing import Optional, Tuple, List
import warnings
warnings.filterwarnings('ignore')

# 设置中文字体支持（可选）
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


class DockingHeatmapGenerator:
    """对接结果热图生成器"""

    # 预设配色方案
    COLOR_SCHEMES = {
        "classic": ["#be3354", "#f9a97a", "#f2f181"],  # R脚本原配色（红→橙→黄）
        "viridis": None,  # matplotlib默认
        "coolwarm": "coolwarm",
        "RdYlGn_r": "RdYlGn_r",  # 红-黄-绿（reversed）
        "blues": "Blues_r",
        "ocean": ["#003f5c", "#58508d", "#bc5090", "#ff6361", "#ffa600"],
        "sunset": ["#2b0a3d", "#8b3a62", "#c85a54", "#f28f3b", "#ffda77"],
    }

    def __init__(
        self,
        csv_path: str,
        output_dir: Optional[str] = None,
        color_scheme: str = "classic",
        dpi: int = 300
    ):
        """
        初始化热图生成器

        Args:
            csv_path: 结合能汇总CSV文件路径
            output_dir: 输出目录（默认与CSV同目录）
            color_scheme: 配色方案名称
            dpi: 图片分辨率
        """
        self.csv_path = Path(csv_path)
        self.output_dir = Path(output_dir) if output_dir else self.csv_path.parent
        self.dpi = dpi

        # 加载数据
        self.data = pd.read_csv(csv_path, index_col=0)
        self.n_rows, self.n_cols = self.data.shape

        # 配色
        colors = self.COLOR_SCHEMES.get(color_scheme, self.COLOR_SCHEMES["classic"])
        if isinstance(colors, list):
            from matplotlib.colors import LinearSegmentedColormap
            self.cmap = LinearSegmentedColormap.from_list("custom", colors, N=100)
        else:
            self.cmap = colors or "viridis"

        print(f"✓ 加载数据: {self.n_rows}行 × {self.n_cols}列")
        print(f"✓ 结合能范围: {self.data.min().min():.2f} ~ {self.data.max().min():.2f} kcal/mol")

    def _calculate_figure_size(
        self,
        cell_size: float = 0.6,
        min_size: Tuple[float, float] = (8, 6),
        max_size: Tuple[float, float] = (30, 25)
    ) -> Tuple[float, float]:
        """自适应计算图片尺寸"""
        width = max(min_size[0], min(self.n_cols * cell_size + 3, max_size[0]))
        height = max(min_size[1], min(self.n_rows * cell_size + 2, max_size[1]))
        return width, height

    def _calculate_font_sizes(self) -> dict:
        """自适应计算字体大小"""
        row_scale = min(1.0, 15 / self.n_rows) if self.n_rows > 15 else 1.0
        col_scale = min(1.0, 15 / self.n_cols) if self.n_cols > 15 else 1.0
        min_scale = min(row_scale, col_scale)

        return {
            "title": max(12, int(16 * min_scale)),
            "xlabel": max(8, int(12 * col_scale)),
            "ylabel": max(8, int(12 * row_scale)),
            "cell_text": max(5, int(9 * min_scale)),
            "cbar_label": max(9, int(11 * min_scale)),
            "cbar_ticks": max(8, int(10 * min_scale)),
        }

    def generate_seaborn_heatmap(
        self,
        title: str = "Molecular Docking Binding Energy Heatmap",
        show_values: bool = True,
        fmt: str = ".3f",
        cbar_label: str = "Binding Energy\n(kcal/mol)",
        output_name: str = "dock_heatmap_seaborn"
    ):
        """
        使用seaborn生成热图（推荐，最简洁）

        Args:
            title: 图表标题
            show_values: 是否显示数值
            fmt: 数值格式
            cbar_label: 色标标签
            output_name: 输出文件名（不含扩展名）
        """
        figsize = self._calculate_figure_size()
        fonts = self._calculate_font_sizes()

        fig, ax = plt.subplots(figsize=figsize)

        # 生成热图
        sns.heatmap(
            self.data,
            annot=show_values,
            fmt=fmt,
            cmap=self.cmap,
            cbar_kws={
                "label": cbar_label,
                "shrink": 0.8,
            },
            linewidths=0.5,
            linecolor='white',
            ax=ax,
            annot_kws={"size": fonts["cell_text"], "weight": "bold"}
        )

        # 样式调整
        ax.set_title(title, fontsize=fonts["title"], weight='bold', pad=20)
        ax.set_xlabel("")
        ax.set_ylabel("")

        # X轴标签（受体）
        ax.set_xticklabels(
            ax.get_xticklabels(),
            rotation=0,
            ha='center',
            fontsize=fonts["xlabel"],
            weight='bold'
        )

        # Y轴标签（配体）
        ax.set_yticklabels(
            ax.get_yticklabels(),
            rotation=0,
            fontsize=fonts["ylabel"],
            weight='bold'
        )

        # 色标字体
        cbar = ax.collections[0].colorbar
        cbar.set_label(cbar_label, fontsize=fonts["cbar_label"], weight='bold')
        cbar.ax.tick_params(labelsize=fonts["cbar_ticks"])

        plt.tight_layout()

        # 保存文件
        pdf_path = self.output_dir / f"{output_name}.pdf"
        png_path = self.output_dir / f"{output_name}.png"

        fig.savefig(pdf_path, dpi=self.dpi, bbox_inches='tight')
        fig.savefig(png_path, dpi=self.dpi, bbox_inches='tight')

        plt.close(fig)

        print(f"✓ Seaborn热图已生成:")
        print(f"  - {pdf_path}")
        print(f"  - {png_path}")

    def generate_matplotlib_heatmap(
        self,
        title: str = "Molecular Docking Binding Energy Heatmap",
        show_values: bool = True,
        fmt: str = ".3f",
        cbar_label: str = "Binding Energy (kcal/mol)",
        output_name: str = "dock_heatmap_matplotlib",
        square_cells: bool = True
    ):
        """
        使用纯matplotlib生成热图（更灵活，完全自定义）

        Args:
            title: 图表标题
            show_values: 是否显示数值
            fmt: 数值格式
            cbar_label: 色标标签
            output_name: 输出文件名
            square_cells: 是否强制正方形单元格
        """
        fonts = self._calculate_font_sizes()

        # 计算图片尺寸
        if square_cells:
            cell_size = 0.5
            width = self.n_cols * cell_size + 3
            height = self.n_rows * cell_size + 2
            figsize = (width, height)
        else:
            figsize = self._calculate_figure_size()

        fig, ax = plt.subplots(figsize=figsize)

        # 绘制热图
        im = ax.imshow(self.data.values, cmap=self.cmap, aspect='auto')

        # 色标
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label(cbar_label, fontsize=fonts["cbar_label"], weight='bold')
        cbar.ax.tick_params(labelsize=fonts["cbar_ticks"])

        # 设置坐标轴
        ax.set_xticks(np.arange(self.n_cols))
        ax.set_yticks(np.arange(self.n_rows))
        ax.set_xticklabels(self.data.columns, fontsize=fonts["xlabel"], weight='bold')
        ax.set_yticklabels(self.data.index, fontsize=fonts["ylabel"], weight='bold')

        # X轴标签位置
        ax.tick_params(top=False, bottom=True, labeltop=False, labelbottom=True)

        # 旋转X轴标签
        plt.setp(ax.get_xticklabels(), rotation=0, ha="center")

        # 显示数值
        if show_values:
            for i in range(self.n_rows):
                for j in range(self.n_cols):
                    value = self.data.iloc[i, j]
                    text = ax.text(
                        j, i, f"{value:{fmt}}",
                        ha="center", va="center",
                        color="black",
                        fontsize=fonts["cell_text"],
                        weight="bold"
                    )

        # 标题
        ax.set_title(title, fontsize=fonts["title"], weight='bold', pad=20)

        # 网格线
        ax.set_xticks(np.arange(self.n_cols + 1) - 0.5, minor=True)
        ax.set_yticks(np.arange(self.n_rows + 1) - 0.5, minor=True)
        ax.grid(which="minor", color="white", linestyle='-', linewidth=1.5)
        ax.tick_params(which="minor", size=0)

        plt.tight_layout()

        # 保存
        pdf_path = self.output_dir / f"{output_name}.pdf"
        png_path = self.output_dir / f"{output_name}.png"

        fig.savefig(pdf_path, dpi=self.dpi, bbox_inches='tight')
        fig.savefig(png_path, dpi=self.dpi, bbox_inches='tight')

        plt.close(fig)

        print(f"✓ Matplotlib热图已生成:")
        print(f"  - {pdf_path}")
        print(f"  - {png_path}")

    def generate_all(self):
        """生成所有风格的热图"""
        print("\n" + "=" * 70)
        print("生成分子对接结合能热图")
        print("=" * 70)

        # Seaborn版本（推荐）
        self.generate_seaborn_heatmap()

        # Matplotlib版本
        self.generate_matplotlib_heatmap()

        print("\n✓ 所有热图生成完成！")
        print(f"输出目录: {self.output_dir}")


def generate_docking_heatmap(
    csv_path: str,
    output_dir: Optional[str] = None,
    color_scheme: str = "classic",
    dpi: int = 300,
    backend: str = "both"
):
    """
    便捷函数：生成对接热图

    Args:
        csv_path: 结合能CSV文件路径
        output_dir: 输出目录
        color_scheme: 配色方案 ("classic", "viridis", "coolwarm", "ocean", "sunset")
        dpi: 分辨率
        backend: 生成器类型 ("seaborn", "matplotlib", "both")

    Example:
        >>> generate_docking_heatmap(
        ...     "结合能汇总.csv",
        ...     color_scheme="ocean",
        ...     dpi=300
        ... )
    """
    generator = DockingHeatmapGenerator(csv_path, output_dir, color_scheme, dpi)

    if backend == "seaborn":
        generator.generate_seaborn_heatmap()
    elif backend == "matplotlib":
        generator.generate_matplotlib_heatmap()
    else:
        generator.generate_all()


# 命令行入口
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python heatmap_generator.py <结合能CSV文件> [配色方案] [DPI]")
        print("配色方案: classic(默认), viridis, coolwarm, ocean, sunset")
        print("示例: python heatmap_generator.py 结合能汇总.csv ocean 300")
        sys.exit(1)

    csv_file = sys.argv[1]
    color = sys.argv[2] if len(sys.argv) > 2 else "classic"
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    generate_docking_heatmap(csv_file, color_scheme=color, dpi=dpi)
