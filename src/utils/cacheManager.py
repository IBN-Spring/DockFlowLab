import json
import os


class CacheManager:
    def __init__(self,cache_file_path,initial_data=None):
        self.file_path =  cache_file_path
        data = initial_data if initial_data is not None else {}

        # 写入 JSON 文件（ensure_ascii=False 支持中文，indent 格式化输出）
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def get_cache_value(self,key):
        if not os.path.exists(self.file_path):
            return None
        with open(self.file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            data = data.get(key)
            return data

    def get_all_cache_value(self):
        if not os.path.exists(self.file_path):
            return None
        with open(self.file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data

    import json
    import os

    def update_cache_value(self, key, value):
        """
        更新缓存中的指定键值对。
        优化点：
        1. 文件不存在时自动创建文件并写入数据，不再返回False
        2. 捕获所有文件读写/JSON解析异常，防止程序崩溃
        3. 确保data始终为字典类型，杜绝TypeError
        更新成功返回 True；出现异常返回 False。
        """
        # 初始化data为字典，【强制兜底】确保永远是dict类型，核心防报错
        data = {}

        # 1. 读取现有数据（文件存在才读取）
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    # 读取文件并转为字典
                    load_data = json.load(f)
                    # 双重保险：如果读取的内容不是字典，重置为空字典
                    if isinstance(load_data, dict):
                        data = load_data
            except json.JSONDecodeError:
                # 捕获JSON格式错误：文件损坏/内容不是标准JSON
                data = {}
            except Exception:
                # 捕获所有文件读写异常：权限问题、文件占用、编码错误等
                data = {}

        # 2. 更新内存中的字典 → 此时data一定是dict，绝对不会报TypeError
        data[key] = value

        # 3. 将更新后的数据写回文件（文件不存在会自动创建）
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception:
            # 捕获写入异常
            return False

if __name__ == '__main__':
    cache_manager = CacheManager('cache.json',{"n":"1","a":"3"})
    print(cache_manager.get_cache_value("n"))
    cache_manager.update_cache_value("n","2")