from __future__ import annotations

import os
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

from .. import paths

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]

PROFILE_BUSY = (
    "O navegador do robô já está aberto: feche a janela 'Google Chrome for Testing'. "
    "Enquanto ela existir o perfil fica travado e o robô abre uma sessão vazia, "
    "como se você não estivesse logado em loja nenhuma."
)

_COOKIE_FILE = ("Default", "Network", "Cookies")


class ProfileInUse(RuntimeError):
    pass


def session_expiry(domain: str, names: tuple[str, ...]) -> float | None:
    linhas = _read_cookie_rows(domain, names)
    if not linhas:
        return None
    prazos = [expira for _n, persistente, expira in linhas if persistente and expira > 0]
    return min(prazos) if prazos else None


def _read_cookie_rows(domain: str, names: tuple[str, ...]) -> list[tuple[str, int, int]]:
    import shutil
    import sqlite3
    import tempfile

    origem = paths.browser_profile_dir().joinpath(*_COOKIE_FILE)
    if not origem.exists() or not names:
        return []
    copia = Path(tempfile.gettempdir()) / f"watcher-cookies-{os.getpid()}.db"
    try:
        shutil.copy2(origem, copia)
    except OSError:
        return []
    try:
        con = sqlite3.connect(copia)
        try:
            marcadores = ",".join("?" for _ in names)
            consulta = (
                "select name, is_persistent, expires_utc/1000000-11644473600 "
                f"from cookies where host_key like ? and name in ({marcadores})"
            )
            return list(con.execute(consulta, (f"%{domain}%", *names)))
        finally:
            con.close()
    except sqlite3.Error:
        return []
    finally:
        try:
            copia.unlink()
        except OSError:
            pass


def has_session_cookies(domain: str, names: tuple[str, ...]) -> bool | None:
    import time

    if not names:
        return None
    linhas = _read_cookie_rows(domain, names)
    if not linhas:
        return False
    agora = int(time.time())
    return any(not persistente or expira > agora for _nome, persistente, expira in linhas)


def profile_in_use() -> bool:
    alvo = paths.browser_profile_dir().joinpath(*_COOKIE_FILE)
    if not alvo.exists():
        return False
    try:
        with open(alvo, "rb+"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _session_file() -> Path:
    return paths.data_dir() / "session_cookies.json"


async def save_session_cookies(context: BrowserContext) -> int:
    import json

    try:
        cookies = await context.cookies()
    except Exception:
        return 0
    voláteis = [c for c in cookies if not c.get("expires") or c["expires"] <= 0]
    if not voláteis:
        return 0
    try:
        _session_file().write_text(json.dumps(voláteis, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return 0
    return len(voláteis)


async def restore_session_cookies(context: BrowserContext) -> int:
    import json

    alvo = _session_file()
    if not alvo.exists():
        return 0
    try:
        cookies = json.loads(alvo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(cookies, list) or not cookies:
        return 0
    try:
        await context.add_cookies(cookies)
    except Exception:
        return 0
    return len(cookies)


async def close_context(context: BrowserContext) -> None:
    await save_session_cookies(context)
    try:
        await context.close()
    except Exception:
        pass
    close_real_chrome()


_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

_cdp_process = None


def system_chrome() -> str | None:
    import shutil

    for caminho in _CHROME_PATHS:
        if Path(caminho).exists():
            return caminho
    return shutil.which("chrome")


def cdp_profile_dir() -> Path:
    alvo = paths.app_dir() / "browser_real"
    alvo.mkdir(parents=True, exist_ok=True)
    return alvo


async def open_real_chrome(playwright: Playwright, url: str = "about:blank") -> BrowserContext:
    global _cdp_process
    import socket
    import subprocess

    chrome = system_chrome()
    if not chrome:
        raise ProfileInUse(
            "Esta loja exige o Google Chrome instalado no sistema, e ele não foi encontrado."
        )
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        porta = sonda.getsockname()[1]
    _cdp_process = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={porta}",
            f"--user-data-dir={cdp_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=pt-BR",
            "--accept-lang=pt-BR,pt",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import asyncio

    ultimo = None
    for _ in range(12):
        await asyncio.sleep(1.5)
        try:
            navegador = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{porta}", timeout=8000)
            return navegador.contexts[0]
        except Exception as exc:
            ultimo = exc
    raise ProfileInUse(f"Não consegui conectar ao Chrome do sistema: {ultimo}")


def close_real_chrome() -> None:
    global _cdp_process
    if _cdp_process is None:
        return
    try:
        _cdp_process.terminate()
    except Exception:
        pass
    _cdp_process = None


async def open_context(playwright: Playwright, headless: bool = True) -> BrowserContext:
    if profile_in_use():
        raise ProfileInUse(PROFILE_BUSY)
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(paths.browser_profile_dir()),
            headless=headless,
            args=_LAUNCH_ARGS,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 850},
        )
    except Exception as exc:
        raise ProfileInUse(f"{PROFILE_BUSY} (detalhe: {exc})") from exc
    await restore_session_cookies(context)
    return context
