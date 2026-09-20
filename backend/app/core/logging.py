"""结构化日志配置。"""
import logging
import sys
from app.config import get_settings


def setup_logging():
    """初始化全局日志。"""
    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # 降低第三方库噪音
    for noisy in ["urllib3", "httpx", "chromadb", "httpcore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("agent")
