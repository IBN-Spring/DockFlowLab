# path_utils.py
import glob
import os
import shutil
import sys

# 全局根目录（程序启动时初始化，仅计算一次）
APP_ROOT = ""
INNER_ROOT_DIR =""
def init_app_root():
    """初始化应用根目录（程序启动时调用一次）"""
    global APP_ROOT
    global INNER_ROOT_DIR
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller打包后：单文件模式下为临时解压目录，文件夹模式下为exe所在目录
        APP_ROOT = sys._MEIPASS if "--onefile" in sys.argv else os.path.dirname(sys.executable)
    else:
        # 开发模式：以当前工具模块所在目录的上层（项目根）为基准
        APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    # 规范化路径（兼容跨平台）
    APP_ROOT = os.path.normpath(APP_ROOT)
    # print(f"应用根目录初始化完成：{APP_ROOT}")
    if getattr(sys, 'frozen', False):
        # 打包后：获取exe文件所在的目录（R_scripts就在这个目录下）
        INNER_ROOT_DIR =   sys._MEIPASS
        INNER_ROOT_DIR = os.path.normpath(INNER_ROOT_DIR)
    else:
        # 开发时：获取当前Python脚本所在的目录
        INNER_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def get_path(relative_path: str) -> str:
    """全局路径拼接函数：传入相对根目录的路径，返回绝对路径"""
    if not APP_ROOT:
        init_app_root()  # 未初始化则自动初始化
    return os.path.normpath(os.path.join(APP_ROOT, relative_path))

def get_inner_path(relative_path: str) -> str:
    print(INNER_ROOT_DIR)
    if not INNER_ROOT_DIR:
        init_app_root()  # 未初始化则自动初始化
    return os.path.normpath(os.path.join(INNER_ROOT_DIR, relative_path))


def join_path(first_path,*paths):
    final_path = os.path.abspath(first_path)
    for other in paths:
        final_path = os.path.join(final_path, other)
    return os.path.abspath(final_path)


#寻找以suffix为后缀的最新文件
def find_latest_file_by_suffix(folder_path,suffix):
    if not os.path.isdir(folder_path):
        return None

    # 构造匹配模式，查找所有以suffix结尾的文件
    pattern = os.path.join(folder_path, f"*{suffix}")
    matched_files = glob.glob(pattern)

    # 过滤掉文件夹（确保只处理文件）
    txt_gz_files = [f for f in matched_files if os.path.isfile(f)]

    # 无符合条件文件的情况
    if not txt_gz_files:
        return None

    # 按文件修改时间排序（最新的在最前面）
    # os.path.getmtime 获取文件最后修改时间（时间戳）
    # 如需按创建时间，可改用 os.path.getctime（注意：Windows 和 Linux 下 getctime 含义不同）
    latest_file = max(txt_gz_files, key=lambda f: os.path.getmtime(f))

    return latest_file


import os
import shutil


def create_and_copy(base_dir, sub_dir, files: list):
    new_dir = os.path.join(base_dir, sub_dir)

    os.makedirs(new_dir, exist_ok=True)

    for file in files:

        # 建议加个判断，防止源文件不存在报错
        if os.path.exists(file):
            shutil.copy2(file, new_dir)
        else:
            print(f"警告: 文件不存在 {file}")

    return os.path.abspath(new_dir)


if __name__ == "__main__":
    init_app_root()
