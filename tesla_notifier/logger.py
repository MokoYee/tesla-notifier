"""日志工具"""

import logging
import sys
from datetime import datetime

from tesla_notifier.config import config


class SimpleFormatter(logging.Formatter):
    """简洁日志格式：时间 级别 模块 - 消息"""

    def format(self, record: logging.LogRecord) -> str:
        # 使用配置的时区
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.timezone)
        now = datetime.now(tz)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 格式：2026-01-16 15:17:21 INFO [main] - 消息内容
        base_msg = f"{time_str} {record.levelname:<5} [{record.name}] - {record.getMessage()}"

        # 如果有附加数据，追加到消息后面
        if hasattr(record, "data") and record.data:
            base_msg += f" | {record.data}"

        # 如果有异常信息，追加堆栈
        if record.exc_info:
            base_msg += f"\n{self.formatException(record.exc_info)}"

        return base_msg


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建日志器"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(SimpleFormatter())
        logger.addHandler(handler)

    return logger


def log_with_data(
    logger: logging.Logger, level: int, msg: str, data: dict | None = None
) -> None:
    """带数据的日志"""
    record = logger.makeRecord(
        logger.name, level, "", 0, msg, (), None
    )
    if data:
        record.data = data  # type: ignore[attr-defined]
    logger.handle(record)
