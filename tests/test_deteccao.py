from __future__ import annotations

import pytest

from watcher.detector import Coupon, Detection, detect, matches_smart_rules
from watcher.matching import (
    contains_word,
    expand_keywords,
    expand_variations,
    matches_exclude,
    matches_include,
    matches_keyword,
    normalize,
)


@pytest.mark.parametrize("texto", ["", "   ", "oi", None])
def test_texto_curto_ou_vazio_nao_vira_oferta(texto):
    resultado = detect(texto)
    assert resultado == Detection()
    assert resultado.label == "Oferta"
    assert resultado.best_coupon is None


def test_porcentagem_acima_de_99_nao_conta_como_desconto():
    assert detect("100% original com 15% off").discount_percent == 15
    assert detect("Produto 100% algodão, rende 150%").discount_percent is None


def test_nome_de_loja_ou_so_numero_nao_vira_cupom():
    assert detect("Cupom: SHOPEE hoje").coupons == []
    assert detect("cupom 12345 valido").coupons == []
    assert not detect("Cupom: SHOPEE hoje").has_coupon


def test_sem_cupom_nao_exige_cupom_mesmo_com_codigo_no_texto():
    resultado = detect("Desconto direto sem cupom, use PROMO10")
    assert resultado.requires_coupon is False
    assert resultado.best_coupon.code == "PROMO10"
    com_cupom = detect("Use o cupom PROMO10 no carrinho")
    assert com_cupom.requires_coupon is True


def test_melhor_cupom_e_o_de_maior_confianca():
    resultado = Detection(coupons=[Coupon("FRACO1", 0.4), Coupon("FORTE1", 0.9), Coupon("MEIO1", 0.6)])
    assert resultado.best_coupon.code == "FORTE1"
    assert resultado.has_coupon
    assert not Detection(coupons=[Coupon("FRACO1", 0.5)]).has_coupon


@pytest.mark.parametrize(
    "texto, rotulo",
    [
        ("Compre e ganhe cashback de 5%", "Cashback"),
        ("Oferta relampago de fone", "Relâmpago"),
        ("So novos usuarios: primeira compra", "Novo usuário"),
        ("MEGA promo 40% OFF", "40% OFF"),
        ("Aproveite frete gratis e cashback", "Frete grátis"),
    ],
)
def test_rotulo_segue_a_prioridade_da_oferta(texto, rotulo):
    assert detect(texto).label == rotulo


def test_nome_da_loja_para_exibir():
    assert detect("Oferta no Mercado Livre hoje").store_label == "Mercado Livre"
    assert detect("Bom dia a todos do grupo").store_label is None


def test_valor_de_desconto_nao_e_confundido_com_preco_de_produto():
    assert detect("R$ 50 OFF acima de R$ 200").has_price is False
    assert detect("Ganhe R$ 20 de volta em compras de R$ 100").has_price is False
    assert detect("Fone por R$ 199").has_price is True
    assert detect("Headset Logitech R$ 349,90").has_price is True


def test_confianca_nunca_passa_de_um():
    texto = "🔥 Cupom MELI50 no Mercado Livre https://mercadolivre.com.br frete gratis 30% OFF cashback"
    resultado = detect(texto)
    assert 0.9 <= resultado.confidence <= 1.0
    assert resultado.has_offer


@pytest.mark.parametrize(
    "regra",
    ["cupom", "coupon", "  ANY_COUPON  "],
)
def test_apelidos_da_regra_de_cupom(regra):
    assert matches_smart_rules(detect("Cupom MELI50: R$ 50 OFF em tudo"), [regra])


def test_apelidos_das_regras_inteligentes():
    frete = detect("Aproveite o frete gratis hoje")
    assert matches_smart_rules(frete, ["frete"])
    novo = detect("So novos usuarios na primeira compra")
    assert matches_smart_rules(novo, ["novo"]) and matches_smart_rules(novo, ["new_user"])
    relampago = detect("Oferta relampago de fone")
    assert matches_smart_rules(relampago, ["relampago"]) and matches_smart_rules(relampago, ["flash"])
    quente = detect("Corre que ta queimando")
    assert matches_smart_rules(quente, ["quente"]) and matches_smart_rules(quente, ["hot"])
    assert matches_smart_rules(detect("Promo na Shopee"), ["  Store:Shopee "])


def test_regra_desconhecida_vazia_ou_malformada_nao_casa():
    oferta = detect("Cupom MELI50 com 70% OFF no Mercado Livre")
    assert not matches_smart_rules(oferta, [])
    assert not matches_smart_rules(oferta, ["", "   "])
    assert not matches_smart_rules(oferta, ["qualquer_coisa"])
    assert not matches_smart_rules(oferta, ["discount>=abc"])
    assert not matches_smart_rules(detect("Cupom MELI50 no Mercado Livre"), ["discount>=1"])
    assert matches_smart_rules(oferta, ["qualquer_coisa", "discount>=70"])


def test_normaliza_texto_vazio_e_acentos():
    assert normalize("") == ""
    assert normalize(None) == ""
    assert normalize("AÇÃO Relâmpago") == "acao relampago"


@pytest.mark.parametrize(
    "palavra, variacao",
    [
        ("cartao", "cartoes"),
        ("cupom", "cupons"),
        ("monitores", "monitor"),
        ("luz", "luzes"),
        ("fone", "fones"),
    ],
)
def test_plural_e_singular_da_palavra(palavra, variacao):
    assert variacao in expand_variations(palavra)


def test_palavra_curta_ou_vazia_nao_ganha_plural():
    assert expand_variations("tv") == {"tv"}
    assert expand_variations("   ") == set()
    assert expand_keywords(["ssd", "", "ssd"]) == ["ssd", "ssds"]


def test_numero_nao_casa_dentro_de_numero_maior():
    assert not contains_word("rtx 40600", "4060")
    assert not contains_word("modelo 14060", "4060")
    assert contains_word("rtx 4060", "4060")
    assert not contains_word("", "4060")
    assert not contains_word("rtx 4060", "   ")


def test_busca_ignora_acento_nos_dois_lados():
    assert matches_keyword("PROMOÇÃO de SSD", "promocao")
    assert matches_keyword("promocao de ssd", "promoção")
    assert not matches_keyword("", "ssd")
    assert not matches_keyword("ssd", "")


def test_alternativa_com_barra_vertical():
    assert matches_include("hd externo barato", ["ssd|hd"])
    assert not matches_include("pendrive barato", ["ssd | hd"])


def test_incluir_so_com_espacos_vale_como_vazio_e_excluir_ignora_palavra_em_branco():
    assert matches_include("qualquer coisa", ["", "   "]) is True
    assert matches_exclude("produto esgotado", ["", "   "]) is False


def test_varias_frases_casam_se_qualquer_uma_casar():
    incluir = ["ssd + 2tb", "rtx + 5060"]
    assert matches_include("SSD NVMe 2TB", incluir)
    assert matches_include("RTX 5060 Ventus", incluir)
    assert not matches_include("SSD 1TB e RTX 4060", incluir)
