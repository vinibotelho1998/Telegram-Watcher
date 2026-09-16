from __future__ import annotations

import re
import unicodedata

_OR = re.compile(r"\s+(?:or|ou)\s+|\|", re.IGNORECASE)

_SYNONYMS: dict[str, set[str]] = {
    "cupom": {"cupons", "codigo", "vale", "promocode", "voucher"},
    "frete": {"frete gratis", "envio gratis", "envio gratuito", "sem frete"},
    "desconto": {"descontos", "off", "promocao", "promo"},
    "mercado livre": {"mercadolivre", "meli", "mercado pago"},
    "magalu": {"magazine luiza", "magazineluiza", "magazine"},
    "shopee": {"shp"},
    "amazon": {"prime"},
    "aliexpress": {"ali", "alibaba"},
    "americanas": {"submarino", "b2w"},
    "casas bahia": {"casasbahia", "ponto frio", "pontofrio"},
}


def normalize(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def expand_variations(word: str) -> set[str]:
    word = normalize(word).strip()
    if not word:
        return set()
    out = {word}
    if len(word) > 2:
        if word.endswith("m"):
            out.add(word[:-1] + "ns")
        elif word.endswith("ao"):
            out.add(word[:-2] + "oes")
        elif word.endswith("s"):
            out.add(word[:-1])
        else:
            out.add(word + "s")
    for base, syns in _SYNONYMS.items():
        if base in word or word in base:
            out.add(base)
            out.update(syns)
        elif word in syns:
            out.add(base)
            out.update(syns)
    return out


def expand_keywords(keywords: list[str]) -> list[str]:
    out: set[str] = set()
    for kw in keywords:
        out.update(expand_variations(kw))
    return sorted(out)


def matches_keyword(text: str, keyword: str) -> bool:
    if not text or not keyword:
        return False
    haystack = normalize(text)
    return any(variation in haystack for variation in expand_variations(keyword))


def _part_matches(text: str, part: str) -> bool:
    options = [o.strip() for o in _OR.split(part) if o.strip()]
    return any(matches_keyword(text, option) for option in options)


def matches_include(text: str, include: list[str]) -> bool:
    phrases = [p for p in (s.strip() for s in include) if p]
    if not phrases:
        return True
    for phrase in phrases:
        parts = [p.strip() for p in phrase.split("+") if p.strip()]
        if parts and all(_part_matches(text, part) for part in parts):
            return True
    return False


def matches_exclude(text: str, exclude: list[str]) -> bool:
    return any(matches_keyword(text, word) for word in exclude if word.strip())
