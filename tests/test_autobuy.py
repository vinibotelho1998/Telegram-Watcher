from __future__ import annotations

from watcher.autobuy.models import BuyResult, BuyStatus, ProductInfo, WatchItem
from watcher.autobuy.notify import build_message
from watcher.autobuy.resolver import extract_urls, find_kabum_product, product_id


def test_extract_urls():
    text = "corre https://www.kabum.com.br/produto/1000741/placa e https://bit.ly/abc123"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert urls[0].startswith("https://www.kabum.com.br")


def test_product_id():
    assert product_id("https://www.kabum.com.br/produto/1000741/placa-de-video") == 1000741
    assert product_id("https://exemplo.com/produto/123") is None
    assert product_id("") is None


def test_find_kabum_product_direct():
    text = "oferta https://www.kabum.com.br/produto/999/x por R$ 10"
    assert find_kabum_product(text) == "https://www.kabum.com.br/produto/999/x"


def test_find_kabum_product_absent():
    assert find_kabum_product("sem link nenhum aqui") is None


def test_watch_item_matches():
    item = WatchItem(id="w1", name="RTX 5060", max_price=2300.0, include=["rtx 5060"], exclude=["5060 ti"])
    assert item.matches("Placa de vídeo RTX 5060 por R$ 2.199")
    assert not item.matches("RTX 5060 Ti 16GB em promoção")
    assert not item.matches("RX 7600 baratinha")


def test_watch_item_requires_include():
    item = WatchItem(id="w2", name="Qualquer", max_price=100.0)
    assert not item.matches("qualquer texto")


def test_watch_item_disabled():
    item = WatchItem(id="w3", name="RTX", max_price=100.0, include=["rtx"], enabled=False)
    assert not item.matches("rtx 4090")


def test_watch_item_roundtrip():
    item = WatchItem(
        id="w4",
        name="SSD",
        max_price=350.5,
        include=["ssd + 1tb"],
        exclude=["sata"],
        min_price=80.0,
        quantity=2,
        disable_after_buy=False,
    )
    clone = WatchItem.from_dict(item.to_dict())
    assert clone == item


def test_watch_item_defaults_from_legacy_dict():
    item = WatchItem.from_dict({"id": "w5", "name": "Antigo", "max_price": 100.0, "include": ["rtx"]})
    assert item.min_price == 0.0
    assert item.quantity == 1
    assert item.disable_after_buy is True
    assert item.store == ""


def test_build_message_pix():
    product = ProductInfo(id=1, title="Produto X", url="https://kabum.com.br/produto/1", pix_price=99.9, card_price=110.0, available=True)
    result = BuyResult(status=BuyStatus.PIX_READY, product=product, pix_code="00020126pix")
    message = build_message(result)
    assert "PIX gerado" in message
    assert "Produto X" in message
    assert "R$ 99.90" in message
    assert "00020126pix" in message


def test_build_message_dry_run():
    result = BuyResult(status=BuyStatus.DRY_RUN_OK, detail="parado antes de finalizar")
    message = build_message(result)
    assert "dry-run" in message
    assert "parado antes de finalizar" in message


def test_records_roundtrip(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(records.paths, "autobuy_dir", lambda: tmp_path)
    product = ProductInfo(id=1, title="Produto X", url="https://kabum.com.br/produto/1", pix_price=10.5, card_price=12.0, available=True)
    result = BuyResult(status=BuyStatus.PIX_READY, product=product, pix_code="00020126teste", screenshot="")
    saved = records.save_record(result, "20260708-120000")
    loaded = records.load_records()
    assert loaded == [saved]
    assert loaded[0].pix_code == "00020126teste"
    assert loaded[0].price == 10.5


def test_records_with_timings(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(records.paths, "autobuy_dir", lambda: tmp_path)
    result = BuyResult(status=BuyStatus.PIX_READY, pix_code="00020126x", elapsed=23.5, timings={"produto": 2.0, "pix": 4.5})
    records.save_record(result, "20260708-130000")
    loaded = records.load_records()[0]
    assert loaded.elapsed == 23.5
    assert loaded.timings == {"produto": 2.0, "pix": 4.5}


def test_build_message_includes_timing():
    result = BuyResult(status=BuyStatus.PIX_READY, pix_code="00020126pix", elapsed=23.5, timings={"produto": 2.0})
    message = build_message(result)
    assert "Tempo: 23,5s" in message
    assert "produto 2,0s" in message


def test_records_sorted_desc(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(records.paths, "autobuy_dir", lambda: tmp_path)
    result = BuyResult(status=BuyStatus.PIX_READY, pix_code="00020126x")
    records.save_record(result, "20260708-100000")
    records.save_record(result, "20260708-110000")
    loaded = records.load_records()
    assert [r.stamp for r in loaded] == ["20260708-110000", "20260708-100000"]


def test_autobuy_store_roundtrip(tmp_path):
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    settings = AutobuySettings(enabled=True, real_mode=False, use_coupon=True)
    items = [WatchItem(id="w1", name="RTX 5060", max_price=2300.0, include=["rtx 5060"])]
    assert store.save(settings, items)
    loaded_settings, loaded_items = store.load()
    assert loaded_settings == settings
    assert loaded_items == items


def test_manager_find_item_respects_master_switch(tmp_path):
    from watcher.autobuy.manager import AutobuyManager
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    item = WatchItem(id="w1", name="RTX 5060", max_price=2300.0, include=["rtx 5060"])
    store.save(AutobuySettings(enabled=False), [item])
    manager = AutobuyManager(store)
    assert manager.find_item("promo rtx 5060 barata") is None
    manager.set_enabled(True)
    assert manager.find_item("promo rtx 5060 barata") == item
    assert manager.find_item("promo rx 7600") is None


def test_manager_pick_coupon(tmp_path):
    from watcher.autobuy.manager import AutobuyManager
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    store.save(AutobuySettings(enabled=True, use_coupon=True), [])
    manager = AutobuyManager(store)
    assert manager.pick_coupon("RTX 5060 com cupom: MELI50 só hoje") == "MELI50"
    manager.set_use_coupon(False)
    assert manager.pick_coupon("RTX 5060 com cupom: MELI50 só hoje") == ""


def test_watch_item_defaults_to_any_store():
    item = WatchItem.new("SSD barato")
    assert item.store == ""


def test_watch_item_legacy_dict_without_store_accepts_all():
    item = WatchItem.from_dict({"id": "w9", "name": "X", "max_price": 10.0, "include": ["ssd"]})
    assert item.store == ""
