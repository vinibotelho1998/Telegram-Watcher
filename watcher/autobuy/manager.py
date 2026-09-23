from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from ..detector import detect
from ..models import LogLevel
from . import journal
from .models import SUCCESS_STATUSES, BuyResult, BuyStatus, ProductInfo, WatchItem, status_label
from .resolver import extract_urls, find_kabum_product, product_id
from .storage import AutobuySettings, AutobuyStore

_LOGGED_USER = re.compile(r"ol[aá],\s*([^\n]+)", re.IGNORECASE)

LogCb = Callable[[str, LogLevel], None]
ResultCb = Callable[[BuyResult], Awaitable[None]]
AlertCb = Callable[[str], None]

_REBUY_COOLDOWN = 1800.0
_RETRY_COOLDOWN = 120.0
_MIN_COUPON_CONFIDENCE = 0.5
_WATCHDOG_RETRY = 900.0
_PENDING_LIMIT = 2
_PENDING_MAX_AGE = 120.0


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
        self._pending: list[tuple[float, str]] = []
        self.accounts: dict[str, str | None] = {}
        self.checked: set[str] = set()
        self.check_failed: set[str] = set()
        self.busy_profile = False
        self.login_open = False
        self._watchdog: asyncio.Task | None = None
        self._last_alert: dict[str, float] = {}
        self._profile_lock = asyncio.Lock()

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

    def find_items(self, text: str) -> list[WatchItem]:
        if not self.settings.enabled or not text:
            return []
        return [item for item in self.items if item.matches(text)]

    def find_item(self, text: str) -> WatchItem | None:
        items = self.find_items(text)
        return items[0] if items else None

    def handle_text(self, text: str) -> bool:
        items = self.find_items(text)
        if not items:
            return False
        nomes = ", ".join(item.name for item in items)
        if not extract_urls(text):
            self._log(f"Promo de {nomes} sem link, ignorada", LogLevel.WARNING)
            return False
        if self._busy:
            if len(self._pending) >= _PENDING_LIMIT:
                self._log(f"Robô ocupado e fila cheia, promo de {nomes} ignorada", LogLevel.WARNING)
                return False
            self._pending.append((time.monotonic(), text))
            self._log(f"Robô ocupado: promo de {nomes} guardada, será tentada assim que ele terminar", LogLevel.WARNING)
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._busy = True
        loop.create_task(self._buy_flow(text, items))
        return True

    def _release(self) -> None:
        self._busy = False
        while self._pending and not self._busy:
            quando, text = self._pending.pop(0)
            if time.monotonic() - quando > _PENDING_MAX_AGE:
                self._log("Promo guardada passou de 2 minutos na fila e foi descartada", LogLevel.WARNING)
                continue
            self.handle_text(text)

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
        if presente:
            self._remember_store(spec.id)
        return presente

    def _remember_store(self, store_id: str) -> None:
        if store_id not in self.settings.known_stores:
            self.settings.known_stores.append(store_id)
            self.save()

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
            self.check_failed.add(spec.id)
            return None
        try:
            async with self._profile_lock, async_playwright() as playwright:
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

            self.check_failed.add(spec.id)
            if isinstance(exc, ProfileInUse):
                self._log(str(exc), LogLevel.WARNING)
                self.busy_profile = True
                return None
            self._log(f"Falha ao verificar conexão com a {spec.label}: {exc}", LogLevel.ERROR)
            return None
        self.busy_profile = False
        self.check_failed.discard(spec.id)
        user = spec.logged_user(raw or "")
        self.accounts[spec.id] = user
        self.checked.add(spec.id)
        if user:
            self._remember_store(spec.id)
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
        self.login_open = True
        try:
            async with self._profile_lock, async_playwright() as playwright:
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
            self.login_open = False
            self._release()
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
        last = self._last_alert.get(key)
        if last is not None and now - last < _WATCHDOG_RETRY:
            return
        self._last_alert[key] = now
        self._on_alert(message)

    async def _watchdog_loop(self) -> None:
        interval = max(1, self.settings.check_hours) * 3600.0
        try:
            await asyncio.sleep(60.0)
            while True:
                de_novo_logo = await self._watchdog_tick()
                await asyncio.sleep(_WATCHDOG_RETRY if self._busy or de_novo_logo else interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"Vigia de sessão parou: {exc}", LogLevel.WARNING)

    async def _watchdog_tick(self) -> bool:
        from . import registry

        if not self.settings.enabled or self._busy:
            return False
        de_novo_logo = False
        for spec in registry.all_stores():
            if not self._store_in_use(spec.id):
                continue
            if spec.requires_window:
                presente = self.quick_check(spec.id)
                verificou = presente is not None
                user = self.accounts.get(spec.id) if presente else None
            else:
                user = await self.check_login(spec.id)
                verificou = spec.id not in self.check_failed
            if not verificou:
                self._log(
                    f"Não deu para verificar a sessão da {spec.label} agora, nova tentativa em 15 minutos",
                    LogLevel.WARNING,
                )
                de_novo_logo = True
                continue
            if user:
                self._log(f"Sessão da {spec.label} ativa ({user})", LogLevel.INFO)
                continue
            message = f"Sua conta da {spec.label} caiu! Reconecte na aba Auto-Compra antes da próxima promoção."
            self._log(message, LogLevel.ERROR)
            self._alert(spec.id, message)
        return de_novo_logo

    def _store_in_use(self, store_id: str) -> bool:
        ativos = [item for item in self.items if item.enabled]
        if any(item.store == store_id for item in ativos):
            return True
        if not any(not item.store for item in ativos):
            return False
        return store_id in self.settings.known_stores or bool(self.accounts.get(store_id))

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
            async with self._profile_lock, async_playwright() as playwright:
                headless = self._hidden(spec, True)
                context = await self._context_for(playwright, spec, headless)
                try:
                    driver = spec.driver(
                        context,
                        on_log=lambda message: self._log(message, LogLevel.INFO),
                        on_captcha=self._on_captcha,
                        headless=headless,
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
            self._release()

    @staticmethod
    def _hidden(spec, preferido: bool) -> bool:
        return spec.headless_allowed(preferido) and not getattr(spec, "needs_real_chrome", False)

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
            async with self._profile_lock, async_playwright() as playwright:
                escondido = self._hidden(spec, headless)
                context = await self._context_for(playwright, spec, escondido)
                try:
                    driver = spec.driver(
                        context,
                        on_log=lambda message: self._log(message, LogLevel.INFO),
                        on_captcha=self._on_captcha,
                        headless=escondido,
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
            self._release()
        level = LogLevel.SUCCESS if result.status in SUCCESS_STATUSES else LogLevel.WARNING
        self._log(f"Teste terminou: {status_label(result.status)}. {result.detail or ''}".strip(), level)
        return result

    async def _buy_flow(self, text: str, items: list[WatchItem]) -> None:
        try:
            result = await self._execute(text, items)
            if result is not None and self._on_result is not None:
                await self._on_result(result)
        except Exception as exc:
            self._log(f"Auto-compra falhou: {exc}", LogLevel.ERROR)
        finally:
            self._release()

    @staticmethod
    def _pick(items: list[WatchItem], links: list) -> tuple[WatchItem, object, str] | None:
        for item in items:
            for spec, url in links:
                if not item.store or item.store == spec.id:
                    return item, spec, url
        return None

    async def _execute(self, text: str, items: list[WatchItem]) -> BuyResult | None:
        from . import registry

        nomes = ", ".join(f"'{item.name}'" for item in items)
        self._log(f"Promo casou com {nomes}, procurando link de loja conhecida", LogLevel.CAPTURE)
        links = registry.direct_products(text)
        escolha = self._pick(items, links)
        if escolha is None:

            def aceita(spec) -> bool:
                return any(not item.store or item.store == spec.id for item in items)

            links = links + await asyncio.to_thread(registry.resolved_products, text, aceita)
            escolha = self._pick(items, links)
        if escolha is None:
            if not links:
                lojas = ", ".join(registry.labels().values())
                self._log(f"Nenhum link de produto nas lojas suportadas ({lojas})", LogLevel.WARNING)
            else:
                lojas_link = ", ".join(dict.fromkeys(spec.label for spec, _url in links))
                lojas_item = ", ".join(
                    dict.fromkeys(registry.get(item.store).label for item in items if registry.get(item.store))
                )
                self._log(
                    f"Promo casou com {nomes}, mas o link é da {lojas_link} e o produto só compra na {lojas_item}",
                    LogLevel.WARNING,
                )
            return None
        item, spec, url = escolha
        self._log(f"Loja identificada: {spec.label} (produto '{item.name}')", LogLevel.INFO)
        key = f"{spec.id}:{url}"
        last = self._recent.get(key)
        if last is not None and time.monotonic() - last < _REBUY_COOLDOWN:
            self._log("Produto já tratado há pouco, ignorando repetição", LogLevel.INFO)
            return None
        self._recent[key] = time.monotonic()
        result = None
        try:
            result = await self._attempt(text, item, spec, url)
        finally:
            if result is None or result.status not in SUCCESS_STATUSES:
                self._recent[key] = time.monotonic() - (_REBUY_COOLDOWN - _RETRY_COOLDOWN)
        return result

    async def _attempt(self, text: str, item: WatchItem, spec, url: str) -> BuyResult | None:
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
        async with self._profile_lock, async_playwright() as playwright:
            escondido = self._hidden(spec, self.settings.headless)
            context = await self._context_for(playwright, spec, escondido)
            try:
                driver = spec.driver(
                    context,
                    on_log=lambda message: self._log(message, LogLevel.INFO),
                    on_captcha=self._on_captcha,
                    headless=escondido,
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
        elif result.status == BuyStatus.DRY_RUN_OK:
            self._log(f"Simulação concluída para {item.name}: {result.detail}", LogLevel.SUCCESS)
        else:
            self._log(f"Compra NÃO feita ({status_label(result.status)}): {result.detail}", LogLevel.WARNING)
        return result
