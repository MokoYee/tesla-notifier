"""日志工具"""

import logging
import sys
from collections.abc import Mapping
from datetime import datetime

from tesla_notifier.config import config


class SimpleFormatter(logging.Formatter):
    """简洁日志格式：时间 级别 模块:行号 - 消息"""

    def format(self, record: logging.LogRecord) -> str:
        # 使用配置的时区
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.timezone)
        now = datetime.now(tz)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 只保留行号
        lineno = record.lineno
        location = f":{lineno}"

        # 格式：2026-01-16 15:17:21 INFO [main]:42 - 消息内容
        base_msg = (
            f"{time_str} {record.levelname:<5} "
            f"[{record.name}]{location} - {record.getMessage()}"
        )

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
    # 设置 logger 默认为 DEBUG 级别，让 handler 各自控制输出级别
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # 从配置获取日志级别
        log_level = getattr(logging, config.log_level, logging.INFO)

        # 控制台 handler：使用配置的日志级别
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(SimpleFormatter())
        logger.addHandler(console_handler)

    return logger


def log_with_data(
    logger: logging.Logger,
    level: int,
    msg: str,
    data: Mapping[str, object] | None = None,
) -> None:
    """带数据的日志

    自动获取调用者的文件名和行号，正确显示日志来源。
    """
    import inspect

    # 获取调用者的栈帧信息（跳过当前函数）
    frame = inspect.currentframe()
    if frame and frame.f_back:
        caller_frame = frame.f_back
        filename = caller_frame.f_code.co_filename
        lineno = caller_frame.f_lineno
        func_name = caller_frame.f_code.co_name
    else:
        filename = "(unknown)"
        lineno = 0
        func_name = "(unknown)"

    # 创建 LogRecord，使用正确的文件名和行号
    record = logger.makeRecord(
        logger.name,
        level,
        filename,
        lineno,
        msg,
        (),
        None,
        func_name,
    )
    if data:
        setattr(record, "data", dict(data))
    logger.handle(record)
