import asyncio
from datetime import datetime, timezone

from watcher.filtering import matching_filters
from watcher.models import ChannelKind, Filter
from watcher.reader import (
    ChatMessage,
    ChatReader,
    build_message,
    build_summary,
    detect_media,
    dialog_kind,
    preview_text,
)


class FakeAttribute:
    def __init__(self, file_name):
        self.file_name = file_name


class FakeDocument:
    def __init__(self, file_name="oferta.pdf"):
        self.attributes = [FakeAttribute(file_name)]
        self.mime_type = "application/pdf"


class FakeSender:
    def __init__(self, first_name="", last_name="", username=None, title=None):
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.title = title


class FakeMessage:
    def __init__(self, mid=1, text="", **extras):
        self.id = mid
        self.message = text
        self.date = datetime(2025, 3, 12, 15, 4, tzinfo=timezone.utc)
        self.sender_id = 77
        self.sender = FakeSender("Ana", "Souza")
        self.out = False
        self.views = 0
        self.edit_date = None
        self.action = None
        self.reply_to = None
        self.post_author = None
        self.photo = None
        self.video = None
        self.video_note = None
        self.voice = None
        self.audio = None
        self.sticker = None
        self.gif = None
        self.poll = None
        self.contact = None
        self.geo = None
        self.document = None
        self.media = None
        for key, value in extras.items():
            setattr(self, key, value)


class FakeEntity:
    def __init__(self, username="canal", participants_count=1200):
        self.username = username
        self.participants_count = participants_count


class FakeDialog:
    def __init__(self, chat_id=-100, name="Ofertas", message=None, group=False, unread=3):
        self.id = chat_id
        self.name = name
        self.message = message
        self.entity = FakeEntity()
        self.is_channel = True
        self.is_group = group
        self.unread_count = unread
        self.pinned = False
        self.date = None


class FakeClient:
    def __init__(self, messages=None, dialogs=None, connected=True):
        self._messages = messages or []
        self._dialogs = dialogs or []
        self._connected = connected
        self.calls = []

    def is_connected(self):
        return self._connected

    async def iter_messages(self, chat_id, **kwargs):
        self.calls.append((chat_id, kwargs))
        for message in self._messages:
            yield message

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs:
            yield dialog


def run(coro):
    return asyncio.run(coro)


def test_detecta_tipo_de_midia():
    assert detect_media(FakeMessage(photo=object()))[0] == "photo"
    assert detect_media(FakeMessage(voice=object()))[0] == "voice"
    assert detect_media(FakeMessage(document=FakeDocument()))[1] == "oferta.pdf"
    assert detect_media(FakeMessage())[0] == ""


def test_monta_mensagem_com_remetente_e_hora():
    message = build_message(FakeMessage(5, "Cupom TESTE10"), -100)
    assert message.id == 5
    assert message.chat_id == -100
    assert message.text == "Cupom TESTE10"
    assert message.sender_name == "Ana Souza"
    assert message.outgoing is False


def test_previa_usa_rotulo_da_midia_quando_nao_ha_texto():
    message = build_message(FakeMessage(9, "", photo=object()), -100)
    assert preview_text(message) == "Foto"


def test_previa_encurta_texto_longo():
    message = ChatMessage(id=1, chat_id=-1, text="a" * 200)
    assert len(preview_text(message, limit=30)) == 30


def test_resumo_do_dialogo_traz_previa_e_nao_lidas():
    summary = build_summary(FakeDialog(message=FakeMessage(2, "Frete grátis hoje")))
    assert summary.name == "Ofertas"
    assert summary.preview == "Frete grátis hoje"
    assert summary.unread == 3
    assert summary.username == "canal"
    assert summary.kind == ChannelKind.CHANNEL


def test_grupo_tem_prioridade_sobre_canal():
    assert dialog_kind(FakeDialog(group=True)) == ChannelKind.GROUP


def test_historico_devolve_do_mais_antigo_para_o_mais_novo():
    client = FakeClient(messages=[FakeMessage(30, "novo"), FakeMessage(29, "antigo")])
    reader = ChatReader(lambda: client)
    messages = run(reader.history(-100, limit=2))
    assert [m.id for m in messages] == [29, 30]


def test_historico_pagina_a_partir_do_id():
    client = FakeClient(messages=[FakeMessage(10, "x")])
    reader = ChatReader(lambda: client)
    run(reader.history(-100, limit=5, before_id=11))
    assert client.calls[0][1]["max_id"] == 11


def test_sem_cliente_conectado_nao_le_nada():
    reader = ChatReader(lambda: None)
    assert run(reader.history(-100)) == []
    assert run(reader.dialogs()) == []
    assert reader.available is False


def test_busca_vazia_nao_chama_telegram():
    client = FakeClient(messages=[FakeMessage(1, "x")])
    reader = ChatReader(lambda: client)
    assert run(reader.search(-100, "   ")) == []
    assert client.calls == []


def test_filtros_que_casam_ignoram_desligados_e_excluidos():
    ativo = Filter(id="a", name="Cupons", include=["cupom"])
    desligado = Filter(id="b", name="Off", enabled=False, include=["cupom"])
    bloqueio = Filter(id="c", name="Bloqueio", exclude=["shein"])
    filtros = [ativo, desligado, bloqueio]
    assert [f.id for f in matching_filters(filtros, -1, "cupom novo")] == ["a"]
    assert matching_filters(filtros, -1, "cupom shein") == []


class DownloadClient:
    def __init__(self, fail: bool):
        self.fail = fail

    def is_connected(self):
        return True

    def _write(self, file):
        with open(file, "wb") as handle:
            handle.write(b"meia" if self.fail else b"imagem-completa")
        if self.fail:
            raise ConnectionError("conexao caiu no meio")
        return file

    async def download_media(self, message, file=None, thumb=None):
        return self._write(file)

    async def download_profile_photo(self, entity, file=None):
        return self._write(file)


def test_miniatura_interrompida_nao_fica_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("watcher.reader.paths.thumb_cache_dir", lambda: tmp_path)
    clients = {"atual": DownloadClient(fail=True)}
    reader = ChatReader(lambda: clients["atual"])
    reader.remember(-100, FakeMessage(5, "", photo=object()))
    assert run(reader.thumbnail(-100, 5)) is None
    clients["atual"] = DownloadClient(fail=False)
    path = run(reader.thumbnail(-100, 5))
    assert path is not None and path.read_bytes() == b"imagem-completa"


def test_avatar_interrompido_nao_fica_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("watcher.reader.paths.avatar_cache_dir", lambda: tmp_path)
    reader = ChatReader(lambda: DownloadClient(fail=True))
    assert run(reader.avatar(-100)) is None
    reader = ChatReader(lambda: DownloadClient(fail=False))
    path = run(reader.avatar(-100))
    assert path is not None and path.read_bytes() == b"imagem-completa"
