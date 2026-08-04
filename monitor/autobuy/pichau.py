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
from .urls import pichau_canonical, pichau_slug

LogCb = Callable[[str], None]
CaptchaCb = Callable[[], None]

STORE_ID = "pichau"
STORE_LABEL = "Pichau"

CAN_CHECKOUT = True

_CART_ADD = "https://www.pichau.com.br/cart?sku={sku}"
_CART_PAGE = "https://www.pichau.com.br/cart"
_CHECKOUT_URL = "https://www.pichau.com.br/checkout"
_CLEAR_CART = (r"limpar\s+carrinho",)
_FINALIZE = (r"finalizar\s+pedido",)
_PIX_RADIO = "input[type=radio][value='mercadopago_custom_pix']"
_SHIPPING_RADIO = "input[type=radio][value^='intelipost_']"
_TERMS_TEXT = r"concordo\s+com\s+os\s+termos"
_COUPON_FIELD = "input[placeholder*='cupom' i], input[name*='coupon' i]"
_COUPON_APPLY = (r"^\s*aplicar\s*$",)
_COUPON_REJECTED = ("invalido", "nao e valido", "expirado", "nao encontrado", "not valid")
_LOST_SESSION = ("/customer/account/login", "/login")

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
_CAPTCHA_FRAMES = (
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='recaptcha/enterprise/bframe']",
    "iframe[src*='hcaptcha']",
    "iframe[title*='desafio' i]",
    "iframe[title*='challenge' i]",
)
_CAPTCHA_MIN_SIZE = 120
_CART_ITEM_COUNT = (
    "() => document.querySelectorAll(\"[class*='cart-item'],[class*='cartItem'],tr[class*='item']\").length"
)

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

        sku = (product.seller or "").strip()
        if not sku or sku == STORE_LABEL:
            shot = await self._screenshot(page, "sem-sku")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="SKU do produto não encontrado")

        await self._clear_cart(page)
        await page.goto(_CART_ADD.format(sku=sku), wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5.0)
        stopwatch.lap("carrinho")
        self._log(f"Produto {sku} colocado no carrinho")

        await page.goto(_CHECKOUT_URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(8.0)
        if self._session_lost(page):
            return self._login_required(product)
        if not await self._handle_captcha(page):
            shot = await self._screenshot(page, "captcha")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Verificação de segurança bloqueou o checkout")

        await self._ensure_shipping(page)
        await self._apply_coupon(page)
        if not await self._select_pix(page):
            shot = await self._screenshot(page, "sem-pix")
            detail = "Não consegui selecionar PIX na forma de pagamento"
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
        await self._accept_terms(page)
        stopwatch.lap("pagamento")

        if not confirm:
            shot = await self._screenshot(page, "dry-run")
            detail = "Parado no checkout com PIX selecionado (use compra real para gerar o código)"
            if self._coupon_note:
                detail += f"; {self._coupon_note}"
            return BuyResult(status=BuyStatus.DRY_RUN_OK, product=product, screenshot=shot, detail=detail)

        botao = await find_clickable(page, _FINALIZE)
        if botao is None:
            shot = await self._screenshot(page, "sem-finalizar")
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail="Botão Finalizar Pedido não encontrado")
        await botao.click(timeout=15000)
        self._log("Pedido enviado, aguardando código PIX")
        await asyncio.sleep(4.0)
        if self._session_lost(page):
            return self._login_required(product)

        if await self._captcha_visivel(page):
            if not await self._esperar_humano(page, "A Pichau pediu captcha para gerar o PIX"):
                shot = await self._screenshot(page, "captcha-no-pix")
                detail = (
                    "A Pichau pediu captcha na hora de gerar o PIX e ele não foi resolvido. "
                    "Confira em Meus Pedidos se o pedido chegou a ser criado"
                )
                return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)

        code = await pix.wait_for(page)
        if code:
            return await self._pix_result(page, product, code, stopwatch)
        if await self._captcha_visivel(page):
            shot = await self._screenshot(page, "captcha-no-pix")
            detail = "Captcha apareceu enquanto o PIX era gerado; confira a janela do navegador"
            return BuyResult(status=BuyStatus.BLOCKED, product=product, screenshot=shot, detail=detail)
        shot = await self._screenshot(page, "sem-codigo")
        detail = "Pedido finalizado mas o código PIX não foi localizado, confira o screenshot"
        return BuyResult(status=BuyStatus.FAILED, product=product, screenshot=shot, detail=detail)

    def _session_lost(self, page: Page) -> bool:
        return any(marca in page.url for marca in _LOST_SESSION)

    def _login_required(self, product: ProductInfo) -> BuyResult:
        detail = "A Pichau pediu login: a sessão caiu. Reconecte a conta e tente de novo"
        self._log(detail)
        return BuyResult(status=BuyStatus.LOGIN_REQUIRED, product=product, detail=detail)

    async def _clear_cart(self, page: Page) -> None:
        try:
            await page.goto(_CART_PAGE, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4.0)
        except Exception:
            return
        antes = await self._itens_no_carrinho(page)
        if antes == 0:
            self._log("Carrinho da Pichau já estava vazio")
            return
        botao = await find_clickable(page, _CLEAR_CART)
        if botao is None:
            self._log(f"Carrinho tem {antes} item(ns) e o botão de limpar não foi encontrado")
            return
        try:
            await botao.click(timeout=8000)
            await asyncio.sleep(2.5)
        except Exception:
            self._log("Falha ao clicar em limpar carrinho")
            return
        confirmar = await find_clickable(page, (r"^\s*(sim|confirmar|limpar|ok)\s*$",))
        if confirmar is not None:
            try:
                await confirmar.click(timeout=6000)
                await asyncio.sleep(2.5)
            except Exception:
                pass
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3.5)
        depois = await self._itens_no_carrinho(page)
        if depois == 0:
            self._log(f"Carrinho da Pichau esvaziado ({antes} item(ns) removidos)")
        else:
            self._log(f"ATENÇÃO: carrinho ainda tem {depois} item(ns); o pedido pode sair com item extra")

    async def _itens_no_carrinho(self, page: Page) -> int:
        try:
            texto = await page_text(page)
            achado = re.search(r"meu\s+carrinho\s*\|?\s*(\d+)", normalize(texto))
            if achado:
                return int(achado.group(1))
            if "carrinho esta vazio" in normalize(texto) or "carrinho vazio" in normalize(texto):
                return 0
            return int(await page.evaluate(_CART_ITEM_COUNT))
        except Exception:
            return -1

    async def _ensure_shipping(self, page: Page) -> None:
        try:
            marcado = await page.locator(f"{_SHIPPING_RADIO}:checked").count()
            if marcado:
                return
            opcao = page.locator(_SHIPPING_RADIO).first
            if await opcao.count() > 0:
                await opcao.check(timeout=8000)
                await asyncio.sleep(2.5)
                self._log("Frete selecionado")
        except Exception:
            pass

    async def _select_pix(self, page: Page) -> bool:
        radio = page.locator(_PIX_RADIO).first
        try:
            if await radio.count() == 0:
                self._log("Opção PIX não encontrada na tela de pagamento")
                return False
            if await radio.is_checked():
                self._log("Forma de pagamento PIX ja estava selecionada")
                return True
            await radio.scroll_into_view_if_needed(timeout=8000)
        except Exception:
            return False

        for tentativa in (self._check_forcado, self._clicar_rotulo_pix, self._clicar_texto_pix):
            try:
                await tentativa(page, radio)
            except Exception:
                continue
            await asyncio.sleep(3.0)
            try:
                if await radio.is_checked():
                    self._log("Forma de pagamento PIX selecionada")
                    return True
            except Exception:
                continue
        return False

    async def _check_forcado(self, _page: Page, radio) -> None:
        await radio.check(timeout=8000, force=True)

    async def _clicar_rotulo_pix(self, page: Page, radio) -> None:
        await radio.evaluate("e => (e.closest('label') || e.parentElement).click()")

    async def _clicar_texto_pix(self, page: Page, _radio) -> None:
        alvo = page.get_by_text(re.compile(r"pague\s+via\s+pix", re.IGNORECASE)).first
        if await alvo.count() > 0:
            await alvo.click(timeout=8000)

    async def _accept_terms(self, page: Page) -> None:
        try:
            caixa = page.get_by_role("checkbox").last
            if await caixa.count() > 0 and not await caixa.is_checked():
                await caixa.check(timeout=8000)
                await asyncio.sleep(1.5)
                self._log("Termos de venda aceitos")
        except Exception:
            self._log("Não consegui marcar os termos de venda")

    async def _apply_coupon(self, page: Page) -> None:
        if not self._coupon or self._coupon_note:
            return
        try:
            campo = page.locator(_COUPON_FIELD).first
            if await campo.count() == 0 or not await campo.is_visible():
                return
            await campo.fill(self._coupon)
            botao = await find_clickable(page, _COUPON_APPLY)
            if botao is None:
                return
            await botao.click(timeout=8000)
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
        shot = await self._screenshot(page, "pix-print", full_page=True)
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
        caminho = paths.autobuy_dir() / f"{stamp}-pix-{_slug(product.title)}.txt"
        caminho.write_text(
            f"{product.title}\nR$ {product.pix_price:.2f}\n{product.url}\n\n{code}\n", encoding="utf-8"
        )
        self._log("Código PIX capturado")
        return result

    async def _captcha_visivel(self, page: Page) -> bool:
        for seletor in _CAPTCHA_FRAMES:
            try:
                quadro = page.locator(seletor).first
                if await quadro.count() == 0 or not await quadro.is_visible():
                    continue
                caixa = await quadro.bounding_box()
            except Exception:
                continue
            if caixa and caixa["width"] >= _CAPTCHA_MIN_SIZE and caixa["height"] >= _CAPTCHA_MIN_SIZE:
                return True
        texto = normalize(await page_text(page))
        return any(hint in texto for hint in _CAPTCHA_HINTS)

    async def _esperar_humano(self, page: Page, motivo: str) -> bool:
        self._log(f"{motivo}: resolva na janela do navegador, o robô continua sozinho depois")
        if self._on_captcha is not None:
            try:
                self._on_captcha()
            except Exception:
                pass
        try:
            await page.bring_to_front()
        except Exception:
            pass
        limite = time.monotonic() + _HUMAN_WAIT_SECONDS
        while time.monotonic() < limite:
            await asyncio.sleep(2.0)
            if not await self._captcha_visivel(page):
                self._log("Verificação resolvida, retomando")
                return True
        self._log("A verificação não foi resolvida a tempo")
        return False

    async def _handle_captcha(self, page: Page) -> bool:
        if not await self._captcha_visivel(page):
            return True
        return await self._esperar_humano(page, "Verificação de segurança na tela")

    async def _handle_captcha_legado(self, page: Page) -> bool:
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
