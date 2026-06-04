from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qasync
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from monitor.models import Channel, ChannelKind, EngineState, Filter, LogLevel
from ui.theme import apply_theme
from ui.window import AppWindow

OUT = Path(__file__).resolve().parent.parent / ".screens"
OUT.mkdir(exist_ok=True)


def register_fonts(app: QApplication) -> None:
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "segoeuil.ttf"):
        QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{name}")
    app.setFont(QFont("Segoe UI", 10))


def pump(app: QApplication, times: int = 30) -> None:
    for _ in range(times):
        app.processEvents()


def main() -> int:
    app = QApplication(sys.argv)
    register_fonts(app)
    apply_theme(dark=True)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = AppWindow(loop)
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
        {"time": "14:02:11", "source": "Pelando Cupons", "text": "Cupom MELI50 - 30% OFF no Mercado Livre", "status": "captured"},
        {"time": "14:02:12", "source": "Meu Canal de Ofertas", "text": "Replicado", "status": "forwarded"},
        {"time": "14:03:48", "source": "Gatry Hardware", "text": "SSD 1TB NVMe com frete grátis", "status": "captured"},
        {"time": "14:03:49", "source": "Meu Canal de Ofertas", "text": "Replicado", "status": "forwarded"},
        {"time": "14:05:02", "source": "Promobit Ofertas", "text": "Erro: canal sem permissão", "status": "failed"},
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

    captures = {
        "dashboard": window.dashboard,
        "filtros": window.filters_page,
        "canais": window.channels_page,
        "console": window.console_page,
        "settings": window.settings_page,
    }
    for name, page in captures.items():
        window.switchTo(page)
        QTest.qWait(450)
        window.grab().save(str(OUT / f"{name}.png"))
        print("salvo", name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
