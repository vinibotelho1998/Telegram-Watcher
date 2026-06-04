from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SwitchButton,
)

from ..common import PageView, make_vscroll
from ..theme import PALETTE


class SettingsPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("settingsPage", "Configurações", parent=parent)
        self._app = app

        save_btn = PrimaryPushButton("Salvar")
        save_btn.setIcon(FluentIcon.SAVE)
        save_btn.clicked.connect(self._save)
        self.add_header_widget(save_btn)

        area, body = make_vscroll()
        self.add_body(area, 1)

        cred, cred_layout = self._card("Credenciais da API")
        self.api_id = LineEdit()
        self.api_id.setPlaceholderText("12345678")
        self.api_hash = PasswordLineEdit()
        self.bot_token = PasswordLineEdit()
        self.phone = LineEdit()
        self.phone.setPlaceholderText("+55 11 90000-0000")
        cred_layout.addLayout(self._field("API ID", self.api_id, "Obtenha em my.telegram.org"))
        cred_layout.addLayout(self._field("API Hash", self.api_hash, "my.telegram.org"))
        cred_layout.addLayout(self._field("Bot Token", self.bot_token, "Opcional — criado no @BotFather"))
        cred_layout.addLayout(self._field("Telefone", self.phone, "Com código do país"))
        body.addWidget(cred)

        behavior, behavior_layout = self._card("Comportamento")
        self.sw_self = self._switch(behavior_layout, "Modo de teste", "Captura mensagens enviadas por você")
        self.sw_auto = self._switch(behavior_layout, "Iniciar monitor automaticamente", "Liga o monitor ao abrir o app")
        self.sw_startup = self._switch(behavior_layout, "Iniciar com o Windows", "Abre o app ao ligar o computador")
        self.sw_theme = self._switch(behavior_layout, "Tema escuro", "")
        body.addWidget(behavior)
        body.addStretch(1)

        self.load()
        self.sw_startup.checkedChanged.connect(app.set_windows_autostart)
        self.sw_theme.checkedChanged.connect(app.toggle_theme)

    def _card(self, title: str) -> tuple[SimpleCardWidget, QVBoxLayout]:
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(14)
        layout.addWidget(StrongBodyLabel(title))
        return card, layout

    def _field(self, label: str, widget: QWidget, hint: str = "") -> QLayout:
        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(CaptionLabel(label))
        column.addWidget(widget)
        if hint:
            hint_label = CaptionLabel(hint)
            hint_label.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            column.addWidget(hint_label)
        return column

    def _switch(self, parent_layout: QVBoxLayout, label: str, description: str) -> SwitchButton:
        row = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(BodyLabel(label))
        if description:
            desc = CaptionLabel(description)
            desc.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            texts.addWidget(desc)
        row.addLayout(texts)
        row.addStretch(1)
        switch = SwitchButton()
        row.addWidget(switch)
        parent_layout.addLayout(row)
        return switch

    def load(self) -> None:
        config = self._app.config
        self.api_id.setText(str(config.api_id) if config.api_id else "")
        self.api_hash.setText(config.api_hash)
        self.bot_token.setText(config.bot_token)
        self.phone.setText(config.phone)
        self.sw_self.setChecked(config.allow_self)
        self.sw_auto.setChecked(config.auto_start)
        self.sw_startup.setChecked(self._app.is_windows_autostart())
        self.sw_theme.setChecked(True)

    def _save(self) -> None:
        self._app.save_settings(
            {
                "api_id": self.api_id.text(),
                "api_hash": self.api_hash.text(),
                "bot_token": self.bot_token.text(),
                "phone": self.phone.text(),
                "allow_self": self.sw_self.isChecked(),
                "auto_start": self.sw_auto.isChecked(),
            }
        )
