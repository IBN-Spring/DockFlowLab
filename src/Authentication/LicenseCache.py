import json
import os
from datetime import datetime

from src.utils.path import get_path
from src.utils.cacheManager import CacheManager
from src.Authentication.ValidatorConfig import *

class LicenseCacheManager:
    def __init__(self, machine_code):
        self.license_cache_path = LICENSE_CACHE_PATH
        self.cache_initial_content = {
            "machine_code": machine_code,
            "expiry_time": "",
            "activation_time": ""
        }
        self.license_cache_manager = CacheManager(self.license_cache_path, self.cache_initial_content)

    def update_license_cache(self, expiry_time, current_time):
        activation_time = "未激活"
        # 定义一个变量接收格式化后的到期时间字符串，避免覆盖入参
        final_expiry_time = expiry_time

        # 只有到期时间不是未激活、且当前时间有效时，才计算有效期
        if expiry_time != '未激活' and current_time is not None:
            try:
                # 1. 解析字符串为datetime对象（改名：expiry_time_obj，避免覆盖）
                expiry_time_obj = datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S")
                # 2. 计算时间差
                time_diff = expiry_time_obj - current_time

                # 3. 格式化时间差，生成激活剩余时间
                days = time_diff.days
                hours, remainder = divmod(time_diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)

                if days > 0:
                    activation_time = f"{days}天 {hours}时 {minutes}分"
                else:
                    activation_time = f"{hours}时 {minutes}分"

                # ✅ 核心修复：把datetime对象转回字符串，保证JSON可序列化
                final_expiry_time = expiry_time_obj.strftime("%Y-%m-%d %H:%M:%S")

            except Exception as e:
                activation_time = "时间计算错误"
                # 异常兜底：用原始入参赋值，保证缓存有值不丢失
                final_expiry_time = expiry_time

        # ✅ 现在写入的都是【字符串类型】，完全兼容JSON序列化
        self.license_cache_manager.update_cache_value('expiry_time', final_expiry_time)
        self.license_cache_manager.update_cache_value("activation_time", activation_time)

    def get_license_cache(self,key):
        return self.license_cache_manager.get_cache_value(key)

    def get_all_license_cache(self):
        return self.license_cache_manager.get_all_cache_value()

