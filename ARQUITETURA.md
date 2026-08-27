# Arquitetura

Guia técnico do **Dbx Telegram Monitor** para quem for desenvolver no projeto.

## Visão geral

Aplicativo desktop para monitorar canais do Telegram e replicar mensagens em tempo real, com filtros por palavra e detecção inteligente de cupons e ofertas. Interface em PyQt6 com QFluentWidgets, motor assíncrono com Telethon integrado ao Qt via qasync.

## Arquitetura

O código separa o núcleo (sem dependência de interface) da camada visual.

```
app.py        Bootstrap: QApplication, loop qasync, logging, AppWindow
monitor/      Núcleo testável, independente de Qt
  config.py     AppConfig: leitura e escrita do .env
  models.py     Filter, Channel, Metrics, EngineState, LogLevel
  storage.py    FilterStore e ChannelCache em JSON
  matching.py   normalização, variações e lógica de palavras (+ e ou)
  detector.py   CouponDetector e avaliação de smart rules
  filtering.py  resolve_destinations, decisão de roteamento
  engine.py     MonitorEngine: Telethon, dedup, rate limit, callbacks
  autobuy/      Compra automática multi-loja, separada do monitor
    models.py     WatchItem (produto vigiado), BuyResult, BuyStatus
    criteria.py   tradutor entre palavras da interface e a sintaxe de matching
    resolver.py   extração de links e resolução de encurtadores
    registry.py   StoreSpec por loja, roteamento de URL e escolha do driver
    browser.py    Chromium persistente do robô (Playwright)
    dom.py        helpers de página compartilhados entre drivers
    pix.py        extração e validação do código PIX (comum às lojas)
    kabum.py      KabumDriver: checkout até gerar o PIX, cupom, métricas
    amazon.py     AmazonDriver: Comprar agora, recusa Prime, força PIX
    terabyte.py   TerabyteDriver: frete, PIX e cupom; exige janela visível
    manager.py    AutobuyManager: observa capturas, roteia loja, vigia sessão
    storage.py    AutobuyStore em data/autobuy.json
    records.py    registros de PIX gerados para a interface
    notify.py     notificação nas Mensagens Salvas do Telegram
ui/           Interface PyQt6
  window.py     AppWindow, orquestra núcleo e telas
  pages/        dashboard, filters, channels, autobuy, console, settings
  dialogs.py    CodeDialog, PasswordDialog, FilterDialog
  watch_dialog.py  editor de produto vigiado com palavras em chips e teste ao vivo
  chips.py      WordChips e FlowLayout para listas de palavras
  widgets.py    StatCard, StatusPill, ActivityChart
  theme.py      tema, paleta e tokens
  tray.py       ícone de bandeja
tests/        Testes pytest do núcleo
```

O auto-compra funciona em conjunto com o monitor mas é um componente separado: o motor apenas emite o texto capturado pelo callback genérico `on_text`, e o `AutobuyManager` decide se compra. A compra usa Playwright (`requirements-autobuy.txt` + `playwright install chromium`) com perfil persistente em `%APPDATA%\DbxTelegramMonitor\browser`, onde o login na loja é feito uma única vez pela janela real do site (nunca por formulário no app). O fluxo para na geração do código PIX: o pagamento é sempre manual.

Cada loja é um driver com a mesma interface: `fetch_product(page, url) -> ProductInfo` e `buy(url, max_price, confirm, coupon, min_price, quantity) -> BuyResult`. O `registry` casa a URL com a loja e instancia o driver certo, então adicionar uma loja é escrever um módulo e registrar um `StoreSpec`. Duas invariantes valem para todo driver: o pagamento é sempre PIX, e o driver **recusa finalizar** se a revisão não confirmar PIX. Quando aparece captcha, o driver alerta pelo callback `on_captcha` e espera a resolução manual em vez de falhar.

Para adicionar uma loja: as funções de URL em `urls.py`, o driver com os dois métodos acima, e a entrada no `registry.STORES`. O login novo entra em `scripts/autobuy_login.py`.

O `StoreSpec` declara as peculiaridades de cada loja. `requires_window=True` quando a loja bloqueia navegador headless: a Terabyte fica atrás do Cloudflare e responde 403 sem janela, então o manager ignora a preferência global e abre visível para ela. `can_checkout=False` marca loja cujo pagamento ainda não foi mapeado, para a interface avisar em vez de prometer compra.

Os seletores vêm de mapeamento real no site, nunca de suposição, e há testes que travam as escolhas frágeis. Três armadilhas já custaram caro: na Amazon os `id` são gerados por sessão (`pp-8H7pkg-107`), então só `name` e `data-testid` servem; na Terabyte existe `#ppixcartao` (Pix + Cartão) ao lado de `#ppix` (PIX puro), e casar por `id*=pix` compraria metade no cartão; e a Terabyte devolve para `/Central.obj` quando a sessão expira, sem pedir login, o que o driver trata como `LOGIN_REQUIRED`.

A interface conversa com o núcleo apenas pela `AppWindow`, que detém a configuração, os filtros, o cache de canais e o motor. O motor recebe callbacks (`on_state`, `on_log`, `on_event`, `on_metrics`) e provedores assíncronos de código e senha. Como tudo roda no mesmo loop do qasync, os callbacks atualizam a interface diretamente.

## Stack

| Item | Tecnologia |
| --- | --- |
| Python | 3.10 ou superior |
| Interface | PyQt6, PyQt6-Fluent-Widgets |
| Telegram | Telethon |
| Assíncrono | qasync |
| Configuração | python-dotenv |
| Build | PyInstaller |

## Convenções

- Identificadores em inglês, textos visíveis ao usuário em português.
- Sem comentários e sem docstrings. O código deve se explicar pelos nomes.
- Type hints em assinaturas públicas. `from __future__ import annotations` em todos os módulos.
- Dados sensíveis e voláteis vivem em `%APPDATA%\DbxTelegramMonitor` (`.env`, `data`, `sessions`, `logs`).

## Rodar e testar

```bash
pip install -r requirements.txt
python app.py

pip install pytest
python -m pytest tests/ -q
```

Os testes cobrem modelos, configuração, detector, correspondência de palavras, roteamento de filtros e o motor sem rede, com eventos simulados.

## Diretórios de dados

`monitor/paths.py` centraliza os caminhos. Em desenvolvimento e no executável os dados persistentes ficam em `%APPDATA%\DbxTelegramMonitor`. Recursos da interface, como ícones, são resolvidos por `resource_path`, compatível com PyInstaller.
