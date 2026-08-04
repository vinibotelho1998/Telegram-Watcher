from __future__ import annotations

import re

_KABUM = re.compile(r"kabum\.com\.br/produto/(\d+)", re.IGNORECASE)
_AMAZON = re.compile(r"amazon\.com(?:\.br)?/(?:.*/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
_TERABYTE = re.compile(r"terabyteshop\.com\.br/produto/(\d+)", re.IGNORECASE)


def kabum_id(url: str) -> int | None:
    match = _KABUM.search(url or "")
    return int(match.group(1)) if match else None


def kabum_owns(url: str) -> bool:
    return kabum_id(url) is not None


def kabum_canonical(url: str) -> str:
    return url


def amazon_id(url: str) -> str | None:
    match = _AMAZON.search(url or "")
    return match.group(1).upper() if match else None


def amazon_owns(url: str) -> bool:
    return amazon_id(url) is not None


def amazon_canonical(url: str) -> str:
    asin = amazon_id(url)
    return f"https://www.amazon.com.br/dp/{asin}" if asin else url


_PICHAU_HOST = re.compile(r"^(?:https?://)?(?:www\.)?pichau\.com\.br(/.*)?$", re.IGNORECASE)
_PICHAU_SECOES = {
    "hardware", "computadores", "perifericos", "games", "notebooks", "monitores",
    "promocoes", "categoria", "conta", "carrinho", "checkout", "login", "busca",
    "sale", "pichau-empresas", "cart", "customer",
}


def pichau_slug(url: str) -> str | None:
    match = _PICHAU_HOST.match((url or "").strip())
    if match is None:
        return None
    caminho = (match.group(1) or "/").split("?", 1)[0].split("#", 1)[0]
    partes = [p for p in caminho.split("/") if p]
    if len(partes) != 1:
        return None
    slug = partes[0].lower()
    if slug in _PICHAU_SECOES or "-" not in slug or len(slug) < 12:
        return None
    return slug


def pichau_owns(url: str) -> bool:
    return pichau_slug(url) is not None


def pichau_canonical(url: str) -> str:
    slug = pichau_slug(url)
    return f"https://www.pichau.com.br/{slug}" if slug else url


def terabyte_id(url: str) -> int | None:
    match = _TERABYTE.search(url or "")
    return int(match.group(1)) if match else None


def terabyte_owns(url: str) -> bool:
    return terabyte_id(url) is not None


def terabyte_canonical(url: str) -> str:
    return url
