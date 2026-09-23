from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import pix
from .. import paths
from ..matching import normalize
from .dom import click_first, page_text
from .models import BuyResult, BuyStatus, ProductInfo
from .urls import terabyte_id

LogCb = Callable[[str], None]
CaptchaCb = Callable[[], None]

STORE_ID = "terabyte"
STORE_LABEL = "Terabyte"

CAN_CHECKOUT = True

_CART_URL = "https://www.terabyteshop.com.br/Entrega.obj"
_PAYMENT_URL = "FormasPagamento.obj"
_BUY_BUTTON = (".buy-button", "#comprar-topo", "a.tbt_comprar")
_CLEAR_CART = ".btn-clear-cart"
_REMOVE_ITEM = ".cart-product-item__remove-btn"
_CEP_FIELD = "#cep"
_CALC_SHIPPING = ("button.tb-button--success",)
_SHIPPING_RADIO = "input[name='frete_SC']"
_SHIPPING_ITEM = ".shipping-frete-item"
_CONTINUE = "#btnAvancarPagamento"

_PIX_RADIO = "#ppix"
_PIX_LABEL = "label[for='ppix']"
_PLACE_ORDER = ("#cart_payment",)
_COUPON_TRIGGER = ".checkout-cupom-accordion__trigger"
_COUPON_FIELD = "#cupom"
_COUPON_APPLY = "#calCupom"
_COUPON_REJECTED = ("not valid", "invalido", "nao e valido", "expirado", "nao encontrado")

_LOST_SESSION = ("/Central.obj", "/Identificacao.obj")
_UNAVAILABLE = ("indisponivel", "esgotado", "sem estoque", "produto indisponivel", "avise-me")
_CAPTCHA_HINTS = ("verificando seu navegador", "checking your browser", "verificacao de seguranca", "um momento")

_MAX_STEPS = 12
_HUMAN_WAIT_SECONDS = 180.0
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")


def product_id(url: str) -> int | None:
    return terabyte_id(url)


def owns(url: str) -> bool:
    return terabyte_id(url) is not None


def canonical_url(url: str) -> str:
    return url


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "produto"


def _clean_title(raw: str) -> str:
    titulo = " ".join((raw or "").split())
    titulo = re.split(r"\s*[|–-]\s*terabyte\b", titulo, flags=re.IGNORECASE)[0]
    return titulo.strip(" |-–")


def _to_float(raw: str) -> float:
    digits = re.sub(r"[^\d,.]", "", raw or "")
    if "," in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif _THOUSANDS.fullmatch(digits):
        digits = digits.replace(".", "")
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


class TerabyteDriver:
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
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)
        data = await page.evaluate(
            """() => {
                const q = s => document.querySelector(s);
                const txt = s => { const e = q(s); return e ? e.textContent.trim() : ''; };
                let ld = null;
                for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                    try {
                        const j = JSON.parse(s.textContent);
                        if (j && j['@type'] === 'Product') { ld = j; break; }
                    } catch (e) {}
                }
                const riscado = [...document.querySelectorAll('del, s, .precoDe, .valDe, [class*=old i]')]
                    .map(e => e.textContent.trim()).filter(t => /R\\$/.test(t));
                return {
                    nome_ld: ld ? (ld.name || '') : '',
                    preco_ld: ld && ld.offers ? String(ld.offers.price || '') : '',
                    disp_ld: ld && ld.offers ? String(ld.offers.availability || '') : '',
                    titulo: txt('h1'),
                    vista: txt('#valVista') || txt('.valVista'),
                    riscado: riscado[0] || '',
                    corpo: document.body ? document.body.innerText.slice(0, 4000) : '',
                    tem_comprar: !!(q('.buy-button') || q('#comprar-topo')),
                };
            }"""
        )
        pid = product_id(page.url) or product_id(url) or 0
        title = _clean_title(data.get("nome_ld") or data.get("titulo") or "")
        if not title:
            return None
        pix_price = _to_float(data.get("vista") or "") or _to_float(data.get("preco_ld") or "")
        if pix_price <= 0:
            return None
        card_price = _to_float(data.get("riscado") or "") or pix_price
        corpo = normalize(data.get("corpo") or "")
        disponivel = bool(data.get("tem_comprar"))
        if "instock" not in (data.get("disp_ld") or "").lower() and data.get("disp_ld"):
            disponivel = False
        if any(hint in corpo for hint in _UNAVAILABLE):
            disponivel = False
        return ProductInfo(
            id=pid,
            title=title[:160],
            url=page.url,
            pix_price=pix_price,
            card_price=max(card_price, pix_price),
            available=disponivel,
            seller=STORE_LABEL,
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
        self._log(f"{product.title[:60]} | à vista R$ {product.pix_price:.2f} | disponível: {product.available}")
        if not product.available:
            return BuyResult(status=BuyStatus.UNAVAILABLE, product=product)
        if max_price > 0 and product.pix_price > max_price:
            detail = f"R$ {product.pix_price:.2f} acima do limite R$ {max_price:.2f}"
            return BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, product=product, detail=detail)
        if min_price > 0 and product.pix_price < min_price:
            detail = f"R$ {product.pix_price:.2f} abaixo do piso R$ {min_price:.2f}, parece anúncio suspeito"
            return BuyResult(status=BuyStatus.PRICE_BELOW_FLOOR, product=product, detail=detail)

        await self._clear_cart(page)
        await page.goto(product.url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.0)
        if not await click_first(page, _BUY_BUTTON, timeout=15000):
            return BuyResult(status=BuyStatus.BLOCKED, product=product, detail="Botão de comprar não encontrado")
        await asyncio.sleep(3.5)
        stopwatch.lap("carrinho")

        if not await self._prepare_shipping(page):
            if self._session_lost(page):
                return self._login_required(product)
            shot = await self._screenshot(page, "entrega")
            detail = "Não consegui calcular o frete na tela de entrega"
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
        stopwatch.lap("entrega")

        if not await click_first(page, (_CONTINUE,), timeout=10000):
            shot = await self._screenshot(page, "sem-continuar")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Botão Continuar não encontrado")
        await asyncio.sleep(4.0)
        if self._session_lost(page):
            return self._login_required(product)
        return await self._checkout(page, product, confirm, stopwatch)

    def _login_required(self, product: ProductInfo) -> BuyResult:
        detail = (
            "A Terabyte devolveu para a Central do Cliente: a sessão expirou. "
            "Reconecte a conta na aba Auto-Compra e tente de novo"
        )
        self._log(detail)
        return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, detail=detail)

    def _session_lost(self, page: Page) -> bool:
        return any(marca in page.url for marca in _LOST_SESSION)

    async def _checkout(self, page: Page, product: ProductInfo, confirm: bool, stopwatch: _Stopwatch) -> BuyResult:
        for _ in range(_MAX_STEPS):
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1.5)
            if not await self._handle_captcha(page):
                shot = await self._screenshot(page, "captcha")
                detail = "Verificação de segurança bloqueou o checkout"
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
            if self._session_lost(page):
                return self._login_required(product)

            code = await pix.extract(page)
            if code:
                stopwatch.lap("pix")
                return await self._pix_result(page, product, code, stopwatch)

            if _PAYMENT_URL not in page.url:
                await asyncio.sleep(2.0)
                continue

            await self._select_pix(page)
            await self._apply_coupon(page)
            if not await self._pix_selected(page):
                shot = await self._screenshot(page, "sem-pix")
                detail = "Não consegui selecionar o PIX na forma de pagamento"
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)

            stopwatch.lap("pagamento")
            if not confirm:
                shot = await self._screenshot(page, "dry-run")
                detail = "Parado na forma de pagamento com PIX selecionado (use compra real para gerar o código)"
                if self._coupon_note:
                    detail += f"; {self._coupon_note}"
                return BuyResult(status=BuyStatus.DRY_RUN_OK, product=product, screenshot=shot, detail=detail)

            if not await click_first(page, _PLACE_ORDER, timeout=10000):
                shot = await self._screenshot(page, "sem-fechar")
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Botão Fechar Pedido não encontrado")
            self._log("Pedido enviado, aguardando código PIX")
            await asyncio.sleep(3.0)
            if self._session_lost(page):
                shot = await self._screenshot(page, "sessao-caiu-no-pedido")
                detail = (
                    "A Terabyte devolveu para a Central do Cliente ao fechar o pedido: a sessão caiu. "
                    "NENHUM pedido foi feito. Reconecte a conta e tente de novo"
                )
                self._log(detail)
                return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, screenshot=shot, detail=detail)
            code = await pix.wait_for(page)
            if code:
                return await self._pix_result(page, product, code, stopwatch)
            if self._session_lost(page):
                shot = await self._screenshot(page, "sessao-caiu-no-pedido")
                detail = "Sessão caiu durante a geração do PIX. Nenhum pedido foi confirmado"
                return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, screenshot=shot, detail=detail)
            shot = await self._screenshot(page, "sem-codigo")
            detail = "Pedido finalizado mas o código PIX não foi localizado, confira o screenshot"
            return BuyResult(status=BuyStatus.FAILED, product=product, screenshot=shot, detail=detail)

        shot = await self._screenshot(page, "loop")
        return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Checkout não convergiu")

    async def _pix_selected(self, page: Page) -> bool:
        try:
            return await page.locator(f"{_PIX_RADIO}:checked").count() > 0
        except Exception:
            return False

    async def _select_pix(self, page: Page) -> None:
        if await self._pix_selected(page):
            return
        if await click_first(page, (_PIX_LABEL, _PIX_RADIO), timeout=8000):
            await asyncio.sleep(2.5)
            if await self._pix_selected(page):
                self._log("Forma de pagamento PIX selecionada")

    async def _apply_coupon(self, page: Page) -> None:
        if not self._coupon or self._coupon_note:
            return
        try:
            field = page.locator(_COUPON_FIELD).first
            if await field.count() == 0 or not await field.is_visible():
                await click_first(page, (_COUPON_TRIGGER,), timeout=6000)
                await asyncio.sleep(2.0)
                field = page.locator(_COUPON_FIELD).first
                if await field.count() == 0:
                    return
            await field.fill(self._coupon)
            await click_first(page, (_COUPON_APPLY,), timeout=6000)
            await asyncio.sleep(3.5)
            texto = normalize(await page_text(page))
            if any(hint in texto for hint in _COUPON_REJECTED):
                self._coupon_note = f"Cupom {self._coupon} recusado pela loja"
            else:
                self._coupon_note = f"Cupom {self._coupon} aplicado"
            self._log(self._coupon_note)
        except Exception:
            self._coupon_note = f"Cupom {self._coupon}: falha ao aplicar"
            self._log(self._coupon_note)

    async def _pix_result(self, page: Page, product: ProductInfo, code: str, stopwatch: _Stopwatch) -> BuyResult:
        from .records import save_record

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
            path = paths.autobuy_dir() / f"{stamp}-pix-{_slug(product.title)}.txt"
            path.write_text(
                f"{product.title}\nR$ {product.pix_price:.2f}\n{product.url}\n\n{code}\n", encoding="utf-8"
            )
        except OSError as exc:
            self._log(f"Não consegui gravar o registro do PIX em disco: {exc}")
        self._log("Código PIX capturado")
        return result

    async def _clear_cart(self, page: Page) -> None:
        try:
            await page.goto(_CART_URL, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.0)
        except Exception:
            return
        if await click_first(page, (_CLEAR_CART,), timeout=6000):
            await asyncio.sleep(2.5)
            self._log("Carrinho da Terabyte esvaziado antes da compra")
            return
        removidos = 0
        for _ in range(10):
            if not await click_first(page, (_REMOVE_ITEM,), timeout=5000):
                break
            removidos += 1
            await asyncio.sleep(1.5)
        if removidos:
            self._log(f"Carrinho da Terabyte limpo: {removidos} item(ns) removidos")

    async def _prepare_shipping(self, page: Page) -> bool:
        if "/Entrega.obj" not in page.url:
            try:
                await page.goto(_CART_URL, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                return False
        await asyncio.sleep(2.5)
        if not await self._handle_captcha(page):
            return False
        try:
            cep = page.locator(_CEP_FIELD).first
            if await cep.count() > 0 and not (await cep.input_value()).strip():
                self._log("CEP vazio na tela de entrega: cadastre um endereço padrão na conta")
                return False
        except Exception:
            pass
        if not await self._shipping_chosen(page):
            await click_first(page, _CALC_SHIPPING, timeout=8000)
            await asyncio.sleep(4.0)
        if not await self._shipping_chosen(page):
            await click_first(page, (f"{_SHIPPING_ITEM} label",), timeout=6000)
            await asyncio.sleep(2.0)
        escolhido = await self._shipping_chosen(page)
        if escolhido:
            self._log("Frete calculado e selecionado")
        return escolhido

    async def _shipping_chosen(self, page: Page) -> bool:
        try:
            return await page.locator(f"{_SHIPPING_RADIO}:checked").count() > 0
        except Exception:
            return False

    async def _handle_captcha(self, page: Page) -> bool:
        texto = normalize(await page_text(page))
        if not any(hint in texto for hint in _CAPTCHA_HINTS):
            return True
        self._log("Verificação de navegador na tela: resolva na janela, o robô continua depois")
        if self._on_captcha is not None:
            try:
                self._on_captcha()
            except Exception:
                pass
        deadline = time.monotonic() + _HUMAN_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            texto = normalize(await page_text(page))
            if not any(hint in texto for hint in _CAPTCHA_HINTS):
                return True
        return False

    async def _screenshot(self, page: Page, tag: str, stamp: str | None = None, full_page: bool = False) -> str:
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        path = paths.autobuy_dir() / f"{stamp}-terabyte-{_slug(tag)}.png"
        try:
            await page.screenshot(path=str(path), full_page=full_page)
        except Exception:
            return ""
        return str(path)
