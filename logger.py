"""Application-wide logging setup.

Every module calls :func:`get_logger` instead of configuring logging itself, so
console and file output stay consistent across the Streamlit app, the CLI and
the test suite.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import LOG_DIR

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_LOG_FILE = LOG_DIR / "expense_system.log"
_configured_loggers: set[str] = set()


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger.

    Args:
        name: Logger name, usually ``__name__`` of the calling module.
        level: Minimum level to emit.

    Returns:
        A logger writing to both stderr and a rotating log file.
    """
    logger = logging.getLogger(name)
    if name in _configured_loggers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        file_handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:  # read-only filesystem (some hosted environments)
        logger.debug("File logging unavailable; continuing with console output only.")

    logger.propagate = False
    _configured_loggers.add(name)
    return logger
