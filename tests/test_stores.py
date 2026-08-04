from __future__ import annotations

import pytest

from monitor.autobuy import registry
from monitor.autobuy.pix import clean_pix

amazon = pytest.importorskip("monitor.autobuy.amazon", reason="requer playwright instalado")


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


def test_amazon_coupon_selectors_use_stable_names():
    assert amazon._COUPON_FIELD == "input[name='ppw-claimCode']"
    assert amazon._COUPON_APPLY == "input[name='ppw-claimCodeApplyPressed']"


def test_amazon_pix_radio_matches_by_value_not_id():
    assert "ppw-instrumentRowSelection" in amazon._PIX_RADIO
    assert "pix" in amazon._PIX_RADIO
    assert "pp-" not in amazon._PIX_RADIO


def test_amazon_detects_real_rejection_message():
    from monitor.matching import normalize

    real = normalize("O cupom de desconto inserido é inválido.")
    assert any(hint in real for hint in amazon._COUPON_REJECTED)


def test_amazon_does_not_flag_success_as_rejection():
    from monitor.matching import normalize

    ok = normalize("Cupom aplicado com sucesso ao seu pedido")
    assert not any(hint in ok for hint in amazon._COUPON_REJECTED)


def test_kabum_quantity_selectors_match_real_markup():
    from monitor.autobuy import kabum

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
    from monitor.autobuy import urls

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
    from monitor.autobuy import terabyte

    assert terabyte._PIX_RADIO == "#ppix"
    assert "ppixcartao" not in terabyte._PIX_RADIO


def test_terabyte_detects_expired_session():
    from monitor.autobuy import terabyte

    assert any("/Central.obj" in m for m in terabyte._LOST_SESSION)


def test_terabyte_detects_real_coupon_rejection():
    from monitor.autobuy import terabyte
    from monitor.matching import normalize

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
    from monitor.autobuy.terabyte import _clean_title

    assert _clean_title("Placa-Mãe Biostar A520MT Chipset A520 AM4 mATX | Terabyte") == (
        "Placa-Mãe Biostar A520MT Chipset A520 AM4 mATX"
    )
    assert _clean_title("SSD Adata Legend 710 256GB - TERABYTE") == "SSD Adata Legend 710 256GB"
    assert _clean_title("  Monitor  LG   29WK600  ") == "Monitor LG 29WK600"
    assert _clean_title("") == ""
