#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockFlowLab 对接盒子选择器
通过 pymol -r box_selector.py -- <pdbqt_path> [config_out_path] 启动
"""
import os
import sys

# ── PyMOL CGO 盒子绘制 ──────────────────────────────────────────────────────

def draw_box(cmd, cx, cy, cz, sx, sy, sz, name="docking_box"):
    from pymol.cgo import BEGIN, LINES, VERTEX, END, COLOR
    x1, x2 = cx - sx / 2, cx + sx / 2
    y1, y2 = cy - sy / 2, cy + sy / 2
    z1, z2 = cz - sz / 2, cz + sz / 2
    edges = [
        (x1,y1,z1),(x2,y1,z1), (x2,y1,z1),(x2,y2,z1),
        (x2,y2,z1),(x1,y2,z1), (x1,y2,z1),(x1,y1,z1),
        (x1,y1,z2),(x2,y1,z2), (x2,y1,z2),(x2,y2,z2),
        (x2,y2,z2),(x1,y2,z2), (x1,y2,z2),(x1,y1,z2),
        (x1,y1,z1),(x1,y1,z2), (x2,y1,z1),(x2,y1,z2),
        (x2,y2,z1),(x2,y2,z2), (x1,y2,z1),(x1,y2,z2),
    ]
    obj = [COLOR, 0.2, 0.8, 0.2, BEGIN, LINES]
    for v in edges:
        obj += [VERTEX] + list(v)
    obj += [END]
    cmd.delete(name)
    cmd.load_cgo(obj, name)


def write_config(path, cx, cy, cz, sx, sy, sz, receptor_path, exhaustiveness=32, num_modes=9, energy_range=3):
    from datetime import datetime
    content = f"""# 手动设置对接盒子（{datetime.now().strftime('%Y-%m-%d %H:%M')}）
receptor = {os.path.abspath(receptor_path)}
ligand = {{ligand_full_path}}
out = {{output_full_path}}
center_x = {round(cx, 3)}
center_y = {round(cy, 3)}
center_z = {round(cz, 3)}
size_x = {round(sx, 3)}
size_y = {round(sy, 3)}
size_z = {round(sz, 3)}
exhaustiveness = {exhaustiveness}
num_modes = {num_modes}
energy_range = {energy_range}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_existing_config(config_path):
    """读取已有 config.txt 中的盒子参数"""
    params = {}
    if not os.path.exists(config_path):
        return params
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.split("#")[0].strip()
                    if k in ("center_x","center_y","center_z","size_x","size_y","size_z"):
                        try:
                            params[k] = float(v)
                        except ValueError:
                            pass
    except Exception:
        pass
    return params


def estimate_center_from_pdbqt(pdbqt_path):
    """从 PDBQT 文件估算分子中心"""
    coords = []
    try:
        with open(pdbqt_path, "r", encoding="ISO-8859-1", errors="ignore") as f:
            for line in f:
                if line.startswith(("ATOM", "HETATM")):
                    try:
                        x = float(line[30:38])
                        y = float(line[38:46])
                        z = float(line[46:54])
                        coords.append((x, y, z))
                    except ValueError:
                        pass
    except Exception:
        pass
    if coords:
        cx = sum(c[0] for c in coords) / len(coords)
        cy = sum(c[1] for c in coords) / len(coords)
        cz = sum(c[2] for c in coords) / len(coords)
        return round(cx, 2), round(cy, 2), round(cz, 2)
    return 0.0, 0.0, 0.0


# ── PyMOL 插件主入口 ─────────────────────────────────────────────────────────

def launch_box_selector(receptor_path, config_out_path):
    import pymol
    from pymol import cmd as pymol_cmd

    # 解析已有盒子参数或估算中心
    existing = read_existing_config(config_out_path)
    cx = existing.get("center_x", None)
    cy = existing.get("center_y", None)
    cz = existing.get("center_z", None)
    sx = existing.get("size_x", 25.0)
    sy = existing.get("size_y", 25.0)
    sz = existing.get("size_z", 25.0)

    if cx is None:
        cx, cy, cz = estimate_center_from_pdbqt(receptor_path)

    # ── Qt GUI ──────────────────────────────────────────────────────────────
    try:
        from pymol.Qt import QtWidgets, QtCore
    except ImportError:
        from PyQt5 import QtWidgets, QtCore

    class BoxSelectorPanel(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("DockFlowLab — 对接盒子选择器")
            self.setMinimumWidth(420)
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

            layout = QtWidgets.QVBoxLayout(self)

            # 文件信息
            info = QtWidgets.QLabel(f"受体: {os.path.basename(receptor_path)}")
            info.setStyleSheet("font-weight:bold; color:#2c3e50;")
            layout.addWidget(info)

            out_info = QtWidgets.QLabel(f"输出: {config_out_path}")
            out_info.setStyleSheet("color:#666; font-size:11px;")
            out_info.setWordWrap(True)
            layout.addWidget(out_info)

            layout.addWidget(self._separator())

            # 参数控件
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(8)

            params = [
                ("center_x", cx, -999, 999, "盒子中心 X (Å)"),
                ("center_y", cy, -999, 999, "盒子中心 Y (Å)"),
                ("center_z", cz, -999, 999, "盒子中心 Z (Å)"),
                ("size_x",   sx,    5, 200, "盒子尺寸 X (Å)"),
                ("size_y",   sy,    5, 200, "盒子尺寸 Y (Å)"),
                ("size_z",   sz,    5, 200, "盒子尺寸 Z (Å)"),
            ]

            self.spins = {}
            self.sliders = {}

            for row, (key, val, vmin, vmax, label) in enumerate(params):
                lbl = QtWidgets.QLabel(label)
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(vmin, vmax)
                spin.setDecimals(2)
                spin.setSingleStep(0.5)
                spin.setValue(val)
                spin.setFixedWidth(90)

                slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                slider.setRange(int(vmin * 10), int(vmax * 10))
                slider.setValue(int(val * 10))

                # 双向绑定
                spin.valueChanged.connect(lambda v, s=slider: s.setValue(int(v * 10)))
                slider.valueChanged.connect(lambda v, sp=spin: sp.setValue(v / 10.0))

                # 任意变化都刷新盒子
                spin.valueChanged.connect(self._refresh_box)

                grid.addWidget(lbl,    row, 0)
                grid.addWidget(spin,   row, 1)
                grid.addWidget(slider, row, 2)

                self.spins[key] = spin
                self.sliders[key] = slider

            layout.addLayout(grid)
            layout.addWidget(self._separator())

            # 按钮行
            btn_row = QtWidgets.QHBoxLayout()

            self.btn_center = QtWidgets.QPushButton("重置到分子中心")
            self.btn_center.clicked.connect(self._reset_center)
            btn_row.addWidget(self.btn_center)

            btn_row.addStretch()

            self.btn_save = QtWidgets.QPushButton("💾  保存 config.txt")
            self.btn_save.setStyleSheet(
                "background:#2ecc71; color:white; font-weight:bold; padding:6px 18px; border-radius:4px;"
            )
            self.btn_save.clicked.connect(self._save)
            btn_row.addWidget(self.btn_save)

            self.btn_close = QtWidgets.QPushButton("关闭")
            self.btn_close.clicked.connect(self.close)
            btn_row.addWidget(self.btn_close)

            layout.addLayout(btn_row)

            # 状态栏
            self.status = QtWidgets.QLabel("")
            self.status.setStyleSheet("color:#27ae60; font-size:12px;")
            layout.addWidget(self.status)

            # 初始绘制
            self._refresh_box()

        def _separator(self):
            line = QtWidgets.QFrame()
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setStyleSheet("color:#ddd;")
            return line

        def _get_values(self):
            return (
                self.spins["center_x"].value(),
                self.spins["center_y"].value(),
                self.spins["center_z"].value(),
                self.spins["size_x"].value(),
                self.spins["size_y"].value(),
                self.spins["size_z"].value(),
            )

        def _refresh_box(self):
            try:
                draw_box(pymol_cmd, *self._get_values())
            except Exception:
                pass

        def _reset_center(self):
            ecx, ecy, ecz = estimate_center_from_pdbqt(receptor_path)
            self.spins["center_x"].setValue(ecx)
            self.spins["center_y"].setValue(ecy)
            self.spins["center_z"].setValue(ecz)

        def _save(self):
            try:
                vals = self._get_values()
                write_config(config_out_path, *vals, receptor_path=receptor_path)
                self.status.setText(f"✅ 已保存到 {os.path.basename(config_out_path)}")
            except Exception as e:
                self.status.setText(f"❌ 保存失败: {e}")
                self.status.setStyleSheet("color:#e74c3c; font-size:12px;")

        def closeEvent(self, event):
            try:
                pymol_cmd.delete("docking_box")
            except Exception:
                pass
            super().closeEvent(event)

    # 加载受体结构（pdbqt 格式需显式指定为 pdb，PyMOL 可正确解析）
    ext = os.path.splitext(receptor_path)[1].lower()
    fmt = "pdb" if ext == ".pdbqt" else ext.lstrip(".")
    pymol_cmd.load(receptor_path, "receptor", format=fmt)
    pymol_cmd.show("cartoon", "receptor")
    pymol_cmd.show("sticks", "hetatm")
    pymol_cmd.color("cyan", "receptor")
    pymol_cmd.orient("receptor")
    pymol_cmd.zoom("receptor", 5)

    # 弹出面板（存到全局防止被垃圾回收，PyMOL 已有 Qt 事件循环无需 exec_）
    global _box_selector_panel
    _box_selector_panel = BoxSelectorPanel()
    _box_selector_panel.show()
    _box_selector_panel.raise_()
    _box_selector_panel.activateWindow()


# ── 命令行入口（pymol -r box_selector.py -- <args>）────────────────────────

def main():
    # 支持三种启动方式：
    #   1. pymol -r box_selector.py -- receptor.pdbqt [config.txt]
    #   2. pymol 内 run box_selector.py,main  （sys.argv 是 pymol 自身参数）
    #   3. 直接 python box_selector.py receptor.pdbqt

    raw = sys.argv[:]

    # 方式1：-- 后面才是我们的参数
    if "--" in raw:
        raw = raw[raw.index("--") + 1:]
    else:
        # 过滤掉不是 .pdbqt / .txt 文件路径的参数（pymol 自身的 argv）
        raw = [a for a in raw if a.endswith((".pdbqt", ".txt")) and os.path.exists(a)]

    if not raw:
        # 没有命令行参数时弹出文件选择对话框
        try:
            from pymol.Qt import QtWidgets
        except ImportError:
            from PyQt5 import QtWidgets
        receptor_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "选择受体 PDBQT 文件", "", "PDBQT 文件 (*.pdbqt);;所有文件 (*)"
        )
        if not receptor_path:
            return
        config_out = os.path.join(os.path.dirname(receptor_path), "config.txt")
    else:
        receptor_path = os.path.abspath(raw[0])
        config_out = os.path.abspath(raw[1]) if len(raw) > 1 else \
            os.path.join(os.path.dirname(receptor_path), "config.txt")

    launch_box_selector(receptor_path, config_out)


main()
