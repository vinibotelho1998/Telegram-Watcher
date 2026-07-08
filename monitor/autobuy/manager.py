from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from ..detector import detect
from ..models import LogLevel
from .models import BuyResult, BuyStatus, WatchItem
from .resolver import extract_urls, find_kabum_product, product_id
from .storage import AutobuySettings, AutobuyStore

_LOGGED_USER = re.compile(r"ol[aá],\s*([^\n]+)", re.IGNORECASE)

LogCb = Callable[[str, LogLevel], None]
ResultCb = Callable[[BuyResult], Awaitable[None]]

_REBUY_COOLDOWN = 1800.0
_MIN_COUPON_CONFIDENCE = 0.5


def _noop_log(_message: str, _level: LogLevel) -> None:
    return None


class AutobuyManager:
    def __init__(
        self,
        store: AutobuyStore | None = None,
        *,
        on_log: LogCb | None = None,
        on_result: ResultCb | None = None,
        headless: bool = True,
    ) -> None:
        self._store = store or AutobuyStore()
        self._log = on_log or _noop_log
        self._on_result = on_result
        self._headless = headless
        self.settings, self.items = self._store.load()
        self._busy = False
        self._recent: dict[int, float] = {}
        self.kabum_user: str | None = None
        self.kabum_checked = False

    def save(self) -> bool:
        return self._store.save(self.settings, self.items)

    def set_enabled(self, enabled: bool) -> None:
        self.settings.enabled = enabled
        self.save()

    def set_real_mode(self, real: bool) -> None:
        self.settings.real_mode = real
        self.save()

    def set_use_coupon(self, use: bool) -> None:
        self.settings.use_coupon = use
        self.save()

    def upsert_item(self, item: WatchItem) -> None:
        if any(existing.id == item.id for existing in self.items):
            self.items = [item if existing.id == item.id else existing for existing in self.items]
        else:
            self.items.append(item)
        self.save()

    def remove_item(self, item_id: str) -> None:
        self.items = [item for item in self.items if item.id != item_id]
        self.save()

    def toggle_item(self, item_id: str, enabled: bool) -> None:
        for item in self.items:
            if item.id == item_id:
                item.enabled = enabled
        self.save()

    def find_item(self, text: str) -> WatchItem | None:
        if not self.settings.enabled or not text:
            return None
        for item in self.items:
            if item.matches(text):
                return item
        return None

    def handle_text(self, text: str) -> bool:
        item = self.find_item(text)
        if item is None:
            return False
        if self._busy:
            self._log(f"Auto-compra ocupada, ignorando promo de {item.name}", LogLevel.WARNING)
            return False
        if not extract_urls(text):
            self._log(f"Promo de {item.name} sem link, ignorada", LogLevel.WARNING)
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._busy = True
        loop.create_task(self._buy_flow(text, item))
        return True

    def pick_coupon(self, text: str) -> str:
        if not self.settings.use_coupon:
            return ""
        detection = detect(text)
        coupon = detection.best_coupon
        if coupon is not None and coupon.confidence >= _MIN_COUPON_CONFIDENCE:
            return coupon.code
        return ""

    async def check_login(self) -> str | None:
        try:
            from playwright.async_api import async_playwright

            from .browser import open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        try:
            async with async_playwright() as playwright:
                context = await open_context(playwright, headless=True)
                try:
                    page = await context.new_page()
                    await page.goto("https://www.kabum.com.br", wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                finally:
                    await context.close()
        except Exception as exc:
            self._log(f"Falha ao verificar conexão com a KaBuM: {exc}", LogLevel.ERROR)
            return None
        match = _LOGGED_USER.search(text or "")
        self.kabum_user = match.group(1).strip() if match else None
        self.kabum_checked = True
        return self.kabum_user

    async def open_login(self) -> str | None:
        if self._busy:
            self._log("Aguarde a compra em andamento antes de conectar", LogLevel.WARNING)
            return self.kabum_user
        try:
            from playwright.async_api import async_playwright

            from .browser import open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        self._busy = True
        try:
            async with async_playwright() as playwright:
                context = await open_context(playwright, headless=False)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://www.kabum.com.br/login", wait_until="domcontentloaded", timeout=45000)
                self._log("Janela da KaBuM aberta: faça login e feche o navegador", LogLevel.INFO)
                closed = asyncio.Event()
                context.on("close", lambda: closed.set())
                await closed.wait()
        except Exception as exc:
            self._log(f"Falha na janela de login: {exc}", LogLevel.ERROR)
            return None
        finally:
            self._busy = False
        return await self.check_login()

    async def _buy_flow(self, text: str, item: WatchItem) -> None:
        try:
            result = await self._execute(text, item)
            if result is not None and self._on_result is not None:
                await self._on_result(result)
        except Exception as exc:
            self._log(f"Auto-compra falhou: {exc}", LogLevel.ERROR)
        finally:
            self._busy = False

    async def _execute(self, text: str, item: WatchItem) -> BuyResult | None:
        self._log(f"Promo casou com '{item.name}', buscando link da KaBuM", LogLevel.CAPTURE)
        url = await asyncio.to_thread(find_kabum_product, text)
        if not url:
            self._log("Nenhum link de produto KaBuM na mensagem", LogLevel.WARNING)
            return None
        pid = product_id(url) or 0
        last = self._recent.get(pid, 0.0)
        if time.monotonic() - last < _REBUY_COOLDOWN:
            self._log(f"Produto {pid} já tratado há pouco, ignorando repetição", LogLevel.INFO)
            return None
        self._recent[pid] = time.monotonic()
        coupon = self.pick_coupon(text)
        if coupon:
            self._log(f"Cupom detectado na mensagem: {coupon}", LogLevel.INFO)
        try:
            from playwright.async_api import async_playwright

            from .browser import open_context
            from .kabum import KabumDriver
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        mode = "compra real" if self.settings.real_mode else "simulação"
        self._log(f"Iniciando {mode}: {url}", LogLevel.INFO)
        async with async_playwright() as playwright:
            context = await open_context(playwright, headless=self._headless)
            try:
                driver = KabumDriver(context, on_log=lambda message: self._log(message, LogLevel.INFO))
                result = await driver.buy(url, item.max_price, confirm=self.settings.real_mode, coupon=coupon)
            finally:
                await context.close()
        if result.status == BuyStatus.PIX_READY:
            item.enabled = False
            self.save()
            self._log(f"PIX gerado para {item.name} em {result.elapsed:.1f}s; item desativado", LogLevel.SUCCESS)
        else:
            self._log(f"Auto-compra terminou com status {result.status.value}: {result.detail}", LogLevel.WARNING)
        return result
