import asyncio
import types

from watcher.config import AppConfig
from watcher.engine import MonitorEngine
from watcher.models import ChannelKind, EngineState, Filter


class FakeMessage:
    def __init__(self, mid, text, media=None):
        self.id = mid
        self.message = text
        self.text = text
        self.media = media


class FakeEvent:
    def __init__(self, chat_id, sender_id, message):
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.message = message


def make_engine(filters, **overrides):
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1], destination_ids=[-999, -100])
    for key, value in overrides.items():
        setattr(config, key, value)
    engine = MonitorEngine(config, lambda: filters)
    engine.sent = []

    async def fake_send(message, dest):
        engine.sent.append((message.id, dest))

    engine._send = fake_send
    return engine


def run(coro):
    asyncio.run(coro)


def run_value(coro):
    return asyncio.run(coro)


def test_forwards_matching_message():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "tem cupom aqui"))))
    assert engine.sent == [(1, -100)]
    assert engine.metrics.forwarded == 1
    assert engine.metrics.received == 1


def test_dedup_same_message():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    event = FakeEvent(-1, 5, FakeMessage(1, "cupom"))
    run(engine._handle(event))
    run(engine._handle(event))
    assert len(engine.sent) == 1


def test_dedup_keeps_most_recent_messages_after_trim():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])

    async def scenario():
        for mid in range(1, 2002):
            await engine._handle(FakeEvent(-1, 5, FakeMessage(mid, "cupom")))
        engine.sent.clear()
        for mid in range(1502, 2002):
            await engine._handle(FakeEvent(-1, 5, FakeMessage(mid, "cupom")))

    run(scenario())
    assert engine.sent == []


def test_sync_channels_marks_supergroup_as_group():
    class DialogClient:
        def is_connected(self):
            return True

        async def iter_dialogs(self, limit=None):
            yield types.SimpleNamespace(id=-100, name="Grupo", is_channel=True, is_group=True)
            yield types.SimpleNamespace(id=-200, name="Canal", is_channel=True, is_group=False)
            yield types.SimpleNamespace(id=7, name="Ana", is_channel=False, is_group=False)

    engine = make_engine([])
    engine._user = DialogClient()
    channels = run_value(engine.sync_channels())
    assert [(c.id, c.kind) for c in channels] == [
        (-100, ChannelKind.GROUP),
        (-200, ChannelKind.CHANNEL),
        (7, ChannelKind.USER),
    ]


def test_skips_unknown_source():
    engine = make_engine([])
    run(engine._handle(FakeEvent(-2, 5, FakeMessage(1, "cupom"))))
    assert engine.metrics.received == 0
    assert engine.sent == []


def test_test_mode_never_replicates_into_the_same_chat():
    engine = make_engine([], source_ids=[777], destination_ids=[777, -999], allow_self=True)
    engine._me = types.SimpleNamespace(id=777)
    next_id = [100]

    async def fake_send(message, dest):
        engine.sent.append((message.id, dest))
        if len(engine.sent) < 10:
            next_id[0] += 1
            await engine._handle(FakeEvent(dest, 777, FakeMessage(next_id[0], message.message)))

    engine._send = fake_send
    run(engine._handle(FakeEvent(777, 777, FakeMessage(1, "cupom teste"))))
    assert engine.sent == [(1, -999)]


def test_retry_after_flood_wait_is_reported_like_any_send(monkeypatch):
    from telethon.errors import FloodWaitError

    from watcher.models import LogLevel

    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100, -200]})
    events, logs = [], []
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1], destination_ids=[-100, -200])
    engine = MonitorEngine(config, lambda: [flt], on_event=events.append, on_log=lambda m, lvl: logs.append((m, lvl)))
    attempts = {-100: [FloodWaitError(request=None, capture=3)], -200: [FloodWaitError(request=None, capture=3), RuntimeError("caiu")]}

    async def flaky_send(message, dest):
        if attempts[dest]:
            raise attempts[dest].pop(0)

    async def no_wait(_seconds):
        return None

    engine._send = flaky_send
    monkeypatch.setattr("watcher.engine.asyncio.sleep", no_wait)
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "cupom"))))
    assert [(e["chat_id"], e["status"]) for e in events if e["status"] != "captured"] == [
        (-100, "forwarded"),
        (-200, "failed"),
    ]
    assert (engine.metrics.forwarded, engine.metrics.failed) == (1, 1)
    assert any(m.startswith("Enviado para") and lvl == LogLevel.SUCCESS for m, lvl in logs)


def test_skips_self_unless_allowed():
    engine = make_engine([])
    engine._me = types.SimpleNamespace(id=77)
    run(engine._handle(FakeEvent(-1, 77, FakeMessage(1, "oi"))))
    assert engine.metrics.received == 0

    allowed = make_engine([], allow_self=True)
    allowed._me = types.SimpleNamespace(id=77)
    run(allowed._handle(FakeEvent(-1, 77, FakeMessage(2, "oi"))))
    assert allowed.metrics.received == 1


def test_filtered_when_no_match():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "mensagem qualquer"))))
    assert engine.metrics.received == 1
    assert engine.metrics.filtered == 1
    assert engine.sent == []


def test_smart_rule_capture_end_to_end():
    flt = Filter.from_dict({"id": "a", "smart_rules": ["any_coupon"], "destination_ids": [-100]})
    engine = make_engine([flt])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "Cupom MELI50 no Mercado Livre"))))
    assert engine.sent == [(1, -100)]


class FakeClient:
    def __init__(self):
        self.forwarded = []
        self.messages = []
        self.files = []
        self.downloads = 0

    async def forward_messages(self, dest, message):
        self.forwarded.append((dest, message.id))

    async def send_message(self, dest, text, formatting_entities=None):
        self.messages.append((dest, text))

    async def send_file(self, dest, data, caption=None, formatting_entities=None, attributes=None, force_document=False):
        payload = data.read() if hasattr(data, "read") else data
        self.files.append((dest, payload, caption))

    async def download_media(self, message, file=None):
        self.downloads += 1
        return b"bytes"


def test_bot_copies_text_instead_of_user_forward():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = FakeClient()
    run(engine._send(FakeMessage(1, "promo"), -100))
    assert engine._bot.messages == [(-100, "promo")]
    assert engine._user.forwarded == []
    assert engine._bot.forwarded == []


def test_bot_copies_media_via_download_reupload():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = FakeClient()
    run(engine._send(FakeMessage(2, "veja", media=object()), -100))
    assert engine._user.downloads == 1
    assert engine._bot.files == [(-100, b"bytes", "veja")]
    assert engine._bot.messages == []


def test_user_forwards_when_no_bot():
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = None
    run(engine._send(FakeMessage(3, "oi"), -100))
    assert engine._user.forwarded == [(-100, 3)]


def test_stop_when_offline_is_noop():
    engine = make_engine([])
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert not engine.is_running


def test_start_without_credentials_does_not_hang():
    engine = MonitorEngine(AppConfig(), lambda: [])
    assert run_value(engine.start()) is False
    assert engine.state == EngineState.ERROR
    assert not engine.is_running


def test_restart_is_reentrant():
    engine = MonitorEngine(AppConfig(), lambda: [])
    assert run_value(engine.start()) is False
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert run_value(engine.start()) is False
    run(engine.stop())
    assert engine.state == EngineState.OFFLINE
    assert not engine.is_running


def test_start_while_stopping_does_not_open_second_client(tmp_path, monkeypatch):
    created = []

    class FakeTelegramClient:
        def __init__(self, session, *_args, **_kwargs):
            created.append(session)

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            return None

    monkeypatch.setattr("watcher.engine.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    engine = make_engine([])
    release = asyncio.Event()

    class SlowClient:
        async def disconnect(self):
            await release.wait()

    engine._user = SlowClient()
    engine.state = EngineState.ONLINE

    async def scenario():
        stopping = asyncio.create_task(engine.stop())
        await asyncio.sleep(0)
        assert engine.state == EngineState.STOPPING
        started = await engine.start()
        release.set()
        await stopping
        return started

    assert run_value(scenario()) is False
    assert created == []
    assert engine.state == EngineState.OFFLINE


def test_login_code_typed_with_spaces_is_sent_without_them(tmp_path, monkeypatch):
    signed = []

    class FakeTelegramClient:
        def __init__(self, *_args, **_kwargs):
            self.authorized = False

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def send_code_request(self, phone):
            return None

        async def sign_in(self, phone=None, code=None, password=None):
            signed.append(code)
            self.authorized = True

        async def get_me(self):
            return types.SimpleNamespace(id=1, first_name="Ana")

        def add_event_handler(self, handler, event):
            return handler

        def remove_event_handler(self, handler):
            return None

        async def disconnect(self):
            return None

    async def code_provider(_phone):
        return " 1 2 3 4 5 "

    monkeypatch.setattr("watcher.engine.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1])
    engine = MonitorEngine(config, lambda: [], code_provider=code_provider)

    async def scenario():
        started = await engine.start()
        await engine.stop()
        return started

    assert run_value(scenario()) is True
    assert signed == ["12345"]


class ClienteFalso:
    criados: list["ClienteFalso"] = []

    def __init__(self, *_args, **_kwargs):
        self.conectado = False
        self.autorizado = False
        self.falhas_de_rede = 0
        self.catch_ups = 0
        self.codigos: list[str] = []
        self.codigos_errados = 0
        self._queda = None
        ClienteFalso.criados.append(self)

    async def connect(self):
        if self.falhas_de_rede > 0:
            self.falhas_de_rede -= 1
            raise ConnectionError("Connection to Telegram failed 5 time(s)")
        self.conectado = True
        self._queda = asyncio.get_running_loop().create_future()

    @property
    def disconnected(self):
        return self._queda

    def cair(self, falhas_ao_voltar: int = 0):
        self.conectado = False
        self.falhas_de_rede = falhas_ao_voltar
        self._queda.set_exception(ConnectionError("caiu"))

    def is_connected(self):
        return self.conectado

    async def is_user_authorized(self):
        return self.autorizado

    async def send_code_request(self, phone):
        return None

    async def sign_in(self, phone=None, code=None, password=None):
        from telethon.errors import PhoneCodeInvalidError

        self.codigos.append(code)
        if self.codigos_errados > 0:
            self.codigos_errados -= 1
            raise PhoneCodeInvalidError(request=None)
        self.autorizado = True

    async def get_me(self):
        return types.SimpleNamespace(id=1, first_name="Ana")

    async def catch_up(self):
        self.catch_ups += 1

    def add_event_handler(self, handler, event):
        return handler

    def remove_event_handler(self, handler):
        return None

    async def disconnect(self):
        self.conectado = False


def _motor_com_cliente_falso(monkeypatch, tmp_path, **kwargs):
    ClienteFalso.criados = []
    monkeypatch.setattr("watcher.engine.TelegramClient", ClienteFalso)
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    monkeypatch.setattr("watcher.engine._RECONNECT_DELAYS", (0,))
    estados, logs = [], []
    config = AppConfig(api_id=1, api_hash="h", phone="+55", source_ids=[-1])
    engine = MonitorEngine(
        config,
        lambda: [],
        on_state=lambda estado, _detalhe="": estados.append(estado),
        on_log=lambda mensagem, _nivel: logs.append(mensagem),
        **kwargs,
    )
    return engine, estados, logs


async def _ate(condicao, voltas: int = 200) -> None:
    for _ in range(voltas):
        if condicao():
            return
        await asyncio.sleep(0)


async def _codigo_certo(_phone, _aviso=""):
    return "12345"


def test_monitor_reconecta_sozinho_quando_a_rede_cai(tmp_path, monkeypatch):
    engine, estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def cenario():
        assert await engine.start()
        cliente = engine._user
        cliente.cair(falhas_ao_voltar=2)
        await _ate(lambda: cliente.catch_ups == 1)
        estado = engine.state
        await engine.stop()
        return cliente, estado

    cliente, estado = run_value(cenario())
    assert estado == EngineState.ONLINE
    assert EngineState.RECONNECTING in estados
    assert cliente.catch_ups == 1
    assert any("Reconectado" in m for m in logs)
    assert engine.state == EngineState.OFFLINE


def test_parar_o_monitor_nao_dispara_reconexao(tmp_path, monkeypatch):
    engine, estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def cenario():
        assert await engine.start()
        cliente = engine._user
        await engine.stop()
        cliente._queda.set_result(None)
        await _ate(lambda: False, voltas=20)
        return cliente

    cliente = run_value(cenario())
    assert EngineState.RECONNECTING not in estados
    assert cliente.catch_ups == 0
    assert engine.state == EngineState.OFFLINE


def test_inicio_automatico_espera_a_rede_voltar(tmp_path, monkeypatch):
    engine, estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    original = ClienteFalso.__init__

    def sem_rede_no_boot(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.falhas_de_rede = 2

    monkeypatch.setattr(ClienteFalso, "__init__", sem_rede_no_boot)

    async def cenario():
        iniciou = await engine.start(insistir=True)
        await engine.stop()
        return iniciou

    assert run_value(cenario()) is True
    assert EngineState.RECONNECTING in estados


def test_parar_durante_a_conexao_do_inicio_automatico_termina_offline(tmp_path, monkeypatch):
    engine, estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def parado_enquanto_conecta(_self):
        await engine.stop()
        raise ConnectionError("Connection to Telegram failed 5 time(s)")

    monkeypatch.setattr(ClienteFalso, "connect", parado_enquanto_conecta)
    assert run_value(engine.start(insistir=True)) is False
    assert engine.state == EngineState.OFFLINE
    assert EngineState.RECONNECTING not in estados


def test_inicio_manual_sem_rede_explica_em_portugues(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    original = ClienteFalso.__init__

    def sem_rede(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.falhas_de_rede = 1

    monkeypatch.setattr(ClienteFalso, "__init__", sem_rede)
    assert run_value(engine.start()) is False
    assert EngineState.ERROR in _estados and EngineState.RECONNECTING not in _estados
    assert engine.last_error == "Sem conexão com o Telegram. Confira a internet."


def test_codigo_errado_pede_de_novo_em_vez_de_derrubar_o_login(tmp_path, monkeypatch):
    avisos: list[str] = []

    async def codigo(_phone, aviso=""):
        avisos.append(aviso)
        return "11111" if len(avisos) == 1 else "22222"

    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=codigo)
    original = ClienteFalso.__init__

    def um_codigo_errado(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.codigos_errados = 1

    monkeypatch.setattr(ClienteFalso, "__init__", um_codigo_errado)

    async def cenario():
        iniciou = await engine.start()
        await engine.stop()
        return iniciou

    assert run_value(cenario()) is True
    assert ClienteFalso.criados[0].codigos == ["11111", "22222"]
    assert avisos[0] == "" and "incorreto" in avisos[1]


def test_sair_da_conta_encerra_a_sessao_e_apaga_os_arquivos(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    saidas: list[bool] = []

    async def log_out(self):
        saidas.append(True)
        return True

    monkeypatch.setattr(ClienteFalso, "log_out", log_out, raising=False)
    for nome in ("user.session", "bot.session"):
        (tmp_path / nome).write_bytes(b"x")

    async def cenario():
        assert await engine.start()
        return await engine.log_out()

    assert run_value(cenario()) is True
    assert saidas == [True]
    assert engine.state == EngineState.OFFLINE
    assert not (tmp_path / "user.session").exists() and not (tmp_path / "bot.session").exists()


def test_flood_wait_do_login_vira_mensagem_em_minutos():
    from telethon.errors import FloodWaitError

    from watcher.engine import explain_error

    assert explain_error(FloodWaitError(request=None, capture=86400)) == (
        "O Telegram limitou as tentativas. Aguarde 24 horas e tente de novo."
    )
    assert "5 minutos" in explain_error(FloodWaitError(request=None, capture=290))


def test_canal_desmarcado_como_destino_deixa_de_receber_pelo_filtro():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt], destination_ids=[-999])
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "tem cupom aqui"))))
    assert engine.sent == []


def test_link_escondido_em_texto_clicavel_e_botao_chega_na_auto_compra():
    from telethon.tl.types import (
        KeyboardButtonRow,
        KeyboardButtonUrl,
        MessageEntityTextUrl,
        ReplyInlineMarkup,
    )

    textos: list[str] = []
    engine = make_engine([])
    engine._on_text = textos.append
    message = FakeMessage(1, "SSD 2TB barato, Comprar aqui")
    message.entities = [MessageEntityTextUrl(offset=17, length=12, url="https://www.kabum.com.br/produto/1/ssd")]
    message.reply_markup = ReplyInlineMarkup(
        rows=[KeyboardButtonRow(buttons=[KeyboardButtonUrl(text="Ir para a loja", url="https://amzn.to/abc")])]
    )
    run(engine._handle(FakeEvent(-1, 5, message)))
    assert "https://www.kabum.com.br/produto/1/ssd" in textos[0]
    assert "https://amzn.to/abc" in textos[0]


def test_bot_repassa_botao_de_link_e_nao_conta_enquete_como_enviada(monkeypatch):
    from telethon.tl.types import KeyboardButtonRow, KeyboardButtonUrl, ReplyInlineMarkup

    class BotComBotoes(FakeClient):
        async def send_message(self, dest, text, formatting_entities=None, buttons=None):
            self.messages.append((dest, text, buttons))

    monkeypatch.setattr("watcher.engine._SEND_INTERVAL", 0)
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = BotComBotoes()
    promo = FakeMessage(1, "Oferta")
    promo.reply_markup = ReplyInlineMarkup(
        rows=[KeyboardButtonRow(buttons=[KeyboardButtonUrl(text="Comprar", url="https://loja.example/p")])]
    )
    assert run_value(engine._send(promo, -100)) is True
    botoes = engine._bot.messages[0][2]
    assert botoes[0][0].url == "https://loja.example/p"

    class ClienteSemMidia(FakeClient):
        async def download_media(self, message, file=None):
            return None

    engine._user = ClienteSemMidia()
    enquete = FakeMessage(2, "", media=object())
    enquete.poll = object()
    run(engine._forward(enquete, [-100]))
    assert engine.metrics.forwarded == 0


def test_erros_do_telegram_viram_mensagem_em_portugues():
    from telethon.errors import (
        ApiIdInvalidError,
        PasswordHashInvalidError,
        PhoneCodeExpiredError,
        PhoneNumberBannedError,
        PhoneNumberInvalidError,
    )

    from watcher.engine import explain_error

    assert "API ID ou API Hash" in explain_error(ApiIdInvalidError(request=None))
    assert "Telefone inválido" in explain_error(PhoneNumberInvalidError(request=None))
    assert "banido" in explain_error(PhoneNumberBannedError(request=None))
    assert "expirou" in explain_error(PhoneCodeExpiredError(request=None))
    assert "Senha de duas etapas" in explain_error(PasswordHashInvalidError(request=None))
    assert explain_error(ConnectionResetError()) == "Sem conexão com o Telegram. Confira a internet."
    assert explain_error(RuntimeError("detalhe")) == "detalhe"
    assert explain_error(KeyError()) == "KeyError"


def test_espera_do_telegram_arredonda_para_cima():
    from watcher.engine import _minutes

    assert _minutes(0) == "1 minuto"
    assert _minutes(60) == "1 minuto"
    assert _minutes(61) == "2 minutos"
    assert _minutes(7140) == "119 minutos"
    assert _minutes(7200) == "2 horas"
    assert _minutes(7201) == "3 horas"


class ClienteComSenha(ClienteFalso):
    senhas_erradas = 0
    codigo_expira = False

    async def sign_in(self, phone=None, code=None, password=None):
        from telethon.errors import PasswordHashInvalidError, PhoneCodeExpiredError, SessionPasswordNeededError

        if password is None:
            self.codigos.append(code)
            if self.codigo_expira and len(self.codigos) == 1:
                raise PhoneCodeExpiredError(request=None)
            raise SessionPasswordNeededError(request=None)
        self.senhas = getattr(self, "senhas", []) + [password]
        if len(self.senhas) <= self.senhas_erradas:
            raise PasswordHashInvalidError(request=None)
        self.autorizado = True

    async def send_code_request(self, phone):
        self.pedidos_de_codigo = getattr(self, "pedidos_de_codigo", 0) + 1


def _motor_com_senha(monkeypatch, tmp_path, respostas, **kwargs):
    avisos: list[str] = []
    fila = list(respostas)

    async def senha(aviso=""):
        avisos.append(aviso)
        return fila.pop(0) if fila else ""

    engine, estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo, password_provider=senha)
    monkeypatch.setattr("watcher.engine.TelegramClient", ClienteComSenha)
    for nome, valor in kwargs.items():
        monkeypatch.setattr(ClienteComSenha, nome, valor)
    return engine, avisos, logs


def test_senha_de_duas_etapas_errada_pede_de_novo(tmp_path, monkeypatch):
    engine, avisos, _logs = _motor_com_senha(monkeypatch, tmp_path, [" errada ", " certa "], senhas_erradas=1)

    async def cenario():
        iniciou = await engine.start()
        cliente = engine._user
        await engine.stop()
        return iniciou, cliente

    iniciou, cliente = run_value(cenario())
    assert iniciou is True
    assert cliente.senhas == ["errada", "certa"]
    assert avisos == ["", "Senha incorreta. Tente de novo."]


def test_senha_errada_tres_vezes_desiste(tmp_path, monkeypatch):
    engine, avisos, _logs = _motor_com_senha(monkeypatch, tmp_path, ["a", "b", "c", "d"], senhas_erradas=5)
    assert run_value(engine.start()) is False
    assert len(avisos) == 3
    assert "todas as tentativas" in engine.last_error
    assert engine.state == EngineState.OFFLINE


def test_senha_em_branco_cancela_o_login(tmp_path, monkeypatch):
    engine, _avisos, _logs = _motor_com_senha(monkeypatch, tmp_path, ["   "])
    assert run_value(engine.start()) is False
    assert engine.last_error == "Login cancelado"


def test_conta_com_duas_etapas_sem_janela_de_senha_explica(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    monkeypatch.setattr("watcher.engine.TelegramClient", ClienteComSenha)
    assert run_value(engine.start()) is False
    assert engine.last_error == "Verificação em duas etapas necessária"


def test_codigo_expirado_pede_um_novo_ao_telegram(tmp_path, monkeypatch):
    engine, avisos, _logs = _motor_com_senha(monkeypatch, tmp_path, ["certa"], codigo_expira=True)
    codigos: list[str] = []

    async def codigo(_phone, aviso=""):
        codigos.append(aviso)
        return "12345"

    engine._code_provider = codigo

    async def cenario():
        iniciou = await engine.start()
        cliente = engine._user
        await engine.stop()
        return iniciou, cliente

    iniciou, cliente = run_value(cenario())
    assert iniciou is True
    assert cliente.pedidos_de_codigo == 2
    assert codigos[0] == "" and "expirou" in codigos[1]


def test_codigo_em_branco_cancela_e_tres_errados_desiste(tmp_path, monkeypatch):
    async def vazio(_phone, _aviso=""):
        return "   "

    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=vazio)
    assert run_value(engine.start()) is False
    assert engine.last_error == "Login cancelado"

    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    original = ClienteFalso.__init__

    def sempre_errado(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.codigos_errados = 10

    monkeypatch.setattr(ClienteFalso, "__init__", sempre_errado)
    assert run_value(engine.start()) is False
    assert ClienteFalso.criados[-1].codigos == ["12345"] * 3
    assert "todas as tentativas" in engine.last_error


def test_sem_janela_de_codigo_nao_tenta_logar(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path)
    assert run_value(engine.start()) is False
    assert engine.last_error == "Sem provedor de código de login"
    assert ClienteFalso.criados[0].codigos == []


def test_bot_que_nao_liga_nao_derruba_o_monitor(tmp_path, monkeypatch):
    engine, _estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    engine.config.bot_token = "123:abc"

    async def bot_recusado(self, bot_token=None):
        raise RuntimeError("token revogado")

    monkeypatch.setattr(ClienteFalso, "start", bot_recusado, raising=False)

    async def cenario():
        iniciou = await engine.start()
        bot = engine._bot
        await engine.stop()
        return iniciou, bot

    iniciou, bot = run_value(cenario())
    assert iniciou is True
    assert bot is None
    assert any("Bot indisponível: token revogado" in m for m in logs)


def test_sessao_encerrada_durante_a_queda_para_o_monitor_com_erro(tmp_path, monkeypatch):
    engine, estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def cenario():
        assert await engine.start()
        cliente = engine._user

        async def sem_conta():
            return None

        cliente.get_me = sem_conta
        cliente.cair()
        await _ate(lambda: engine.state == EngineState.OFFLINE)
        return cliente

    cliente = run_value(cenario())
    assert EngineState.ERROR in estados
    assert "Conecte a conta de novo" in engine.last_error
    assert engine.state == EngineState.OFFLINE
    assert cliente.catch_ups == 0


def test_monitor_ja_ligado_nao_abre_outro_cliente(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def cenario():
        assert await engine.start()
        assert await engine.start()
        await engine.stop()

    run(cenario())
    assert len(ClienteFalso.criados) == 1


def test_sem_bot_encaminhamento_recusado_vira_copia(monkeypatch):
    class ClienteSemEncaminhar(FakeClient):
        async def forward_messages(self, dest, message):
            raise RuntimeError("canal proíbe encaminhar")

        async def send_file(self, dest, data, caption=None):
            self.files.append((dest, data, caption))

    engine = make_engine([])
    engine._user = ClienteSemEncaminhar()
    midia = object()
    run(engine._send_as_user(FakeMessage(1, "texto"), -100))
    run(engine._send_as_user(FakeMessage(2, "legenda", media=midia), -100))
    assert engine._user.messages == [(-100, "texto")]
    assert engine._user.files == [(-100, midia, "legenda")]


def test_limite_do_telegram_ao_encaminhar_nao_vira_copia():
    import pytest
    from telethon.errors import FloodWaitError

    class ClienteLimitado(FakeClient):
        async def forward_messages(self, dest, message):
            raise FloodWaitError(request=None, capture=30)

    engine = make_engine([])
    engine._user = ClienteLimitado()
    with pytest.raises(FloodWaitError):
        run(engine._send_as_user(FakeMessage(1, "texto"), -100))
    assert engine._user.messages == []


def test_nome_do_arquivo_copiado_pelo_bot():
    from watcher.engine import MonitorEngine

    com_nome = types.SimpleNamespace(attributes=[types.SimpleNamespace(), types.SimpleNamespace(file_name="nota.pdf")])
    assert MonitorEngine._document_name(com_nome) == "nota.pdf"
    sem_nome = types.SimpleNamespace(attributes=[], mime_type="application/pdf")
    assert MonitorEngine._document_name(sem_nome) == "arquivo.pdf"
    desconhecido = types.SimpleNamespace(attributes=[], mime_type="")
    assert MonitorEngine._document_name(desconhecido) == "arquivo.bin"


def test_aviso_para_mim_so_com_conta_conectada():
    engine = make_engine([])
    assert run_value(engine.notify_me("oi")) is False

    class Cliente(FakeClient):
        conectado = False

        def is_connected(self):
            return self.conectado

        async def send_file(self, dest, data, caption=None):
            self.files.append((dest, data))

    engine._user = Cliente()
    assert run_value(engine.notify_me("oi")) is False
    engine._user.conectado = True
    assert run_value(engine.notify_me("PIX gerado", "print.png")) is True
    assert engine._user.messages == [("me", "PIX gerado")]
    assert engine._user.files == [("me", "print.png")]

    async def falha(*_args, **_kwargs):
        raise ConnectionError("caiu")

    engine._user.send_message = falha
    assert run_value(engine.notify_me("oi")) is False


def test_mensagem_do_proprio_bot_nao_e_replicada():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])
    engine._bot_me = types.SimpleNamespace(id=999)
    run(engine._handle(FakeEvent(-1, 999, FakeMessage(1, "cupom"))))
    assert engine.sent == []
    assert engine.metrics.received == 0


def test_erro_ao_processar_mensagem_conta_como_erro_sem_derrubar():
    engine = make_engine([])

    async def quebra(_event):
        raise RuntimeError("inesperado")

    engine._handle = quebra
    run(engine._on_new_message(FakeEvent(-1, 5, FakeMessage(1, "x"))))
    assert engine.metrics.errors == 1


def test_link_que_ja_esta_no_texto_nao_e_repetido():
    from telethon.tl.types import MessageEntityTextUrl

    from watcher.engine import text_with_links

    mensagem = FakeMessage(1, "Compre em https://loja.example/p")
    mensagem.entities = [MessageEntityTextUrl(offset=10, length=22, url="https://loja.example/p")]
    assert text_with_links(mensagem) == "Compre em https://loja.example/p"
    assert text_with_links(FakeMessage(2, "")) == ""


def test_esquecer_sessao_apaga_os_arquivos_da_conta(tmp_path, monkeypatch):
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    (tmp_path / "user.session").write_bytes(b"x")
    (tmp_path / "user.session-journal").write_bytes(b"x")
    (tmp_path / "bot.session").write_bytes(b"x")
    assert MonitorEngine.forget_session("user") is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bot.session"]
    assert MonitorEngine.forget_session("user") is False


def test_sair_da_conta_sem_conexao_apaga_a_sessao_local(tmp_path, monkeypatch):
    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    (tmp_path / "user.session").write_bytes(b"x")
    engine = make_engine([])
    assert run_value(engine.log_out()) is False
    assert list(tmp_path.iterdir()) == []


def test_sincronizar_canais_com_monitor_desligado_devolve_vazio():
    engine = make_engine([])
    assert run_value(engine.sync_channels()) == []


def test_erro_na_auto_compra_ou_na_tela_de_conversas_nao_impede_a_replicacao():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_ids": [-100]})
    engine = make_engine([flt])

    def auto_compra_quebrada(_texto):
        raise RuntimeError("auto-compra quebrou")

    def tela_quebrada(_chat_id, _mensagem):
        raise RuntimeError("tela fechada")

    engine._on_text = auto_compra_quebrada
    engine._on_incoming = tela_quebrada
    run(engine._handle(FakeEvent(-1, 5, FakeMessage(1, "tem cupom aqui"))))
    assert engine.sent == [(1, -100)]
    assert engine.metrics.forwarded == 1


def test_envios_seguidos_respeitam_o_intervalo_minimo(monkeypatch):
    import time as relogio_real

    relogio = [1000.0]
    esperas: list[float] = []

    async def espera(segundos):
        esperas.append(round(segundos, 2))
        relogio[0] += segundos

    monkeypatch.setattr(
        "watcher.engine.time",
        types.SimpleNamespace(monotonic=lambda: relogio[0], time=relogio_real.time, strftime=relogio_real.strftime),
    )
    monkeypatch.setattr("watcher.engine.asyncio.sleep", espera)
    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()

    async def cenario():
        await engine._send(FakeMessage(1, "a"), -100)
        relogio[0] += 0.3
        await engine._send(FakeMessage(2, "b"), -100)
        relogio[0] += 5
        await engine._send(FakeMessage(3, "c"), -100)

    run(cenario())
    assert esperas == [0.5]
    assert engine._user.forwarded == [(-100, 1), (-100, 2), (-100, 3)]


def test_bot_copia_documento_com_o_nome_e_os_atributos_originais():
    class Bot(FakeClient):
        async def send_file(self, dest, data, caption=None, formatting_entities=None, attributes=None,
                            force_document=False, **_extra):
            self.files.append((dest, data.name, data.read(), caption, attributes, force_document))

    engine = make_engine([])
    del engine._send
    engine._user = FakeClient()
    engine._bot = Bot()
    atributo = types.SimpleNamespace(file_name="nota.pdf")
    mensagem = FakeMessage(4, "segue a nota", media=object())
    mensagem.document = types.SimpleNamespace(attributes=[atributo], mime_type="application/pdf")
    assert run_value(engine._send(mensagem, -100)) is True
    assert engine._bot.files == [(-100, "nota.pdf", b"bytes", "segue a nota", [atributo], False)]


def test_monitor_ligado_sem_origem_avisa_onde_marcar(tmp_path, monkeypatch):
    engine, _estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    engine.config.source_ids = []

    async def cenario():
        iniciou = await engine.start()
        await engine.stop()
        return iniciou

    assert run_value(cenario()) is True
    assert any(m.startswith("Nenhuma origem marcada") for m in logs)


def _bot_que_liga(monkeypatch):
    async def liga(self, bot_token=None):
        self.conectado = True

    async def perfil(self):
        return types.SimpleNamespace(id=1, first_name="Ana", username="robo")

    monkeypatch.setattr(ClienteFalso, "start", liga, raising=False)
    monkeypatch.setattr(ClienteFalso, "get_me", perfil)


def test_reconexao_com_bot_fora_do_ar_e_sem_recuperar_o_atrasado_continua_vigiando(tmp_path, monkeypatch):
    engine, _estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)
    engine.config.bot_token = "123:abc"
    _bot_que_liga(monkeypatch)

    async def cenario():
        assert await engine.start()
        usuario, bot = engine._user, engine._bot
        bot.conectado = False
        bot.falhas_de_rede = 5

        async def atrasado_falha():
            usuario.catch_ups += 1
            raise ConnectionError("sem resposta")

        usuario.catch_up = atrasado_falha
        usuario.cair()
        await _ate(lambda: usuario.catch_ups == 1)
        await _ate(lambda: False, voltas=5)
        primeira = (engine.state, engine._bot is bot)
        usuario.cair()
        await _ate(lambda: usuario.catch_ups == 2)
        await _ate(lambda: False, voltas=5)
        segunda = engine.state
        await engine.stop()
        return primeira, segunda

    primeira, segunda = run_value(cenario())
    assert primeira == (EngineState.ONLINE, True)
    assert segunda == EngineState.ONLINE
    assert sum("Reconectado" in m for m in logs) == 2
    assert any(m.startswith("Bot ainda desconectado") for m in logs)


def test_parar_no_meio_da_reconexao_nao_volta_a_ficar_online(tmp_path, monkeypatch):
    engine, estados, logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def cenario():
        assert await engine.start()
        cliente = engine._user

        async def conecta_depois_de_parar():
            await engine.stop()
            cliente.conectado = True

        cliente.connect = conecta_depois_de_parar
        cliente.cair()
        await _ate(lambda: False, voltas=50)
        return cliente

    cliente = run_value(cenario())
    assert engine.state == EngineState.OFFLINE
    assert estados[-1] == EngineState.OFFLINE
    assert cliente.conectado is False
    assert not any("Reconectado" in m for m in logs)


def test_sair_da_conta_com_erro_no_telegram_ainda_apaga_a_sessao(tmp_path, monkeypatch):
    engine, _estados, _logs = _motor_com_cliente_falso(monkeypatch, tmp_path, code_provider=_codigo_certo)

    async def log_out(self):
        raise ConnectionError("caiu")

    monkeypatch.setattr(ClienteFalso, "log_out", log_out, raising=False)
    (tmp_path / "user.session").write_bytes(b"x")

    async def cenario():
        assert await engine.start()
        return await engine.log_out()

    assert run_value(cenario()) is False
    assert engine.state == EngineState.OFFLINE
    assert not (tmp_path / "user.session").exists()


def test_sessao_que_o_windows_nao_deixa_apagar_nao_derruba(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr("watcher.engine.paths.sessions_dir", lambda: tmp_path)
    (tmp_path / "user.session").write_bytes(b"x")
    (tmp_path / "user.session-journal").write_bytes(b"x")
    original = Path.unlink

    def em_uso(self, *args, **kwargs):
        if self.name == "user.session":
            raise PermissionError(32, "arquivo em uso")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", em_uso)
    assert MonitorEngine.forget_session("user") is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["user.session"]
    assert MonitorEngine.forget_session("user") is False
