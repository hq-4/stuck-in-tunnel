"""Dual-sink logging bootstrap: Pretty Rich console + Structured JSONL file.

Usage:
    from utils.logging_setup import setup_logging
    logger = setup_logging()
    logger.info("Startup complete", extra={"subsys": "bootstrap", "event": "startup"})
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

ICON: dict[int, str] = {
    logging.DEBUG: "ℹ",
    logging.INFO: "✔",
    logging.WARNING: "⚠",
    logging.ERROR: "✖",
    logging.CRITICAL: "✖",
}

_JSONL_KEYS = (
    "ts",
    "level",
    "name",
    "subsys",
    "guild_id",
    "user_id",
    "msg_id",
    "event",
    "detail",
    "message",
)


class JSONLFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
                + f".{int(record.msecs):03d}"
            ),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        for key in ("subsys", "guild_id", "user_id", "msg_id", "event", "detail"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        return json.dumps({k: payload[k] for k in _JSONL_KEYS if k in payload})


class IconFilter(logging.Filter):
    """Injects level icon into log records for Rich console formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.icon = ICON.get(record.levelno, "•")  # type: ignore[attr-defined]
        return True


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logger with Pretty Rich console + JSONL file sinks.

    Aborts (os._exit(2)) if exactly two handlers are not active after setup.
    Safe to call multiple times — idempotent via _configured guard.
    """
    root = logging.getLogger()
    if getattr(root, "_configured", False):
        return root

    root.setLevel(level)

    # --- Pretty console sink ---
    console = Console(stderr=True, soft_wrap=False)
    pretty = RichHandler(
        console=console,
        show_path=False,
        enable_link_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=(level == logging.DEBUG),
        markup=True,
        log_time_format="%Y-%m-%d %H:%M:%S.%f",
    )
    pretty.set_name("pretty_handler")
    pretty.addFilter(IconFilter())

    # --- Structured JSONL sink ---
    log_path = os.getenv("APP_JSONL_PATH", "logs/app.jsonl")
    _ensure_dir(log_path)
    jsonl = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    jsonl.set_name("jsonl_handler")
    jsonl.setFormatter(JSONLFormatter())

    root.handlers = [pretty, jsonl]

    # --- Enforcer: abort if sinks are misconfigured ---
    names = sorted(h.get_name() for h in root.handlers)
    if names != ["jsonl_handler", "pretty_handler"]:
        sys.stderr.write(
            f"[logging-enforcer] expected pretty_handler + jsonl_handler, got {names}\n"
        )
        sys.stderr.flush()
        os._exit(2)

    root._configured = True  # type: ignore[attr-defined]
    return root
