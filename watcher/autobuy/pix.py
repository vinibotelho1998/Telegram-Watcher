from __future__ import annotations

import asyncio
import re
import time

from playwright.async_api import Page

from .dom import find_clickable, page_text

_PIX_CODE = re.compile(r"000201.{20,600}?6304[0-9A-Za-z]{4}", re.DOTALL)
_PIX_MARKER = "bcb.pix"
_CRC_FIELD = re.compile(r"(?=6304([0-9A-Fa-f]{4}))")
_MAX_PIX_LENGTH = 640
_COPY_PATTERNS = (
    r"copiar\s+c[oó]digo\s+pix",
    r"copiar\s+c[oó]digo",
    r"copiar\s+e\s+colar",
    r"c[oó]digo\s+copia\s+e\s+cola",
)

WAIT_SECONDS = 90.0


def _crc16(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _with_valid_crc(raw: str, start: int) -> str:
    window = raw[start : start + _MAX_PIX_LENGTH * 4]
    for tail in (re.sub(r"[\r\n]+", "", window), re.sub(r"\s+", "", window)):
        for end in _CRC_FIELD.finditer(tail, 0, _MAX_PIX_LENGTH):
            candidate = tail[: end.start() + 8]
            if _crc16(candidate[:-4]) == end.group(1).upper():
                return candidate
    return ""


def clean_pix(raw: str) -> str:
    if not raw:
        return ""
    match = _PIX_CODE.search(raw)
    if not match:
        return ""
    code = _with_valid_crc(raw, match.start()) or re.sub(r"[\r\n\s]+", "", match.group(0)).strip()
    return code if _PIX_MARKER in code.lower() else ""


async def from_copy_button(page: Page) -> str:
    button = await find_clickable(page, _COPY_PATTERNS)
    if button is None:
        return ""
    try:
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await button.click(timeout=5000)
        await asyncio.sleep(0.6)
        value = await page.evaluate("() => navigator.clipboard.readText()")
    except Exception:
        return ""
    return clean_pix(value or "")


async def from_fields(page: Page) -> str:
    try:
        values = await page.eval_on_selector_all("input, textarea", "els => els.map(e => e.value || '')")
    except Exception:
        return ""
    for value in values:
        code = clean_pix(value)
        if code:
            return code
    return ""


async def extract(page: Page) -> str:
    code = await from_copy_button(page)
    if code:
        return code
    code = await from_fields(page)
    if code:
        return code
    return clean_pix(await page_text(page))


async def wait_for(page: Page, timeout: float = WAIT_SECONDS) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = await extract(page)
        if code:
            return code
        await asyncio.sleep(2.0)
    return ""
