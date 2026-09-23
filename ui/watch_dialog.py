from __future__ import annotations

import re

from PyQt6.QtWidgets import QGridLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    SingleDirectionScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
)

from watcher.autobuy import registry
from watcher.autobuy.criteria import Criteria
from watcher.autobuy.models import WatchItem
from watcher.autobuy.resolver import extract_urls

from .chips import WordChips
from .common import format_brl
from .theme import PALETTE

_EXAMPLE = (
    "🔥 BUG DE PREÇO 🔥\n"
    "SSD Kingston NV2 2TB NVMe\n"
    "De R$ 899 por apenas R$ 289,90 à vista no PIX\n"
    "https://www.kabum.com.br/produto/123456/ssd-kingston-nv2-2tb"
)

_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")


def parse_price(text: str) -> float:
    clean = text.replace("R$", "").strip()
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif _THOUSANDS.fullmatch(clean):
        clean = clean.replace(".", "")
    try:
        return float(clean)
    except ValueError:
        return 0.0


def format_price(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") if value > 0 else ""


class WatchDialog(MessageBoxBase):
    def __init__(self, existing: WatchItem | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._existing = existing
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel("Editar produto" if existing else "Novo produto vigiado"))

        scroll = SingleDirectionScrollArea()
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        altura = 520
        if parent is not None and parent.height() > 0:
            altura = min(altura, max(240, parent.height() - 230))
        scroll.setMinimumHeight(altura)
        host = QWidget()
        host.setStyleSheet("QWidget { background: transparent; }")
        body = QVBoxLayout(host)
        body.setContentsMargins(2, 2, 12, 2)
        body.setSpacing(16)
        scroll.setWidget(host)
        self.viewLayout.addWidget(scroll)

        self.name_input = LineEdit()
        self.name_input.setPlaceholderText("ex: SSD 2TB barato")
        body.addWidget(StrongBodyLabel("Nome do produto vigiado"))
        body.addWidget(self.name_input)

        self.all_chips = WordChips(
            "Precisa ter TODAS estas palavras",
            "A mensagem só serve se tiver todas. Ex: ssd e 2tb — aí só entra anúncio de SSD de 2TB.",
            "digite uma palavra e aperte Enter",
        )
        body.addWidget(self.all_chips)

        self.any_chips = WordChips(
            "E pelo menos UMA destas (opcional)",
            "Basta uma delas aparecer. Use para os apelidos da mesma coisa. Ex: bug, erro de preço, precinho.",
            "digite uma palavra e aperte Enter",
        )
        body.addWidget(self.any_chips)

        self.none_chips = WordChips(
            "Nunca comprar se aparecer",
            "Se qualquer uma destas aparecer, o robô ignora a mensagem. Ex: usado, recondicionado, sata.",
            "digite uma palavra e aperte Enter",
            blocking=True,
        )
        body.addWidget(self.none_chips)

        body.addWidget(self._price_section())
        body.addWidget(self._test_section())

        self.missing = CaptionLabel("")
        self.missing.setWordWrap(True)
        self.missing.setStyleSheet(f"color: {PALETTE['danger']};")
        self.viewLayout.addWidget(self.missing)

        self._load(existing)
        self.yesButton.setText("Salvar")
        self.cancelButton.setText("Cancelar")
        for chips in (self.all_chips, self.any_chips, self.none_chips):
            chips.changed.connect(self._on_change)
        self.name_input.textChanged.connect(self._validate)
        self.max_input.textChanged.connect(self._on_change)
        self.min_input.textChanged.connect(self._on_change)
        self.store_combo.currentIndexChanged.connect(self._on_change)
        self._on_change()
        self.widget.setMinimumWidth(620)

    def _price_section(self) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)

        grid.addWidget(StrongBodyLabel("Preço máximo à vista (R$)"), 0, 0)
        self.max_input = LineEdit()
        self.max_input.setPlaceholderText("ex: 350")
        grid.addWidget(self.max_input, 1, 0)
        grid.addWidget(self._muted("Acima disso o robô não compra."), 2, 0)

        grid.addWidget(StrongBodyLabel("Preço mínimo (R$)"), 0, 1)
        self.min_input = LineEdit()
        self.min_input.setPlaceholderText("ex: 80 (opcional)")
        grid.addWidget(self.min_input, 1, 1)
        grid.addWidget(self._muted("Proteção: abaixo disso é anúncio suspeito."), 2, 1)

        grid.addWidget(StrongBodyLabel("Quantidade"), 0, 2)
        self.qty_spin = SpinBox()
        self.qty_spin.setRange(1, 10)
        grid.addWidget(self.qty_spin, 1, 2)
        grid.addWidget(self._muted("Unidades por compra."), 2, 2)

        grid.addWidget(StrongBodyLabel("Loja"), 3, 0)
        self.store_combo = ComboBox()
        self.store_combo.addItem("Qualquer loja suportada")
        self._store_ids = [""]
        for store_id, label in registry.labels().items():
            self.store_combo.addItem(label)
            self._store_ids.append(store_id)
        grid.addWidget(self.store_combo, 4, 0)
        grid.addWidget(self._muted("Só compra se o link for dessa loja."), 5, 0)

        self.disable_check = CheckBox("Desligar este produto depois que gerar um PIX")
        grid.addWidget(self.disable_check, 6, 0, 1, 3)
        return box

    def _test_section(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(StrongBodyLabel("Testar com uma mensagem"))
        layout.addWidget(self._muted("Cole aqui uma promoção de verdade e veja na hora se o robô compraria."))
        self.test_input = PlainTextEdit()
        self.test_input.setPlaceholderText(_EXAMPLE)
        self.test_input.setFixedHeight(110)
        self.test_input.textChanged.connect(self._on_change)
        layout.addWidget(self.test_input)
        self.test_result = BodyLabel("Cole uma mensagem para testar")
        self.test_result.setWordWrap(True)
        self.test_result.setStyleSheet(f"color: {PALETTE['muted']};")
        layout.addWidget(self.test_result)
        self.test_detail = CaptionLabel("")
        self.test_detail.setWordWrap(True)
        self.test_detail.setStyleSheet(f"color: {PALETTE['muted']};")
        layout.addWidget(self.test_detail)
        return box

    @staticmethod
    def _muted(text: str) -> CaptionLabel:
        label = CaptionLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE['muted']};")
        return label

    def criteria(self) -> Criteria:
        return Criteria(
            all_words=self.all_chips.words(),
            any_words=self.any_chips.words(),
            none_words=self.none_chips.words(),
        )

    def _on_change(self) -> None:
        self._validate()
        self._run_preview()

    def _problems(self) -> list[str]:
        faltando: list[str] = []
        if not self.name_input.text().strip():
            faltando.append("dê um nome ao produto")
        if self.criteria().is_empty():
            faltando.append("coloque ao menos uma palavra em TODAS ou em UMA destas")
        maximo = parse_price(self.max_input.text())
        minimo = parse_price(self.min_input.text())
        if maximo <= 0:
            faltando.append("informe o preço máximo")
        elif minimo >= maximo:
            faltando.append("o preço mínimo precisa ser menor que o máximo")
        return faltando

    def _validate(self) -> None:
        faltando = self._problems()
        self.yesButton.setEnabled(not faltando)
        self.missing.setText(f"Para salvar: {'; '.join(faltando)}." if faltando else "")

    def _price_window(self) -> str:
        maximo = parse_price(self.max_input.text())
        minimo = parse_price(self.min_input.text())
        if maximo <= 0:
            return "O robô abre o link, mas falta definir o preço máximo"
        if minimo > 0:
            return f"O robô abre o link e só compra se o preço à vista estiver entre {format_brl(minimo)} e {format_brl(maximo)}"
        return f"O robô abre o link e só compra se o preço à vista for até {format_brl(maximo)}"

    def _link_problem(self, text: str) -> tuple[str, str]:
        if not extract_urls(text):
            return "danger", "Sem link de loja: o robô ignoraria esta mensagem"
        links = registry.direct_products(text)
        loja = self._store_ids[self.store_combo.currentIndex()]
        if not links:
            return "warning", "O link não é de uma loja suportada. Se for encurtador, o robô abre para descobrir a loja"
        if loja and not any(spec.id == loja for spec, _url in links):
            spec = registry.get(loja)
            outras = ", ".join(dict.fromkeys(s.label for s, _url in links))
            return "danger", f"O link é da {outras}, mas este produto só compra na {spec.label if spec else loja}"
        return "", ""

    def _run_preview(self) -> None:
        text = self.test_input.toPlainText().strip()
        if not text:
            self.test_result.setText("Cole uma mensagem para testar")
            self.test_result.setStyleSheet(f"color: {PALETTE['muted']};")
            self.test_detail.setText(self.criteria().summary())
            return
        report = self.criteria().check(text)
        tom, motivo = ("", "")
        if report.matched:
            tom, motivo = self._link_problem(text)
        if not report.matched:
            color, mark, reason = PALETTE["danger"], "✘", report.reason
        elif tom == "danger":
            color, mark, reason = PALETTE["danger"], "✘", f"As palavras batem, mas: {motivo}"
        elif tom == "warning":
            color, mark, reason = PALETTE["warning"], "?", f"As palavras batem. {motivo}"
        else:
            color, mark, reason = PALETTE["success"], "✔", f"As palavras batem. {self._price_window()}."
        self.test_result.setText(f"{mark}  {reason}")
        self.test_result.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.test_detail.setText(self._detail(report))

    @staticmethod
    def _detail(report) -> str:
        parts: list[str] = []
        if report.all_checks:
            marks = ", ".join(f"{'✔' if c.hit else '✘'} {c.word}" for c in report.all_checks)
            parts.append(f"todas: {marks}")
        if report.any_checks:
            marks = ", ".join(f"{'✔' if c.hit else '✘'} {c.word}" for c in report.any_checks)
            parts.append(f"alguma: {marks}")
        hits = [c.word for c in report.none_checks if c.hit]
        if hits:
            parts.append(f"proibidas encontradas: {', '.join(hits)}")
        return "      ".join(parts)

    def _load(self, existing: WatchItem | None) -> None:
        if not existing:
            self.qty_spin.setValue(1)
            self.disable_check.setChecked(True)
            self.store_combo.setCurrentIndex(0)
            return
        if existing.store in self._store_ids:
            self.store_combo.setCurrentIndex(self._store_ids.index(existing.store))
        criteria = existing.criteria()
        self.name_input.setText(existing.name)
        self.all_chips.set_words(criteria.all_words)
        self.any_chips.set_words(criteria.any_words)
        self.none_chips.set_words(criteria.none_words)
        self.max_input.setText(format_price(existing.max_price))
        self.min_input.setText(format_price(existing.min_price))
        self.qty_spin.setValue(max(1, existing.quantity))
        self.disable_check.setChecked(existing.disable_after_buy)

    def result_item(self) -> WatchItem:
        base = self._existing or WatchItem.new()
        criteria = self.criteria()
        return WatchItem(
            id=base.id,
            name=self.name_input.text().strip() or "Sem nome",
            max_price=parse_price(self.max_input.text()),
            include=criteria.to_include(),
            exclude=criteria.to_exclude(),
            quantity=self.qty_spin.value(),
            enabled=base.enabled,
            store=self._store_ids[self.store_combo.currentIndex()],
            min_price=parse_price(self.min_input.text()),
            disable_after_buy=self.disable_check.isChecked(),
            all_words=criteria.all_words,
            any_words=criteria.any_words,
        )
