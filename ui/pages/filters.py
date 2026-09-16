from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TransparentToolButton,
)

from watcher.detector import STORES
from watcher.models import Filter

from ..common import PageView, clear_layout, make_vscroll
from ..theme import PALETTE

_SMART_NAMES = {
    "any_coupon": "cupom",
    "free_shipping": "frete grátis",
    "cashback": "cashback",
    "flash": "relâmpago",
    "new_user": "novo usuário",
    "discount>=30": "30%+",
}


def _smart_label(rules: list[str]) -> str:
    labels = []
    for rule in rules:
        if rule.startswith("store:"):
            store = STORES.get(rule.split(":", 1)[1])
            labels.append(store["label"][0] if store else "loja")
        else:
            labels.append(_SMART_NAMES.get(rule, rule))
    return ", ".join(labels)


def _summary(flt: Filter) -> str:
    parts = [
        f"{len(flt.source_ids)} origem(ns)" if flt.source_ids else "todas as origens",
        f"{len(flt.destination_ids)} destino(s)" if flt.destination_ids else "destino padrão",
    ]
    if flt.include:
        parts.append(f"{len(flt.include)} palavra(s)")
    if flt.smart_rules:
        parts.append(_smart_label(flt.smart_rules))
    if flt.exclude:
        parts.append(f"exclui {len(flt.exclude)}")
    return "   ·   ".join(parts)


class _FilterCard(CardWidget):
    def __init__(self, flt: Filter, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flt = flt
        self._app = app
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 14, 12)
        layout.setSpacing(14)

        self.switch = SwitchButton()
        self.switch.setChecked(flt.enabled)
        self.switch.checkedChanged.connect(lambda checked: app.toggle_filter(flt.id, checked))
        layout.addWidget(self.switch)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = StrongBodyLabel(flt.name)
        if not flt.enabled:
            name.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        info.addWidget(name)
        summary = CaptionLabel(_summary(flt))
        summary.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        info.addWidget(summary)
        layout.addLayout(info, 1)

        edit = TransparentToolButton(FluentIcon.EDIT)
        edit.clicked.connect(lambda: app.open_filter_editor(flt))
        delete = TransparentToolButton(FluentIcon.DELETE)
        delete.clicked.connect(lambda: app.delete_filter(flt.id))
        layout.addWidget(edit)
        layout.addWidget(delete)


class FiltersPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("filtersPage", "Filtros", parent=parent)
        self._app = app

        new_btn = PrimaryPushButton("Novo filtro")
        new_btn.setIcon(FluentIcon.ADD)
        new_btn.clicked.connect(lambda: app.open_filter_editor(None))
        self.add_header_widget(new_btn)

        area, self._list = make_vscroll()
        self.add_body(area, 1)
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self._list)
        if not self._app.filters:
            empty = QWidget()
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(0, 80, 0, 0)
            layout.setSpacing(6)
            title = SubtitleLabel("Nenhum filtro ainda")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint = CaptionLabel("Crie um filtro para escolher o que será replicado.")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            layout.addWidget(title)
            layout.addWidget(hint)
            self._list.addWidget(empty)
            self._list.addStretch(1)
            return
        for flt in self._app.filters:
            self._list.addWidget(_FilterCard(flt, self._app))
        self._list.addStretch(1)
