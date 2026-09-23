from __future__ import annotations

from telethon import TelegramClient

from .. import VERSION, paths
from ..config import AppConfig
from .models import BuyResult, BuyStatus, status_label

_STEP_LABELS = {
    "produto": "produto",
    "pre-carrinho": "pré-carrinho",
    "carrinho": "carrinho",
    "endereco": "endereço",
    "entrega": "entrega",
    "pagamento": "pagamento",
    "confirmacao": "revisão",
    "pix": "PIX",
}


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "s"


def headline(result: BuyResult) -> str:
    if result.status == BuyStatus.PIX_READY:
        return "PIX gerado pelo Auto-Compra"
    if result.status == BuyStatus.DRY_RUN_OK:
        return "Auto-Compra: simulação concluída, nenhum pedido foi feito"
    return f"Auto-Compra: compra NÃO feita, {status_label(result.status)}"


def build_message(result: BuyResult) -> str:
    product = result.product
    lines = [f"[Watcher] {headline(result)}"]
    if product:
        lines.append(product.title)
        lines.append(f"À vista no PIX: R$ {product.pix_price:.2f}")
        lines.append(product.url)
    if result.detail:
        lines.append(result.detail)
    if result.elapsed:
        timing = f"Tempo: {_seconds(result.elapsed)}"
        if result.timings:
            steps = " | ".join(f"{_STEP_LABELS.get(key, key)} {_seconds(value)}" for key, value in result.timings.items())
            timing += f" ({steps})"
        lines.append(timing)
    if result.pix_code:
        lines.append("")
        lines.append("Copia e cola:")
        lines.append(result.pix_code)
    return "\n".join(lines)


async def send_pix_notification(config: AppConfig, result: BuyResult) -> bool:
    if not config.api_id or not config.api_hash:
        return False
    session = str(paths.sessions_dir() / "user")
    client = TelegramClient(
        session,
        config.api_id,
        config.api_hash,
        device_model="Telegram Watcher",
        app_version=VERSION,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False
        await client.send_message("me", build_message(result))
        if result.screenshot:
            await client.send_file("me", result.screenshot)
        return True
    except Exception:
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
