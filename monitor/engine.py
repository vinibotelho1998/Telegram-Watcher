from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import time
from collections.abc import Awaitable, Callable
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from . import VERSION, paths
from .config import AppConfig
from .filtering import resolve_destinations
from .models import Channel, ChannelKind, EngineState, Filter, LogLevel, Metrics
from .reader import ChatReader, build_message

log = logging.getLogger("monitor.engine")

StateCb = Callable[[EngineState, str], None]
LogCb = Callable[[str, LogLevel], None]
EventCb = Callable[[dict], None]
MetricsCb = Callable[[], None]
TextCb = Callable[[str], None]
IncomingCb = Callable[[int, object], None]
CodeProvider = Callable[[str], Awaitable[Optional[str]]]
PasswordProvider = Callable[[], Awaitable[Optional[str]]]

_SEND_INTERVAL = 0.8
_SEEN_LIMIT = 2000


def _noop(*_args, **_kwargs) -> None:
    return None


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _preview(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MonitorEngine:
    def __init__(
        self,
        config: AppConfig,
        filter_provider: Callable[[], list[Filter]],
        *,
        on_state: StateCb | None = None,
        on_log: LogCb | None = None,
        on_event: EventCb | None = None,
        on_metrics: MetricsCb | None = None,
        on_text: TextCb | None = None,
        on_incoming: IncomingCb | None = None,
        code_provider: CodeProvider | None = None,
        password_provider: PasswordProvider | None = None,
    ) -> None:
        self.config = config
        self._filters = filter_provider
        self._on_state = on_state or _noop
        self._on_log = on_log or _noop
        self._on_event = on_event or _noop
        self._on_metrics = on_metrics or _noop
        self._on_text = on_text or _noop
        self._on_incoming = on_incoming or _noop
        self._code_provider = code_provider
        self._password_provider = password_provider

        self.state = EngineState.OFFLINE
        self.metrics = Metrics()
        self.reader = ChatReader(lambda: self._user)

        self._user: TelegramClient | None = None
        self._bot: TelegramClient | None = None
        self._me = None
        self._bot_me = None
        self._handler = None
        self._seen: set[tuple[int, int]] = set()
        self._send_lock = asyncio.Lock()
        self._last_send = 0.0

    @property
    def is_running(self) -> bool:
        return self.state in (EngineState.ONLINE, EngineState.CONNECTING, EngineState.AUTH)

    def _set_state(self, state: EngineState, detail: str = "") -> None:
        self.state = state
        self._on_state(state, detail)

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        self._on_log(message, level)

    async def start(self) -> bool:
        if self.is_running:
            return True
        if not self.config.is_ready:
            self._set_state(EngineState.ERROR, "Credenciais incompletas")
            self._log("Configure API ID, API Hash e telefone", LogLevel.ERROR)
            return False
        try:
            self._set_state(EngineState.CONNECTING)
            self._seen.clear()
            await self._connect()
            if not await self._authenticate():
                await self.stop()
                return False
            self._register_handler()
            self.metrics.reset()
            self.metrics.started_at = time.time()
            self._set_state(EngineState.ONLINE)
            self._log("Sistema online e monitorando", LogLevel.SUCCESS)
            self._on_metrics()
            return True
        except Exception as exc:
            log.exception("Falha ao iniciar engine")
            self._set_state(EngineState.ERROR, str(exc))
            self._log(f"Erro ao iniciar: {exc}", LogLevel.ERROR)
            await self.stop()
            return False

    async def _connect(self) -> None:
        session = str(paths.sessions_dir() / "user")
        self._user = TelegramClient(
            session,
            self.config.api_id,
            self.config.api_hash,
            device_model="Dbx Telegram Monitor",
            app_version=VERSION,
        )
        await self._user.connect()
        if self.config.bot_token:
            self._bot = TelegramClient(
                str(paths.sessions_dir() / "bot"),
                self.config.api_id,
                self.config.api_hash,
            )

    async def _authenticate(self) -> bool:
        self._set_state(EngineState.AUTH)
        if not await self._user.is_user_authorized():
            if not self._code_provider:
                self._log("Sem provedor de código de login", LogLevel.ERROR)
                return False
            self._log(f"Enviando código para {self.config.phone}", LogLevel.INFO)
            await self._user.send_code_request(self.config.phone)
            code = await self._code_provider(self.config.phone)
            if not code:
                self._log("Login cancelado", LogLevel.WARNING)
                return False
            try:
                await self._user.sign_in(self.config.phone, code.strip())
            except PhoneCodeInvalidError:
                self._log("Código inválido", LogLevel.ERROR)
                return False
            except SessionPasswordNeededError:
                if not self._password_provider:
                    self._log("Verificação em duas etapas necessária", LogLevel.ERROR)
                    return False
                password = await self._password_provider()
                if not password:
                    return False
                await self._user.sign_in(password=password.strip())
        self._me = await self._user.get_me()
        self._log(f"Conectado como {self._me.first_name}", LogLevel.SUCCESS)
        if self._bot:
            try:
                await self._bot.start(bot_token=self.config.bot_token)
                self._bot_me = await self._bot.get_me()
                self._log(f"Bot @{self._bot_me.username} pronto", LogLevel.SUCCESS)
            except Exception as exc:
                self._log(f"Bot indisponível: {exc}", LogLevel.WARNING)
                self._bot = None
        return True

    def _register_handler(self) -> None:
        self._handler = self._user.add_event_handler(self._on_new_message, events.NewMessage())

    async def _on_new_message(self, event) -> None:
        try:
            await self._handle(event)
        except Exception:
            log.exception("Erro ao processar mensagem")
            self.metrics.errors += 1
            self._on_metrics()

    async def _handle(self, event) -> None:
        chat_id = event.chat_id
        self._publish_incoming(chat_id, event.message)
        if chat_id not in self.config.source_ids:
            return
        sender_id = event.sender_id
        if self._bot_me is not None and sender_id == self._bot_me.id:
            return
        if self._me is not None and sender_id == self._me.id and not self.config.allow_self:
            return

        message = event.message
        key = (chat_id, message.id)
        if key in self._seen:
            return
        self._seen.add(key)
        if len(self._seen) > _SEEN_LIMIT:
            self._seen = set(list(self._seen)[-_SEEN_LIMIT // 2:])

        text = message.message or getattr(message, "text", "") or ""
        self.metrics.mark_capture()
        source = self.config.channel_name(chat_id)
        self._on_event(
            {"time": _now(), "source": source, "text": _preview(text), "status": "captured", "chat_id": chat_id}
        )
        self._log(f"Capturado de {source}", LogLevel.CAPTURE)
        self._on_metrics()
        try:
            self._on_text(text)
        except Exception:
            log.exception("Erro no hook de texto")

        destinations = resolve_destinations(self._filters(), chat_id, text, self.config.destination_ids)
        if not destinations:
            self.metrics.filtered += 1
            self._on_metrics()
            return
        await self._forward(message, destinations)

    def _publish_incoming(self, chat_id: int, message) -> None:
        if message is None:
            return
        try:
            self.reader.remember(chat_id, message)
            self._on_incoming(chat_id, build_message(message, chat_id))
        except Exception:
            log.exception("Erro ao publicar mensagem para a leitura")

    async def _forward(self, message, destinations: list[int]) -> None:
        for dest in destinations:
            if not dest:
                continue
            name = self.config.channel_name(dest)
            try:
                await self._send(message, dest)
                self.metrics.forwarded += 1
                self._on_event({"time": _now(), "source": name, "text": "Replicado", "status": "forwarded", "chat_id": dest})
                self._log(f"Enviado para {name}", LogLevel.SUCCESS)
            except FloodWaitError as exc:
                self._log(f"Limite do Telegram: aguardando {exc.seconds}s", LogLevel.WARNING)
                await asyncio.sleep(exc.seconds + 1)
                try:
                    await self._send(message, dest)
                    self.metrics.forwarded += 1
                except Exception as retry_exc:
                    self.metrics.failed += 1
                    self._log(f"Falha em {name}: {retry_exc}", LogLevel.ERROR)
            except Exception as exc:
                self.metrics.failed += 1
                self._on_event({"time": _now(), "source": name, "text": f"Erro: {exc}", "status": "failed", "chat_id": dest})
                self._log(f"Falha em {name}: {exc}", LogLevel.ERROR)
            self._on_metrics()

    async def _send(self, message, dest: int) -> None:
        async with self._send_lock:
            elapsed = time.monotonic() - self._last_send
            if elapsed < _SEND_INTERVAL:
                await asyncio.sleep(_SEND_INTERVAL - elapsed)
            if self._bot is not None:
                await self._copy_as_bot(message, dest)
            else:
                await self._send_as_user(message, dest)
            self._last_send = time.monotonic()

    async def _copy_as_bot(self, message, dest: int) -> None:
        caption = message.message or getattr(message, "text", "") or ""
        entities = getattr(message, "entities", None) or None
        if message.media and not getattr(message, "web_preview", None):
            data = await self._user.download_media(message, file=bytes)
            if data:
                buffer = io.BytesIO(data)
                document = getattr(message, "document", None)
                if document:
                    buffer.name = self._document_name(document)
                    await self._bot.send_file(
                        dest,
                        buffer,
                        caption=caption,
                        formatting_entities=entities,
                        attributes=list(document.attributes),
                        force_document=False,
                    )
                else:
                    buffer.name = "image.jpg"
                    await self._bot.send_file(
                        dest,
                        buffer,
                        caption=caption,
                        formatting_entities=entities,
                    )
                return
        if caption:
            await self._bot.send_message(dest, caption, formatting_entities=entities)

    @staticmethod
    def _document_name(document) -> str:
        for attr in document.attributes:
            name = getattr(attr, "file_name", None)
            if name:
                return name
        extension = mimetypes.guess_extension(getattr(document, "mime_type", "") or "") or ".bin"
        return f"arquivo{extension}"

    async def _send_as_user(self, message, dest: int) -> None:
        try:
            await self._user.forward_messages(dest, message)
        except (FloodWaitError, asyncio.CancelledError):
            raise
        except Exception:
            caption = message.message or getattr(message, "text", "") or ""
            if message.media:
                await self._user.send_file(dest, message.media, caption=caption)
            else:
                await self._user.send_message(dest, caption)

    async def stop(self) -> None:
        if self.state == EngineState.OFFLINE:
            return
        self._set_state(EngineState.STOPPING)
        if self._user is not None and self._handler is not None:
            try:
                self._user.remove_event_handler(self._handler)
            except Exception:
                pass
        for client in (self._user, self._bot):
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        self._user = self._bot = self._handler = None
        self._me = self._bot_me = None
        self.reader.forget()
        self.metrics.started_at = None
        self._set_state(EngineState.OFFLINE)
        self._log("Monitor parado", LogLevel.INFO)
        self._on_metrics()

    async def notify_me(self, message: str, file: str | None = None) -> bool:
        if self._user is None or not self._user.is_connected():
            return False
        try:
            await self._user.send_message("me", message)
            if file:
                await self._user.send_file("me", file)
            return True
        except Exception:
            log.exception("Falha ao enviar notificação")
            return False

    async def sync_channels(self) -> list[Channel]:
        if self._user is None or not self._user.is_connected():
            return []
        result: list[Channel] = []
        async for dialog in self._user.iter_dialogs(limit=400):
            if dialog.is_channel:
                kind = ChannelKind.CHANNEL
            elif dialog.is_group:
                kind = ChannelKind.GROUP
            else:
                kind = ChannelKind.USER
            result.append(Channel(id=dialog.id, name=dialog.name or "Sem nome", kind=kind))
        return result
