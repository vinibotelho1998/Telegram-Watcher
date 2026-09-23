from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from dotenv import dotenv_values

from . import paths
from .storage import write_text_atomic

_ALIAS_SEPARATOR = re.compile(r",(?=-?\d+:)")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_ids(value: Any) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _parse_aliases(value: Any) -> dict[int, str]:
    if not value:
        return {}
    out: dict[int, str] = {}
    for pair in _ALIAS_SEPARATOR.split(str(value)):
        if ":" not in pair:
            continue
        raw_id, _, name = pair.partition(":")
        try:
            out[int(raw_id.strip())] = name.strip()
        except ValueError:
            continue
    return out


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


@dataclass
class AppConfig:
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    bot_token: str = ""
    source_ids: list[int] = field(default_factory=list)
    destination_ids: list[int] = field(default_factory=list)
    aliases: dict[int, str] = field(default_factory=dict)
    auto_start: bool = False
    allow_self: bool = False
    debug: bool = False
    dark_theme: bool = True
    tray_hint_shown: bool = False

    @classmethod
    def load(cls) -> "AppConfig":
        path = paths.env_file()
        raw = dotenv_values(path, encoding="utf-8", interpolate=False) if path.exists() else {}
        return cls(
            api_id=_to_int(raw.get("API_ID")),
            api_hash=(raw.get("API_HASH") or "").strip(),
            phone=(raw.get("PHONE_NUMBER") or "").strip(),
            bot_token=(raw.get("BOT_TOKEN") or "").strip(),
            source_ids=_parse_ids(raw.get("SOURCE_IDS")),
            destination_ids=_parse_ids(raw.get("DESTINATION_IDS")),
            aliases=_parse_aliases(raw.get("SOURCE_ALIASES")),
            auto_start=_to_bool(raw.get("AUTO_START")),
            allow_self=_to_bool(raw.get("ALLOW_SELF_MESSAGES")),
            debug=_to_bool(raw.get("DEBUG")),
            dark_theme=_to_bool(raw.get("DARK_THEME"), default=True),
            tray_hint_shown=_to_bool(raw.get("TRAY_HINT_SHOWN")),
        )

    def reload(self) -> None:
        fresh = AppConfig.load()
        for f in fresh.__dataclass_fields__:
            setattr(self, f, getattr(fresh, f))

    def save(self) -> bool:
        aliases = ",".join(f"{cid}:{' '.join(name.split())}" for cid, name in self.aliases.items() if name)
        lines = [
            f"API_ID={self.api_id}",
            f"API_HASH={self.api_hash}",
            f"PHONE_NUMBER={self.phone}",
            f"BOT_TOKEN={self.bot_token}",
            f"SOURCE_IDS={','.join(map(str, self.source_ids))}",
            f"DESTINATION_IDS={','.join(map(str, self.destination_ids))}",
            f"SOURCE_ALIASES={_quote(aliases)}",
            f"AUTO_START={str(self.auto_start).lower()}",
            f"ALLOW_SELF_MESSAGES={str(self.allow_self).lower()}",
            f"DEBUG={str(self.debug).lower()}",
            f"DARK_THEME={str(self.dark_theme).lower()}",
            f"TRAY_HINT_SHOWN={str(self.tray_hint_shown).lower()}",
            "",
        ]
        return write_text_atomic(paths.env_file(), "\n".join(lines))

    @property
    def is_ready(self) -> bool:
        return bool(self.api_id and self.api_hash and self.phone)

    def channel_name(self, chat_id: int) -> str:
        return self.aliases.get(chat_id) or f"ID {chat_id}"
