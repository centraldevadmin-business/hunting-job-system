"""
Centralized logging setup.

Every module writes to a log file (logs/hunt.log) plus the console.
Configured from settings.yaml (logging.level, logging.file).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from src.db.repository import _project_root


def _load_log_config() -> dict:
    path = _project_root() / "config" / "settings.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("logging", {}) or {}


def setup_logging(name: str = "hunt") -> logging.Logger:
    """Return a configured logger. Idempotent — safe to call multiple times."""
    cfg = _load_log_config()
    level_name = cfg.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = cfg.get("file", "logs/hunt.log")

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers if called repeatedly.
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler
    log_path = _project_root() / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "hunt") -> logging.Logger:
    """Convenience accessor — returns the shared logger."""
    return setup_logging(name)
