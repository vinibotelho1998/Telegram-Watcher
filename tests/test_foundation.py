import json
from pathlib import Path

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


def test_tema_escolhido_e_lembrado_e_env_antigo_abre_escuro(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    env.write_text("API_ID=1\nAPI_HASH=x\nPHONE_NUMBER=+55\n", encoding="utf-8")
    antigo = AppConfig.load()
    assert antigo.dark_theme is True
    assert antigo.tray_hint_shown is False
    antigo.dark_theme = False
    antigo.tray_hint_shown = True
    assert antigo.save()
    lido = AppConfig.load()
    assert lido.dark_theme is False
    assert lido.tray_hint_shown is True


def test_parse_helpers():
    assert _parse_ids("-1, -2 ,x, 3") == [-1, -2, 3]
    assert _parse_aliases("bad,-1:Canal A,-2:Outro") == {-1: "Canal A", -2: "Outro"}


def test_config_roundtrip_keeps_aliases_with_special_characters(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    aliases = {
        -1: "Ofertas, Cupons e Promos",
        -2: "Canal #1 do Bug",
        -3: "Promo: 50% OFF",
        -4: "Aspas 'simples' e \"duplas\"",
        -5: "Barra \\ invertida",
        -6: "Cupom ${HOME} e $PATH",
        -7: "Live, 20:00 com cupom",
    }
    cfg = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=list(aliases), aliases=dict(aliases))
    assert cfg.save()
    assert AppConfig.load().aliases == aliases


def test_config_reads_legacy_alias_with_comma(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SOURCE_ALIASES=-1:Ofertas, Cupons,-2:Outro\n", encoding="utf-8")
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    assert AppConfig.load().aliases == {-1: "Ofertas, Cupons", -2: "Outro"}


def test_filter_store_roundtrip(tmp_path):
    store = FilterStore(tmp_path / "filters.json")
    assert store.load() == []
    f = Filter.new("Teste")
    assert store.save([f])
    out = store.load()
    assert len(out) == 1 and out[0].name == "Teste"


def test_filter_store_keeps_previous_file_when_write_fails(tmp_path, monkeypatch):
    store = FilterStore(tmp_path / "filters.json")
    assert store.save([Filter.new("Original")])
    original_write = Path.write_text

    def disk_full(self, data, *args, **kwargs):
        original_write(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    assert store.save([Filter.new("Novo")]) is False
    monkeypatch.undo()
    assert [f.name for f in store.load()] == ["Original"]
    assert [p.name for p in tmp_path.iterdir()] == ["filters.json"]


def test_filter_store_retries_when_file_is_briefly_locked(tmp_path, monkeypatch):
    import os

    from watcher import storage

    store = FilterStore(tmp_path / "filters.json")
    real_replace = os.replace
    failures = [PermissionError(32, "in use"), PermissionError(32, "in use")]

    def flaky_replace(src, dst):
        if failures:
            raise failures.pop()
        real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", flaky_replace)
    monkeypatch.setattr(storage.time, "sleep", lambda _seconds: None)
    assert store.save([Filter.new("Travado")])
    monkeypatch.undo()
    assert [f.name for f in store.load()] == ["Travado"]


def test_config_keeps_previous_env_when_write_fails(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    assert AppConfig(api_id=123, api_hash="abc", phone="+5511").save()
    original_write = Path.write_text

    def disk_full(self, data, *args, **kwargs):
        original_write(self, data[: len(data) // 3], *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    assert AppConfig(api_id=999, api_hash="zzz", phone="+1").save() is False
    monkeypatch.undo()
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    loaded = AppConfig.load()
    assert (loaded.api_id, loaded.api_hash, loaded.phone) == (123, "abc", "+5511")


def test_filter_store_skips_broken_items(tmp_path):
    path = tmp_path / "filters.json"
    path.write_text(
        json.dumps([{"id": "a", "name": "Bom"}, "lixo", {"id": "b", "source_ids": ["abc"]}]),
        encoding="utf-8",
    )
    assert [f.name for f in FilterStore(path).load()] == ["Bom"]


def test_autobuy_store_skips_broken_items(tmp_path):
    from watcher.autobuy.storage import AutobuyStore

    path = tmp_path / "autobuy.json"
    path.write_text(
        json.dumps({"items": [{"id": "w1", "name": "Ok", "max_price": 10}, {"id": "w2", "max_price": "caro"}]}),
        encoding="utf-8",
    )
    _settings, items = AutobuyStore(path).load()
    assert [i.id for i in items] == ["w1"]


def test_autobuy_store_with_broken_settings_falls_back_to_safe_defaults(tmp_path):
    from watcher.autobuy.storage import AutobuyStore

    path = tmp_path / "autobuy.json"
    for broken in ({"enabled": True, "real_mode": True, "check_hours": "seis"}, ["lixo"]):
        path.write_text(
            json.dumps({"settings": broken, "items": [{"id": "w1", "name": "Ok", "max_price": 10}]}),
            encoding="utf-8",
        )
        settings, items = AutobuyStore(path).load()
        assert (settings.enabled, settings.real_mode) == (False, False)
        assert [i.id for i in items] == ["w1"]


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
