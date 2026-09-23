import asyncio
import types

from watcher.config import AppConfig
from watcher.engine import MonitorEngine
from watcher.models import ChannelKind, EngineState, Filter


class FakeMessage:
    def __init__(self, mid, text, media=None):
        self.id = mid
        self.message = text
        self.text = text
        self.media = media


class FakeEvent:
    def __init__(self, chat_id, sender_id, message):
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.message = message


def make_engine(filters, **overrides):
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1], destination_ids=[-999])
    for key, value in overrides.items():
        setattr(config, key, value)
    engine = MonitorEngine(config, lambda: filters)
    engine.sent = []

    async def fake_send(message, dest):
        engine.sent.append((message.id, dest))

    engine._send = fake_send
    return engine


def run(coro):
    asyncio.run(coro)


def run_value(coro):
    return asyncio.run(coro)


def test_forwards_matching_message():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "tem cupom aqui"))))
    assert engine.sent == [(1, -100)]
    assert engine.metrics.forwarded == 1
    assert engine.metrics.received == 1


def test_dedup_same_message():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    event = FakeEvent(-1, 5, FakeMessage(1, "cupom"))
    run(engine._handle(event))
    run(engine._handle(event))
    assert len(engine.sent) == 1


def test_dedup_keeps_most_recent_messages_after_trim():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])

    async def scenario():
        for mid in range(1, 2002):
            await engine._handle(FakeEvent(-1, 5, FakeMessage(mid, "cupom")))
        engine.sent.clear()
        for mid in range(1502, 2002):
            await engine._handle(FakeEvent(-1, 5, FakeMessage(mid, "cupom")))

    run(scenario())
    assert engine.sent == []


def test_sync_channels_marks_supergroup_as_group():
    class DialogClient:
        def is_connected(self):
            return True

        async def iter_dialogs(self, limit=None):
            yield types.SimpleNamespace(id=-100, name="Grupo", is_channel=True, is_group=True)
            yield types.SimpleNamespace(id=-200, name="Canal", is_channel=True, is_group=False)
            yield types.SimpleNamespace(id=7, name="Ana", is_channel=False, is_group=False)

    engine = make_engine([])
    engine._user = DialogClient()
    channels = run_value(engine.sync_channels())
    assert [(c.id, c.kind) for c in channels] == [
        (-100, ChannelKind.GROUP),
        (-200, ChannelKind.CHANNEL),
        (7, ChannelKind.USER),
    ]


def test_skips_unknown_source():
    engine = make_engine([])
    run(engine._handle(FakeEvent(-2, 5, FakeMessage(1, "cupom"))))
    assert engine.metrics.received == 0
    assert engine.sent == []


def test_test_mode_never_replicates_into_the_same_chat():
    engine = make_engine([], source_ids=[777], destination_ids=[777, -999], allow_self=True)
    engine._me = types.SimpleNamespace(id=777)
    next_id = [100]

    async def fake_send(message, dest):
        engine.sent.append((message.id, dest))
        if len(engine.sent) < 10:
            next_id[0] += 1
            await engine._handle(FakeEvent(dest, 777, FakeMessage(next_id[0], message.message)))

    engine._send = fake_send
    run(engine._handle(FakeEvent(777, 777, FakeMessage(1, "cupom teste"))))
    assert engine.sent == [(1, -999)]


def test_retry_after_flood_wait_is_reported_like_any_send(monkeypatch):
    from telethon.errors import FloodWaitError

    from watcher.models import LogLevel

    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100, -200]})
    events, logs = [], []
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1])
    engine = MonitorEngine(config, lambda: [flt], on_event=events.append, on_log=lambda m, lvl: logs.append((m, lvl)))
    attempts = {-100: [FloodWaitError(request=None, capture=3)], -200: [FloodWaitError(request=None, capture=3), RuntimeError("caiu")]}

    async def flaky_send(message, dest):
        if attempts[dest]:
            raise attempts[dest].pop(0)

    async def no_wait(_seconds):
        return None

    engine._send = flaky_send
    monkeypatch.setattr("watcher.engine.asyncio.sleep", no_wait)
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "cupom"))))
    assert [(e["chat_id"], e["status"]) for e in events if e["status"] != "captured"] == [
        (-100, "forwarded"),
        (-200, "failed"),
    ]
    assert (engine.metrics.forwarded, engine.metrics.failed) == (1, 1)
    assert any(m.startswith("Enviado para") and lvl == LogLevel.SUCCESS for m, lvl in logs)


def test_skips_self_unless_allowed():
    engine = make_engine([])
    engine._me = types.SimpleNamespace(id=77)
    run(engine._handle(FakeEvent(-1, 77, FakeMessage(1, "oi"))))
    assert engine.metrics.received == 0

    allowed = make_engine([], allow_self=True)
    allowed._me = types.SimpleNamespace(id=77)
    run(allowed._handle(FakeEvent(-1, 77, FakeMessage(2, "oi"))))
    assert allowed.metrics.received == 1


def test_filtered_when_no_match():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "mensagem qualquer"))))
    assert engine.metrics.received == 1
    assert engine.metrics.filtered == 1
    assert engine.sent == []


def test_smart_rule_capture_end_to_end():
    flt = Filter.from_dict({"id": "a", "smart_rules": ["any_coupon"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "Cupom MELI50 no Mercado Livre"))))
    assert engine.sent == [(1, -100)]


class FakeClient:
    def __init__(self):
        self.forwarded = []
        self.messages = []
        self.files = []
        self.downloads = 0

    async def forward_messages(self, dest, message):
        self.forwarded.append((dest, message.id))

    async def send_message(self, dest, text, formatting_entities=None):
        self.messages.append((dest, text))

    async def send_file(self, dest, data, caption=None, formatting_entities=None, attributes=None, force_document=False):
        payload = data.read() if hasattr(data, "read") else data
        self.files.append((dest, payload, caption))

    async def download_media(self, message, file=None):
        self.downloads += 1
        return b"bytes"


def test_bot_copies_text_instead_of_user_forward():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = FakeClient()
    run(engine._send(FakeMessage(1, "promo"), -100))
    assert engine._bot.messages == [(-100, "promo")]
    assert engine._user.forwarded == []
    assert engine._bot.forwarded == []


def test_bot_copies_media_via_download_reupload():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = FakeClient()
    run(engine._send(FakeMessage(2, "veja", media=object()), -100))
    assert engine._user.downloads == 1
    assert engine._bot.files == [(-100, b"bytes", "veja")]
    assert engine._bot.messages == []


def test_user_forwards_when_no_bot():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = None
    run(engine._send(FakeMessage(3, "oi"), -100))
    assert engine._user.forwarded == [(-100, 3)]


def test_stop_when_offline_is_noop():
    engine = make_engine([])
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert not engine.is_running


def test_start_without_credentials_does_not_hang():
    engine = MonitorEngine(AppConfig(), lambda: [])
    assert run_value(engine.start()) is False
    assert engine.state == EngineState.ERROR
    assert not engine.is_running


def test_restart_is_reentrant():
    engine = MonitorEngine(AppConfig(), lambda: [])
    assert run_value(engine.start()) is False
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert run_value(engine.start()) is False
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert not engine.is_running


def test_start_while_stopping_does_not_open_second_client(tmp_path, monkeypatch):
    created = []

    class FakeTelegramClient:
        def __init__(self, session, *_args, **_kwargs):
            created.append(session)

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            return None

    monkeypatch.setattr("watcher.engine.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    engine = make_engine([])
    release = asyncio.Event()

    class SlowClient:
        async def disconnect(self):
            await release.wait()

    engine._user = SlowClient()
    engine.state = EngineState.ONLINE

    async def scenario():
        stopping = asyncio.create_task(engine.stop())
        await asyncio.sleep(0)
        assert engine.state == EngineState.STOPPING
        started = await engine.start()
        release.set()
        await stopping
        return started

    assert run_value(scenario()) is False
    assert created == []
    assert engine.state == EngineState.OFFLINE


def test_login_code_typed_with_spaces_is_sent_without_them(tmp_path, monkeypatch):
    signed = []

    class FakeTelegramClient:
        def __init__(self, *_args, **_kwargs):
            self.authorized = False

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def send_code_request(self, phone):
            return None

        async def sign_in(self, phone=None, code=None, password=None):
            signed.append(code)
            self.authorized = True

        async def get_me(self):
            return types.SimpleNamespace(id=1, first_name="Ana")

        def add_event_handler(self, handler, event):
            return handler

        def remove_event_handler(self, handler):
            return None

        async def disconnect(self):
            return None

    async def code_provider(_phone):
        return " 1 2 3 4 5 "

    monkeypatch.setattr("watcher.engine.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1])
    engine = MonitorEngine(config, lambda: [], code_provider=code_provider)

    async def scenario():
        started = await engine.start()
        await engine.stop()
        return started

    assert run_value(scenario()) is True
    assert signed == ["12345"]
