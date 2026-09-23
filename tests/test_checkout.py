from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("playwright.async_api", reason="requer playwright instalado")

from watcher import paths  # noqa: E402
from watcher.autobuy import amazon, kabum, terabyte  # noqa: E402
from watcher.autobuy.models import BuyResult, BuyStatus, ProductInfo  # noqa: E402

_PIX = (
    "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
    "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D"
)


class _Vazio:
    async def count(self):
        return 0

    async def is_visible(self):
        return False

    @property
    def first(self):
        return self


class _Elemento:
    def __init__(self, rotulo="", ao_clicar=None):
        self.rotulo = rotulo
        self.ao_clicar = ao_clicar
        self.cliques = 0

    async def click(self, timeout=None):
        self.cliques += 1
        if self.ao_clicar is not None:
            self.ao_clicar()

    async def check(self, timeout=None):
        await self.click(timeout)

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def count(self):
        return 1

    async def evaluate(self, _script):
        return self.rotulo

    async def bounding_box(self):
        return None

    @property
    def first(self):
        return self


class _Lista:
    def __init__(self, elementos):
        self.elementos = list(elementos)

    async def count(self):
        return len(self.elementos)

    def nth(self, indice):
        return self.elementos[indice]

    @property
    def first(self):
        return self.elementos[0] if self.elementos else _Vazio()


class _Pagina:
    def __init__(self, url, corpo="", seletores=None, botoes=None):
        self.url = url
        self.corpo = corpo
        self.seletores = dict(seletores or {})
        self.botoes = dict(botoes or {})
        self.fechada = False
        self.context = self

    def locator(self, seletor, has_text=None):
        return _Lista(self.seletores.get(seletor, []))

    def get_by_role(self, papel, name=None):
        if papel != "button":
            return _Lista([])
        return _Lista([botao for rotulo, botao in self.botoes.items() if name.search(rotulo)])

    def get_by_text(self, _padrao):
        return _Lista([])

    async def evaluate(self, _script):
        return self.corpo

    async def eval_on_selector_all(self, _seletor, _script):
        return []

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def grant_permissions(self, *_args, **_kwargs):
        return None

    async def bring_to_front(self):
        return None

    async def screenshot(self, **_kwargs):
        raise RuntimeError("sem tela")

    async def close(self):
        self.fechada = True


@pytest.fixture
def sem_espera(tmp_path, monkeypatch):
    import itertools
    import types

    from watcher.autobuy import pix

    async def rapido(_segundos=0, result=None):
        return result

    relogio = itertools.count(0, 50)
    monkeypatch.setattr(asyncio, "sleep", rapido)
    monkeypatch.setattr(pix, "time", types.SimpleNamespace(monotonic=lambda: next(relogio)))
    monkeypatch.setattr(paths, "autobuy_dir", lambda: tmp_path)
    return tmp_path


def _produto(loja="kabum"):
    return ProductInfo(id=1, title="SSD 2TB", url=f"https://{loja}.example/p", pix_price=289.9, card_price=320.0, available=True)


def _kabum_na_revisao(forma, confirm, depois_de_finalizar=""):
    pagina = _Pagina("https://www.kabum.com.br/pagamento", f"Revisão do pedido\nForma de pagamento\n{forma}\nTotal")

    def finalizou():
        pagina.corpo = depois_de_finalizar

    finalizar = _Elemento("Finalizar pedido", finalizou)
    pagina.botoes["Finalizar pedido"] = finalizar
    driver = kabum.KabumDriver(None)
    resultado = asyncio.run(driver._checkout(pagina, _produto(), confirm, kabum._Stopwatch()))
    return resultado, finalizar


def test_kabum_simulacao_para_antes_de_finalizar(sem_espera):
    resultado, finalizar = _kabum_na_revisao("PIX", confirm=False)
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert finalizar.cliques == 0
    assert "PIX confirmado" in resultado.detail


def test_kabum_simulacao_avisa_quando_o_pix_nao_esta_na_revisao(sem_espera):
    resultado, finalizar = _kabum_na_revisao("Cartão de crédito final 1234", confirm=False)
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert finalizar.cliques == 0
    assert "PIX não confirmado" in resultado.detail


def test_kabum_compra_real_nao_finaliza_se_o_pagamento_nao_e_pix(sem_espera):
    resultado, finalizar = _kabum_na_revisao("Cartão de crédito final 1234", confirm=True)
    assert resultado.status == BuyStatus.BLOCKED
    assert finalizar.cliques == 0


def test_kabum_compra_real_com_pix_finaliza_uma_vez_e_guarda_o_codigo(sem_espera):
    resultado, finalizar = _kabum_na_revisao("PIX", confirm=True, depois_de_finalizar=f"Pague com:\n{_PIX}")
    assert resultado.status == BuyStatus.PIX_READY
    assert resultado.pix_code == _PIX
    assert finalizar.cliques == 1
    from watcher.autobuy.records import load_records

    assert [r.pix_code for r in load_records(store="kabum")] == [_PIX]


def test_kabum_pedido_finalizado_sem_codigo_avisa_para_conferir(sem_espera):
    resultado, finalizar = _kabum_na_revisao("PIX", confirm=True, depois_de_finalizar="Processando")
    assert resultado.status == BuyStatus.FAILED
    assert finalizar.cliques == 1
    assert "confira" in resultado.detail


def test_kabum_sem_botao_para_avancar_desiste_sem_ficar_em_loop(sem_espera):
    pagina = _Pagina("https://www.kabum.com.br/endereco", "Escolha o endereço")
    resultado = asyncio.run(kabum.KabumDriver(None)._checkout(pagina, _produto(), True, kabum._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED
    assert "Nenhum botão de avanço" in resultado.detail


def test_kabum_caindo_no_login_pede_para_reconectar(sem_espera):
    pagina = _Pagina("https://www.kabum.com.br/login?redirect=/pagamento", "Entrar")
    resultado = asyncio.run(kabum.KabumDriver(None)._checkout(pagina, _produto(), True, kabum._Stopwatch()))
    assert resultado.status == BuyStatus.LOGIN_REQUIRED


class _ContextoKabum:
    def __init__(self, produto):
        self.pagina = _Pagina("https://www.kabum.com.br/produto/1/ssd", json.dumps({"props": {"pageProps": {"product": produto}}}))

    async def new_page(self):
        return self.pagina

    async def goto(self, *_args, **_kwargs):
        return None


def _compra_kabum(produto, **kwargs):
    contexto = _ContextoKabum(produto)
    contexto.pagina.goto = contexto.goto
    resultado = asyncio.run(kabum.KabumDriver(contexto).buy(contexto.pagina.url, **kwargs))
    return resultado, contexto.pagina


def test_kabum_sem_estoque_ou_abaixo_do_piso_nao_vai_ao_carrinho(sem_espera):
    base = {"id": 1, "title": "SSD", "prices": {"price": 300.0, "priceWithDiscount": 250.0}}
    resultado, pagina = _compra_kabum({**base, "available": False}, max_price=500.0, confirm=True)
    assert resultado.status == BuyStatus.UNAVAILABLE and pagina.fechada
    resultado, _pagina = _compra_kabum({**base, "available": True}, max_price=500.0, min_price=260.0, confirm=True)
    assert resultado.status == BuyStatus.PRICE_BELOW_FLOOR
    assert resultado.product.pix_price == 250.0


def test_kabum_sem_preco_pix_usa_o_preco_cheio_para_o_limite(sem_espera):
    produto = {"id": 1, "title": "SSD", "available": True, "prices": {"price": 600.0}}
    resultado, _pagina = _compra_kabum(produto, max_price=500.0, confirm=True)
    assert resultado.status == BuyStatus.PRICE_ABOVE_LIMIT
    assert resultado.product.pix_price == 600.0


def test_kabum_pagina_sem_dados_do_produto_e_nao_encontrado(sem_espera):
    contexto = _ContextoKabum({})
    contexto.pagina.corpo = "<html>pagina nova</html>"
    contexto.pagina.goto = contexto.goto
    resultado = asyncio.run(kabum.KabumDriver(contexto).buy(contexto.pagina.url, max_price=500.0))
    assert resultado.status == BuyStatus.NOT_FOUND
    sem_id = _ContextoKabum({"title": "sem id", "prices": {"price": 10.0}})
    sem_id.pagina.goto = sem_id.goto
    assert asyncio.run(kabum.KabumDriver(None).fetch_product(sem_id.pagina, sem_id.pagina.url)) is None
    sem_preco = _ContextoKabum({"id": 1, "title": "sem preço", "prices": {}})
    sem_preco.pagina.goto = sem_preco.goto
    assert asyncio.run(kabum.KabumDriver(None).fetch_product(sem_preco.pagina, sem_preco.pagina.url)) is None


def test_kabum_erro_inesperado_vira_falha_e_fecha_a_pagina(sem_espera):
    contexto = _ContextoKabum({"id": 1})

    async def quebra(*_args, **_kwargs):
        raise RuntimeError("navegador fechou")

    contexto.pagina.goto = quebra
    resultado = asyncio.run(kabum.KabumDriver(contexto).buy(contexto.pagina.url, max_price=500.0))
    assert resultado.status == BuyStatus.FAILED
    assert "navegador fechou" in resultado.detail
    assert contexto.pagina.fechada


@pytest.mark.parametrize(
    "caminho, etapa",
    [("/precarrinho/123", "pre-carrinho"), ("/carrinho?x=1", "carrinho"), ("", "pagina"), ("/pagamento/", "pagamento")],
)
def test_kabum_nome_da_etapa_pelo_endereco(caminho, etapa):
    assert kabum._step_name(caminho) == etapa


def test_nome_de_arquivo_do_print_sem_caractere_estranho():
    assert kabum._slug("SSD Kingston NV2 2TB / NVMe!") == "ssd-kingston-nv2-2tb-nvme"
    assert kabum._slug("***") == "produto"
    assert len(kabum._slug("x" * 100)) == 40


def _amazon_na_revisao(rotulo, confirm, corpo="Revisar pedido", radio_pix=False, depois_de_finalizar=""):
    pagina = _Pagina("https://www.amazon.com.br/checkout/p/p-123/spc?pipelineType=Chewbacca", corpo)

    def finalizou():
        pagina.corpo = depois_de_finalizar

    finalizar = _Elemento(rotulo, finalizou)
    pagina.seletores["#placeOrder"] = [finalizar]
    if radio_pix:
        pagina.seletores[amazon._PIX_RADIO] = [_Elemento("pix")]
    driver = amazon.AmazonDriver(None)
    resultado = asyncio.run(driver._checkout(pagina, _produto("amazon"), confirm, amazon._Stopwatch()))
    return resultado, finalizar


def test_amazon_simulacao_com_pix_para_na_revisao(sem_espera):
    resultado, finalizar = _amazon_na_revisao("Confirmar pedido com Pix", confirm=False)
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert finalizar.cliques == 0


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("radio_pix", [False, True])
def test_amazon_nunca_finaliza_com_cartao(sem_espera, confirm, radio_pix):
    resultado, finalizar = _amazon_na_revisao("Confirmar pedido com Mastercard 4745", confirm=confirm, radio_pix=radio_pix)
    assert resultado.status == BuyStatus.BLOCKED
    assert finalizar.cliques == 0
    assert "Mastercard" in resultado.detail


def test_amazon_botao_neutro_confere_o_pagando_com_da_tela(sem_espera):
    resultado, finalizar = _amazon_na_revisao(
        "Confirmar pedido", confirm=True, corpo="Pagando com Cartão Visa final 1234", radio_pix=True
    )
    assert resultado.status == BuyStatus.BLOCKED
    assert finalizar.cliques == 0


def test_amazon_botao_neutro_com_pix_escolhido_pelo_robo_finaliza(sem_espera):
    resultado, finalizar = _amazon_na_revisao(
        "Confirmar pedido", confirm=True, radio_pix=True, depois_de_finalizar=f"Pedido feito\n{_PIX}"
    )
    assert resultado.status == BuyStatus.PIX_READY
    assert finalizar.cliques == 1
    assert resultado.pix_code == _PIX


def test_amazon_botao_neutro_sem_pix_escolhido_nao_finaliza(sem_espera):
    resultado, finalizar = _amazon_na_revisao("Confirmar pedido", confirm=True)
    assert resultado.status == BuyStatus.BLOCKED
    assert finalizar.cliques == 0


def test_amazon_tela_de_login_pede_para_reconectar(sem_espera):
    pagina = _Pagina("https://www.amazon.com.br/ap/signin?openid=x", "Fazer login")
    resultado = asyncio.run(amazon.AmazonDriver(None)._checkout(pagina, _produto("amazon"), True, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.LOGIN_REQUIRED


def test_amazon_captcha_com_navegador_oculto_bloqueia_na_hora(sem_espera):
    pagina = _Pagina("https://www.amazon.com.br/checkout/p/p-1/spc", "Digite os caracteres que você vê abaixo")
    avisos: list[str] = []
    driver = amazon.AmazonDriver(None, on_captcha=lambda: avisos.append("x"), headless=True)
    resultado = asyncio.run(driver._checkout(pagina, _produto("amazon"), True, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED
    assert avisos == []


class _PaginaProduto(_Pagina):
    def __init__(self, url, dados):
        super().__init__(url)
        self.dados = dados

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_selector(self, *_args, **_kwargs):
        return None

    async def evaluate(self, _script):
        return self.dados


_ASIN = "https://www.amazon.com.br/dp/B0CGBQVDNK"


def test_amazon_le_preco_com_desconto_pix_e_estoque(sem_espera):
    dados = {
        "asin": "",
        "titulo": "SSD Kingston",
        "preco": "R$ 1.000,00",
        "promo": "10% off à vista no Pix",
        "caixa": "",
        "estoque": "Em estoque",
        "vendedor": "Amazon.com.br",
        "tem_compra": True,
        "tem_carrinho": False,
    }
    produto = asyncio.run(amazon.AmazonDriver(None).fetch_product(_PaginaProduto(_ASIN, dados), _ASIN))
    assert (produto.card_price, produto.pix_price, produto.available) == (1000.0, 900.0, True)
    indisponivel = {**dados, "estoque": "Não disponível. Fora de estoque."}
    produto = asyncio.run(amazon.AmazonDriver(None).fetch_product(_PaginaProduto(_ASIN, indisponivel), _ASIN))
    assert produto.available is False
    for faltando in ({**dados, "titulo": ""}, {**dados, "preco": ""}):
        assert asyncio.run(amazon.AmazonDriver(None).fetch_product(_PaginaProduto(_ASIN, faltando), _ASIN)) is None


def test_amazon_quantidade_limitada_pela_loja(sem_espera):
    escolhidos: list[str] = []

    class Seletor:
        async def count(self):
            return 1

        async def evaluate(self, _script):
            return ["1", "2", "3"]

        async def select_option(self, valor):
            escolhidos.append(valor)

        @property
        def first(self):
            return self

    pagina = _Pagina(_ASIN)
    pagina.locator = lambda _seletor, has_text=None: Seletor()
    driver = amazon.AmazonDriver(None)
    driver._quantity = 5
    asyncio.run(driver._set_quantity(pagina))
    driver._quantity = 2
    asyncio.run(driver._set_quantity(pagina))
    assert escolhidos == ["3", "2"]


def _terabyte_no_pagamento(confirm, pix_disponivel=True, depois_de_fechar=None, corpo="Formas de pagamento"):
    pagina = _Pagina("https://www.terabyteshop.com.br/FormasPagamento.obj", corpo)

    def marcou_pix():
        pagina.seletores["#ppix:checked"] = [_Elemento("pix")]

    if pix_disponivel:
        pagina.seletores["label[for='ppix']"] = [_Elemento("PIX", marcou_pix)]

    def fechou():
        if depois_de_fechar is not None:
            depois_de_fechar(pagina)

    fechar = _Elemento("Fechar pedido", fechou)
    pagina.seletores["#cart_payment"] = [fechar]
    driver = terabyte.TerabyteDriver(None)
    resultado = asyncio.run(driver._checkout(pagina, _produto("terabyte"), confirm, terabyte._Stopwatch()))
    return resultado, fechar


def test_terabyte_simulacao_seleciona_pix_e_nao_fecha_o_pedido(sem_espera):
    resultado, fechar = _terabyte_no_pagamento(confirm=False)
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert fechar.cliques == 0


@pytest.mark.parametrize("confirm", [False, True])
def test_terabyte_sem_pix_selecionado_nao_fecha_o_pedido(sem_espera, confirm):
    resultado, fechar = _terabyte_no_pagamento(confirm=confirm, pix_disponivel=False)
    assert resultado.status == BuyStatus.BLOCKED
    assert fechar.cliques == 0
    assert "PIX" in resultado.detail


def test_terabyte_compra_real_fecha_e_traz_o_codigo(sem_espera):
    def gerou(pagina):
        pagina.corpo = f"Pedido recebido\n{_PIX}"

    resultado, fechar = _terabyte_no_pagamento(confirm=True, depois_de_fechar=gerou)
    assert resultado.status == BuyStatus.PIX_READY
    assert fechar.cliques == 1
    assert resultado.pix_code == _PIX


def test_terabyte_sessao_que_cai_ao_fechar_avisa_que_nao_houve_pedido(sem_espera):
    def caiu(pagina):
        pagina.url = "https://www.terabyteshop.com.br/Central.obj"

    resultado, fechar = _terabyte_no_pagamento(confirm=True, depois_de_fechar=caiu)
    assert resultado.status == BuyStatus.LOGIN_REQUIRED
    assert fechar.cliques == 1
    assert "NENHUM pedido" in resultado.detail


def test_terabyte_verificacao_de_navegador_oculta_bloqueia(sem_espera, monkeypatch):
    import itertools
    import time
    import types

    from watcher.autobuy.models import HIDDEN_CAPTCHA

    relogio = itertools.count(0, 50)
    monkeypatch.setattr(terabyte, "time", types.SimpleNamespace(monotonic=lambda: next(relogio), strftime=time.strftime))
    pagina = _Pagina("https://www.terabyteshop.com.br/FormasPagamento.obj", "Checking your browser before accessing")
    avisos: list[str] = []
    driver = terabyte.TerabyteDriver(None, on_captcha=lambda: avisos.append("captcha"), headless=True)
    resultado = asyncio.run(driver._checkout(pagina, _produto("terabyte"), True, terabyte._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED
    assert resultado.detail == HIDDEN_CAPTCHA
    assert avisos == []


_TERABYTE = "https://www.terabyteshop.com.br/produto/25639/ssd"


def _dados_terabyte(**mudancas):
    base = {
        "nome_ld": "SSD Adata Legend 710 256GB | Terabyte",
        "preco_ld": "199.90",
        "disp_ld": "https://schema.org/InStock",
        "titulo": "Outro título",
        "vista": "R$ 189,90",
        "riscado": "R$ 249,90",
        "corpo": "Comprar\nFrete grátis",
        "tem_comprar": True,
    }
    base.update(mudancas)
    return base


def test_terabyte_le_preco_a_vista_titulo_e_codigo(sem_espera):
    produto = asyncio.run(terabyte.TerabyteDriver(None).fetch_product(_PaginaProduto(_TERABYTE, _dados_terabyte()), _TERABYTE))
    assert produto.title == "SSD Adata Legend 710 256GB"
    assert (produto.pix_price, produto.card_price, produto.available, produto.id) == (189.9, 249.9, True, 25639)
    sem_vista = _dados_terabyte(vista="", riscado="")
    produto = asyncio.run(terabyte.TerabyteDriver(None).fetch_product(_PaginaProduto(_TERABYTE, sem_vista), _TERABYTE))
    assert (produto.pix_price, produto.card_price) == (199.9, 199.9)
    riscado_menor = _dados_terabyte(riscado="R$ 99,90")
    produto = asyncio.run(terabyte.TerabyteDriver(None).fetch_product(_PaginaProduto(_TERABYTE, riscado_menor), _TERABYTE))
    assert (produto.pix_price, produto.card_price) == (189.9, 189.9)


@pytest.mark.parametrize(
    "mudancas",
    [
        {"disp_ld": "https://schema.org/OutOfStock"},
        {"corpo": "Produto indisponível\nAvise-me quando chegar"},
        {"tem_comprar": False, "disp_ld": ""},
    ],
)
def test_terabyte_sem_estoque_detectado(sem_espera, mudancas):
    pagina = _PaginaProduto(_TERABYTE, _dados_terabyte(**mudancas))
    assert asyncio.run(terabyte.TerabyteDriver(None).fetch_product(pagina, _TERABYTE)).available is False


def test_terabyte_sem_titulo_ou_preco_nao_e_produto(sem_espera):
    for mudancas in ({"nome_ld": "", "titulo": ""}, {"vista": "", "preco_ld": ""}):
        pagina = _PaginaProduto(_TERABYTE, _dados_terabyte(**mudancas))
        assert asyncio.run(terabyte.TerabyteDriver(None).fetch_product(pagina, _TERABYTE)) is None


def test_terabyte_avisa_que_compra_so_uma_unidade(sem_espera):
    class Contexto:
        async def new_page(self):
            return _Pagina(_TERABYTE)

    for status, avisa in ((BuyStatus.DRY_RUN_OK, True), (BuyStatus.PRICE_ABOVE_LIMIT, False)):
        driver = terabyte.TerabyteDriver(Contexto())

        async def rodou(*_args, status=status):
            return BuyResult(status=status, detail="ok")

        driver._run = rodou
        resultado = asyncio.run(driver.buy(_TERABYTE, 500.0, quantity=3))
        assert ("quantidade não suportada" in resultado.detail) is avisa
        assert resultado.detail.startswith("ok")


class _Avancou(Exception):
    pass


def _pagina_que_nao_pode_avancar(url):
    pagina = _Pagina(url)

    async def avancou(*_args, **_kwargs):
        raise _Avancou()

    def localizou(*_args, **_kwargs):
        raise _Avancou()

    pagina.click = avancou
    pagina.goto = avancou
    pagina.locator = localizou
    return pagina


@pytest.mark.parametrize(
    "modulo, classe",
    [(kabum, "KabumDriver"), (amazon, "AmazonDriver"), (terabyte, "TerabyteDriver")],
)
def test_preco_fora_da_faixa_ou_sem_estoque_nunca_segue_para_a_compra(sem_espera, modulo, classe):
    produto = ProductInfo(id=1, title="SSD", url="https://loja.example/p", pix_price=300.0, card_price=320.0, available=True)
    driver = getattr(modulo, classe)(None)

    async def le(_page, _url):
        return produto

    driver.fetch_product = le

    def rodar(maximo, minimo=0.0):
        pagina = _pagina_que_nao_pode_avancar(produto.url)
        return asyncio.run(driver._run(pagina, produto.url, maximo, minimo, True, modulo._Stopwatch()))

    acima = rodar(299.99)
    assert acima.status == BuyStatus.PRICE_ABOVE_LIMIT and "R$ 300.00 acima do limite R$ 299.99" == acima.detail
    assert rodar(500.0, 300.01).status == BuyStatus.PRICE_BELOW_FLOOR
    for maximo, minimo in ((300.0, 300.0), (0.0, 0.0)):
        with pytest.raises(_Avancou):
            rodar(maximo, minimo)
    produto.available = False
    assert rodar(0.0).status == BuyStatus.UNAVAILABLE


def test_kabum_tempo_esgotado_vira_falha_com_motivo_e_fecha_a_pagina(sem_espera):
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    contexto = _ContextoKabum({"id": 1})

    async def lento(*_args, **_kwargs):
        raise PlaywrightTimeout("Timeout 45000ms exceeded")

    contexto.pagina.goto = lento
    resultado = asyncio.run(kabum.KabumDriver(contexto).buy(contexto.pagina.url, max_price=500.0))
    assert resultado.status == BuyStatus.FAILED
    assert resultado.detail == "Tempo esgotado: Timeout 45000ms exceeded"
    assert contexto.pagina.fechada


class _BotaoRemover:
    def __init__(self, carrinho, produto_id, visivel=True):
        self.carrinho = carrinho
        self.produto_id = produto_id
        self.visivel = visivel

    async def get_attribute(self, nome):
        return f"remove-button-{self.produto_id}" if nome == "id" else None

    async def is_visible(self):
        return self.visivel

    async def click(self, timeout=None):
        self.carrinho.remove(self)


_CARRINHO = "https://www.kabum.com.br/carrinho"


def test_kabum_limpa_o_carrinho_sem_tirar_o_produto_da_promo_e_so_uma_vez(sem_espera):
    carrinho: list[_BotaoRemover] = []
    carrinho.extend([
        _BotaoRemover(carrinho, 7),
        _BotaoRemover(carrinho, 1),
        _BotaoRemover(carrinho, 9),
        _BotaoRemover(carrinho, 3, visivel=False),
    ])
    pagina = _Pagina(_CARRINHO, seletores={kabum._REMOVE_BUTTON: carrinho})
    logs: list[str] = []
    driver = kabum.KabumDriver(None, on_log=logs.append)
    asyncio.run(driver._clear_other_items(pagina, 1))
    assert [botao.produto_id for botao in carrinho] == [1, 3]
    assert logs == ["Carrinho limpo: 2 item(ns) que não eram da promoção foram removidos"]
    carrinho.append(_BotaoRemover(carrinho, 8))
    asyncio.run(driver._clear_other_items(pagina, 1))
    assert [botao.produto_id for botao in carrinho] == [1, 3, 8]
    so_a_promo = kabum.KabumDriver(None, on_log=logs.append)
    asyncio.run(so_a_promo._clear_other_items(_Pagina(_CARRINHO, seletores={kabum._REMOVE_BUTTON: carrinho[:1]}), 1))
    assert len(logs) == 1


class _Campo:
    def __init__(self, valor=""):
        self.valor = valor
        self.preenchido: list[str] = []

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def input_value(self):
        return self.valor

    async def fill(self, texto):
        self.preenchido.append(texto)

    @property
    def first(self):
        return self


def test_kabum_ajusta_a_quantidade_uma_vez_so(sem_espera):
    quantidade = _Campo("1")

    def mais_um():
        quantidade.valor = str(int(quantidade.valor) + 1)

    mais = _Elemento("+", mais_um)
    pagina = _Pagina(_CARRINHO, seletores={kabum._QUANTITY_PLUS: [mais], kabum._QUANTITY_INPUT: [quantidade]})
    logs: list[str] = []
    driver = kabum.KabumDriver(None, on_log=logs.append)
    driver._quantity = 3
    asyncio.run(driver._set_quantity(pagina))
    asyncio.run(driver._set_quantity(pagina))
    assert mais.cliques == 2
    assert logs == ["Quantidade ajustada para 3"]
    uma = kabum.KabumDriver(None, on_log=logs.append)
    asyncio.run(uma._set_quantity(pagina))
    assert mais.cliques == 2
    sem_botao = kabum.KabumDriver(None, on_log=logs.append)
    sem_botao._quantity = 2
    asyncio.run(sem_botao._set_quantity(_Pagina(_CARRINHO)))
    assert logs[-1] == "Botão de quantidade não encontrado, seguindo com 1 unidade (pedido: 2)"


_CAMPO_CUPOM_KABUM = "[data-testid='input-coupon'], #inputAplicarCupom"
_BOTAO_CUPOM_KABUM = "[data-testid='button-apply-coupon']"


def _cupom_kabum(corpo, url="https://www.kabum.com.br/endereco", campo_existe=True, clique_quebra=False):
    campo = _Campo()

    def quebra():
        raise RuntimeError("botão sumiu")

    aplicar = _Elemento("Aplicar", quebra if clique_quebra else None)
    seletores = {_CAMPO_CUPOM_KABUM: [campo] if campo_existe else [], _BOTAO_CUPOM_KABUM: [aplicar]}
    driver = kabum.KabumDriver(None)
    driver._coupon = "MELI50"
    pagina = _Pagina(url, corpo, seletores)
    asyncio.run(driver._apply_coupon(pagina))
    asyncio.run(driver._apply_coupon(pagina))
    return driver._coupon_note, campo.preenchido, aplicar.cliques


def test_kabum_cupom_so_na_tela_de_endereco_uma_vez_e_resultado_pela_pagina(sem_espera):
    assert _cupom_kabum("Cupom aplicado com sucesso") == ("Cupom MELI50 aplicado", ["MELI50"], 1)
    assert _cupom_kabum("Cupom inválido ou expirado") == ("Cupom MELI50 recusado pela loja", ["MELI50"], 1)
    assert _cupom_kabum("x", url=_CARRINHO) == ("", [], 0)
    assert _cupom_kabum("x", campo_existe=False) == ("", [], 0)
    assert _cupom_kabum("x", clique_quebra=True) == ("Cupom MELI50: falha ao aplicar", ["MELI50"], 1)


class _Quadro(_Elemento):
    def __init__(self, largura, altura):
        super().__init__("iframe")
        self.caixa = {"width": largura, "height": altura}

    async def bounding_box(self):
        return self.caixa


_DESAFIO = "iframe[src*='recaptcha/api2/bframe']"


def test_kabum_captcha_so_conta_quando_o_desafio_esta_aberto_na_tela(sem_espera):
    driver = kabum.KabumDriver(None)
    assert asyncio.run(driver._captcha_present(_Pagina(_CARRINHO, seletores={_DESAFIO: [_Quadro(400, 580)]}))) is True
    assert asyncio.run(driver._captcha_present(_Pagina(_CARRINHO, seletores={_DESAFIO: [_Quadro(70, 70)]}))) is False
    assert asyncio.run(driver._captcha_present(_Pagina(_CARRINHO))) is False


def test_kabum_captcha_com_janela_espera_a_pessoa_resolver_ate_o_prazo(sem_espera, monkeypatch):
    import itertools
    import time
    import types

    relogio = itertools.count(0, 10)
    monkeypatch.setattr(kabum, "time", types.SimpleNamespace(monotonic=lambda: next(relogio), strftime=time.strftime))
    avisos: list[int] = []

    def aviso_quebrado():
        avisos.append(1)
        raise RuntimeError("bandeja fora")

    pagina = _Pagina(_CARRINHO, seletores={_DESAFIO: [_Quadro(400, 580)]})
    driver = kabum.KabumDriver(None, on_captcha=aviso_quebrado)
    verificacoes = [0]
    original = driver._captcha_present

    async def presente(alvo):
        verificacoes[0] += 1
        if verificacoes[0] == 3:
            alvo.seletores.clear()
        return await original(alvo)

    driver._captcha_present = presente
    assert asyncio.run(driver._handle_captcha(pagina)) is True
    assert avisos == [1] and verificacoes[0] == 3
    preso = _Pagina(_CARRINHO, seletores={_DESAFIO: [_Quadro(400, 580)]})
    logs: list[str] = []
    assert asyncio.run(kabum.KabumDriver(None, on_log=logs.append)._handle_captcha(preso)) is False
    assert logs[-1] == "Captcha não foi resolvido a tempo"


def test_kabum_avanca_pelo_botao_certo_e_nunca_por_continuar_comprando(sem_espera):
    driver = kabum.KabumDriver(None)
    marcado = _Elemento("Continuar")
    pagina = _Pagina(_CARRINHO, seletores={"[data-testid='button-continue']": [marcado]})
    assert asyncio.run(driver._click_continue(pagina)) is True
    assert marcado.cliques == 1
    voltar_a_loja = _Elemento("Continuar comprando")
    pagamento = _Elemento("Ir para o pagamento")
    pagina = _Pagina(_CARRINHO, botoes={"Continuar comprando": voltar_a_loja, "Ir para o pagamento": pagamento})
    assert asyncio.run(driver._click_continue(pagina)) is True
    assert (voltar_a_loja.cliques, pagamento.cliques) == (0, 1)
    so_voltar = _Pagina(_CARRINHO, botoes={"Continuar comprando": voltar_a_loja})
    assert asyncio.run(driver._click_continue(so_voltar)) is False
    assert voltar_a_loja.cliques == 0


def _pre_carrinho(botoes):
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    pagina = _Pagina("https://www.kabum.com.br/precarrinho/1")

    async def espera_url(_padrao, timeout=None):
        if "/carrinho" not in pagina.url.replace("precarrinho", ""):
            raise PlaywrightTimeout("sem navegação")

    def foi_ao_carrinho():
        pagina.url = _CARRINHO

    pagina.wait_for_url = espera_url
    for rotulo in botoes:
        pagina.botoes[rotulo] = _Elemento(rotulo, foi_ao_carrinho)
    return pagina


def test_kabum_sai_do_pre_carrinho_recusando_servicos_extras(sem_espera):
    logs: list[str] = []
    pagina = _pre_carrinho(["Agora não"])
    assert asyncio.run(kabum.KabumDriver(None, on_log=logs.append)._leave_precart(pagina)) is True
    assert pagina.botoes["Agora não"].cliques == 1
    assert logs == ["Recusando serviços extras do pré-carrinho"]
    pagina = _pre_carrinho(["Agora não", "Ir para o carrinho"])
    assert asyncio.run(kabum.KabumDriver(None)._leave_precart(pagina)) is True
    assert (pagina.botoes["Ir para o carrinho"].cliques, pagina.botoes["Agora não"].cliques) == (1, 0)
    assert asyncio.run(kabum.KabumDriver(None)._leave_precart(_pre_carrinho([]))) is False


_PAGAMENTO_AMAZON = "https://www.amazon.com.br/checkout/p/p-1/pay"


def _cupom_amazon(corpo, com_link=True, com_aplicar=True):
    campo = _Campo()
    aplicar = _Elemento("Aplicar")
    pagina = _Pagina(_PAGAMENTO_AMAZON, corpo)
    if com_aplicar:
        pagina.seletores[amazon._COUPON_APPLY] = [aplicar]

    def abriu():
        pagina.seletores[amazon._COUPON_FIELD] = [campo]

    if com_link:
        pagina.botoes["Usar um cupom de desconto"] = _Elemento("link", abriu)
    driver = amazon.AmazonDriver(None)
    driver._coupon = "PRIME10"
    primeira = asyncio.run(driver._apply_coupon(pagina))
    segunda = asyncio.run(driver._apply_coupon(pagina))
    return primeira, segunda, driver._coupon_note, campo.preenchido, aplicar.cliques


def test_amazon_cupom_abre_o_campo_aplica_uma_vez_e_le_a_resposta(sem_espera):
    assert _cupom_amazon("Desconto aplicado") == (True, False, "Cupom PRIME10 aplicado", ["PRIME10"], 1)
    assert _cupom_amazon("O cupom de desconto inserido é inválido")[2] == "Cupom PRIME10 recusado pela loja"
    assert _cupom_amazon("x", com_aplicar=False) == (
        False, False, "Cupom PRIME10: botão de aplicar não encontrado", ["PRIME10"], 0
    )
    assert _cupom_amazon("x", com_link=False) == (False, False, "", [], 0)


def test_terabyte_esvazia_o_carrinho_antes_de_comprar(sem_espera):
    visitas: list[str] = []
    logs: list[str] = []

    def pagina_do_carrinho():
        pagina = _Pagina("https://www.terabyteshop.com.br/")

        async def vai(url, **_kwargs):
            visitas.append(url)

        pagina.goto = vai
        return pagina

    pagina = pagina_do_carrinho()
    limpar = _Elemento("Limpar carrinho")
    pagina.seletores[terabyte._CLEAR_CART] = [limpar]
    asyncio.run(terabyte.TerabyteDriver(None, on_log=logs.append)._clear_cart(pagina))
    assert limpar.cliques == 1 and visitas == [terabyte._CART_URL]
    assert logs == ["Carrinho da Terabyte esvaziado antes da compra"]
    pagina = pagina_do_carrinho()
    itens: list[_Elemento] = []
    for _ in range(3):
        item = _Elemento("remover")
        item.ao_clicar = lambda item=item: itens.remove(item)
        itens.append(item)
    pagina.seletores[terabyte._REMOVE_ITEM] = itens
    asyncio.run(terabyte.TerabyteDriver(None, on_log=logs.append)._clear_cart(pagina))
    assert itens == []
    assert logs[-1] == "Carrinho da Terabyte limpo: 3 item(ns) removidos"
    fora_do_ar = _pagina_que_nao_pode_avancar("https://www.terabyteshop.com.br/")
    fora_do_ar.locator = lambda *_args, **_kwargs: pytest.fail("não deveria procurar botão sem abrir o carrinho")
    asyncio.run(terabyte.TerabyteDriver(None)._clear_cart(fora_do_ar))


_ENTREGA_TERABYTE = "https://www.terabyteshop.com.br/Entrega.obj"
_PAGAMENTO_TERABYTE = "https://www.terabyteshop.com.br/FormasPagamento.obj"


def _cupom_terabyte(corpo, campo_visivel=True, gatilho_abre=True, clique_quebra=False):
    campo = _Campo()
    pagina = _Pagina(_PAGAMENTO_TERABYTE, corpo)

    def abriu():
        if gatilho_abre:
            pagina.seletores[terabyte._COUPON_FIELD] = [campo]

    def quebra():
        raise RuntimeError("botão sumiu")

    gatilho = _Elemento("Cupom", abriu)
    aplicar = _Elemento("Aplicar", quebra if clique_quebra else None)
    pagina.seletores[terabyte._COUPON_TRIGGER] = [gatilho]
    pagina.seletores[terabyte._COUPON_APPLY] = [aplicar]
    if campo_visivel:
        pagina.seletores[terabyte._COUPON_FIELD] = [campo]
    driver = terabyte.TerabyteDriver(None)
    driver._coupon = "TERA10"
    asyncio.run(driver._apply_coupon(pagina))
    asyncio.run(driver._apply_coupon(pagina))
    return driver._coupon_note, campo.preenchido, gatilho.cliques, aplicar.cliques


def test_terabyte_cupom_abre_o_campo_aplica_uma_vez_e_le_a_resposta(sem_espera):
    assert _cupom_terabyte("Cupom aplicado") == ("Cupom TERA10 aplicado", ["TERA10"], 0, 1)
    assert _cupom_terabyte("Cupom não é válido") == ("Cupom TERA10 recusado pela loja", ["TERA10"], 0, 1)
    assert _cupom_terabyte("ok", campo_visivel=False) == ("Cupom TERA10 aplicado", ["TERA10"], 1, 1)
    assert _cupom_terabyte("ok", campo_visivel=False, gatilho_abre=False) == ("", [], 2, 0)
    nao_clicou = ("Cupom TERA10: botão de aplicar não encontrado", ["TERA10"], 0, 1)
    assert _cupom_terabyte("Cupom aplicado", clique_quebra=True) == nao_clicou
    pagina = _Pagina(_PAGAMENTO_TERABYTE)
    pagina.locator = lambda *_args, **_kwargs: pytest.fail("sem cupom não procura campo")
    sem_cupom = terabyte.TerabyteDriver(None)
    asyncio.run(sem_cupom._apply_coupon(pagina))
    assert sem_cupom._coupon_note == ""


def _entrega_terabyte(cep="01001-000", frete_ao_calcular=True, frete_ao_escolher=True, url=_ENTREGA_TERABYTE):
    pagina = _Pagina(url)
    visitas: list[str] = []

    async def vai(destino, **_kwargs):
        visitas.append(destino)

    pagina.goto = vai

    def marca_frete():
        pagina.seletores[f"{terabyte._SHIPPING_RADIO}:checked"] = [_Elemento("frete")]

    calcular = _Elemento("Calcular", marca_frete if frete_ao_calcular else None)
    escolher = _Elemento("Sedex", marca_frete if frete_ao_escolher else None)
    pagina.seletores[terabyte._CALC_SHIPPING[0]] = [calcular]
    pagina.seletores[f"{terabyte._SHIPPING_ITEM} label"] = [escolher]
    if cep is not None:
        pagina.seletores[terabyte._CEP_FIELD] = [_Campo(cep)]
    logs: list[str] = []
    pronto = asyncio.run(terabyte.TerabyteDriver(None, on_log=logs.append)._prepare_shipping(pagina))
    return pronto, calcular.cliques, escolher.cliques, visitas, logs


def test_terabyte_entrega_calcula_o_frete_e_so_escolhe_se_precisar(sem_espera):
    assert _entrega_terabyte() == (True, 1, 0, [], ["Frete calculado e selecionado"])
    assert _entrega_terabyte(frete_ao_calcular=False)[:3] == (True, 1, 1)
    assert _entrega_terabyte(frete_ao_calcular=False, frete_ao_escolher=False) == (False, 1, 1, [], [])
    assert _entrega_terabyte(cep=None)[0] is True
    pronto, _calcular, _escolher, visitas, _logs = _entrega_terabyte(url="https://www.terabyteshop.com.br/")
    assert pronto is True and visitas == [terabyte._CART_URL]


def test_terabyte_entrega_com_cep_vazio_para_e_pede_endereco(sem_espera):
    pronto, calcular, escolher, _visitas, logs = _entrega_terabyte(cep="  ")
    assert (pronto, calcular, escolher) == (False, 0, 0)
    assert logs == ["CEP vazio na tela de entrega: cadastre um endereço padrão na conta"]


def test_terabyte_entrega_fora_do_ar_nao_segue(sem_espera):
    pagina = _pagina_que_nao_pode_avancar("https://www.terabyteshop.com.br/")

    async def quebra(*_args, **_kwargs):
        raise RuntimeError("rede caiu")

    pagina.goto = quebra
    assert asyncio.run(terabyte.TerabyteDriver(None)._prepare_shipping(pagina)) is False


def test_terabyte_fora_da_tela_de_pagamento_desiste_depois_do_limite_de_passos(sem_espera):
    from watcher.autobuy.models import CHECKOUT_STUCK

    pagina = _Pagina(_ENTREGA_TERABYTE, "Carregando")
    passos = [0]

    async def carregou(*_args, **_kwargs):
        passos[0] += 1

    pagina.wait_for_load_state = carregou
    resultado = asyncio.run(terabyte.TerabyteDriver(None)._checkout(pagina, _produto("terabyte"), True, terabyte._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == CHECKOUT_STUCK
    assert passos[0] == terabyte._MAX_STEPS


def test_terabyte_codigo_ja_na_tela_nao_fecha_o_pedido_de_novo(sem_espera):
    resultado, fechar = _terabyte_no_pagamento(confirm=True, corpo=f"Pedido 123\n{_PIX}")
    assert resultado.status == BuyStatus.PIX_READY and resultado.pix_code == _PIX
    assert fechar.cliques == 0
    gravado = list(sem_espera.glob("*-pix-ssd-2tb.txt"))
    assert len(gravado) == 1 and gravado[0].read_text(encoding="utf-8").endswith(f"{_PIX}\n")


def test_terabyte_pedido_fechado_sem_codigo_avisa_para_conferir(sem_espera):
    resultado, fechar = _terabyte_no_pagamento(confirm=True)
    assert resultado.status == BuyStatus.FAILED and "confira" in resultado.detail
    assert fechar.cliques == 1


def test_terabyte_sessao_que_cai_esperando_o_codigo_avisa_que_nada_foi_confirmado(sem_espera, monkeypatch):
    from watcher.autobuy import pix

    async def caiu_esperando(pagina, **_kwargs):
        pagina.url = "https://www.terabyteshop.com.br/Identificacao.obj"
        return ""

    monkeypatch.setattr(pix, "wait_for", caiu_esperando)
    resultado, fechar = _terabyte_no_pagamento(confirm=True)
    assert resultado.status == BuyStatus.LOGIN_REQUIRED and fechar.cliques == 1
    assert resultado.detail == "Sessão caiu durante a geração do PIX. Nenhum pedido foi confirmado"


def test_terabyte_sem_botao_de_fechar_pedido_bloqueia(sem_espera):
    pagina = _Pagina(_PAGAMENTO_TERABYTE, "Formas de pagamento")
    pagina.seletores["#ppix:checked"] = [_Elemento("pix")]
    resultado = asyncio.run(terabyte.TerabyteDriver(None)._checkout(pagina, _produto("terabyte"), True, terabyte._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == "Botão Fechar Pedido não encontrado"


def test_terabyte_simulacao_leva_o_resultado_do_cupom_no_detalhe(sem_espera):
    pagina = _Pagina(_PAGAMENTO_TERABYTE, "Cupom aplicado")
    pagina.seletores["#ppix:checked"] = [_Elemento("pix")]
    pagina.seletores[terabyte._COUPON_FIELD] = [_Campo()]
    pagina.seletores[terabyte._COUPON_APPLY] = [_Elemento("Aplicar")]
    driver = terabyte.TerabyteDriver(None)
    driver._coupon = "TERA10"
    resultado = asyncio.run(driver._checkout(pagina, _produto("terabyte"), False, terabyte._Stopwatch()))
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert resultado.detail.endswith("; Cupom TERA10 aplicado")


def _run_terabyte(tem_botao=True, entrega=True, depois_da_entrega="", tem_continuar=True, depois_de_continuar=""):
    produto = ProductInfo(id=1, title="SSD", url=_TERABYTE, pix_price=300.0, card_price=320.0, available=True)
    pagina = _Pagina(_TERABYTE)
    chamadas: list[str] = []

    async def vai(*_args, **_kwargs):
        return None

    pagina.goto = vai
    if tem_botao:
        pagina.seletores[".buy-button"] = [_Elemento("Comprar")]

    def continuou():
        if depois_de_continuar:
            pagina.url = depois_de_continuar

    if tem_continuar:
        pagina.seletores[terabyte._CONTINUE] = [_Elemento("Continuar", continuou)]
    driver = terabyte.TerabyteDriver(None)

    async def le(_page, _url):
        return produto

    async def limpa(_page):
        chamadas.append("limpa")

    async def prepara(_page):
        chamadas.append("entrega")
        if depois_da_entrega:
            pagina.url = depois_da_entrega
        return entrega

    async def fecha(*_args):
        chamadas.append("checkout")
        return BuyResult(status=BuyStatus.DRY_RUN_OK, product=produto)

    driver.fetch_product = le
    driver._clear_cart = limpa
    driver._prepare_shipping = prepara
    driver._checkout = fecha
    resultado = asyncio.run(driver._run(pagina, _TERABYTE, 500.0, 0.0, True, terabyte._Stopwatch()))
    return resultado, chamadas


def test_terabyte_fluxo_ate_o_pagamento_para_em_cada_tropeco(sem_espera):
    resultado, chamadas = _run_terabyte()
    assert resultado.status == BuyStatus.DRY_RUN_OK and chamadas == ["limpa", "entrega", "checkout"]
    resultado, chamadas = _run_terabyte(tem_botao=False)
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == "Botão de comprar não encontrado"
    assert chamadas == ["limpa"]
    resultado, chamadas = _run_terabyte(entrega=False)
    assert resultado.status == BuyStatus.BLOCKED and "frete" in resultado.detail and "checkout" not in chamadas
    resultado, _chamadas = _run_terabyte(entrega=False, depois_da_entrega="https://www.terabyteshop.com.br/Central.obj")
    assert resultado.status == BuyStatus.LOGIN_REQUIRED
    resultado, chamadas = _run_terabyte(tem_continuar=False)
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == "Botão Continuar não encontrado"
    assert "checkout" not in chamadas
    resultado, chamadas = _run_terabyte(depois_de_continuar="https://www.terabyteshop.com.br/Identificacao.obj")
    assert resultado.status == BuyStatus.LOGIN_REQUIRED and "checkout" not in chamadas


def test_terabyte_verificacao_com_janela_espera_a_pessoa_ate_o_prazo(sem_espera, monkeypatch):
    import itertools
    import time
    import types

    relogio = itertools.count(0, 50)
    monkeypatch.setattr(terabyte, "time", types.SimpleNamespace(monotonic=lambda: next(relogio), strftime=time.strftime))
    avisos: list[int] = []

    def aviso_quebrado():
        avisos.append(1)
        raise RuntimeError("bandeja fora")

    pagina = _Pagina(_PAGAMENTO_TERABYTE, "Verificando seu navegador")
    leituras = [0]

    async def le(_script):
        leituras[0] += 1
        return "Formas de pagamento" if leituras[0] >= 3 else pagina.corpo

    pagina.evaluate = le
    driver = terabyte.TerabyteDriver(None, on_captcha=aviso_quebrado)
    assert asyncio.run(driver._handle_captcha(pagina)) is True
    assert avisos == [1] and leituras[0] == 3
    preso = _Pagina(_PAGAMENTO_TERABYTE, "Verificando seu navegador")
    assert asyncio.run(terabyte.TerabyteDriver(None)._handle_captcha(preso)) is False


def test_terabyte_erro_inesperado_vira_falha_e_fecha_a_pagina(sem_espera):
    pagina = _Pagina(_TERABYTE)

    class Contexto:
        async def new_page(self):
            return pagina

    async def quebra(*_args, **_kwargs):
        raise RuntimeError("navegador fechou")

    pagina.goto = quebra
    resultado = asyncio.run(terabyte.TerabyteDriver(Contexto()).buy(_TERABYTE, 500.0))
    assert resultado.status == BuyStatus.FAILED and resultado.detail == "navegador fechou"
    assert pagina.fechada and resultado.screenshot == ""


def test_amazon_recusa_o_prime_escolhe_pix_confirma_o_pagamento_e_para_na_revisao(sem_espera):
    pagina = _Pagina(_PAGAMENTO_AMAZON, "Escolha a forma de pagamento")
    logs: list[str] = []

    def recusou():
        del pagina.botoes["Não, obrigado"]

    def confirmou():
        pagina.url = "https://www.amazon.com.br/checkout/p/p-1/spc"
        pagina.seletores["#placeOrder"] = [finalizar]

    prime = _Elemento("Não, obrigado", recusou)
    radio = _Elemento("pix")
    usar = _Elemento("Usar este método de pagamento", confirmou)
    finalizar = _Elemento("Confirmar pedido")
    pagina.botoes["Não, obrigado"] = prime
    pagina.seletores[amazon._PIX_RADIO] = [radio]
    pagina.seletores[amazon._USE_PAYMENT[0]] = [usar]
    driver = amazon.AmazonDriver(None, on_log=logs.append)
    resultado = asyncio.run(driver._checkout(pagina, _produto("amazon"), False, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert (prime.cliques, radio.cliques, usar.cliques, finalizar.cliques) == (1, 1, 1, 0)
    assert "Oferta do Prime recusada" in logs and "Forma de pagamento PIX selecionada" in logs
    assert logs.index("Etapa: pagamento") < logs.index("Etapa: revisao")


def test_amazon_travado_no_checkout_conta_a_etapa_e_os_botoes_da_tela(sem_espera):
    from watcher.autobuy.models import CHECKOUT_STUCK

    pagina = _Pagina(_PAGAMENTO_AMAZON)
    passos = [0]

    async def carregou(*_args, **_kwargs):
        passos[0] += 1

    async def le(script):
        return ["Voltar", "Ajuda"] if "querySelectorAll('button" in script else "Forma de pagamento"

    pagina.wait_for_load_state = carregou
    pagina.evaluate = le
    resultado = asyncio.run(amazon.AmazonDriver(None)._checkout(pagina, _produto("amazon"), True, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED
    assert resultado.detail == f"{CHECKOUT_STUCK} (etapa 'pagamento'); botões na tela: Voltar | Ajuda"
    assert passos[0] == amazon._MAX_STEPS


def test_amazon_pedido_finalizado_sem_codigo_avisa_para_conferir(sem_espera):
    resultado, finalizar = _amazon_na_revisao("Confirmar pedido com Pix", confirm=True, depois_de_finalizar="Processando")
    assert resultado.status == BuyStatus.FAILED and "confira" in resultado.detail
    assert finalizar.cliques == 1


def test_amazon_codigo_ja_na_tela_nao_finaliza_de_novo(sem_espera):
    resultado, finalizar = _amazon_na_revisao("Confirmar pedido com Pix", confirm=True, corpo=f"Pedido feito\n{_PIX}")
    assert resultado.status == BuyStatus.PIX_READY and resultado.pix_code == _PIX
    assert finalizar.cliques == 0


def test_amazon_simulacao_leva_o_resultado_do_cupom_no_detalhe(sem_espera):
    pagina = _Pagina("https://www.amazon.com.br/checkout/p/p-1/spc", "Desconto aplicado")
    pagina.seletores["#placeOrder"] = [_Elemento("Confirmar pedido com Pix")]
    pagina.seletores[amazon._COUPON_FIELD] = [_Campo()]
    pagina.seletores[amazon._COUPON_APPLY] = [_Elemento("Aplicar")]
    driver = amazon.AmazonDriver(None)
    driver._coupon = "PRIME10"
    resultado = asyncio.run(driver._checkout(pagina, _produto("amazon"), False, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert resultado.detail.endswith("; Cupom PRIME10 aplicado")


def test_amazon_troca_a_forma_de_pagamento_so_fora_da_tela_de_pagamento(sem_espera):
    revisao = _Pagina("https://www.amazon.com.br/checkout/p/p-1/spc")
    trocar = _Elemento("Pagando com Visa")
    revisao.botoes["Pagando com Visa"] = trocar
    assert asyncio.run(amazon.AmazonDriver(None)._select_pix(revisao)) is True
    assert trocar.cliques == 1
    pagamento = _Pagina(_PAGAMENTO_AMAZON)
    outro = _Elemento("Pagando com Visa")
    pagamento.botoes["Pagando com Visa"] = outro
    assert asyncio.run(amazon.AmazonDriver(None)._select_pix(pagamento)) is False
    assert outro.cliques == 0
    assert asyncio.run(amazon.AmazonDriver(None)._select_pix(_Pagina("https://www.amazon.com.br/checkout/p/p-1/spc"))) is False


def test_amazon_confirma_o_pagamento_pelo_texto_do_botao(sem_espera):
    pagina = _Pagina(_PAGAMENTO_AMAZON)
    continuar = _Elemento("Continuar")
    pagina.botoes["Continuar"] = continuar
    pagina.botoes["Continuar comprando"] = _Elemento("Continuar comprando")
    assert asyncio.run(amazon.AmazonDriver(None)._confirm_payment(pagina)) is True
    assert continuar.cliques == 1
    so_voltar = _Pagina(_PAGAMENTO_AMAZON)
    voltar = _Elemento("Continuar comprando")
    so_voltar.botoes["Continuar comprando"] = voltar
    assert asyncio.run(amazon.AmazonDriver(None)._confirm_payment(so_voltar)) is False
    assert voltar.cliques == 0


def test_amazon_login_e_captcha_reconhecidos_pelo_formulario(sem_espera):
    driver = amazon.AmazonDriver(None)
    senha = _Pagina(_PAGAMENTO_AMAZON, seletores={"#ap_password": [_Elemento("senha")]})
    assert asyncio.run(driver._needs_login(senha)) is True
    assert asyncio.run(driver._needs_login(_Pagina(_PAGAMENTO_AMAZON))) is False
    formulario = _Pagina(_PAGAMENTO_AMAZON, seletores={"form[action*='validateCaptcha']": [_Elemento("form")]})
    assert asyncio.run(driver._captcha_present(formulario)) is True
    assert asyncio.run(driver._captcha_present(_Pagina(_PAGAMENTO_AMAZON, "Revisar pedido"))) is False


def test_amazon_captcha_com_janela_espera_a_pessoa_ate_o_prazo(sem_espera, monkeypatch):
    import itertools
    import time
    import types

    relogio = itertools.count(0, 50)
    monkeypatch.setattr(amazon, "time", types.SimpleNamespace(monotonic=lambda: next(relogio), strftime=time.strftime))
    avisos: list[int] = []

    def aviso_quebrado():
        avisos.append(1)
        raise RuntimeError("bandeja fora")

    pagina = _Pagina(_PAGAMENTO_AMAZON, "Digite os caracteres que você vê abaixo")
    leituras = [0]

    async def le(_script):
        leituras[0] += 1
        return "Revisar pedido" if leituras[0] >= 3 else pagina.corpo

    pagina.evaluate = le
    logs: list[str] = []
    driver = amazon.AmazonDriver(None, on_log=logs.append, on_captcha=aviso_quebrado)
    assert asyncio.run(driver._handle_captcha(pagina)) is True
    assert avisos == [1] and leituras[0] == 3
    assert logs[-1] == "Captcha resolvido, retomando o checkout"
    preso = _Pagina(_PAGAMENTO_AMAZON, "Digite os caracteres que você vê abaixo")
    assert asyncio.run(amazon.AmazonDriver(None)._handle_captcha(preso)) is False


@pytest.mark.parametrize(
    "url, etapa",
    [
        ("https://www.amazon.com.br/dp/B0CGBQVDNK", "produto"),
        ("https://www.amazon.com.br/checkout/p/p-1/pip?x=1", "prime"),
        ("https://www.amazon.com.br/checkout/p/p-1/spc/", "revisao"),
        ("https://www.amazon.com.br/checkout/p/p-1/pay", "pagamento"),
        ("https://www.amazon.com.br/checkout/p/p-1/address", "address"),
    ],
)
def test_amazon_nome_da_etapa_pelo_endereco(url, etapa):
    assert amazon.AmazonDriver._step_name(url) == etapa


def test_amazon_quantidade_sem_opcao_numerica_segue_com_uma(sem_espera):
    class Seletor:
        async def count(self):
            return 1

        async def evaluate(self, _script):
            return ["", "mais"]

        @property
        def first(self):
            return self

    pagina = _Pagina(_ASIN)
    pagina.locator = lambda _seletor, has_text=None: Seletor()
    logs: list[str] = []
    driver = amazon.AmazonDriver(None, on_log=logs.append)
    driver._quantity = 4
    asyncio.run(driver._set_quantity(pagina))
    assert logs == ["Não consegui ajustar a quantidade, seguindo com 1 (pedido: 4)"]


def test_amazon_compra_sem_botao_comprar_agora_bloqueia_e_fecha_a_pagina(sem_espera):
    produto = ProductInfo(id=1, title="SSD", url=_ASIN, pix_price=300.0, card_price=320.0, available=True)
    pagina = _Pagina(_ASIN)

    class Contexto:
        async def new_page(self):
            return pagina

    driver = amazon.AmazonDriver(Contexto())

    async def le(_page, _url):
        return produto

    driver.fetch_product = le
    resultado = asyncio.run(driver.buy(_ASIN, 500.0, confirm=True))
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == "Botão 'Comprar agora' não encontrado"
    assert pagina.fechada and "produto" in resultado.timings


def test_amazon_tempo_esgotado_vira_falha_com_motivo(sem_espera):
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    pagina = _Pagina(_ASIN)

    class Contexto:
        async def new_page(self):
            return pagina

    async def lento(*_args, **_kwargs):
        raise PlaywrightTimeout("Timeout 45000ms exceeded")

    pagina.goto = lento
    resultado = asyncio.run(amazon.AmazonDriver(Contexto()).buy(_ASIN, 500.0))
    assert resultado.status == BuyStatus.FAILED and resultado.detail == "Tempo esgotado: Timeout 45000ms exceeded"
    assert pagina.fechada


def test_amazon_codigo_capturado_mesmo_sem_conseguir_gravar_em_disco(sem_espera, monkeypatch):
    def disco_cheio(*_args, **_kwargs):
        raise OSError(28, "sem espaço")

    monkeypatch.setattr(amazon, "save_record", disco_cheio)
    logs: list[str] = []
    driver = amazon.AmazonDriver(None, on_log=logs.append)
    pagina = _Pagina("https://www.amazon.com.br/checkout/p/p-1/spc", f"Pedido feito\n{_PIX}")
    resultado = asyncio.run(driver._checkout(pagina, _produto("amazon"), True, amazon._Stopwatch()))
    assert resultado.status == BuyStatus.PIX_READY and resultado.pix_code == _PIX
    assert any(log.startswith("Não consegui gravar o registro do PIX em disco") for log in logs)
    assert logs[-1] == "Código PIX capturado"


def _run_kabum(sai_do_pre_carrinho=True):
    produto = ProductInfo(id=1, title="SSD", url="https://www.kabum.com.br/produto/1/ssd", pix_price=300.0, card_price=320.0, available=True)
    pagina = _Pagina(produto.url)
    cliques: list[str] = []
    chamadas: list[str] = []

    async def clicou(seletor, **_kwargs):
        cliques.append(seletor)
        pagina.url = "https://www.kabum.com.br/precarrinho/1"

    async def esperou(*_args, **_kwargs):
        return None

    pagina.click = clicou
    pagina.wait_for_url = esperou
    driver = kabum.KabumDriver(None)

    async def le(_page, _url):
        return produto

    async def sai(_page):
        chamadas.append("pre-carrinho")
        return sai_do_pre_carrinho

    async def fecha(*_args):
        chamadas.append("checkout")
        return BuyResult(status=BuyStatus.DRY_RUN_OK, product=produto)

    driver.fetch_product = le
    driver._leave_precart = sai
    driver._checkout = fecha
    resultado = asyncio.run(driver._run(pagina, produto.url, 500.0, 0.0, True, kabum._Stopwatch()))
    return resultado, cliques, chamadas


def test_kabum_compra_passa_pelo_pre_carrinho_antes_do_checkout(sem_espera):
    resultado, cliques, chamadas = _run_kabum()
    assert resultado.status == BuyStatus.DRY_RUN_OK
    assert cliques == ["#purchase"] and chamadas == ["pre-carrinho", "checkout"]
    resultado, cliques, chamadas = _run_kabum(sai_do_pre_carrinho=False)
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == "Não consegui sair do pré-carrinho"
    assert chamadas == ["pre-carrinho"]


def test_kabum_codigo_ja_no_carrinho_vira_pix_sem_avancar(sem_espera):
    avancar = _Elemento("Continuar")
    pagina = _Pagina(_CARRINHO, f"Pedido feito\n{_PIX}", botoes={"Continuar": avancar})
    resultado = asyncio.run(kabum.KabumDriver(None)._checkout(pagina, _produto(), True, kabum._Stopwatch()))
    assert resultado.status == BuyStatus.PIX_READY and resultado.pix_code == _PIX
    assert avancar.cliques == 0


def test_kabum_captcha_com_navegador_oculto_bloqueia_o_checkout(sem_espera):
    from watcher.autobuy.models import HIDDEN_CAPTCHA

    avancar = _Elemento("Continuar")
    pagina = _Pagina(_CARRINHO, seletores={_DESAFIO: [_Quadro(400, 580)]}, botoes={"Continuar": avancar})
    driver = kabum.KabumDriver(None, headless=True)
    resultado = asyncio.run(driver._checkout(pagina, _produto(), True, kabum._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == HIDDEN_CAPTCHA
    assert avancar.cliques == 0


def test_kabum_avancando_sem_sair_do_lugar_desiste_no_limite_de_passos(sem_espera):
    from watcher.autobuy.models import CHECKOUT_STUCK

    avancar = _Elemento("Continuar")
    pagina = _Pagina("https://www.kabum.com.br/endereco", "Escolha o endereço", botoes={"Continuar": avancar})
    resultado = asyncio.run(kabum.KabumDriver(None)._checkout(pagina, _produto(), True, kabum._Stopwatch()))
    assert resultado.status == BuyStatus.BLOCKED and resultado.detail == CHECKOUT_STUCK
    assert avancar.cliques == kabum._MAX_CHECKOUT_STEPS


def test_kabum_login_reconhecido_pela_senha_ou_pelo_texto(sem_espera):
    driver = kabum.KabumDriver(None)
    senha = _Pagina(_CARRINHO, seletores={"input[type='password']": [_Elemento("senha")]})
    assert asyncio.run(driver._needs_login(senha)) is True
    assert asyncio.run(driver._needs_login(_Pagina(_CARRINHO, "Acesse sua conta para continuar"))) is True
    assert asyncio.run(driver._needs_login(_Pagina(_CARRINHO, "Resumo do pedido"))) is False


def test_kabum_escolhe_pix_so_na_tela_de_pagamento(sem_espera):
    logs: list[str] = []
    opcao = _Elemento("PIX")
    pagina = _Pagina("https://www.kabum.com.br/pagamento", seletores={"#paymentPage button": [opcao]})
    asyncio.run(kabum.KabumDriver(None, on_log=logs.append)._select_pix_option(pagina))
    assert opcao.cliques == 1 and logs == ["Forma de pagamento PIX selecionada"]
    pelo_papel = _Elemento("Pix")
    pagina = _Pagina("https://www.kabum.com.br/pagamento", botoes={"Pix": pelo_papel})
    asyncio.run(kabum.KabumDriver(None)._select_pix_option(pagina))
    assert pelo_papel.cliques == 1
    fora = _Elemento("PIX")
    asyncio.run(kabum.KabumDriver(None)._select_pix_option(_Pagina(_CARRINHO, seletores={"#paymentPage button": [fora]})))
    assert fora.cliques == 0


def test_kabum_pagamento_so_e_pix_quando_aparece_depois_da_forma_de_pagamento(sem_espera):
    driver = kabum.KabumDriver(None)
    assert asyncio.run(driver._payment_is_pix(_Pagina(_CARRINHO, "Pague com PIX e ganhe desconto"))) is False
    assert asyncio.run(driver._payment_is_pix(_Pagina(_CARRINHO, "Forma de pagamento\nPIX"))) is True

    quebrada = _Pagina(_CARRINHO)

    async def caiu(_script):
        raise RuntimeError("página fechou")

    quebrada.evaluate = caiu
    assert asyncio.run(driver._payment_is_pix(quebrada)) is False


def test_kabum_fecha_o_aviso_de_cookies_uma_vez(sem_espera):
    recusar = _Elemento("Rejeitar")
    aceitar = _Elemento("Aceitar")
    pagina = _Pagina(_CARRINHO, seletores={"#adopt-reject-all-button": [recusar], "#adopt-accept-all-button": [aceitar]})
    asyncio.run(kabum.KabumDriver(None)._dismiss_consent(pagina))
    assert (recusar.cliques, aceitar.cliques) == (1, 0)


def test_kabum_espera_a_pagina_sair_do_carregando(sem_espera, monkeypatch):
    import itertools
    import types

    relogio = itertools.count(0, 1)
    monkeypatch.setattr(kabum, "time", types.SimpleNamespace(monotonic=lambda: next(relogio)))
    pagina = _Pagina(_CARRINHO)
    leituras = [0]

    async def le(_script):
        leituras[0] += 1
        return "Carregando..." if leituras[0] < 4 else "Resumo"

    pagina.evaluate = le
    asyncio.run(kabum.KabumDriver(None)._wait_page_ready(pagina))
    assert leituras[0] == 4
    leituras[0] = -100
    asyncio.run(kabum.KabumDriver(None)._wait_page_ready(pagina, timeout=5))
    assert leituras[0] < 0
