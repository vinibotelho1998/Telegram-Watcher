from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from monitor import paths
from monitor.autobuy.browser import open_context


async def main() -> None:
    async with async_playwright() as playwright:
        context = await open_context(playwright, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.kabum.com.br/login")
        print(f"Perfil do robô: {paths.browser_profile_dir()}")
        print("Faça login na KaBuM na janela aberta.")
        print("A sessão fica salva no perfil acima. Feche o navegador quando terminar.")
        closed = asyncio.Event()
        context.on("close", lambda: closed.set())
        await closed.wait()


if __name__ == "__main__":
    asyncio.run(main())
