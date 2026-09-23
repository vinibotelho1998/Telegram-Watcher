from __future__ import annotations

import re
import urllib.request

from .urls import kabum_id

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def extract_urls(text: str) -> list[str]:
    return _URL.findall(text or "")


def product_id(url: str) -> int | None:
    return kabum_id(url)


def resolve(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.url
    except Exception:
        return url


def find_kabum_product(text: str) -> str | None:
    for url in extract_urls(text):
        if product_id(url):
            return url
    for url in extract_urls(text):
        resolved = resolve(url)
        if product_id(resolved):
            return resolved
    return None
