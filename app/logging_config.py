"""结构化日志。"""

from __future__ import annotations

import logging
import sys

from app.config import settings


def setup_logging() -> None:
    """初始化 JSON 风格日志（M1 简化版用普通格式）。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # 避免重复初始化
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # 降低噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
