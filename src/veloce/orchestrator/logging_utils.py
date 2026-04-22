import logging
import os
from collections.abc import Mapping
from typing import Any


def configure_logging() -> None:
    """Configure process-wide logging once using environment settings."""
    level_name = os.getenv("VELOCE_LOG_LEVEL", "INFO").upper().strip() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def _safe_value(value: Any, *, limit: int = 280) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value).replace("\n", "\\n").strip()
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _format_fields(fields: Mapping[str, Any]) -> str:
    if not fields:
        return ""
    ordered = [f"{key}={_safe_value(value)}" for key, value in sorted(fields.items())]
    return " " + " ".join(ordered)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(level, "%s%s", event, _format_fields(fields))


def log_info(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.INFO, event, **fields)


def log_warning(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.WARNING, event, **fields)
