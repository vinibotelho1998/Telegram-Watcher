from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from monitor.autobuy.browser import open_context
from monitor.autobuy.kabum import KabumDriver
from monitor.autobuy.models import BuyStatus
from monitor.autobuy.notify import send_pix_notification
from monitor.autobuy.resolver import find_kabum_product
from monitor.config import AppConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Testa o fluxo de compra da KaBuM até gerar o PIX")
    parser.add_argument("target", help="URL do produto ou texto de mensagem com link")
    parser.add_argument("--max-price", type=float, required=True, help="Preço máximo à vista")
    parser.add_argument("--confirm", action="store_true", help="Finaliza o pedido de verdade (padrão é dry-run)")
    parser.add_argument("--headless", action="store_true", help="Roda sem janela (padrão abre o navegador)")
    parser.add_argument("--notify", action="store_true", help="Envia o resultado nas Mensagens Salvas do seu Telegram")
    parser.add_argument("--coupon", default="", help="Cupom de desconto para aplicar no checkout")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    url = args.target if args.target.startswith("http") else None
    if url is None or not find_kabum_product(url):
        url = find_kabum_product(args.target)
    if not url:
        print("Nenhum link de produto KaBuM encontrado no texto informado")
        return 2
    print(f"Produto alvo: {url}")
    print(f"Limite: R$ {args.max_price:.2f} | modo: {'COMPRA REAL' if args.confirm else 'dry-run'}")
    async with async_playwright() as playwright:
        context = await open_context(playwright, headless=args.headless)
        driver = KabumDriver(context, on_log=lambda message: print(f"  {message}"))
        result = await driver.buy(url, args.max_price, confirm=args.confirm, coupon=args.coupon)
        await context.close()
    print(f"Status: {result.status.value}")
    if result.product:
        print(f"Produto: {result.product.title}")
        print(f"À vista: R$ {result.product.pix_price:.2f}")
    if result.detail:
        print(f"Detalhe: {result.detail}")
    if result.screenshot:
        print(f"Screenshot: {result.screenshot}")
    if result.pix_code:
        print("PIX copia e cola:")
        print(result.pix_code)
    print(f"Tempo: {result.elapsed:.1f}s")
    if result.timings:
        print("Etapas: " + " | ".join(f"{key} {value:.1f}s" for key, value in result.timings.items()))
    if args.notify and result.status in (BuyStatus.PIX_READY, BuyStatus.DRY_RUN_OK):
        sent = await send_pix_notification(AppConfig.load(), result)
        print("Notificação enviada nas Mensagens Salvas" if sent else "Falha ao notificar (sessão do Telegram indisponível)")
    return 0 if result.status in (BuyStatus.PIX_READY, BuyStatus.DRY_RUN_OK) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
