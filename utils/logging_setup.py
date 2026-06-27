"""
Sorachio-STS Logging Setup
Structured logging with rich console output and file rotation.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_console = Console(stderr=True)
_initialized = False


def setup_logging(
    level: str = "INFO",
    log_dir: str | None = None,
    log_file: str = "sorachio.log",
) -> logging.Logger:
    """
    Configure structured logging with:
    - Rich console handler (coloured, human-readable)
    - Rotating file handler (JSON-friendly for post-analysis)
    """
    global _initialized
    if _initialized:
        return logging.getLogger("sorachio")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Root logger
    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()

    # --- Console handler (Rich) ---
    console_handler = RichHandler(
        console=_console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        level=numeric_level,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    # --- File handler ---
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)

    # Silence noisy libraries
    for noisy in ["httpx", "httpcore", "urllib3", "asyncio"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True
    return logging.getLogger("sorachio")


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the sorachio namespace."""
    return logging.getLogger(f"sorachio.{name}")
