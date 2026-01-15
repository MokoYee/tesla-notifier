"""日志工具"""

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON 格式日志"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "data") and record.data:
            log_entry["data"] = record.data

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建 JSON 格式日志器"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
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
