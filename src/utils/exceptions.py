"""
DockFlowLab 自定义异常类
提供更精确的错误处理和用户友好的错误信息
"""


class DockFlowLabException(Exception):
    """DockFlowLab 基础异常类"""

    def __init__(self, message: str, details: str = None):
        self.message = message
        self.details = details
        super().__init__(self.message)

    def __str__(self):
        if self.details:
            return f"{self.message}\n详细信息: {self.details}"
        return self.message


# ============== 配置相关异常 ==============

class ConfigurationError(DockFlowLabException):
    """配置错误"""
    pass


class InvalidConfigError(ConfigurationError):
    """无效的配置值"""
    pass


class MissingConfigError(ConfigurationError):
    """缺少必需的配置项"""
    pass


# ============== 文件相关异常 ==============

class FileError(DockFlowLabException):
    """文件操作错误"""
    pass


class FileNotFoundError(FileError):
    """文件未找到"""
    pass


class InvalidFileFormatError(FileError):
    """无效的文件格式"""
    pass


class FileValidationError(FileError):
    """文件验证失败"""
    pass


class FileCorruptedError(FileError):
    """文件损坏"""
    pass


# ============== 下载相关异常 ==============

class DownloadError(DockFlowLabException):
    """下载错误"""
    pass


class NetworkError(DownloadError):
    """网络连接错误"""
    pass


class DatabaseError(DownloadError):
    """数据库查询错误"""
    pass


class MoleculeNotFoundError(DownloadError):
    """分子未找到"""
    pass


# ============== 预处理相关异常 ==============

class PreprocessingError(DockFlowLabException):
    """预处理错误"""
    pass


class ConversionError(PreprocessingError):
    """格式转换错误"""
    pass


class OptimizationError(PreprocessingError):
    """能量优化错误"""
    pass


class InvalidMoleculeError(PreprocessingError):
    """无效的分子结构"""
    pass


class ForceFieldError(PreprocessingError):
    """力场计算错误"""
    pass


# ============== 盒子生成相关异常 ==============

class BoxGenerationError(DockFlowLabException):
    """对接盒子生成错误"""
    pass


class NoPocketFoundError(BoxGenerationError):
    """未检测到结合口袋"""
    pass


class InvalidBoxSizeError(BoxGenerationError):
    """无效的盒子尺寸"""
    pass


class NoActiveResiduesError(BoxGenerationError):
    """未找到活性残基"""
    pass


# ============== 对接相关异常 ==============

class DockingError(DockFlowLabException):
    """对接错误"""
    pass


class VinaExecutionError(DockingError):
    """Vina执行错误"""
    pass


class DockingFailedError(DockingError):
    """对接失败"""
    pass


class InvalidLigandError(DockingError):
    """无效的配体"""
    pass


class InvalidReceptorError(DockingError):
    """无效的受体"""
    pass


class BoxConfigError(DockingError):
    """盒子配置错误"""
    pass


# ============== 分析相关异常 ==============

class AnalysisError(DockFlowLabException):
    """结果分析错误"""
    pass


class NoResultsError(AnalysisError):
    """无对接结果"""
    pass


class InvalidResultError(AnalysisError):
    """无效的对接结果"""
    pass


class ParsingError(AnalysisError):
    """结果解析错误"""
    pass


# ============== 依赖相关异常 ==============

class DependencyError(DockFlowLabException):
    """依赖错误"""
    pass


class MissingDependencyError(DependencyError):
    """缺少必需的依赖"""
    pass


class VersionMismatchError(DependencyError):
    """版本不匹配"""
    pass


class ToolNotFoundError(DependencyError):
    """外部工具未找到"""
    pass


# ============== 运行时异常 ==============

class RuntimeError(DockFlowLabException):
    """运行时错误"""
    pass


class TimeoutError(RuntimeError):
    """超时错误"""
    pass


class MemoryError(RuntimeError):
    """内存不足"""
    pass


class ProcessError(RuntimeError):
    """进程执行错误"""
    pass


# ============== 验证相关异常 ==============

class ValidationError(DockFlowLabException):
    """验证错误"""
    pass


class ParameterValidationError(ValidationError):
    """参数验证错误"""
    pass


class StructureValidationError(ValidationError):
    """结构验证错误"""
    pass


class EnergyValidationError(ValidationError):
    """能量验证错误"""
    pass


# ============== 辅助函数 ==============

def handle_exception(exc: Exception, log_func=None, raise_custom=True):
    """统一异常处理

    Args:
        exc: 捕获的异常
        log_func: 日志函数
        raise_custom: 是否转换为自定义异常并重新抛出

    Raises:
        DockFlowLabException: 转换后的自定义异常
    """
    # 如果已经是自定义异常，直接抛出
    if isinstance(exc, DockFlowLabException):
        if log_func:
            log_func(str(exc), "ERROR")
        if raise_custom:
            raise exc
        return

    # 转换标准异常为自定义异常
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    if log_func:
        log_func(f"{exc_type}: {exc_msg}", "ERROR")

    if raise_custom:
        # 根据异常类型转换
        if "file" in exc_msg.lower() or "not found" in exc_msg.lower():
            raise FileError(f"文件操作失败: {exc_msg}", details=exc_type)
        elif "network" in exc_msg.lower() or "connection" in exc_msg.lower():
            raise NetworkError(f"网络错误: {exc_msg}", details=exc_type)
        elif "timeout" in exc_msg.lower():
            raise TimeoutError(f"操作超时: {exc_msg}", details=exc_type)
        elif "memory" in exc_msg.lower():
            raise MemoryError(f"内存不足: {exc_msg}", details=exc_type)
        else:
            raise RuntimeError(f"运行时错误: {exc_msg}", details=exc_type)


def format_error_message(exc: DockFlowLabException, include_solutions: bool = True) -> str:
    """格式化错误消息，包含可能的解决方案

    Args:
        exc: 自定义异常对象
        include_solutions: 是否包含解决方案

    Returns:
        str: 格式化的错误消息
    """
    msg = f"❌ {exc.__class__.__name__}: {exc.message}"

    if exc.details:
        msg += f"\n   详情: {exc.details}"

    if include_solutions:
        solutions = _get_solutions(exc)
        if solutions:
            msg += f"\n   💡 建议解决方案:"
            for i, sol in enumerate(solutions, 1):
                msg += f"\n      {i}. {sol}"

    return msg


def _get_solutions(exc: DockFlowLabException) -> list:
    """根据异常类型提供解决方案"""
    solutions = []

    if isinstance(exc, FileNotFoundError):
        solutions.extend([
            "检查文件路径是否正确",
            "确认文件确实存在",
            "检查文件权限"
        ])
    elif isinstance(exc, InvalidFileFormatError):
        solutions.extend([
            "确认文件格式正确（支持: PDB, SDF, MOL2, PDBQT）",
            "尝试使用OpenBabel转换格式",
            "检查文件是否损坏"
        ])
    elif isinstance(exc, NetworkError):
        solutions.extend([
            "检查网络连接",
            "使用代理或VPN",
            "稍后重试"
        ])
    elif isinstance(exc, MissingDependencyError):
        solutions.extend([
            "安装缺失的依赖: pip install <package>",
            "检查Conda环境是否激活",
            "参考文档安装指南"
        ])
    elif isinstance(exc, NoPocketFoundError):
        solutions.extend([
            "手动指定对接盒子中心和尺寸",
            "检查受体是否有活性残基",
            "尝试使用共晶配体坐标"
        ])
    elif isinstance(exc, DockingFailedError):
        solutions.extend([
            "增大exhaustiveness参数",
            "调整对接盒子大小",
            "检查配体和受体文件是否有效",
            "查看Vina日志文件获取详细错误"
        ])
    elif isinstance(exc, MemoryError):
        solutions.extend([
            "减少并行任务数量",
            "关闭其他程序释放内存",
            "使用更小的对接盒子"
        ])
    elif isinstance(exc, TimeoutError):
        solutions.extend([
            "增加超时时间限制",
            "减小对接盒子大小",
            "降低exhaustiveness参数"
        ])

    return solutions


# ============== 异常上下文管理器 ==============

class exception_handler:
    """异常处理上下文管理器

    Example:
        with exception_handler(log_func=log, operation="对接任务"):
            run_docking()
    """

    def __init__(self, log_func=None, operation: str = "操作", raise_error: bool = True):
        self.log_func = log_func
        self.operation = operation
        self.raise_error = raise_error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            return True

        # 记录异常
        if isinstance(exc_val, DockFlowLabException):
            msg = format_error_message(exc_val)
        else:
            msg = f"❌ {self.operation}失败: {exc_val}"

        if self.log_func:
            self.log_func(msg, "ERROR")
        else:
            print(msg)

        # 是否抑制异常
        return not self.raise_error


# ============== 装饰器 ==============

def catch_exceptions(operation: str = "操作", log_func=None, default_return=None):
    """异常捕获装饰器

    Args:
        operation: 操作描述
        log_func: 日志函数
        default_return: 异常时的默认返回值

    Example:
        @catch_exceptions("对接任务", log_func=log, default_return=False)
        def run_docking():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except DockFlowLabException as e:
                msg = format_error_message(e)
                if log_func:
                    log_func(msg, "ERROR")
                else:
                    print(msg)
                return default_return
            except Exception as e:
                msg = f"❌ {operation}失败: {e}"
                if log_func:
                    log_func(msg, "ERROR")
                else:
                    print(msg)
                return default_return
        return wrapper
    return decorator
