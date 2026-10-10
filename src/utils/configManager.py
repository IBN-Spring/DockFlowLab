import configparser
import os

from src.utils.path import get_path


class ConfigManager:
    """配置文件管理类，用于读写config.ini文件中的R和Output节"""

    def __init__(self, config_path=None):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        # 设置配置文件路径
        if config_path is None:
            self.config_path = get_path("config.ini")
        else:
            self.config_path = config_path

        # 创建配置解析器
        self.config = configparser.ConfigParser()

        # 读取配置文件
        self.load_config()

    def load_config(self):
        """读取配置文件"""
        if os.path.exists(self.config_path):
            self.config.read(self.config_path, encoding="utf-8")
        else:
            # 如果配置文件不存在，创建默认配置
            self.create_default_config()
            self.save_config()

    def save_config(self):
        """保存配置到文件"""
        with open(self.config_path, "w", encoding="utf-8") as configfile:
            self.config.write(configfile)

    def get(self, section, option, fallback=None):
        """
        获取配置项的值

        Args:
            section: 配置节名称，仅支持"R"和"Output"
            option: 配置项名称
            fallback: 如果配置项不存在则返回的默认值

        Returns:
            配置项的值，如果不存在则返回fallback
        """
        try:
            return self.config.get(section, option, fallback=fallback)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def set(self, section, option, value):
        """
        设置配置项的值

        Args:
            section: 配置节名称，支持"R"、"Output"、"Analysis"和"Proxy"
            option: 配置项名称
            value: 配置项的值
        """
        if section not in self.config:
            self.config[section] = {}

        self.config[section][option] = str(value)
        self.save_config()

    def create_default_config(self):
        """创建默认配置"""

        # Environment section
        self.config["Environment"] = {}

        # R 配置
        default_rscript_path = get_path("portableR/R-4.5.1/bin/Rscript.exe")
        if os.path.exists(default_rscript_path):
            self.config["Environment"]["Rscript_path"] = default_rscript_path
        else:
            self.config["Environment"]["Rscript_path"] = ""

        # Chrome 配置
        default_chrome_path = get_path('portableChrome/GoogleChromePortable.exe')
        if os.path.exists(default_chrome_path):
            self.config["Environment"]["Chrome_path"] = default_chrome_path
        else:
            self.config["Environment"]["Chrome_path"] = ""

        # Output section
        self.config["Output"] = {}

        default_result_path = get_path("result")
        if os.path.exists(default_result_path):
            self.config["Output"]["output_directory"] = default_result_path
        else:
            self.config["Output"]["output_directory"] = ""

        # 分析配置
        self.config["Analysis"] = {
            "genecard_threshold": "10",
            "use_batman_supplement": "1"
        }

        # 代理配置
        self.config["Proxy"] = {
            "proxy_mode": "1",  # 1: 本地代理, 2: 动态代理
            "proxy_address": ""
        }

        # 对接引擎配置（固定使用 Vina CPU）
        self.config["Docking"] = {
            "engine": "Vina-CPU",
        }


# ✅ 修复：global_config 懒加载，避免 import 时立即初始化导致 NameError
global_config = None


def _get_global_config():
    global global_config
    if global_config is None:
        global_config = ConfigManager()
    return global_config


# 便捷函数
def get_config(section, option, fallback=None):
    """便捷函数：获取配置项值"""
    return _get_global_config().get(section, option, fallback)


def set_config(section, option, value):
    """便捷函数：设置配置项值"""
    _get_global_config().set(section, option, value)


def save_config():
    """便捷函数：保存配置"""
    _get_global_config().save_config()
