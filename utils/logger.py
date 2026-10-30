"""
Logging Module.

Cung cấp logger chuẩn hóa cho toàn bộ hệ thống với colored output
và format thống nhất.
"""

import logging
import sys


# Định nghĩa ANSI color codes cho từng log level
_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",     # Cyan
    logging.INFO: "\033[32m",      # Green
    logging.WARNING: "\033[33m",   # Yellow
    logging.ERROR: "\033[31m",     # Red
    logging.CRITICAL: "\033[35m",  # Magenta
}
_RESET = "\033[0m"


class _ColoredFormatter(logging.Formatter):
    """Formatter với ANSI color codes cho console output."""

    def __init__(self, fmt: str, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt)

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{_RESET}"
        return super().format(record)


# Cache các logger đã tạo để tránh thêm handler trùng lặp
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    """Tạo và trả về một logger đã được cấu hình.

    Args:
        name: Tên của logger (thường là __name__ của module gọi).

    Returns:
        logging.Logger: Logger với console handler và colored formatting.
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Tránh thêm handler nếu đã có (khi module bị import nhiều lần)
    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        formatter = _ColoredFormatter(
            fmt="[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # Ngăn log lan truyền lên root logger (tránh bị ghi đôi)
    logger.propagate = False

    _loggers[name] = logger
    return logger
