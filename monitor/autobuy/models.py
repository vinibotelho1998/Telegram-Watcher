from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..matching import matches_exclude, matches_include


class BuyStatus(str, Enum):
    PIX_READY = "pix_ready"
    DRY_RUN_OK = "dry_run_ok"
    PRICE_ABOVE_LIMIT = "price_above_limit"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    LOGIN_REQUIRED = "login_required"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class WatchItem:
    id: str
    name: str
    max_price: float
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    quantity: int = 1
    enabled: bool = True
    store: str = "kabum"

    @classmethod
    def new(cls, name: str = "", max_price: float = 0.0) -> "WatchItem":
        return cls(id=f"w{int(time.time() * 1000):x}", name=name or "Novo produto", max_price=max_price)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WatchItem":
        return cls(
            id=str(raw.get("id") or f"w{int(time.time() * 1000):x}"),
            name=str(raw.get("name") or "Sem nome"),
            max_price=float(raw.get("max_price") or 0.0),
            include=[str(x) for x in (raw.get("include") or [])],
            exclude=[str(x) for x in (raw.get("exclude") or [])],
            quantity=int(raw.get("quantity") or 1),
            enabled=bool(raw.get("enabled", True)),
            store=str(raw.get("store") or "kabum"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "max_price": self.max_price,
            "include": self.include,
            "exclude": self.exclude,
            "quantity": self.quantity,
            "enabled": self.enabled,
            "store": self.store,
        }

    def matches(self, text: str) -> bool:
        if not self.enabled or not text:
            return False
        if not any(word.strip() for word in self.include):
            return False
        if matches_exclude(text, self.exclude):
            return False
        return matches_include(text, self.include)


@dataclass(slots=True)
class ProductInfo:
    id: int
    title: str
    url: str
    pix_price: float
    card_price: float
    available: bool
    seller: str = ""


@dataclass(slots=True)
class BuyResult:
    status: BuyStatus
    product: ProductInfo | None = None
    pix_code: str = ""
    screenshot: str = ""
    detail: str = ""
    elapsed: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
