from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import time
from collections.abc import Awaitable, Callable
from typing import Optional

from telethon import Button, TelegramClient, events
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from . import VERSION, paths
from .config import AppConfig
from .filtering import resolve_destinations
from .models import Channel, EngineState, Filter, LogLevel, Metrics
from .reader import ChatReader, build_message, detect_media, dialog_kind

log = logging.getLogger("watcher.engine")

StateCb = Callable[[EngineState, str], None]
LogCb = Callable[[str, LogLevel], None]
EventCb = Callable[[dict], None]
MetricsCb = Callable[[], None]
TextCb = Callable[[str], None]
IncomingCb = Callable[[int, object], None]
CodeProvider = Callable[..., Awaitable[Optional[str]]]
PasswordProvider = Callable[..., Awaitable[Optional[str]]]

_SEND_INTERVAL = 0.8
_SEEN_LIMIT = 2000
_RECONNECT_DELAYS = (5, 15, 30, 60)
_LOGIN_ATTEMPTS = 3


class _Parado(Exception):
    pass


def _noop(*_args, **_kwargs) -> None:
    return None


def _minutes(seconds: int) -> str:
    minutos = max(1, -(-int(seconds) // 60))
    if minutos >= 120:
        return f"{-(-minutos // 60)} horas"
    return f"{minutos} minuto{'s' if minutos > 1 else ''}"


def explain_error(exc: BaseException) -> str:
    if isinstance(exc, ApiIdInvalidError):
        return "API ID ou API Hash inválidos. Confira os valores em my.telegram.org."
    if isinstance(exc, PhoneNumberInvalidError):
        return "Telefone inválido. Use o número com código do país, ex: +55 11 99999-9999."
    if isinstance(exc, PhoneNumberBannedError):
        return "Este número foi banido pelo Telegram."
    if isinstance(exc, PhoneCodeExpiredError):
        return "O código de login expirou. Tente conectar de novo."
    if isinstance(exc, PasswordHashInvalidError):
        return "Senha de duas etapas incorreta."
    if isinstance(exc, FloodWaitError):
        return f"O Telegram limitou as tentativas. Aguarde {_minutes(exc.seconds)} e tente de novo."
    if isinstance(exc, OSError):
        return "Sem conexão com o Telegram. Confira a internet."
    return str(exc) or type(exc).__name__


def message_links(message) -> list[str]:
    links: list[str] = []
    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if isinstance(url, str) and url and url not in links:
            links.append(url)
    for _label, url in _url_buttons(message):
        if url not in links:
            links.append(url)
    return links


def _url_buttons(message) -> list[tuple[str, str]]:
    markup = getattr(message, "reply_markup", None)
    out: list[tuple[str, str]] = []
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if isinstance(url, str) and url:
                out.append((str(getattr(button, "text", "") or url), url))
    return out


def text_with_links(message) -> str:
    text = message.message or getattr(message, "text", "") or ""
    extra = [url for url in message_links(message) if url not in text]
    return "\n".join([text, *extra]) if extra else text


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
        self._seen: dict[tuple[int, int], None] = {}
        self._send_lock = asyncio.Lock()
        self._last_send = 0.0
        self._stop_event: asyncio.Event | None = None
        self._vigia: asyncio.Task | None = None
        self.last_error = ""

    @property
    def is_running(self) -> bool:
        return self.state in (
            EngineState.ONLINE,
            EngineState.CONNECTING,
            EngineState.AUTH,
            EngineState.RECONNECTING,
        )

    def _set_state(self, state: EngineState, detail: str = "") -> None:
        self.state = state
        self._on_state(state, detail)

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        self._on_log(message, level)

    def _fail(self, message: str, level: LogLevel = LogLevel.ERROR) -> bool:
        self.last_error = message
        self._log(message, level)
        return False

    async def start(self, *, insistir: bool = False) -> bool:
        if self.is_running:
            return True
        if self.state == EngineState.STOPPING:
            self._log("Aguarde o monitor terminar de parar", LogLevel.WARNING)
            return False
        self.last_error = ""
        if not self.config.is_ready:
            self.last_error = "Configure API ID, API Hash e telefone"
            self._set_state(EngineState.ERROR, "Credenciais incompletas")
            self._log(self.last_error, LogLevel.ERROR)
            return False
        self._stop_event = asyncio.Event()
        try:
            self._set_state(EngineState.CONNECTING)
            self._seen.clear()
            await self._connect(insistir)
            if not await self._authenticate():
                await self.stop()
                return False
            self._register_handler()
            self.metrics.reset()
            self.metrics.started_at = time.time()
            self._set_state(EngineState.ONLINE)
            self._log("Sistema online e monitorando", LogLevel.SUCCESS)
            if not self.config.source_ids:
                self._log(
                    "Nenhuma origem marcada: em Canais, marque Origem nos canais que quer monitorar",
                    LogLevel.WARNING,
                )
            self._on_metrics()
            self._watch_connection()
            return True
        except _Parado:
            return False
        except Exception as exc:
            log.exception("Falha ao iniciar engine")
            self.last_error = explain_error(exc)
            self._set_state(EngineState.ERROR, self.last_error)
            self._log(f"Erro ao iniciar: {self.last_error}", LogLevel.ERROR)
            await self.stop()
            return False

    async def _wait_stop(self, seconds: float) -> bool:
        event = self._stop_event
        if event is None:
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return False
        return True

    async def _connect(self, insistir: bool = False) -> None:
        session = str(paths.sessions_dir() / "user")
        self._user = TelegramClient(
            session,
            self.config.api_id,
            self.config.api_hash,
            device_model="Telegram Watcher",
            app_version=VERSION,
        )
        parada = self._stop_event
        tentativa = 0
        while True:
            try:
                await self._user.connect()
                break
            except OSError as exc:
                if not insistir:
                    raise
                if parada is None or parada.is_set():
                    raise _Parado() from exc
                espera = _RECONNECT_DELAYS[min(tentativa, len(_RECONNECT_DELAYS) - 1)]
                tentativa += 1
                self._set_state(EngineState.RECONNECTING)
                self._log(f"{explain_error(exc)} Nova tentativa em {espera}s", LogLevel.WARNING)
                if await self._wait_stop(espera):
                    raise _Parado()
                self._set_state(EngineState.CONNECTING)
        if self.config.bot_token:
            self._bot = TelegramClient(
                str(paths.sessions_dir() / "bot"),
                self.config.api_id,
                self.config.api_hash,
            )

    def _watch_connection(self) -> None:
        client = self._user
        if client is None or not hasattr(client, "disconnected"):
            return
        self._vigia = asyncio.get_running_loop().create_task(self._vigiar(client))

    async def _vigiar(self, client) -> None:
        while True:
            try:
                await client.disconnected
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if self._stop_event is None or self._stop_event.is_set() or self._user is not client:
                return
            if not await self._reconectar(client):
                return

    async def _reconectar(self, client) -> bool:
        self._set_state(EngineState.RECONNECTING)
        self._log("Conexão com o Telegram caiu. Tentando reconectar...", LogLevel.WARNING)
        tentativa = 0
        while True:
            espera = _RECONNECT_DELAYS[min(tentativa, len(_RECONNECT_DELAYS) - 1)]
            tentativa += 1
            if await self._wait_stop(espera):
                return False
            try:
                await client.connect()
                me = await client.get_me()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                proxima = _RECONNECT_DELAYS[min(tentativa, len(_RECONNECT_DELAYS) - 1)]
                self._log(f"Ainda sem conexão: {explain_error(exc)} Nova tentativa em {proxima}s", LogLevel.WARNING)
                continue
            if self._stop_event is None or self._stop_event.is_set() or self._user is not client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return False
            if me is None:
                self._fail("A sessão do Telegram foi encerrada. Conecte a conta de novo.")
                self._set_state(EngineState.ERROR, self.last_error)
                await self.stop()
                return False
            if self._bot is not None and not self._bot.is_connected():
                try:
                    await self._bot.connect()
                except Exception as exc:
                    self._log(f"Bot ainda desconectado: {explain_error(exc)}", LogLevel.WARNING)
            self._set_state(EngineState.ONLINE)
            self._log("Reconectado ao Telegram", LogLevel.SUCCESS)
            try:
                await client.catch_up()
            except Exception:
                log.exception("Falha ao buscar o que chegou durante a queda")
            return True

    async def _ask_code(self, aviso: str) -> str:
        if aviso:
            code = await self._code_provider(self.config.phone, aviso)
        else:
            code = await self._code_provider(self.config.phone)
        return "".join((code or "").split())

    async def _ask_password(self, aviso: str) -> str:
        password = await (self._password_provider(aviso) if aviso else self._password_provider())
        return (password or "").strip()

    async def _sign_in_with_code(self) -> bool:
        aviso = ""
        for _ in range(_LOGIN_ATTEMPTS):
            code = await self._ask_code(aviso)
            if not code:
                return self._fail("Login cancelado", LogLevel.WARNING)
            try:
                await self._user.sign_in(self.config.phone, code)
                return True
            except PhoneCodeInvalidError:
                aviso = "Código incorreto. Confira no app do Telegram e digite de novo."
                self._log("Código inválido", LogLevel.WARNING)
            except PhoneCodeExpiredError:
                await self._user.send_code_request(self.config.phone)
                aviso = "O código expirou. Enviamos um novo: confira no app do Telegram."
                self._log("Código expirado, novo código enviado", LogLevel.WARNING)
            except SessionPasswordNeededError:
                return await self._sign_in_with_password()
        return self._fail("Código incorreto em todas as tentativas. Tente conectar de novo.")

    async def _sign_in_with_password(self) -> bool:
        if not self._password_provider:
            return self._fail("Verificação em duas etapas necessária")
        aviso = ""
        for _ in range(_LOGIN_ATTEMPTS):
            password = await self._ask_password(aviso)
            if not password:
                return self._fail("Login cancelado", LogLevel.WARNING)
            try:
                await self._user.sign_in(password=password)
                return True
            except PasswordHashInvalidError:
                aviso = "Senha incorreta. Tente de novo."
                self._log("Senha de duas etapas incorreta", LogLevel.WARNING)
        return self._fail("Senha de duas etapas incorreta em todas as tentativas.")

    async def _authenticate(self) -> bool:
        self._set_state(EngineState.AUTH)
        if not await self._user.is_user_authorized():
            if not self._code_provider:
                return self._fail("Sem provedor de código de login")
            self._log(f"Enviando código para {self.config.phone}", LogLevel.INFO)
            await self._user.send_code_request(self.config.phone)
            if not await self._sign_in_with_code():
                return False
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
        self._seen[key] = None
        if len(self._seen) > _SEEN_LIMIT:
            self._seen = dict.fromkeys(list(self._seen)[-(_SEEN_LIMIT // 2):])

        text = message.message or getattr(message, "text", "") or ""
        full_text = text_with_links(message)
        self.metrics.mark_capture()
        source = self.config.channel_name(chat_id)
        self._on_event(
            {"time": _now(), "source": source, "text": _preview(text), "status": "captured", "chat_id": chat_id}
        )
        self._log(f"Capturado de {source}", LogLevel.CAPTURE)
        self._on_metrics()
        try:
            self._on_text(full_text)
        except Exception:
            log.exception("Erro no hook de texto")

        marked = set(self.config.destination_ids)
        destinations = [
            dest
            for dest in resolve_destinations(self._filters(), chat_id, full_text, self.config.destination_ids)
            if dest != chat_id and dest in marked
        ]
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
                try:
                    sent = await self._send(message, dest)
                except FloodWaitError as exc:
                    self._log(f"Limite do Telegram: aguardando {exc.seconds}s", LogLevel.WARNING)
                    await asyncio.sleep(exc.seconds + 1)
                    sent = await self._send(message, dest)
                if sent is False:
                    _kind, label = detect_media(message)
                    tipo = label.lower() if label else "mensagem"
                    self._log(f"{tipo.capitalize()} não pode ser copiada pelo bot, nada enviado para {name}", LogLevel.WARNING)
                    continue
                self.metrics.forwarded += 1
                self._on_event({"time": _now(), "source": name, "text": "Replicado", "status": "forwarded", "chat_id": dest})
                self._log(f"Enviado para {name}", LogLevel.SUCCESS)
            except Exception as exc:
                self.metrics.failed += 1
                self._on_event({"time": _now(), "source": name, "text": f"Erro: {exc}", "status": "failed", "chat_id": dest})
                self._log(f"Falha em {name}: {exc}", LogLevel.ERROR)
            self._on_metrics()

    async def _send(self, message, dest: int) -> bool:
        async with self._send_lock:
            elapsed = time.monotonic() - self._last_send
            if elapsed < _SEND_INTERVAL:
                await asyncio.sleep(_SEND_INTERVAL - elapsed)
            if self._bot is not None:
                sent = await self._copy_as_bot(message, dest)
            else:
                await self._send_as_user(message, dest)
                sent = True
            self._last_send = time.monotonic()
            return sent

    async def _copy_as_bot(self, message, dest: int) -> bool:
        caption = message.message or getattr(message, "text", "") or ""
        entities = getattr(message, "entities", None) or None
        rows = [[Button.url(label, url)] for label, url in _url_buttons(message)]
        extra = {"buttons": rows} if rows else {}
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
                        **extra,
                    )
                else:
                    buffer.name = "image.jpg"
                    await self._bot.send_file(
                        dest,
                        buffer,
                        caption=caption,
                        formatting_entities=entities,
                        **extra,
                    )
                return True
        if caption:
            await self._bot.send_message(dest, caption, formatting_entities=entities, **extra)
            return True
        return False

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
        if self._stop_event is not None:
            self._stop_event.set()
        vigia, self._vigia = self._vigia, None
        if vigia is not None and vigia is not asyncio.current_task():
            vigia.cancel()
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
        async for dialog in self._user.iter_dialogs(limit=1000):
            result.append(Channel(id=dialog.id, name=dialog.name or "Sem nome", kind=dialog_kind(dialog)))
        return result

    @staticmethod
    def forget_session(kind: str) -> bool:
        removed = False
        for suffix in (".session", ".session-journal"):
            alvo = paths.sessions_dir() / f"{kind}{suffix}"
            try:
                if alvo.exists():
                    alvo.unlink()
                    removed = True
            except OSError:
                log.exception("Não consegui apagar a sessão %s", alvo.name)
        return removed

    async def log_out(self) -> bool:
        client = self._user
        ended = False
        if self._stop_event is not None:
            self._stop_event.set()
        if client is not None and client.is_connected():
            try:
                ended = bool(await client.log_out())
            except Exception:
                log.exception("Falha ao sair da conta do Telegram")
        await self.stop()
        self.forget_session("user")
        self.forget_session("bot")
        self._log("Conta do Telegram desconectada deste app", LogLevel.INFO)
        return ended
