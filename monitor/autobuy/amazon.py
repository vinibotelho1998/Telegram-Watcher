from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import pix
from .. import paths
from ..matching import normalize
from .dom import click_first, find_clickable, page_text
from .models import BuyResult, BuyStatus, ProductInfo
from .records import save_record

LogCb = Callable[[str], None]
CaptchaCb = Callable[[], None]

STORE_ID = "amazon"
STORE_LABEL = "Amazon"

_PRODUCT = re.compile(r"amazon\.com(?:\.br)?/(?:.*/)?(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
_ASIN_ONLY = re.compile(r"\b(B0[A-Z0-9]{8})\b")

_PRICE_SELECTORS = (
    "#corePrice_feature_div .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-offscreen",
    "#priceblock_ourprice",
    "#price_inside_buybox",
)
_PIX_PROMO = "#promotionMessageInsideBuyBox_feature_div"
_PIX_DISCOUNT = re.compile(r"(\d{1,2})\s*%\s*off[^.]{0,40}\bpix\b", re.IGNORECASE)

_BUY_NOW = "#buy-now-button"
_PLACE_ORDER = ("#placeOrder", "input[name='placeYourOrder1']")
_PIX_RADIO = "input[name='ppw-instrumentRowSelection'][value*='pix' i]"
_USE_PAYMENT = (
    "#checkout-primary-continue-button-id .a-button-input",
    "#checkout-primary-continue-button-id",
    "#checkout-secondary-continue-button-id .a-button-input",
    "#checkout-secondary-continue-button-id",
)
_USE_PAYMENT_TEXT = (
    r"use\s+este\s+m[ée]todo\s+de\s+pagamento",
    r"^\s*continuar\s*$",
)
_COUPON_FIELD = "input[name='ppw-claimCode']"
_COUPON_APPLY = "input[name='ppw-claimCodeApplyPressed']"
_COUPON_LINK = (r"usar\s+um\s+cupom\s+de\s+desconto", r"cupom\s+de\s+desconto\s+ou\s+vale-presente")
_COUPON_REJECTED = (
    "cupom de desconto inserido e invalido",
    "invalido",
    "nao e valido",
    "expirado",
    "nao foi possivel",
    "nao pode ser aplicado",
    "verifique o codigo",
)

_DECLINE_PRIME = (
    r"^\s*n[ãa]o,?\s*obrigad",
    r"^\s*agora\s+n[ãa]o\s*$",
    r"^\s*continuar\s+sem\s+prime",
)
_CHANGE_PAYMENT = (
    r"pagando\s+com",
    r"^\s*alterar\s*$",
)
_CARD_WORDS = ("cartao", "credito", "mastercard", "visa", "elo", "american express", "nupay", "boleto")
_CAPTCHA_HINTS = ("digite os caracteres", "not a robot", "sou um rob", "enter the characters")

_MAX_STEPS = 14
_HUMAN_WAIT_SECONDS = 180.0


def product_id(url: str) -> str | None:
    match = _PRODUCT.search(url or "")
    return match.group(1).upper() if match else None


def owns(url: str) -> bool:
    return product_id(url) is not None


def canonical_url(url: str) -> str:
    asin = product_id(url)
    return f"https://www.amazon.com.br/dp/{asin}" if asin else url


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "produto"


def _to_float(raw: str) -> float:
    digits = re.sub(r"[^\d,.]", "", raw or "").replace(".", "").replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0


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


class AmazonDriver:
    def __init__(
        self,
        context: BrowserContext,
        on_log: LogCb | None = None,
        on_captcha: CaptchaCb | None = None,
    ) -> None:
        self._context = context
        self._log = on_log or (lambda _message: None)
        self._on_captcha = on_captcha
        self._quantity = 1
        self._pix_selected = False
        self._coupon = ""
        self._coupon_note = ""

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
        self._quantity = max(1, quantity)
        self._pix_selected = False
        self._coupon = coupon.strip().upper()
        self._coupon_note = ""
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
        await page.goto(canonical_url(url), wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector(_PIX_PROMO, timeout=6000)
        except PlaywrightTimeout:
            await asyncio.sleep(1.5)
        data = await page.evaluate(
            """() => {
                const q = s => document.querySelector(s);
                const txt = s => { const e = q(s); return e ? e.textContent.trim() : ''; };
                const seletores = [
                    '#corePrice_feature_div .a-offscreen',
                    '#corePriceDisplay_desktop_feature_div .a-offscreen',
                    '#priceblock_ourprice',
                    '#price_inside_buybox'
                ];
                let preco = '';
                for (const s of seletores) { preco = txt(s); if (preco) break; }
                const caixa = q('#buybox') || q('#rightCol') || q('#centerCol');
                return {
                    asin: q('#ASIN') ? q('#ASIN').value : '',
                    titulo: txt('#productTitle'),
                    preco: preco,
                    promo: txt('#promotionMessageInsideBuyBox_feature_div'),
                    caixa: caixa ? caixa.innerText : '',
                    estoque: txt('#availability'),
                    vendedor: txt('#sellerProfileTriggerId') || txt('#merchant-info'),
                    tem_compra: !!q('#buy-now-button'),
                    tem_carrinho: !!q('#add-to-cart-button'),
                };
            }"""
        )
        asin = (data.get("asin") or product_id(url) or "").strip()
        if not asin or not data.get("titulo"):
            return None
        card_price = _to_float(data.get("preco") or "")
        if card_price <= 0:
            return None
        pix_price = self._pix_price(card_price, data.get("promo") or "", data.get("caixa") or "")
        estoque = normalize(data.get("estoque") or "")
        available = bool(data.get("tem_compra") or data.get("tem_carrinho"))
        if "indisponivel" in estoque or "fora de estoque" in estoque:
            available = False
        return ProductInfo(
            id=int(re.sub(r"\D", "", asin) or 0),
            title=str(data.get("titulo") or "")[:160],
            url=page.url,
            pix_price=pix_price,
            card_price=card_price,
            available=available,
            seller=str(data.get("vendedor") or "").strip()[:60],
        )

    def _pix_price(self, card_price: float, promo: str, fallback: str = "") -> float:
        match = _PIX_DISCOUNT.search(promo or "") or _PIX_DISCOUNT.search(fallback or "")
        if not match:
            return card_price
        try:
            percent = int(match.group(1))
        except ValueError:
            return card_price
        if not 0 < percent < 90:
            return card_price
        value = round(card_price * (1 - percent / 100.0), 2)
        self._log(f"Desconto de {percent}% no PIX detectado na página")
        return value

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
        self._log(f"{product.title[:60]} | PIX R$ {product.pix_price:.2f} | disponível: {product.available}")
        if not product.available:
            return BuyResult(status=BuyStatus.UNAVAILABLE, product=product)
        if max_price > 0 and product.pix_price > max_price:
            detail = f"R$ {product.pix_price:.2f} acima do limite R$ {max_price:.2f}"
            return BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, product=product, detail=detail)
        if min_price > 0 and product.pix_price < min_price:
            detail = f"R$ {product.pix_price:.2f} abaixo do piso R$ {min_price:.2f}, parece anúncio suspeito"
            return BuyResult(status=BuyStatus.PRICE_BELOW_FLOOR, product=product, detail=detail)
        await self._set_quantity(page)
        if not await click_first(page, (_BUY_NOW,), timeout=15000):
            return BuyResult(status=BuyStatus.BLOCKED, product=product, detail="Botão 'Comprar agora' não encontrado")
        self._log("Comprar agora acionado, entrando no checkout")
        return await self._checkout(page, product, confirm, stopwatch)

    async def _set_quantity(self, page: Page) -> None:
        if self._quantity <= 1:
            return
        try:
            select = page.locator("#quantity, select[name='quantity']").first
            if await select.count() > 0:
                opcoes = await select.evaluate("e => [...e.options].map(o => o.value)")
                disponiveis = [int(o) for o in opcoes if o.isdigit()]
                if not disponiveis:
                    raise ValueError("sem opções numéricas")
                alvo = self._quantity
                if alvo not in disponiveis:
                    alvo = max(disponiveis)
                    self._log(f"A loja só permite até {alvo} unidades (pedido: {self._quantity})")
                await select.select_option(str(alvo))
                await asyncio.sleep(1.0)
                self._log(f"Quantidade ajustada para {alvo}")
                return
        except Exception:
            pass
        self._log(f"Não consegui ajustar a quantidade, seguindo com 1 (pedido: {self._quantity})")

    async def _checkout(self, page: Page, product: ProductInfo, confirm: bool, stopwatch: _Stopwatch) -> BuyResult:
        last_step = ""
        for _ in range(_MAX_STEPS):
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1.5)
            step = self._step_name(page.url)
            if step != last_step:
                if last_step:
                    stopwatch.lap(last_step)
                last_step = step
                self._log(f"Etapa: {step}")

            if not await self._handle_captcha(page):
                shot = await self._screenshot(page, "captcha")
                detail = "Captcha bloqueou o checkout e não foi resolvido a tempo"
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)

            if await self._needs_login(page):
                detail = "Sessão não logada: rode scripts/autobuy_login.py amazon e tente de novo"
                return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, detail=detail)

            code = await pix.extract(page)
            if code:
                stopwatch.lap(step)
                return await self._pix_result(page, product, code, stopwatch)

            if await self._decline_prime(page):
                continue

            if await self._apply_coupon(page):
                continue

            if not self._pix_selected and await self._select_pix(page):
                continue

            final = await self._final_button(page)
            if final is not None:
                label = await self._button_label(final)
                if not await self._payment_is_pix(page, label):
                    shot = await self._screenshot(page, "pagamento-nao-pix")
                    detail = f"Pagamento não é PIX (botão diz '{label[:40]}'), pedido não finalizado"
                    return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
                if not confirm:
                    stopwatch.lap(step)
                    shot = await self._screenshot(page, "dry-run")
                    detail = "Parado na revisão com PIX selecionado (use compra real para gerar o código)"
                    if self._coupon_note:
                        detail += f"; {self._coupon_note}"
                    return BuyResult(status=BuyStatus.DRY_RUN_OK, product=product, screenshot=shot, detail=detail)
                stopwatch.lap(step)
                await final.click()
                self._log("Pedido enviado, aguardando código PIX")
                code = await pix.wait_for(page)
                if code:
                    return await self._pix_result(page, product, code, stopwatch)
                shot = await self._screenshot(page, "sem-pix")
                detail = "Pedido finalizado mas o código PIX não foi localizado, confira o screenshot"
                return BuyResult(status=BuyStatus.FAILED, product=product, screenshot=shot, detail=detail)

            if await self._confirm_payment(page):
                continue
            await asyncio.sleep(2.0)

        shot = await self._screenshot(page, "loop")
        visiveis = await self._visible_buttons(page)
        detail = f"Checkout não convergiu na etapa '{last_step}'"
        if visiveis:
            detail += f"; botões na tela: {' | '.join(visiveis)}"
        return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)

    async def _visible_buttons(self, page: Page) -> list[str]:
        try:
            return await page.evaluate(
                """() => [...document.querySelectorAll('button, input[type=submit], span.a-button-inner')]
                    .filter(b => b.offsetParent !== null)
                    .map(b => (b.value || b.textContent || '').trim().replace(/\\s+/g,' ').slice(0,40))
                    .filter(t => t.length > 1)
                    .slice(0, 6)"""
            )
        except Exception:
            return []

    @staticmethod
    def _step_name(url: str) -> str:
        if "/checkout/" not in url:
            return "produto"
        tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return {"pip": "prime", "spc": "revisao", "pay": "pagamento"}.get(tail, tail or "checkout")

    async def _decline_prime(self, page: Page) -> bool:
        button = await find_clickable(page, _DECLINE_PRIME)
        if button is None:
            return False
        try:
            await button.click(timeout=8000)
        except Exception:
            return False
        self._log("Oferta do Prime recusada")
        await asyncio.sleep(2.0)
        return True

    async def _apply_coupon(self, page: Page) -> bool:
        if not self._coupon or self._coupon_note:
            return False
        field = page.locator(_COUPON_FIELD).first
        try:
            if await field.count() == 0 or not await field.is_visible():
                link = await find_clickable(page, _COUPON_LINK)
                if link is None:
                    return False
                await link.click(timeout=8000)
                await asyncio.sleep(2.5)
                field = page.locator(_COUPON_FIELD).first
                if await field.count() == 0 or not await field.is_visible():
                    return False
            await field.fill(self._coupon)
            if not await click_first(page, (_COUPON_APPLY,)):
                self._coupon_note = f"Cupom {self._coupon}: botão de aplicar não encontrado"
                self._log(self._coupon_note)
                return False
            await asyncio.sleep(3.0)
            texto = normalize(await page_text(page))
            if any(hint in texto for hint in _COUPON_REJECTED):
                self._coupon_note = f"Cupom {self._coupon} recusado pela loja"
            else:
                self._coupon_note = f"Cupom {self._coupon} aplicado"
            self._log(self._coupon_note)
            return True
        except Exception:
            self._coupon_note = f"Cupom {self._coupon}: falha ao aplicar"
            self._log(self._coupon_note)
            return False

    async def _confirm_payment(self, page: Page) -> bool:
        if await click_first(page, _USE_PAYMENT):
            self._log("Método de pagamento confirmado")
            await asyncio.sleep(1.5)
            return True
        button = await find_clickable(page, _USE_PAYMENT_TEXT)
        if button is None:
            return False
        try:
            await button.click(timeout=8000)
        except Exception:
            return False
        self._log("Método de pagamento confirmado")
        await asyncio.sleep(1.5)
        return True

    async def _select_pix(self, page: Page) -> bool:
        radio = page.locator(_PIX_RADIO)
        try:
            if await radio.count() > 0:
                await radio.first.check(timeout=8000)
                self._pix_selected = True
                self._log("Forma de pagamento PIX selecionada")
                await asyncio.sleep(1.0)
                await self._confirm_payment(page)
                return True
        except Exception:
            pass
        if "/pay" in page.url:
            return False
        button = await find_clickable(page, _CHANGE_PAYMENT)
        if button is None:
            return False
        try:
            await button.click(timeout=8000)
        except Exception:
            return False
        self._log("Abrindo a troca de forma de pagamento")
        await asyncio.sleep(2.0)
        return True

    async def _button_label(self, locator) -> str:
        try:
            return (
                await locator.evaluate(
                    """el => {
                        const lb = el.getAttribute('aria-labelledby');
                        if (lb) {
                            const alvo = document.getElementById(lb);
                            if (alvo && alvo.textContent.trim()) return alvo.textContent.trim();
                        }
                        const span = el.closest('span.a-button');
                        if (span) {
                            const t = span.querySelector('.a-button-text');
                            if (t && t.textContent.trim()) return t.textContent.trim();
                        }
                        return (el.value || el.textContent || '').trim();
                    }"""
                )
                or ""
            ).strip()
        except Exception:
            return ""

    async def _final_button(self, page: Page):
        for selector in _PLACE_ORDER:
            locator = page.locator(selector)
            try:
                if await locator.count() > 0 and await locator.first.is_visible() and await locator.first.is_enabled():
                    return locator.first
            except Exception:
                continue
        return None

    async def _payment_is_pix(self, page: Page, button_label: str) -> bool:
        label = normalize(button_label)
        if any(word in label for word in _CARD_WORDS):
            return False
        if "pix" in label:
            return True
        text = normalize(await page_text(page))
        marker = "pagando com"
        if marker in text:
            return "pix" in text.split(marker, 1)[1][:60]
        return self._pix_selected

    async def _needs_login(self, page: Page) -> bool:
        if "/ap/signin" in page.url:
            return True
        try:
            if await page.locator("#ap_password").count() > 0:
                return True
        except Exception:
            return False
        return False

    async def _captcha_present(self, page: Page) -> bool:
        try:
            if await page.locator("form[action*='validateCaptcha']").count() > 0:
                return True
        except Exception:
            pass
        text = normalize(await page_text(page))
        return any(hint in text for hint in _CAPTCHA_HINTS)

    async def _handle_captcha(self, page: Page) -> bool:
        if not await self._captcha_present(page):
            return True
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
        return False

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
        save_record(result, stamp, STORE_ID)
        self._save_pix(product, code, stamp)
        self._log("Código PIX capturado")
        return result

    async def _screenshot(self, page: Page, tag: str, stamp: str | None = None, full_page: bool = False) -> str:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        path = paths.autobuy_dir() / f"{stamp}-amazon-{_slug(tag)}.png"
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
