from __future__ import annotations

import asyncio
import re
import time

from playwright.async_api import Locator, Page

_READY_SECONDS = 20.0


async def page_text(page: Page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


async def find_button(page: Page, patterns: tuple[str, ...]) -> Locator | None:
    for raw in patterns:
        pattern = re.compile(raw, re.IGNORECASE)
        locator = page.get_by_role("button", name=pattern)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible() and await candidate.is_enabled():
                    return candidate
            except Exception:
                continue
    return None


async def find_clickable(page: Page, patterns: tuple[str, ...]) -> Locator | None:
    button = await find_button(page, patterns)
    if button is not None:
        return button
    for raw in patterns:
        pattern = re.compile(raw, re.IGNORECASE)
        for role in ("link", "radio"):
            locator = page.get_by_role(role, name=pattern)
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        locator = page.get_by_text(pattern).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def click_first(page: Page, selectors: tuple[str, ...], timeout: float = 8000) -> bool:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if not await candidate.is_visible() or not await candidate.is_enabled():
                    continue
                await candidate.click(timeout=timeout)
                return True
            except Exception:
                continue
    return False


async def wait_page_ready(page: Page, marker: str = "carregando", timeout: float = _READY_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = await page_text(page)
        if marker not in text.lower():
            return
        await asyncio.sleep(1.0)
