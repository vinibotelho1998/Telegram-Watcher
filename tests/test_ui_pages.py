from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLabel

from watcher.config import AppConfig
from watcher.reader import ChatMessage


class LeitorFalso:
    available = True

    def __init__(self) -> None:
        self.portoes: dict[int, asyncio.Event] = {}

    def portao(self, chat_id: int) -> asyncio.Event:
        return self.portoes.setdefault(chat_id, asyncio.Event())

    async def history(self, chat_id, limit=40, before_id=None):
        if before_id:
            return []
        await self.portao(chat_id).wait()
        return [ChatMessage(id=-chat_id, chat_id=chat_id, text=f"mensagem de {chat_id}", sender_id=1)]

    async def avatar(self, chat_id):
        return None

    async def thumbnail(self, chat_id, message_id):
        return None


class AppFalso:
    def __init__(self) -> None:
        self.engine = SimpleNamespace(reader=LeitorFalso())
        self.config = AppConfig()
        self.filters = []
        self.tarefas: list[asyncio.Task] = []
        self.salvos: list[dict] = []

    def schedule(self, coro):
        tarefa = asyncio.get_running_loop().create_task(coro)
        self.tarefas.append(tarefa)
        return tarefa

    def toast(self, *_args, **_kwargs) -> None:
        return None

    def is_windows_autostart(self) -> bool:
        return False

    def set_windows_autostart(self, _enabled: bool) -> None:
        return None

    def toggle_theme(self, _dark: bool) -> None:
        return None

    def save_settings(self, payload: dict, silent: bool = False) -> None:
        self.salvos.append(payload)


async def _esperar_tarefas(app: AppFalso) -> None:
    while any(not tarefa.done() for tarefa in app.tarefas):
        await asyncio.gather(*app.tarefas)


def test_trocar_de_conversa_durante_o_carregamento_nao_trava(qapp):
    from ui.pages.messages import MessagesPage

    app = AppFalso()
    leitor = app.engine.reader

    async def cenario():
        pagina = MessagesPage(app)
        pagina.open_chat(-1)
        await asyncio.sleep(0)
        pagina.open_chat(-2)
        await asyncio.sleep(0)
        leitor.portao(-1).set()
        leitor.portao(-2).set()
        await _esperar_tarefas(app)
        return pagina

    pagina = asyncio.run(cenario())
    assert list(pagina._bubbles) == [2]
    pagina.deleteLater()


def test_configuracoes_mostram_credenciais_salvas_pelo_assistente(qapp):
    from ui.pages.settings import SettingsPage

    app = AppFalso()
    pagina = SettingsPage(app)
    app.config.api_id = 123
    app.config.api_hash = "abc"
    app.config.phone = "+5511"
    pagina.show()
    pagina._save()
    pagina.close()
    assert app.salvos[-1]["api_id"] == "123"
    assert app.salvos[-1]["api_hash"] == "abc"
    assert app.salvos[-1]["phone"] == "+5511"


@pytest.mark.parametrize(
    "texto, valor",
    [
        ("350", 350.0),
        ("2.300", 2300.0),
        ("R$ 1.299,90", 1299.90),
        ("2,30", 2.30),
        ("2300.50", 2300.50),
        ("12.345.678", 12345678.0),
        ("abc", 0.0),
    ],
)
def test_preco_digitado_com_ponto_de_milhar(texto, valor):
    from ui.watch_dialog import parse_price

    assert parse_price(texto) == pytest.approx(valor)


def test_falha_ao_carregar_conversas_avisa_e_libera_o_botao(qapp):
    from ui.pages.messages import MessagesPage

    app = AppFalso()
    avisos: list[tuple] = []
    app.toast = lambda mensagem, tipo="info": avisos.append((mensagem, tipo))

    async def sem_rede(limit=200):
        raise ConnectionError("sem rede")

    app.engine.reader.dialogs = sem_rede

    async def cenario():
        pagina = MessagesPage(app)
        pagina.reload_btn.setEnabled(False)
        await pagina._fetch_chats()
        return pagina

    pagina = asyncio.run(cenario())
    assert pagina.reload_btn.isEnabled()
    assert avisos and avisos[-1][1] == "error" and "sem rede" in avisos[-1][0]
    pagina.deleteLater()


def test_falha_na_busca_mostra_aviso_na_conversa(qapp):
    from ui.pages.messages import MessagesPage

    app = AppFalso()

    async def busca_quebrada(chat_id, query, limit=60):
        raise ConnectionError("sem rede")

    app.engine.reader.search = busca_quebrada

    async def cenario():
        pagina = MessagesPage(app)
        pagina._chat_id = -1
        await pagina._fetch_search("cupom")
        return pagina

    pagina = asyncio.run(cenario())
    textos = [w.text() for w in pagina.findChildren(QLabel)]
    assert any("sem rede" in texto for texto in textos)
    pagina.deleteLater()


def test_falha_ao_sincronizar_canais_avisa(qapp):
    from ui.window import AppWindow

    avisos: list[tuple] = []
    estados: list[bool] = []

    async def sem_rede():
        raise ConnectionError("sem rede")

    janela = SimpleNamespace(
        engine=SimpleNamespace(sync_channels=sem_rede),
        channels_page=SimpleNamespace(set_syncing=estados.append),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    asyncio.run(AppWindow._sync_channels(janela))
    assert estados == [False]
    assert avisos and avisos[-1][1] == "error" and "sem rede" in avisos[-1][0]


def test_console_nao_cresce_sem_limite(qapp):
    from ui.pages.console import ConsolePage
    from watcher.models import LogLevel

    pagina = ConsolePage(None)
    for indice in range(1200):
        pagina.append(f"linha {indice}", LogLevel.INFO)
    assert pagina.view.document().blockCount() <= 800
    assert "linha 1199" in pagina.view.toPlainText()
    pagina.deleteLater()


def test_conversa_escondida_nao_acumula_mensagens(qapp):
    from ui.pages.messages import MessagesPage

    app = AppFalso()
    app.engine.reader.portao(-1).set()

    async def cenario():
        pagina = MessagesPage(app)
        pagina.open_chat(-1)
        await _esperar_tarefas(app)
        for indice in range(300):
            pagina.on_incoming(-1, ChatMessage(id=1000 + indice, chat_id=-1, text="nova", sender_id=1))
        escondida = len(pagina._bubbles)
        pagina.show()
        await _esperar_tarefas(app)
        pagina.on_incoming(-1, ChatMessage(id=5000, chat_id=-1, text="ao vivo", sender_id=1))
        pagina.hide()
        return pagina, escondida

    pagina, escondida = asyncio.run(cenario())
    assert escondida == 1
    assert sorted(pagina._bubbles) == [1, 5000]
    pagina.deleteLater()


def test_erro_nao_tratado_vai_para_o_log_em_vez_de_derrubar_o_app(caplog):
    import logging
    import sys

    import app

    try:
        raise RuntimeError("falha num clique")
    except RuntimeError:
        tipo, valor, rastro = sys.exc_info()
    with caplog.at_level(logging.ERROR, logger="watcher"):
        app._log_unhandled(tipo, valor, rastro)
    assert "falha num clique" in caplog.text


def test_erro_em_tarefa_de_fundo_vai_para_o_log(caplog):
    import gc
    import logging

    import app

    async def falha():
        raise RuntimeError("tarefa quebrou")

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(app._log_async_error)
    try:
        with caplog.at_level(logging.ERROR, logger="watcher"):
            tarefa = loop.create_task(falha())
            loop.run_until_complete(asyncio.sleep(0.01))
            del tarefa
            gc.collect()
    finally:
        loop.close()
    registros = [r for r in caplog.records if r.name == "watcher" and r.exc_info]
    assert registros and "tarefa quebrou" in str(registros[0].exc_info[1])


def test_texto_vindo_do_telegram_nao_vira_html(qapp, tmp_path, monkeypatch):
    from PyQt6.QtGui import QColor, QImage

    from ui.pages.channels import _ChannelRow
    from ui.pages.dashboard import _FeedRow
    from ui.pages.messages import MessagesPage, _Bubble, _ChatRow
    from watcher.models import Channel
    from watcher.reader import ChatSummary

    monkeypatch.chdir(tmp_path)
    quadro = QImage(300, 300, QImage.Format.Format_RGB32)
    quadro.fill(QColor("red"))
    quadro.save("isca.png")
    isca = "<img src=isca.png>"

    app = AppFalso()
    app.engine.reader.portao(-1).set()

    async def conversa():
        pagina = MessagesPage(app)
        pagina._summaries = [ChatSummary(id=-1, name=isca)]
        pagina.open_chat(-1)
        await _esperar_tarefas(app)
        return pagina

    pagina = asyncio.run(conversa())
    assert pagina.thread_title.sizeHint().height() < 100
    pagina.deleteLater()

    widgets = [
        _FeedRow({"time": "10:00", "source": isca, "text": isca, "status": "captured"}),
        _ChatRow(ChatSummary(id=-1, name=isca, preview=isca), ""),
        _Bubble(
            ChatMessage(id=1, chat_id=-1, text="oi", sender_id=2, sender_name=isca, media_kind="file", media_label=isca),
            True,
            [],
        ),
        _ChannelRow(Channel(-1, isca), AppFalso()),
    ]
    for widget in widgets:
        for rotulo in widget.findChildren(QLabel):
            assert rotulo.sizeHint().height() < 100, type(widget).__name__
        widget.deleteLater()


def test_registro_de_pix_com_texto_da_loja_nao_vira_html(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    from PyQt6.QtGui import QColor, QImage

    from ui.pages.autobuy import AutobuyPage
    from watcher.autobuy.records import PixRecord

    imagem = tmp_path / "isca.png"
    quadro = QImage(300, 300, QImage.Format.Format_RGB32)
    quadro.fill(QColor("red"))
    quadro.save(str(imagem))
    isca = f'<img src="{imagem.as_uri()}">'

    pagina = AutobuyPage(None)
    pagina._set_test_status("kabum", f"{isca} — à vista no PIX R$ 10,00", "success")
    pagina._expanded.add("20260101-000000")
    registro = PixRecord(
        stamp="20260101-000000", title=isca, price=10.0, url="", pix_code=isca,
        screenshot="", status="pix_ready", created_at="01/01/2026 00:00",
    )
    cartao = pagina._record_card(registro)
    for rotulo in cartao.findChildren(QLabel) + [pagina._widgets["kabum"]]:
        assert rotulo.sizeHint().height() < 100
    cartao.deleteLater()
    pagina.deleteLater()


def test_aviso_do_pix_nao_abre_segunda_conexao_com_o_monitor_ligado(qapp, monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow
    from watcher.autobuy.models import BuyResult, BuyStatus
    from watcher.models import EngineState

    reserva: list[BuyResult] = []

    async def segunda_conexao(_config, resultado):
        reserva.append(resultado)
        return True

    async def envio_falhou(_mensagem, _arquivo=None):
        return False

    monkeypatch.setattr(modulo, "send_pix_notification", segunda_conexao)

    def janela(estado):
        return SimpleNamespace(
            engine=SimpleNamespace(state=estado, notify_me=envio_falhou),
            config=AppConfig(),
            autobuy_page=SimpleNamespace(refresh=lambda: None),
            _toast=lambda *_args, **_kwargs: None,
        )

    resultado = BuyResult(status=BuyStatus.PIX_READY, pix_code="000201")
    asyncio.run(AppWindow._on_autobuy_result(janela(EngineState.ONLINE), resultado))
    assert reserva == []
    asyncio.run(AppWindow._on_autobuy_result(janela(EngineState.OFFLINE), resultado))
    assert reserva == [resultado]


def test_restaurar_a_janela_nao_apaga_o_que_esta_sendo_digitado(qapp):
    from PyQt6.QtGui import QShowEvent

    from ui.pages.settings import SettingsPage

    class RestauradaDoMinimizado(QShowEvent):
        def spontaneous(self) -> bool:
            return True

    app = AppFalso()
    app.config.api_hash = "salvo"
    pagina = SettingsPage(app)
    pagina.api_hash.setText("digitando")
    pagina.showEvent(RestauradaDoMinimizado())
    assert pagina.api_hash.text() == "digitando"
    pagina.showEvent(QShowEvent())
    assert pagina.api_hash.text() == "salvo"
    pagina.deleteLater()
