from __future__ import annotations

from qfluentwidgets import Theme, isDarkTheme, setTheme, setThemeColor

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

_LIGHT_TEXT = {
    "success": "#15803D",
    "danger": "#B91C1C",
    "warning": "#B45309",
    "capture": "#0369A1",
    "muted": "#5F6670",
}

MUTED = (_LIGHT_TEXT["muted"], PALETTE["muted"])

STATE_STYLE: dict[EngineState, tuple[str, str]] = {
    EngineState.OFFLINE: ("offline", "Offline"),
    EngineState.CONNECTING: ("busy", "Conectando"),
    EngineState.AUTH: ("busy", "Autenticando"),
    EngineState.ONLINE: ("online", "Online"),
    EngineState.RECONNECTING: ("busy", "Reconectando"),
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


def text_tone(key: str) -> str:
    if not isDarkTheme() and key in _LIGHT_TEXT:
        return _LIGHT_TEXT[key]
    return PALETTE.get(key, key)


def surface() -> str:
    return "rgba(0, 0, 0, 0.28)" if isDarkTheme() else "rgba(0, 0, 0, 0.04)"


def hairline() -> str:
    return "rgba(255, 255, 255, 0.06)" if isDarkTheme() else "rgba(0, 0, 0, 0.10)"


def faint() -> str:
    return "rgba(255, 255, 255, 0.12)" if isDarkTheme() else "rgba(0, 0, 0, 0.12)"
