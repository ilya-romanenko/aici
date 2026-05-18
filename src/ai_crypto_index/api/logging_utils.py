from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.config import dictConfig
from pathlib import Path
from typing import Any

_LOGGING_CONFIGURED = False

_RESERVED_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        # Use ISO 8601 with UTC normalisation when tzinfo missing.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON for easier ingestion."""

    def formatTime(  # noqa: D401
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Render timestamps with microseconds on platforms lacking %f support."""

        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        active_fmt = datefmt or self.datefmt

        if active_fmt:
            if "%f" in active_fmt:
                fmt_with_placeholder = active_fmt.replace("%f", "{microsecond:06d}")
                formatted = dt.strftime(fmt_with_placeholder)
                return formatted.format(microsecond=dt.microsecond)
            return dt.strftime(active_fmt)

        iso_value = dt.isoformat(timespec="milliseconds")
        return iso_value.replace("+00:00", "Z")

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED_ATTRS:
                continue
            payload[key] = value

        return json.dumps(payload, default=_json_default, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Initialise structured JSON logging once per process."""

    global _LOGGING_CONFIGURED  # noqa: PLW0603
    if _LOGGING_CONFIGURED:
        return

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "ai_crypto_index.api.logging_utils.JsonFormatter",
                    "datefmt": "%Y-%m-%dT%H:%M:%S.%fZ",
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "level": level.upper(),
                    "formatter": "json",
                }
            },
            "loggers": {
                "": {
                    "handlers": ["default"],
                    "level": level.upper(),
                },
                "ccxt": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "ccxt.base.exchange": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "uvicorn": {
                    "handlers": ["default"],
                    "level": level.upper(),
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": level.upper(),
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": level.upper(),
                    "propagate": False,
                },
            },
        }
    )

    _LOGGING_CONFIGURED = True

    # Silence noisy third-party loggers that can emit after shutdown.
    for name in ("urllib3", "urllib3.connectionpool"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.ERROR)


__all__ = ["configure_logging", "JsonFormatter"]
