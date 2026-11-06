import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _sem_rede(*_args, **_kwargs):
    raise OSError("teste não acessa a rede")


@pytest.fixture(autouse=True)
def _isola_ambiente(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.getbasetemp() / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("APPDATA", str(tmp_path_factory.getbasetemp() / "appdata"))
    monkeypatch.setattr("urllib.request.urlopen", _sem_rede)
