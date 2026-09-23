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
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SwitchButton,
)

from watcher import APP_NAME, AUTHOR, VERSION

from ..common import PageView, make_vscroll
from ..theme import PALETTE


class SettingsPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("settingsPage", "Configurações", parent=parent)
        self._app = app

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
        actions = QHBoxLayout()
        actions.setSpacing(10)
        save_btn = PrimaryPushButton("Salvar credenciais")
        save_btn.setIcon(FluentIcon.SAVE)
        save_btn.clicked.connect(self._save)
        actions.addWidget(save_btn)
        logout_btn = PushButton("Sair da conta do Telegram")
        logout_btn.setIcon(FluentIcon.CLOSE)
        logout_btn.clicked.connect(self._logout)
        actions.addWidget(logout_btn)
        actions.addStretch(1)
        cred_layout.addLayout(actions)
        cred_hint = CaptionLabel(
            "Trocar o telefone ou o API ID desconecta a conta atual: no próximo início o Telegram "
            "pede um código novo. Trocar o token do bot desconecta só o bot."
        )
        cred_hint.setWordWrap(True)
        cred_hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        cred_layout.addWidget(cred_hint)
        body.addWidget(cred)

        behavior, behavior_layout = self._card("Comportamento")
        self.sw_self = self._switch(
            behavior_layout,
            "Capturar mensagens enviadas por mim",
            "Para testar filtros mandando mensagem você mesmo. Elas são replicadas e podem disparar a Auto-Compra.",
        )
        self.sw_auto = self._switch(behavior_layout, "Iniciar monitor automaticamente", "Liga o monitor ao abrir o app")
        self.sw_startup = self._switch(
            behavior_layout, "Iniciar com o Windows", "Abre o app minimizado na bandeja ao ligar o computador"
        )
        self.sw_theme = self._switch(behavior_layout, "Tema escuro", "")
        body.addWidget(behavior)

        about, about_layout = self._card("Sobre")
        about_layout.addWidget(BodyLabel(f"{APP_NAME} — versão {VERSION}"))
        author_label = CaptionLabel(f"Desenvolvido por {AUTHOR}")
        author_label.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        about_layout.addWidget(author_label)
        body.addWidget(about)

        body.addStretch(1)

        self.load()
        self.sw_startup.checkedChanged.connect(app.set_windows_autostart)
        self.sw_theme.checkedChanged.connect(app.toggle_theme)
        self.sw_self.checkedChanged.connect(self._save_behavior)
        self.sw_auto.checkedChanged.connect(self._save_behavior)

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
            desc.setWordWrap(True)
            desc.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            texts.addWidget(desc)
        row.addLayout(texts, 1)
        switch = SwitchButton()
        switch.setOnText("Ligado")
        switch.setOffText("Desligado")
        row.addWidget(switch)
        parent_layout.addLayout(row)
        return switch

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not event.spontaneous():
            self._load_config()

    def load(self) -> None:
        self._load_config()
        self.sw_startup.setChecked(self._app.is_windows_autostart())
        self.sw_theme.setChecked(getattr(self._app.config, "dark_theme", True))

    def _load_config(self) -> None:
        config = self._app.config
        self.api_id.setText(str(config.api_id) if config.api_id else "")
        self.api_hash.setText(config.api_hash)
        self.bot_token.setText(config.bot_token)
        self.phone.setText(config.phone)
        for switch, value in ((self.sw_self, config.allow_self), (self.sw_auto, config.auto_start)):
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)

    def _save_behavior(self, _checked: bool = False) -> None:
        self._app.save_behavior(self.sw_self.isChecked(), self.sw_auto.isChecked())

    def _logout(self) -> None:
        self._app.logout_telegram()

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
