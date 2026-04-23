import logging
import os
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from typing import Any

from rich.logging import RichHandler


def configure_logging() -> None:
    """Configure process-wide logging with both Rich console and rotating file handlers."""
    level_name = os.getenv("VELOCE_LOG_LEVEL", "INFO").upper().strip() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()

    # If handlers are already configured, do not duplicate them
    if root_logger.handlers:
        root_logger.setLevel(level)
        return

    root_logger.setLevel(level)

    # 1. Ensure the data directory exists
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/logs"))
    os.makedirs(log_dir, exist_ok=True)
    
    service_name = os.getenv("VELOCE_SERVICE_NAME", "orchestrator")
    log_file_path = os.path.join(log_dir, f"veloce_{service_name}.log")

    # 2. File Handler (Rotating log file: max 5MB per file, keep 3 backups)
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(file_format)

    # 3. Rich Console Handler (Colourised for local development)
    # Rich automatically adds the timestamp and log level to the console UI
    console_handler = RichHandler(rich_tracebacks=True, markup=True)
    console_format = logging.Formatter("%(name)s - %(message)s")
    console_handler.setFormatter(console_format)

    # 4. Attach both handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


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
    # Format the extra fields so they appear clearly in both file and rich console
    formatted_fields = _format_fields(fields)

    # Using a subtle colour tag for the extra fields to make the event name pop in Rich
    # The file handler will just see standard text, whilst Rich will parse the markup.
    if formatted_fields:
        message = f"[bold]{event}[/bold] [dim]{formatted_fields}[/dim]"
    else:
        message = f"[bold]{event}[/bold]"

    logger.log(level, message)


def log_info(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.INFO, event, **fields)


def log_warning(logger: logging.Logger, event: str, **fields: Any) -> None:
    log_event(logger, logging.WARNING, event, **fields)
