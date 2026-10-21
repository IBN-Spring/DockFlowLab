"""界面主题：配色、字体与全局样式表。

界面的基准字号按“逻辑像素”设计。Qt 已经会按系统缩放放大逻辑像素，
但在 100% 缩放的高分辨率笔记本上，14px 的文字物理尺寸仍然偏小。
``apply_theme`` 会根据屏幕的实际像素密度计算一个 1.0–1.3 的附加缩放，
并作用于全局样式表和内联字号，避免用户在笔记本上看到过小的文字。
"""

from __future__ import annotations

import math
import os
import re

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

from ..tools.runtime import resource_path

# 参考密度：普通 24 英寸 1080p 屏幕约为 92 DPI，常见的舒适区间在
# 105–120 DPI 之间。只在高于参考值时放大，不主动缩小界面。
REFERENCE_DPI = 105.0
MAX_UI_SCALE = 1.3
MIN_UI_SCALE = 1.0

_ui_scale = 1.0

# 与报告、图表共用同一套色板，避免界面与结果“两种风格”
BG = "#f4f3ef"
SURFACE = "#ffffff"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
BORDER = "#e1e0d9"
BASELINE = "#c3c2b7"
ACCENT = "#2a78d6"
ACCENT_HOVER = "#256abf"
ACCENT_DARK = "#0d366b"
ACCENT_SOFT = "#eaf2fd"
SIDEBAR = "#0e2a4d"
SIDEBAR_TEXT = "#c9d8ea"
SUCCESS = "#1f6b3a"
SUCCESS_BG = "#e8f5ec"
WARNING = "#8a5a00"
WARNING_BG = "#fff4d6"
DANGER = "#b3261e"
DANGER_BG = "#fdecea"

CJK_FONTS = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
)
MONO_FONTS = ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono")


def choose_font(candidates: tuple[str, ...], fallback: str = "") -> str:
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return fallback


def _clamp_scale(value: float) -> float:
    if not math.isfinite(value):
        return MIN_UI_SCALE
    return max(MIN_UI_SCALE, min(MAX_UI_SCALE, value))


def detect_ui_scale() -> float:
    """根据主屏的像素密度估算附加缩放。

    ``physicalDotsPerInch()`` 返回的是已经折算过系统缩放的实际密度：
    1920×1080 的普通显示器约为 92，15 英寸 1080p 笔记本在 100% 缩放下
    约为 140，在 150% 缩放下约为 94。因此它比单纯看分辨率更可靠。

    可用环境变量 ``DOCKFLOWLAB_UI_SCALE`` 覆盖自动结果，便于排查显示问题。
    """
    override = os.environ.get("DOCKFLOWLAB_UI_SCALE", "").strip()
    if override:
        try:
            return _clamp_scale(float(override))
        except ValueError:
            pass
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return MIN_UI_SCALE
    try:
        dpi = float(screen.physicalDotsPerInch())
    except Exception:
        return MIN_UI_SCALE
    if not math.isfinite(dpi) or dpi <= 0:
        return MIN_UI_SCALE
    return _clamp_scale(dpi / REFERENCE_DPI)


def set_ui_scale(scale: float) -> None:
    global _ui_scale
    _ui_scale = _clamp_scale(float(scale))


def ui_scale() -> float:
    return _ui_scale


def px(value: float) -> str:
    """把设计稿中的像素值换算成当前界面缩放下的 QSS 值。"""
    return f"{max(1, round(value * _ui_scale))}px"


_FONT_SIZE_RE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")


def _scale_qss_fonts(text: str) -> str:
    if abs(_ui_scale - 1.0) < 1e-6:
        return text
    return _FONT_SIZE_RE.sub(
        lambda match: f"font-size: {max(1, round(float(match.group(1)) * _ui_scale))}px",
        text,
    )


def apply_theme(app: QApplication, scale: float | None = None) -> float:
    """应用样式并返回实际使用的界面缩放。"""
    set_ui_scale(detect_ui_scale() if scale is None else scale)
    app.setStyle("Fusion")
    family = choose_font(CJK_FONTS, "Sans Serif")
    font = QFont(family)
    font.setPointSizeF(10.0 * _ui_scale)
    app.setFont(font)
    app.setStyleSheet(_scale_qss_fonts(_qss()))
    return _ui_scale


def _qss() -> str:
    check_icon = resource_path("check.png").as_posix()
    chevron_icon = resource_path("chevron-down.png").as_posix()
    return f"""
    QWidget {{
        color: {INK};
        font-size: 14px;
    }}
    QMainWindow, QWidget#Root, QWidget#Page, QScrollArea#PageScroll, QWidget#ScrollBody {{
        background: {BG};
    }}
    QScrollArea {{ border: none; background: transparent; }}
    /* 滚动区内容默认继承系统调色板；Windows 深色模式下会变成黑底。
       设置对话框显式使用浅色背景，保证标签与输入框可读。 */
    QScrollArea#SettingsScroll, QWidget#SettingsViewport, QWidget#SettingsBody {{
        background: {BG};
    }}
    QLabel {{ background: transparent; }}

    /* 侧栏 */
    QWidget#Sidebar {{ background: {SIDEBAR}; }}
    QWidget#Sidebar QLabel {{ color: #ffffff; }}
    QLabel#Brand {{ font-size: 21px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#BrandSub {{ color: {SIDEBAR_TEXT}; font-size: 12px; }}
    QLabel#SidebarCaption {{ color: #7f9aba; font-size: 11px; letter-spacing: 1px; }}
    QLabel#SidebarProject {{ color: #ffffff; font-size: 13px; font-weight: 600; }}
    QLabel#SidebarPath {{ color: #8fa9c6; font-size: 11px; }}
    QPushButton#StepButton {{
        color: {SIDEBAR_TEXT};
        background: transparent;
        border: none;
        border-radius: 8px;
        text-align: left;
        padding: 10px 14px;
        font-size: 14px;
    }}
    QPushButton#StepButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
    QPushButton#StepButton[current="true"] {{
        background: rgba(255, 255, 255, 0.14);
        color: #ffffff;
        font-weight: 600;
    }}
    QPushButton#StepButton:disabled {{ color: #5f7694; }}
    QPushButton#SidebarButton {{
        color: #ffffff;
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 8px;
        padding: 8px 12px;
    }}
    QPushButton#SidebarButton:hover {{ background: rgba(255, 255, 255, 0.16); }}
    QFrame#SidebarDivider {{ background: rgba(255, 255, 255, 0.12); max-height: 1px; }}

    /* 页面标题 */
    QLabel#Eyebrow {{ color: {ACCENT}; font-size: 12px; font-weight: 700; }}
    QLabel#PageTitle {{ font-size: 26px; font-weight: 700; }}
    QLabel#PageSubtitle {{ color: {SECONDARY}; font-size: 13px; }}
    QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
    QLabel#CardHint {{ color: {SECONDARY}; font-size: 12px; }}
    QLabel#Muted {{ color: {MUTED}; font-size: 12px; }}
    QLabel#Warning {{ color: {WARNING}; font-size: 12px; font-weight: 600; }}
    QLabel#Metric {{ font-size: 26px; font-weight: 700; }}
    QLabel#MetricLabel {{ color: {SECONDARY}; font-size: 12px; }}

    /* 卡片 */
    QFrame#Card {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QFrame#Tile {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}
    QFrame#TileAccent {{
        background: {ACCENT_SOFT};
        border: 1px solid #c9defa;
        border-radius: 10px;
    }}
    QFrame#DropZone {{
        background: #fbfbf9;
        border: 1.5px dashed {BASELINE};
        border-radius: 10px;
    }}
    QFrame#DropZone[dragging="true"] {{
        background: {ACCENT_SOFT};
        border: 1.5px dashed {ACCENT};
    }}
    QFrame#EmptyState {{
        background: #fbfbf9;
        border: 1px dashed {BASELINE};
        border-radius: 10px;
    }}
    QFrame#EntityRow, QFrame#BoxRow {{
        background: transparent;
        border: none;
        border-bottom: 1px solid #f0efe9;
    }}

    /* 按钮 */
    QPushButton {{
        background: {SURFACE};
        border: 1px solid #cfcec6;
        border-radius: 8px;
        padding: 8px 14px;
        min-height: 20px;
    }}
    QPushButton:hover {{ background: #f7f6f2; border-color: {BASELINE}; }}
    QPushButton:pressed {{ background: #efeee8; }}
    QPushButton:disabled {{
        color: #a8a7a0;
        background: #f3f2ee;
        border-color: {BORDER};
    }}
    QPushButton#Primary {{
        background: {ACCENT};
        color: #ffffff;
        border: none;
        font-weight: 600;
        padding: 10px 20px;
    }}
    QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#Primary:disabled {{ background: #b9cfe9; color: #eef5fc; }}
    QPushButton#Danger {{ color: {DANGER}; border-color: #eec9c6; background: #ffffff; }}
    QPushButton#Danger:hover {{ background: #fdf2f1; }}
    QPushButton#Link {{
        background: transparent;
        border: none;
        color: {ACCENT};
        padding: 4px 6px;
    }}
    QPushButton#Link:hover {{ color: {ACCENT_HOVER}; background: transparent; }}
    QPushButton#Small {{ padding: 4px 10px; font-size: 12px; min-height: 18px; }}
    QPushButton#Insight {{
        padding: 4px 10px;
        font-size: 12px;
        min-height: 18px;
    }}
    QPushButton#Insight:checked {{
        background: {ACCENT_SOFT};
        border-color: {ACCENT};
        color: {ACCENT_DARK};
        font-weight: 600;
    }}

    /* 输入 */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {SURFACE};
        border: 1px solid #cfcec6;
        border-radius: 8px;
        padding: 7px 10px;
        min-height: 20px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1.5px solid {ACCENT};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background: #f3f2ee;
        color: {MUTED};
    }}
    QComboBox {{ padding-right: 30px; }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 26px;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url("{chevron_icon}");
        width: 14px;
        height: 14px;
    }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        selection-background-color: {ACCENT_SOFT};
        selection-color: {INK};
        padding: 4px;
        outline: none;
    }}
    QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid #b9b8b0;
        border-radius: 4px;
        background: {SURFACE};
    }}
    QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
    QCheckBox::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
        image: url("{check_icon}");
    }}
    QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid #b9b8b0;
        border-radius: 8px;
        background: {SURFACE};
    }}
    QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
    QRadioButton::indicator:checked {{
        width: 8px;
        height: 8px;
        border: 4px solid {ACCENT};
        border-radius: 8px;
        background: {SURFACE};
    }}
    /* 数字框由程序设置 NoButtons；这里再兜底隐藏，避免出现残缺的上下箭头 */
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 0;
        height: 0;
        border: none;
    }}

    /* 进度与日志 */
    QProgressBar {{
        background: #e9e8e2;
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
    QPlainTextEdit#Log {{
        background: #fbfbf9;
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 8px;
        color: {SECONDARY};
    }}
    QLabel#ImagePreview, QLabel#HeatmapPreview {{
        background: #fbfbf9;
        border: 1px solid {BORDER};
        border-radius: 10px;
        color: {MUTED};
    }}
    QListWidget#StyleList {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 5px;
        outline: none;
    }}
    QListWidget#StyleList::item {{
        padding: 6px 8px;
        border-radius: 5px;
    }}
    QListWidget#StyleList::item:hover {{ background: #f7f6f2; }}
    QListWidget#StyleList::item:selected {{
        background: {ACCENT_SOFT};
        color: {INK};
    }}

    /* 表格 */
    QTableWidget {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        gridline-color: transparent;
        selection-background-color: {ACCENT_SOFT};
        selection-color: {INK};
        alternate-background-color: #fcfcfa;
    }}
    QTableWidget::item {{ padding: 7px 8px; border-bottom: 1px solid #f0efe9; }}
    QTableWidget::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
    QHeaderView::section {{
        background: #f6f6f3;
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 8px 10px;
        color: {SECONDARY};
        font-weight: 600;
        font-size: 12px;
    }}
    QTableCornerButton::section {{ background: #f6f6f3; border: none; }}

    /* 滚动条 */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #d3d2ca;
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {BASELINE}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #d3d2ca; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QToolTip {{
        background: #1f2937;
        color: #ffffff;
        border: none;
        padding: 6px 8px;
    }}
    QDialog {{ background: {BG}; }}
    QStatusBar {{
        background: {BG};
        color: {SECONDARY};
        border-top: 1px solid {BORDER};
        font-size: 12px;
    }}
    QStatusBar::item {{ border: none; }}
    """
