from monitor.filtering import resolve_destinations
from monitor.models import Filter

DEFAULT = [-999]


def make(**kwargs) -> Filter:
    return Filter.from_dict({"id": "x", "name": "t", **kwargs})


def test_no_filters_uses_default():
    assert resolve_destinations([], -1, "oi", DEFAULT) == [-999]


def test_disabled_only_uses_default():
    flt = make(enabled=False, include_words=["cupom"])
    assert resolve_destinations([flt], -1, "cupom", DEFAULT) == [-999]


def test_include_routes_to_filter_destination():
    flt = make(include_words=["cupom"], destination_ids=[-100])
    assert resolve_destinations([flt], -1, "tem cupom aqui", DEFAULT) == [-100]
    assert resolve_destinations([flt], -1, "nada relevante", DEFAULT) == []


def test_exclude_blocks():
    flt = make(include_words=["cupom"], exclude_words=["esgotado"], destination_ids=[-100])
    assert resolve_destinations([flt], -1, "cupom esgotado", DEFAULT) == []


def test_source_scoping():
    flt = make(source_ids=[-5], destination_ids=[-100])
    assert resolve_destinations([flt], -5, "qualquer", DEFAULT) == [-100]
    assert resolve_destinations([flt], -1, "qualquer", DEFAULT) == []


def test_smart_rule_any_coupon():
    flt = make(smart_rules=["any_coupon"], destination_ids=[-100])
    assert resolve_destinations([flt], -1, "Cupom MELI50 no Mercado Livre", DEFAULT) == [-100]
    assert resolve_destinations([flt], -1, "apenas um bom dia", DEFAULT) == []


def test_smart_rule_store_falls_back_to_default_destination():
    flt = make(smart_rules=["store:shopee"])
    assert resolve_destinations([flt], -1, "promo na shopee hoje", DEFAULT) == [-999]


def test_multiple_filters_union_destinations():
    a = make(include_words=["cupom"], destination_ids=[-100])
    b = make(smart_rules=["free_shipping"], destination_ids=[-200])
    out = resolve_destinations([a, b], -1, "cupom com frete gratis", DEFAULT)
    assert set(out) == {-100, -200}


def test_include_filter_does_not_replicate_others():
    flt = make(include_words=["9800x3d", "mãe", "5070"], destination_ids=[-100])
    assert resolve_destinations([flt], -1, "placa 5070 barata", DEFAULT) == [-100]
    assert resolve_destinations([flt], -1, "suporte para monitor e tv", DEFAULT) == []


def test_exclude_only_filter_is_blocklist_not_catch_all():
    keywords = make(include_words=["5070"], destination_ids=[-100])
    blocklist = make(exclude_words=["pneu"], destination_ids=[-100])
    assert resolve_destinations([keywords, blocklist], -1, "kit pampers glicerina", DEFAULT) == []
    assert resolve_destinations([keywords, blocklist], -1, "placa 5070", DEFAULT) == [-100]


def test_exclude_blocks_across_filters():
    keywords = make(include_words=["5070"], destination_ids=[-100])
    blocklist = make(exclude_words=["pneu"], destination_ids=[-100])
    assert resolve_destinations([keywords, blocklist], -1, "placa 5070 com pneu", DEFAULT) == []


def test_min_confidence_gate():
    flt = make(smart_rules=["store:shopee"], destination_ids=[-100], min_confidence=0.9)
    assert resolve_destinations([flt], -1, "shopee", DEFAULT) == []
