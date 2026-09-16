from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS = sorted(p for p in (RAIZ / "ui").rglob("*.py") if "__pycache__" not in str(p))
ARQUIVOS += [RAIZ / "app.py"]


@pytest.mark.parametrize("arquivo", ARQUIVOS, ids=lambda p: p.name)
def test_arquivo_da_interface_compila(arquivo):
    fonte = arquivo.read_text(encoding="utf-8")
    try:
        ast.parse(fonte, filename=str(arquivo))
    except SyntaxError as erro:
        pytest.fail(f"{arquivo.relative_to(RAIZ)} linha {erro.lineno}: {erro.msg}")


def test_modulos_do_nucleo_compilam():
    for arquivo in sorted((RAIZ / "watcher").rglob("*.py")):
        if "__pycache__" in str(arquivo):
            continue
        ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
