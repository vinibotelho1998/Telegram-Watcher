from __future__ import annotations

from playwright.async_api import BrowserContext, Playwright

from .. import paths

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


async def open_context(playwright: Playwright, headless: bool = True) -> BrowserContext:
    return await playwright.chromium.launch_persistent_context(
        str(paths.browser_profile_dir()),
        headless=headless,
        args=_LAUNCH_ARGS,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1366, "height": 850},
    )
