from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    HyperlinkButton,
    IndeterminateProgressBar,
    LineEdit,
    MessageBoxBase,
    PasswordLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from monitor.models import EngineState

from .theme import PALETTE

_STEP_COUNT = 4
_CONNECT_STEP = 3


def _bullet(number: int) -> QLabel:
    dot = QLabel(str(number))
    dot.setFixedSize(24, 24)
    dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dot.setStyleSheet(
        f"background: {PALETTE['info']}; color: white; border-radius: 12px; font-weight: 600;"
    )
    return dot


def _step_row(number: int, text: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(_bullet(number), 0, Qt.AlignmentFlag.AlignTop)
    label = BodyLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    return row


class SetupWizard(MessageBoxBase):
    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._index = 0
        self._connected = False
        self._connecting = False

        self.viewLayout.setSpacing(16)
        self._build_header()
        self._build_stack()

        self._hint = CaptionLabel("")
        self._hint.setWordWrap(True)
        self._hint.setTextColor(QColor(PALETTE["danger"]), QColor(PALETTE["danger"]))
        self.viewLayout.addWidget(self._hint)

        self.widget.setMinimumWidth(560)
        self._wire_buttons()
        self._go_to(0)

    def _build_header(self) -> None:
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        dots_row = QHBoxLayout()
        dots_row.setSpacing(6)
        dots_row.setContentsMargins(0, 0, 0, 0)
        self._dots: list[QLabel] = []
        for _ in range(_STEP_COUNT):
            dot = QLabel()
            dot.setFixedHeight(6)
            self._dots.append(dot)
            dots_row.addWidget(dot)
        dots_row.addStretch(1)
        layout.addLayout(dots_row)

        self._counter = CaptionLabel("")
        self._counter.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        layout.addWidget(self._counter)
        self.viewLayout.addWidget(header)

    def _build_stack(self) -> None:
        self._stack = QStackedWidget()
        self._stack.setMinimumHeight(320)
        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_api())
        self._stack.addWidget(self._page_phone())
        self._stack.addWidget(self._page_connect())
        self.viewLayout.addWidget(self._stack)

    def _page_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(TitleLabel("Vamos configurar sua conta"))
        intro = BodyLabel(
            "Em poucos passos seu monitor estará conectado ao Telegram. "
            "Você vai precisar de três coisas, e eu te mostro onde achar cada uma."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(6)
        layout.addWidget(_step_row(1, "API ID e API Hash, obtidos no site my.telegram.org."))
        layout.addWidget(_step_row(2, "Seu número de telefone do Telegram, com o código do país."))
        layout.addWidget(_step_row(3, "Um bot é opcional e ajuda a enviar as mensagens replicadas."))
        layout.addStretch(1)
        return page

    def _page_api(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel("Credenciais da API"))
        layout.addWidget(_step_row(1, "Acesse my.telegram.org e entre com o seu número."))
        layout.addWidget(_step_row(2, "Abra a opção API development tools."))
        layout.addWidget(_step_row(3, "Crie um app com qualquer título. A plataforma pode ser Desktop."))
        layout.addWidget(_step_row(4, "Copie os valores api_id e api_hash que aparecem."))

        link = HyperlinkButton("https://my.telegram.org", "Abrir my.telegram.org")
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(4)

        layout.addWidget(CaptionLabel("API ID"))
        self.api_id = LineEdit()
        self.api_id.setPlaceholderText("Ex: 12345678")
        layout.addWidget(self.api_id)

        layout.addWidget(CaptionLabel("API Hash"))
        self.api_hash = PasswordLineEdit()
        self.api_hash.setPlaceholderText("Cole o api_hash aqui")
        layout.addWidget(self.api_hash)
        layout.addStretch(1)
        return page

    def _page_phone(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel("Telefone e bot"))

        layout.addWidget(CaptionLabel("Telefone (com código do país)"))
        self.phone = LineEdit()
        self.phone.setPlaceholderText("Ex: +55 11 99999-9999")
        layout.addWidget(self.phone)
        hint = CaptionLabel("É o mesmo número da sua conta do Telegram.")
        hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        layout.addWidget(hint)

        layout.addSpacing(10)
        layout.addWidget(StrongBodyLabel("Bot do Telegram (opcional)"))
        bot_info = BodyLabel(
            "No Telegram, abra o @BotFather, envie /newbot e siga as instruções. "
            "Depois cole o token gerado abaixo."
        )
        bot_info.setWordWrap(True)
        layout.addWidget(bot_info)
        link = HyperlinkButton("https://t.me/botfather", "Abrir @BotFather")
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)

        self.bot_token = PasswordLineEdit()
        self.bot_token.setPlaceholderText("Cole o token do bot (opcional)")
        layout.addWidget(self.bot_token)
        layout.addStretch(1)
        return page

    def _page_connect(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._connect_title = TitleLabel("Tudo pronto para conectar")
        layout.addWidget(self._connect_title)
        self._connect_text = BodyLabel(
            "Clique em Conectar ao Telegram. Você vai receber um código de verificação "
            "no app do Telegram e digitá-lo na janela seguinte."
        )
        self._connect_text.setWordWrap(True)
        layout.addWidget(self._connect_text)

        self._progress = IndeterminateProgressBar()
        self._progress.stop()
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status = BodyLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch(1)
        return page

    def _wire_buttons(self) -> None:
        for button in (self.yesButton, self.cancelButton):
            try:
                button.clicked.disconnect()
            except TypeError:
                pass
        self.yesButton.clicked.connect(self._on_next)
        self.cancelButton.clicked.connect(self._on_back)

    def _go_to(self, index: int) -> None:
        self._index = index
        self._stack.setCurrentIndex(index)
        self._hint.setText("")
        self._counter.setText(f"Passo {index + 1} de {_STEP_COUNT}")
        for position, dot in enumerate(self._dots):
            active = position == index
            done = position < index
            color = PALETTE["info"] if (active or done) else "rgba(255,255,255,0.12)"
            dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
        self._update_buttons()

    def _update_buttons(self) -> None:
        if self._connected:
            self.yesButton.setText("Concluir")
            self.cancelButton.hide()
            return
        self.cancelButton.show()
        self.cancelButton.setText("Cancelar" if self._index == 0 else "Voltar")
        if self._index == 0:
            self.yesButton.setText("Começar")
        elif self._index == _CONNECT_STEP:
            self.yesButton.setText("Conectar ao Telegram")
        else:
            self.yesButton.setText("Próximo")

    def _on_back(self) -> None:
        if self._connecting:
            return
        if self._connected:
            self.accept()
            return
        if self._index == 0:
            self.reject()
            return
        self._go_to(self._index - 1)

    def _on_next(self) -> None:
        if self._connecting:
            return
        if self._connected:
            self.accept()
            return
        if self._index < _CONNECT_STEP:
            if self._validate():
                self._go_to(self._index + 1)
            return
        self._start_connection()

    def _validate(self) -> bool:
        if self._index == 1:
            if not self.api_id.text().strip().isdigit():
                return self._fail("Informe o API ID (somente números).")
            if not self.api_hash.text().strip():
                return self._fail("Informe o API Hash.")
        if self._index == 2:
            if len(self.phone.text().strip()) < 8:
                return self._fail("Informe um telefone válido com o código do país.")
        return True

    def _fail(self, message: str) -> bool:
        self._hint.setText(message)
        return False

    def _start_connection(self) -> None:
        self._save_config()
        self._set_connecting(True)
        self._app.schedule(self._connection_task())

    def _save_config(self) -> None:
        self._app.save_settings(
            {
                "api_id": self.api_id.text(),
                "api_hash": self.api_hash.text(),
                "bot_token": self.bot_token.text(),
                "phone": self.phone.text(),
                "allow_self": self._app.config.allow_self,
                "auto_start": self._app.config.auto_start,
            },
            silent=True,
        )

    async def _connection_task(self) -> None:
        ok = False
        try:
            ok = await self._app.engine.start()
        except Exception:
            ok = False
        self._set_connecting(False)
        if ok and self._app.engine.state == EngineState.ONLINE:
            self._set_connected()
        else:
            self._hint.setText("Não foi possível conectar. Confira os dados e tente novamente.")

    def _set_connecting(self, value: bool) -> None:
        self._connecting = value
        self._hint.setText("")
        self.yesButton.setDisabled(value)
        self.cancelButton.setDisabled(value)
        if value:
            self._progress.show()
            self._progress.start()
            self._status.setText("Conectando ao Telegram...")
        else:
            self._progress.stop()
            self._progress.hide()
            self._status.setText("")

    def _set_connected(self) -> None:
        self._connected = True
        self._connect_title.setText("Conta conectada")
        self._connect_text.setText(
            "Pronto. Agora abra a aba Canais para sincronizar e escolher origem e destino, "
            "depois crie filtros para definir o que será replicado."
        )
        self._status.setText("")
        self.cancelButton.setDisabled(False)
        self.yesButton.setDisabled(False)
        self._update_buttons()
