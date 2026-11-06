"""Integração com a sessão gráfica Linux (XDG Autostart)."""
import os
import sys
import tempfile
from pathlib import Path

from . import paths


def autostart_file() -> Path:
    home = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    if not home.is_absolute():
        home = Path.home() / ".config"
    return home / "autostart" / "telegram-watcher.desktop"


def exec_arg(value: str) -> str:
    # Exec tem sua própria camada de aspas, além do escape do formato .desktop.
    escaped = "".join("\\" + ch if ch in '\\"$`' else ch for ch in value)
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def set_autostart(enabled: bool) -> None:
    target = autostart_file()
    if not enabled:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    program = sys.executable if getattr(sys, "frozen", False) else str(paths.resource_path("abrir.sh"))
    entry = ("[Desktop Entry]\nType=Application\nName=Telegram Watcher\n"
             f"Exec={exec_arg(program)} --bandeja\nTerminal=false\n")
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False, encoding="utf-8") as f:
        tmp = Path(f.name)
        try:
            f.write(entry)
            f.flush()
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
