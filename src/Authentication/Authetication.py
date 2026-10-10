# license_guard.py (客户端保护模块)
import traceback

import wmi
import hashlib
import rsa
import ntplib
import os
import json
import base64
from src.Authentication.LicenseCache import *
# --- 配置 ---
from src.Authentication.ValidatorConfig import *



class LicenseGuard:
    def __init__(self):
        self.license_file = LICENSE_KEY_PATH
        self.pubkey = rsa.PublicKey.load_pkcs1(PUBLIC_KEY_STR.encode())
        self.machine_code = self.get_machine_code()
        self.cache_manager = LicenseCacheManager(self.machine_code)



    def get_machine_code(self):
        """获取机器码 (CPU序列号 + 硬盘序列号 的 MD5)"""
        try:
            c = wmi.WMI()
            cpu_serial = c.Win32_Processor()[0].ProcessorId.strip()
            disk_serial = c.Win32_DiskDrive()[0].SerialNumber.strip()

            raw_info = f"{cpu_serial}_{disk_serial}_MySalt123"  # 加盐
            md5 = hashlib.md5(raw_info.encode('utf-8')).hexdigest()
            return md5.upper()
        except Exception:
            return "UNKNOWN_MACHINE"

    def get_network_time(self):
        """获取网络时间，失败则返回 None"""
        try:
            client = ntplib.NTPClient()
            # 尝试多个NTP服务器
            hosts = ['pool.ntp.org', 'time.windows.com', 'ntp.aliyun.com']
            for host in hosts:
                try:
                    response = client.request(host, timeout=3)
                    return datetime.fromtimestamp(response.tx_time)
                except:
                    continue
            return None
        except:
            return None

    # def generate_cache(self):
    #     self.cache_manager.update_cache_value()



    def verify_license(self):
        """验证许可证有效性"""
        current_time = None
        if not os.path.exists(self.license_file):
            return False, "许可证文件不存在",current_time

        try:
            with open(self.license_file, 'r') as f:
                license_data = json.load(f)

            signature = base64.b64decode(license_data['signature'])
            data_str = license_data['data']

            # 1. RSA 验签：证明文件是你生成的，且未被篡改
            try:
                rsa.verify(data_str.encode(), signature, self.pubkey)
            except rsa.VerificationError:
                return False, "许可证无效或已损坏",current_time

            # 2. 解析数据
            payload = json.loads(base64.b64decode(data_str).decode())

            # 3. 验证机器码
            current_machine = self.machine_code
            if payload['machine_code'] != current_machine:
                return False, "许可证与当前设备不匹配",current_time

            # 4. 验证时间 (强制联网)
            expire_str = payload['expire_date']
            
            # 尝试解析不同的时间格式
            try:
                # 尝试解析包含时分秒的格式
                expire_date = datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    # 尝试解析只包含日期的格式
                    expire_date = datetime.strptime(expire_str, "%Y-%m-%d")
                    # 如果只有日期，添加默认时间 23:59:59
                    expire_date = expire_date.replace(hour=23, minute=59, second=59)
                except ValueError:
                    return False, f"无效的到期时间格式: {expire_str}",current_time

            current_time = self.get_network_time()
            if current_time is None:
                # 如果联网失败，可以决定是直接拒绝，还是允许使用但警告
                # 这里为了安全，选择拒绝
                return False, "无法连接网络验证时间"

            if current_time > expire_date:
                return False, f"许可证已过期 (过期日: {expire_str})",current_time


            # update_cache_file(expire_str,current_time)
            self.cache_manager.update_license_cache(expire_str,current_time)
            print(self.cache_manager.get_all_license_cache())
            return True, f"激活成功，有效期至: {expire_str}", current_time

        except Exception as e:
            tb_str = traceback.format_exc()
            return False, f"验证出错: {tb_str}", current_time


# --- 使用示例 ---
if __name__ == "__main__":
    guard = LicenseGuard()
    machine_code = guard.get_machine_code()
    print(f"📌 本机机器码: {machine_code}")
    print("正在验证...")

    is_valid, msg, _ = guard.verify_license()
    if is_valid:
        print(f"✅ {msg}")
        # 启动你的主程序...
    else:
        print(f"❌ {msg}")
        input("按回车键退出...")
        exit()
