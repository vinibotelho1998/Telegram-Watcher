from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import paths
from .models import ChannelKind

log = logging.getLogger("watcher.reader")

ClientProvider = Callable[[], Optional[Any]]

_RAW_LIMIT = 800

_MEDIA_LABELS: dict[str, str] = {
    "photo": "Foto",
    "video": "Vídeo",
    "voice": "Mensagem de voz",
    "audio": "Áudio",
    "sticker": "Figurinha",
    "gif": "GIF",
    "poll": "Enquete",
    "contact": "Contato",
    "location": "Localização",
    "file": "Arquivo",
}


@dataclass(slots=True)
class ChatSummary:
    id: int
    name: str
    kind: ChannelKind = ChannelKind.CHANNEL
    username: str = ""
    preview: str = ""
    date: datetime | None = None
    unread: int = 0
    pinned: bool = False
    participants: int = 0


@dataclass(slots=True)
class ChatMessage:
    id: int
    chat_id: int
    text: str = ""
    date: datetime | None = None
    sender_id: int | None = None
    sender_name: str = ""
    outgoing: bool = False
    media_kind: str = ""
    media_label: str = ""
    reply_to_id: int | None = None
    views: int = 0
    edited: bool = False
    service: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.text and not self.media_kind and not self.service


def preview_text(message: ChatMessage, limit: int = 62) -> str:
    body = " ".join((message.text or "").split())
    if not body and message.media_label:
        body = message.media_label
    if not body and message.service:
        body = message.service
    return body if len(body) <= limit else body[: limit - 1] + "…"


def dialog_kind(dialog: Any) -> ChannelKind:
    if getattr(dialog, "is_group", False):
        return ChannelKind.GROUP
    if getattr(dialog, "is_channel", False):
        return ChannelKind.CHANNEL
    return ChannelKind.USER


def detect_media(message: Any) -> tuple[str, str]:
    if getattr(message, "photo", None):
        return "photo", _MEDIA_LABELS["photo"]
    if getattr(message, "voice", None):
        return "voice", _MEDIA_LABELS["voice"]
    if getattr(message, "video", None) or getattr(message, "video_note", None):
        return "video", _MEDIA_LABELS["video"]
    if getattr(message, "gif", None):
        return "gif", _MEDIA_LABELS["gif"]
    if getattr(message, "sticker", None):
        return "sticker", _MEDIA_LABELS["sticker"]
    if getattr(message, "audio", None):
        return "audio", _MEDIA_LABELS["audio"]
    if getattr(message, "poll", None):
        return "poll", _MEDIA_LABELS["poll"]
    if getattr(message, "contact", None):
        return "contact", _MEDIA_LABELS["contact"]
    if getattr(message, "geo", None):
        return "location", _MEDIA_LABELS["location"]
    document = getattr(message, "document", None)
    if document is not None:
        return "file", _document_name(document)
    if getattr(message, "media", None):
        return "file", _MEDIA_LABELS["file"]
    return "", ""


def _document_name(document: Any) -> str:
    for attr in getattr(document, "attributes", None) or []:
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return _MEDIA_LABELS["file"]


def _sender_name(message: Any, fallback: str = "") -> str:
    author = getattr(message, "post_author", None)
    if author:
        return str(author)
    sender = getattr(message, "sender", None)
    if sender is not None:
        for attribute in ("title", "username"):
            value = getattr(sender, attribute, None)
            if value:
                return str(value)
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        full = f"{first} {last}".strip()
        if full:
            return full
    return fallback


def _service_text(message: Any) -> str:
    action = getattr(message, "action", None)
    if action is None:
        return ""
    return type(action).__name__.replace("MessageAction", "")


def build_message(message: Any, chat_id: int, chat_name: str = "") -> ChatMessage:
    kind, label = detect_media(message)
    reply = getattr(message, "reply_to", None)
    return ChatMessage(
        id=int(getattr(message, "id", 0) or 0),
        chat_id=chat_id,
        text=getattr(message, "message", None) or getattr(message, "text", "") or "",
        date=getattr(message, "date", None),
        sender_id=getattr(message, "sender_id", None),
        sender_name=_sender_name(message, chat_name),
        outgoing=bool(getattr(message, "out", False)),
        media_kind=kind,
        media_label=label,
        reply_to_id=getattr(reply, "reply_to_msg_id", None) if reply is not None else None,
        views=int(getattr(message, "views", 0) or 0),
        edited=getattr(message, "edit_date", None) is not None,
        service=_service_text(message),
    )


def build_summary(dialog: Any) -> ChatSummary:
    entity = getattr(dialog, "entity", None)
    last = getattr(dialog, "message", None)
    chat_id = int(getattr(dialog, "id", 0) or 0)
    preview = ""
    when = getattr(dialog, "date", None)
    if last is not None:
        preview = preview_text(build_message(last, chat_id))
        when = getattr(last, "date", None) or when
    return ChatSummary(
        id=chat_id,
        name=getattr(dialog, "name", None) or "Sem nome",
        kind=dialog_kind(dialog),
        username=getattr(entity, "username", None) or "",
        preview=preview,
        date=when,
        unread=int(getattr(dialog, "unread_count", 0) or 0),
        pinned=bool(getattr(dialog, "pinned", False)),
        participants=int(getattr(entity, "participants_count", 0) or 0),
    )


class ChatReader:
    def __init__(self, client_provider: ClientProvider) -> None:
        self._client_provider = client_provider
        self._raw: dict[tuple[int, int], Any] = {}
        self._no_avatar: set[int] = set()

    @property
    def client(self) -> Any | None:
        client = self._client_provider()
        if client is None or not client.is_connected():
            return None
        return client

    @property
    def available(self) -> bool:
        return self.client is not None

    def forget(self) -> None:
        self._raw.clear()
        self._no_avatar.clear()

    def remember(self, chat_id: int, message: Any) -> None:
        key = (chat_id, int(getattr(message, "id", 0) or 0))
        self._raw[key] = message
        if len(self._raw) > _RAW_LIMIT:
            for old in list(self._raw)[: _RAW_LIMIT // 2]:
                self._raw.pop(old, None)

    async def dialogs(self, limit: int = 200) -> list[ChatSummary]:
        client = self.client
        if client is None:
            return []
        out: list[ChatSummary] = []
        async for dialog in client.iter_dialogs(limit=limit):
            out.append(build_summary(dialog))
        return out

    async def history(self, chat_id: int, limit: int = 40, before_id: int | None = None) -> list[ChatMessage]:
        client = self.client
        if client is None:
            return []
        kwargs: dict[str, Any] = {"limit": limit}
        if before_id:
            kwargs["max_id"] = before_id
        out: list[ChatMessage] = []
        async for message in client.iter_messages(chat_id, **kwargs):
            self.remember(chat_id, message)
            out.append(build_message(message, chat_id))
        out.reverse()
        return out

    async def search(self, chat_id: int, query: str, limit: int = 60) -> list[ChatMessage]:
        client = self.client
        if client is None or not query.strip():
            return []
        out: list[ChatMessage] = []
        async for message in client.iter_messages(chat_id, search=query.strip(), limit=limit):
            self.remember(chat_id, message)
            out.append(build_message(message, chat_id))
        out.reverse()
        return out

    async def avatar(self, chat_id: int) -> Path | None:
        client = self.client
        if client is None or chat_id in self._no_avatar:
            return None
        target = paths.avatar_cache_dir() / f"{chat_id}.jpg"
        if target.exists():
            return target
        try:
            saved = await client.download_profile_photo(chat_id, file=str(target))
        except Exception:
            log.debug("Sem avatar para %s", chat_id, exc_info=True)
            saved = None
        if not saved:
            self._no_avatar.add(chat_id)
            return None
        return Path(saved)

    async def thumbnail(self, chat_id: int, message_id: int) -> Path | None:
        client = self.client
        raw = self._raw.get((chat_id, message_id))
        if client is None or raw is None:
            return None
        kind, _ = detect_media(raw)
        if kind not in ("photo", "video", "gif", "sticker"):
            return None
        target = paths.thumb_cache_dir() / f"{chat_id}_{message_id}.jpg"
        if target.exists():
            return target
        try:
            saved = await client.download_media(raw, file=str(target), thumb=-1)
        except Exception:
            log.debug("Sem miniatura para %s/%s", chat_id, message_id, exc_info=True)
            return None
        return Path(saved) if saved else None
