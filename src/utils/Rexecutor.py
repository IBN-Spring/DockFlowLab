import json
import subprocess
import os
import threading
import traceback
import zlib
try:
    from cryptography.fernet import Fernet
except ImportError:
    print("cryptography import faild")

from src.utils.configManager import get_config
from src.utils.path import get_path, get_inner_path


class Rexecutor:
    Rscriptexe_path =None  #Rscript.exe的路径
    script_path =None #要执行的R脚本的路径
    result_path =None #输出目录的路径
    step_name = None #步骤别名,可以用来隐藏R脚本信息
    args = [] #其余附带参数

    def __init__(self):
        # 1. 从配置读取（通常是相对路径，如 portableR/bin/Rscript.exe）
        raw_path = get_config("Environment", "Rscript_path")

        # 2. 使用 get_path 转换为打包环境下的绝对路径
        # 如果 raw_path 已经是绝对路径，get_path 通常也会正确处理（取决于实现，建议配置里写相对路径）
        if raw_path:
            self.Rscriptexe_path = get_path(raw_path)
        else:
            # 如果配置为空，给个默认值
            self.Rscriptexe_path = get_path("portableR/bin/Rscript.exe")

    def add_arg(self,arg:str):
        self.args.append(arg)


    def run(self):
        if  not os.path.exists(self.Rscriptexe_path):
            yield "❌找不到Rscript.exe"
            return

        if self.step_name is None:
            self.step_name = os.path.basename(self.script_path)
        yield f"🚀 开始执行: {self.step_name} ..."


        try:
            cmd = [self.Rscriptexe_path, self.script_path, self.result_path] + self.args

            startupinfo = None
            if os.name == 'nt':  # 仅在 Windows 下执行
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,  # 文本模式读取
                bufsize=1,  # 行缓冲，实时输出
                universal_newlines=True,
                encoding="utf-8",
                errors="ignore",
                shell=False,
                startupinfo = startupinfo
            )

            while proc.poll() is None:  # 子进程还在运行
                line = proc.stdout.readline()
                if line:  # 避免空行
                    yield line.strip()

                # 读取剩余的输出（防止进程结束后还有未读取的内容）
            remaining = proc.stdout.read()
            if remaining:
                yield remaining.strip()

            # 等待子进程完全结束，获取返回码
            proc.wait()

        except subprocess.CalledProcessError as e:
            yield f"❌ R 脚本执行失败！状态码：{e.returncode}"
            yield "=== 错误信息 (STDERR) ==="
            yield e.stderr
        except Exception as e:
            tb_str = traceback.format_exc()
            yield f"❌ 调用失败: {e}\n{tb_str}"



class EncodedRexecutor:
    Rscriptexe_path =None  #Rscript.exe的路径
    script_name =None #资源文件中R脚本的名字
    result_path =None #输出目录的路径
    step_name = None #步骤别名,可以用来隐藏R脚本信息
    r_code_content = None
    key = "H0vif-Y_Re8u-DFb3NgJ4Vz2t88wQ6swkDr4qXnt04A="
    R_resource_path = get_path("r_assets.dat")
    args = [] #其余附带参数

    def __init__(self):
        # 1. 从配置读取（通常是相对路径，如 portableR/bin/Rscript.exe）
        raw_path = get_config("Environment", "Rscript_path")

        # 2. 使用 get_path 转换为打包环境下的绝对路径
        # 如果 raw_path 已经是绝对路径，get_path 通常也会正确处理（取决于实现，建议配置里写相对路径）
        if raw_path:
            self.Rscriptexe_path = get_path(raw_path)
        else:
            # 如果配置为空，给个默认值
            self.Rscriptexe_path = get_path("portableR/bin/Rscript.exe")

    def add_arg(self,arg:str):
        self.args.append(arg)

    def decode(self,R_script_name):
        from cryptography.fernet import Fernet
        cipher = Fernet(self.key)
        try:
            with open(self.R_resource_path, "rb") as f:
                encrypted_data = f.read()

            # 解密 -> 解压 -> 反序列化
            decrypted_data = cipher.decrypt(encrypted_data)
            json_bytes = zlib.decompress(decrypted_data)
            return json.loads(json_bytes)[R_script_name]  # 返回字典 {'script1.R': 'code...', ...}
        except Exception as e:
            print("资源文件损坏或被篡改")
            return None

    def run(self):
        self.r_code_content = self.decode(self.script_name)
        if not os.path.exists(self.Rscriptexe_path):
            yield "❌ 找不到 Rscript.exe"
            return

        if self.step_name is None:
            self.step_name = self.script_name
        yield f"🚀 开始执行: {self.step_name} ..."

        # 假设 self.r_code_content 存储了已经解密的 R 代码字符串
        # 如果没有，您需要先从加密资源中加载它
        if not hasattr(self, 'r_code_content') or not self.r_code_content:
            yield f"❌ 加载{self.step_name}模块失败"
            return

        try:
            # 构建命令：Rscript - [args...]
            # '-' 表示从标准输入读取脚本
            cmd = [self.Rscriptexe_path, '-', self.result_path] + self.args
            startupinfo = None
            if os.name == 'nt':  # 仅在 Windows 下执行
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="ignore",
                startupinfo = startupinfo
            )

            # 在单独的线程中写入 stdin
            def write_stdin():
                try:
                    proc.stdin.write(self.r_code_content)
                    proc.stdin.close()
                except:
                    pass

            writer_thread = threading.Thread(target=write_stdin)
            writer_thread.start()

            # 在主线程中读取输出
            for line in proc.stdout:
                yield line.strip()

            writer_thread.join()
            # 获取最终返回码
            return_code = proc.wait()

            if return_code != 0:
                yield f"⚠️ 模块{self.step_name}运行结束，状态码：{return_code}"
            else:
                yield "✅ 执行成功"

        except subprocess.CalledProcessError as e:
            yield f"❌ 模块{self.step_name}执行异常！状态码：{e.returncode}"
        except Exception as e:
            tb_str = traceback.format_exc()
            yield f"❌ 调用失败: {e}\n{tb_str}"

if __name__ == "__main__":
    Rexe = EncodedRexecutor()
    Rexe.script_name = "平台文件下载.R"
    Rexe.result_path = "D:\\TEST\\test1"
    Rexe.add_arg("GSE10358")
    Rexe.add_arg("http://127.0.0.1:10809")
    for msg in Rexe.run():
        print(msg)