from __future__ import annotations

import re
from dataclasses import dataclass, field

from .matching import normalize

STORES: dict[str, dict[str, list[str]]] = {
    "mercado_livre": {
        "label": ["Mercado Livre"],
        "names": ["mercado livre", "mercadolivre", "meli", "mercado pago"],
        "domains": ["mercadolivre.com.br", "mercadolivre.com", "mercadopago.com.br"],
        "prefixes": ["MELI", "VOLTEI"],
    },
    "magalu": {
        "label": ["Magazine Luiza"],
        "names": ["magalu", "magazine luiza", "magazineluiza"],
        "domains": ["magazineluiza.com.br", "magalu.com.br"],
        "prefixes": ["MAE", "PRAVC", "MAGALU"],
    },
    "amazon": {
        "label": ["Amazon"],
        "names": ["amazon"],
        "domains": ["amazon.com.br", "amzn.to", "amazon.com"],
        "prefixes": ["AMAZON"],
    },
    "shopee": {
        "label": ["Shopee"],
        "names": ["shopee", "shoppe"],
        "domains": ["shopee.com.br", "shp.ee"],
        "prefixes": ["SHOPEE"],
    },
    "aliexpress": {
        "label": ["AliExpress"],
        "names": ["aliexpress", "ali express", "alibaba"],
        "domains": ["aliexpress.com", "s.click.aliexpress.com"],
        "prefixes": ["MIBR", "PELANDO"],
    },
    "americanas": {
        "label": ["Americanas"],
        "names": ["americanas", "submarino"],
        "domains": ["americanas.com.br", "submarino.com.br"],
        "prefixes": ["AMERICANAS"],
    },
    "casas_bahia": {
        "label": ["Casas Bahia"],
        "names": ["casas bahia", "casasbahia", "ponto frio", "pontofrio"],
        "domains": ["casasbahia.com.br", "pontofrio.com.br"],
        "prefixes": ["PONTOFRIO"],
    },
    "kabum": {
        "label": ["KaBuM!"],
        "names": ["kabum"],
        "domains": ["kabum.com.br"],
        "prefixes": ["KABUM"],
    },
    "shein": {
        "label": ["Shein"],
        "names": ["shein"],
        "domains": ["shein.com.br", "shein.com"],
        "prefixes": ["SHEIN"],
    },
}

_COUPON_PATTERNS: list[tuple[str, str, float]] = [
    (r"\b(MIBR|PELANDO|MAE|PRAVC|MELI|VALE|LIVE|VOLTEI|MAGALU|SHEIN)(\d{1,4})\b", "loja", 0.35),
    (r"(?:cupom|cupons|c[oó]digo|code|promocode)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,20})\b", "rotulado", 0.3),
    (r"(?:use|cole|insira|aplique|digite|utilize)\s*(?:o\s*)?(?:cupom|c[oó]digo)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,20})\b", "acao", 0.25),
    (r"(?:voucher|vale)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,20})\b", "voucher", 0.2),
    (r"\b([A-Z][A-Z0-9]{3,15})\b[^\n]{0,25}(?:off|de desconto)", "perto_desconto", 0.15),
    (r"[🎟🎫🏷🎁]\s*([A-Za-z0-9][A-Za-z0-9_-]{2,20})\b", "emoji", 0.15),
    (r"\b([A-Z]{2,}\d{2,}[A-Z0-9]*)\b", "alfanumerico", 0.1),
]

_FALSE_POSITIVES = {
    "HTTPS", "HTTP", "WWW", "COM", "HTML", "OFF", "FULL", "HD", "FHD", "4K", "8K",
    "SSD", "DDR4", "DDR5", "RGB", "USB", "HDMI", "RAM", "CPU", "GPU", "FPS", "LED", "LCD",
    "WIFI", "NFC", "GPS", "PDF", "PNG", "JPG", "MP4", "MP3", "CPF", "CNPJ", "CEP", "CNH",
    "BRL", "USD", "PIX", "BOLETO", "AM", "PM", "MIN", "HORA", "HORAS", "DIAS", "MESES",
    "GANHE", "GANHA", "COMPRE", "CONFIRA", "ACESSE", "APENAS", "AGORA", "HOJE", "CORRA",
    "CORRE", "LINK", "CLIQUE", "SOMENTE", "VALIDO", "VALIDA", "APLICADO", "APLICADA",
    "AUTOMATICO", "TODOS", "TODAS", "SUPER", "MEGA", "BLACK", "FRIDAY", "GRATIS", "FRETE",
    "DESCONTO", "CUPOM", "CUPONS", "CODIGO", "OFERTA", "OFERTAS", "PROMO", "PRECO",
}

_COUPON_WORDS = ["cupom", "cupons", "codigo", "code", "promocode", "voucher", "vale"]
_DIRECT = ["nao precisa de cupom", "sem cupom", "desconto automatico", "desconto direto"]
_HOT = ["hot", "imperdivel", "corre", "corram", "so hoje", "ultimas", "queimando"]
_FLASH = ["relampago", "flash sale", "oferta relampago", "so agora", "hora do consumidor"]
_NEW_USER = ["novos usuarios", "novo usuario", "primeira compra", "conta nova", "so novos"]
_FREE_SHIPPING = ["frete gratis", "envio gratis", "sem frete", "frete free"]
_CASHBACK = ["cashback", "dinheiro de volta", "receba de volta", "volta r$"]
_LINKS = ["http", "bit.ly", "tidd.ly", "shp.ee", "amzn.to", "/sec/", "mercadolivre"]


@dataclass(slots=True)
class Coupon:
    code: str
    confidence: float
    store: str | None = None
    source: str = ""


@dataclass(slots=True)
class Detection:
    has_offer: bool = False
    confidence: float = 0.0
    store: str | None = None
    coupons: list[Coupon] = field(default_factory=list)
    discount_percent: int | None = None
    is_hot: bool = False
    is_free_shipping: bool = False
    is_cashback: bool = False
    is_new_user: bool = False
    is_flash: bool = False
    requires_coupon: bool = False

    @property
    def has_coupon(self) -> bool:
        return any(c.confidence > 0.5 for c in self.coupons)

    @property
    def best_coupon(self) -> Coupon | None:
        return max(self.coupons, key=lambda c: c.confidence, default=None)

    @property
    def store_label(self) -> str | None:
        if not self.store:
            return None
        return STORES[self.store]["label"][0]

    @property
    def label(self) -> str:
        if self.has_coupon or self.requires_coupon:
            return "Cupom"
        if self.is_free_shipping:
            return "Frete grátis"
        if self.is_cashback:
            return "Cashback"
        if self.is_flash:
            return "Relâmpago"
        if self.is_new_user:
            return "Novo usuário"
        if self.discount_percent:
            return f"{self.discount_percent}% OFF"
        return "Oferta"


class CouponDetector:
    def __init__(self) -> None:
        self._patterns = [(re.compile(p, re.IGNORECASE | re.MULTILINE), kind, weight) for p, kind, weight in _COUPON_PATTERNS]

    def detect(self, text: str) -> Detection:
        if not text or len(text.strip()) < 4:
            return Detection()
        norm = normalize(text)
        store = self._detect_store(text, norm)
        coupons = self._extract(text, store)
        discount = self._discount(text)
        has_word = self._has(norm, _COUPON_WORDS)
        is_direct = self._has(norm, _DIRECT)
        detection = Detection(
            store=store,
            coupons=coupons,
            discount_percent=discount,
            is_hot=self._has(norm, _HOT) or "🔥" in text,
            is_free_shipping=self._has(norm, _FREE_SHIPPING),
            is_cashback=self._has(norm, _CASHBACK),
            is_new_user=self._has(norm, _NEW_USER),
            is_flash=self._has(norm, _FLASH),
            requires_coupon=has_word and not is_direct and bool(coupons),
        )
        detection.confidence = self._confidence(detection, has_word, discount, text)
        detection.has_offer = detection.confidence > 0.25
        return detection

    @staticmethod
    def _has(norm: str, terms: list[str]) -> bool:
        return any(term in norm for term in terms)

    def _detect_store(self, text: str, norm: str) -> str | None:
        upper = text.upper()
        best: str | None = None
        best_score = 0
        for store_id, data in STORES.items():
            score = sum(2 for name in data["names"] if name in norm)
            score += sum(4 for domain in data["domains"] if domain in norm)
            score += sum(3 for prefix in data["prefixes"] if prefix in upper)
            if score > best_score:
                best, best_score = store_id, score
        return best

    def _extract(self, text: str, store: str | None) -> list[Coupon]:
        found: list[Coupon] = []
        seen: set[str] = set()
        for pattern, kind, weight in self._patterns:
            for match in pattern.findall(text):
                code = "".join(match if isinstance(match, tuple) else (match,)).strip(":-[](){}<> ")
                code_upper = code.upper()
                if not (3 <= len(code) <= 25) or code_upper in seen:
                    continue
                if self._is_false_positive(code_upper) or not self._plausible(code):
                    continue
                seen.add(code_upper)
                found.append(Coupon(
                    code=code_upper,
                    confidence=self._coupon_confidence(code_upper, weight, store),
                    store=store,
                    source=kind,
                ))
        found.sort(key=lambda c: c.confidence, reverse=True)
        return found

    def _is_false_positive(self, code: str) -> bool:
        if code in _FALSE_POSITIVES or code.isdigit():
            return True
        lower = code.lower()
        for data in STORES.values():
            if lower in [n.replace(" ", "") for n in data["names"]]:
                return True
        return "." in code and any(tld in lower for tld in ("com", ".br", "http"))

    @staticmethod
    def _plausible(code: str) -> bool:
        return any(ch.isdigit() for ch in code) or (code.isupper() and len(code) >= 4)

    def _coupon_confidence(self, code: str, weight: float, store: str | None) -> float:
        confidence = 0.5 + weight
        if code.isupper():
            confidence += 0.1
        if any(c.isdigit() for c in code):
            confidence += 0.1
        if store and any(code.startswith(p) for p in STORES[store]["prefixes"]):
            confidence += 0.15
        return min(confidence, 1.0)

    @staticmethod
    def _discount(text: str) -> int | None:
        values = [int(m) for m in re.findall(r"(\d{1,3})\s*%", text) if m.isdigit() and int(m) <= 99]
        return max(values) if values else None

    def _confidence(self, det: Detection, has_word: bool, discount: int | None, text: str) -> float:
        score = 0.0
        if det.coupons:
            score += max(c.confidence for c in det.coupons) * 0.4
        if has_word:
            score += 0.2
        if discount:
            score += 0.15
        if det.store:
            score += 0.1
        if det.is_free_shipping or det.is_cashback:
            score += 0.1
        if det.is_hot:
            score += 0.05
        if self._has(text.lower(), _LINKS):
            score += 0.05
        return min(score, 1.0)


def matches_smart_rules(det: Detection, rules: list[str]) -> bool:
    return any(_rule_matches(det, rule.strip().lower()) for rule in rules if rule.strip())


def _rule_matches(det: Detection, rule: str) -> bool:
    if rule in ("any_coupon", "coupon", "cupom"):
        return det.has_coupon or det.requires_coupon
    if rule in ("free_shipping", "frete"):
        return det.is_free_shipping
    if rule == "cashback":
        return det.is_cashback
    if rule in ("new_user", "novo"):
        return det.is_new_user
    if rule in ("flash", "relampago"):
        return det.is_flash
    if rule in ("hot", "quente"):
        return det.is_hot
    if rule.startswith("store:"):
        return det.store == rule.split(":", 1)[1]
    if rule.startswith("discount>="):
        try:
            threshold = int(rule.split(">=", 1)[1])
        except ValueError:
            return False
        return det.discount_percent is not None and det.discount_percent >= threshold
    return False


_detector = CouponDetector()


def detect(text: str) -> Detection:
    return _detector.detect(text)
