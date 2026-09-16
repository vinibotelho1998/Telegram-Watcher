from __future__ import annotations

from qfluentwidgets import Theme, setTheme, setThemeColor

from watcher.models import EngineState, LogLevel

ACCENT = "#3B82F6"
TEXT = ("#1B1B1B", "#F2F3F5")

PALETTE = {
    "success": "#22C55E",
    "danger": "#EF4444",
    "warning": "#F59E0B",
    "info": "#3B82F6",
    "capture": "#38BDF8",
    "muted": "#8A8F98",
    "online": "#22C55E",
    "offline": "#6B7280",
    "busy": "#F59E0B",
}

STATE_STYLE: dict[EngineState, tuple[str, str]] = {
    EngineState.OFFLINE: ("offline", "Offline"),
    EngineState.CONNECTING: ("busy", "Conectando"),
    EngineState.AUTH: ("busy", "Autenticando"),
    EngineState.ONLINE: ("online", "Online"),
    EngineState.STOPPING: ("busy", "Parando"),
    EngineState.ERROR: ("danger", "Erro"),
}

LOG_COLOR: dict[LogLevel, str] = {
    LogLevel.INFO: "muted",
    LogLevel.SUCCESS: "success",
    LogLevel.WARNING: "warning",
    LogLevel.ERROR: "danger",
    LogLevel.CAPTURE: "capture",
}


def apply_theme(dark: bool = True) -> None:
    setTheme(Theme.DARK if dark else Theme.LIGHT)
    setThemeColor(ACCENT)


def color(key: str) -> str:
    return PALETTE.get(key, key)
