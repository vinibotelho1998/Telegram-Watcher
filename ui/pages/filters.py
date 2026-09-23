from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
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

REPLICA_TUDO = "Nenhum filtro ativo: tudo o que chega nas origens é replicado para os destinos padrão."


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


def _catch_all_note(flt: Filter) -> str:
    if any(w.strip() for w in flt.include) or flt.smart_rules:
        return ""
    if any(w.strip() for w in flt.exclude):
        return "Só bloqueia essas palavras em todos os filtros, não replica nada sozinho"
    return "Replica tudo o que chega nas origens dele"


def _banner(text: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("warningBanner")
    frame.setStyleSheet(
        "QFrame#warningBanner {"
        " background: rgba(245, 158, 11, 0.14);"
        " border: 1px solid rgba(245, 158, 11, 0.45);"
        " border-radius: 8px;"
        " }"
    )
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 10, 16, 10)
    label = BodyLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    return frame


class _FilterCard(CardWidget):
    def __init__(self, flt: Filter, page: "FiltersPage", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flt = flt
        app = page._app
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 14, 12)
        layout.setSpacing(14)

        self.switch = SwitchButton()
        self.switch.setOnText("Ligado")
        self.switch.setOffText("Desligado")
        self.switch.setChecked(flt.enabled)
        self.switch.checkedChanged.connect(lambda checked: page.set_enabled(self, flt, checked))
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
        note = _catch_all_note(flt)
        if note:
            aviso = CaptionLabel(note)
            aviso.setTextColor(QColor(PALETTE["warning"]), QColor(PALETTE["warning"]))
            info.addWidget(aviso)
        layout.addLayout(info, 1)

        edit = TransparentToolButton(FluentIcon.EDIT)
        edit.setToolTip("Editar filtro")
        edit.clicked.connect(lambda: app.open_filter_editor(flt))
        delete = TransparentToolButton(FluentIcon.DELETE)
        delete.setToolTip("Excluir filtro")
        delete.clicked.connect(lambda: app.delete_filter(flt.id))
        layout.addWidget(edit)
        layout.addWidget(delete)

    def restore(self, checked: bool) -> None:
        self.switch.blockSignals(True)
        self.switch.setChecked(checked)
        self.switch.blockSignals(False)


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

    def _replicates_everything(self, filters: list[Filter]) -> bool:
        return bool(self._app.config.destination_ids) and not any(f.enabled for f in filters)

    def set_enabled(self, card: _FilterCard, flt: Filter, checked: bool) -> None:
        if not checked:
            restantes = [f for f in self._app.filters if f.enabled and f.id != flt.id]
            if not restantes and self._app.config.destination_ids:
                pergunta = (
                    f"Desligar o filtro '{flt.name}'? Sem nenhum filtro ligado, tudo o que chega nas "
                    "origens passa a ser replicado para os destinos padrão."
                )
                if not self._app.confirm("Desligar o último filtro", pergunta, "Desligar mesmo assim"):
                    card.restore(True)
                    return
        self._app.toggle_filter(flt.id, checked)
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        clear_layout(self._list)
        if self._replicates_everything(self._app.filters):
            self._list.addWidget(_banner(REPLICA_TUDO))
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
            self._list.addWidget(_FilterCard(flt, self))
        self._list.addStretch(1)
