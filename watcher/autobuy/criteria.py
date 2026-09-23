from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..matching import matches_include, matches_keyword

_OR_SPLIT = re.compile(r"\s+(?:or|ou)\s+|\|", re.IGNORECASE)
_JUNCAO = re.compile(r"\s\+\s")


def _hit(text: str, word: str) -> bool:
    if _JUNCAO.search(word):
        return matches_include(text, [word])
    return matches_keyword(text, word)


def _clean(words: list[str]) -> list[str]:
    out: list[str] = []
    for word in words:
        value = " ".join(word.split()).strip(" ,")
        if value and value.lower() not in {w.lower() for w in out}:
            out.append(value)
    return out


@dataclass(slots=True)
class WordCheck:
    word: str
    hit: bool


@dataclass(slots=True)
class CriteriaReport:
    matched: bool
    all_checks: list[WordCheck] = field(default_factory=list)
    any_checks: list[WordCheck] = field(default_factory=list)
    none_checks: list[WordCheck] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class Criteria:
    all_words: list[str] = field(default_factory=list)
    any_words: list[str] = field(default_factory=list)
    none_words: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, include: list[str], exclude: list[str]) -> "Criteria":
        phrases = [p for p in (s.strip() for s in include) if p]
        all_words: list[str] = []
        any_words: list[str] = []
        if len(phrases) == 1:
            for part in (p.strip() for p in phrases[0].split("+")):
                if not part:
                    continue
                options = [o.strip() for o in _OR_SPLIT.split(part) if o.strip()]
                if len(options) > 1:
                    any_words.extend(options)
                else:
                    all_words.append(part)
        elif phrases:
            for phrase in phrases:
                partes = [p.strip() for p in phrase.split("+") if p.strip()]
                if len(partes) > 1:
                    any_words.append(" + ".join(partes))
                    continue
                any_words.extend(o.strip() for o in _OR_SPLIT.split(phrase) if o.strip())
        return cls(all_words=_clean(all_words), any_words=_clean(any_words), none_words=_clean(exclude))

    def is_simple(self) -> bool:
        return not any("+" in word for word in self.all_words + self.any_words)

    def is_empty(self) -> bool:
        return not self.all_words and not self.any_words

    def to_include(self) -> list[str]:
        parts = list(self.all_words)
        if self.any_words:
            parts.append(" ou ".join(self.any_words))
        return [" + ".join(parts)] if parts else []

    def to_exclude(self) -> list[str]:
        return list(self.none_words)

    def summary(self) -> str:
        parts: list[str] = []
        if self.all_words:
            parts.append("tem " + " e ".join(self.all_words))
        if self.any_words:
            parts.append("tem " + " ou ".join(self.any_words))
        if self.none_words:
            parts.append("não tem " + ", ".join(self.none_words))
        return "   |   ".join(parts) if parts else "sem palavras definidas"

    def check(self, text: str) -> CriteriaReport:
        all_checks = [WordCheck(word, _hit(text, word)) for word in self.all_words]
        any_checks = [WordCheck(word, _hit(text, word)) for word in self.any_words]
        none_checks = [WordCheck(word, matches_keyword(text, word)) for word in self.none_words]
        blocked = [c.word for c in none_checks if c.hit]
        missing = [c.word for c in all_checks if not c.hit]
        report = CriteriaReport(False, all_checks, any_checks, none_checks)
        if self.is_empty():
            report.reason = "Defina ao menos uma palavra obrigatória ou uma alternativa"
            return report
        if blocked:
            report.reason = f"Bloqueado pela palavra proibida: {', '.join(blocked)}"
            return report
        if missing:
            report.reason = f"Falta a palavra obrigatória: {', '.join(missing)}"
            return report
        if self.any_words and not any(c.hit for c in any_checks):
            report.reason = "Nenhuma das alternativas apareceu no texto"
            return report
        report.matched = True
        report.reason = "As palavras batem"
        return report
