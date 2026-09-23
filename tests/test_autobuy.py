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
    assert message.splitlines()[0] == "[Watcher] Auto-Compra: simulação concluída, nenhum pedido foi feito"
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
        monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: FakeDriver())
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


def test_aviso_de_compra_que_falhou_nao_diz_que_foi_simulacao():
    for status in (BuyStatus.LOGIN_REQUIRED, BuyStatus.PRICE_ABOVE_LIMIT, BuyStatus.BLOCKED, BuyStatus.UNAVAILABLE):
        cabecalho = build_message(BuyResult(status=status)).splitlines()[0]
        assert "compra NÃO feita" in cabecalho, status
        assert "simulação" not in cabecalho and "dry-run" not in cabecalho, status
    cabecalho = build_message(BuyResult(status=BuyStatus.LOGIN_REQUIRED)).splitlines()[0]
    assert "reconecte" in cabecalho


_PS5_AMAZON = "https://www.amazon.com.br/dp/B0CGBQVDNK"


def test_produto_de_outra_loja_nao_rouba_a_promo(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    fake.manager.items = [
        WatchItem(id="k", name="PS5 KaBuM", max_price=3000.0, include=["ps5"], store="kabum"),
        WatchItem(id="a", name="PS5 Amazon", max_price=3200.0, include=["ps5"], store="amazon"),
    ]
    compras: list[tuple[str, float]] = []

    class Driver:
        async def buy(self, url, max_price, **_kwargs):
            compras.append((url, max_price))
            return BuyResult(status=BuyStatus.DRY_RUN_OK, detail="ok")

    monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: Driver())

    async def scenario():
        assert fake.manager.handle_text(f"PS5 Slim por R$ 2.999 {_PS5_AMAZON}")
        await fake.wait_idle()

    asyncio.run(scenario())
    assert compras == [(_PS5_AMAZON, 3200.0)]


def test_encurtador_so_e_aberto_ate_achar_a_loja_do_produto(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    fake.manager.items = [WatchItem(id="a", name="PS5 Amazon", max_price=3200.0, include=["ps5"], store="amazon")]
    destinos = {
        "https://encurta.example/k": "https://www.kabum.com.br/produto/1/ps5",
        "https://encurta.example/a": _PS5_AMAZON,
        "https://t.me/grupo_vip": "https://t.me/grupo_vip",
    }
    abertos: list[str] = []

    def resolve(url):
        abertos.append(url)
        return destinos[url]

    monkeypatch.setattr(registry, "resolve", resolve)

    async def scenario():
        assert fake.manager.handle_text("PS5 Slim " + " ".join(destinos))
        await fake.wait_idle()

    asyncio.run(scenario())
    assert fake.purchases == [_PS5_AMAZON]
    assert abertos == ["https://encurta.example/k", "https://encurta.example/a"]


def test_promo_que_chega_com_o_robo_ocupado_espera_na_fila(tmp_path, monkeypatch):
    import asyncio

    fake = _FakeBrowser(tmp_path, monkeypatch)

    async def scenario():
        fake.manager._busy = True
        assert fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        assert fake.purchases == []
        fake.manager._release()
        await fake.wait_idle()

    asyncio.run(scenario())
    assert fake.purchases == [_PROMO_URL]


def test_promo_velha_na_fila_e_descartada(tmp_path, monkeypatch):
    import asyncio
    import types

    from watcher.autobuy import manager as manager_module

    fake = _FakeBrowser(tmp_path, monkeypatch)
    relogio = [1000.0]
    monkeypatch.setattr(manager_module, "time", types.SimpleNamespace(monotonic=lambda: relogio[0]))

    async def scenario():
        fake.manager._busy = True
        fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        relogio[0] += 121
        fake.manager._release()
        await fake.wait_idle()

    asyncio.run(scenario())
    assert fake.purchases == []


def test_compra_que_falhou_pode_ser_tentada_de_novo_logo(tmp_path, monkeypatch):
    import asyncio
    import types

    from watcher.autobuy import manager as manager_module
    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    relogio = [1000.0]
    monkeypatch.setattr(manager_module, "time", types.SimpleNamespace(monotonic=lambda: relogio[0]))
    status = [BuyStatus.LOGIN_REQUIRED]

    class Driver:
        async def buy(self, url, max_price, **_kwargs):
            fake.purchases.append(url)
            return BuyResult(status=status[0])

    monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: Driver())

    async def scenario():
        fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        await fake.wait_idle()
        relogio[0] += 600
        status[0] = BuyStatus.DRY_RUN_OK
        fake.manager.handle_text(f"VOLTOU SSD barato {_PROMO_URL}")
        await fake.wait_idle()
        relogio[0] += 600
        fake.manager.handle_text(f"SSD barato de novo {_PROMO_URL}")
        await fake.wait_idle()

    asyncio.run(scenario())
    assert fake.purchases == [_PROMO_URL, _PROMO_URL]


def _manager_para_vigia(tmp_path, itens, conhecidas=()):
    from watcher.autobuy.manager import AutobuyManager
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    store.save(AutobuySettings(enabled=True, known_stores=list(conhecidas)), itens)
    alertas: list[str] = []
    return AutobuyManager(store, on_alert=alertas.append), alertas


def test_vigia_nao_alarma_loja_que_nunca_foi_conectada(tmp_path):
    item = WatchItem(id="w", name="SSD", max_price=500.0, include=["ssd"])
    manager, _alertas = _manager_para_vigia(tmp_path, [item], conhecidas=["kabum"])
    assert manager._store_in_use("kabum")
    assert not manager._store_in_use("amazon")
    assert not manager._store_in_use("terabyte")
    preso = WatchItem(id="t", name="Placa", max_price=500.0, include=["placa"], store="terabyte")
    manager.items.append(preso)
    assert manager._store_in_use("terabyte")


def test_vigia_nao_alarma_quando_nao_deu_para_verificar(tmp_path, monkeypatch):
    import asyncio

    item = WatchItem(id="w", name="SSD", max_price=500.0, include=["ssd"], store="kabum")
    manager, alertas = _manager_para_vigia(tmp_path, [item])

    async def verificacao_falhou(store_id="kabum", deep=True):
        manager.check_failed.add(store_id)
        return None

    monkeypatch.setattr(manager, "check_login", verificacao_falhou)
    assert asyncio.run(manager._watchdog_tick()) is True
    assert alertas == []

    async def deslogado(store_id="kabum", deep=True):
        manager.check_failed.discard(store_id)
        return None

    monkeypatch.setattr(manager, "check_login", deslogado)
    assert asyncio.run(manager._watchdog_tick()) is False
    assert len(alertas) == 1 and "KaBuM" in alertas[0]


def test_vigia_da_terabyte_nao_abre_janela(tmp_path, monkeypatch):
    import asyncio

    item = WatchItem(id="t", name="Placa", max_price=500.0, include=["placa"], store="terabyte")
    manager, alertas = _manager_para_vigia(tmp_path, [item])

    async def abriria_janela(*_args, **_kwargs):
        raise AssertionError("não deveria abrir o navegador")

    monkeypatch.setattr(manager, "check_login", abriria_janela)
    monkeypatch.setattr(manager, "quick_check", lambda store_id: manager.accounts.update({store_id: None}) or False)
    asyncio.run(manager._watchdog_tick())
    assert len(alertas) == 1 and "Terabyte" in alertas[0]


def test_palavra_com_mais_no_produto_casa_igual_ao_teste_ao_vivo():
    from watcher.autobuy.criteria import Criteria

    criteria = Criteria(all_words=["galaxy", "s24+"])
    item = WatchItem(
        id="w", name="S24+", max_price=5000.0, include=criteria.to_include(),
        all_words=criteria.all_words, any_words=criteria.any_words,
    )
    texto = "Samsung Galaxy S24 Ultra 512GB por R$ 4.999"
    assert criteria.check(texto).matched is False
    assert item.matches(texto) is False
    assert item.matches("Samsung Galaxy S24+ 256GB por R$ 3.999") is True
    clone = WatchItem.from_dict(item.to_dict())
    assert clone.matches(texto) is False


def test_captcha_com_navegador_oculto_para_na_hora_sem_pedir_para_resolver():
    import asyncio

    import pytest

    pytest.importorskip("playwright.async_api")
    from watcher.autobuy.kabum import KabumDriver
    from watcher.autobuy.models import HIDDEN_CAPTCHA

    avisos: list[str] = []
    logs: list[str] = []
    driver = KabumDriver(object(), on_log=logs.append, on_captcha=lambda: avisos.append("captcha"), headless=True)

    async def tem_captcha(_page):
        return True

    driver._captcha_present = tem_captcha
    assert asyncio.run(driver._handle_captcha(object())) is False
    assert avisos == []
    assert logs == [HIDDEN_CAPTCHA]


def _manager_sem_navegador(tmp_path, itens=(), **settings):
    from watcher.autobuy.manager import AutobuyManager
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    store.save(AutobuySettings(**{"enabled": True, **settings}), list(itens))
    logs: list[tuple[str, object]] = []
    manager = AutobuyManager(store, on_log=lambda mensagem, nivel: logs.append((mensagem, nivel)))
    return manager, store, logs


def test_editar_produto_substitui_sem_duplicar_e_fica_salvo(tmp_path, monkeypatch):
    from watcher import paths

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    manager, store, _logs = _manager_sem_navegador(tmp_path)
    manager.upsert_item(WatchItem(id="w1", name="SSD", max_price=300.0, include=["ssd"]))
    manager.upsert_item(WatchItem(id="w2", name="RTX", max_price=2000.0, include=["rtx"]))
    manager.upsert_item(WatchItem(id="w1", name="SSD 2TB", max_price=350.0, include=["ssd"]))
    _settings, salvos = store.load()
    assert [(i.id, i.name, i.max_price) for i in salvos] == [("w1", "SSD 2TB", 350.0), ("w2", "RTX", 2000.0)]
    manager.toggle_item("w2", False)
    manager.remove_item("w1")
    _settings, salvos = store.load()
    assert [(i.id, i.enabled) for i in salvos] == [("w2", False)]


def test_chaves_do_autobuy_ficam_salvas(tmp_path, monkeypatch):
    from watcher import paths

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    manager, store, _logs = _manager_sem_navegador(tmp_path)
    manager.set_real_mode(True)
    assert store.load()[0].real_mode is True
    manager.set_headless(True)
    assert store.load()[0].headless is True
    manager.set_use_coupon(False)
    assert store.load()[0].use_coupon is False
    manager.set_enabled(False)
    assert store.load()[0].enabled is False


def test_promo_sem_link_e_ignorada_com_aviso(tmp_path, monkeypatch):
    from watcher import paths
    from watcher.models import LogLevel

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    item = WatchItem(id="w1", name="SSD", max_price=300.0, include=["ssd"])
    manager, _store, logs = _manager_sem_navegador(tmp_path, [item])
    assert manager.handle_text("SSD 2TB baratíssimo, corre!") is False
    assert logs == [("Promo de SSD sem link, ignorada", LogLevel.WARNING)]
    assert manager.handle_text("bom dia https://www.kabum.com.br/produto/1") is False
    assert len(logs) == 1
    assert not manager.busy


def test_promo_fora_de_loop_nao_deixa_o_robo_preso_como_ocupado(tmp_path, monkeypatch):
    from watcher import paths

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    item = WatchItem(id="w1", name="SSD", max_price=300.0, include=["ssd"])
    manager, _store, _logs = _manager_sem_navegador(tmp_path, [item])
    assert manager.handle_text("SSD https://www.kabum.com.br/produto/1") is False
    assert manager.busy is False


def test_fila_do_robo_ocupado_tem_limite(tmp_path, monkeypatch):
    from watcher import paths

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    item = WatchItem(id="w1", name="SSD", max_price=300.0, include=["ssd"])
    manager, _store, logs = _manager_sem_navegador(tmp_path, [item])
    manager._busy = True
    respostas = [manager.handle_text(f"SSD {n} https://www.kabum.com.br/produto/{n}") for n in range(1, 4)]
    assert respostas == [True, True, False]
    assert len(manager._pending) == 2
    assert "fila cheia" in logs[-1][0]


def test_conta_da_loja_pela_sessao_guardada_sem_abrir_navegador(tmp_path, monkeypatch):
    import asyncio

    import pytest

    pytest.importorskip("playwright.async_api")
    from watcher import paths
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    manager, store, _logs = _manager_sem_navegador(tmp_path)
    presente = {"kabum": True, "amazon": False, "terabyte": None}
    monkeypatch.setattr(browser, "has_session_cookies", lambda dominio, _nomes: presente[dominio])
    aberturas: list[bool] = []

    def nao_abre(*_args, **_kwargs):
        aberturas.append(True)
        raise AssertionError("não deveria abrir o navegador")

    monkeypatch.setattr("playwright.async_api.async_playwright", nao_abre)
    assert manager.quick_check("kabum") is True
    assert manager.account("kabum") == "conta salva"
    assert manager.quick_check("amazon") is False
    assert manager.account("amazon") is None
    assert manager.quick_check("terabyte") is None
    assert manager.quick_check("pichau") is None
    assert manager.checked == {"kabum", "amazon"}
    assert store.load()[0].known_stores == ["kabum"]
    manager.accounts["kabum"] = "VINICIUS"
    assert manager.quick_check("kabum") is True
    assert asyncio.run(manager.check_login("kabum", deep=False)) == "VINICIUS"
    assert asyncio.run(manager.check_login("pichau")) is None
    presente["kabum"] = False
    assert manager.quick_check("kabum") is False
    assert manager.account("kabum") is None
    assert aberturas == []


def test_operacoes_com_robo_ocupado_ou_link_estranho_nao_abrem_navegador(tmp_path, monkeypatch):
    import asyncio

    import pytest

    pytest.importorskip("playwright.async_api")
    from watcher import paths

    aberturas: list[bool] = []

    def nao_abre(*_args, **_kwargs):
        aberturas.append(True)
        raise AssertionError("não deveria abrir o navegador")

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    monkeypatch.setattr("playwright.async_api.async_playwright", nao_abre)
    manager, _store, logs = _manager_sem_navegador(tmp_path)
    estranho = "https://golpe.example/produto/1"
    assert asyncio.run(manager.probe(estranho)) is None
    assert asyncio.run(manager.run_test(estranho, 100.0, confirm=True)) is None
    assert len(logs) == 2
    assert all("Link não reconhecido" in mensagem and "KaBuM!" in mensagem for mensagem, _ in logs)
    assert asyncio.run(manager.open_login("pichau")) is None
    manager._busy = True
    manager.accounts["kabum"] = "VINICIUS"
    kabum = "https://www.kabum.com.br/produto/1"
    assert asyncio.run(manager.probe(kabum)) is None
    assert asyncio.run(manager.run_test(kabum, 100.0, confirm=True)) is None
    assert asyncio.run(manager.open_login("kabum")) == "VINICIUS"
    assert manager.busy
    assert aberturas == []


def test_pix_gerado_desliga_o_produto_so_quando_pedido(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    uma_vez = WatchItem(id="a", name="SSD", max_price=500.0, include=["ssd"], store="kabum")
    sempre = WatchItem(id="b", name="SSD", max_price=500.0, include=["ssd"], store="kabum", disable_after_buy=False)
    fake.manager.items = [uma_vez, sempre]

    class Driver:
        async def buy(self, url, max_price, **_kwargs):
            return BuyResult(status=BuyStatus.PIX_READY, pix_code="000201", elapsed=3.0)

    monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: Driver())
    for item in (uma_vez, sempre):
        resultado = asyncio.run(fake.manager._attempt("SSD", item, registry.get("kabum"), _PROMO_URL))
        assert resultado.status == BuyStatus.PIX_READY
    assert uma_vez.enabled is False
    assert sempre.enabled is True
    salvos = {i.id: i.enabled for i in fake.manager._store.load()[1]}
    assert salvos == {"a": False, "b": True}


def test_modo_real_manda_confirmar_e_simulacao_nao(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    pedidos: list[dict] = []

    class Driver:
        async def buy(self, url, max_price, **kwargs):
            pedidos.append(kwargs)
            return BuyResult(status=BuyStatus.DRY_RUN_OK)

    monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: Driver())
    item = WatchItem(id="a", name="SSD", max_price=500.0, include=["ssd"], quantity=2, min_price=100.0)
    spec = registry.get("kabum")
    asyncio.run(fake.manager._attempt("SSD cupom: MELI50", item, spec, _PROMO_URL))
    fake.manager.settings.real_mode = True
    fake.manager.settings.use_coupon = False
    asyncio.run(fake.manager._attempt("SSD cupom: MELI50", item, spec, _PROMO_URL))
    assert [(p["confirm"], p["coupon"], p["quantity"], p["min_price"]) for p in pedidos] == [
        (False, "MELI50", 2, 100.0),
        (True, "", 2, 100.0),
    ]


def test_teste_ao_vivo_libera_o_robo_mesmo_quando_a_loja_quebra(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)

    class Driver:
        async def buy(self, url, max_price, **_kwargs):
            raise RuntimeError("layout mudou")

    monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: Driver())
    assert asyncio.run(fake.manager.run_test(_PROMO_URL, 100.0)) is None
    assert fake.manager.busy is False
    assert fake.open_contexts == []


def test_vigia_nao_verifica_nada_com_autobuy_desligado_ou_ocupado(tmp_path):
    import asyncio

    item = WatchItem(id="w", name="SSD", max_price=500.0, include=["ssd"], store="kabum")
    manager, alertas = _manager_para_vigia(tmp_path, [item])

    async def nao_verifica(*_args, **_kwargs):
        raise AssertionError("não deveria verificar")

    manager.check_login = nao_verifica
    manager.settings.enabled = False
    assert asyncio.run(manager._watchdog_tick()) is False
    manager.settings.enabled = True
    manager._busy = True
    assert asyncio.run(manager._watchdog_tick()) is False
    assert alertas == []


def test_vigia_confere_loja_conectada_mesmo_sem_produto_preso_a_ela(tmp_path):
    item = WatchItem(id="w", name="SSD", max_price=500.0, include=["ssd"])
    manager, _alertas = _manager_para_vigia(tmp_path, [item])
    assert not manager._store_in_use("amazon")
    manager.accounts["amazon"] = "VINICIUS"
    assert manager._store_in_use("amazon")
    manager.toggle_item("w", False)
    assert not manager._store_in_use("amazon")


def _robo_com_contexto(tmp_path, monkeypatch, contexto, driver=None):
    from watcher.autobuy import browser, registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    fechados: list[object] = []

    async def abre(_playwright, headless=True):
        if isinstance(contexto, Exception):
            raise contexto
        return contexto

    async def fecha(context):
        fechados.append(context)

    monkeypatch.setattr(browser, "open_context", abre)
    monkeypatch.setattr(browser, "close_context", fecha)
    if driver is not None:
        monkeypatch.setattr(registry.StoreSpec, "driver", lambda self, context, **_kwargs: driver)
    logs: list[tuple[str, object]] = []
    fake.manager._on_log_ui = lambda mensagem, nivel: logs.append((mensagem, nivel))
    return fake, fechados, logs


def _asyncio_do_robo(monkeypatch, **trocas):
    import asyncio
    import types

    from watcher.autobuy import manager as manager_module

    proxy = types.SimpleNamespace(**{nome: getattr(asyncio, nome) for nome in dir(asyncio) if not nome.startswith("__")})
    for nome, valor in trocas.items():
        setattr(proxy, nome, valor)
    monkeypatch.setattr(manager_module, "asyncio", proxy)


def test_verificar_conta_com_o_navegador_do_robo_aberto_avisa_sem_dar_a_conta_como_caida(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    fake = _FakeBrowser(tmp_path, monkeypatch)
    manager = fake.manager
    logs: list[tuple[str, object]] = []
    manager._on_log_ui = lambda mensagem, nivel: logs.append((mensagem, nivel))
    manager.accounts["kabum"] = "VINICIUS"
    fake.open_contexts.append(object())
    assert asyncio.run(manager.check_login("kabum")) is None
    assert manager.busy_profile is True
    assert "kabum" in manager.check_failed
    assert manager.account("kabum") == "VINICIUS"
    assert logs[-1][1] == LogLevel.WARNING
    fake.open_contexts.clear()
    assert asyncio.run(manager.check_login("kabum")) == "VINICIUS"
    assert manager.busy_profile is False
    assert "kabum" not in manager.check_failed
    assert manager.was_checked("kabum")
    assert manager._store.load()[0].known_stores == ["kabum"]


def test_falha_ao_ler_a_conta_fecha_o_navegador_e_registra_o_erro(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    class PaginaForaDoAr:
        async def goto(self, *_args, **_kwargs):
            raise TimeoutError("site fora do ar")

    class Contexto:
        async def new_page(self):
            return PaginaForaDoAr()

    contexto = Contexto()
    fake, fechados, logs = _robo_com_contexto(tmp_path, monkeypatch, contexto)
    fake.manager.accounts["kabum"] = "VINICIUS"
    assert asyncio.run(fake.manager.check_login("kabum")) is None
    assert fechados == [contexto]
    assert "kabum" in fake.manager.check_failed
    assert fake.manager.account("kabum") == "VINICIUS"
    assert fake.manager.busy_profile is False
    assert logs == [("Falha ao verificar conexão com a KaBuM!: site fora do ar", LogLevel.ERROR)]


def test_janela_de_login_guarda_a_sessao_enquanto_aberta_e_libera_o_robo(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import browser, registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    manager = fake.manager
    abre_para_conferir = browser.open_context
    visitas: list[str] = []
    durante: list[tuple[bool, bool]] = []

    class PaginaDeLogin:
        async def goto(self, url, **_kwargs):
            visitas.append(url)

    class JanelaDeLogin:
        pages = [PaginaDeLogin()]

        def __init__(self):
            self.ao_fechar = None

        def on(self, evento, callback):
            assert evento == "close"
            self.ao_fechar = callback

    janela = JanelaDeLogin()

    async def abre(playwright, headless=True):
        if headless is False:
            return janela
        return await abre_para_conferir(playwright, headless=headless)

    async def guarda(_context):
        durante.append((manager.busy, manager.login_open))
        if len(durante) == 2:
            janela.ao_fechar()
        return 3

    esperas: list[int] = []

    async def espera_curta(aguardavel, timeout):
        esperas.append(1)
        if len(esperas) > 5 and janela.ao_fechar is not None:
            janela.ao_fechar()
        tarefa = asyncio.ensure_future(aguardavel)
        await fake.real_sleep(0)
        if tarefa.done():
            return tarefa.result()
        tarefa.cancel()
        raise asyncio.TimeoutError

    monkeypatch.setattr(browser, "open_context", abre)
    monkeypatch.setattr(browser, "save_session_cookies", guarda)
    _asyncio_do_robo(monkeypatch, wait_for=espera_curta)
    logs: list[str] = []
    manager._on_log_ui = lambda mensagem, _nivel: logs.append(mensagem)
    assert asyncio.run(manager.open_login("kabum")) == "VINICIUS"
    assert visitas == [registry.get("kabum").login_url]
    assert durante == [(True, True), (True, True)]
    assert (manager.busy, manager.login_open) == (False, False)
    assert "Sessão guardada (3 cookies de sessão)" in logs


def test_janela_de_login_que_nao_abre_libera_o_robo(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    fake, _fechados, logs = _robo_com_contexto(tmp_path, monkeypatch, RuntimeError("chrome não abriu"))
    assert asyncio.run(fake.manager.open_login("kabum")) is None
    assert (fake.manager.busy, fake.manager.login_open) == (False, False)
    assert logs == [("Falha na janela de login: chrome não abriu", LogLevel.ERROR)]


def test_consulta_de_produto_fecha_a_pagina_e_libera_o_robo_mesmo_com_erro(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    class Pagina:
        def __init__(self):
            self.fechada = False

        async def close(self):
            self.fechada = True

    class Contexto:
        def __init__(self):
            self.paginas: list[Pagina] = []

        async def new_page(self):
            self.paginas.append(Pagina())
            return self.paginas[-1]

    produto = ProductInfo(id=1, title="SSD", url="x", pix_price=299.9, card_price=320.0, available=True)
    lidos: list[str] = []

    class Driver:
        falha = False

        async def fetch_product(self, _page, url):
            lidos.append(url)
            if self.falha:
                raise RuntimeError("layout mudou")
            return produto

    contexto, driver = Contexto(), Driver()
    fake, fechados, logs = _robo_com_contexto(tmp_path, monkeypatch, contexto, driver)
    link = "https://www.amazon.com.br/SSD-Kingston/dp/B0CGBQVDNK/ref=sr_1_1?th=1"
    assert asyncio.run(fake.manager.probe(link)) is produto
    assert lidos == ["https://www.amazon.com.br/dp/B0CGBQVDNK"]
    driver.falha = True
    assert asyncio.run(fake.manager.probe(link)) is None
    assert [p.fechada for p in contexto.paginas] == [True, True]
    assert fechados == [contexto, contexto]
    assert fake.manager.busy is False
    assert logs[-1] == ("Falha ao consultar o produto: layout mudou", LogLevel.ERROR)


def test_resultado_da_compra_chega_no_aviso_e_erro_no_aviso_nao_prende_o_robo(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    fake = _FakeBrowser(tmp_path, monkeypatch)
    logs: list[tuple[str, object]] = []
    fake.manager._on_log_ui = lambda mensagem, nivel: logs.append((mensagem, nivel))
    recebidos: list[BuyStatus] = []

    async def aviso(resultado):
        recebidos.append(resultado.status)

    async def aviso_quebrado(_resultado):
        raise RuntimeError("telegram fora")

    async def cenario():
        fake.manager._on_result = aviso
        assert fake.manager.handle_text(f"SSD barato {_PROMO_URL}")
        await fake.wait_idle()
        fake.manager._on_result = aviso_quebrado
        assert fake.manager.handle_text("SSD barato https://www.kabum.com.br/produto/456/ssd")
        await fake.wait_idle()

    asyncio.run(cenario())
    assert recebidos == [BuyStatus.DRY_RUN_OK]
    assert fake.manager.busy is False
    assert logs[-1] == ("Auto-compra falhou: telegram fora", LogLevel.ERROR)


def test_promo_sem_link_de_loja_ou_com_link_de_outra_loja_explica_no_log(tmp_path, monkeypatch):
    import asyncio

    from watcher.autobuy import registry

    fake = _FakeBrowser(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "resolve", lambda url: url)
    logs: list[str] = []
    fake.manager._on_log_ui = lambda mensagem, _nivel: logs.append(mensagem)
    itens = fake.manager.items
    assert asyncio.run(fake.manager._execute("SSD https://encurta.example/x", itens)) is None
    assert logs[-1] == "Nenhum link de produto nas lojas suportadas (KaBuM!, Amazon, Terabyte)"
    assert asyncio.run(fake.manager._execute(f"SSD {_PS5_AMAZON}", itens)) is None
    assert logs[-1] == "Promo casou com 'SSD', mas o link é da Amazon e o produto só compra na KaBuM!"
    assert fake.purchases == []


def test_vigia_liga_uma_vez_so_e_para_quando_pedido(tmp_path):
    import asyncio

    manager, _alertas = _manager_para_vigia(tmp_path, [])
    manager.start_watchdog()
    assert manager._watchdog is None

    async def cenario():
        manager.start_watchdog()
        primeira = manager._watchdog
        manager.start_watchdog()
        segunda = manager._watchdog
        manager.stop_watchdog()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return primeira, segunda, primeira.cancelled()

    primeira, segunda, cancelada = asyncio.run(cenario())
    assert segunda is primeira
    assert cancelada is True
    assert manager._watchdog is None


def test_vigia_volta_em_15_minutos_se_nao_verificou_e_para_com_aviso_se_quebrar(tmp_path, monkeypatch):
    import asyncio

    from watcher.models import LogLevel

    manager, _alertas = _manager_para_vigia(tmp_path, [])
    esperas: list[float] = []
    voltas: list[int] = []
    logs: list[tuple[str, object]] = []
    manager._on_log_ui = lambda mensagem, nivel: logs.append((mensagem, nivel))

    async def dorme(segundos):
        esperas.append(segundos)

    async def verifica():
        voltas.append(1)
        if len(voltas) == 1:
            return True
        if len(voltas) == 2:
            return False
        raise RuntimeError("quebrou")

    _asyncio_do_robo(monkeypatch, sleep=dorme)
    manager._watchdog_tick = verifica
    asyncio.run(manager._watchdog_loop())
    assert esperas == [60.0, 900.0, 6 * 3600.0]
    assert logs[-1] == ("Vigia de sessão parou: quebrou", LogLevel.WARNING)


def test_vigia_com_conta_ativa_nao_alarma(tmp_path):
    import asyncio

    item = WatchItem(id="w", name="SSD", max_price=500.0, include=["ssd"], store="kabum")
    manager, alertas = _manager_para_vigia(tmp_path, [item])
    logs: list[str] = []
    manager._on_log_ui = lambda mensagem, _nivel: logs.append(mensagem)

    async def logado(store_id="kabum", deep=True):
        return "VINICIUS"

    manager.check_login = logado
    assert asyncio.run(manager._watchdog_tick()) is False
    assert alertas == []
    assert logs == ["Sessão da KaBuM! ativa (VINICIUS)"]


def test_alerta_da_mesma_loja_volta_depois_de_15_minutos(tmp_path, monkeypatch):
    import types

    from watcher.autobuy import manager as manager_module
    from watcher.autobuy.manager import AutobuyManager

    relogio = [1000.0]
    monkeypatch.setattr(manager_module, "time", types.SimpleNamespace(monotonic=lambda: relogio[0]))
    manager, alertas = _manager_para_vigia(tmp_path, [])
    manager._alert("kabum", "caiu 1")
    relogio[0] += 899
    manager._alert("kabum", "caiu 2")
    manager._alert("amazon", "caiu a")
    relogio[0] += 2
    manager._alert("kabum", "caiu 3")
    assert alertas == ["caiu 1", "caiu a", "caiu 3"]
    AutobuyManager(manager._store)._alert("kabum", "sem ninguém ouvindo")
