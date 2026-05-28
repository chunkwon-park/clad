"""Logging setup using rich."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler

from . import paths

_CONFIGURED = False


def get(name: str = "clad") -> logging.Logger:
    return logging.getLogger(name)


def setup(level: str | None = None, file_log: bool = False) -> logging.Logger:
    global _CONFIGURED
    log = logging.getLogger("clad")
    if _CONFIGURED:
        return log

    lvl_name = (level or os.environ.get("CLAD_LOG_LEVEL") or "INFO").upper()
    log.setLevel(getattr(logging, lvl_name, logging.INFO))
    log.propagate = False

    handler: logging.Handler = RichHandler(
        rich_tracebacks=True,
        show_time=False,
        show_path=False,
        markup=False,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)

    if file_log:
        paths.ensure_layout()
        fh = RotatingFileHandler(
            paths.bridge_log(), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        log.addHandler(fh)

    _CONFIGURED = True
    return log
