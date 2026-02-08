"""
Logging Configuration for Polymarket Trading Bot

Sets up Python's logging module with:
- File handler: all messages at DEBUG level → bot.log (rotated)
- Console handler: INFO and above → stdout (with optional emoji)

Usage in any module:
    import logging
    logger = logging.getLogger(__name__)

    logger.info("Position opened: %s @ %.4f", side, price)
    logger.warning("Order stale after 30 min")
    logger.error("API call failed: %s", error)

Call setup_logging() once at bot startup (main.py or auto_trader.py).
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler


LOG_FILE = os.getenv("BOT_LOG_FILE", "bot.log")
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(os.getenv("BOT_LOG_MAX_MB", "10")) * 1024 * 1024
LOG_BACKUP_COUNT = int(os.getenv("BOT_LOG_BACKUPS", "3"))

_initialized = False


def setup_logging(
    level: str = LOG_LEVEL,
    log_file: str = LOG_FILE,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
):
    """
    Configure logging for the entire bot. Call once at startup.

    Args:
        level: Minimum log level for console output (DEBUG, INFO, WARNING, ERROR)
        log_file: Path to the log file (set to "" to disable file logging)
        max_bytes: Max log file size before rotation
        backup_count: Number of rotated log files to keep
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # Capture everything; handlers filter

    # Console handler — clean, human-readable
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level, logging.INFO))
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # File handler — detailed, machine-parseable, rotated
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        )
        file_handler.setFormatter(file_fmt)
        root.addHandler(file_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience: get a named logger (same as logging.getLogger)."""
    return logging.getLogger(name)
