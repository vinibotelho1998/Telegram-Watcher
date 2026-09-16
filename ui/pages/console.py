from __future__ import annotations

import html
from time import strftime

from PyQt6.QtWidgets import QTextEdit, QWidget
from qfluentwidgets import ComboBox, FluentIcon, TransparentPushButton

from watcher.models import LogLevel

from ..common import PageView
from ..theme import LOG_COLOR, PALETTE

_FILTERS: list[tuple[str, LogLevel | None]] = [
    ("Tudo", None),
    ("Capturas", LogLevel.CAPTURE),
    ("Sucesso", LogLevel.SUCCESS),
    ("Avisos", LogLevel.WARNING),
    ("Erros", LogLevel.ERROR),
]

_STYLE = """
QTextEdit#consoleView {
    background: rgba(0, 0, 0, 0.28);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 12px;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 12px;
}
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
        self.view.setObjectName("consoleView")
        self.view.setStyleSheet(_STYLE)
        self.add_body(self.view, 1)

    def _on_filter(self, index: int) -> None:
        self._active = _FILTERS[index][1]
        self.view.clear()
        for level, timestamp, message in self._buffer:
            if self._active is None or self._active == level:
                self.view.append(self._line(level, timestamp, message))

    def append(self, message: str, level: LogLevel) -> None:
        timestamp = strftime("%H:%M:%S")
        self._buffer.append((level, timestamp, message))
        if len(self._buffer) > 800:
            self._buffer = self._buffer[-800:]
        if self._active is None or self._active == level:
            self.view.append(self._line(level, timestamp, message))

    @staticmethod
    def _line(level: LogLevel, timestamp: str, message: str) -> str:
        tone = PALETTE[LOG_COLOR.get(level, "muted")]
        return (
            f'<span style="color:{PALETTE["muted"]}">{timestamp}</span>&nbsp;&nbsp;'
            f'<span style="color:{tone}">{html.escape(message)}</span>'
        )

    def clear(self) -> None:
        self._buffer.clear()
        self.view.clear()
