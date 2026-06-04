# AGENTS.md

Guia técnico do **DBX Telegram Monitor** para quem desenvolve.

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
ui/           Interface PyQt6
  window.py     AppWindow, orquestra núcleo e telas
  pages/        dashboard, filters, channels, console, settings
  dialogs.py    CodeDialog, PasswordDialog, FilterDialog
  widgets.py    StatCard, StatusPill, ActivityChart
  theme.py      tema, paleta e tokens
  tray.py       ícone de bandeja
tests/        Testes pytest do núcleo
```

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
- Dados sensíveis e voláteis vivem em `%APPDATA%\TelegramMonitorPro` (`.env`, `data`, `sessions`, `logs`).

## Rodar e testar

```bash
pip install -r requirements.txt
python app.py

pip install pytest
python -m pytest tests/ -q
```

Os testes cobrem modelos, configuração, detector, correspondência de palavras, roteamento de filtros e o motor sem rede, com eventos simulados.

## Diretórios de dados

`monitor/paths.py` centraliza os caminhos. Em desenvolvimento e no executável os dados persistentes ficam em `%APPDATA%\TelegramMonitorPro`. Recursos da interface, como ícones, são resolvidos por `resource_path`, compatível com PyInstaller.
