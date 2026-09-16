from watcher.config import AppConfig, _parse_aliases, _parse_ids
from watcher.models import Channel, ChannelKind, Filter, Metrics
from watcher.storage import ChannelCache, FilterStore


def test_filter_legacy_from_dict():
    legacy = {
        "id": "1",
        "name": "Promo",
        "include_words": ["cupom", "meli"],
        "exclude_words": ["esgotado"],
        "destination_id": -100123,
        "source_ids": [-100999],
        "enabled": True,
    }
    f = Filter.from_dict(legacy)
    assert f.include == ["cupom", "meli"]
    assert f.exclude == ["esgotado"]
    assert f.destination_ids == [-100123]
    assert f.source_ids == [-100999]
    assert f.applies_to(-100999) and not f.applies_to(-1)


def test_filter_roundtrip():
    f = Filter.new("X")
    f.smart_rules = ["any_coupon"]
    f.destination_ids = [-100]
    assert Filter.from_dict(f.to_dict()) == f


def test_config_roundtrip(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    cfg = AppConfig(
        api_id=123, api_hash="abc", phone="+5511",
        source_ids=[-1, -2], destination_ids=[-9],
        aliases={-1: "Canal A"}, auto_start=True,
    )
    assert cfg.save()
    loaded = AppConfig.load()
    assert loaded.api_id == 123
    assert loaded.source_ids == [-1, -2]
    assert loaded.destination_ids == [-9]
    assert loaded.aliases == {-1: "Canal A"}
    assert loaded.auto_start is True
    assert loaded.is_ready
    assert loaded.channel_name(-1) == "Canal A"
    assert loaded.channel_name(-5) == "ID -5"


def test_parse_helpers():
    assert _parse_ids("-1, -2 ,x, 3") == [-1, -2, 3]
    assert _parse_aliases("-1:Canal A,-2:Outro,bad") == {-1: "Canal A", -2: "Outro"}


def test_filter_store_roundtrip(tmp_path):
    store = FilterStore(tmp_path / "filters.json")
    assert store.load() == []
    f = Filter.new("Teste")
    assert store.save([f])
    out = store.load()
    assert len(out) == 1 and out[0].name == "Teste"


def test_channel_cache_roundtrip(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")
    cache.save([Channel(-100, "ML", ChannelKind.CHANNEL), Channel(50, "User", ChannelKind.USER)])
    out = cache.load()
    assert out[0].id == -100 and out[0].kind == ChannelKind.CHANNEL
    assert out[1].kind == ChannelKind.USER


def test_metrics():
    m = Metrics()
    assert m.success_rate == 100.0 and m.uptime == 0.0
    m.forwarded, m.failed = 3, 1
    assert m.success_rate == 75.0
    m.mark_capture()
    assert m.received == 1 and sum(m.by_hour) == 1
