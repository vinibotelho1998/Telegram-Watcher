from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLayout, QLayoutItem, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, LineEdit, StrongBodyLabel, TransparentToolButton
from qfluentwidgets import FluentIcon

from .theme import PALETTE

_CHIP_STYLE = """
QWidget#chip {
    background: rgba(59, 130, 246, 0.16);
    border: 1px solid rgba(59, 130, 246, 0.35);
    border-radius: 12px;
}
"""

_BLOCK_CHIP_STYLE = """
QWidget#chip {
    background: rgba(239, 68, 68, 0.16);
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 12px;
}
"""


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(width, apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect.width(), apply=True, origin=(rect.x(), rect.y()))

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        from PyQt6.QtCore import QSize

        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, width: int, apply: bool, origin: tuple[int, int] = (0, 0)) -> int:
        from PyQt6.QtCore import QRect

        x, y = origin
        start_x = x
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x > start_x and x + hint.width() > start_x + width:
                x = start_x
                y += line_height + spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        return y + line_height - origin[1]


class WordChips(QWidget):
    changed = pyqtSignal()

    def __init__(
        self,
        title: str,
        hint: str,
        placeholder: str,
        blocking: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._words: list[str] = []
        self._blocking = blocking

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(StrongBodyLabel(title))
        hint_label = CaptionLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"color: {PALETTE['muted']};")
        layout.addWidget(hint_label)

        entry = QHBoxLayout()
        entry.setSpacing(6)
        self._input = LineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.setClearButtonEnabled(True)
        self._input.returnPressed.connect(self._commit)
        entry.addWidget(self._input, 1)
        add_btn = TransparentToolButton(FluentIcon.ADD)
        add_btn.setToolTip("Adicionar palavra")
        add_btn.clicked.connect(self._commit)
        entry.addWidget(add_btn)
        layout.addLayout(entry)

        self._chip_host = QWidget()
        self._chip_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._flow = FlowLayout(self._chip_host)
        layout.addWidget(self._chip_host)

        self._empty = CaptionLabel("Nenhuma palavra ainda")
        self._empty.setStyleSheet(f"color: {PALETTE['muted']};")
        layout.addWidget(self._empty)

    def words(self) -> list[str]:
        return list(self._words)

    def set_words(self, words: list[str]) -> None:
        self._words = []
        for word in words:
            value = " ".join(str(word).split()).strip(" ,")
            if value and value.lower() not in {w.lower() for w in self._words}:
                self._words.append(value)
        self._rebuild()

    def _commit(self) -> None:
        raw = self._input.text()
        added = False
        for chunk in raw.split(","):
            value = " ".join(chunk.split()).strip()
            if not value or value.lower() in {w.lower() for w in self._words}:
                continue
            self._words.append(value)
            added = True
        self._input.clear()
        if added:
            self._rebuild()
            self.changed.emit()

    def _remove(self, word: str) -> None:
        self._words = [w for w in self._words if w != word]
        self._rebuild()
        self.changed.emit()

    def _rebuild(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for word in self._words:
            self._flow.addWidget(self._chip(word))
        self._empty.setVisible(not self._words)
        self._chip_host.setVisible(bool(self._words))
        self._chip_host.updateGeometry()

    def _chip(self, word: str) -> QWidget:
        chip = QWidget()
        chip.setObjectName("chip")
        chip.setStyleSheet(_BLOCK_CHIP_STYLE if self._blocking else _CHIP_STYLE)
        row = QHBoxLayout(chip)
        row.setContentsMargins(10, 3, 4, 3)
        row.setSpacing(2)
        label = CaptionLabel(word)
        row.addWidget(label)
        close = TransparentToolButton(FluentIcon.CLOSE)
        close.setFixedSize(18, 18)
        close.setToolTip("Remover")
        close.clicked.connect(lambda _=False, value=word: self._remove(value))
        row.addWidget(close)
        return chip
