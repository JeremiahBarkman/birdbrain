"""Structured logging setup (see requirements §21).

Every service should call configure_logging() once at startup instead
of using print() or bare logging.basicConfig(). Log records are
emitted as single-line JSON so they stay greppable and parseable.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_RESERVED_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message"}


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON with UTC timestamps."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp_utc": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "service": self.service_name,
            "severity": record.levelname,
            "event": getattr(record, "event", record.funcName),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_FIELDS and key != "event":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    log_level: str = "INFO",
    service_name: str = "backyard_bird",
    log_dir: Path | None = None,
) -> logging.Logger:
    """Configure root logging. Safe to call more than once (idempotent)."""
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    formatter = StructuredFormatter(service_name)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / f"{service_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return root
