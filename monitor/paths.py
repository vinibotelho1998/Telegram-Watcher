from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> Path:
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def assets_path(*parts: str) -> Path:
    return resource_path("assets", *parts)


def app_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(root) / APP_ID
    else:
        path = Path.home() / f".{APP_ID.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sub(name: str) -> Path:
    path = app_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _sub("data")


def sessions_dir() -> Path:
    return _sub("sessions")


def logs_dir() -> Path:
    return _sub("logs")


def env_file() -> Path:
    return app_dir() / ".env"


def filters_file() -> Path:
    return data_dir() / "filters.json"


def channels_cache_file() -> Path:
    return data_dir() / "channels_cache.json"
