import sys
import os
import PyQt5
try:
    dirname = os.path.dirname(PyQt5.__file__)
    plugin_path = os.path.join(dirname, 'Qt5', 'plugins', 'platforms')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path
except Exception:
    pass

from PyQt5.QtWidgets import QApplication, QDialog, QHBoxLayout, QVBoxLayout, QWidget, QLabel, QPushButton, QMessageBox, QSplashScreen, QTextEdit, QStackedWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QClipboard, QPixmap, QPainter

from src.utils.path import get_path
from src.Authentication.Authetication import LicenseGuard


class ActivationWindow(QDialog):
    """未激活时的机器码展示窗口"""
    def __init__(self,guard = None):
        super().__init__()
        if guard is not None:
            self.license_guard = guard
        else:
            self.license_guard = LicenseGuard()
        self.init_ui()
        self.show()

        from PyQt5.QtGui import QIcon
        import os
        icon_path = get_path("logo.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def init_ui(self):
        """初始化界面"""
        # 设置窗口属性
        self.setWindowTitle("软件未激活")
        self.setFixedSize(500, 400)  # 增大窗口尺寸以适应激活码输入界面
        # 设置窗口总是显示在最顶层
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint | Qt.Dialog)
        self.setModal(True)  # 设置为模态窗口
        self.raise_()  # 确保窗口在最前面
        self.activateWindow()  # 激活窗口

        # 创建主布局
        main_layout = QVBoxLayout()
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 创建QStackedWidget来管理两个页面
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)
        
        # 创建机器码显示页面
        self.create_machine_code_page()
        
        # 创建激活码输入页面
        self.create_license_input_page()
        
        # 设置主布局
        self.setLayout(main_layout)
    
    def create_machine_code_page(self):
        """创建机器码显示页面"""
        # 创建页面容器
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # 创建标题
        title = QLabel("软件未激活")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 创建提示信息
        info = QLabel("软件未激活或许可证文件缺失，如有需要请带着机器码联系我们")
        info.setFont(QFont("Microsoft YaHei", 11))
        info.setAlignment(Qt.AlignCenter)
        info.setWordWrap(True)
        layout.addWidget(info)

        # 创建机器码标签
        machine_code_label = QLabel("机器码：")
        machine_code_label.setFont(QFont("Microsoft YaHei", 12))
        machine_code_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(machine_code_label)

        # 创建机器码显示框
        machine_code = self.license_guard.machine_code
        self.machine_code_display = QLabel(machine_code)
        self.machine_code_display.setFont(QFont("Consolas", 14))
        self.machine_code_display.setAlignment(Qt.AlignCenter)
        self.machine_code_display.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                padding: 10px;
                border-radius: 5px;
                selection-background-color: #007acc;
            }
        """)
        self.machine_code_display.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.machine_code_display)

        # 创建关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.setFont(QFont("Microsoft YaHei", 11))
        close_btn.setFixedWidth(120)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        close_btn.clicked.connect(self.close)
        
        # 创建复制机器码按钮
        copy_btn = QPushButton("复制机器码")
        copy_btn.setFont(QFont("Microsoft YaHei", 11))
        copy_btn.setFixedWidth(120)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #50b83c;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #40982f;
            }
        """)
        copy_btn.clicked.connect(lambda: self.copy_machine_code(machine_code))
        
        # 创建"已有激活码"按钮
        activate_btn = QPushButton("已有激活码")
        activate_btn.setFont(QFont("Microsoft YaHei", 11))
        activate_btn.setFixedWidth(120)
        activate_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9800;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #e68a00;
            }
        """)
        activate_btn.clicked.connect(self.switch_to_license_input)
        
        # 创建按钮布局
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(50)  # 进一步增加按钮间距
        btn_layout.addWidget(copy_btn)
        btn_layout.addWidget(activate_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        # 添加页面到stacked_widget
        self.stacked_widget.addWidget(page)
    
    def create_license_input_page(self):
        """创建激活码输入页面"""
        # 创建页面容器
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # 创建标题
        title = QLabel("输入激活码")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # 创建提示信息
        info = QLabel("请将获取到的激活码内容粘贴到下方文本框中，然后点击生成按钮")
        info.setFont(QFont("Microsoft YaHei", 11))
        info.setAlignment(Qt.AlignCenter)
        info.setWordWrap(True)
        layout.addWidget(info)
        
        # 创建多行文本框
        self.license_text = QTextEdit()
        self.license_text.setFont(QFont("Consolas", 12))
        self.license_text.setStyleSheet("""
            QTextEdit {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                padding: 10px;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.license_text)
        
        # 创建生成按钮
        generate_btn = QPushButton("激活")
        generate_btn.setFont(QFont("Microsoft YaHei", 11))
        generate_btn.setFixedWidth(120)
        generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #50b83c;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #40982f;
            }
        """)
        generate_btn.clicked.connect(self.generate_license_file)
        
        # 创建返回按钮
        back_btn = QPushButton("返回")
        back_btn.setFont(QFont("Microsoft YaHei", 11))
        back_btn.setFixedWidth(120)
        back_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        back_btn.clicked.connect(self.switch_to_machine_code)
        
        # 创建关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.setFont(QFont("Microsoft YaHei", 11))
        close_btn.setFixedWidth(120)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        close_btn.clicked.connect(self.close)
        
        # 创建按钮布局
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(30)  # 增加按钮间距
        btn_layout.addWidget(back_btn)
        btn_layout.addWidget(generate_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        # 添加页面到stacked_widget
        self.stacked_widget.addWidget(page)
    
    def switch_to_machine_code(self):
        """切换到机器码显示页面"""
        self.setWindowTitle("软件未激活")
        self.stacked_widget.setCurrentIndex(0)
    
    def switch_to_license_input(self):
        """切换到激活码输入页面"""
        self.setWindowTitle("输入激活码")
        self.stacked_widget.setCurrentIndex(1)
    
    def copy_machine_code(self, machine_code):
        """复制机器码到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(machine_code)
        QMessageBox.information(self, "提示", "机器码已成功复制到剪贴板！", QMessageBox.Ok)
    
    def generate_license_file(self):
        """生成license.key文件"""
        license_content = self.license_text.toPlainText().strip()
        if not license_content:
            QMessageBox.warning(self, "警告", "激活码内容不能为空！", QMessageBox.Ok)
            return
        
        try:
            # 获取根目录路径

            license_path = self.license_guard.license_file
            
            # 写入文件
            with open(license_path, "w", encoding="utf-8") as f:
                f.write(license_content)
            
            QMessageBox.information(self, "成功", f"license.key文件已成功生成到根目录！\n路径：{license_path}", QMessageBox.Ok)
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成license.key文件失败：{str(e)}", QMessageBox.Ok)


class InitializationScreen(QSplashScreen):
    """软件初始化页面，显示LOGO和初始化提示信息"""
    def __init__(self, logo_path=None):
        # 如果没有提供logo_path，则使用默认路径
        if logo_path is None:
            logo_path = get_path("logo.ico")
        
        # 加载logo
        logo_pixmap = QPixmap()
        if os.path.exists(logo_path):
            logo_pixmap = QPixmap(logo_path)
        
        # 创建一个新的QPixmap，大小足够容纳logo和文字
        if not logo_pixmap.isNull():
            width = max(300, logo_pixmap.width())  # 确保最小宽度为300
            height = logo_pixmap.height() + 40  # 减少为40像素间距
            result_pixmap = QPixmap(width, height)
            result_pixmap.fill(Qt.transparent)  # 使用透明背景
            
            # 绘制logo到顶部居中位置
            painter = QPainter(result_pixmap)
            logo_x = (width - logo_pixmap.width()) // 2  # 居中位置
            painter.drawPixmap(logo_x, 0, logo_pixmap)  # 绘制logo
            painter.end()
        else:
            # 如果logo加载失败，使用默认大小和透明背景
            result_pixmap = QPixmap(300, 360)
            result_pixmap.fill(Qt.transparent)
        
        # 初始化QSplashScreen，使用透明背景
        super().__init__(result_pixmap, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)

        # 添加"软件初始化中"文本，放在底部居中显示
        self.showMessage("软件初始化中", Qt.AlignCenter | Qt.AlignBottom, Qt.white)
        
        # 设置字体
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        self.setFont(font)
        
        # 显示在屏幕中心
        self.setGeometry(
            QApplication.desktop().screenGeometry().center().x() - self.width() // 2,
            QApplication.desktop().screenGeometry().center().y() - self.height() // 2,
            self.width(),
            self.height()
        )
        
        # 显示初始化页面
        self.show()
    
    def close_screen(self):
        """关闭初始化页面"""
        self.close()


def show_initialization_screen():
    """显示初始化页面并返回实例，以便后续关闭"""
    if not QApplication.instance():
        app = QApplication(sys.argv)
    else:
        app = QApplication.instance()
        
    # 获取logo路径
    logo_path = get_path("logo.ico")
    
    # 创建并显示初始化页面
    init_screen = InitializationScreen(logo_path)
    app.processEvents()  # 确保页面立即显示
    return init_screen


# 测试代码
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 显示初始化页面
    init_screen = show_initialization_screen()
    
    # 模拟初始化过程（3秒后显示激活窗口并关闭初始化页面）
    def show_main_window():
        window = ActivationWindow()
        init_screen.close_screen()
        print(init_screen)
    
    QTimer.singleShot(3000, show_main_window)
    
    sys.exit(app.exec_())