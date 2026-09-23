from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import SingleDirectionScrollArea, SubtitleLabel, TitleLabel


def plain(label: QLabel) -> QLabel:
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def make_vscroll() -> tuple[SingleDirectionScrollArea, QVBoxLayout]:
    area = SingleDirectionScrollArea()
    area.setWidgetResizable(True)
    area.enableTransparentBackground()
    host = QWidget()
    host.setStyleSheet("QWidget { background: transparent; }")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 6, 0)
    layout.setSpacing(12)
    area.setWidget(host)
    return area, layout


class PageView(QWidget):
    def __init__(self, object_name: str, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(32, 22, 32, 24)
        self._outer.setSpacing(16)

        self._header = QHBoxLayout()
        self._header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(TitleLabel(title))
        if subtitle:
            titles.addWidget(SubtitleLabel(subtitle))
        self._header.addLayout(titles)
        self._header.addStretch(1)
        self._outer.addLayout(self._header)

    def add_header_widget(self, widget: QWidget) -> None:
        self._header.addWidget(widget)

    def add_body(self, widget: QWidget, stretch: int = 0) -> None:
        self._outer.addWidget(widget, stretch)

    def add_body_layout(self, layout: QLayout, stretch: int = 0) -> None:
        self._outer.addLayout(layout, stretch)
