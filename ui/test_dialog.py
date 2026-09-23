from __future__ import annotations

from PyQt6.QtWidgets import QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    StrongBodyLabel,
    SubtitleLabel,
)

from watcher.autobuy import registry

from .theme import PALETTE

_CART = " Atenção: o robô remove os outros itens do seu carrinho nessa loja."

MODES = [
    (
        "Só consultar preço",
        "Abre a página do produto e lê preço e estoque. Não mexe no carrinho, não compra nada.",
        "muted",
    ),
    (
        "Simulação até a revisão",
        "Percorre o checkout inteiro, seleciona PIX e para antes de finalizar. Nenhum pedido é feito." + _CART,
        "muted",
    ),
    (
        "Ir até gerar o PIX",
        "COMPRA DE VERDADE: finaliza o pedido e traz o código PIX. Se você não pagar, o pedido expira e cancela sozinho."
        + _CART,
        "warning",
    ),
]

BUY_MODE = 2

_BUTTONS = ("Consultar preço", "Simular checkout", "Comprar e gerar PIX")

_INTROS = (
    "Abre a página do produto que você escolher e mostra o preço à vista e o estoque, sem comprar nada.",
    "Roda o fluxo de compra com o produto que você escolher, do mesmo jeito que numa promoção capturada, "
    "e para antes de finalizar.",
    "Roda o fluxo de compra de verdade com o produto que você escolher e finaliza o pedido.",
)


class TestDialog(MessageBoxBase):
    def __init__(
        self,
        store_id: str,
        url: str = "",
        mode: int = 0,
        coupon: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._spec = registry.get(store_id)
        label = self._spec.label if self._spec else store_id

        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel(f"Testar o fluxo na {label}"))
        self.intro = self._muted("")
        self.viewLayout.addWidget(self.intro)

        self.viewLayout.addWidget(StrongBodyLabel("Link do produto"))
        self.url_input = LineEdit()
        self.url_input.setPlaceholderText(self._placeholder())
        self.url_input.setText(url)
        self.url_input.setClearButtonEnabled(True)
        self.url_input.textChanged.connect(self._validate)
        self.viewLayout.addWidget(self.url_input)

        self.url_hint = CaptionLabel("")
        self.url_hint.setWordWrap(True)
        self.viewLayout.addWidget(self.url_hint)

        self.viewLayout.addWidget(StrongBodyLabel("Cupom (opcional)"))
        self.coupon_input = LineEdit()
        self.coupon_input.setPlaceholderText("ex: BEMVINDO10 — deixe vazio para não aplicar")
        self.coupon_input.setText(coupon)
        self.coupon_input.setClearButtonEnabled(True)
        self.viewLayout.addWidget(self.coupon_input)
        self.viewLayout.addWidget(self._muted(
            "Se preenchido, o robô tenta aplicar esse cupom no checkout e informa no resultado "
            "se a loja aceitou ou recusou."
        ))

        self.viewLayout.addWidget(StrongBodyLabel("Até onde ir"))
        self.mode_combo = ComboBox()
        self.mode_combo.addItems([name for name, _desc, _kind in MODES])
        self.mode_combo.setCurrentIndex(mode)
        self.mode_combo.currentIndexChanged.connect(self._describe)
        self.viewLayout.addWidget(self.mode_combo)

        self.mode_hint = BodyLabel("")
        self.mode_hint.setWordWrap(True)
        self.viewLayout.addWidget(self.mode_hint)

        self.cancelButton.setText("Cancelar")
        self.widget.setMinimumWidth(560)
        self._describe()
        self._validate()

    def _placeholder(self) -> str:
        if self._spec is None:
            return "https://..."
        exemplos = {
            "kabum": "https://www.kabum.com.br/produto/123456/nome-do-produto",
            "amazon": "https://www.amazon.com.br/dp/B0XXXXXXXX",
        }
        return exemplos.get(self._spec.id, "https://...")

    @staticmethod
    def _muted(text: str) -> CaptionLabel:
        label = CaptionLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE['muted']};")
        return label

    def _describe(self) -> None:
        index = self.mode_combo.currentIndex()
        _name, desc, kind = MODES[index]
        self.mode_hint.setText(desc)
        weight = "600" if kind == "warning" else "400"
        self.mode_hint.setStyleSheet(f"color: {PALETTE[kind]}; font-weight: {weight};")
        self.intro.setText(_INTROS[index])
        self.yesButton.setText(_BUTTONS[index])

    def _validate(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.url_hint.setText("")
            self.yesButton.setEnabled(False)
            return
        found = registry.match_url(url)
        alvo = self._spec.id if self._spec else ""
        if found is None:
            self.url_hint.setText("Link não reconhecido por nenhuma loja suportada.")
            self.url_hint.setStyleSheet(f"color: {PALETTE['danger']};")
            self.yesButton.setEnabled(False)
            return
        if found.id != alvo:
            nome = self._spec.label if self._spec else alvo
            self.url_hint.setText(f"Esse link é da {found.label}, mas este teste é da {nome}.")
            self.url_hint.setStyleSheet(f"color: {PALETTE['danger']};")
            self.yesButton.setEnabled(False)
            return
        self.url_hint.setText(f"Link válido da {found.label}.")
        self.url_hint.setStyleSheet(f"color: {PALETTE['success']};")
        self.yesButton.setEnabled(True)

    def result_url(self) -> str:
        return self.url_input.text().strip()

    def result_mode(self) -> int:
        return self.mode_combo.currentIndex()

    def result_coupon(self) -> str:
        return self.coupon_input.text().strip().upper()
