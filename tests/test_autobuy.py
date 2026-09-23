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


def test_records_ignore_malformed_files(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(records.paths, "autobuy_dir", lambda: tmp_path)
    (tmp_path / "20260101-000000-pix.json").write_text("[]", encoding="utf-8")
    (tmp_path / "20260101-000001-pix.json").write_text('{"timings": [1, 2]}', encoding="utf-8")
    records.save_record(BuyResult(status=BuyStatus.PIX_READY, pix_code="00020126x"), "20260708-100000")
    assert [r.stamp for r in records.load_records()] == ["20260708-100000"]


class _FakeBrowser:
    def __init__(self, tmp_path, monkeypatch, *, clock: float = 10_000.0) -> None:
        import asyncio
        import types

        import pytest

        async_api = pytest.importorskip("playwright.async_api")
        from watcher import paths
        from watcher.autobuy import browser, registry
        from watcher.autobuy import manager as manager_module
        from watcher.autobuy.manager import AutobuyManager
        from watcher.autobuy.storage import AutobuySettings, AutobuyStore

        self.open_contexts: list[object] = []
        self.purchases: list[str] = []
        self.alerts: list[str] = []
        self.release = asyncio.Event()
        self.release.set()
        self.real_sleep = asyncio.sleep
        fake = self

        class FakePlaywright:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        class FakePage:
            async def goto(self, *_args, **_kwargs):
                await fake.release.wait()

            async def evaluate(self, _script):
                return "Olá, VINICIUS"

        class FakeContext:
            async def new_page(self):
                return FakePage()

        async def fake_open_context(_playwright, headless=True):
            if fake.open_contexts:
                raise browser.ProfileInUse(browser.PROFILE_BUSY)
            context = FakeContext()
            fake.open_contexts.append(context)
            return context

        async def fake_close_context(context):
            fake.open_contexts.remove(context)

        class FakeDriver:
            async def buy(self, url, max_price, **_kwargs):
                fake.purchases.append(url)
                return BuyResult(status=BuyStatus.DRY_RUN_OK, detail="ok")

        async def fast_sleep(_delay=0, result=None):
            await fake.real_sleep(0)
            return result

        monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
        monkeypatch.setattr(async_api, "async_playwright", lambda: FakePlaywright())
        monkeypatch.setattr(browser, "open_context", fake_open_context)
        monkeypatch.setattr(browser, "close_context", fake_close_context)
        monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, on_log=None, on_captcha=None: FakeDriver())
        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        monkeypatch.setattr(manager_module, "time", types.SimpleNamespace(monotonic=lambda: clock))

        store = AutobuyStore(tmp_path / "autobuy.json")
        item = WatchItem(id="w1", name="SSD", max_price=500.0, include=["ssd"], store="kabum")
        store.save(AutobuySettings(enabled=True), [item])
        self.manager = AutobuyManager(store, on_alert=self.alerts.append)

    async def wait_idle(self) -> None:
        for _ in range(200):
            if not self.manager.busy:
                return
            await self.real_sleep(0.01)


_PROMO_URL = "https://www.kabum.com.br/produto/123/ssd"


def test_manager_waits_for_session_check_before_buying(tmp_path, monkeypatch):
    import asyncio

    fake = _FakeBrowser(tmp_path, monkeypatch)
    fake.release.clear()

    async def scenario():
        check = asyncio.create_task(fake.manager.check_login("kabum"))
        await fake.real_sleep(0.05)
        assert fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        await fake.real_sleep(0.05)
        fake.release.set()
        user = await check
        await fake.wait_idle()
        return user

    assert asyncio.run(scenario()) == "VINICIUS"
    assert fake.purchases == [_PROMO_URL]
    assert fake.open_contexts == []


def test_manager_buys_first_promo_right_after_boot(tmp_path, monkeypatch):
    import asyncio

    fake = _FakeBrowser(tmp_path, monkeypatch, clock=120.0)

    async def scenario():
        assert fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        await fake.wait_idle()
        assert fake.manager.handle_text(f"SSD barato de novo {_PROMO_URL}")
        await fake.wait_idle()

    asyncio.run(scenario())
    assert fake.purchases == [_PROMO_URL]


def test_manager_first_alert_is_not_muted_right_after_boot(tmp_path, monkeypatch):
    fake = _FakeBrowser(tmp_path, monkeypatch, clock=120.0)
    fake.manager._alert("kabum", "Sua conta caiu")
    fake.manager._alert("kabum", "Sua conta caiu de novo")
    assert fake.alerts == ["Sua conta caiu"]


_LINKS_DE_OUTRO_SITE = [
    "https://golpe.example/kabum.com.br/produto/123",
    "https://golpe.example/?r=https://www.kabum.com.br/produto/123",
    "https://www.kabum.com.br@golpe.example/produto/123",
    "https://golpe.example\\@www.kabum.com.br/produto/123",
    "https://www.kabum.com.br.golpe.example/produto/123",
    "https://golpe.example/terabyteshop.com.br/produto/9",
    "https://golpe.example/amazon.com.br/dp/B0CGBQVDNK",
]


def test_link_de_outro_site_nao_passa_por_loja(monkeypatch):
    from watcher.autobuy import registry

    monkeypatch.setattr(registry, "resolve", lambda url: url)
    for link in _LINKS_DE_OUTRO_SITE:
        assert registry.match_url(link) is None, link
        assert registry.find_product(f"SSD 2TB barato {link}") is None, link
    assert product_id("https://golpe.example/kabum.com.br/produto/123") is None


def test_links_das_lojas_continuam_reconhecidos():
    from watcher.autobuy import registry

    casos = {
        "https://www.kabum.com.br/produto/1000741/placa": "kabum",
        "https://kabum.com.br/produto/1": "kabum",
        "HTTPS://WWW.KABUM.COM.BR/produto/7": "kabum",
        "https://www.terabyteshop.com.br/produto/25639/ssd": "terabyte",
        "https://www.amazon.com.br/SSD-Kingston/dp/B0CGBQVDNK/ref=sr_1_1?th=1": "amazon",
        "https://www.amazon.com.br/gp/product/B0C9DWTTQF": "amazon",
    }
    for link, loja in casos.items():
        assert registry.match_url(link).id == loja, link
