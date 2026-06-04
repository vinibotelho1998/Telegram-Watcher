from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EngineState(str, Enum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    AUTH = "auth"
    ONLINE = "online"
    STOPPING = "stopping"
    ERROR = "error"


class ChannelKind(str, Enum):
    CHANNEL = "channel"
    GROUP = "group"
    USER = "user"


class LogLevel(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CAPTURE = "capture"


@dataclass(slots=True)
class Channel:
    id: int
    name: str = ""
    kind: ChannelKind = ChannelKind.CHANNEL

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Channel":
        value = raw.get("kind") or raw.get("type") or "channel"
        try:
            kind = ChannelKind(value)
        except ValueError:
            kind = ChannelKind.CHANNEL
        return cls(id=int(raw["id"]), name=str(raw.get("name", "")), kind=kind)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind.value}


@dataclass(slots=True)
class Filter:
    id: str
    name: str = "Sem nome"
    enabled: bool = True
    source_ids: list[int] = field(default_factory=list)
    destination_ids: list[int] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    smart_rules: list[str] = field(default_factory=list)
    min_confidence: float = 0.0

    @classmethod
    def new(cls, name: str = "") -> "Filter":
        return cls(id=f"f{int(time.time() * 1000):x}", name=name or "Novo filtro")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Filter":
        include = raw.get("include") or raw.get("include_words") or []
        exclude = raw.get("exclude") or raw.get("exclude_words") or []
        destinations = list(raw.get("destination_ids") or [])
        if not destinations and raw.get("destination_id"):
            destinations = [raw["destination_id"]]
        return cls(
            id=str(raw.get("id") or f"f{int(time.time() * 1000):x}"),
            name=str(raw.get("name") or "Sem nome"),
            enabled=bool(raw.get("enabled", True)),
            source_ids=[int(x) for x in (raw.get("source_ids") or [])],
            destination_ids=[int(x) for x in destinations],
            include=[str(x) for x in include],
            exclude=[str(x) for x in exclude],
            smart_rules=[str(x) for x in (raw.get("smart_rules") or [])],
            min_confidence=float(raw.get("min_confidence") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "source_ids": self.source_ids,
            "destination_ids": self.destination_ids,
            "include": self.include,
            "exclude": self.exclude,
            "smart_rules": self.smart_rules,
            "min_confidence": self.min_confidence,
        }

    def applies_to(self, chat_id: int) -> bool:
        return not self.source_ids or chat_id in self.source_ids


@dataclass(slots=True)
class Metrics:
    received: int = 0
    forwarded: int = 0
    failed: int = 0
    filtered: int = 0
    errors: int = 0
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)
    started_at: float | None = None
    last_message_at: float | None = None

    @property
    def uptime(self) -> float:
        return 0.0 if self.started_at is None else max(0.0, time.time() - self.started_at)

    @property
    def success_rate(self) -> float:
        total = self.forwarded + self.failed
        return 100.0 if total == 0 else self.forwarded / total * 100.0

    def mark_capture(self) -> None:
        self.received += 1
        self.last_message_at = time.time()
        self.by_hour[datetime.now().hour] += 1

    def reset(self) -> None:
        self.received = self.forwarded = self.failed = self.filtered = self.errors = 0
        self.by_hour = [0] * 24
        self.started_at = None
        self.last_message_at = None
