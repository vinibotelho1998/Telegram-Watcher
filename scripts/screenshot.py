from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qasync
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from monitor.models import Channel, ChannelKind, EngineState, Filter, LogLevel
from monitor.reader import ChatMessage, ChatSummary
from ui.theme import apply_theme
from ui.window import AppWindow

OUT = Path(__file__).resolve().parent.parent / ".screens"
OUT.mkdir(exist_ok=True)

NOW = datetime.now(timezone.utc)

DEMO_CHATS = [
    ChatSummary(id=-1002, name="Pelando Cupons", kind=ChannelKind.CHANNEL, username="pelando",
                preview="Cupom MELI50 no Mercado Livre", date=NOW, unread=3, participants=84000),
    ChatSummary(id=-1001, name="Promobit Ofertas", kind=ChannelKind.CHANNEL, username="promobit",
                preview="Frete grátis na Amazon hoje", date=NOW - timedelta(hours=2), unread=0),
    ChatSummary(id=-1003, name="Gatry Hardware", kind=ChannelKind.GROUP,
                preview="SSD 1TB NVMe por R$ 329", date=NOW - timedelta(days=1), unread=7),
    ChatSummary(id=-2001, name="Meu Canal de Ofertas", kind=ChannelKind.CHANNEL,
                preview="Replicado", date=NOW - timedelta(days=3), unread=0),
]

DEMO_HISTORY = [
    ChatMessage(id=101, chat_id=-1002, text="Bom dia! Abrindo a rodada de cupons de hoje.",
                date=NOW - timedelta(days=1, hours=4), sender_id=1, sender_name="Pelando"),
    ChatMessage(id=102, chat_id=-1002, text="", date=NOW - timedelta(hours=5), sender_id=1,
                sender_name="Pelando", media_kind="photo", media_label="Foto"),
    ChatMessage(id=103, chat_id=-1002,
                text="Cupom MELI50 - 30% OFF no Mercado Livre\nhttps://mercadolivre.com.br/oferta\nSó até meia-noite!",
                date=NOW - timedelta(hours=3), sender_id=1, sender_name="Pelando", views=21430),
    ChatMessage(id=104, chat_id=-1002, text="Frete grátis acima de R$ 79 na Amazon",
                date=NOW - timedelta(hours=1), sender_id=1, sender_name="Pelando", views=9120),
    ChatMessage(id=105, chat_id=-1002, text="tabela-de-cupons.pdf", date=NOW - timedelta(minutes=25),
                sender_id=1, sender_name="Pelando", media_kind="file", media_label="tabela-de-cupons.pdf"),
]


class DemoReader:
    available = True

    async def dialogs(self, limit=200):
        return list(DEMO_CHATS)

    async def history(self, chat_id, limit=40, before_id=None):
        if before_id or chat_id != -1002:
            return []
        return list(DEMO_HISTORY)

    async def search(self, chat_id, query, limit=60):
        return []

    async def avatar(self, chat_id):
        return None

    async def thumbnail(self, chat_id, message_id):
        return None

    def forget(self):
        pass


def register_fonts(app: QApplication) -> None:
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "segoeuil.ttf"):
        QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{name}")
    app.setFont(QFont("Segoe UI", 10))


def pump(app: QApplication, times: int = 30) -> None:
    for _ in range(times):
        app.processEvents()


def settle(loop: asyncio.AbstractEventLoop, seconds: float = 0.45) -> None:
    loop.run_until_complete(asyncio.sleep(seconds))


def main() -> int:
    app = QApplication(sys.argv)
    register_fonts(app)
    apply_theme(dark=True)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    AppWindow.open_setup_wizard = lambda self: None
    window = AppWindow(loop)
    window.engine.reader = DemoReader()
    window.setMicaEffectEnabled(False)
    window.resize(1240, 820)
    window.show()
    pump(app)

    window.config.source_ids = [-1001, -1002, -1003]
    window.config.destination_ids = [-2001]
    window.config.aliases = {
        -1001: "Promobit Ofertas",
        -1002: "Pelando Cupons",
        -1003: "Gatry Hardware",
        -2001: "Meu Canal de Ofertas",
    }
    window.channels = [
        Channel(-1001, "Promobit Ofertas", ChannelKind.CHANNEL),
        Channel(-1002, "Pelando Cupons", ChannelKind.CHANNEL),
        Channel(-1003, "Gatry Hardware", ChannelKind.GROUP),
        Channel(-2001, "Meu Canal de Ofertas", ChannelKind.CHANNEL),
        Channel(-1004, "Tech Deals BR", ChannelKind.CHANNEL),
    ]
    window.filters = [
        Filter.from_dict({"id": "1", "name": "Cupons Mercado Livre", "smart_rules": ["any_coupon", "store:mercado_livre"], "destination_ids": [-2001]}),
        Filter.from_dict({"id": "2", "name": "Frete grátis + hardware", "include": ["ssd", "placa de video"], "smart_rules": ["free_shipping"]}),
        Filter.from_dict({"id": "3", "name": "Descontos 30%+", "smart_rules": ["discount>=30"], "enabled": False}),
    ]

    metrics = window.engine.metrics
    metrics.received, metrics.forwarded, metrics.filtered, metrics.failed = 148, 96, 49, 3
    hours = [0, 0, 0, 0, 0, 0, 2, 5, 9, 14, 22, 31, 28, 19, 24, 17, 12, 20, 26, 33, 18, 9, 4, 1]
    metrics.by_hour = hours

    events = [
        {"time": "14:02:11", "source": "Pelando Cupons", "text": "Cupom MELI50 - 30% OFF no Mercado Livre", "status": "captured", "chat_id": -1002},
        {"time": "14:02:12", "source": "Meu Canal de Ofertas", "text": "Replicado", "status": "forwarded", "chat_id": -2001},
        {"time": "14:03:48", "source": "Gatry Hardware", "text": "SSD 1TB NVMe com frete grátis", "status": "captured", "chat_id": -1003},
        {"time": "14:03:49", "source": "Meu Canal de Ofertas", "text": "Replicado", "status": "forwarded", "chat_id": -2001},
        {"time": "14:05:02", "source": "Promobit Ofertas", "text": "Erro: canal sem permissão", "status": "failed", "chat_id": -1001},
    ]
    logs = [
        ("Conectado como Vinicius", LogLevel.SUCCESS),
        ("Bot @dbx_monitor_bot pronto", LogLevel.SUCCESS),
        ("Sistema online e monitorando", LogLevel.SUCCESS),
        ("Capturado de Pelando Cupons", LogLevel.CAPTURE),
        ("Enviado para Meu Canal de Ofertas", LogLevel.SUCCESS),
        ("Limite do Telegram: aguardando 12s", LogLevel.WARNING),
        ("Falha em Promobit Ofertas: sem permissão", LogLevel.ERROR),
    ]

    window._on_state(EngineState.ONLINE)
    window._on_metrics()
    for event in events:
        window._on_event(event)
    for message, level in logs:
        window._on_log(message, level)
    window.filters_page.refresh()
    window.channels_page.refresh()
    window.settings_page.load()

    window.messages_page.reload_chats()
    settle(loop)
    window.messages_page.open_chat(-1002)
    settle(loop)

    captures = {
        "dashboard": window.dashboard,
        "conversas": window.messages_page,
        "filtros": window.filters_page,
        "canais": window.channels_page,
        "console": window.console_page,
        "settings": window.settings_page,
    }
    for name, page in captures.items():
        window.switchTo(page)
        settle(loop)
        window.grab().save(str(OUT / f"{name}.png"))
        print("salvo", name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
