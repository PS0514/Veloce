"""
Runtime configuration stored in a JSON file.

This module manages ``data/veloce_config.json`` which holds settings that the
application is allowed to *write* at runtime (OAuth tokens, selected channels,
keywords, calendar ID).  The ``.env`` file is reserved for static secrets and
infrastructure settings and is **never** modified by the program.
"""

import json
import os
from pathlib import Path
from threading import Lock

_lock = Lock()

# Default location sits next to the database.
_DEFAULT_DIR = os.getenv("VELOCE_DATA_DIR", "data")
_CONFIG_FILENAME = "veloce_config.json"


def _config_path() -> Path:
    data_dir = Path(os.getenv("VELOCE_DATA_DIR", _DEFAULT_DIR))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _CONFIG_FILENAME


def load_runtime_config() -> dict:
    """Read the config file and return a dict (empty dict if missing)."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with _lock:
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_runtime_config(data: dict) -> None:
    """Overwrite the config file with *data*."""
    path = _config_path()
    with _lock:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_config_value(key: str, default: str = "") -> str:
    """Return a single value from the config file (string)."""
    return str(load_runtime_config().get(key, default))


def set_config_value(key: str, value: str) -> None:
    """Set a single key in the config file (string)."""
    cfg = load_runtime_config()
    cfg[key] = value
    save_runtime_config(cfg)


def merge_config_values(updates: dict[str, str]) -> None:
    """Merge *updates* into the existing config without removing other keys."""
    cfg = load_runtime_config()
    cfg.update(updates)
    save_runtime_config(cfg)
