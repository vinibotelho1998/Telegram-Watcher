from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable

from playwright.async_api import BrowserContext, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import pix
from .. import paths
from ..matching import normalize
from .models import CHECKOUT_STUCK, HIDDEN_CAPTCHA, BuyResult, BuyStatus, ProductInfo, yes_no
from .records import save_record

LogCb = Callable[[str], None]
CaptchaCb = Callable[[], None]

STORE_ID = "kabum"
STORE_LABEL = "KaBuM!"
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
_REMOVE_BUTTON = "button[id^='remove-button-']"
_QUANTITY_INPUT = "input[id^='quantity-'], input[aria-label='Quantidade']"
_QUANTITY_PLUS = "button[aria-label*='aumentar quantidade' i], button[aria-label*='aumentar' i]"
_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha']",
    "iframe[title*='desafio' i]",
    "iframe[title*='challenge' i]",
    "#challenge-form",
    "#cf-challenge-running",
)
_MAX_CHECKOUT_STEPS = 20
_MAX_STUCK_RETRIES = 3
_PIX_WAIT_SECONDS = 90.0
_PAGE_READY_SECONDS = 20.0
_CAPTCHA_MIN_SIZE = 120
_HUMAN_WAIT_SECONDS = 180.0


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
    def __init__(
        self,
        context: BrowserContext,
        on_log: LogCb | None = None,
        on_captcha: CaptchaCb | None = None,
        headless: bool = False,
    ) -> None:
        self._context = context
        self._log = on_log or (lambda _message: None)
        self._on_captcha = on_captcha
        self._headless = headless
        self._coupon = ""
        self._coupon_note = ""
        self._quantity = 1
        self._quantity_done = False
        self._cart_cleared = False

    async def buy(
        self,
        url: str,
        max_price: float,
        confirm: bool = False,
        coupon: str = "",
        min_price: float = 0.0,
        quantity: int = 1,
    ) -> BuyResult:
        started = time.monotonic()
        stopwatch = _Stopwatch()
        self._coupon = coupon.strip().upper()
        self._coupon_note = ""
        self._quantity = max(1, quantity)
        self._quantity_done = False
        self._cart_cleared = False
        page = await self._context.new_page()
        try:
            result = await self._run(page, url, max_price, min_price, confirm, stopwatch)
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
        if pix_price <= 0:
            return None
        return ProductInfo(
            id=int(product["id"]),
            title=str(product.get("title") or ""),
            url=page.url,
            pix_price=pix_price,
            card_price=card_price,
            available=bool(product.get("available")),
            seller=str(product.get("sellerName") or ""),
        )

    async def _run(
        self,
        page: Page,
        url: str,
        max_price: float,
        min_price: float,
        confirm: bool,
        stopwatch: _Stopwatch,
    ) -> BuyResult:
        product = await self.fetch_product(page, url)
        if product is None:
            return BuyResult(status=BuyStatus.NOT_FOUND, detail="Dados do produto não encontrados na página")
        stopwatch.lap("produto")
        self._log(f"{product.title} | à vista R$ {product.pix_price:.2f} | disponível: {yes_no(product.available)}")
        if not product.available:
            return BuyResult(status=BuyStatus.UNAVAILABLE, product=product, detail="Produto sem estoque")
        if max_price > 0 and product.pix_price > max_price:
            detail = f"R$ {product.pix_price:.2f} acima do limite R$ {max_price:.2f}"
            return BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, product=product, detail=detail)
        if min_price > 0 and product.pix_price < min_price:
            detail = f"R$ {product.pix_price:.2f} abaixo do piso R$ {min_price:.2f}, parece anúncio suspeito"
            return BuyResult(status=BuyStatus.PRICE_BELOW_FLOOR, product=product, detail=detail)
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
            if not await self._handle_captcha(page):
                shot = await self._screenshot(page, "captcha")
                detail = HIDDEN_CAPTCHA if self._headless else "Captcha bloqueou o checkout e não foi resolvido a tempo"
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
            if await self._needs_login(page):
                detail = "Sessão não logada: conecte a conta na aba Auto-Compra e tente de novo"
                return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, detail=detail)
            if "/carrinho" in page.url:
                await self._clear_other_items(page, product.id)
                await self._set_quantity(page)
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
                    detail = "Parado antes de finalizar o pedido (para comprar de verdade, use o modo Compra real)"
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
        return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=CHECKOUT_STUCK)

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

    async def _captcha_present(self, page: Page) -> bool:
        for selector in _CAPTCHA_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0 or not await locator.is_visible():
                    continue
                box = await locator.bounding_box()
            except Exception:
                continue
            if box is None:
                continue
            if box["width"] >= _CAPTCHA_MIN_SIZE and box["height"] >= _CAPTCHA_MIN_SIZE:
                return True
        return False

    async def _handle_captcha(self, page: Page) -> bool:
        if not await self._captcha_present(page):
            return True
        if self._headless:
            self._log(HIDDEN_CAPTCHA)
            return False
        self._log("Captcha na tela: resolva na janela do navegador, o robô continua sozinho depois")
        if self._on_captcha is not None:
            try:
                self._on_captcha()
            except Exception:
                pass
        try:
            await page.bring_to_front()
        except Exception:
            pass
        deadline = time.monotonic() + _HUMAN_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            if not await self._captcha_present(page):
                self._log("Captcha resolvido, retomando o checkout")
                return True
        self._log("Captcha não foi resolvido a tempo")
        return False

    async def _clear_other_items(self, page: Page, keep_id: int) -> None:
        if self._cart_cleared:
            return
        self._cart_cleared = True
        manter = f"remove-button-{keep_id}"
        removidos = 0
        for _ in range(12):
            alvo = None
            botoes = page.locator(_REMOVE_BUTTON)
            try:
                total = await botoes.count()
            except Exception:
                return
            for index in range(total):
                candidato = botoes.nth(index)
                try:
                    if await candidato.get_attribute("id") != manter and await candidato.is_visible():
                        alvo = candidato
                        break
                except Exception:
                    continue
            if alvo is None:
                break
            try:
                await alvo.click(timeout=8000)
                removidos += 1
                await asyncio.sleep(1.5)
                await self._wait_page_ready(page)
            except Exception:
                break
        if removidos:
            self._log(f"Carrinho limpo: {removidos} item(ns) que não eram da promoção foram removidos")

    async def _set_quantity(self, page: Page) -> None:
        if self._quantity <= 1 or self._quantity_done:
            return
        self._quantity_done = True
        try:
            button = page.locator(_QUANTITY_PLUS).first
            if await button.count() > 0 and await button.is_visible():
                for _ in range(self._quantity - 1):
                    await button.click(timeout=5000)
                    await asyncio.sleep(1.0)
                atual = await self._read_quantity(page)
                self._log(f"Quantidade ajustada para {atual or self._quantity}")
                return
        except Exception:
            pass
        self._log(f"Botão de quantidade não encontrado, seguindo com 1 unidade (pedido: {self._quantity})")

    async def _read_quantity(self, page: Page) -> str:
        try:
            field = page.locator(_QUANTITY_INPUT).first
            if await field.count() > 0:
                return (await field.input_value()).strip()
        except Exception:
            pass
        return ""

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
        return await pix.extract(page)

    async def _wait_pix(self, page: Page, timeout: float = _PIX_WAIT_SECONDS) -> str:
        return await pix.wait_for(page, timeout)

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
        try:
            save_record(result, stamp, STORE_ID)
            self._save_pix(product, code, stamp)
        except OSError as exc:
            self._log(f"Não consegui gravar o registro do PIX em disco: {exc}")
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
