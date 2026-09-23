from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_OR = re.compile(r"\s+(?:or|ou)\s+|\|", re.IGNORECASE)

_SYNONYMS: dict[str, set[str]] = {
    "cupom": {"cupons", "promocode", "voucher"},
    "frete": {"frete gratis", "envio gratis", "envio gratuito", "sem frete"},
    "desconto": {"descontos", "off", "promocao", "promo"},
    "mercado livre": {"mercadolivre", "meli", "mercado pago"},
    "magalu": {"magazine luiza", "magazineluiza"},
    "shopee": {"shp"},
    "aliexpress": {"alibaba"},
    "americanas": {"submarino", "b2w"},
    "casas bahia": {"casasbahia", "ponto frio", "pontofrio"},
}


def normalize(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _edge(char: str) -> str:
    if char.isdigit():
        return r"\d"
    if char.isalpha():
        return r"[^\W\d_]"
    return ""


@lru_cache(maxsize=2048)
def _word_pattern(term: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in term.split())
    left = _edge(term[0])
    right = _edge(term[-1])
    before = f"(?<!{left})" if left else ""
    after = f"(?!{right})" if right else ""
    return re.compile(before + body + after)


def contains_word(haystack: str, term: str) -> bool:
    term = " ".join(term.split())
    if not haystack or not term:
        return False
    return _word_pattern(term).search(haystack) is not None


def expand_variations(word: str) -> set[str]:
    word = " ".join(normalize(word).split())
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
            if len(word) > 4 and word.endswith(("res", "zes")):
                out.add(word[:-2])
        else:
            out.add(word + "s")
            if word.endswith(("r", "z")):
                out.add(word + "es")
    for base, syns in _SYNONYMS.items():
        if contains_word(word, base) or contains_word(base, word) or word in syns:
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
    return any(contains_word(haystack, variation) for variation in expand_variations(keyword))


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
