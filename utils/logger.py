"""Logging helpers for file and journald-friendly console output."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "logs" / "autotrader.log"


def get_logger(name: str) -> logging.Logger:
    """Return an application logger configured once per process."""
    load_dotenv(PROJECT_ROOT / ".env")

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
