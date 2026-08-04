from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable

from playwright.async_api import BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .. import paths
from ..matching import normalize
from .dom import click_first, find_clickable, page_text
from .models import BuyResult, BuyStatus, ProductInfo
from .urls import pichau_canonical, pichau_slug

LogCb = Callable[[str], None]
CaptchaCb = Callable[[], None]

STORE_ID = "pichau"
STORE_LABEL = "Pichau"

CAN_CHECKOUT = False

_PRICE_PIX = "[class*='price_vista']"
_PRICE_READY = (
    "() => [...document.querySelectorAll(\"[class*='price_vista'],[class*='price_total']\")]"
    ".some(e => /R\\$\\s*[\\d.]+,\\d{2}/.test(e.textContent || ''))"
)
_PRICE_CARD = "[class*='price_total']"
_PRICE_FROM = "[class*='price_from']"
_BUY_PATTERNS = (r"colocar\s+no\s+carrinho", r"^\s*comprar\s*$")
_CART_URL = "https://www.pichau.com.br/checkout/cart"

_UNAVAILABLE = ("indisponivel", "esgotado", "sem estoque", "avise-me", "produto indisponivel")
_CAPTCHA_HINTS = ("verificando seu navegador", "checking your browser", "verificacao de seguranca")

_HUMAN_WAIT_SECONDS = 180.0


def product_id(url: str) -> str | None:
    return pichau_slug(url)


def owns(url: str) -> bool:
    return pichau_slug(url) is not None


def canonical_url(url: str) -> str:
    return pichau_canonical(url)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "produto"


def _price_br(raw: str) -> float:
    match = re.search(r"\d[\d.,]*\d|\d", raw or "")
    if match is None:
        return 0.0
    texto = match.group(0)
    decimal = ""
    for separador in (",", "."):
        cauda = texto.rsplit(separador, 1)
        if len(cauda) == 2 and len(cauda[1]) == 2 and cauda[1].isdigit():
            if decimal == "" or texto.rfind(separador) > texto.rfind(decimal):
                decimal = separador
    if decimal:
        inteiro = texto.rsplit(decimal, 1)[0]
        centavos = texto.rsplit(decimal, 1)[1]
        limpo = re.sub(r"[.,]", "", inteiro) + "." + centavos
    else:
        limpo = re.sub(r"[.,]", "", texto)
    try:
        return float(limpo)
    except ValueError:
        return 0.0


def _price_iso(raw: str) -> float:
    try:
        return float((raw or "").strip())
    except ValueError:
        return 0.0


def _clean_title(raw: str) -> str:
    titulo = " ".join((raw or "").split())
    titulo = re.split(r"\s*\|\s*pichau\b", titulo, flags=re.IGNORECASE)[0]
    return titulo.strip(" |-–")


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


class PichauDriver:
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
        await page.goto(canonical_url(url), wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_function(_PRICE_READY, timeout=20000)
        except PlaywrightTimeout:
            self._log("Preço da Pichau demorou a renderizar, lendo o que houver na página")
            await asyncio.sleep(3.0)
        data = await page.evaluate(
            """() => {
                const txt = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; };
                let ld = null;
                for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                    try {
                        const j = JSON.parse(s.textContent);
                        if (j && j['@type'] === 'Product') { ld = j; break; }
                    } catch (e) {}
                }
                const comValor = seletor => {
                    for (const e of document.querySelectorAll(seletor)) {
                        const t = (e.textContent || '').trim();
                        if (/R\\$\\s*[\\d.]+,\\d{2}/.test(t)) return t;
                    }
                    return '';
                };
                return {
                    nome_ld: ld ? (ld.name || '') : '',
                    sku: ld ? (ld.sku || '') : '',
                    preco_ld: ld && ld.offers ? String(ld.offers.price || '') : '',
                    disp_ld: ld && ld.offers ? String(ld.offers.availability || '') : '',
                    titulo: txt('h1'),
                    pix: comValor("[class*='price_vista']"),
                    cartao: comValor("[class*='price_total']"),
                    corpo: document.body ? document.body.innerText.slice(0, 3000) : '',
                };
            }"""
        )
        title = _clean_title(data.get("nome_ld") or data.get("titulo") or "")
        if not title:
            return None
        bruto_pix = data.get("pix") or ""
        bruto_cartao = data.get("cartao") or ""
        pix_price = _price_br(bruto_pix)
        card_price = _price_br(bruto_cartao) or _price_iso(data.get("preco_ld") or "")
        self._log(f"precos lidos: pix={bruto_pix!r}->{pix_price} cartao={bruto_cartao!r}->{card_price}")
        if pix_price <= 0:
            pix_price = card_price
        if pix_price <= 0:
            return None
        corpo = normalize(data.get("corpo") or "")
        disponivel = "instock" in (data.get("disp_ld") or "").lower()
        if not data.get("disp_ld"):
            disponivel = "produto disponivel" in corpo
        if any(hint in corpo for hint in _UNAVAILABLE):
            disponivel = False
        return ProductInfo(
            id=0,
            title=title[:160],
            url=page.url,
            pix_price=pix_price,
            card_price=max(card_price, pix_price),
            available=disponivel,
            seller=str(data.get("sku") or STORE_LABEL)[:60],
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

        botao = await find_clickable(page, _BUY_PATTERNS)
        if botao is None:
            shot = await self._screenshot(page, "sem-botao")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Botão de comprar não encontrado")
        await botao.click(timeout=15000)
        await asyncio.sleep(4.0)
        stopwatch.lap("carrinho")
        self._log("Produto colocado no carrinho")

        shot = await self._screenshot(page, "carrinho")
        detail = (
            "Produto no carrinho. O checkout de pagamento da Pichau ainda não foi mapeado, "
            "então o robô para aqui e não gera o PIX"
        )
        if confirm:
            detail += ". Finalize manualmente se quiser concluir esta compra"
        return BuyResult(status=BuyStatus.DRY_RUN_OK, product=product, screenshot=shot, detail=detail)

    async def _handle_captcha(self, page: Page) -> bool:
        texto = normalize(await page_text(page))
        if not any(hint in texto for hint in _CAPTCHA_HINTS):
            return True
        self._log("Verificação de segurança na tela: resolva na janela, o robô continua depois")
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

    async def _screenshot(self, page: Page, tag: str, full_page: bool = False) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = paths.autobuy_dir() / f"{stamp}-pichau-{_slug(tag)}.png"
        try:
            await page.screenshot(path=str(path), full_page=full_page)
        except Exception:
            return ""
        return str(path)
