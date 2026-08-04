from __future__ import annotations

import time

from .. import paths

_MAX_BYTES = 2_000_000
_KEEP_TAIL = 400_000


def log_path():
    return paths.autobuy_dir() / "autobuy.log"


def _rotate(path) -> None:
    try:
        if path.stat().st_size <= _MAX_BYTES:
            return
        conteudo = path.read_bytes()[-_KEEP_TAIL:]
        path.write_bytes(b"[log truncado]\n" + conteudo)
    except OSError:
        pass


def write(message: str, level: str = "info", store: str = "") -> None:
    if not message:
        return
    path = log_path()
    carimbo = time.strftime("%d/%m %H:%M:%S")
    loja = f"[{store}] " if store else ""
    linha = f"{carimbo} {level.upper():7} {loja}{message}\n"
    try:
        with open(path, "a", encoding="utf-8") as arquivo:
            arquivo.write(linha)
    except OSError:
        return
    _rotate(path)


def separator(title: str) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as arquivo:
            arquivo.write(f"\n{'=' * 66}\n{time.strftime('%d/%m/%Y %H:%M:%S')}  {title}\n{'=' * 66}\n")
    except OSError:
        pass


def tail(lines: int = 120) -> str:
    path = log_path()
    if not path.exists():
        return ""
    try:
        conteudo = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(conteudo[-lines:])
