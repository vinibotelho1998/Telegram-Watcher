from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from watcher import paths
from watcher.config import AppConfig, _to_bool
from watcher.models import Channel, ChannelKind, Filter, Metrics
from watcher.storage import ChannelCache, FilterStore, read_json, write_json


def test_json_ausente_quebrado_ou_com_bytes_invalidos_volta_o_padrao(tmp_path):
    padrao = {"vazio": True}
    assert read_json(tmp_path / "nao_existe.json", padrao) is padrao
    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text('{"filtros": [', encoding="utf-8")
    assert read_json(quebrado, []) == []
    binario = tmp_path / "binario.json"
    binario.write_bytes(b"\xff\xfe\x00lixo")
    assert read_json(binario, []) == []


def test_dado_que_nao_vira_json_nao_apaga_o_arquivo_salvo(tmp_path):
    alvo = tmp_path / "dados.json"
    assert write_json(alvo, {"a": 1})
    assert write_json(alvo, {"conjunto": {1, 2}}) is False
    assert json.loads(alvo.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["dados.json"]


def test_cache_de_canais_ignora_item_quebrado_em_vez_de_travar_a_abertura(tmp_path):
    alvo = tmp_path / "channels_cache.json"
    alvo.write_text(
        json.dumps(["lixo", {"id": "abc"}, {"id": -1, "name": "Ofertas", "type": "group"}, {"id": 5, "kind": "planeta"}]),
        encoding="utf-8",
    )
    canais = ChannelCache(alvo).load()
    assert [(c.id, c.name, c.kind) for c in canais] == [
        (-1, "Ofertas", ChannelKind.GROUP),
        (5, "", ChannelKind.CHANNEL),
    ]


def test_cache_de_canais_que_nao_e_lista_volta_vazio(tmp_path):
    alvo = tmp_path / "channels_cache.json"
    alvo.write_text(json.dumps({"id": -1}), encoding="utf-8")
    assert ChannelCache(alvo).load() == []


def test_filtros_que_nao_sao_lista_voltam_vazio(tmp_path):
    alvo = tmp_path / "filters.json"
    alvo.write_text(json.dumps({"id": "a", "name": "Solto"}), encoding="utf-8")
    assert FilterStore(alvo).load() == []


def test_filtro_antigo_com_destino_unico_zerado_fica_sem_destino():
    flt = Filter.from_dict({"id": "a", "include_words": ["cupom"], "destination_id": 0})
    assert flt.destination_ids == []
    antigo = Filter.from_dict({"id": "b", "destination_id": "-100"})
    assert antigo.destination_ids == [-100]


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", " yes ", "On"])
def test_valores_ligados_no_env(valor):
    assert _to_bool(valor) is True


@pytest.mark.parametrize("valor", ["0", "false", "no", "off", "talvez"])
def test_valores_desligados_no_env(valor):
    assert _to_bool(valor, default=True) is False


def test_env_com_valores_invalidos_abre_com_padrao_seguro(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "API_ID=abc\nAPI_HASH=  h  \nPHONE_NUMBER=\nSOURCE_IDS=-1,,x, -2\nAUTO_START=talvez\nDARK_THEME=\nDEBUG=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    cfg = AppConfig.load()
    assert cfg.api_id == 0
    assert cfg.api_hash == "h"
    assert cfg.source_ids == [-1, -2]
    assert cfg.auto_start is False
    assert cfg.dark_theme is True
    assert cfg.debug is True
    assert not cfg.is_ready


def test_env_inexistente_abre_vazio_e_nao_pronto(tmp_path, monkeypatch):
    monkeypatch.setattr("watcher.paths.env_file", lambda: tmp_path / "sem.env")
    cfg = AppConfig.load()
    assert cfg == AppConfig()
    assert not cfg.is_ready


def test_recarregar_atualiza_o_mesmo_objeto_que_o_monitor_usa(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    em_uso = AppConfig(api_id=1, api_hash="velho", phone="+1", source_ids=[-1])
    assert em_uso.save()
    AppConfig(api_id=2, api_hash="novo", phone="+2", source_ids=[-7, -8], aliases={-7: "Novo"}).save()
    referencia = em_uso
    em_uso.reload()
    assert referencia is em_uso
    assert (em_uso.api_id, em_uso.api_hash, em_uso.phone) == (2, "novo", "+2")
    assert em_uso.source_ids == [-7, -8]
    assert em_uso.channel_name(-7) == "Novo"


def test_apelido_vazio_nao_e_gravado(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr("watcher.paths.env_file", lambda: env)
    AppConfig(aliases={-1: "", -2: "  Canal   com   espaços  "}).save()
    assert AppConfig.load().aliases == {-2: "Canal com espaços"}


def test_metricas_zeram_ao_reiniciar():
    m = Metrics()
    m.forwarded, m.failed, m.filtered, m.errors = 3, 2, 5, 1
    m.mark_capture()
    m.started_at = time.time() - 30
    m.reset()
    assert (m.received, m.forwarded, m.failed, m.filtered, m.errors) == (0, 0, 0, 0, 0)
    assert m.by_hour == [0] * 24
    assert m.started_at is None and m.last_message_at is None
    assert m.success_rate == 100.0


def test_tempo_ligado_nunca_fica_negativo():
    m = Metrics(started_at=time.time() + 3600)
    assert m.uptime == 0.0
    m.started_at = time.time() - 10
    assert 9 <= m.uptime <= 60


def test_canal_com_id_texto_numerico_e_convertido():
    canal = Channel.from_dict({"id": "-100", "name": 123})
    assert canal.id == -100 and canal.name == "123"
    assert Channel.from_dict(canal.to_dict()) == canal


@pytest.mark.skipif(os.name != "nt", reason="pasta de dados do Windows")
def test_pastas_do_app_ficam_dentro_do_appdata_isolado():
    raiz = Path(os.environ["APPDATA"])
    for pasta in (paths.data_dir(), paths.sessions_dir(), paths.logs_dir(), paths.autobuy_dir(),
                  paths.avatar_cache_dir(), paths.thumb_cache_dir(), paths.browser_profile_dir()):
        assert pasta.is_dir()
        assert raiz in pasta.parents
    assert paths.filters_file().parent == paths.data_dir()
    assert paths.env_file().parent == paths.app_dir()


def test_log_do_autobuy_grava_nivel_e_loja(tmp_path, monkeypatch):
    from watcher.autobuy import journal

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    journal.write("")
    assert not journal.log_path().exists()
    assert journal.tail() == ""
    journal.write("Produto no carrinho", "warning", "kabum")
    journal.separator("TESTE")
    journal.write("sem loja")
    linhas = journal.tail().splitlines()
    assert "WARNING [kabum] Produto no carrinho" in linhas[0]
    assert any("TESTE" in linha for linha in linhas)
    assert linhas[-1].endswith("INFO    sem loja")
    assert journal.tail(1) == linhas[-1]


def test_log_grande_e_cortado_guardando_o_fim(tmp_path, monkeypatch):
    from watcher.autobuy import journal

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    monkeypatch.setattr(journal, "_MAX_BYTES", 400)
    monkeypatch.setattr(journal, "_KEEP_TAIL", 120)
    for indice in range(30):
        journal.write(f"linha {indice:02d}")
    conteudo = journal.log_path().read_bytes()
    assert len(conteudo) <= 400
    assert b"linha 29" in conteudo
    assert b"linha 00" not in conteudo
    assert b"[log truncado]" in conteudo


def test_registros_filtrados_por_loja_e_limitados(tmp_path, monkeypatch):
    from watcher.autobuy import records
    from watcher.autobuy.models import BuyResult, BuyStatus

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    resultado = BuyResult(status=BuyStatus.PIX_READY, pix_code="000201x")
    for segundo, loja in enumerate(["kabum", "amazon", "kabum", "terabyte", "kabum"]):
        records.save_record(resultado, f"20260101-00000{segundo}", loja)
    assert [r.stamp for r in records.load_records(store="kabum")] == [
        "20260101-000004",
        "20260101-000002",
        "20260101-000000",
    ]
    assert [r.store for r in records.load_records(limit=2)] == ["kabum", "terabyte"]
    assert records.load_records(store="shopee") == []


def test_registro_antigo_sem_loja_descobre_a_loja_pela_url(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    antigos = {
        "20260101-000001": "https://www.kabum.com.br/produto/1/ssd",
        "20260101-000002": "https://www.amazon.com.br/dp/B0CGBQVDNK",
        "20260101-000003": "https://golpe.example/kabum.com.br/produto/1",
    }
    for carimbo, url in antigos.items():
        (tmp_path / f"{carimbo}-pix.json").write_text(
            json.dumps({"stamp": carimbo, "url": url, "price": "10.5"}), encoding="utf-8"
        )
    lojas = {r.stamp: r.store for r in records.load_records()}
    assert lojas == {"20260101-000001": "kabum", "20260101-000002": "amazon", "20260101-000003": ""}
    assert records.load_records(store="kabum")[0].price == 10.5


def test_configuracao_do_autobuy_corrige_intervalo_e_lojas_vazias():
    from watcher.autobuy.storage import AutobuySettings

    assert AutobuySettings.from_dict({"check_hours": 0}).check_hours == 6
    assert AutobuySettings.from_dict({"check_hours": -3}).check_hours == 1
    assert AutobuySettings.from_dict({"check_hours": "12"}).check_hours == 12
    assert AutobuySettings.from_dict({"known_stores": ["kabum", "", "amazon"]}).known_stores == ["kabum", "amazon"]
    assert AutobuySettings.from_dict({"known_stores": None}).known_stores == []
    padrao = AutobuySettings.from_dict({})
    assert (padrao.enabled, padrao.real_mode, padrao.use_coupon, padrao.headless) == (False, False, True, False)


def test_apelido_com_id_que_nao_e_numero_e_ignorado():
    from watcher.config import _parse_aliases

    assert _parse_aliases("abc:Lixo,-1:Canal A,-2:Outro") == {-1: "Canal A", -2: "Outro"}
    assert _parse_aliases("") == {}


def test_arquivo_travado_o_tempo_todo_desiste_sem_perder_o_que_estava_salvo(tmp_path, monkeypatch):
    from watcher import storage

    alvo = tmp_path / "filters.json"
    assert write_json(alvo, [{"id": "a"}])
    tentativas: list[int] = []

    def sempre_travado(_origem, _destino):
        tentativas.append(1)
        raise PermissionError(32, "arquivo em uso")

    monkeypatch.setattr(storage.os, "replace", sempre_travado)
    monkeypatch.setattr(storage.time, "sleep", lambda _segundos: None)
    assert write_json(alvo, [{"id": "b"}]) is False
    monkeypatch.undo()
    assert len(tentativas) == storage._REPLACE_ATTEMPTS
    assert json.loads(alvo.read_text(encoding="utf-8")) == [{"id": "a"}]
    assert [p.name for p in tmp_path.iterdir()] == ["filters.json"]


def test_log_do_autobuy_sem_acesso_ao_arquivo_nao_derruba_a_compra(tmp_path, monkeypatch):
    from watcher.autobuy import journal

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    journal.log_path().mkdir()
    journal.write("Produto no carrinho", "info", "kabum")
    journal.separator("TESTE")
    assert journal.tail() == ""


def test_apagar_registro_com_arquivo_em_uso_apaga_o_resto(tmp_path, monkeypatch):
    from watcher.autobuy import records

    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    carimbo = "20260101-000001"
    (tmp_path / f"{carimbo}-pix.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"{carimbo}-pix.png").write_bytes(b"png")
    original = Path.unlink

    def em_uso(self, *args, **kwargs):
        if self.suffix == ".png":
            raise PermissionError(32, "aberto no visualizador")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", em_uso)
    assert records.delete_record(carimbo) == 1
    assert [p.name for p in tmp_path.iterdir()] == [f"{carimbo}-pix.png"]


def test_autobuy_json_que_nao_e_objeto_abre_vazio_e_desligado(tmp_path):
    from watcher.autobuy.storage import AutobuyStore

    alvo = tmp_path / "autobuy.json"
    for conteudo in ("[1, 2]", "null", "{quebrado"):
        alvo.write_text(conteudo, encoding="utf-8")
        settings, items = AutobuyStore(alvo).load()
        assert settings.enabled is False and settings.real_mode is False
        assert items == []
