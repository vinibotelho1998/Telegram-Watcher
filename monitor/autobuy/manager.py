from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from ..detector import detect
from ..models import LogLevel
from . import journal
from .models import BuyResult, BuyStatus, ProductInfo, WatchItem
from .resolver import extract_urls, find_kabum_product, product_id
from .storage import AutobuySettings, AutobuyStore

_LOGGED_USER = re.compile(r"ol[aá],\s*([^\n]+)", re.IGNORECASE)

LogCb = Callable[[str, LogLevel], None]
ResultCb = Callable[[BuyResult], Awaitable[None]]
AlertCb = Callable[[str], None]

_REBUY_COOLDOWN = 1800.0
_MIN_COUPON_CONFIDENCE = 0.5
_WATCHDOG_RETRY = 900.0


def _noop_log(_message: str, _level: LogLevel) -> None:
    return None


class AutobuyManager:
    def __init__(
        self,
        store: AutobuyStore | None = None,
        *,
        on_log: LogCb | None = None,
        on_result: ResultCb | None = None,
        on_captcha: Callable[[], None] | None = None,
        on_alert: AlertCb | None = None,
    ) -> None:
        self._store = store or AutobuyStore()
        self._on_log_ui = on_log or _noop_log
        self._store_tag = ""
        self._on_result = on_result
        self._on_captcha = on_captcha
        self._on_alert = on_alert
        self.settings, self.items = self._store.load()
        self._busy = False
        self._recent: dict[str, float] = {}
        self.accounts: dict[str, str | None] = {}
        self.checked: set[str] = set()
        self.busy_profile = False
        self._watchdog: asyncio.Task | None = None
        self._last_alert: dict[str, float] = {}

    def _log(self, message: str, level: LogLevel = LogLevel.INFO) -> None:
        journal.write(message, level.value, self._store_tag)
        self._on_log_ui(message, level)

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

    def set_headless(self, headless: bool) -> None:
        self.settings.headless = headless
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

    async def _context_for(self, playwright, spec, headless: bool):
        from .browser import open_context, open_real_chrome

        if getattr(spec, "needs_real_chrome", False):
            self._log(f"{spec.label} exige o Chrome do sistema, abrindo janela real", LogLevel.INFO)
            return await open_real_chrome(playwright, spec.home_url)
        return await open_context(playwright, headless=headless)

    def account(self, store_id: str) -> str | None:
        return self.accounts.get(store_id)

    def was_checked(self, store_id: str) -> bool:
        return store_id in self.checked

    def quick_check(self, store_id: str) -> bool | None:
        from . import registry
        from .browser import has_session_cookies

        spec = registry.get(store_id)
        if spec is None:
            return None
        presente = has_session_cookies(spec.cookie_domain, spec.session_cookies)
        if presente is None:
            return None
        self.accounts.setdefault(spec.id, "conta salva" if presente else None)
        if presente and not self.accounts.get(spec.id):
            self.accounts[spec.id] = "conta salva"
        if not presente:
            self.accounts[spec.id] = None
        self.checked.add(spec.id)
        return presente

    async def check_login(self, store_id: str = "kabum", deep: bool = True) -> str | None:
        from . import registry

        spec = registry.get(store_id)
        if spec is None:
            return None
        if not deep:
            rapido = self.quick_check(store_id)
            if rapido is not None:
                return self.accounts.get(spec.id)
        try:
            from playwright.async_api import async_playwright

            from .browser import close_context, open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        try:
            async with async_playwright() as playwright:
                context = await self._context_for(playwright, spec, spec.headless_allowed(True))
                try:
                    page = await context.new_page()
                    await page.goto(spec.home_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                    raw = await page.evaluate(spec.user_script)
                finally:
                    await close_context(context)
        except Exception as exc:
            from .browser import ProfileInUse

            if isinstance(exc, ProfileInUse):
                self._log(str(exc), LogLevel.WARNING)
                self.busy_profile = True
                return None
            self._log(f"Falha ao verificar conexão com a {spec.label}: {exc}", LogLevel.ERROR)
            return None
        self.busy_profile = False
        user = spec.logged_user(raw or "")
        self.accounts[spec.id] = user
        self.checked.add(spec.id)
        return user

    async def check_all(self) -> dict[str, str | None]:
        from . import registry

        for spec in registry.all_stores():
            await self.check_login(spec.id, deep=False)
        return dict(self.accounts)

    async def open_login(self, store_id: str = "kabum") -> str | None:
        from . import registry

        spec = registry.get(store_id)
        if spec is None:
            return None
        if self._busy:
            self._log("Aguarde a compra em andamento antes de conectar", LogLevel.WARNING)
            return self.accounts.get(spec.id)
        try:
            from playwright.async_api import async_playwright

            from .browser import open_context, save_session_cookies
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        self._busy = True
        try:
            async with async_playwright() as playwright:
                context = await open_context(playwright, headless=False)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(spec.login_url, wait_until="domcontentloaded", timeout=45000)
                self._log(f"Janela da {spec.label} aberta: faça login e feche o navegador", LogLevel.INFO)
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
                if salvos:
                    self._log(f"Sessão guardada ({salvos} cookies de sessão)", LogLevel.SUCCESS)
        except Exception as exc:
            self._log(f"Falha na janela de login: {exc}", LogLevel.ERROR)
            return None
        finally:
            self._busy = False
        return await self.check_login(spec.id)

    @property
    def busy(self) -> bool:
        return self._busy

    def start_watchdog(self) -> None:
        if self._watchdog is not None and not self._watchdog.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._watchdog = loop.create_task(self._watchdog_loop())

    def stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _alert(self, key: str, message: str) -> None:
        if self._on_alert is None:
            return
        now = time.monotonic()
        if now - self._last_alert.get(key, 0.0) < _WATCHDOG_RETRY:
            return
        self._last_alert[key] = now
        self._on_alert(message)

    async def _watchdog_loop(self) -> None:
        interval = max(1, self.settings.check_hours) * 3600.0
        try:
            await asyncio.sleep(60.0)
            while True:
                await self._watchdog_tick()
                await asyncio.sleep(interval if not self._busy else _WATCHDOG_RETRY)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"Vigia de sessão parou: {exc}", LogLevel.WARNING)

    async def _watchdog_tick(self) -> None:
        from . import registry

        if not self.settings.enabled or self._busy:
            return
        for spec in registry.all_stores():
            if not self._store_in_use(spec.id):
                continue
            user = await self.check_login(spec.id)
            if user:
                self._log(f"Sessão da {spec.label} ativa ({user})", LogLevel.INFO)
                continue
            message = f"Sua conta da {spec.label} caiu! Reconecte na aba Auto-Compra antes da próxima promoção."
            self._log(message, LogLevel.ERROR)
            self._alert(spec.id, message)

    def _store_in_use(self, store_id: str) -> bool:
        ativos = [item for item in self.items if item.enabled]
        if not ativos:
            return False
        return any(not item.store or item.store == store_id for item in ativos)

    async def probe(self, url: str) -> ProductInfo | None:
        from . import registry

        if self._busy:
            self._log("Aguarde a operação em andamento", LogLevel.WARNING)
            return None
        spec = registry.match_url(url)
        if spec is None:
            lojas = ", ".join(registry.labels().values())
            self._log(f"Link não reconhecido. Lojas suportadas: {lojas}", LogLevel.ERROR)
            return None
        try:
            from playwright.async_api import async_playwright

            from .browser import close_context, open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        self._busy = True
        try:
            async with async_playwright() as playwright:
                context = await self._context_for(playwright, spec, spec.headless_allowed(True))
                try:
                    driver = spec.driver(
                        context,
                        on_log=lambda message: self._log(message, LogLevel.INFO),
                        on_captcha=self._on_captcha,
                    )
                    page = await context.new_page()
                    try:
                        return await driver.fetch_product(page, spec.canonical(url))
                    finally:
                        await page.close()
                finally:
                    await close_context(context)
        except Exception as exc:
            self._log(f"Falha ao consultar o produto: {exc}", LogLevel.ERROR)
            return None
        finally:
            self._busy = False

    async def run_test(
        self,
        url: str,
        max_price: float,
        *,
        confirm: bool = False,
        coupon: str = "",
        min_price: float = 0.0,
        quantity: int = 1,
        headless: bool = False,
    ) -> BuyResult | None:
        from . import registry

        if self._busy:
            self._log("Aguarde a operação em andamento antes de testar", LogLevel.WARNING)
            return None
        spec = registry.match_url(url)
        if spec is None:
            lojas = ", ".join(registry.labels().values())
            self._log(f"Link não reconhecido. Lojas suportadas: {lojas}", LogLevel.ERROR)
            return None
        try:
            from playwright.async_api import async_playwright

            from .browser import close_context, open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        mode = "compra real" if confirm else "simulação"
        self._store_tag = spec.id
        journal.separator(f"TESTE {mode.upper()} na {spec.label}: {url[:60]}")
        self._log(f"Teste iniciado em {mode} na {spec.label}: {url}", LogLevel.CAPTURE)
        self._busy = True
        try:
            async with async_playwright() as playwright:
                context = await self._context_for(playwright, spec, spec.headless_allowed(headless))
                try:
                    driver = spec.driver(
                        context,
                        on_log=lambda message: self._log(message, LogLevel.INFO),
                        on_captcha=self._on_captcha,
                    )
                    result = await driver.buy(
                        spec.canonical(url),
                        max_price,
                        confirm=confirm,
                        coupon=coupon,
                        min_price=min_price,
                        quantity=quantity,
                    )
                finally:
                    await close_context(context)
        except Exception as exc:
            self._log(f"Teste falhou: {exc}", LogLevel.ERROR)
            return None
        finally:
            self._busy = False
        level = LogLevel.SUCCESS if result.status in (BuyStatus.PIX_READY, BuyStatus.DRY_RUN_OK) else LogLevel.WARNING
        self._log(f"Teste terminou como {result.status.value}: {result.detail or 'sem detalhes'}", level)
        return result

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
        from . import registry

        self._log(f"Promo casou com '{item.name}', procurando link de loja conhecida", LogLevel.CAPTURE)
        allowed = [item.store] if item.store else None
        found = await asyncio.to_thread(registry.find_product, text, allowed)
        if found is None:
            lojas = ", ".join(registry.labels().values())
            self._log(f"Nenhum link de produto nas lojas suportadas ({lojas})", LogLevel.WARNING)
            return None
        spec, url = found
        self._log(f"Loja identificada: {spec.label}", LogLevel.INFO)
        key = f"{spec.id}:{url}"
        last = self._recent.get(key, 0.0)
        if time.monotonic() - last < _REBUY_COOLDOWN:
            self._log("Produto já tratado há pouco, ignorando repetição", LogLevel.INFO)
            return None
        self._recent[key] = time.monotonic()
        coupon = self.pick_coupon(text)
        if coupon:
            self._log(f"Cupom detectado na mensagem: {coupon}", LogLevel.INFO)
        try:
            from playwright.async_api import async_playwright

            from .browser import close_context, open_context
        except ImportError:
            self._log("Playwright não instalado: pip install -r requirements-autobuy.txt", LogLevel.ERROR)
            return None
        mode = "compra real" if self.settings.real_mode else "simulação"
        self._store_tag = spec.id
        journal.separator(f"PROMO CAPTURADA -> {mode.upper()} na {spec.label}: {url[:60]}")
        self._log(f"Iniciando {mode} na {spec.label}: {url}", LogLevel.INFO)
        async with async_playwright() as playwright:
            context = await self._context_for(playwright, spec, spec.headless_allowed(self.settings.headless))
            try:
                driver = spec.driver(
                    context,
                    on_log=lambda message: self._log(message, LogLevel.INFO),
                    on_captcha=self._on_captcha,
                )
                result = await driver.buy(
                    url,
                    item.max_price,
                    confirm=self.settings.real_mode,
                    coupon=coupon,
                    min_price=item.min_price,
                    quantity=item.quantity,
                )
            finally:
                await close_context(context)
        if result.status == BuyStatus.PIX_READY:
            if item.disable_after_buy:
                item.enabled = False
                self.save()
            suffix = "; item desativado" if item.disable_after_buy else ""
            self._log(f"PIX gerado para {item.name} em {result.elapsed:.1f}s{suffix}", LogLevel.SUCCESS)
        else:
            self._log(f"Auto-compra terminou com status {result.status.value}: {result.detail}", LogLevel.WARNING)
        return result
