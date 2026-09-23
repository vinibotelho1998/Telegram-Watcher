from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from watcher import paths
from watcher.autobuy import registry, urls
from watcher.autobuy.models import BuyResult, BuyStatus, ProductInfo, WatchItem, status_label, yes_no
from watcher.autobuy.notify import build_message, headline
from watcher.autobuy.resolver import extract_urls, find_kabum_product, resolve


@pytest.mark.parametrize(
    "texto, nome",
    [
        ("Olá, VINICIUS", "VINICIUS"),
        ("olá,   Maria Clara.", "Maria Clara"),
        ("Olá, Ana!\nMinha conta", "Ana"),
        ("Olá, faça seu login", None),
        ("Ola, Entre ou cadastre-se", None),
        ("Olá, minha conta", None),
        ("Olá, ", None),
        ("Bem-vindo à loja", None),
        ("", None),
    ],
)
def test_nome_do_cliente_logado_na_loja(texto, nome):
    assert registry.get("kabum").logged_user(texto) == nome


def test_nome_do_cliente_muito_longo_e_cortado():
    assert registry.get("amazon").logged_user("Olá, " + "X" * 60) == "X" * 40


def test_loja_procurada_sem_diferenca_de_caixa_ou_espaco():
    assert registry.get(" KaBuM ").id == "kabum"
    assert registry.get("") is None
    assert registry.get(None) is None
    assert registry.get("pichau") is None


def test_terabyte_nunca_roda_com_navegador_escondido():
    assert registry.get("terabyte").headless_allowed(True) is False
    assert registry.get("kabum").headless_allowed(True) is True
    assert registry.get("kabum").headless_allowed(False) is False


def test_link_direto_da_amazon_vira_endereco_canonico():
    texto = "PS5 https://www.amazon.com.br/Console-PS5/dp/B0CGBQVDNK/ref=sr_1_1?th=1 e https://t.me/grupo"
    assert [(spec.id, url) for spec, url in registry.direct_products(texto)] == [
        ("amazon", "https://www.amazon.com.br/dp/B0CGBQVDNK")
    ]


def test_loja_permitida_ignora_caixa_e_espaco(monkeypatch):
    monkeypatch.setattr(registry, "resolve", lambda url: url)
    texto = "SSD https://www.kabum.com.br/produto/1/ssd"
    assert registry.find_product(texto, [" KABUM "])[0].id == "kabum"
    assert registry.find_product(texto, ["amazon"]) is None
    assert registry.find_product(texto, ["", "  "])[0].id == "kabum"


def test_encurtador_ja_de_loja_nao_e_aberto_de_novo(monkeypatch):
    abertos: list[str] = []

    def resolve_falso(url):
        abertos.append(url)
        return "https://www.kabum.com.br/produto/2/x"

    monkeypatch.setattr(registry, "resolve", resolve_falso)
    texto = "https://www.kabum.com.br/produto/1/ssd https://encurta.example/a"
    links = registry.resolved_products(texto)
    assert abertos == ["https://encurta.example/a"]
    assert [url for _spec, url in links] == ["https://www.kabum.com.br/produto/2/x"]


@pytest.mark.parametrize(
    "link, loja, codigo",
    [
        ("https://www.kabum.com.br:443/produto/5", "kabum", 5),
        ("http://kabum.com.br/produto/7/x", "kabum", 7),
        ("https://amazon.com.br/dp/b0cgbqvdnk", "amazon", "B0CGBQVDNK"),
        ("https://www.amazon.com/gp/product/B0C9DWTTQF", "amazon", "B0C9DWTTQF"),
        ("https://www.terabyteshop.com.br/produto/25639/ssd", "terabyte", 25639),
    ],
)
def test_codigo_do_produto_em_cada_loja(link, loja, codigo):
    leitor = {"kabum": urls.kabum_id, "amazon": urls.amazon_id, "terabyte": urls.terabyte_id}[loja]
    assert leitor(link) == codigo
    assert registry.match_url(link).id == loja


def test_link_de_loja_sem_pagina_de_produto_nao_e_reconhecido():
    for link in (
        "https://www.kabum.com.br/",
        "https://www.kabum.com.br/busca/ssd",
        "https://www.amazon.com.br/s?k=ssd",
        "https://www.terabyteshop.com.br/hardware",
        "ftp://www.kabum.com.br/produto/1",
        "",
    ):
        assert registry.match_url(link) is None, link
    assert urls.amazon_canonical("https://www.amazon.com.br/s?k=ssd") == "https://www.amazon.com.br/s?k=ssd"


def test_link_termina_antes_de_parenteses_colchetes_e_aspas():
    texto = 'veja (https://a.com/x) e [https://b.com/y] "https://c.com/z" <https://d.com/w>'
    assert extract_urls(texto) == ["https://a.com/x", "https://b.com/y", "https://c.com/z", "https://d.com/w"]
    assert extract_urls(None) == []


def test_encurtador_que_nao_abre_devolve_o_proprio_link():
    assert resolve("https://bit.ly/abc") == "https://bit.ly/abc"


def test_encurtador_segue_o_redirecionamento_ate_a_loja(monkeypatch):
    pedidos: list[tuple] = []

    class Resposta:
        url = "https://www.kabum.com.br/produto/1/ssd"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def abre(pedido, timeout):
        pedidos.append((pedido.full_url, pedido.get_header("User-agent"), timeout))
        return Resposta()

    monkeypatch.setattr("urllib.request.urlopen", abre)
    assert resolve("https://bit.ly/abc") == "https://www.kabum.com.br/produto/1/ssd"
    assert resolve("https://bit.ly/abc", timeout=3) == "https://www.kabum.com.br/produto/1/ssd"
    assert [(url, tempo) for url, _agente, tempo in pedidos] == [("https://bit.ly/abc", 10.0), ("https://bit.ly/abc", 3)]
    assert all(agente and agente.startswith("Mozilla/5.0") for _url, agente, _tempo in pedidos)


def test_cada_loja_usa_o_seu_proprio_robo_de_compra():
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy.amazon import AmazonDriver
    from watcher.autobuy.kabum import KabumDriver
    from watcher.autobuy.terabyte import TerabyteDriver

    esperado = {"kabum": KabumDriver, "amazon": AmazonDriver, "terabyte": TerabyteDriver}
    contexto = object()
    for loja, classe in esperado.items():
        robo = registry.get(loja).driver(contexto, headless=True)
        assert type(robo) is classe, loja
        assert robo._context is contexto and robo._headless is True, loja


def test_kabum_encontrada_atras_de_encurtador(monkeypatch):
    from watcher.autobuy import resolver

    monkeypatch.setattr(resolver, "resolve", lambda url: "https://www.kabum.com.br/produto/9/x" if "bit.ly" in url else url)
    assert find_kabum_product("promo https://t.me/canal https://bit.ly/abc") == "https://www.kabum.com.br/produto/9/x"


def test_rotulo_de_status_desconhecido_e_sim_nao():
    assert status_label(BuyStatus.LOGIN_REQUIRED).startswith("conta da loja desconectada")
    assert status_label("estranho") == "estranho"
    assert (yes_no(True), yes_no(False)) == ("sim", "não")


def test_produto_antigo_com_quantidade_ou_preco_invalido_fica_seguro():
    item = WatchItem.from_dict({"id": "w", "name": "", "max_price": None, "quantity": -5})
    assert item.quantity == 1
    assert item.max_price == 0.0
    assert item.name == "Sem nome"
    assert WatchItem.from_dict({"quantity": 0}).quantity == 1
    assert WatchItem.new().id.startswith("w") and WatchItem.new().name == "Novo produto"


def test_aviso_completo_com_produto_detalhe_tempo_e_codigo():
    produto = ProductInfo(id=1, title="SSD 2TB", url="https://x/p", pix_price=289.9, card_price=320.0, available=True)
    resultado = BuyResult(
        status=BuyStatus.PIX_READY,
        product=produto,
        pix_code="000201abc",
        detail="Cupom X aplicado",
        elapsed=12.34,
        timings={"produto": 1.0, "etapa-nova": 2.25},
    )
    linhas = build_message(resultado).splitlines()
    assert linhas == [
        "[Watcher] PIX gerado pelo Auto-Compra",
        "SSD 2TB",
        "À vista no PIX: R$ 289.90",
        "https://x/p",
        "Cupom X aplicado",
        "Tempo: 12,3s (produto 1,0s | etapa-nova 2,2s)",
        "",
        "Copia e cola:",
        "000201abc",
    ]


def test_aviso_sem_tempo_nem_codigo_fica_curto():
    assert build_message(BuyResult(status=BuyStatus.UNAVAILABLE)) == (
        "[Watcher] Auto-Compra: compra NÃO feita, produto sem estoque"
    )
    assert headline(BuyResult(status=BuyStatus.DRY_RUN_OK)).startswith("Auto-Compra: simulação")


class _ClienteTelegramFalso:
    criados: list["_ClienteTelegramFalso"] = []
    autorizado = True
    falha_no_envio = False

    def __init__(self, sessao, *_args, **_kwargs):
        self.sessao = sessao
        self.enviados: list[tuple] = []
        self.desconectado = False
        _ClienteTelegramFalso.criados.append(self)

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return self.autorizado

    async def send_message(self, destino, texto):
        if self.falha_no_envio:
            raise ConnectionError("caiu")
        self.enviados.append(("texto", destino, texto))

    async def send_file(self, destino, arquivo):
        self.enviados.append(("arquivo", destino, arquivo))

    async def disconnect(self):
        self.desconectado = True


def _aviso_com_cliente_falso(monkeypatch, tmp_path, **atributos):
    from watcher.autobuy import notify

    _ClienteTelegramFalso.criados = []
    for nome, valor in atributos.items():
        monkeypatch.setattr(_ClienteTelegramFalso, nome, valor)
    monkeypatch.setattr(notify, "TelegramClient", _ClienteTelegramFalso)
    monkeypatch.setattr(paths, "sessions_dir", lambda: tmp_path)
    return notify


def test_aviso_de_pix_sem_credencial_nao_abre_conexao(monkeypatch, tmp_path):
    from watcher.config import AppConfig

    notify = _aviso_com_cliente_falso(monkeypatch, tmp_path)
    resultado = BuyResult(status=BuyStatus.PIX_READY, pix_code="000201")
    assert asyncio.run(notify.send_pix_notification(AppConfig(), resultado)) is False
    assert _ClienteTelegramFalso.criados == []


def test_aviso_de_pix_manda_texto_e_print_para_mensagens_salvas(monkeypatch, tmp_path):
    from watcher.config import AppConfig

    notify = _aviso_com_cliente_falso(monkeypatch, tmp_path)
    resultado = BuyResult(status=BuyStatus.PIX_READY, pix_code="000201", screenshot="print.png")
    assert asyncio.run(notify.send_pix_notification(AppConfig(api_id=1, api_hash="h"), resultado)) is True
    cliente = _ClienteTelegramFalso.criados[0]
    assert cliente.sessao == str(tmp_path / "user")
    assert [(tipo, destino) for tipo, destino, _ in cliente.enviados] == [("texto", "me"), ("arquivo", "me")]
    assert cliente.desconectado


@pytest.mark.parametrize("atributos", [{"autorizado": False}, {"falha_no_envio": True}])
def test_aviso_de_pix_que_falha_desconecta_e_devolve_falso(monkeypatch, tmp_path, atributos):
    from watcher.config import AppConfig

    notify = _aviso_com_cliente_falso(monkeypatch, tmp_path, **atributos)
    resultado = BuyResult(status=BuyStatus.PIX_READY, pix_code="000201")
    assert asyncio.run(notify.send_pix_notification(AppConfig(api_id=1, api_hash="h"), resultado)) is False
    cliente = _ClienteTelegramFalso.criados[0]
    assert cliente.enviados == []
    assert cliente.desconectado


_EPOCA_CHROME = 11644473600


def _perfil_com_cookies(tmp_path, monkeypatch, linhas):
    pytest.importorskip("playwright.async_api")
    import tempfile

    perfil = tmp_path / "perfil"
    banco = perfil / "Default" / "Network" / "Cookies"
    banco.parent.mkdir(parents=True)
    con = sqlite3.connect(banco)
    con.execute("create table cookies (host_key text, name text, is_persistent integer, expires_utc integer)")
    for host, nome, persistente, expira in linhas:
        con.execute(
            "insert into cookies values (?, ?, ?, ?)",
            (host, nome, persistente, (expira + _EPOCA_CHROME) * 1_000_000 if expira else 0),
        )
    con.commit()
    con.close()
    copias = tmp_path / "copias"
    copias.mkdir()
    monkeypatch.setattr(paths, "browser_profile_dir", lambda: perfil)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(copias))
    from watcher.autobuy import browser

    return browser, copias


def test_sessao_da_loja_lida_do_perfil_do_navegador(tmp_path, monkeypatch):
    agora = int(time.time())
    browser, copias = _perfil_com_cookies(
        tmp_path,
        monkeypatch,
        [
            (".kabum.com.br", "session", 0, 0),
            (".amazon.com.br", "at-acbbr", 1, agora - 60),
            (".terabyteshop.com.br", "PHPSESSID", 1, agora + 86400 * 3),
            (".terabyteshop.com.br", "outro", 1, agora + 60),
        ],
    )
    assert browser.has_session_cookies("kabum", ("session", "id_cliente")) is True
    assert browser.has_session_cookies("amazon", ("at-acbbr",)) is False
    assert browser.has_session_cookies("terabyte", ("PHPSESSID",)) is True
    assert browser.has_session_cookies("shopee", ("session",)) is False
    assert browser.has_session_cookies("kabum", ()) is None
    assert browser.session_expiry("kabum", ("session",)) is None
    assert abs(browser.session_expiry("terabyte", ("PHPSESSID",)) - (agora + 86400 * 3)) <= 1
    assert list(copias.iterdir()) == []


def test_sem_perfil_do_navegador_ninguem_esta_logado(tmp_path, monkeypatch):
    browser, _copias = _perfil_com_cookies(tmp_path, monkeypatch, [])
    monkeypatch.setattr(paths, "browser_profile_dir", lambda: tmp_path / "vazio")
    assert browser.has_session_cookies("kabum", ("session",)) is False
    assert browser.session_expiry("kabum", ("session",)) is None
    assert browser.profile_in_use() is False


def test_perfil_travado_por_outra_janela_e_detectado(tmp_path, monkeypatch):
    browser, _copias = _perfil_com_cookies(tmp_path, monkeypatch, [])

    def travado(*_args, **_kwargs):
        raise PermissionError(13, "em uso")

    assert browser.profile_in_use() is False
    monkeypatch.setattr(browser, "open", travado, raising=False)
    assert browser.profile_in_use() is True

    def outro_erro(*_args, **_kwargs):
        raise OSError(5, "erro qualquer")

    monkeypatch.setattr(browser, "open", outro_erro, raising=False)
    assert browser.profile_in_use() is False


def test_navegador_nao_abre_com_o_perfil_travado(tmp_path, monkeypatch):
    browser, _copias = _perfil_com_cookies(tmp_path, monkeypatch, [])
    abertos: list[bool] = []

    class Chromium:
        async def launch_persistent_context(self, *_args, **_kwargs):
            abertos.append(True)
            raise RuntimeError("perfil em uso")

    playwright = type("P", (), {"chromium": Chromium()})()
    monkeypatch.setattr(browser, "profile_in_use", lambda: True)
    with pytest.raises(browser.ProfileInUse):
        asyncio.run(browser.open_context(playwright))
    assert abertos == []
    monkeypatch.setattr(browser, "profile_in_use", lambda: False)
    with pytest.raises(browser.ProfileInUse, match="perfil em uso"):
        asyncio.run(browser.open_context(playwright))
    assert abertos == [True]


def test_banco_de_cookies_corrompido_ou_sem_copia_conta_como_sem_sessao(tmp_path, monkeypatch):
    import shutil

    browser, copias = _perfil_com_cookies(tmp_path, monkeypatch, [])
    banco = tmp_path / "perfil" / "Default" / "Network" / "Cookies"
    banco.write_bytes(b"isto nao e um banco sqlite " * 20)
    assert browser.has_session_cookies("kabum", ("session",)) is False
    assert browser.session_expiry("kabum", ("session",)) is None
    assert list(copias.iterdir()) == []

    def sem_copia(*_args, **_kwargs):
        raise PermissionError(13, "arquivo travado pelo chrome")

    monkeypatch.setattr(shutil, "copy2", sem_copia)
    assert browser.has_session_cookies("kabum", ("session",)) is False


def test_navegador_abre_no_perfil_do_robo_e_devolve_a_sessao_guardada(tmp_path, monkeypatch):
    import json

    browser, _copias = _perfil_com_cookies(tmp_path, monkeypatch, [])
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    (tmp_path / "session_cookies.json").write_text(json.dumps([{"name": "session", "value": "a"}]), encoding="utf-8")
    contexto = _ContextoCookies()
    pedidos: list[tuple] = []

    class Chromium:
        async def launch_persistent_context(self, pasta, **opcoes):
            pedidos.append((pasta, opcoes["headless"], opcoes["locale"]))
            return contexto

    playwright = type("P", (), {"chromium": Chromium()})()
    assert asyncio.run(browser.open_context(playwright, headless=False)) is contexto
    assert pedidos == [(str(tmp_path / "perfil"), False, "pt-BR")]
    assert [c["name"] for c in contexto.adicionados] == ["session"]

    class ContextoFechado:
        async def cookies(self):
            raise RuntimeError("navegador já fechou")

    assert asyncio.run(browser.save_session_cookies(ContextoFechado())) == 0


class _ContextoCookies:
    def __init__(self, cookies=None, falha_ao_adicionar=False, falha_ao_fechar=False):
        self._cookies = cookies or []
        self.adicionados: list = []
        self.fechado = False
        self._falha_ao_adicionar = falha_ao_adicionar
        self._falha_ao_fechar = falha_ao_fechar

    async def cookies(self):
        return self._cookies

    async def add_cookies(self, cookies):
        if self._falha_ao_adicionar:
            raise RuntimeError("cookie invalido")
        self.adicionados.extend(cookies)

    async def close(self):
        self.fechado = True
        if self._falha_ao_fechar:
            raise RuntimeError("ja fechado")


def test_so_cookies_de_sessao_sao_guardados_e_devolvidos(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    cookies = [
        {"name": "session", "value": "a", "expires": -1},
        {"name": "id_cliente", "value": "b"},
        {"name": "lembrar", "value": "c", "expires": time.time() + 3600},
    ]
    assert asyncio.run(browser.save_session_cookies(_ContextoCookies(cookies))) == 2
    novo = _ContextoCookies()
    assert asyncio.run(browser.restore_session_cookies(novo)) == 2
    assert [c["name"] for c in novo.adicionados] == ["session", "id_cliente"]
    assert asyncio.run(browser.save_session_cookies(_ContextoCookies([cookies[2]]))) == 0
    assert asyncio.run(browser.restore_session_cookies(_ContextoCookies())) == 2


@pytest.mark.parametrize("conteudo", ["{quebrado", '{"name": "session"}', "[]", '"texto"'])
def test_arquivo_de_sessao_invalido_nao_restaura_nada(tmp_path, monkeypatch, conteudo):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    assert asyncio.run(browser.restore_session_cookies(_ContextoCookies())) == 0
    (tmp_path / "session_cookies.json").write_text(conteudo, encoding="utf-8")
    contexto = _ContextoCookies()
    assert asyncio.run(browser.restore_session_cookies(contexto)) == 0
    assert contexto.adicionados == []


def test_loja_que_recusa_cookie_antigo_nao_derruba_a_abertura(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    (tmp_path / "session_cookies.json").write_text('[{"name": "x", "value": "1"}]', encoding="utf-8")
    assert asyncio.run(browser.restore_session_cookies(_ContextoCookies(falha_ao_adicionar=True))) == 0


def test_fechar_o_navegador_guarda_a_sessao_e_encerra_o_chrome_real(tmp_path, monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    encerrados: list[bool] = []
    processo = type("Proc", (), {"terminate": lambda self: encerrados.append(True)})()
    monkeypatch.setattr(browser, "_cdp_process", processo)
    contexto = _ContextoCookies([{"name": "session", "value": "a", "expires": -1}], falha_ao_fechar=True)
    asyncio.run(browser.close_context(contexto))
    assert contexto.fechado
    assert encerrados == [True]
    assert browser._cdp_process is None
    assert (tmp_path / "session_cookies.json").exists()
    browser.close_real_chrome()
    assert encerrados == [True]


class _Elemento:
    def __init__(self, nome, visivel=True, habilitado=True, falha=False):
        self.nome = nome
        self.visivel = visivel
        self.habilitado = habilitado
        self.falha = falha
        self.cliques = 0

    async def is_visible(self):
        if self.falha:
            raise RuntimeError("sumiu")
        return self.visivel

    async def is_enabled(self):
        return self.habilitado

    async def click(self, timeout=None):
        self.cliques += 1

    async def count(self):
        return 1

    @property
    def first(self):
        return self


class _Lista:
    def __init__(self, elementos, falha_na_contagem=False):
        self.elementos = elementos
        self.falha_na_contagem = falha_na_contagem

    async def count(self):
        if self.falha_na_contagem:
            raise RuntimeError("pagina recarregando")
        return len(self.elementos)

    def nth(self, indice):
        return self.elementos[indice]

    @property
    def first(self):
        return self.elementos[0]


class _PaginaDom:
    def __init__(self, por_seletor=None, botoes=None, textos=None, corpo="", falha_no_texto=False):
        self.por_seletor = por_seletor or {}
        self.botoes = botoes or {}
        self.textos = textos or {}
        self.corpo = corpo
        self.falha_no_texto = falha_no_texto
        self.leituras = 0

    def locator(self, seletor):
        return self.por_seletor.get(seletor, _Lista([]))

    def get_by_role(self, papel, name=None):
        if papel != "button":
            return _Lista([])
        return _Lista([el for padrao, els in self.botoes.items() if name.search(padrao) for el in els])

    def get_by_text(self, padrao):
        return _Lista([el for texto, el in self.textos.items() if padrao.search(texto)] or [_Elemento("nada", visivel=False)])

    async def evaluate(self, _script):
        self.leituras += 1
        if self.falha_no_texto:
            raise RuntimeError("navegador fechou")
        return self.corpo() if callable(self.corpo) else self.corpo


def test_clique_pula_elemento_escondido_ou_desabilitado():
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy.dom import click_first

    escondido = _Elemento("a", visivel=False)
    desligado = _Elemento("b", habilitado=False)
    quebrado = _Elemento("c", falha=True)
    certo = _Elemento("d")
    pagina = _PaginaDom(
        por_seletor={
            "#instavel": _Lista([], falha_na_contagem=True),
            "#botao": _Lista([escondido, desligado, quebrado, certo]),
        }
    )
    assert asyncio.run(click_first(pagina, ("#nao-existe", "#instavel", "#botao"))) is True
    assert (escondido.cliques, desligado.cliques, quebrado.cliques, certo.cliques) == (0, 0, 0, 1)
    assert asyncio.run(click_first(pagina, ("#nao-existe",))) is False


def test_botao_procurado_pelo_nome_so_se_estiver_clicavel():
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy.dom import find_button, find_clickable

    desligado = _Elemento("finalizar", habilitado=False)
    pagina = _PaginaDom(botoes={"Finalizar pedido": [desligado]}, textos={"Copiar código Pix": _Elemento("texto")})
    assert asyncio.run(find_button(pagina, (r"finalizar",))) is None
    desligado.habilitado = True
    assert asyncio.run(find_button(pagina, (r"finalizar",))) is desligado
    assert asyncio.run(find_clickable(pagina, (r"copiar\s+c[oó]digo",))).nome == "texto"
    assert asyncio.run(find_clickable(pagina, (r"cancelar",))) is None


def test_espera_a_pagina_parar_de_carregar(monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import dom

    import types

    async def sem_espera(_segundos):
        return None

    monkeypatch.setattr(dom, "asyncio", types.SimpleNamespace(sleep=sem_espera))
    respostas = iter(["Carregando...", "CARREGANDO", "Carrinho pronto"])
    pagina = _PaginaDom(corpo=lambda: next(respostas))
    asyncio.run(dom.wait_page_ready(pagina))
    assert pagina.leituras == 3
    assert asyncio.run(dom.page_text(_PaginaDom(falha_no_texto=True))) == ""


_PIX_VALIDO = (
    "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
    "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D"
)


class _PaginaPix(_PaginaDom):
    def __init__(self, campos=(), corpo="", **kwargs):
        super().__init__(corpo=corpo, **kwargs)
        self.campos = list(campos)

    async def eval_on_selector_all(self, _seletor, _script):
        return self.campos


def test_codigo_pix_vem_do_campo_antes_do_texto_da_pagina():
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import pix

    pagina = _PaginaPix(campos=["", "lixo 000201 sem marca", _PIX_VALIDO], corpo="nada")
    assert asyncio.run(pix.extract(pagina)) == _PIX_VALIDO
    so_no_texto = _PaginaPix(campos=["qualquer"], corpo=f"Pague com o código:\n{_PIX_VALIDO}\nObrigado")
    assert asyncio.run(pix.extract(so_no_texto)) == _PIX_VALIDO
    assert asyncio.run(pix.extract(_PaginaPix(corpo="aguardando pagamento"))) == ""


def test_espera_do_pix_desiste_no_prazo(monkeypatch):
    pytest.importorskip("playwright.async_api")
    from watcher.autobuy import pix

    import types

    relogio = [0.0]

    async def avanca(segundos):
        relogio[0] += segundos

    monkeypatch.setattr(pix, "time", types.SimpleNamespace(monotonic=lambda: relogio[0]))
    monkeypatch.setattr(pix, "asyncio", types.SimpleNamespace(sleep=avanca))
    pagina = _PaginaPix(corpo="gerando o pix")
    assert asyncio.run(pix.wait_for(pagina, timeout=10)) == ""
    assert relogio[0] >= 10
    chegando = iter(["gerando", "gerando", _PIX_VALIDO])
    relogio[0] = 0.0
    assert asyncio.run(pix.wait_for(_PaginaPix(corpo=lambda: next(chegando)), timeout=60)) == _PIX_VALIDO


class _PaginaComBotaoDeCopiar(_PaginaPix):
    def __init__(self, area_de_transferencia, **kwargs):
        import types

        self.botao = _Elemento("copiar")
        super().__init__(botoes={"Copiar código Pix": [self.botao]}, **kwargs)
        self.area = area_de_transferencia
        self.permissoes: list[str] = []
        self.context = types.SimpleNamespace(grant_permissions=self._concede)

    async def _concede(self, permissoes):
        self.permissoes.extend(permissoes)

    async def evaluate(self, script):
        if "clipboard" in script:
            if isinstance(self.area, Exception):
                raise self.area
            return self.area
        return await super().evaluate(script)


def test_codigo_pix_pelo_botao_de_copiar_e_campo_quando_a_area_de_transferencia_falha(monkeypatch):
    pytest.importorskip("playwright.async_api")
    import types

    from watcher.autobuy import pix

    async def sem_espera(_segundos):
        return None

    monkeypatch.setattr(pix, "asyncio", types.SimpleNamespace(sleep=sem_espera))
    copiado = _PaginaComBotaoDeCopiar(f"  {_PIX_VALIDO}\n", campos=["lixo"], corpo="nada")
    assert asyncio.run(pix.extract(copiado)) == _PIX_VALIDO
    assert copiado.botao.cliques == 1
    assert "clipboard-read" in copiado.permissoes
    negado = _PaginaComBotaoDeCopiar(PermissionError("negado"), campos=["", _PIX_VALIDO])
    assert asyncio.run(pix.extract(negado)) == _PIX_VALIDO
    assert negado.botao.cliques == 1
    assert asyncio.run(pix.from_copy_button(_PaginaPix(corpo=_PIX_VALIDO))) == ""
    campos_quebrados = _PaginaPix(corpo=f"Código: {_PIX_VALIDO}")

    async def campos_sumiram(_seletor, _script):
        raise RuntimeError("página recarregou")

    campos_quebrados.eval_on_selector_all = campos_sumiram
    assert asyncio.run(pix.extract(campos_quebrados)) == _PIX_VALIDO
