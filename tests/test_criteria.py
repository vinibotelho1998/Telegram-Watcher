from __future__ import annotations

from monitor.autobuy.criteria import Criteria
from monitor.autobuy.models import WatchItem


def test_to_include_joins_all_and_any():
    criteria = Criteria(all_words=["ssd", "2tb"], any_words=["bug", "erro de preco"])
    assert criteria.to_include() == ["ssd + 2tb + bug ou erro de preco"]


def test_to_include_only_all():
    assert Criteria(all_words=["rtx 5060"]).to_include() == ["rtx 5060"]


def test_to_include_only_any():
    assert Criteria(any_words=["bug", "erro"]).to_include() == ["bug ou erro"]


def test_to_include_empty():
    assert Criteria().to_include() == []


def test_parse_roundtrip():
    criteria = Criteria(all_words=["ssd", "2tb"], any_words=["bug", "erro"], none_words=["usado"])
    assert Criteria.parse(criteria.to_include(), criteria.to_exclude()) == criteria


def test_parse_legacy_multiple_phrases_become_alternatives():
    criteria = Criteria.parse(["rtx 5060", "rtx 5070"], [])
    assert criteria.all_words == []
    assert criteria.any_words == ["rtx 5060", "rtx 5070"]


def test_parse_drops_duplicates():
    criteria = Criteria.parse(["ssd + ssd"], ["usado", "USADO"])
    assert criteria.all_words == ["ssd"]
    assert criteria.none_words == ["usado"]


def test_generated_criteria_drives_watch_item():
    criteria = Criteria(all_words=["ssd", "2tb"], any_words=["bug", "erro de preco"], none_words=["usado"])
    item = WatchItem(
        id="w1",
        name="SSD",
        max_price=350.0,
        include=criteria.to_include(),
        exclude=criteria.to_exclude(),
    )
    assert item.matches("BUG DE PRECO! SSD 2TB por R$ 289")
    assert item.matches("Erro de preço no SSD 2TB")
    assert not item.matches("SSD 2TB por R$ 289")
    assert not item.matches("BUG no SSD 1TB")
    assert not item.matches("BUG SSD 2TB usado")


def test_check_reports_missing_required_word():
    report = Criteria(all_words=["ssd", "2tb"]).check("promo de ssd 1tb")
    assert not report.matched
    assert "2tb" in report.reason
    assert [c.hit for c in report.all_checks] == [True, False]


def test_check_reports_blocked_word():
    report = Criteria(all_words=["ssd"], none_words=["usado"]).check("ssd usado barato")
    assert not report.matched
    assert "usado" in report.reason


def test_check_reports_missing_alternative():
    report = Criteria(all_words=["ssd"], any_words=["bug", "erro"]).check("ssd normal em promocao")
    assert not report.matched
    assert "alternativas" in report.reason


def test_check_accepts():
    report = Criteria(all_words=["ssd"], any_words=["bug"], none_words=["usado"]).check("BUG no ssd novo")
    assert report.matched


def test_check_requires_words():
    assert Criteria().is_empty()
    report = Criteria().check("qualquer texto")
    assert not report.matched
    assert "palavra" in report.reason


def test_summary_describes_rules():
    summary = Criteria(all_words=["ssd", "2tb"], any_words=["bug"], none_words=["usado"]).summary()
    assert "ssd e 2tb" in summary
    assert "usado" in summary
