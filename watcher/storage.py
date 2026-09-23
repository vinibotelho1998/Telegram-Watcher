from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import paths
from .models import Channel, Filter

_REPLACE_ATTEMPTS = 5


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_text_atomic(path: Path, text: str) -> bool:
    temp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(text, encoding="utf-8")
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temp, path)
                return True
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.05)
    except OSError:
        pass
    try:
        temp.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def write_json(path: Path, data: Any) -> bool:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return False
    return write_text_atomic(path, text)


class FilterStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.filters_file()

    def load(self) -> list[Filter]:
        data = read_json(self.path, [])
        out: list[Filter] = []
        if isinstance(data, list):
            for item in data:
                try:
                    out.append(Filter.from_dict(item))
                except (AttributeError, KeyError, ValueError, TypeError):
                    continue
        return out

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
                except (AttributeError, KeyError, ValueError, TypeError):
                    continue
        return out

    def save(self, channels: list[Channel]) -> bool:
        return write_json(self.path, [c.to_dict() for c in channels])
