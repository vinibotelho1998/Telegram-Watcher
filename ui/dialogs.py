from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    SingleDirectionScrollArea,
    SpinBox,
    SubtitleLabel,
)

from monitor.config import AppConfig
from monitor.detector import STORES
from monitor.models import Filter

_SMART_PRESETS: list[tuple[str, str]] = [
    ("Qualquer cupom", "any_coupon"),
    ("Frete grátis", "free_shipping"),
    ("Cashback", "cashback"),
    ("Oferta relâmpago", "flash"),
    ("Novo usuário", "new_user"),
]


class CodeDialog(MessageBoxBase):
    def __init__(self, phone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("Código de verificação")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(BodyLabel(f"Enviamos um código para {phone}. Digite abaixo."))

        self.input = LineEdit()
        self.input.setPlaceholderText("Ex: 1 2 3 4 5")
        self.input.setClearButtonEnabled(True)
        self.viewLayout.addWidget(self.input)

        self.yesButton.setText("Confirmar")
        self.cancelButton.setText("Cancelar")
        self.yesButton.setDisabled(True)
        self.input.textChanged.connect(lambda text: self.yesButton.setEnabled(bool(text.strip())))
        self.widget.setMinimumWidth(380)

    def code(self) -> str:
        return self.input.text().strip()


class PasswordDialog(MessageBoxBase):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel("Verificação em duas etapas"))
        self.viewLayout.addWidget(BodyLabel("Digite a senha da sua conta do Telegram."))
        self.input = LineEdit()
        self.input.setEchoMode(LineEdit.EchoMode.Password)
        self.input.setPlaceholderText("Senha")
        self.viewLayout.addWidget(self.input)
        self.yesButton.setText("Entrar")
        self.cancelButton.setText("Cancelar")
        self.widget.setMinimumWidth(380)

    def password(self) -> str:
        return self.input.text()


class FilterDialog(MessageBoxBase):
    _INCLUDE_ALL = "Incluir se conter (vazio = replica tudo; separe por vírgula, + exige junção)"
    _INCLUDE_SMART = "Incluir se conter (vazio = filtra pela detecção inteligente abaixo)"

    def __init__(self, config: AppConfig, existing: Filter | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._existing = existing
        self._source_checks: dict[int, CheckBox] = {}
        self._dest_checks: dict[int, CheckBox] = {}
        self._smart_checks: dict[str, CheckBox] = {}

        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel("Editar filtro" if existing else "Novo filtro"))

        self.name_input = LineEdit()
        self.name_input.setPlaceholderText("Nome do filtro")
        self._add_field("Nome", self.name_input)

        self.include_input = LineEdit()
        self.include_input.setPlaceholderText("ex: cupom + mercado livre ou meli")
        self.include_caption = CaptionLabel(self._INCLUDE_ALL)
        self.viewLayout.addWidget(self.include_caption)
        self.viewLayout.addWidget(self.include_input)

        self.exclude_input = LineEdit()
        self.exclude_input.setPlaceholderText("esgotado, encerrado")
        self._add_field("Ignorar se conter (bloqueia em todos os filtros)", self.exclude_input)

        self._build_smart_section()
        self._build_channels_section()
        self._load(existing)
        self._connect_smart_hint()

        self.yesButton.setText("Salvar")
        self.cancelButton.setText("Cancelar")
        self.widget.setMinimumWidth(560)

    def _add_field(self, label: str, widget: QWidget) -> None:
        caption = CaptionLabel(label)
        self.viewLayout.addWidget(caption)
        self.viewLayout.addWidget(widget)

    def _connect_smart_hint(self) -> None:
        for check in self._smart_checks.values():
            check.stateChanged.connect(self._refresh_include_hint)
        self.store_combo.currentIndexChanged.connect(self._refresh_include_hint)
        self._refresh_include_hint()

    def _smart_selected(self) -> bool:
        if any(check.isChecked() for check in self._smart_checks.values()):
            return True
        return self.store_combo.currentIndex() > 0

    def _refresh_include_hint(self) -> None:
        self.include_caption.setText(self._INCLUDE_SMART if self._smart_selected() else self._INCLUDE_ALL)

    def _build_smart_section(self) -> None:
        self.viewLayout.addWidget(CaptionLabel("Detecção inteligente"))
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for index, (label, rule) in enumerate(_SMART_PRESETS):
            check = CheckBox(label)
            self._smart_checks[rule] = check
            grid.addWidget(check, index // 2, index % 2)
        discount = CheckBox("Desconto 30%+")
        self._smart_checks["discount>=30"] = discount
        grid.addWidget(discount, len(_SMART_PRESETS) // 2, len(_SMART_PRESETS) % 2)
        self.viewLayout.addWidget(grid_host)

        store_row = QWidget()
        store_layout = QGridLayout(store_row)
        store_layout.setContentsMargins(0, 0, 0, 0)
        store_layout.addWidget(CaptionLabel("Loja"), 0, 0)
        self.store_combo = ComboBox()
        self.store_combo.addItem("Qualquer")
        self._store_ids = [""]
        for store_id, data in STORES.items():
            self.store_combo.addItem(data["label"][0])
            self._store_ids.append(store_id)
        store_layout.addWidget(self.store_combo, 0, 1)
        self.viewLayout.addWidget(store_row)

    def _build_channels_section(self) -> None:
        self.viewLayout.addWidget(CaptionLabel("Origem (vazio = todos os canais monitorados)"))
        self.viewLayout.addWidget(self._channel_scroll(self._config.source_ids, self._source_checks))
        self.viewLayout.addWidget(CaptionLabel("Destino (vazio = destinos padrão)"))
        self.viewLayout.addWidget(self._channel_scroll(self._config.destination_ids, self._dest_checks))

    def _channel_scroll(self, ids: list[int], registry: dict[int, CheckBox]) -> QWidget:
        scroll = SingleDirectionScrollArea()
        scroll.setFixedHeight(96)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QGridLayout(host)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        if not ids:
            layout.addWidget(CaptionLabel("Nenhum canal configurado ainda."), 0, 0)
        for index, cid in enumerate(ids):
            check = CheckBox(self._config.channel_name(cid))
            registry[cid] = check
            layout.addWidget(check, index // 2, index % 2)
        scroll.setWidget(host)
        return scroll

    def _load(self, existing: Filter | None) -> None:
        if not existing:
            return
        self.name_input.setText(existing.name)
        self.include_input.setText(", ".join(existing.include))
        self.exclude_input.setText(", ".join(existing.exclude))
        for rule, check in self._smart_checks.items():
            check.setChecked(rule in existing.smart_rules)
        for rule in existing.smart_rules:
            if rule.startswith("store:"):
                store_id = rule.split(":", 1)[1]
                if store_id in self._store_ids:
                    self.store_combo.setCurrentIndex(self._store_ids.index(store_id))
        for cid, check in self._source_checks.items():
            check.setChecked(cid in existing.source_ids)
        for cid, check in self._dest_checks.items():
            check.setChecked(cid in existing.destination_ids)

    @staticmethod
    def _split(text: str) -> list[str]:
        return [part.strip() for part in text.split(",") if part.strip()]

    def result_filter(self) -> Filter:
        base = self._existing or Filter.new()
        smart = [rule for rule, check in self._smart_checks.items() if check.isChecked()]
        store_id = self._store_ids[self.store_combo.currentIndex()]
        if store_id:
            smart.append(f"store:{store_id}")
        return Filter(
            id=base.id,
            name=self.name_input.text().strip() or "Sem nome",
            enabled=base.enabled,
            source_ids=[cid for cid, check in self._source_checks.items() if check.isChecked()],
            destination_ids=[cid for cid, check in self._dest_checks.items() if check.isChecked()],
            include=self._split(self.include_input.text()),
            exclude=self._split(self.exclude_input.text()),
            smart_rules=smart,
            min_confidence=base.min_confidence,
        )
