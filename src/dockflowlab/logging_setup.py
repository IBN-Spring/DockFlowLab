"""日志配置：控制台 + 项目内滚动日志。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "dockflowlab"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_configured = False


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def _default_log_dir() -> Path:
    from .config import user_cache_dir

    return user_cache_dir() / "logs"


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """初始化日志。未指定目录时写入当前平台的用户缓存目录。"""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if not _configured:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        logger.addHandler(console)
        _configured = True

    log_dir = Path(log_dir) if log_dir is not None else _default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "session.log").resolve()
    # 避免重复添加同一个文件
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == target:
            break
    else:
        file_handler = RotatingFileHandler(
            target, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(file_handler)
    return logger
