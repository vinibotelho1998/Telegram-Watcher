from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable

from playwright.async_api import BrowserContext, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .. import paths
from ..matching import normalize
from .models import BuyResult, BuyStatus, ProductInfo
from .records import save_record

LogCb = Callable[[str], None]

_PIX_CODE = re.compile(r"000201.{20,600}?6304[0-9A-Za-z]{4}", re.DOTALL)
_PIX_MARKER = "bcb.pix"


def _clean_pix(raw: str) -> str:
    if not raw:
        return ""
    match = _PIX_CODE.search(raw)
    if not match:
        return ""
    code = re.sub(r"[\r\n]+", "", match.group(0)).strip()
    return code if _PIX_MARKER in code.lower() else ""
_CONTINUE_TESTIDS = ("[data-testid='button-cart']", "[data-testid='button-continue']")
_CONTINUE_PATTERNS = (
    r"^\s*continuar\s*$",
    r"^\s*ir\s+para\s+o?\s*pagamento\s*$",
    r"^\s*avan[cç]ar\s*$",
)
_FINALIZE_PATTERNS = (
    r"^\s*finalizar(\s+(pedido|compra))?\s*$",
    r"^\s*concluir(\s+pedido)?\s*$",
    r"^\s*fazer\s+pedido\s*$",
)
_CONSENT_SELECTORS = ("#adopt-reject-all-button", "#adopt-accept-all-button")
_MAX_CHECKOUT_STEPS = 20
_MAX_STUCK_RETRIES = 3
_PIX_WAIT_SECONDS = 90.0
_PAGE_READY_SECONDS = 20.0


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "produto"


def _step_name(path: str) -> str:
    clean = path.split("?", 1)[0].strip("/")
    if not clean:
        return "pagina"
    if clean.startswith("precarrinho"):
        return "pre-carrinho"
    return clean.split("/", 1)[0]


class _Stopwatch:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self._last = self.started
        self.laps: dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.laps[name] = round(self.laps.get(name, 0.0) + (now - self._last), 1)
        self._last = now

    def total(self) -> float:
        return time.monotonic() - self.started


class KabumDriver:
    def __init__(self, context: BrowserContext, on_log: LogCb | None = None) -> None:
        self._context = context
        self._log = on_log or (lambda _message: None)
        self._coupon = ""
        self._coupon_note = ""

    async def buy(self, url: str, max_price: float, confirm: bool = False, coupon: str = "") -> BuyResult:
        started = time.monotonic()
        stopwatch = _Stopwatch()
        self._coupon = coupon.strip().upper()
        self._coupon_note = ""
        page = await self._context.new_page()
        try:
            result = await self._run(page, url, max_price, confirm, stopwatch)
        except PlaywrightTimeout as exc:
            result = BuyResult(status=BuyStatus.FAILED, detail=f"Tempo esgotado: {exc}")
        except Exception as exc:
            result = BuyResult(status=BuyStatus.FAILED, detail=str(exc))
        if not result.screenshot:
            result.screenshot = await self._screenshot(page, result.status.value)
        await page.close()
        if not result.elapsed:
            result.elapsed = round(time.monotonic() - started, 1)
        if not result.timings:
            result.timings = dict(stopwatch.laps)
        return result

    async def fetch_product(self, page: Page, url: str) -> ProductInfo | None:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        raw = await page.evaluate("() => document.getElementById('__NEXT_DATA__')?.textContent || ''")
        if not raw:
            return None
        try:
            product = json.loads(raw)["props"]["pageProps"]["product"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        if not isinstance(product, dict) or not product.get("id"):
            return None
        prices = product.get("prices") or {}
        card_price = float(prices.get("price") or product.get("price") or 0.0)
        pix_price = float(prices.get("priceWithDiscount") or 0.0) or card_price
        return ProductInfo(
            id=int(product["id"]),
            title=str(product.get("title") or ""),
            url=page.url,
            pix_price=pix_price,
            card_price=card_price,
            available=bool(product.get("available")),
            seller=str(product.get("sellerName") or ""),
        )

    async def _run(self, page: Page, url: str, max_price: float, confirm: bool, stopwatch: _Stopwatch) -> BuyResult:
        product = await self.fetch_product(page, url)
        if product is None:
            return BuyResult(status=BuyStatus.NOT_FOUND, detail="Dados do produto não encontrados na página")
        stopwatch.lap("produto")
        self._log(f"{product.title} | à vista R$ {product.pix_price:.2f} | disponível: {product.available}")
        if not product.available:
            return BuyResult(status=BuyStatus.UNAVAILABLE, product=product)
        if max_price > 0 and product.pix_price > max_price:
            detail = f"R$ {product.pix_price:.2f} acima do limite R$ {max_price:.2f}"
            return BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, product=product, detail=detail)
        await self._dismiss_consent(page)
        await page.click("#purchase", timeout=15000)
        await page.wait_for_url("**/precarrinho/**", timeout=20000)
        await self._wait_page_ready(page)
        if not await self._leave_precart(page):
            shot = await self._screenshot(page, "precarrinho")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Não consegui sair do pré-carrinho")
        stopwatch.lap("pre-carrinho")
        self._log("Produto no carrinho")
        return await self._checkout(page, product, confirm, stopwatch)

    async def _leave_precart(self, page: Page) -> bool:
        for _ in range(4):
            if "/carrinho" in page.url:
                return True
            button = await self._find_button(page, (r"ir\s+para\s+o\s+carrinho",))
            if button is None:
                button = await self._find_button(page, (r"^\s*agora\s+n[aã]o\s*$",))
                if button is not None:
                    self._log("Recusando serviços extras do pré-carrinho")
            if button is None:
                await asyncio.sleep(2.0)
                continue
            try:
                await button.click(timeout=8000)
            except Exception:
                pass
            try:
                await page.wait_for_url("**/carrinho**", timeout=10000)
                return True
            except PlaywrightTimeout:
                continue
        return "/carrinho" in page.url

    async def _checkout(self, page: Page, product: ProductInfo, confirm: bool, stopwatch: _Stopwatch) -> BuyResult:
        last_path = ""
        stuck = 0
        for _ in range(_MAX_CHECKOUT_STEPS):
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1.5)
            await self._wait_page_ready(page)
            path = page.url.split("kabum.com.br", 1)[-1]
            if path != last_path:
                if last_path:
                    stopwatch.lap(_step_name(last_path))
                last_path = path
                self._log(f"Etapa: {path}")
            await self._dismiss_consent(page)
            if await self._needs_login(page):
                detail = "Sessão não logada: rode scripts/autobuy_login.py e tente de novo"
                return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, detail=detail)
            code = await self._extract_pix(page)
            if code:
                stopwatch.lap(_step_name(path))
                return await self._pix_result(page, product, code, stopwatch)
            await self._apply_coupon(page)
            await self._select_pix_option(page)
            finalize = await self._find_button(page, _FINALIZE_PATTERNS)
            if finalize is not None:
                pix_confirmed = await self._payment_is_pix(page)
                if not confirm:
                    stopwatch.lap(_step_name(path))
                    shot = await self._screenshot(page, "dry-run")
                    detail = "Parado antes de finalizar o pedido (use confirm para comprar)"
                    detail += "; PIX confirmado na revisão" if pix_confirmed else "; atenção: PIX não confirmado na revisão"
                    if self._coupon_note:
                        detail += f"; {self._coupon_note}"
                    return BuyResult(status=BuyStatus.DRY_RUN_OK, product=product, screenshot=shot, detail=detail)
                if not pix_confirmed:
                    shot = await self._screenshot(page, "pagamento-nao-pix")
                    detail = "Forma de pagamento na revisão não é PIX, pedido não finalizado"
                    return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
                stopwatch.lap(_step_name(path))
                await finalize.click()
                self._log("Pedido enviado, aguardando código PIX")
                code = await self._wait_pix(page)
                if code:
                    return await self._pix_result(page, product, code, stopwatch)
                shot = await self._screenshot(page, "sem-pix")
                detail = "Pedido finalizado mas o código PIX não foi localizado, confira o screenshot"
                return BuyResult(status=BuyStatus.FAILED, product=product, screenshot=shot, detail=detail)
            if await self._click_continue(page):
                stuck = 0
                continue
            stuck += 1
            if stuck >= _MAX_STUCK_RETRIES:
                shot = await self._screenshot(page, "travado")
                detail = "Nenhum botão de avanço encontrado, possível captcha ou mudança de layout"
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
            await asyncio.sleep(2.0)
        shot = await self._screenshot(page, "loop")
        return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Checkout não convergiu")

    async def _apply_coupon(self, page: Page) -> None:
        if not self._coupon or self._coupon_note or "/endereco" not in page.url:
            return
        try:
            field = page.locator("[data-testid='input-coupon'], #inputAplicarCupom").first
            if await field.count() == 0 or not await field.is_visible():
                return
            await field.fill(self._coupon)
            await page.locator("[data-testid='button-apply-coupon']").first.click(timeout=5000)
            await asyncio.sleep(2.5)
            text = normalize(await page.evaluate("() => document.body ? document.body.innerText : ''"))
            if any(word in text for word in ("invalido", "expirado", "esgotado", "nao e valido", "indisponivel", "nao encontrado")):
                self._coupon_note = f"Cupom {self._coupon} recusado pela loja"
            else:
                self._coupon_note = f"Cupom {self._coupon} aplicado"
            self._log(self._coupon_note)
        except Exception:
            self._coupon_note = f"Cupom {self._coupon}: falha ao aplicar"
            self._log(self._coupon_note)

    async def _payment_is_pix(self, page: Page) -> bool:
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return False
        norm = normalize(text or "")
        marker = "forma de pagamento"
        if marker not in norm:
            return False
        return "pix" in norm.split(marker, 1)[1][:80]

    async def _wait_page_ready(self, page: Page, timeout: float = _PAGE_READY_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text = ""
            if "carregando" not in (text or "").lower():
                return
            await asyncio.sleep(1.0)

    async def _dismiss_consent(self, page: Page) -> None:
        for selector in _CONSENT_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    await locator.first.click(timeout=2000)
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue

    async def _needs_login(self, page: Page) -> bool:
        if "/login" in page.url:
            return True
        try:
            if await page.locator("input[type='password']").count() > 0:
                return True
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            return "acesse sua conta" in normalize(text)
        except Exception:
            return False

    async def _select_pix_option(self, page: Page) -> None:
        if "/pagamento" not in page.url:
            return
        try:
            option = page.locator("#paymentPage button", has_text=re.compile(r"^pix", re.IGNORECASE)).first
            if await option.count() == 0:
                option = page.get_by_role("button", name=re.compile(r"^pix", re.IGNORECASE)).first
            if await option.count() > 0 and await option.is_visible():
                await option.click(timeout=3000)
                await asyncio.sleep(1.0)
                self._log("Forma de pagamento PIX selecionada")
        except Exception:
            pass

    async def _find_button(self, page: Page, patterns: tuple[str, ...]) -> Locator | None:
        for raw in patterns:
            pattern = re.compile(raw, re.IGNORECASE)
            locator = page.get_by_role("button", name=pattern)
            try:
                count = await locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible() and await candidate.is_enabled():
                    return candidate
        return None

    async def _click_continue(self, page: Page) -> bool:
        for selector in _CONTINUE_TESTIDS:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                count = 0
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible() and await candidate.is_enabled():
                    try:
                        await candidate.click(timeout=8000)
                    except Exception:
                        pass
                    return True
        button = await self._find_button(page, _CONTINUE_PATTERNS)
        if button is None:
            return False
        try:
            await button.click(timeout=8000)
        except Exception:
            pass
        return True

    async def _extract_pix(self, page: Page) -> str:
        code = await self._pix_from_copy_button(page)
        if code:
            return code
        try:
            values = await page.eval_on_selector_all(
                "input, textarea", "els => els.map(e => e.value || '')"
            )
        except Exception:
            values = []
        for value in values:
            code = _clean_pix(value)
            if code:
                return code
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return ""
        return _clean_pix(text or "")

    async def _pix_from_copy_button(self, page: Page) -> str:
        button = await self._find_button(page, (r"copiar\s+c[oó]digo\s+pix",))
        if button is None:
            locator = page.get_by_text(re.compile(r"copiar\s+c[oó]digo\s+pix", re.IGNORECASE)).first
            try:
                if await locator.count() > 0 and await locator.is_visible():
                    button = locator
            except Exception:
                button = None
        if button is None:
            return ""
        try:
            await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
            await button.click(timeout=5000)
            await asyncio.sleep(0.6)
            value = await page.evaluate("() => navigator.clipboard.readText()")
        except Exception:
            return ""
        return _clean_pix(value or "")

    async def _wait_pix(self, page: Page, timeout: float = _PIX_WAIT_SECONDS) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = await self._extract_pix(page)
            if code:
                return code
            await asyncio.sleep(2.0)
        return ""

    async def _pix_result(self, page: Page, product: ProductInfo, code: str, stopwatch: _Stopwatch) -> BuyResult:
        stopwatch.lap("pix")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shot = await self._screenshot(page, "pix-print", stamp=stamp, full_page=True)
        result = BuyResult(
            status=BuyStatus.PIX_READY,
            product=product,
            pix_code=code,
            screenshot=shot,
            detail=self._coupon_note,
            elapsed=round(stopwatch.total(), 1),
            timings=dict(stopwatch.laps),
        )
        save_record(result, stamp)
        self._save_pix(product, code, stamp)
        self._log("Código PIX capturado")
        return result

    async def _screenshot(self, page: Page, tag: str, stamp: str | None = None, full_page: bool = False) -> str:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        path = paths.autobuy_dir() / f"{stamp}-{_slug(tag)}.png"
        try:
            await page.screenshot(path=str(path), full_page=full_page)
        except Exception:
            return ""
        return str(path)

    def _save_pix(self, product: ProductInfo, code: str, stamp: str | None = None) -> None:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        path = paths.autobuy_dir() / f"{stamp}-pix-{_slug(product.title)}.txt"
        content = f"{product.title}\nR$ {product.pix_price:.2f}\n{product.url}\n\n{code}\n"
        path.write_text(content, encoding="utf-8")
