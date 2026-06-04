from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, SimpleCardWidget, StrongBodyLabel

from monitor.models import EngineState
from .theme import PALETTE, STATE_STYLE, color


class StatCard(SimpleCardWidget):
    def __init__(self, title: str, accent: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(2)

        self._value = QLabel("0")
        font = QFont()
        font.setPointSize(28)
        font.setWeight(QFont.Weight.DemiBold)
        self._value.setFont(font)
        self._value.setStyleSheet(f"color: {color(accent)};")

        self._title = CaptionLabel(title)
        self._title.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))

        layout.addStretch(1)
        layout.addWidget(self._value)
        layout.addWidget(self._title)

    def set_value(self, value: object) -> None:
        self._value.setText(str(value))


class StatusPill(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._dot = QLabel()
        self._dot.setFixedSize(10, 10)
        self._label = StrongBodyLabel("Offline")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        self.set_state(EngineState.OFFLINE)

    def set_state(self, state: EngineState) -> None:
        key, text = STATE_STYLE.get(state, ("offline", "Offline"))
        hex_color = PALETTE[key]
        self._dot.setStyleSheet(f"background-color: {hex_color}; border-radius: 5px;")
        self._label.setText(text)


class ActivityChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(170)
        self._data: list[int] = [0] * 24

    def set_data(self, data: list[int]) -> None:
        self._data = list(data)[:24] or [0] * 24
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        pad_x, pad_top, pad_bottom = 6, 14, 22
        usable_h = height - pad_top - pad_bottom
        peak = max(self._data) or 1
        slot = (width - pad_x * 2) / 24
        bar_w = slot * 0.6

        top = QColor(PALETTE["info"])
        bottom = QColor(PALETTE["capture"])

        for hour, value in enumerate(self._data):
            x = pad_x + slot * hour + (slot - bar_w) / 2
            bar_h = max(3.0, (value / peak) * usable_h) if value else 3.0
            y = pad_top + (usable_h - bar_h)
            rect = QRectF(x, y, bar_w, bar_h)

            path = QPainterPath()
            path.addRoundedRect(rect, bar_w / 2, bar_w / 2)
            if value:
                gradient = QLinearGradient(0, y, 0, y + bar_h)
                gradient.setColorAt(0, top)
                gradient.setColorAt(1, bottom)
                painter.fillPath(path, gradient)
            else:
                painter.fillPath(path, QColor(255, 255, 255, 16))

            if hour % 6 == 0:
                painter.setPen(QColor(PALETTE["muted"]))
                label_font = QFont()
                label_font.setPointSize(8)
                painter.setFont(label_font)
                painter.drawText(
                    QRectF(pad_x + slot * hour, height - pad_bottom + 4, slot, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    f"{hour:02d}h",
                )
        painter.end()
