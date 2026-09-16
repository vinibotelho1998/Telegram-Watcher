# Terceiros

Telegram Watcher
Copyright (C) 2026 Vinicius Botelho

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os
termos da GNU General Public License v3.0, publicada pela Free Software Foundation.
Veja [LICENSE](LICENSE).

## Por que GPLv3 e não MIT

A interface depende de duas bibliotecas GPLv3:

| Biblioteca | Licença declarada | Origem |
|---|---|---|
| PyQt6 | `GPL-3.0-only` | Riverbank Computing |
| PyQt6-Fluent-Widgets | `GPLv3` | zhiyiYo, qfluentwidgets.com |

A GPL é copyleft: um trabalho combinado com elas tem de sair sob a mesma licença.
Trocar PyQt6 por PySide6, que é LGPL, **não** resolveria, porque o Fluent-Widgets é
GPLv3 em qualquer binding. As alternativas seriam licença comercial das duas ou
reescrever a interface inteira sem o Fluent-Widgets.

Riverbank e qfluentwidgets vendem licença comercial. Quem quiser usar este código num
produto fechado precisa dela, não basta este repositório.

## Demais dependências

| Biblioteca | Licença | Para quê |
|---|---|---|
| Telethon | MIT | cliente do Telegram |
| qasync | BSD 2-Clause | junta o loop do asyncio com o do Qt |
| python-dotenv | BSD 3-Clause | leitura do `.env` |
| Pillow | MIT-CMU | imagem e ícone |
| Playwright | Apache 2.0 | automação do navegador na auto-compra |
| PyInstaller | GPLv2 ou posterior, com exceção que permite distribuir programas não livres | gera o executável |

A exceção do PyInstaller permite distribuir o executável gerado sob a licença que
você quiser, então ela não impõe nada além do que o PyQt6 já impõe aqui.

As licenças acima, exceto a do Playwright, foram lidas dos metadados dos pacotes
instalados, não de memória.

O instalador leva ainda o **Qt 6**, da The Qt Company, usado através do PyQt6 e
portanto sob GPLv3 nesta distribuição.

## Marcas citadas

KaBuM, Amazon e Terabyte aparecem no código apenas como nomes das lojas que a
auto-compra sabe percorrer. Não há vínculo, patrocínio nem endosso de nenhuma delas.
