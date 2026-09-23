from __future__ import annotations

import html
from time import strftime

from PyQt6.QtWidgets import QTextEdit, QWidget
from qfluentwidgets import ComboBox, FluentIcon, TransparentPushButton, qconfig

from watcher.models import LogLevel

from ..common import PageView
from ..theme import LOG_COLOR, hairline, surface, text_tone

_FILTERS: list[tuple[str, LogLevel | None]] = [
    ("Tudo", None),
    ("Capturas", LogLevel.CAPTURE),
    ("Sucesso", LogLevel.SUCCESS),
    ("Avisos", LogLevel.WARNING),
    ("Erros", LogLevel.ERROR),
]

_MAX_LINES = 800


def _style() -> str:
    return f"""
QTextEdit#consoleView {{
    background: {surface()};
    border: 1px solid {hairline()};
    border-radius: 10px;
    padding: 12px;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12px;
}}
"""


class ConsolePage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("consolePage", "Console", parent=parent)
        self._buffer: list[tuple[LogLevel, str, str]] = []
        self._active: LogLevel | None = None

        self.combo = ComboBox()
        for label, _ in _FILTERS:
            self.combo.addItem(label)
        self.combo.setFixedWidth(150)
        self.combo.currentIndexChanged.connect(self._on_filter)

        clear_btn = TransparentPushButton("Limpar")
        clear_btn.setIcon(FluentIcon.BROOM)
        clear_btn.clicked.connect(self.clear)

        self.add_header_widget(self.combo)
        self.add_header_widget(clear_btn)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.document().setMaximumBlockCount(_MAX_LINES)
        self.view.setObjectName("consoleView")
        self.view.setStyleSheet(_style())
        self.add_body(self.view, 1)
        qconfig.themeChanged.connect(self._on_theme)

    def _on_theme(self, *_args) -> None:
        self.view.setStyleSheet(_style())
        self._render()

    def _on_filter(self, index: int) -> None:
        self._active = _FILTERS[index][1]
        self._render()

    def _render(self) -> None:
        self.view.clear()
        for level, timestamp, message in self._buffer:
            if self._active is None or self._active == level:
                self.view.append(self._line(level, timestamp, message))

    def append(self, message: str, level: LogLevel) -> None:
        timestamp = strftime("%H:%M:%S")
        self._buffer.append((level, timestamp, message))
        if len(self._buffer) > _MAX_LINES:
            self._buffer = self._buffer[-_MAX_LINES:]
        if self._active is None or self._active == level:
            self.view.append(self._line(level, timestamp, message))

    @staticmethod
    def _line(level: LogLevel, timestamp: str, message: str) -> str:
        tone = text_tone(LOG_COLOR.get(level, "muted"))
        return (
            f'<span style="color:{text_tone("muted")}">{timestamp}</span>&nbsp;&nbsp;'
            f'<span style="color:{tone}">{html.escape(message)}</span>'
        )

    def clear(self) -> None:
        self._buffer.clear()
        self.view.clear()
