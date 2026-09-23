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


def test_registro_de_pix_com_texto_da_loja_nao_vira_html(qapp, tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from PyQt6.QtGui import QColor, QImage

    from ui.pages.autobuy import AutobuyPage
    from watcher.autobuy.records import PixRecord

    monkeypatch.chdir(tmp_path)
    quadro = QImage(300, 300, QImage.Format.Format_RGB32)
    quadro.fill(QColor("red"))
    quadro.save("isca.png")
    isca = "<img src=isca.png>"

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
        falsa = SimpleNamespace(
            engine=SimpleNamespace(state=estado, notify_me=envio_falhou),
            config=AppConfig(),
            autobuy_page=SimpleNamespace(refresh=lambda: None),
            _toast=lambda *_args, **_kwargs: None,
        )
        falsa.send_result = lambda resultado: AppWindow.send_result(falsa, resultado)
        return falsa

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


_PAIS: list = []


def _pai():
    from PyQt6.QtWidgets import QWidget

    pai = QWidget()
    pai.resize(1200, 900)
    _PAIS.append(pai)
    return pai


def test_palavra_digitada_sem_enter_entra_no_produto(qapp):
    from ui.watch_dialog import WatchDialog

    dialogo = WatchDialog(None, _pai())
    dialogo.name_input.setText("PS5")
    dialogo.all_chips._input.setText("ps5")
    dialogo.none_chips._input.setText("usado, recondicionado")
    dialogo.max_input.setText("3000")
    assert dialogo.yesButton.isEnabled()
    item = dialogo.result_item()
    assert item.exclude == ["usado", "recondicionado"]
    assert item.all_words == ["ps5"]
    assert not item.matches("PS5 usado por R$ 1.500")
    dialogo.deleteLater()


def test_preco_minimo_acima_do_maximo_nao_deixa_salvar(qapp):
    from ui.watch_dialog import WatchDialog

    dialogo = WatchDialog(None, _pai())
    dialogo.name_input.setText("PS5")
    dialogo.all_chips.set_words(["ps5"])
    dialogo.max_input.setText("300")
    dialogo.min_input.setText("3000")
    assert not dialogo.yesButton.isEnabled()
    assert "mínimo" in dialogo.missing.text()
    dialogo.min_input.setText("100")
    assert dialogo.yesButton.isEnabled()
    assert dialogo.missing.text() == ""
    dialogo.deleteLater()


def test_teste_ao_vivo_olha_link_e_loja_antes_de_dizer_que_compraria(qapp):
    from ui.watch_dialog import WatchDialog

    dialogo = WatchDialog(None, _pai())
    dialogo.all_chips.set_words(["ssd", "2tb"])
    dialogo.max_input.setText("350")
    dialogo.store_combo.setCurrentIndex(dialogo._store_ids.index("kabum"))
    dialogo.test_input.setPlainText("SSD 2TB por R$ 299 no PIX")
    assert dialogo.test_result.text().startswith("✘") and "link" in dialogo.test_result.text()
    dialogo.test_input.setPlainText("SSD 2TB por R$ 299 https://www.amazon.com.br/dp/B0CGBQVDNK")
    assert dialogo.test_result.text().startswith("✘") and "Amazon" in dialogo.test_result.text()
    dialogo.test_input.setPlainText("SSD 2TB por R$ 299 https://www.kabum.com.br/produto/1/ssd")
    assert dialogo.test_result.text().startswith("✔") and "R$ 350,00" in dialogo.test_result.text()
    dialogo.deleteLater()


def _pagina_de_filtros(filtros, confirmar):
    from ui.pages.filters import FiltersPage

    app = AppFalso()
    app.config.destination_ids = [-9]
    app.filters = filtros
    app.perguntas = []
    app.trocados = []

    def confirm(titulo, texto, ok="Confirmar"):
        app.perguntas.append(texto)
        return confirmar

    def toggle_filter(filter_id, enabled):
        app.trocados.append((filter_id, enabled))
        for flt in app.filters:
            if flt.id == filter_id:
                flt.enabled = enabled

    app.confirm = confirm
    app.toggle_filter = toggle_filter
    app.open_filter_editor = lambda _flt: None
    app.delete_filter = lambda _id: None
    return app, FiltersPage(app)


def test_desligar_o_ultimo_filtro_avisa_que_tudo_sera_replicado(qapp):
    from ui.pages.filters import REPLICA_TUDO, _FilterCard
    from watcher.models import Filter

    app, pagina = _pagina_de_filtros([Filter.from_dict({"id": "a", "name": "Cupons", "include": ["cupom"]})], False)
    cartao = pagina.findChildren(_FilterCard)[0]
    cartao.switch.setChecked(False)
    assert app.perguntas and "replicado" in app.perguntas[0]
    assert app.trocados == []
    assert cartao.switch.isChecked()
    assert not any(REPLICA_TUDO == w.text() for w in pagina.findChildren(QLabel))

    app.filters[0].enabled = False
    pagina.refresh()
    assert any(REPLICA_TUDO == w.text() for w in pagina.findChildren(QLabel))
    pagina.deleteLater()


def test_desligar_filtro_com_outro_ligado_nao_pergunta(qapp):
    from ui.pages.filters import _FilterCard
    from watcher.models import Filter

    filtros = [
        Filter.from_dict({"id": "a", "name": "Cupons", "include": ["cupom"]}),
        Filter.from_dict({"id": "b", "name": "SSD", "include": ["ssd"]}),
    ]
    app, pagina = _pagina_de_filtros(filtros, False)
    pagina.findChildren(_FilterCard)[0].switch.setChecked(False)
    assert app.perguntas == []
    assert app.trocados == [("a", False)]
    pagina.deleteLater()


def test_conversa_com_monitor_desligado_nao_diz_que_esta_vazia(qapp):
    from ui.pages.messages import OFFLINE_TEXT, MessagesPage

    app = AppFalso()
    app.engine.reader.available = False
    pagina = MessagesPage(app)
    pagina.open_chat(-1)
    textos = [w.text() for w in pagina.findChildren(QLabel)]
    assert OFFLINE_TEXT in textos
    assert not any("Nenhuma mensagem" in t for t in textos)
    assert not pagina.thread_search.isEnabled()
    assert app.tarefas == []
    pagina.deleteLater()


def test_conversa_aberta_nao_volta_a_mostrar_nao_lidas(qapp):
    from ui.pages.messages import MessagesPage
    from watcher.reader import ChatSummary

    app = AppFalso()
    app.engine.reader.portao(-1).set()

    async def cenario():
        pagina = MessagesPage(app)
        pagina._summaries = [ChatSummary(id=-1, name="Pelando", unread=4), ChatSummary(id=-2, name="Gatry", unread=2)]
        pagina._render_chats()
        pagina.open_chat(-1)
        await _esperar_tarefas(app)
        pagina.search.setText("pel")
        pagina.search.setText("")
        pagina.scope.setCurrentIndex(3)
        return pagina

    pagina = asyncio.run(cenario())
    assert list(pagina._rows) == [-2]
    pagina.scope.setCurrentIndex(0)
    assert pagina._rows[-1]._badge.text() == ""
    pagina.deleteLater()


def test_marca_de_filtro_segue_a_decisao_do_monitor(qapp):
    from ui.pages.messages import MessagesPage
    from watcher.models import Filter

    app = AppFalso()
    app.config.source_ids = [-1]
    app.config.destination_ids = [-9]
    app.filters = [Filter.from_dict({"id": "a", "name": "Cupons", "include": ["cupom"]})]
    pagina = MessagesPage(app)
    origem = pagina._marks(ChatMessage(id=1, chat_id=-1, text="cupom aqui"))
    fora = pagina._marks(ChatMessage(id=2, chat_id=-9, text="cupom aqui"))
    assert ("Filtro Cupons", "filter") in origem
    assert all(tom != "filter" for _texto, tom in fora)
    assert any("não é origem" in texto for texto, _tom in fora)

    app.filters[0].enabled = False
    sem_filtro = pagina._marks(ChatMessage(id=3, chat_id=-1, text="bom dia"))
    assert ("Replicaria: nenhum filtro ativo", "warning") in sem_filtro
    pagina.deleteLater()


def test_conversas_cabe_em_tela_de_notebook_com_conversa_aberta(qapp):
    from ui.pages.messages import MessagesPage
    from watcher.reader import ChatSummary

    app = AppFalso()
    app.engine.reader.portao(-1).set()
    longo = "Canal de Promoções e Cupons do Brasil Oficial " * 3

    async def cenario():
        pagina = MessagesPage(app)
        pagina._summaries = [ChatSummary(id=-1, name=longo, username="canal_com_nome_bem_comprido", participants=84000)]
        pagina._render_chats()
        pagina.open_chat(-1)
        await _esperar_tarefas(app)
        return pagina

    pagina = asyncio.run(cenario())
    assert pagina.minimumSizeHint().width() <= 900
    pagina.deleteLater()


def test_erro_longo_no_feed_nao_alarga_o_dashboard(qapp):
    from ui.pages.dashboard import _FeedRow

    linha = _FeedRow({"time": "10:00", "source": "Canal " * 30, "text": "Erro: " + "x" * 300, "status": "failed"})
    assert linha.minimumSizeHint().width() < 400
    linha.deleteLater()


def test_canais_em_portugues_e_aviso_de_lista_cortada(qapp):
    from ui.pages.channels import ChannelsPage
    from watcher.models import Channel, ChannelKind

    app = AppFalso()
    app.channels = [Channel(-i, f"Canal {i}", ChannelKind.GROUP) for i in range(1, 251)]
    app.request_sync = lambda: None
    pagina = ChannelsPage(app)
    textos = [w.text() for w in pagina.findChildren(QLabel)]
    assert any(t.startswith("Grupo") for t in textos)
    assert not any(t.startswith("group") for t in textos)
    assert any("Mostrando 200 de 250" in t for t in textos)
    pagina.search.setText("nao existe")
    textos = [w.text() for w in pagina.findChildren(QLabel)]
    assert "Nenhum canal com esse nome" in textos
    pagina.deleteLater()


def test_interruptores_de_comportamento_valem_na_hora(qapp):
    from PyQt6.QtGui import QShowEvent

    from ui.pages.settings import SettingsPage

    app = AppFalso()
    gravados: list[tuple[bool, bool]] = []

    def save_behavior(allow_self, auto_start):
        gravados.append((allow_self, auto_start))
        app.config.allow_self = allow_self
        app.config.auto_start = auto_start

    app.save_behavior = save_behavior
    pagina = SettingsPage(app)
    pagina.sw_auto.setChecked(True)
    assert gravados == [(False, True)]
    pagina.showEvent(QShowEvent())
    assert pagina.sw_auto.isChecked()
    assert gravados == [(False, True)]
    pagina.deleteLater()


def _janela_falsa(**extra):
    from ui.window import AppWindow

    avisos: list[tuple] = []
    apagadas: list[str] = []
    falsa = SimpleNamespace(
        config=AppConfig(api_id=123, api_hash="abc", phone="+55 11 99999-0000", bot_token="bot:1"),
        engine=SimpleNamespace(is_running=False, forget_session=apagadas.append),
        channels_page=SimpleNamespace(refresh=lambda: None),
        dashboard=SimpleNamespace(refresh_account=lambda: None),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
        avisos=avisos,
        apagadas=apagadas,
        **extra,
    )
    for nome in ("_forget_sessions", "_erase_sessions"):
        setattr(falsa, nome, getattr(AppWindow, nome).__get__(falsa))
    return falsa


def _dados(**mudancas):
    base = {"api_id": "123", "api_hash": "abc", "bot_token": "bot:1", "phone": "+5511999990000"}
    base.update(mudancas)
    return {**base, "allow_self": False, "auto_start": False}


def test_trocar_o_telefone_apaga_a_sessao_da_conta_antiga(qapp, monkeypatch):
    from ui.window import AppWindow

    monkeypatch.setattr(AppConfig, "save", lambda self: True)
    janela = _janela_falsa()
    assert AppWindow.save_settings(janela, _dados()) is True
    assert janela.apagadas == []
    assert AppWindow.save_settings(janela, _dados(phone="+55 21 98888-7777")) is True
    assert janela.apagadas == ["user"]
    assert AppWindow.save_settings(janela, _dados(phone="+55 21 98888-7777", bot_token="bot:2")) is True
    assert janela.apagadas == ["user", "bot"]


def test_api_id_com_letra_nao_e_salvo(qapp, monkeypatch):
    from ui.window import AppWindow

    salvos: list[AppConfig] = []
    monkeypatch.setattr(AppConfig, "save", lambda self: salvos.append(self) or True)
    janela = _janela_falsa()
    assert AppWindow.save_settings(janela, _dados(api_id="12a45")) is False
    assert salvos == []
    assert janela.config.api_id == 123
    assert janela.avisos and janela.avisos[-1][1] == "error"


def test_compra_que_falhou_por_sessao_avisa_com_bipe_e_balao(qapp):
    from ui.window import AppWindow
    from watcher.autobuy.models import BuyResult, BuyStatus

    alertas: list[str] = []
    avisos: list[tuple] = []

    async def enviado(_resultado):
        return True

    falsa = SimpleNamespace(
        send_result=enviado,
        autobuy_page=SimpleNamespace(refresh=lambda: None),
        alert=alertas.append,
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    asyncio.run(AppWindow._on_autobuy_result(falsa, BuyResult(status=BuyStatus.LOGIN_REQUIRED)))
    assert alertas and "NÃO feita" in alertas[0]
    asyncio.run(AppWindow._on_autobuy_result(falsa, BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, detail="R$ 10 acima")))
    assert avisos[-1][1] == "warning" and "R$ 10 acima" in avisos[-1][0]


def test_excluir_filtro_pede_confirmacao(qapp):
    from ui.window import AppWindow
    from watcher.models import Filter

    salvos: list = []
    falsa = SimpleNamespace(
        filters=[Filter.from_dict({"id": "a", "name": "Cupons"})],
        confirm=lambda *_args: False,
        filter_store=SimpleNamespace(save=salvos.append),
    )
    AppWindow.delete_filter(falsa, "a")
    assert len(falsa.filters) == 1 and salvos == []


def test_bandeja_mostra_reconectando(qapp):
    from PyQt6.QtGui import QIcon

    from ui.tray import SystemTray
    from watcher.models import EngineState

    bandeja = SystemTray(QIcon(), lambda: None, lambda: None, lambda: None)
    bandeja.set_state(EngineState.RECONNECTING)
    assert "Reconectando" in bandeja.toolTip()
    bandeja.set_state(EngineState.AUTH)
    assert "Autenticando" in bandeja.toolTip()
    bandeja.deleteLater()


def test_assistente_abre_com_os_dados_salvos(qapp):
    from ui.setup_wizard import SetupWizard

    app = AppFalso()
    app.config = AppConfig(api_id=123, api_hash="abc", phone="+5511", bot_token="bot:1")
    app.engine.is_running = False
    assistente = SetupWizard(app, _pai())
    assert assistente.api_id.text() == "123"
    assert assistente.api_hash.text() == "abc"
    assert assistente.phone.text() == "+5511"
    assert assistente.bot_token.text() == "bot:1"
    assistente.deleteLater()


def test_assistente_para_o_monitor_antes_de_salvar_e_mostra_o_erro_real(qapp):
    from ui.setup_wizard import SetupWizard
    from watcher.models import EngineState

    passos: list[str] = []

    class Motor:
        is_running = True
        state = EngineState.OFFLINE
        last_error = "Telefone inválido. Use o número com código do país, ex: +55 11 99999-9999."

        async def stop(self):
            passos.append("stop")
            self.is_running = False

        async def start(self):
            passos.append("start")
            return False

    app = AppFalso()
    app.config = AppConfig(api_id=1, api_hash="x", phone="+55")
    app.engine = Motor()
    app.save_settings = lambda payload, silent=False: passos.append("save") or True
    assistente = SetupWizard(app, _pai())
    asyncio.run(assistente._connection_task())
    assert passos == ["stop", "save", "start"]
    assert "Telefone inválido" in assistente._hint.text()
    assistente.deleteLater()


def test_teste_de_compra_real_nao_fica_lembrado(qapp, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from ui.pages import autobuy as modulo
    from ui.pages.autobuy import AutobuyPage

    class DialogoFalso:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return True

        def result_url(self):
            return "https://www.kabum.com.br/produto/1/x"

        def result_mode(self):
            return 2

        def result_coupon(self):
            return ""

    monkeypatch.setattr(modulo, "TestDialog", DialogoFalso)
    app = AppFalso()
    perguntas: list[str] = []
    app.confirm = lambda _titulo, texto, _ok="": perguntas.append(texto) or False
    app.schedule = lambda coro: coro.close()
    pagina = AutobuyPage(None)
    pagina._app = app
    pagina._open_test("kabum")
    assert perguntas and "de verdade" in perguntas[0]
    assert not pagina._running
    app.confirm = lambda *_args: True
    pagina._open_test("kabum")
    assert pagina._running
    assert pagina._test_mode["kabum"] == 0
    pagina.deleteLater()


def test_valores_em_reais_e_inteiros_no_formato_brasileiro():
    from ui.common import format_brl, format_int

    assert format_brl(1234.5) == "R$ 1.234,50"
    assert format_brl(0) == "R$ 0,00"
    assert format_brl(1234567.891) == "R$ 1.234.567,89"
    assert format_int(1234567) == "1.234.567"
    assert format_int(12) == "12"


def test_texto_da_conversa_vira_link_sem_abrir_brecha_de_html():
    from ui.pages.messages import _rich

    saida = _rich('<b>oi</b> veja https://loja.example/p?a=1&b=2 e www.site.com\nfim "https://x.com/"onmouseover=alert(1)')
    assert "<b>" not in saida and "&lt;b&gt;oi&lt;/b&gt;" in saida
    assert 'href="https://loja.example/p?a=1&amp;b=2"' in saida
    assert 'href="https://www.site.com"' in saida
    assert "<br>fim" in saida
    assert 'href="https://x.com/"' in saida
    assert "onmouseover" not in saida.split("&quot;onmouseover")[0]
    assert _rich("") == ""


def test_iniciais_do_avatar():
    from ui.pages.messages import _initials

    assert _initials("") == "?"
    assert _initials("   ") == "?"
    assert _initials("promo") == "PR"
    assert _initials("ana souza silva") == "AS"


def test_rotulo_do_dia_nas_conversas():
    from datetime import date, timedelta

    from ui.pages.messages import _MONTHS, _WEEKDAYS, _day_label

    hoje = date.today()
    assert _day_label(hoje) == "Hoje"
    assert _day_label(hoje - timedelta(days=1)) == "Ontem"
    tres = hoje - timedelta(days=3)
    assert _day_label(tres) == _WEEKDAYS[tres.weekday()].capitalize()
    antigo = hoje - timedelta(days=400)
    assert _day_label(antigo) == f"{antigo.day} de {_MONTHS[antigo.month - 1]} de {antigo.year}"


def test_hora_da_ultima_mensagem_na_lista():
    from datetime import datetime, timedelta

    from ui.pages.messages import _list_stamp

    agora = datetime.now().astimezone().replace(hour=10, minute=5)
    assert _list_stamp(None) == ""
    assert _list_stamp(agora) == "10:05"
    assert _list_stamp(agora - timedelta(days=1)) == "ontem"
    mes = agora - timedelta(days=40)
    assert _list_stamp(mes) == mes.strftime("%d/%m")
    velho = agora - timedelta(days=400)
    assert _list_stamp(velho) == velho.strftime("%m/%Y")


def test_resumo_do_cartao_de_filtro():
    from ui.pages.filters import _catch_all_note, _smart_label, _summary
    from watcher.models import Filter

    completo = Filter(
        id="a", source_ids=[-1, -2], include=["cupom", "frete"], exclude=["shein"],
        smart_rules=["any_coupon", "store:shopee", "store:sumiu", "hot"],
    )
    resumo = _summary(completo)
    assert resumo.split("   ·   ") == ["2 origem(ns)", "destino padrão", "2 palavra(s)", "cupom, Shopee, loja, hot", "exclui 1"]
    assert _summary(Filter(id="b", destination_ids=[-9])).startswith("todas as origens   ·   1 destino(s)")
    assert _smart_label([]) == ""
    assert _catch_all_note(completo) == ""
    assert "não replica nada" in _catch_all_note(Filter(id="c", exclude=["shein"]))
    assert "Replica tudo" in _catch_all_note(Filter(id="d", include=["  "]))
    assert "Replica tudo" in _catch_all_note(Filter(id="e", exclude=["", "  "]))


def test_canais_marcados_aparecem_primeiro_sem_repetir(qapp):
    from ui.pages.channels import ChannelsPage
    from watcher.models import Channel, ChannelKind

    app = AppFalso()
    app.config.source_ids = [-5, -1]
    app.config.destination_ids = [-1, -9]
    app.config.aliases = {-9: "Destino Antigo"}
    app.channels = [Channel(-1, "Ofertas", ChannelKind.GROUP), Channel(-7, "Outro"), Channel(-5, "Cupons")]
    app.request_sync = lambda: None
    pagina = ChannelsPage(app)
    mostrados = pagina._display_channels()
    assert [(c.id, c.kind) for c in mostrados] == [
        (-5, ChannelKind.CHANNEL),
        (-1, ChannelKind.GROUP),
        (-9, ChannelKind.CHANNEL),
        (-7, ChannelKind.CHANNEL),
    ]
    assert [c.name for c in mostrados][2] == "Destino Antigo"
    pagina.search.setText("-7")
    assert "Outro" in [w.text() for w in pagina.findChildren(QLabel)]
    pagina.deleteLater()


def _janela_de_canais(filtros=()):
    from ui.window import AppWindow

    avisos: list[tuple] = []
    config = AppConfig()
    config.save = lambda: True
    falsa = SimpleNamespace(
        config=config,
        filters=list(filtros),
        filters_page=SimpleNamespace(refresh=lambda: None),
        dashboard=SimpleNamespace(refresh=lambda: None),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
        avisos=avisos,
    )
    falsa.papel = lambda chat_id, nome, papel, ligado: AppWindow.set_channel_role(falsa, chat_id, nome, papel, ligado)
    return falsa


def test_apelido_do_canal_some_so_quando_ele_nao_tem_mais_papel():
    janela = _janela_de_canais()
    janela.papel(-1, "Ofertas", "source", True)
    janela.papel(-1, "Ofertas", "source", True)
    janela.papel(-1, "Nome Novo", "destination", True)
    assert (janela.config.source_ids, janela.config.destination_ids) == ([-1], [-1])
    assert janela.config.aliases == {-1: "Ofertas"}
    janela.papel(-1, "Ofertas", "source", False)
    assert janela.config.aliases == {-1: "Ofertas"}
    janela.papel(-1, "Ofertas", "destination", False)
    assert (janela.config.source_ids, janela.config.destination_ids, janela.config.aliases) == ([], [], {})


def test_desmarcar_canal_usado_por_filtro_avisa_o_efeito():
    from watcher.models import Filter

    filtros = [
        Filter(id="a", name="Cupons", destination_ids=[-9]),
        Filter(id="b", name="SSD", source_ids=[-9]),
    ]
    janela = _janela_de_canais(filtros)
    janela.papel(-9, "Destino", "destination", True)
    assert janela.avisos == []
    janela.papel(-9, "Destino", "destination", False)
    mensagem, tipo = janela.avisos[-1]
    assert tipo == "warning"
    assert "destino dos filtros Cupons" in mensagem and "SSD" not in mensagem
    janela.papel(-9, "Destino", "source", True)
    janela.papel(-9, "Destino", "source", False)
    assert "origem dos filtros SSD" in janela.avisos[-1][0]


def test_excluir_filtro_confirmado_salva_e_atualiza_as_telas():
    from ui.window import AppWindow
    from watcher.models import Filter

    salvos: list = []
    atualizados: list[str] = []
    avisos: list[tuple] = []
    falsa = SimpleNamespace(
        filters=[Filter(id="a", name="Cupons"), Filter(id="b", name="SSD")],
        confirm=lambda *_args: True,
        filter_store=SimpleNamespace(save=lambda filtros: salvos.append([f.id for f in filtros])),
        filters_page=SimpleNamespace(refresh=lambda: atualizados.append("filtros")),
        messages_page=SimpleNamespace(refresh_marks=lambda: atualizados.append("conversas")),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    AppWindow.delete_filter(falsa, "a")
    assert [f.id for f in falsa.filters] == ["b"]
    assert salvos == [["b"]]
    assert atualizados == ["filtros", "conversas"]
    AppWindow.toggle_filter(falsa, "b", False)
    assert falsa.filters[0].enabled is False
    assert salvos == [["b"], ["b"]]


def test_comando_de_inicializacao_do_windows_entre_aspas(monkeypatch):
    import sys

    from ui.window import TRAY_FLAG, AppWindow

    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Telegram Watcher\TelegramWatcher.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert AppWindow._startup_command(None) == f'"{sys.executable}" {TRAY_FLAG}'
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(sys, "executable", r"C:\Python 3\python.exe")
    comando = AppWindow._startup_command(None)
    assert comando.startswith('"C:\\Python 3\\pythonw.exe" "')
    assert comando.endswith(f'app.py" {TRAY_FLAG}')


def test_editar_produto_sem_mudar_nada_devolve_o_mesmo_produto(qapp):
    from ui.watch_dialog import WatchDialog
    from watcher.autobuy.models import WatchItem

    original = WatchItem(
        id="w1", name="SSD 2TB", max_price=1299.9, include=["ssd + 2tb + bug ou erro"], exclude=["usado"],
        quantity=3, enabled=False, store="amazon", min_price=100.0, disable_after_buy=False,
        all_words=["ssd", "2tb"], any_words=["bug", "erro"],
    )
    dialogo = WatchDialog(original, _pai())
    assert dialogo.max_input.text() == "1299,90"
    assert dialogo.yesButton.isEnabled()
    assert dialogo.result_item() == original
    dialogo.deleteLater()


def test_produto_antigo_com_mais_aberto_no_editor_continua_comprando(qapp):
    from ui.watch_dialog import WatchDialog
    from watcher.autobuy.models import WatchItem

    antigo = WatchItem(id="w1", name="Placas", max_price=2500.0, include=["rtx + 5060", "rx + 7600"])
    dialogo = WatchDialog(antigo, _pai())
    dialogo.test_input.setPlainText("RTX 5060 Ventus por R$ 2.199 https://www.kabum.com.br/produto/1/rtx")
    assert dialogo.test_result.text().startswith("✔")
    salvo = dialogo.result_item()
    assert salvo.matches("RTX 5060 Ventus por R$ 2.199")
    assert not salvo.matches("Suporte para 5060 e 7600")
    dialogo.deleteLater()


def test_editar_filtro_sem_mudar_nada_devolve_o_mesmo_filtro(qapp):
    from ui.dialogs import FilterDialog
    from watcher.models import Filter

    config = AppConfig(source_ids=[-1, -2], destination_ids=[-9], aliases={-1: "Ofertas"})
    original = Filter(
        id="f1", name="Cupons", enabled=False, source_ids=[-2, -3], destination_ids=[-9],
        include=["cupom + meli", "frete"], exclude=["shein", "esgotado"],
        smart_rules=["any_coupon", "discount>=30", "store:shopee"], min_confidence=0.4,
    )
    dialogo = FilterDialog(config, original, _pai())
    assert dialogo._source_checks[-3].text() == "ID -3 (não é mais origem)"
    assert dialogo._source_checks[-1].text() == "Ofertas" and not dialogo._source_checks[-1].isChecked()
    resultado = dialogo.result_filter()
    assert set(resultado.source_ids) == {-2, -3}
    assert set(resultado.smart_rules) == set(original.smart_rules)
    resultado.source_ids = original.source_ids
    resultado.smart_rules = original.smart_rules
    assert resultado == original
    dialogo.deleteLater()


def test_dica_do_campo_incluir_segue_o_tipo_de_filtro(qapp):
    from ui.dialogs import FilterDialog

    dialogo = FilterDialog(AppConfig(), None, _pai())
    assert dialogo.include_caption.text() == FilterDialog._INCLUDE_ALL
    dialogo.exclude_input.setText("shein")
    assert dialogo.include_caption.text() == FilterDialog._INCLUDE_BLOCK
    dialogo.include_input.setText("cupom")
    assert dialogo.include_caption.text() == FilterDialog._INCLUDE_ALL
    dialogo.store_combo.setCurrentIndex(1)
    assert dialogo.include_caption.text() == FilterDialog._INCLUDE_SMART
    novo = dialogo.result_filter()
    assert novo.include == ["cupom"] and novo.exclude == ["shein"]
    assert novo.smart_rules == [f"store:{dialogo._store_ids[1]}"]
    assert novo.name == "Sem nome" and novo.enabled
    dialogo.deleteLater()


def test_teste_da_loja_so_aceita_link_da_propria_loja(qapp):
    from ui.test_dialog import BUY_MODE, TestDialog

    dialogo = TestDialog("kabum", parent=_pai())
    assert not dialogo.yesButton.isEnabled()
    dialogo.url_input.setText("https://www.amazon.com.br/dp/B0CGBQVDNK")
    assert not dialogo.yesButton.isEnabled() and "Amazon" in dialogo.url_hint.text()
    dialogo.url_input.setText("https://golpe.example/kabum.com.br/produto/1")
    assert not dialogo.yesButton.isEnabled() and "não reconhecido" in dialogo.url_hint.text()
    dialogo.url_input.setText("  https://www.kabum.com.br/produto/1/ssd  ")
    assert dialogo.yesButton.isEnabled()
    assert dialogo.result_url() == "https://www.kabum.com.br/produto/1/ssd"
    dialogo.coupon_input.setText(" bemvindo10 ")
    assert dialogo.result_coupon() == "BEMVINDO10"
    dialogo.mode_combo.setCurrentIndex(BUY_MODE)
    assert dialogo.yesButton.text() == "Comprar e gerar PIX"
    assert "COMPRA DE VERDADE" in dialogo.mode_hint.text()
    dialogo.deleteLater()


def test_palavras_do_produto_sem_repetir_e_removiveis(qapp):
    from ui.chips import WordChips

    chips = WordChips("t", "h", "p")
    mudancas: list[bool] = []
    chips.changed.connect(lambda: mudancas.append(True))
    chips.set_words(["SSD", "ssd", " 2tb, ", "", "Bug  de   preço"])
    assert chips.words() == ["SSD", "2tb", "Bug de preço"]
    chips._input.setText("nvme, NVME, ssd")
    assert chips.words() == ["SSD", "2tb", "Bug de preço", "nvme"]
    chips._commit()
    assert chips._input.text() == ""
    assert chips.words() == ["SSD", "2tb", "Bug de preço", "nvme"]
    chips._remove("2tb")
    assert chips.words() == ["SSD", "Bug de preço", "nvme"]
    assert mudancas
    chips.deleteLater()


def test_console_filtra_por_nivel_e_nao_interpreta_html(qapp):
    from ui.pages.console import ConsolePage
    from watcher.models import LogLevel

    pagina = ConsolePage(None)
    pagina.append("<b>Capturado</b> de <i>Canal</i>", LogLevel.CAPTURE)
    pagina.append("Falha em Destino", LogLevel.ERROR)
    assert "<b>Capturado</b> de <i>Canal</i>" in pagina.view.toPlainText()
    pagina.combo.setCurrentIndex(4)
    assert pagina.view.toPlainText().count("\n") == 0
    assert "Falha em Destino" in pagina.view.toPlainText()
    assert "Capturado" not in pagina.view.toPlainText()
    pagina.append("Outra captura", LogLevel.CAPTURE)
    assert "Outra captura" not in pagina.view.toPlainText()
    pagina.combo.setCurrentIndex(0)
    assert "Outra captura" in pagina.view.toPlainText()
    pagina.clear()
    assert pagina.view.toPlainText() == "" and pagina._buffer == []
    pagina.deleteLater()


def _pagina_autobuy(tmp_path, confirmar=True, **settings):
    from ui.pages.autobuy import AutobuyPage
    from watcher.autobuy.manager import AutobuyManager
    from watcher.autobuy.storage import AutobuySettings, AutobuyStore

    store = AutobuyStore(tmp_path / "autobuy.json")
    store.save(AutobuySettings(**settings), [])
    app = AppFalso()
    app.autobuy = AutobuyManager(store)
    app.perguntas = []
    app.confirm = lambda _titulo, texto, _ok="": app.perguntas.append(texto) or confirmar
    return AutobuyPage(app), app, store


def test_ligar_compra_real_sem_confirmar_volta_para_simulacao(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    pagina, app, store = _pagina_autobuy(tmp_path, confirmar=False, enabled=True)
    pagina.mode_combo.setCurrentIndex(1)
    assert app.perguntas and "de verdade" in app.perguntas[0]
    assert pagina.mode_combo.currentIndex() == 0
    assert store.load()[0].real_mode is False
    assert pagina.state_label.text().startswith("Simulação")
    pagina.deleteLater()


def test_ligar_compra_real_confirmada_fica_salva_e_avisa_na_tela(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    pagina, _app, store = _pagina_autobuy(tmp_path, confirmar=True, enabled=True)
    pagina.mode_combo.setCurrentIndex(1)
    assert store.load()[0].real_mode is True
    assert pagina.state_label.text().startswith("Compra real ativa")
    pagina.mode_combo.setCurrentIndex(0)
    assert store.load()[0].real_mode is False
    pagina.deleteLater()


def test_produto_ligado_com_autobuy_desligado_aparece_pausado(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy.models import WatchItem

    pagina, app, _store = _pagina_autobuy(tmp_path, enabled=False)
    app.autobuy.upsert_item(WatchItem(id="w", name="SSD", max_price=300.0, include=["ssd"], store="kabum"))
    cartao = pagina._item_card(app.autobuy.items[0])
    textos = [w.text() for w in cartao.findChildren(QLabel)]
    assert any(t.startswith("Pausado") for t in textos)
    assert any("R$ 300,00" in t and "KaBuM!" in t for t in textos)
    app.autobuy.set_enabled(True)
    ligado = pagina._item_card(app.autobuy.items[0])
    textos = [w.text() for w in ligado.findChildren(QLabel)]
    assert not any(t.startswith("Pausado") for t in textos)
    cartao.deleteLater()
    ligado.deleteLater()
    pagina.deleteLater()


def test_situacao_da_conta_de_cada_loja(qapp, tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    import time as relogio

    from watcher.autobuy import browser

    pagina, app, _store = _pagina_autobuy(tmp_path)
    prazo = [None]
    monkeypatch.setattr(browser, "session_expiry", lambda _dominio, _nomes: prazo[0])
    manager = app.autobuy
    assert pagina._store_status_text("kabum") == "conexão não verificada"
    manager.checked.add("kabum")
    assert pagina._store_status_text("kabum") == "não conectado"
    manager.accounts["kabum"] = "VINICIUS"
    assert pagina._store_status_text("kabum") == "conectado como VINICIUS (sem prazo fixo: expira por inatividade)"
    prazo[0] = relogio.time() - 10
    assert pagina._store_status_text("kabum").endswith("(cookie expirado)")
    prazo[0] = relogio.time() + 5 * 3600 + 60
    assert pagina._store_status_text("kabum").endswith("(expira em 5 horas)")
    prazo[0] = relogio.time() + 10 * 86400 + 60
    assert pagina._store_status_text("kabum").endswith("(expira em 10 dias)")
    manager.accounts["kabum"] = "conta salva"
    assert pagina._store_status_text("kabum").startswith("conta salva (expira em 10 dias)")
    manager.busy_profile = True
    assert "Chrome for Testing" in pagina._store_status_text("kabum")
    pagina._checking.add("kabum")
    assert pagina._store_status_text("kabum") == "verificando conexão..."
    pagina.deleteLater()


def test_assistente_nao_avanca_com_dado_invalido(qapp):
    from ui.setup_wizard import SetupWizard

    app = AppFalso()
    app.engine.is_running = False
    assistente = SetupWizard(app, _pai())
    assistente._on_next()
    assert assistente._index == 1
    assistente.api_id.setText("12a")
    assistente.api_hash.setText("abc")
    assistente._on_next()
    assert assistente._index == 1 and "somente números" in assistente._hint.text()
    assistente.api_id.setText(" 123 ")
    assistente.api_hash.setText("  ")
    assistente._on_next()
    assert assistente._index == 1 and "API Hash" in assistente._hint.text()
    assistente.api_hash.setText("abc")
    assistente._on_next()
    assert assistente._index == 2 and assistente._hint.text() == ""
    assistente.phone.setText("+55 11")
    assistente._on_next()
    assert assistente._index == 2 and "telefone" in assistente._hint.text()
    assistente.phone.setText("+55 11 99999-0000")
    assistente._on_next()
    assert assistente._index == 3
    assert assistente.yesButton.text() == "Conectar ao Telegram"
    assistente._on_back()
    assert assistente._index == 2 and assistente.cancelButton.text() == "Voltar"
    assistente.deleteLater()


def test_marca_de_filtro_sem_destino_marcado(qapp):
    from ui.pages.messages import MessagesPage
    from watcher.models import Filter

    app = AppFalso()
    app.config.source_ids = [-1]
    app.config.destination_ids = [-9]
    app.filters = [Filter.from_dict({"id": "a", "name": "Cupons", "include": ["cupom"], "destination_ids": [-50]})]
    pagina = MessagesPage(app)
    marcas = pagina._marks(ChatMessage(id=1, chat_id=-1, text="Use o cupom MELI50 no Mercado Livre"))
    assert ("Filtro Cupons, sem destino", "muted") in marcas
    assert ("MELI50", "detect") in marcas
    assert ("Mercado Livre", "detect") in marcas
    assert pagina._marks(ChatMessage(id=2, chat_id=-1, text="")) == []
    app.config.destination_ids = []
    app.filters[0].enabled = False
    assert pagina._marks(ChatMessage(id=3, chat_id=-1, text="bom dia")) == []
    pagina.deleteLater()


def test_painel_explica_o_que_falta_para_ver_atividade(qapp):
    from ui.pages.dashboard import DashboardPage
    from watcher.engine import MonitorEngine
    from watcher.models import EngineState

    app = AppFalso()
    app.engine = MonitorEngine(app.config, lambda: [])
    app.open_setup_wizard = lambda: None
    app.toggle_engine = lambda: None
    app.open_conversation = lambda _chat_id: None
    pagina = DashboardPage(app)
    assert pagina._empty.text().startswith("Nenhuma atividade ainda")
    assert pagina.toggle_btn.text() == "Iniciar monitor" and pagina.account_btn.text() == "Conectar conta"
    pagina.set_state(EngineState.CONNECTING)
    assert not pagina.toggle_btn.isEnabled()
    pagina.set_state(EngineState.ONLINE)
    assert pagina.toggle_btn.text() == "Parar monitor" and pagina.toggle_btn.isEnabled()
    assert pagina._empty.text().startswith("Nenhuma origem marcada")
    app.config.source_ids = [-1]
    pagina.refresh()
    assert pagina._empty.text() == "Aguardando mensagens de 1 origem."
    for indice in range(12):
        pagina.add_event({"time": "10:00", "source": f"Canal {indice}", "text": "x", "status": "captured"})
    assert pagina._empty is None
    assert pagina._feed.count() == 9
    assert pagina.feed_count.text() == "12 eventos"
    pagina.deleteLater()


def _janela_do_monitor(pronto=True, ligado=False):
    passos: list = []

    class Motor:
        is_running = ligado

        async def start(self, insistir=False):
            passos.append(("start", insistir))

        async def stop(self):
            passos.append("stop")

    return SimpleNamespace(
        engine=Motor(),
        config=AppConfig(api_id=1, api_hash="h", phone="+55") if pronto else AppConfig(),
        autobuy=SimpleNamespace(
            start_watchdog=lambda: passos.append("vigia ligado"),
            stop_watchdog=lambda: passos.append("vigia parado"),
        ),
        open_setup_wizard=lambda: passos.append("assistente"),
        schedule=asyncio.run,
        passos=passos,
    )


def test_botao_do_monitor_liga_desliga_ou_abre_o_assistente():
    from ui.window import AppWindow

    desligado = _janela_do_monitor()
    AppWindow.toggle_engine(desligado)
    assert desligado.passos == [("start", False), "vigia ligado"]
    ligado = _janela_do_monitor(ligado=True)
    AppWindow.toggle_engine(ligado)
    assert ligado.passos == ["vigia parado", "stop"]
    sem_conta = _janela_do_monitor(pronto=False)
    AppWindow.toggle_engine(sem_conta)
    assert sem_conta.passos == ["assistente"]


def test_inicio_automatico_so_liga_monitor_parado_e_com_conta():
    from ui.window import AppWindow

    for janela in (_janela_do_monitor(ligado=True), _janela_do_monitor(pronto=False)):
        AppWindow.auto_start(janela)
        assert janela.passos == []
    janela = _janela_do_monitor()
    AppWindow.auto_start(janela)
    assert janela.passos == [("start", True), "vigia ligado"]


def test_sincronizar_canais_so_troca_a_lista_quando_vem_algo_e_sempre_libera_o_botao():
    from ui.window import AppWindow
    from watcher.models import Channel

    respostas: list = [[], [Channel(-2, "Novo")]]

    async def sincroniza():
        return respostas.pop(0)

    salvos: list = []
    estados: list[bool] = []
    avisos: list[tuple] = []
    janela = SimpleNamespace(
        channels=[Channel(-1, "Antigo")],
        engine=SimpleNamespace(sync_channels=sincroniza, is_running=True),
        channel_cache=SimpleNamespace(save=salvos.append),
        channels_page=SimpleNamespace(refresh=lambda: None, set_syncing=estados.append),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
        schedule=asyncio.run,
    )
    janela._sync_channels = lambda: AppWindow._sync_channels(janela)
    AppWindow.request_sync(janela)
    assert [c.id for c in janela.channels] == [-1]
    assert salvos == [] and avisos == []
    assert estados == [True, False]
    AppWindow.request_sync(janela)
    assert [c.id for c in janela.channels] == [-2]
    assert [[c.id for c in lista] for lista in salvos] == [[-2]]
    assert avisos == [("1 canais sincronizados", "success")]
    janela.engine.is_running = False
    AppWindow.request_sync(janela)
    assert avisos[-1] == ("Inicie o monitor para sincronizar os canais", "warning")
    assert estados == [True, False, True, False]


def test_editor_de_filtro_troca_o_existente_ou_acrescenta_e_salva(monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow
    from watcher.models import Filter

    resposta = {"aceito": True, "filtro": None}

    class DialogoFalso:
        def __init__(self, _config, _filtro, _pai):
            pass

        def exec(self):
            return resposta["aceito"]

        def result_filter(self):
            return resposta["filtro"]

    monkeypatch.setattr(modulo, "FilterDialog", DialogoFalso)
    salvos: list = []
    atualizados: list[str] = []
    avisos: list[tuple] = []
    janela = SimpleNamespace(
        config=AppConfig(),
        filters=[Filter(id="a", name="Cupons"), Filter(id="b", name="SSD")],
        filter_store=SimpleNamespace(save=lambda filtros: salvos.append([(f.id, f.name) for f in filtros])),
        filters_page=SimpleNamespace(refresh=lambda: atualizados.append("filtros")),
        messages_page=SimpleNamespace(refresh_marks=lambda: atualizados.append("conversas")),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    resposta["filtro"] = Filter(id="a", name="Cupons 2")
    AppWindow.open_filter_editor(janela, janela.filters[0])
    resposta["filtro"] = Filter(id="c", name="Placa")
    AppWindow.open_filter_editor(janela, None)
    assert salvos == [
        [("a", "Cupons 2"), ("b", "SSD")],
        [("a", "Cupons 2"), ("b", "SSD"), ("c", "Placa")],
    ]
    assert atualizados == ["filtros", "conversas", "filtros", "conversas"]
    assert avisos == [("Filtro salvo", "success")] * 2
    resposta["aceito"] = False
    AppWindow.open_filter_editor(janela, None)
    assert len(salvos) == 2 and len(janela.filters) == 3


def test_editor_de_produto_salva_e_excluir_pergunta_pelo_nome(monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow
    from watcher.autobuy.models import WatchItem

    novo = WatchItem(id="w2", name="Placa", max_price=900.0, include=["placa"])
    resposta = {"aceito": True, "confirma": False}

    class DialogoFalso:
        def __init__(self, _item, _pai):
            pass

        def exec(self):
            return resposta["aceito"]

        def result_item(self):
            return novo

    monkeypatch.setattr(modulo, "WatchDialog", DialogoFalso)
    feitos: list = []
    perguntas: list[str] = []
    avisos: list[tuple] = []
    janela = SimpleNamespace(
        autobuy=SimpleNamespace(
            items=[WatchItem(id="w1", name="SSD 2TB", max_price=500.0, include=["ssd"])],
            upsert_item=lambda item: feitos.append(("salvo", item.id)),
            remove_item=lambda item_id: feitos.append(("removido", item_id)),
        ),
        autobuy_page=SimpleNamespace(refresh=lambda: feitos.append("tela")),
        confirm=lambda _titulo, texto, _ok="": perguntas.append(texto) or resposta["confirma"],
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    AppWindow.open_watch_editor(janela, None)
    resposta["aceito"] = False
    AppWindow.open_watch_editor(janela, None)
    AppWindow.delete_watch_item(janela, "w1")
    assert "'SSD 2TB'" in perguntas[-1]
    resposta["confirma"] = True
    AppWindow.delete_watch_item(janela, "w1")
    assert feitos == [("salvo", "w2"), "tela", ("removido", "w1"), "tela"]
    assert avisos == [("Produto salvo", "success"), ("Produto removido", "info")]


class _EventoDeFechar:
    def __init__(self):
        self.estado = None

    def ignore(self):
        self.estado = "ignorado"

    def accept(self):
        self.estado = "aceito"


def test_fechar_a_janela_vai_para_a_bandeja_e_avisa_so_na_primeira_vez(qapp, monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow

    temporizados: list[int] = []
    monkeypatch.setattr(modulo, "QTimer", SimpleNamespace(singleShot=lambda ms, _funcao: temporizados.append(ms)))
    notas: list[str] = []
    salvos: list[bool] = []
    escondida: list[bool] = []
    agendados: list[str] = []
    config = AppConfig()
    config.save = lambda: salvos.append(config.tray_hint_shown) or True
    janela = SimpleNamespace(
        _really_quit=False,
        _tray=SimpleNamespace(
            notify=lambda _titulo, texto, warning=True: notas.append(texto),
            hide=lambda: notas.append("bandeja escondida"),
        ),
        config=config,
        hide=lambda: escondida.append(True),
        engine=SimpleNamespace(is_running=True, stop=lambda: "parar"),
        schedule=agendados.append,
    )
    for _ in range(2):
        evento = _EventoDeFechar()
        AppWindow.closeEvent(janela, evento)
        assert evento.estado == "ignorado"
    assert len(escondida) == 2
    assert len(notas) == 1 and "bandeja" in notas[0]
    assert salvos == [True]
    assert agendados == [] and temporizados == []
    janela._really_quit = True
    evento = _EventoDeFechar()
    AppWindow.closeEvent(janela, evento)
    assert evento.estado == "aceito"
    assert agendados == ["parar"]
    assert notas[-1] == "bandeja escondida"
    assert temporizados == [300]


def test_sem_bandeja_fechar_a_janela_encerra_o_app(qapp, monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow

    temporizados: list[int] = []
    monkeypatch.setattr(modulo, "QTimer", SimpleNamespace(singleShot=lambda ms, _funcao: temporizados.append(ms)))
    janela = SimpleNamespace(
        _really_quit=False,
        _tray=None,
        config=AppConfig(),
        engine=SimpleNamespace(is_running=False),
        schedule=lambda _coro: pytest.fail("não deveria parar um monitor desligado"),
    )
    evento = _EventoDeFechar()
    AppWindow.closeEvent(janela, evento)
    assert evento.estado == "aceito" and temporizados == [300]


def test_janela_de_codigo_devolve_o_digitado_ou_nada_se_cancelar(qapp):
    from PyQt6.QtCore import QObject, pyqtSignal

    from ui.window import AppWindow

    class Dialogo(QObject):
        accepted = pyqtSignal()
        rejected = pyqtSignal()

        def show(self):
            return None

    async def cenario():
        janela = SimpleNamespace(_loop=asyncio.get_running_loop())
        aceito = Dialogo()
        pedido = AppWindow._await_dialog(janela, aceito, lambda: "12345")
        aceito.accepted.emit()
        aceito.rejected.emit()
        cancelado = Dialogo()
        outro = AppWindow._await_dialog(janela, cancelado, lambda: "99999")
        cancelado.rejected.emit()
        cancelado.accepted.emit()
        return await pedido, await outro

    assert asyncio.run(cenario()) == ("12345", None)


def test_sair_da_conta_so_com_confirmacao_e_aviso_conforme_o_telegram_respondeu():
    from ui.window import AppWindow

    resposta = {"confirma": False, "encerrada": True}
    feitos: list = []
    avisos: list[tuple] = []

    async def log_out():
        feitos.append("log_out")
        return resposta["encerrada"]

    janela = SimpleNamespace(
        confirm=lambda *_args: resposta["confirma"],
        engine=SimpleNamespace(log_out=log_out),
        autobuy=SimpleNamespace(stop_watchdog=lambda: feitos.append("vigia parado")),
        dashboard=SimpleNamespace(refresh_account=lambda: feitos.append("painel")),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
        schedule=asyncio.run,
    )
    AppWindow.logout_telegram(janela)
    assert feitos == [] and avisos == []
    resposta["confirma"] = True
    AppWindow.logout_telegram(janela)
    assert feitos == ["vigia parado", "log_out", "painel"]
    assert avisos[-1] == ("Você saiu da conta do Telegram", "success")
    resposta["encerrada"] = False
    AppWindow.logout_telegram(janela)
    assert avisos[-1][1] == "warning" and "Dispositivos" in avisos[-1][0]


def test_monitor_online_carrega_conversas_e_so_sincroniza_canais_se_nao_tem_lista(monkeypatch):
    from ui import window as modulo
    from ui.window import AppWindow
    from watcher.models import Channel, EngineState

    temporizados: list[tuple] = []
    monkeypatch.setattr(modulo, "QTimer", SimpleNamespace(singleShot=lambda ms, funcao: temporizados.append((ms, funcao))))
    estados: list = []
    bandeja: list = []
    avisos: list[tuple] = []
    janela = SimpleNamespace(
        dashboard=SimpleNamespace(set_state=estados.append),
        messages_page=SimpleNamespace(reload_chats="recarregar conversas"),
        channels=[],
        request_sync="sincronizar canais",
        _tray=SimpleNamespace(set_state=bandeja.append),
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
    )
    AppWindow._on_state(janela, EngineState.ONLINE)
    assert temporizados == [(600, "recarregar conversas"), (900, "sincronizar canais")]
    janela.channels = [Channel(-1, "Ofertas")]
    AppWindow._on_state(janela, EngineState.ONLINE)
    assert temporizados[2:] == [(600, "recarregar conversas")]
    AppWindow._on_state(janela, EngineState.ERROR)
    assert avisos == []
    AppWindow._on_state(janela, EngineState.ERROR, "Sessão encerrada")
    assert avisos == [("Sessão encerrada", "error")]
    assert len(temporizados) == 3
    assert estados == bandeja == [EngineState.ONLINE, EngineState.ONLINE, EngineState.ERROR, EngineState.ERROR]


def test_alerta_da_auto_compra_aparece_na_tela_na_bandeja_e_no_telegram():
    from ui.window import AppWindow

    avisos: list[tuple] = []
    notas: list[str] = []
    bipes: list[int] = []
    agendados: list[tuple] = []
    janela = SimpleNamespace(
        _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)),
        _tray=SimpleNamespace(notify=lambda _titulo, texto: notas.append(texto)),
        _beep=lambda: bipes.append(1),
        engine=SimpleNamespace(notify_me=lambda mensagem: ("notificar", mensagem)),
        schedule=agendados.append,
    )
    janela.alert = lambda mensagem: AppWindow.alert(janela, mensagem)
    AppWindow._on_autobuy_alert(janela, "Sua conta da KaBuM! caiu!")
    AppWindow._on_captcha(janela)
    assert avisos[0] == ("Sua conta da KaBuM! caiu!", "warning")
    assert notas[0] == "Sua conta da KaBuM! caiu!"
    assert len(bipes) == 2
    assert agendados[0] == ("notificar", "[Watcher] Sua conta da KaBuM! caiu!")
    assert agendados[1][1].startswith("[Watcher] Captcha na tela!")


def test_pedido_de_codigo_com_a_janela_escondida_avisa_pela_bandeja():
    from ui.window import AppWindow

    visivel = [False]
    notas: list[str] = []
    mostradas: list[int] = []
    janela = SimpleNamespace(
        isVisible=lambda: visivel[0],
        isMinimized=lambda: False,
        _show_window=lambda: mostradas.append(1),
        _tray=SimpleNamespace(notify=lambda _titulo, texto: notas.append(texto)),
    )
    AppWindow._call_attention(janela, "Digite o código")
    visivel[0] = True
    AppWindow._call_attention(janela, "Digite o código de novo")
    assert notas == ["Digite o código"]
    assert len(mostradas) == 2


def test_falha_ao_gravar_o_comportamento_avisa():
    from ui.window import AppWindow

    avisos: list[tuple] = []
    config = AppConfig()
    config.save = lambda: False
    janela = SimpleNamespace(config=config, _toast=lambda mensagem, tipo="info": avisos.append((mensagem, tipo)))
    AppWindow.save_behavior(janela, True, True)
    assert (config.allow_self, config.auto_start) == (True, True)
    assert avisos == [("Não foi possível salvar", "error")]
    config.save = lambda: True
    AppWindow.save_behavior(janela, False, True)
    assert len(avisos) == 1


def _pagina_de_conversas():
    from ui.pages.messages import MessagesPage

    app = AppFalso()
    app.agendados = []
    app.schedule = lambda coro: app.agendados.append(coro.__qualname__) or coro.close()
    return MessagesPage(app), app


def test_lista_de_conversas_filtra_por_papel_nao_lidas_e_busca_com_fixadas_no_topo(qapp):
    from datetime import datetime, timedelta, timezone

    from watcher.reader import ChatSummary

    pagina, app = _pagina_de_conversas()
    app.config.source_ids = [-1]
    app.config.destination_ids = [-2]
    agora = datetime.now(timezone.utc)
    pagina._summaries = [
        ChatSummary(-1, "Ofertas", date=agora - timedelta(hours=2)),
        ChatSummary(-2, "Destino", date=agora, unread=3),
        ChatSummary(-3, "Fixada", date=agora - timedelta(days=5), pinned=True),
        ChatSummary(-4, "Sem data"),
    ]

    def visiveis():
        return [s.id for s in pagina._visible_chats()]

    assert visiveis() == [-3, -2, -1, -4]
    pagina.scope.setCurrentIndex(1)
    assert visiveis() == [-1]
    pagina.scope.setCurrentIndex(2)
    assert visiveis() == [-2]
    pagina.scope.setCurrentIndex(3)
    assert visiveis() == [-2]
    pagina.scope.setCurrentIndex(0)
    pagina.search.setText("OFER")
    assert visiveis() == [-1]
    pagina.search.setText("-4")
    assert visiveis() == [-4]
    pagina.search.setText("zzz")
    assert "Nenhuma conversa com esse critério." in [w.text() for w in pagina.findChildren(QLabel)]
    assert pagina._role_of(-1) == "origem" and pagina._role_of(-2) == "destino" and pagina._role_of(-3) == ""
    pagina.deleteLater()


def test_mensagem_nova_conta_nao_lida_so_em_conversa_fechada_e_recebida(qapp):
    from watcher.reader import ChatSummary

    pagina, app = _pagina_de_conversas()
    pagina._summaries = [ChatSummary(-1, "Aberta"), ChatSummary(-2, "Fechada")]
    pagina._render_chats()
    pagina._chat_id = -1
    pagina.on_incoming(-2, ChatMessage(id=10, chat_id=-2, text="promo nova"))
    pagina.on_incoming(-2, ChatMessage(id=11, chat_id=-2, text="minha resposta", outgoing=True))
    pagina.on_incoming(-1, ChatMessage(id=12, chat_id=-1, text="na aberta"))
    aberta, fechada = pagina._summaries
    assert (aberta.unread, fechada.unread) == (0, 1)
    assert fechada.preview == "minha resposta" and aberta.preview == "na aberta"
    assert pagina._rows[-2]._badge.text() == "1"
    assert pagina._stale is True and 12 not in pagina._bubbles
    pagina.show()
    assert pagina._stale is False
    assert app.agendados == ["MessagesPage._fetch_history"]
    pagina.on_incoming(-1, ChatMessage(id=13, chat_id=-1, text="ao vivo"))
    assert 13 in pagina._bubbles
    pagina._searching = True
    pagina.on_incoming(-1, ChatMessage(id=14, chat_id=-1, text="durante a busca"))
    assert 14 not in pagina._bubbles
    pagina.hide()
    pagina.deleteLater()


def test_rolar_ate_o_topo_carrega_mensagens_antigas_so_quando_faz_sentido(qapp):
    pagina, app = _pagina_de_conversas()
    pagina._on_scroll(0)
    pagina._chat_id = -1
    pagina._on_scroll(0)
    pagina._oldest_id = 50
    pagina._on_scroll(200)
    assert app.agendados == []
    pagina._on_scroll(10)
    assert app.agendados == ["MessagesPage._fetch_history"]
    for campo in ("_searching", "_exhausted", "_loading"):
        setattr(pagina, campo, True)
        pagina._on_scroll(0)
        setattr(pagina, campo, False)
    assert len(app.agendados) == 1
    pagina.deleteLater()


def test_busca_na_conversa_mostra_resultados_e_limpar_volta_ao_historico(qapp):
    from ui.pages.messages import OFFLINE_TEXT

    pagina, app = _pagina_de_conversas()
    leitor = app.engine.reader
    respostas = [[ChatMessage(id=5, chat_id=-1, text="cupom MELI50", sender_id=1)], []]

    async def busca(_chat_id, _termo):
        return respostas.pop(0)

    leitor.search = busca
    pagina._chat_id = -1
    asyncio.run(pagina._fetch_search("cupom"))
    assert list(pagina._bubbles) == [5]
    assert any(w.text().startswith("1 resultados para “cupom”") for w in pagina.findChildren(QLabel))
    asyncio.run(pagina._fetch_search("nada"))
    assert pagina._bubbles == {}
    assert "Nada encontrado para “nada”." in [w.text() for w in pagina.findChildren(QLabel)]
    pagina._run_search("cupom")
    assert pagina._searching is True
    assert app.agendados == ["MessagesPage._fetch_search"]
    pagina._oldest_id = 9
    pagina._exhausted = True
    pagina._on_search_text("   ")
    assert (pagina._searching, pagina._oldest_id, pagina._exhausted) == (False, None, False)
    assert app.agendados[-1] == "MessagesPage._fetch_history"
    pagina._on_search_text("")
    assert len(app.agendados) == 2
    leitor.available = False
    pagina._run_search("cupom")
    assert pagina._searching is False
    assert OFFLINE_TEXT in [w.text() for w in pagina.findChildren(QLabel)]
    assert len(app.agendados) == 2
    pagina.deleteLater()


def test_marcar_conversa_como_origem_pela_tela_de_conversas(qapp):
    from watcher.reader import ChatSummary

    pagina, app = _pagina_de_conversas()
    papeis: list[tuple] = []
    atualizados: list[str] = []

    def marca(chat_id, nome, papel, ligado):
        papeis.append((chat_id, nome, papel, ligado))
        alvo = app.config.source_ids if papel == "source" else app.config.destination_ids
        if ligado:
            alvo.append(chat_id)
        else:
            alvo.remove(chat_id)

    app.set_channel_role = marca
    app.channels_page = SimpleNamespace(refresh=lambda: atualizados.append("canais"))
    pagina._set_role("source", True)
    assert papeis == []
    pagina._summaries = [ChatSummary(-1, "Ofertas VIP")]
    pagina._chat_id = -1
    pagina._set_role("source", True)
    pagina._set_role("destination", True)
    assert papeis == [(-1, "Ofertas VIP", "source", True), (-1, "Ofertas VIP", "destination", True)]
    assert atualizados == ["canais", "canais"]
    assert pagina._role_of(-1) == "origem · destino"
    assert app.agendados.count("MessagesPage._fetch_history") == 2
    pagina.deleteLater()


def test_rolar_para_cima_traz_mensagens_antigas_acima_e_para_quando_acaba(qapp):
    pagina, app = _pagina_de_conversas()
    paginas = {
        None: [ChatMessage(id=10, chat_id=-1, text="nova", sender_id=1), ChatMessage(id=11, chat_id=-1, text="mais nova", sender_id=1)],
        10: [ChatMessage(id=5, chat_id=-1, text="antiga", sender_id=2), ChatMessage(id=6, chat_id=-1, text="antiga 2", sender_id=2)],
        5: [],
    }
    pedidos: list = []

    async def historico(_chat_id, _limit=40, before_id=None):
        pedidos.append(before_id)
        return paginas[before_id]

    app.engine.reader.history = historico
    pagina._chat_id = -1
    asyncio.run(pagina._load_history(-1, True))
    assert pagina._oldest_id == 10 and sorted(pagina._bubbles) == [10, 11]
    asyncio.run(pagina._load_history(-1, False))
    assert pagina._oldest_id == 5 and sorted(pagina._bubbles) == [5, 6, 10, 11]
    assert pagina._thread.itemAt(0).widget() is pagina._bubbles[5].parentWidget()
    assert pagina._exhausted is False
    asyncio.run(pagina._load_history(-1, False))
    assert pagina._exhausted is True
    assert pedidos == [None, 10, 5]
    assert sorted(pagina._bubbles) == [5, 6, 10, 11]
    pagina.deleteLater()


def test_erro_ao_ler_a_conversa_so_aparece_se_ela_continua_aberta(qapp):
    pagina, app = _pagina_de_conversas()
    troca = {"para": None}

    async def sem_rede(_chat_id, _limit=40, before_id=None):
        if troca["para"] is not None:
            pagina._chat_id = troca["para"]
        raise ConnectionError("sem rede")

    app.engine.reader.history = sem_rede
    avisos: list[str] = []
    pagina._show_placeholder = avisos.append
    pagina._chat_id = -1
    asyncio.run(pagina._load_history(-1, True))
    troca["para"] = -2
    asyncio.run(pagina._load_history(-1, True))
    assert avisos == ["Não foi possível ler esta conversa: sem rede"]
    pagina.deleteLater()


def test_clicar_em_canal_com_id_grande_abre_a_propria_conversa(qapp):
    from watcher.reader import ChatSummary

    pagina, app = _pagina_de_conversas()
    canal = -1001371796882
    pagina._summaries = [ChatSummary(canal, "La Promotion - Promoções")]
    pagina._render_chats()
    pagina._rows[canal].clicked.emit()
    assert pagina._chat_id == canal
    assert pagina.thread_title.text() == "La Promotion - Promoções"
    assert pagina._rows[canal]._marker.isVisibleTo(pagina._rows[canal])
    assert app.agendados == ["MessagesPage._fetch_history", "MessagesPage._fetch_avatar"]
    pagina.deleteLater()


def test_carregar_conversas_mostra_a_lista_busca_fotos_e_reabre_a_conversa(qapp, tmp_path):
    from PyQt6.QtGui import QColor, QPixmap

    from watcher.reader import ChatSummary

    pagina, app = _pagina_de_conversas()
    leitor = app.engine.reader
    foto = tmp_path / "foto.png"
    imagem = QPixmap(12, 12)
    imagem.fill(QColor("red"))
    assert imagem.save(str(foto))

    async def dialogos(_limite=200):
        return [ChatSummary(-1, "Ofertas"), ChatSummary(-2, "Cupons")]

    async def avatar(chat_id):
        return foto if chat_id == -1 else None

    leitor.dialogs = dialogos
    leitor.avatar = avatar
    pagina._chat_id = -1
    pagina._opened_offline = True
    pagina.reload_btn.setEnabled(False)
    pagina._loading_chats = True
    asyncio.run(pagina._fetch_chats())
    assert pagina.reload_btn.isEnabled() and pagina._loading_chats is False
    assert list(pagina._rows) == [-1, -2]
    assert app.agendados == ["MessagesPage._fetch_avatars", "MessagesPage._fetch_history", "MessagesPage._fetch_avatar"]
    assert pagina._opened_offline is False
    asyncio.run(pagina._fetch_avatars())
    assert pagina._avatars == {-1: str(foto)}
    pagina.deleteLater()


def test_atualizar_conversas_com_monitor_desligado_so_avisa(qapp):
    pagina, app = _pagina_de_conversas()
    avisos: list[tuple] = []
    app.toast = lambda mensagem, tipo="info": avisos.append((mensagem, tipo))
    app.engine.reader.available = False
    pagina.reload_chats()
    assert avisos == [("Inicie o monitor para carregar as conversas", "warning")]
    assert app.agendados == [] and pagina.reload_btn.isEnabled()
    app.engine.reader.available = True
    pagina.reload_chats()
    assert not pagina.reload_btn.isEnabled() and pagina._loading_chats is True
    assert "Carregando conversas..." in [w.text() for w in pagina.findChildren(QLabel)]
    assert app.agendados == ["MessagesPage._fetch_chats"]
    pagina.deleteLater()


def test_teste_da_loja_mostra_o_desfecho_de_cada_resultado(qapp, tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher import paths
    from watcher.autobuy import records
    from watcher.autobuy.models import BuyResult, BuyStatus, ProductInfo

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    pagina, app, _store = _pagina_autobuy(tmp_path)
    produto = ProductInfo(id=1, title="SSD Kingston 1TB", url="x", pix_price=399.9, card_price=420.0, available=True)
    respostas: dict = {"produto": produto, "teste": None, "enviado": True}
    pedidos: list[tuple] = []
    enviados: list = []

    async def probe(_url):
        return respostas["produto"]

    async def run_test(_url, max_price, confirm, coupon):
        pedidos.append((max_price, confirm, coupon))
        return respostas["teste"]

    async def send_result(resultado):
        enviados.append(resultado.status)
        return respostas["enviado"]

    app.autobuy.probe = probe
    app.autobuy.run_test = run_test
    app.send_result = send_result

    def rodar(modo, cupom=""):
        pagina._running = True
        asyncio.run(pagina._run_test_async("kabum", "https://www.kabum.com.br/produto/1", modo, cupom))
        assert pagina._running is False
        return pagina._test_status["kabum"], pagina._test_color["kabum"]

    assert rodar(0) == ("SSD Kingston 1TB — à vista no PIX R$ 399,90 (disponível)", "success")
    produto.available = False
    assert rodar(0) == ("SSD Kingston 1TB — à vista no PIX R$ 399,90 (sem estoque)", "warning")
    respostas["produto"] = None
    assert rodar(0) == ("Não consegui ler os dados do produto nessa página.", "danger")
    assert rodar(1, "CUPOM10") == ("O teste não pôde ser executado, veja o Console.", "danger")
    respostas["teste"] = BuyResult(status=BuyStatus.DRY_RUN_OK, elapsed=12.34, detail="Parou na revisão.")
    texto, cor = rodar(1)
    assert texto == "Simulação percorreu o checkout em 12,3s e parou antes de finalizar. Parou na revisão."
    assert cor == "success"
    records.save_record(BuyResult(status=BuyStatus.PIX_READY, pix_code="000201"), "20260101-000001", "kabum")
    respostas["teste"] = BuyResult(status=BuyStatus.PIX_READY, elapsed=30.0)
    assert rodar(2) == ("PIX gerado em 30,0s. Ele está aqui embaixo e também no seu Telegram.", "success")
    assert "kabum" in pagina._open_stores and "20260101-000001" in pagina._expanded
    respostas["enviado"] = False
    assert rodar(2)[0].endswith("Não consegui mandar para o seu Telegram.")
    respostas["teste"] = BuyResult(status=BuyStatus.PRICE_ABOVE_LIMIT, detail="R$ 450 acima de R$ 400")
    assert rodar(2) == ("Não concluiu, preço acima do limite: R$ 450 acima de R$ 400", "warning")
    assert pedidos == [(0.0, False, "CUPOM10"), (0.0, False, ""), (0.0, True, ""), (0.0, True, ""), (0.0, True, "")]
    assert enviados == [BuyStatus.DRY_RUN_OK, BuyStatus.PIX_READY, BuyStatus.PIX_READY]
    pagina.deleteLater()


def test_verificar_todas_as_lojas_segue_mesmo_se_uma_falhar_e_libera_os_botoes(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    pagina, app, _store = _pagina_autobuy(tmp_path)
    agendados: list = []
    app.schedule = agendados.append
    verificadas: list[tuple] = []

    async def check_login(store_id, deep=True):
        verificadas.append((store_id, deep))
        if store_id == "kabum":
            raise RuntimeError("quebrou")
        return None

    app.autobuy.check_login = check_login
    pagina.check_all_stores()
    assert pagina._checking == {"kabum", "amazon", "terabyte"}
    assert not any(botao.isEnabled() for botao in pagina._verify_buttons.values())
    assert pagina._status_labels["amazon"].text() == "verificando conexão..."
    pagina.check_all_stores()
    pagina._check_store("kabum")
    pagina._connect_store("kabum")
    assert len(agendados) == 1
    asyncio.run(agendados[0])
    assert verificadas == [("kabum", False), ("amazon", False), ("terabyte", False)]
    assert pagina._checking == set()
    assert all(botao.isEnabled() for botao in pagina._verify_buttons.values())
    pagina.deleteLater()


def test_verificar_e_conectar_uma_loja_avisam_o_resultado_e_nao_deixam_a_loja_presa(qapp, tmp_path):
    pytest.importorskip("playwright.async_api")
    pagina, app, _store = _pagina_autobuy(tmp_path)
    agendados: list = []
    app.schedule = agendados.append
    avisos: list[tuple] = []
    pagina._notify = lambda mensagem, tipo="success": avisos.append((mensagem, tipo))
    respostas: dict = {"conta": "VINICIUS", "login": None}

    async def check_login(_store_id, deep=True):
        return respostas["conta"]

    async def open_login(_store_id):
        if isinstance(respostas["login"], Exception):
            raise respostas["login"]
        return respostas["login"]

    app.autobuy.check_login = check_login
    app.autobuy.open_login = open_login
    pagina._check_store("kabum")
    assert pagina._checking == {"kabum"}
    asyncio.run(agendados.pop())
    assert avisos[-1] == ("KaBuM!: conectado como VINICIUS", "success")
    respostas["conta"] = None
    pagina._check_store("amazon")
    asyncio.run(agendados.pop())
    assert avisos[-1] == ("Amazon: não conectado", "warning")
    pagina._connect_store("terabyte")
    assert pagina._connecting == {"terabyte"}
    assert avisos[-1][0].startswith("Abrindo a Terabyte")
    asyncio.run(agendados.pop())
    assert avisos[-1] == ("Terabyte: login não concluído", "warning")
    respostas["login"] = RuntimeError("navegador fechou")
    pagina._connect_store("kabum")
    with pytest.raises(RuntimeError):
        asyncio.run(agendados.pop())
    assert pagina._checking == set() and pagina._connecting == set()
    assert all(botao.isEnabled() for botao in pagina._verify_buttons.values())
    pagina.deleteLater()


def _motor_do_assistente(resultado):
    from watcher.models import EngineState

    class Motor:
        is_running = False
        state = EngineState.OFFLINE
        last_error = ""
        partidas = 0

        async def start(self):
            self.partidas += 1
            if isinstance(resultado, Exception):
                raise resultado
            self.state = EngineState.ONLINE if resultado else EngineState.ERROR
            return resultado

    return Motor()


def _assistente_no_passo_de_conectar(motor):
    from ui.setup_wizard import SetupWizard

    app = AppFalso()
    app.config = AppConfig(api_id=1, api_hash="x", phone="+55 11 99999-0000")
    app.engine = motor
    app.save_settings = lambda payload, silent=False: True
    app.agendados = []
    app.schedule = app.agendados.append
    assistente = SetupWizard(app, _pai())
    assistente._go_to(3)
    return assistente, app


def test_assistente_conectado_conclui_e_nao_conecta_duas_vezes(qapp):
    motor = _motor_do_assistente(True)
    assistente, app = _assistente_no_passo_de_conectar(motor)
    assistente._on_next()
    assert assistente._connecting is True
    assert not assistente.yesButton.isEnabled() and not assistente.cancelButton.isEnabled()
    assistente._on_next()
    assistente._on_back()
    assert len(app.agendados) == 1 and assistente._index == 3
    asyncio.run(app.agendados[0])
    assert motor.partidas == 1
    assert assistente.connected is True
    assert assistente.yesButton.text() == "Concluir" and assistente.cancelButton.isHidden()
    fechamentos: list[str] = []
    assistente.accept = lambda: fechamentos.append("concluido")
    assistente._on_next()
    assistente._on_back()
    assert fechamentos == ["concluido", "concluido"]
    assistente.deleteLater()


def test_assistente_com_erro_inesperado_ao_conectar_libera_os_botoes_e_explica(qapp):
    assistente, app = _assistente_no_passo_de_conectar(_motor_do_assistente(RuntimeError("quebrou")))
    assistente._on_next()
    asyncio.run(app.agendados[0])
    assert assistente._connecting is False and assistente.connected is False
    assert assistente.yesButton.isEnabled() and assistente.yesButton.text() == "Conectar ao Telegram"
    assert assistente._hint.text() == "Não foi possível conectar. Confira os dados e tente novamente."
    assistente.deleteLater()


def test_assistente_avisa_que_reinicia_o_monitor_ligado(qapp):
    motor = _motor_do_assistente(True)
    motor.is_running = True
    assistente, _app = _assistente_no_passo_de_conectar(motor)
    assert assistente._connect_text.text().endswith("será reiniciado com estes dados.")
    motor.is_running = False
    assistente._go_to(3)
    assert "reiniciado" not in assistente._connect_text.text()
    assistente.deleteLater()


def test_janela_de_codigo_so_confirma_com_algo_digitado_e_senha_guarda_espacos(qapp):
    from ui.dialogs import CodeDialog, PasswordDialog

    dialogo = CodeDialog("+55 11 99999-0000", _pai(), "Código incorreto, confira e digite de novo.")
    assert not dialogo.yesButton.isEnabled()
    dialogo.input.setText("   ")
    assert not dialogo.yesButton.isEnabled()
    dialogo.input.setText(" 1 2 3 4 5 ")
    assert dialogo.yesButton.isEnabled()
    assert dialogo.code() == "1 2 3 4 5"
    rotulos = {w.text(): w for w in dialogo.findChildren(QLabel)}
    assert not rotulos["Código incorreto, confira e digite de novo."].isHidden()
    assert any("+55 11 99999-0000" in texto for texto in rotulos)
    sem_aviso = CodeDialog("+55", _pai())
    assert all(w.isHidden() for w in sem_aviso.findChildren(QLabel) if w.text() == "")
    senha = PasswordDialog(_pai())
    senha.input.setText(" minha senha ")
    assert senha.password() == " minha senha "
    for janela in (dialogo, sem_aviso, senha):
        janela.deleteLater()


def test_palavras_do_filtro_quebram_linha_quando_nao_cabem(qapp):
    from PyQt6.QtWidgets import QWidget

    from ui.chips import FlowLayout

    host = QWidget()
    fluxo = FlowLayout(host, spacing=4)
    for _ in range(3):
        caixa = QWidget()
        caixa.setFixedSize(100, 20)
        fluxo.addWidget(caixa)
    assert fluxo.hasHeightForWidth()
    assert fluxo.heightForWidth(400) == 20
    assert fluxo.heightForWidth(250) == 44
    assert fluxo.heightForWidth(90) == 68
    assert (fluxo.minimumSize().width(), fluxo.minimumSize().height()) == (100, 20)
    assert fluxo.itemAt(5) is None and fluxo.takeAt(-1) is None
    assert fluxo.takeAt(0) is not None and fluxo.count() == 2
    host.deleteLater()


def test_grafico_de_atividade_desenha_com_dados_vazios_ou_a_mais(qapp):
    from ui.widgets import ActivityChart

    grafico = ActivityChart()
    grafico.resize(480, 170)
    grafico.set_data([])
    assert grafico._data == [0] * 24
    assert not grafico.grab().isNull()
    grafico.set_data(list(range(30)))
    assert grafico._data == list(range(24))
    cheio = grafico.grab().toImage()
    grafico.set_data([0] * 22 + [10, 1])
    baixa = grafico.grab().toImage()
    assert cheio.pixelColor(464, 30) != cheio.pixelColor(20, 30)
    assert baixa.pixelColor(445, 30) == cheio.pixelColor(464, 30)
    assert baixa.pixelColor(464, 30) == baixa.pixelColor(20, 30) == cheio.pixelColor(20, 30)
    assert baixa.pixelColor(464, 140) != baixa.pixelColor(20, 140)
    grafico.deleteLater()


def test_bandeja_abre_no_clique_duplo_e_aviso_que_falha_nao_derruba(qapp):
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QSystemTrayIcon

    from ui.tray import SystemTray

    abertas: list[int] = []
    bandeja = SystemTray(QIcon(), lambda: abertas.append(1), lambda: None, lambda: None)
    bandeja._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert abertas == []
    bandeja._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert abertas == [1]

    def sem_bandeja(*_args):
        raise RuntimeError("sem área de notificação")

    bandeja.showMessage = sem_bandeja
    bandeja.notify("Telegram Watcher", "Conta caiu")
    bandeja.deleteLater()


def test_pagina_de_filtros_vazia_explica_e_avisa_quando_replica_tudo(qapp):
    from ui.pages.filters import REPLICA_TUDO, FiltersPage

    app, pagina = _pagina_de_filtros([], True)
    textos = [w.text() for w in pagina.findChildren(QLabel)]
    assert "Nenhum filtro ainda" in textos and REPLICA_TUDO in textos
    pagina.deleteLater()
    app.config.destination_ids = []
    sem_destino = FiltersPage(app)
    textos = [w.text() for w in sem_destino.findChildren(QLabel)]
    assert "Nenhum filtro ainda" in textos and REPLICA_TUDO not in textos
    sem_destino.deleteLater()


def test_cartao_do_filtro_avisa_o_que_ele_replica_e_liga_os_botoes(qapp):
    from qfluentwidgets import TransparentToolButton

    from ui.pages.filters import _FilterCard
    from watcher.models import Filter

    filtros = [
        Filter.from_dict({"id": "a", "name": "Bloqueio", "exclude": ["shein"]}),
        Filter.from_dict({"id": "b", "name": "SSD", "include": ["ssd"]}),
    ]
    app, pagina = _pagina_de_filtros(filtros, True)
    editados: list[object] = []
    apagados: list[str] = []
    app.open_filter_editor = editados.append
    app.delete_filter = apagados.append
    cartoes = pagina.findChildren(_FilterCard)
    avisos = [[w.text() for w in cartao.findChildren(QLabel) if "replica" in w.text().lower()] for cartao in cartoes]
    assert avisos[0] == ["Só bloqueia essas palavras em todos os filtros, não replica nada sozinho"]
    assert avisos[1] == []
    editar, excluir = cartoes[1].findChildren(TransparentToolButton)
    editar.click()
    excluir.click()
    assert editados == [filtros[1]] and apagados == ["b"]
    pagina.deleteLater()
