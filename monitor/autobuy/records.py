from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .. import paths
from .models import BuyResult


@dataclass(slots=True)
class PixRecord:
    stamp: str
    title: str
    price: float
    url: str
    pix_code: str
    screenshot: str
    status: str
    created_at: str
    elapsed: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PixRecord":
        raw_timings = raw.get("timings") or {}
        return cls(
            stamp=str(raw.get("stamp") or ""),
            title=str(raw.get("title") or "Sem título"),
            price=float(raw.get("price") or 0.0),
            url=str(raw.get("url") or ""),
            pix_code=str(raw.get("pix_code") or ""),
            screenshot=str(raw.get("screenshot") or ""),
            status=str(raw.get("status") or ""),
            created_at=str(raw.get("created_at") or ""),
            elapsed=float(raw.get("elapsed") or 0.0),
            timings={str(key): float(value) for key, value in raw_timings.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stamp": self.stamp,
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "pix_code": self.pix_code,
            "screenshot": self.screenshot,
            "status": self.status,
            "created_at": self.created_at,
            "elapsed": self.elapsed,
            "timings": self.timings,
        }


def save_record(result: BuyResult, stamp: str | None = None) -> PixRecord:
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    product = result.product
    record = PixRecord(
        stamp=stamp,
        title=product.title if product else "Sem título",
        price=product.pix_price if product else 0.0,
        url=product.url if product else "",
        pix_code=result.pix_code,
        screenshot=result.screenshot,
        status=result.status.value,
        created_at=time.strftime("%d/%m/%Y %H:%M"),
        elapsed=round(result.elapsed, 1),
        timings=dict(result.timings),
    )
    path = paths.autobuy_dir() / f"{stamp}-pix.json"
    path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def delete_record(stamp: str) -> int:
    if not stamp:
        return 0
    removed = 0
    for path in paths.autobuy_dir().glob(f"{stamp}-*"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def load_records(limit: int = 50) -> list[PixRecord]:
    records: list[PixRecord] = []
    for path in paths.autobuy_dir().glob("*-pix.json"):
        try:
            records.append(PixRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    records.sort(key=lambda record: record.stamp, reverse=True)
    return records[:limit]
