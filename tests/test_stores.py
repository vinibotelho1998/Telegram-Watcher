from __future__ import annotations

import pytest

amazon = pytest.importorskip("watcher.autobuy.amazon", reason="requer playwright instalado")

from watcher.autobuy import registry  # noqa: E402
from watcher.autobuy.pix import clean_pix  # noqa: E402


def test_amazon_product_id_from_dp():
    assert amazon.product_id("https://www.amazon.com.br/dp/B0BGJR1R3B") == "B0BGJR1R3B"


def test_amazon_product_id_with_slug_and_ref():
    url = "https://www.amazon.com.br/Cabo-USB-C/dp/B0CGBQVDNK/ref=sr_1_1?keywords=cabo"
    assert amazon.product_id(url) == "B0CGBQVDNK"


def test_amazon_product_id_gp_product():
    assert amazon.product_id("https://www.amazon.com.br/gp/product/B0C9DWTTQF") == "B0C9DWTTQF"


def test_amazon_product_id_rejects_other_stores():
    assert amazon.product_id("https://www.kabum.com.br/produto/475647/placa") is None
    assert amazon.product_id("https://www.magazineluiza.com.br/x/p/123") is None


def test_amazon_canonical_url():
    url = "https://www.amazon.com.br/Cabo/dp/B0CGBQVDNK/ref=sr_1_1?th=1"
    assert amazon.canonical_url(url) == "https://www.amazon.com.br/dp/B0CGBQVDNK"


def test_amazon_pix_price_applies_discount():
    driver = amazon.AmazonDriver.__new__(amazon.AmazonDriver)
    driver._log = lambda _m: None
    promo = "5% off à vista no Pix ou NuPay ou em até 12x de R$ 230,86/mês"
    assert driver._pix_price(2631.49, promo) == pytest.approx(2499.92, abs=0.01)


def test_amazon_pix_price_without_promo_keeps_price():
    driver = amazon.AmazonDriver.__new__(amazon.AmazonDriver)
    driver._log = lambda _m: None
    assert driver._pix_price(8.00, "") == 8.00


def test_amazon_pix_price_uses_fallback_text():
    driver = amazon.AmazonDriver.__new__(amazon.AmazonDriver)
    driver._log = lambda _m: None
    corpo = "Comprar agora\n10% off à vista no Pix\nEntrega grátis"
    assert driver._pix_price(100.0, "", corpo) == pytest.approx(90.0, abs=0.01)


def test_amazon_pix_price_ignores_absurd_discount():
    driver = amazon.AmazonDriver.__new__(amazon.AmazonDriver)
    driver._log = lambda _m: None
    assert driver._pix_price(100.0, "99% off à vista no Pix") == 100.0


def _driver():
    driver = amazon.AmazonDriver.__new__(amazon.AmazonDriver)
    driver._log = lambda _m: None
    driver._pix_selected = False
    return driver


@pytest.mark.parametrize(
    "label",
    [
        "Confirmar pedido com cartão 4745",
        "Confirmar pedido com Mastercard 4745",
        "Confirmar pedido com NuPay",
        "Confirmar pedido com Boleto",
    ],
)
def test_amazon_refuses_to_finish_outside_pix(label):
    import asyncio

    assert asyncio.run(_driver()._payment_is_pix(None, label)) is False


def test_amazon_accepts_pix_label():
    import asyncio

    assert asyncio.run(_driver()._payment_is_pix(None, "Confirmar pedido com Pix")) is True


@pytest.mark.parametrize("label", ["Confirmar pedido pelo Pix", "Revisar e pagar com Pix"])
def test_amazon_pix_label_is_not_mistaken_for_card_inside_other_words(label):
    import asyncio

    assert asyncio.run(_driver()._payment_is_pix(None, label)) is True


@pytest.mark.parametrize(
    "label",
    ["Pagar com Mastercard4745", "Usar créditos da conta", "Cartão Visa final 1234 + Pix"],
)
def test_amazon_still_refuses_card_words_next_to_digits_or_plural(label):
    import asyncio

    assert asyncio.run(_driver()._payment_is_pix(None, label)) is False


def test_amazon_coupon_selectors_use_stable_names():
    assert amazon._COUPON_FIELD == "input[name='ppw-claimCode']"
    assert amazon._COUPON_APPLY == "input[name='ppw-claimCodeApplyPressed']"


def test_amazon_pix_radio_matches_by_value_not_id():
    assert "ppw-instrumentRowSelection" in amazon._PIX_RADIO
    assert "pix" in amazon._PIX_RADIO
    assert "pp-" not in amazon._PIX_RADIO


def test_amazon_detects_real_rejection_message():
    from watcher.matching import normalize

    real = normalize("O cupom de desconto inserido é inválido.")
    assert any(hint in real for hint in amazon._COUPON_REJECTED)


def test_amazon_does_not_flag_success_as_rejection():
    from watcher.matching import normalize

    ok = normalize("Cupom aplicado com sucesso ao seu pedido")
    assert not any(hint in ok for hint in amazon._COUPON_REJECTED)


def test_kabum_quantity_selectors_match_real_markup():
    from watcher.autobuy import kabum

    assert "quantity-" in kabum._QUANTITY_INPUT
    assert "aumentar" in kabum._QUANTITY_PLUS.lower()


def test_amazon_step_names():
    base = "https://www.amazon.com.br/checkout/p/p-703/"
    assert amazon.AmazonDriver._step_name(base + "pip?x=1") == "prime"
    assert amazon.AmazonDriver._step_name(base + "spc?x=1") == "revisao"
    assert amazon.AmazonDriver._step_name(base + "pay?x=1") == "pagamento"
    assert amazon.AmazonDriver._step_name("https://www.amazon.com.br/dp/B0X") == "produto"


def test_registry_routes_each_store():
    assert registry.match_url("https://www.kabum.com.br/produto/475647/placa").id == "kabum"
    assert registry.match_url("https://www.amazon.com.br/dp/B0BGJR1R3B").id == "amazon"
    assert registry.match_url("https://www.magazineluiza.com.br/x/p/1") is None


def test_registry_find_product_in_message():
    texto = "BUG! SSD 2TB por R$ 289 https://www.amazon.com.br/dp/B0CGBQVDNK corre"
    spec, url = registry.find_product(texto)
    assert spec.id == "amazon"
    assert url == "https://www.amazon.com.br/dp/B0CGBQVDNK"


def test_registry_find_product_respects_allowed_store():
    texto = "promo https://www.amazon.com.br/dp/B0CGBQVDNK"
    assert registry.find_product(texto, ["kabum"]) is None
    assert registry.find_product(texto, ["amazon"]) is not None


def test_registry_find_product_without_link():
    assert registry.find_product("promo sem link nenhum") is None


def test_registry_labels_cover_all_stores():
    labels = registry.labels()
    assert labels["kabum"] == "KaBuM!"
    assert labels["amazon"] == "Amazon"
    assert labels["terabyte"] == "Terabyte"


def test_terabyte_url_recognition():
    from watcher.autobuy import urls

    assert urls.terabyte_id("https://www.terabyteshop.com.br/produto/25639/ssd-adata") == 25639
    assert urls.terabyte_id("https://www.kabum.com.br/produto/105104/x") is None
    assert registry.match_url("https://www.terabyteshop.com.br/produto/25639/x").id == "terabyte"


def test_terabyte_requires_visible_window():
    spec = registry.get("terabyte")
    assert spec.requires_window is True
    assert spec.headless_allowed(True) is False


def test_stores_without_forced_window_keep_preference():
    for store_id in ("kabum", "amazon"):
        spec = registry.get(store_id)
        assert spec.headless_allowed(True) is True
        assert spec.headless_allowed(False) is False


def test_all_stores_can_checkout():
    for store_id in ("kabum", "amazon", "terabyte"):
        assert registry.get(store_id).can_checkout is True


def test_terabyte_pix_selector_avoids_pix_plus_card():
    from watcher.autobuy import terabyte

    assert terabyte._PIX_RADIO == "#ppix"
    assert "ppixcartao" not in terabyte._PIX_RADIO


def test_terabyte_detects_expired_session():
    from watcher.autobuy import terabyte

    assert any("/Central.obj" in m for m in terabyte._LOST_SESSION)


def test_terabyte_detects_real_coupon_rejection():
    from watcher.autobuy import terabyte
    from watcher.matching import normalize

    assert any(h in normalize("! Not valid!") for h in terabyte._COUPON_REJECTED)
    assert not any(h in normalize("Cupom aplicado com sucesso") for h in terabyte._COUPON_REJECTED)


def test_terabyte_detects_logged_user():
    spec = registry.get("terabyte")
    assert spec.logged_user("Olá, VINICIUS\nMinha Conta") == "VINICIUS"
    assert spec.logged_user("Olá, Entre ou Cadastre-se") is None


def test_clean_pix_accepts_valid_code():
    code = "00020126580014BR.GOV.BCB.PIX0136abc" + "0" * 30 + "63041D3D"
    assert clean_pix(code) == code


def test_clean_pix_rejects_code_without_marker():
    code = "00020126580014XX.YYY.ZZZ.AAA0136abc" + "0" * 30 + "63041D3D"
    assert clean_pix(code) == ""


def test_clean_pix_rejects_garbage():
    assert clean_pix("") == ""
    assert clean_pix("nada de pix aqui") == ""


def test_clean_pix_strips_whitespace():
    code = "00020126580014BR.GOV.BCB.PIX0136abc" + "0" * 30 + "63041D3D"
    assert clean_pix(f"  {code[:20]}\n{code[20:]}  ") == code


def test_terabyte_title_drops_store_suffix():
    from watcher.autobuy.terabyte import _clean_title

    assert _clean_title("Placa-Mãe Biostar A520MT Chipset A520 AM4 mATX | Terabyte") == (
        "Placa-Mãe Biostar A520MT Chipset A520 AM4 mATX"
    )
    assert _clean_title("SSD Adata Legend 710 256GB - TERABYTE") == "SSD Adata Legend 710 256GB"
    assert _clean_title("  Monitor  LG   29WK600  ") == "Monitor LG 29WK600"
    assert _clean_title("") == ""


def test_terabyte_price_with_thousands_dot_and_no_cents():
    from watcher.autobuy.terabyte import _to_float

    assert _to_float("R$ 1.299") == 1299.0
    assert _to_float("R$ 12.345") == 12345.0
    assert _to_float("R$ 1.234.567") == 1234567.0
    assert _to_float("R$ 1.299,90") == pytest.approx(1299.90)
    assert _to_float("1299.90") == pytest.approx(1299.90)
    assert _to_float("289.9") == pytest.approx(289.9)
    assert _to_float("") == 0.0


@pytest.mark.parametrize(
    "texto, valor",
    [
        ("R$ 1.299,90", 1299.90),
        ("R$\xa01.299,90", 1299.90),
        ("R$1,299.90", 1299.90),
        ("R$ 1.299", 1299.0),
        ("R$ 89,9", 89.9),
        ("R$ 12", 12.0),
        ("", 0.0),
    ],
)
def test_amazon_price_parser_never_reads_a_lower_price(texto, valor):
    assert amazon._to_float(texto) == pytest.approx(valor)


def test_no_store_needs_real_chrome():
    for outra in ("kabum", "amazon", "terabyte"):
        assert registry.get(outra).needs_real_chrome is False


def _crc16(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


_BCB_EXAMPLE = (
    "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
    "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D"
)


def test_clean_pix_keeps_spaces_that_belong_to_the_code():
    assert clean_pix(_BCB_EXAMPLE) == _BCB_EXAMPLE
    assert clean_pix(f"Pix copia e cola:\n{_BCB_EXAMPLE}\nValido por 30 minutos") == _BCB_EXAMPLE
    assert clean_pix(f"{_BCB_EXAMPLE[:50]}\n{_BCB_EXAMPLE[50:]}") == _BCB_EXAMPLE


def test_clean_pix_does_not_cut_code_at_earlier_crc_marker():
    body = (
        "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
        "5204000053039865802BR5913Fulano de Tal6008BRASILIA"
        "62140510TX6304ABCD6304"
    )
    code = body + _crc16(body)
    assert clean_pix(f"Copie o código: {code} e pague") == code
    assert clean_pix(f"{code[:40]}\n{code[40:]}") == code


def test_clean_pix_finds_crc_right_after_a_field_ending_in_the_marker():
    body = (
        "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
        "5204000053039865802BR5913Fulano de Tal6008BRASILIA"
        "62100506AB63046304"
    )
    code = body + _crc16(body)
    assert clean_pix(f"pague: {code} ok") == code


@pytest.mark.parametrize(
    "module_name, class_name",
    [("kabum", "KabumDriver"), ("amazon", "AmazonDriver"), ("terabyte", "TerabyteDriver")],
)
def test_pix_result_keeps_code_when_record_cannot_be_written(tmp_path, monkeypatch, module_name, class_name):
    import asyncio
    import importlib

    from watcher import paths
    from watcher.autobuy.models import BuyStatus, ProductInfo

    module = importlib.import_module(f"watcher.autobuy.{module_name}")
    driver_class = getattr(module, class_name)
    driver = driver_class.__new__(driver_class)
    driver._log = lambda _m: None
    driver._coupon_note = ""
    blocked = tmp_path / "arquivo"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(paths, "autobuy_dir", lambda: blocked)

    class Page:
        async def screenshot(self, **_kwargs):
            raise RuntimeError("sem tela")

    product = ProductInfo(id=1, title="SSD", url="https://x", pix_price=10.0, card_price=12.0, available=True)
    result = asyncio.run(driver._pix_result(Page(), product, "00020126codigo", module._Stopwatch()))
    assert result.status == BuyStatus.PIX_READY
    assert result.pix_code == "00020126codigo"


def test_session_cookies_survive_interrupted_write(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from watcher import paths
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)

    class Context:
        def __init__(self, value):
            self.value = value

        async def cookies(self):
            return [{"name": "session", "value": self.value, "domain": ".kabum.com.br", "path": "/", "expires": -1}]

    assert asyncio.run(browser.save_session_cookies(Context("original"))) == 1
    original_write = Path.write_text

    def disk_full(self, data, *args, **kwargs):
        original_write(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    assert asyncio.run(browser.save_session_cookies(Context("novo"))) == 0
    monkeypatch.undo()
    saved = (tmp_path / "session_cookies.json").read_text(encoding="utf-8")
    assert '"original"' in saved


class _PaginaKabum:
    def __init__(self, produto: dict) -> None:
        import json

        self.url = "https://www.kabum.com.br/produto/123/ssd"
        self.cliques: list[str] = []
        self._dados = json.dumps({"props": {"pageProps": {"product": produto}}})

    async def goto(self, *_args, **_kwargs):
        return None

    async def evaluate(self, _script):
        return self._dados

    async def click(self, selector, **_kwargs):
        self.cliques.append(selector)

    async def screenshot(self, **_kwargs):
        raise RuntimeError("sem tela")

    async def close(self):
        return None


def _compra_kabum(tmp_path, monkeypatch, produto: dict):
    import asyncio

    from watcher import paths
    from watcher.autobuy.kabum import KabumDriver

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    pagina = _PaginaKabum(produto)

    class Contexto:
        async def new_page(self):
            return pagina

    resultado = asyncio.run(KabumDriver(Contexto()).buy(pagina.url, max_price=300.0, confirm=True))
    return resultado, pagina


def test_kabum_sem_preco_na_pagina_nao_segue_para_o_checkout(tmp_path, monkeypatch):
    from watcher.autobuy.models import BuyStatus

    resultado, pagina = _compra_kabum(tmp_path, monkeypatch, {"id": 123, "title": "SSD", "available": True})
    assert resultado.status == BuyStatus.NOT_FOUND
    assert pagina.cliques == []


def test_kabum_com_preco_acima_do_limite_para(tmp_path, monkeypatch):
    from watcher.autobuy.models import BuyStatus

    produto = {"id": 123, "title": "SSD", "available": True, "prices": {"price": 500.0, "priceWithDiscount": 450.0}}
    resultado, pagina = _compra_kabum(tmp_path, monkeypatch, produto)
    assert resultado.status == BuyStatus.PRICE_ABOVE_LIMIT
    assert resultado.product.pix_price == 450.0
    assert pagina.cliques == []


def test_apagar_registro_da_terabyte_leva_o_print_do_pix_junto(tmp_path, monkeypatch):
    import asyncio
    import itertools

    from watcher import paths
    from watcher.autobuy import records, terabyte
    from watcher.autobuy.models import ProductInfo

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    segundos = itertools.count()
    monkeypatch.setattr(terabyte.time, "strftime", lambda _fmt, *_a: f"20260101-0000{next(segundos):02d}")

    class Pagina:
        async def screenshot(self, path, **_kwargs):
            with open(path, "wb") as arquivo:
                arquivo.write(b"png")

    driver = terabyte.TerabyteDriver.__new__(terabyte.TerabyteDriver)
    driver._log = lambda _m: None
    driver._coupon_note = ""
    produto = ProductInfo(id=1, title="SSD", url="https://www.terabyteshop.com.br/produto/1", pix_price=10.0, card_price=12.0, available=True)
    resultado = asyncio.run(driver._pix_result(Pagina(), produto, "00020126codigo", terabyte._Stopwatch()))
    stamp = records.load_records()[0].stamp
    assert resultado.screenshot.startswith(str(tmp_path / stamp))
    assert records.delete_record(stamp) == 3
    assert list(tmp_path.iterdir()) == []


def test_apagar_registro_so_aceita_carimbo_de_verdade(tmp_path, monkeypatch):
    from watcher import paths
    from watcher.autobuy import records

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    (tmp_path / "20260101-000000-pix.json").write_text("{}", encoding="utf-8")
    (tmp_path / "20260101-000000-pix-ssd.txt").write_text("codigo", encoding="utf-8")
    for carimbo in ("*", "2026*", "../*", ""):
        assert records.delete_record(carimbo) == 0
    assert len(list(tmp_path.iterdir())) == 2
