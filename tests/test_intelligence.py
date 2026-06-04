import pytest

from monitor.detector import CouponDetector, matches_smart_rules
from monitor.matching import (
    matches_exclude,
    matches_include,
    matches_keyword,
    normalize,
)

det = CouponDetector()


@pytest.mark.parametrize(
    "text, code",
    [
        ("Cupom: MELI50 - 20% OFF no Mercado Livre", "MELI50"),
        ("Use o cupom MAE100 para ganhar R$ 20 OFF", "MAE100"),
        ("Codigo PELANDO10 valido ate amanha", "PELANDO10"),
        ("🔥 Cupom LIVE200 acima de R$ 200", "LIVE200"),
        ("CUPOM: VOLTEI30 (so funciona no app)", "VOLTEI30"),
        ("Ganhe 30% OFF com o codigo PRAVC", "PRAVC"),
        ("COUPON CODE: AMAZONBR20", "AMAZONBR20"),
    ],
)
def test_detects_real_coupons(text, code):
    result = det.detect(text)
    assert result.has_coupon, f"esperava cupom em: {text}"
    assert code in [c.code for c in result.coupons]


@pytest.mark.parametrize(
    "text",
    [
        "Cupom aplicado automaticamente (sem codigo)",
        "Preco baixo! R$ 50 OFF sem cupom",
        "Bom dia, alguem sabe que horas abre a loja?",
        "GANHE muito dinheiro trabalhando em casa",
    ],
)
def test_no_false_coupon(text):
    assert not det.detect(text).has_coupon


@pytest.mark.parametrize(
    "text, store",
    [
        ("Oferta no Mercado Livre hoje", "mercado_livre"),
        ("Confira em magazineluiza.com.br", "magalu"),
        ("Promo na Shopee", "shopee"),
        ("Link da amazon.com.br aqui", "amazon"),
    ],
)
def test_store_detection(text, store):
    assert det.detect(text).store == store


def test_smart_rules_coupon_and_store_and_discount():
    result = det.detect("Cupom: MELI50 - 30% OFF no Mercado Livre")
    assert matches_smart_rules(result, ["any_coupon"])
    assert matches_smart_rules(result, ["store:mercado_livre"])
    assert matches_smart_rules(result, ["discount>=30"])
    assert not matches_smart_rules(result, ["discount>=50"])
    assert not matches_smart_rules(result, ["cashback"])


def test_smart_rules_free_shipping_and_cashback():
    fs = det.detect("Aproveite o frete gratis na Shopee hoje")
    assert fs.is_free_shipping and matches_smart_rules(fs, ["free_shipping"])
    cb = det.detect("Compre e receba 10% de cashback")
    assert cb.is_cashback and matches_smart_rules(cb, ["cashback"])


def test_discount_extraction():
    assert det.detect("MEGA promo 70% OFF").discount_percent == 70
    assert det.detect("sem percentual aqui").discount_percent is None


def test_label():
    assert det.detect("Cupom MELI50 no Mercado Livre").label == "Cupom"
    assert det.detect("Frete gratis hoje").label == "Frete grátis"


def test_normalize():
    assert normalize("Promoção É Imperdível") == "promocao e imperdivel"


def test_matches_keyword_variations():
    assert matches_keyword("vi no mercado livre", "meli")
    assert matches_keyword("cupons de desconto", "cupom")
    assert not matches_keyword("nada aqui", "shopee")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("cupom desconto para lojas americanas apenas hoje", True),
        ("cupom desconto para mercado livre apenas hoje", True),
        ("cupom desconto para meli apenas hoje", True),
        ("aqui esta o cupom da amazon", False),
    ],
)
def test_include_and_or_logic(text, expected):
    include = ["cupom + americanas ou mercado livre or meli"]
    assert matches_include(text, include) is expected


def test_include_empty_passes():
    assert matches_include("qualquer coisa", []) is True


def test_exclude():
    assert matches_exclude("produto esgotado", ["esgotado"])
    assert not matches_exclude("disponivel agora", ["esgotado"])
