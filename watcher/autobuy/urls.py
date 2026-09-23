from __future__ import annotations

import re

_HOST = r"^https?://(?:[a-z0-9-]+\.)*"
_KABUM = re.compile(_HOST + r"kabum\.com\.br(?::\d+)?/produto/(\d+)", re.IGNORECASE)
_AMAZON = re.compile(
    _HOST + r"amazon\.com(?:\.br)?(?::\d+)?/(?:[^?#]*/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE
)
_TERABYTE = re.compile(_HOST + r"terabyteshop\.com\.br(?::\d+)?/produto/(\d+)", re.IGNORECASE)


def kabum_id(url: str) -> int | None:
    match = _KABUM.search((url or "").strip())
    return int(match.group(1)) if match else None


def kabum_owns(url: str) -> bool:
    return kabum_id(url) is not None


def kabum_canonical(url: str) -> str:
    return url


def amazon_id(url: str) -> str | None:
    match = _AMAZON.search((url or "").strip())
    return match.group(1).upper() if match else None


def amazon_owns(url: str) -> bool:
    return amazon_id(url) is not None


def amazon_canonical(url: str) -> str:
    asin = amazon_id(url)
    return f"https://www.amazon.com.br/dp/{asin}" if asin else url


def terabyte_id(url: str) -> int | None:
    match = _TERABYTE.search((url or "").strip())
    return int(match.group(1)) if match else None


def terabyte_owns(url: str) -> bool:
    return terabyte_id(url) is not None


def terabyte_canonical(url: str) -> str:
    return url
