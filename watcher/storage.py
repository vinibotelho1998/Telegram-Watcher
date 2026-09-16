from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths
from .models import Channel, Filter


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except (OSError, TypeError):
        return False


class FilterStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.filters_file()

    def load(self) -> list[Filter]:
        data = read_json(self.path, [])
        return [Filter.from_dict(item) for item in data] if isinstance(data, list) else []

    def save(self, filters: list[Filter]) -> bool:
        return write_json(self.path, [f.to_dict() for f in filters])


class ChannelCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.channels_cache_file()

    def load(self) -> list[Channel]:
        data = read_json(self.path, [])
        out: list[Channel] = []
        if isinstance(data, list):
            for item in data:
                try:
                    out.append(Channel.from_dict(item))
                except (KeyError, ValueError, TypeError):
                    continue
        return out

    def save(self, channels: list[Channel]) -> bool:
        return write_json(self.path, [c.to_dict() for c in channels])
