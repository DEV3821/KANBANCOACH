"""Logging setup for SAMI Kanban Coach Phase 0."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_dir: str | Path, level: int = logging.DEBUG) -> logging.Logger:
    """Configure and return the application logger.

    Logs to both file (DEBUG) and console (INFO).

    Args:
        log_dir: Directory path for the log file.
        level: Logging level for the file handler (default DEBUG).

    Returns:
        Configured root logger for sami_kanban_coach.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "email_recall.log"

    logger = logging.getLogger("sami_kanban_coach")
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on re-init
    if logger.handlers:
        return logger

    # File handler — verbose
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(fh)

    # Console handler — INFO+
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(
        logging.Formatter("%(levelname)s: %(message)s")
    )
    logger.addHandler(ch)

    logger.info("Logging initialised — log file: %s", log_file)
    return logger
