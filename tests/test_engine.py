import asyncio
import types

from monitor.config import AppConfig
from monitor.engine import MonitorEngine
from monitor.models import EngineState, Filter


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


def test_skips_unknown_source():
    engine = make_engine([])
    run(engine._handle(FakeEvent(-2, 5, FakeMessage(1, "cupom"))))
    assert engine.metrics.received == 0
    assert engine.sent == []


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
