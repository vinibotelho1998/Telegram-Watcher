from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..storage import read_json, write_json
from .models import WatchItem


@dataclass(slots=True)
class AutobuySettings:
    enabled: bool = False
    real_mode: bool = False
    use_coupon: bool = True
    headless: bool = False
    check_hours: int = 6
    known_stores: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AutobuySettings":
        return cls(
            enabled=bool(raw.get("enabled", False)),
            real_mode=bool(raw.get("real_mode", False)),
            use_coupon=bool(raw.get("use_coupon", True)),
            headless=bool(raw.get("headless", False)),
            check_hours=max(1, int(raw.get("check_hours") or 6)),
            known_stores=[str(x) for x in (raw.get("known_stores") or []) if str(x)],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "real_mode": self.real_mode,
            "use_coupon": self.use_coupon,
            "headless": self.headless,
            "check_hours": self.check_hours,
            "known_stores": self.known_stores,
        }


class AutobuyStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.autobuy_config_file()

    def load(self) -> tuple[AutobuySettings, list[WatchItem]]:
        data = read_json(self.path, {})
        if not isinstance(data, dict):
            data = {}
        try:
            settings = AutobuySettings.from_dict(data.get("settings") or {})
        except (AttributeError, ValueError, TypeError):
            settings = AutobuySettings()
        items: list[WatchItem] = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            try:
                items.append(WatchItem.from_dict(item))
            except (ValueError, TypeError):
                continue
        return settings, items

    def save(self, settings: AutobuySettings, items: list[WatchItem]) -> bool:
        return write_json(self.path, {"settings": settings.to_dict(), "items": [i.to_dict() for i in items]})
