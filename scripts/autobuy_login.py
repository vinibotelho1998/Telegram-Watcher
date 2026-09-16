from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from watcher import paths
from watcher.autobuy.browser import open_context, save_session_cookies

LOGIN_URLS = {
    "kabum": "https://www.kabum.com.br/login",
    "terabyte": "https://www.terabyteshop.com.br/Identificacao.obj",
    "amazon": "https://www.amazon.com.br/ap/signin?openid.mode=checkid_setup"
    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.assoc_handle=brflex&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
    "&openid.return_to=https%3A%2F%2Fwww.amazon.com.br%2F",
}


async def main() -> None:
    store = (sys.argv[1] if len(sys.argv) > 1 else "kabum").strip().lower()
    url = LOGIN_URLS.get(store)
    if url is None:
        print(f"Loja desconhecida: {store}")
        print(f"Disponíveis: {', '.join(sorted(LOGIN_URLS))}")
        return
    async with async_playwright() as playwright:
        context = await open_context(playwright, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url)
        print(f"Perfil do robô: {paths.browser_profile_dir()}")
        print(f"Faça login na loja {store} na janela aberta.")
        print("A sessão fica salva no perfil acima. Feche o navegador quando terminar.")
        closed = asyncio.Event()
        context.on("close", lambda: closed.set())
        salvos = 0
        while not closed.is_set():
            try:
                await asyncio.wait_for(closed.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            if closed.is_set():
                break
            salvos = await save_session_cookies(context)
        print(f"Navegador fechado. Cookies de sessão guardados: {salvos}")


if __name__ == "__main__":
    asyncio.run(main())
