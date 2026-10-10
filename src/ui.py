#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分子对接工具 - 简约UI界面
PyQt5 灰白色主题
"""
import sys
import os

# 解决 Qt 平台插件路径问题
try:
    import PyQt5

    dirname = os.path.dirname(PyQt5.__file__)
    plugin_path = os.path.join(dirname, 'Qt5', 'plugins', 'platforms')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path
except Exception:
    pass

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QGroupBox, QSplitter, QMessageBox, QProgressBar,
    QFrame, QDialog, QLineEdit, QFileDialog, QInputDialog
)

def QInputDialog_getItem(parent, title, label, items, current=0, editable=False):
    return QInputDialog.getItem(parent, title, label, items, current, editable)

from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QMetaObject, Q_ARG
from PyQt5.QtGui import QFont

# 导入路径工具
from src.utils.path import get_path
from src.utils.Rexecutor import EncodedRexecutor

# 导入核心功能
try:
    from src.core import (
        CONFIG,
        initialize_directories,
        download_ligands_from_list,
        download_receptors_from_list,
        get_conda_env,
    )
    # 可选模块（可能尚未就绪）
    try:
        from src.core import process_all_receptors
    except ImportError:
        process_all_receptors = None
    try:
        from src.core import process_all_ligands
    except ImportError:
        process_all_ligands = None
    try:
        from src.core import generate_all_docking_boxes
    except ImportError:
        generate_all_docking_boxes = None
    try:
        from src.core import run_docking
    except ImportError:
        run_docking = None
    try:
        from src.core import convert_pdbqt_to_pdb
    except ImportError:
        convert_pdbqt_to_pdb = None
    try:
        from src.core import analyze_results
    except ImportError:
        analyze_results = None
    try:
        from src.core import generate_all_pymol_scripts
    except ImportError:
        generate_all_pymol_scripts = None
    try:
        from src.core import generate_paper_content
    except ImportError:
        generate_paper_content = None

    CORE_AVAILABLE = True
except ImportError as e:
    print(f"警告: 无法导入核心模块 - {e}")
    import traceback; traceback.print_exc()
    CORE_AVAILABLE = False


# ==========================================
# 工作线程
# ==========================================
class TaskWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool)

    def __init__(self, task_func, task_name="Task"):
        super().__init__()
        self.task_func = task_func
        self.task_name = task_name

    def run(self):
        try:
            self.log_signal.emit(f"▶ 开始: {self.task_name}")
            result = self.task_func()
            if result:
                self.log_signal.emit(f"✓ 完成: {self.task_name}")
            else:
                self.log_signal.emit(f"✗ 失败: {self.task_name}")
            self.finished_signal.emit(result if result else False)
        except Exception as e:
            self.log_signal.emit(f"✗ 错误: {str(e)}")
            self.finished_signal.emit(False)


# ==========================================
# 主界面
# ==========================================
class DockingMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DockFlowLab v3.1")
        self.resize(1400, 750)  # 加宽窗口以适应三栏布局
        self.worker = None

        # 创建中央容器
        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # 顶部工具栏
        toolbar = self.create_toolbar()
        central_layout.addWidget(toolbar)

        # 主内容区
        content = self.create_content()
        central_layout.addWidget(content)

        self.setCentralWidget(central_widget)
        self.setup_styles()

        # 初始化
        self.log_message("[系统] 分子对接工具已启动")
        if CORE_AVAILABLE:
            from src.core.logger import set_log_callback
            set_log_callback(self._core_log_callback)
            self.run_task(initialize_directories, "初始化目录")
        else:
            self.log_message("[警告] 核心模块未加载，部分功能不可用")

    def create_toolbar(self):
        """创建顶部工具栏"""
        toolbar = QWidget()
        toolbar.setObjectName("top_toolbar")
        toolbar.setFixedHeight(56)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(24, 0, 24, 0)

        # 标题
        title = QLabel("DockFlowLab")
        title.setObjectName("app_title")
        layout.addWidget(title)
        layout.addStretch()

        # 状态指示
        self.status_label = QLabel("● 就绪")
        self.status_label.setObjectName("status_label")
        layout.addWidget(self.status_label)

        return toolbar

    def create_content(self):
        """创建主内容区 - 三栏布局"""
        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)

        # 左侧面板 - 工作流程和快捷操作
        left_panel = self.create_left_panel()

        # 中间面板 - 日志输出
        middle_panel = self.create_middle_panel()

        # 右侧面板 - 分子列表输入
        right_panel = self.create_right_panel()

        # 分割器 - 三栏布局
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(middle_panel)
        splitter.addWidget(right_panel)

        # 设置三栏的初始比例 (左:中:右 = 3:4:3)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)

        layout.addWidget(splitter)
        return content

    def create_left_panel(self):
        """创建左侧功能面板 - 工作流程和快捷操作"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # 工作流程组
        workflow_group = QGroupBox("工作流程")
        workflow_layout = QVBoxLayout(workflow_group)
        workflow_layout.setContentsMargins(20, 24, 20, 20)
        workflow_layout.setSpacing(12)

        # 流程按钮
        workflow_buttons = [
            ("1. 下载配体与受体", self.download_molecules, "从数据库下载配体和受体"),
            ("2. 预处理受体", self.preprocess_receptors, "修复结构并转换格式"),
            ("3. 预处理配体", self.preprocess_ligands, "添加氢原子并转换格式"),
            ("4. 生成对接盒子", self.generate_boxes, "计算对接区域参数"),
            ("5. 执行分子对接", self.run_docking_task, "运行AutoDock Vina"),
            ("6. 分析结果", self.analyze_task, "生成统计数据和热图"),
            ("7. 生成实验报告", self.generate_report, "生成HTML格式实验报告"),
        ]

        for text, callback, tooltip in workflow_buttons:
            btn = QPushButton(text)
            btn.setObjectName("workflow_btn")
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(42)
            btn.clicked.connect(callback)
            workflow_layout.addWidget(btn)

        layout.addWidget(workflow_group)

        # 快捷操作组
        quick_group = QGroupBox("快捷操作")
        quick_layout = QVBoxLayout(quick_group)
        quick_layout.setContentsMargins(20, 24, 20, 20)
        quick_layout.setSpacing(12)

        # 一键执行按钮
        self.full_btn = QPushButton("▶ 一键执行完整流程")
        self.full_btn.setObjectName("primary_btn")
        self.full_btn.setCursor(Qt.PointingHandCursor)
        self.full_btn.setFixedHeight(48)
        self.full_btn.clicked.connect(self.run_full_pipeline)
        quick_layout.addWidget(self.full_btn)

        # 其他快捷按钮
        quick_buttons = [
            ("打开结果目录", self.open_results_dir),
            ("盒子选择器", self.open_box_selector),
            ("启动 PyMOL", self.open_pymol),
            ("设置", self.open_settings),
        ]

        for text, callback in quick_buttons:
            btn = QPushButton(text)
            btn.setObjectName("secondary_btn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(36)
            btn.clicked.connect(callback)
            quick_layout.addWidget(btn)

        layout.addWidget(quick_group)
        layout.addStretch()

        return panel

    def create_middle_panel(self):
        """创建中间日志面板"""
        panel = QGroupBox("运行日志")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(12)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setObjectName("log_area")
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progress_bar")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        return panel

    def create_right_panel(self):
        """创建右侧分子列表输入面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # 分子列表输入组
        molecule_group = QGroupBox("分子列表输入")
        molecule_layout = QVBoxLayout(molecule_group)
        molecule_layout.setContentsMargins(20, 24, 20, 20)
        molecule_layout.setSpacing(12)

        # 配体输入框（自动检测：纯数字→CID，含字母→PDB ID）
        lig_label = QLabel("配体列表 (每行一个，自动识别CID/PDB):")
        lig_label.setObjectName("input_label")
        molecule_layout.addWidget(lig_label)

        self.lig_input = QTextEdit()
        self.lig_input.setPlaceholderText("纯数字走CID查询，含字母走PDB查询\n例如：\n2244\n508\n1768")
        self.lig_input.setMinimumHeight(120)
        molecule_layout.addWidget(self.lig_input)

        # 受体输入框（自动检测：纯数字→CID，含字母→PDB ID）
        rec_label = QLabel("受体列表 (每行一个，自动识别CID/PDB):")
        rec_label.setObjectName("input_label")
        molecule_layout.addWidget(rec_label)

        self.rec_input = QTextEdit()
        self.rec_input.setPlaceholderText("纯数字走CID查询，含字母走PDB查询\n例如：\n1TIM\n4GCR\n2GS6")
        self.rec_input.setMinimumHeight(120)
        molecule_layout.addWidget(self.rec_input)

        # 保存按钮
        self.save_list_btn = QPushButton("💾 保存分子&蛋白列表")
        self.save_list_btn.setObjectName("primary_btn")
        self.save_list_btn.setCursor(Qt.PointingHandCursor)
        self.save_list_btn.setFixedHeight(42)
        self.save_list_btn.clicked.connect(self.save_molecule_lists)
        molecule_layout.addWidget(self.save_list_btn)

        layout.addWidget(molecule_group)
        layout.addStretch()

        return panel

    # ==========================================
    # 日志功能
    # ==========================================
    def _core_log_callback(self, msg):
        """核心模块日志回调，线程安全地转发到 UI"""
        QMetaObject.invokeMethod(
            self, "_on_core_log",
            Qt.QueuedConnection,
            Q_ARG(str, msg)
        )

    @pyqtSlot(str)
    def _on_core_log(self, msg):
        self.log_message(msg)

    def log_message(self, msg):
        """添加日志消息"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        # 核心模块日志已含时间戳，避免重复加
        if msg.startswith("[20") and "] [" in msg[:25]:
            self.log_text.append(msg)
        else:
            self.log_text.append(f"[{timestamp}] {msg}")
        # 滚动到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.End)
        self.log_text.setTextCursor(cursor)

    def set_status(self, text, running=False):
        """设置状态"""
        if running:
            self.status_label.setText(f"● {text}")
            self.status_label.setStyleSheet("color: #D4A05C;")
        else:
            self.status_label.setText(f"● {text}")
            self.status_label.setStyleSheet("color: #5C9E5C;")

    def save_molecule_lists(self):
        """保存配体和受体列表到文件，自动识别CID（纯数字）和PDB ID（含字母）"""
        try:
            lig_text = self.lig_input.toPlainText().strip()
            rec_text = self.rec_input.toPlainText().strip()

            if not lig_text and not rec_text:
                QMessageBox.warning(self, "警告", "请至少输入配体或受体列表")
                return

            lig_saved = 0
            rec_saved = 0

            if lig_text:
                items = [ln.strip() for ln in lig_text.splitlines() if ln.strip()]
                ligand_file_path = get_path("Ligand_list.txt")
                with open(ligand_file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(items) + '\n')
                lig_saved = len(items)
                cid_count = sum(1 for i in items if i.isdigit())
                pdb_count = len(items) - cid_count
                self.log_message(f"[系统] 已保存配体列表: {cid_count}个CID + {pdb_count}个PDB → {ligand_file_path}")

            if rec_text:
                items = [ln.strip() for ln in rec_text.splitlines() if ln.strip()]
                receptor_file_path = get_path("Receptor_list.txt")
                with open(receptor_file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(items) + '\n')
                rec_saved = len(items)
                cid_count = sum(1 for i in items if i.isdigit())
                pdb_count = len(items) - cid_count
                self.log_message(f"[系统] 已保存受体列表: {cid_count}个CID + {pdb_count}个PDB → {receptor_file_path}")

            QMessageBox.information(self, "成功", f"已保存：配体 {lig_saved} 个 + 受体 {rec_saved} 个")

        except Exception as e:
            self.log_message(f"[错误] 保存分子列表失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"保存分子列表失败: {str(e)}")

    # ==========================================
    # 任务执行
    # ==========================================
    def run_task(self, task_func, task_name):
        """在后台线程执行任务"""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "有任务正在运行，请等待完成")
            return

        self.set_status("运行中", running=True)
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)  # 不确定进度

        self.worker = TaskWorker(task_func, task_name)
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_task_finished)
        self.worker.start()

    def on_task_finished(self, success):
        """任务完成回调"""
        self.progress_bar.hide()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        if success:
            self.set_status("就绪")
        else:
            self.set_status("就绪")

    # ==========================================
    # 功能实现
    # ==========================================
    def _check_func(self, func, name):
        """检查函数是否可用"""
        if not CORE_AVAILABLE:
            self.log_message("[错误] 核心模块未加载")
            return False
        if func is None:
            self.log_message(f"[错误] {name} 模块尚未就绪")
            return False
        return True

    def download_molecules(self):
        """下载分子"""
        if not CORE_AVAILABLE:
            self.log_message("[错误] 核心模块未加载")
            return

        def task():
            r1 = download_ligands_from_list()
            r2 = download_receptors_from_list()
            return r1 or r2

        self.run_task(task, "下载分子")

    def preprocess_receptors(self):
        """预处理受体"""
        if not self._check_func(process_all_receptors, "预处理受体"):
            return

        def task():
            env = get_conda_env()
            result = process_all_receptors(env)
            # process_all_receptors 返回 (success_count, total, failed)
            if isinstance(result, tuple):
                return result[0] > 0
            return bool(result)

        self.run_task(task, "预处理受体")

    def preprocess_ligands(self):
        """预处理配体"""
        if not self._check_func(process_all_ligands, "预处理配体"):
            return

        def task():
            result = process_all_ligands()
            if isinstance(result, tuple):
                return result[0] > 0
            return bool(result)

        self.run_task(task, "预处理配体")

    def generate_boxes(self):
        """生成对接盒子"""
        if not self._check_func(generate_all_docking_boxes, "生成对接盒子"):
            return

        def task():
            result = generate_all_docking_boxes()
            if isinstance(result, tuple):
                return result[0]
            return bool(result)

        self.run_task(task, "生成对接盒子")

    def run_docking_task(self):
        """执行分子对接（Vina CPU）"""
        if not self._check_func(run_docking, "分子对接"):
            return

        def task():
            env = get_conda_env()
            return run_docking(env)

        self.run_task(task, "分子对接")

    def analyze_task(self):
        """分析结果"""
        if not CORE_AVAILABLE:
            self.log_message("[错误] 核心模块未加载")
            return

        def task():
            env = get_conda_env()
            r1 = convert_pdbqt_to_pdb(env) if convert_pdbqt_to_pdb else True
            r2 = analyze_results() if analyze_results else True
            if generate_all_pymol_scripts:
                generate_all_pymol_scripts()

            # 生成分子对接热图
            result_dir = get_path("7.分析结果")
            csv_file = os.path.join(result_dir, "结合能汇总.csv")
            if os.path.exists(csv_file):
                self.worker.log_signal.emit("▶ 开始生成分子对接热图...")
                rexe = EncodedRexecutor()
                rexe.script_name = "分子对接热图.R"
                rexe.result_path = result_dir
                rexe.step_name = "分子对接热图"
                for msg in rexe.run():
                    self.worker.log_signal.emit(msg)
            else:
                self.worker.log_signal.emit("[跳过] 未找到结合能汇总.csv，跳过热图生成")

            return r1 and r2

        self.run_task(task, "结果分析")

    def generate_report(self):
        """生成实验报告"""
        if not self._check_func(generate_paper_content, "实验报告生成"):
            return

        def task():
            return generate_paper_content()

        self.run_task(task, "生成实验报告")

    def run_full_pipeline(self):
        """执行完整流程"""
        if not CORE_AVAILABLE:
            self.log_message("[错误] 核心模块未加载")
            return

        reply = QMessageBox.question(
            self, "确认",
            "将执行完整对接流程，可能需要较长时间。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        def task():
            env = get_conda_env()
            steps = [
                ("初始化目录", lambda: initialize_directories()),
                ("下载配体", lambda: download_ligands_from_list()),
                ("下载受体", lambda: download_receptors_from_list()),
                ("预处理受体", lambda: process_all_receptors(env) if process_all_receptors else None),
                ("预处理配体", lambda: process_all_ligands() if process_all_ligands else None),
                ("生成对接盒子", lambda: generate_all_docking_boxes() if generate_all_docking_boxes else None),
                ("执行对接", lambda: run_docking(env) if run_docking else None),
                ("转换格式", lambda: convert_pdbqt_to_pdb(env) if convert_pdbqt_to_pdb else None),
                ("分析结果", lambda: analyze_results() if analyze_results else None),
                ("生成PyMOL脚本", lambda: generate_all_pymol_scripts() if generate_all_pymol_scripts else None),
            ]

            for name, func in steps:
                self.log_message(f"\n>>> {name}")
                try:
                    func()
                except Exception as e:
                    self.log_message(f"✗ {name} 错误: {str(e)}")
            return True

        self.run_task(task, "完整流程")

    def open_settings(self):
        """打开设置对话框"""
        dlg = SettingsDialog(self)
        dlg.exec_()

    def open_box_selector(self):
        """启动对接盒子选择器（PyMOL + 交互面板）"""
        import subprocess
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pymol_exe = os.path.join(base, "runtime", "Scripts", "pymol.exe")
        plugin_script = os.path.join(base, "src", "box_selector.py")

        if not os.path.exists(pymol_exe):
            QMessageBox.critical(self, "错误", f"未找到 PyMOL:\n{pymol_exe}")
            return
        if not os.path.exists(plugin_script):
            QMessageBox.critical(self, "错误", f"未找到插件脚本:\n{plugin_script}")
            return

        # 扫描已有受体目录，让用户选择
        receptor_base = CONFIG.get("3.受体", "")
        receptor_choices = []
        if os.path.isdir(receptor_base):
            for d in sorted(os.listdir(receptor_base)):
                pdbqt = os.path.join(receptor_base, d, f"{d}.pdbqt")
                if os.path.exists(pdbqt):
                    receptor_choices.append((d, pdbqt))

        if not receptor_choices:
            # 没有预处理好的受体，让用户手动选文件
            path, _ = QFileDialog.getOpenFileName(
                self, "选择受体 PDBQT 文件", receptor_base,
                "PDBQT 文件 (*.pdbqt);;所有文件 (*)"
            )
            if not path:
                return
            receptor_path = path
            config_out = os.path.join(os.path.dirname(path), "config.txt")
        else:
            # 弹出选择对话框
            names = [f"{d}  ({pdbqt})" for d, pdbqt in receptor_choices]
            chosen, ok = QInputDialog_getItem(self, "选择受体", "选择要设置盒子的受体：", names, 0, False)
            if not ok:
                return
            idx = names.index(chosen)
            receptor_path = receptor_choices[idx][1]
            config_out = os.path.join(os.path.dirname(receptor_path), "config.txt")

        try:
            env = os.environ.copy()
            env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
            env.pop("QT_PLUGIN_PATH", None)
            subprocess.Popen(
                [pymol_exe, "-r", plugin_script, "--", receptor_path, config_out],
                cwd=os.path.dirname(pymol_exe),
                env=env
            )
            self.log_message(f"[系统] 已启动盒子选择器: {os.path.basename(receptor_path)}")
        except Exception as e:
            self.log_message(f"[错误] 启动盒子选择器失败: {str(e)}")

    def open_pymol(self):
        """启动 PyMOL"""
        import subprocess
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pymol_dir = os.path.join(base, "open-source-pymol")
        python_exe = os.path.join(pymol_dir, "python.exe")
        pymol_init = os.path.join(pymol_dir, "Lib", "site-packages", "pymol", "__init__.py")
        if not os.path.exists(python_exe) or not os.path.exists(pymol_init):
            self.log_message(f"[错误] 未找到 PyMOL 组件: {pymol_dir}")
            return
        try:
            env = os.environ.copy()
            env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
            env.pop("QT_PLUGIN_PATH", None)
            subprocess.Popen([python_exe, pymol_init], cwd=pymol_dir, env=env)
            self.log_message("[系统] 已启动 PyMOL")
        except Exception as e:
            self.log_message(f"[错误] 启动 PyMOL 失败: {str(e)}")

    def open_results_dir(self):
        """打开结果目录"""
        if not CORE_AVAILABLE:
            return

        import subprocess
        import platform

        results_dir = CONFIG.get("7.分析结果", ".")
        try:
            if platform.system() == "Windows":
                os.startfile(results_dir)
            elif platform.system() == "Darwin":
                subprocess.run(["open", results_dir])
            else:
                subprocess.run(["xdg-open", results_dir])
            self.log_message(f"[系统] 已打开: {results_dir}")
        except Exception as e:
            self.log_message(f"[错误] 打开目录失败: {str(e)}")

    # ==========================================
    # 样式设置
    # ==========================================
    def setup_styles(self):
        """设置界面样式"""
        qss = """
        QMainWindow {
            background-color: #f5f5f7;
        }

        QWidget {
            font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 13px;
            color: #333;
        }

        /* 顶部工具栏 */
        QWidget#top_toolbar {
            background-color: #ffffff;
            border-bottom: 1px solid #e0e0e0;
        }

        /* 应用标题 */
        QLabel#app_title {
            font-size: 18px;
            font-weight: 700;
            color: #2c3e50;
        }

        /* 状态标签 */
        QLabel#status_label {
            font-size: 13px;
            color: #5C9E5C;
        }

        /* 分组框 */
        QGroupBox {
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 8px;
            background-color: #ffffff;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 8px;
            left: 16px;
            font-weight: 600;
            font-size: 14px;
            color: #555;
        }

        /* 工作流程按钮 */
        QPushButton#workflow_btn {
            background-color: #ffffff;
            color: #333;
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            padding: 8px 16px;
            font-weight: 500;
            text-align: left;
            padding-left: 20px;
        }

        QPushButton#workflow_btn:hover {
            background-color: #f8f8f8;
            border-color: #b0b0b0;
        }

        QPushButton#workflow_btn:pressed {
            background-color: #f0f0f0;
        }

        /* 主要按钮 */
        QPushButton#primary_btn {
            background-color: #4a4a4a;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
        }

        QPushButton#primary_btn:hover {
            background-color: #5a5a5a;
        }

        QPushButton#primary_btn:pressed {
            background-color: #3a3a3a;
        }

        /* 次要按钮 */
        QPushButton#secondary_btn {
            background-color: #f5f5f5;
            color: #555;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            font-weight: 500;
        }

        QPushButton#secondary_btn:hover {
            background-color: #eeeeee;
            border-color: #d0d0d0;
        }

        /* 日志区域 */
        QTextEdit#log_area {
            border: none;
            background-color: #fafafa;
            color: #444;
            font-family: "Consolas", "Monaco", monospace;
            font-size: 12px;
            padding: 12px;
            border-radius: 4px;
        }

        /* 进度条 */
        QProgressBar#progress_bar {
            border: none;
            background-color: #e0e0e0;
            border-radius: 3px;
        }

        QProgressBar#progress_bar::chunk {
            background-color: #4a4a4a;
            border-radius: 3px;
        }

        /* 分割器 */
        QSplitter::handle {
            background-color: transparent;
            width: 8px;
        }

        /* 输入标签 */
        QLabel#input_label {
            font-weight: 600;
            color: #555;
            font-size: 13px;
        }
        """
        self.setStyleSheet(qss)


# ==========================================
# 主函数
# ==========================================
def main():
    app = QApplication(sys.argv)
    # 设置应用属性
    app.setApplicationName("分子对接工具")
    app.setStyle("Fusion")
    window = DockingMainWindow()
    window.show()
    sys.exit(app.exec_())


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedSize(520, 180)
        from src.utils.path import get_path as _get_path
        self._config_path = _get_path("config.ini")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ── Rscript 路径行 ──
        row = QHBoxLayout()
        row.addWidget(QLabel("Rscript.exe 路径:"))
        self.rscript_edit = QLineEdit()
        self.rscript_edit.setPlaceholderText("请选择 Rscript.exe 路径")
        row.addWidget(self.rscript_edit)
        browse_btn = QPushButton("浏览")
        browse_btn.setFixedWidth(60)
        browse_btn.clicked.connect(self._browse)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._load()

    def _load(self):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(self._config_path, encoding="utf-8")
        self.rscript_edit.setText(cfg.get("Environment", "rscript_path", fallback=""))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Rscript.exe", "", "可执行文件 (*.exe);;所有文件 (*)"
        )
        if path:
            self.rscript_edit.setText(path)

    def _save(self):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(self._config_path, encoding="utf-8")
        if "Environment" not in cfg:
            cfg["Environment"] = {}
        cfg["Environment"]["rscript_path"] = self.rscript_edit.text().strip()

        with open(self._config_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        QMessageBox.information(self, "成功", "设置已保存")
        self.accept()


if __name__ == "__main__":
    main()
