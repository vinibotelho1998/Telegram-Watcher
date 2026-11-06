import os
import sys
from pathlib import Path

import pytest

from watcher import linux_desktop, paths

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="integração do Linux")


def test_diretorio_xdg_privado_e_compatibilidade_com_pasta_antiga(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    legacy = tmp_path / ".telegramwatcher"
    legacy.mkdir()
    assert paths.app_dir() == legacy
    legacy.rmdir()
    root = paths.app_dir()
    assert root == tmp_path / "xdg/telegramwatcher"
    assert root.stat().st_mode & 0o077 == 0


def test_autostart_liga_e_desliga_com_caminho_entre_aspas(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(paths, "resource_path", lambda *_: Path("/opt/app com 10%/abrir.sh"))
    linux_desktop.set_autostart(True)
    assert 'Exec="/opt/app com 10%%/abrir.sh" --bandeja' in linux_desktop.autostart_file().read_text()
    linux_desktop.set_autostart(False)
    assert not linux_desktop.autostart_file().exists()


def test_cookies_e_trava_do_chromium_no_linux(tmp_path, monkeypatch):
    import socket
    from watcher.autobuy import browser

    monkeypatch.setattr(paths, "browser_profile_dir", lambda: tmp_path)
    default = tmp_path / "Default"
    default.mkdir()
    (default / "Cookies").touch()
    assert browser._cookie_file() == default / "Cookies"
    lock = tmp_path / "SingletonLock"
    lock.symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    assert browser.profile_in_use()
    lock.unlink()
    assert not browser.profile_in_use()
