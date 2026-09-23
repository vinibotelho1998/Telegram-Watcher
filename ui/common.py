from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    SingleDirectionScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)


def plain(label: QLabel) -> QLabel:
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def format_int(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def format_brl(value: float) -> str:
    texto = f"{float(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


class _Elide:
    def _setup_elide(self, text: str) -> None:
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = " ".join((text or "").split())
        self.setToolTip(self._full)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._elide()

    def _elide(self) -> None:
        largura = self.width() - 2
        texto = self._full
        if largura > 0:
            texto = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, largura)
        QLabel.setText(self, texto)
        self.setToolTip(self._full if texto != self._full else "")

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self._full) + 4)
        return hint

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(24)
        return hint


def _elided(base):
    class Elided(_Elide, base):
        def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
            base.__init__(self, parent)
            self._setup_elide(text)

    Elided.__name__ = f"Elided{base.__name__}"
    return Elided


ElidedBody = _elided(BodyLabel)
ElidedStrong = _elided(StrongBodyLabel)
ElidedCaption = _elided(CaptionLabel)
ElidedSubtitle = _elided(SubtitleLabel)


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
