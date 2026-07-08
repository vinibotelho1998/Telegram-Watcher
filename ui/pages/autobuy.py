from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SwitchButton,
    TransparentPushButton,
)

from monitor import paths
from monitor.autobuy.manager import AutobuyManager
from monitor.autobuy.models import WatchItem
from monitor.autobuy.records import PixRecord, delete_record, load_records

from ..common import PageView, clear_layout, make_vscroll
from ..theme import PALETTE

_PREVIEW_WIDTH = 460

_STEP_LABELS = {
    "produto": "produto",
    "pre-carrinho": "pré-carrinho",
    "carrinho": "carrinho",
    "endereco": "endereço",
    "entrega": "entrega",
    "pagamento": "pagamento",
    "confirmacao": "revisão",
    "pix": "PIX",
}

_CODE_STYLE = f"""
QLabel#pixCode {{
    background: rgba(0, 0, 0, 0.28);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    padding: 10px;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 11px;
    color: {PALETTE['capture']};
}}
"""


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "s"


class AutobuyPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("autobuyPage", "Auto-Compra", "Compra automática até gerar o PIX", parent=parent)
        self._app = app
        self._manager: AutobuyManager = app.autobuy if app is not None else AutobuyManager()
        self._expanded: set[str] = set()

        self.master_switch = SwitchButton()
        self.master_switch.setOnText("Ativa")
        self.master_switch.setOffText("Desligada")
        self.master_switch.setChecked(self._manager.settings.enabled)
        self.master_switch.checkedChanged.connect(self._on_master)

        new_btn = PrimaryPushButton("Novo produto")
        new_btn.setIcon(FluentIcon.ADD)
        new_btn.clicked.connect(lambda: self._edit_item(None))

        folder_btn = TransparentPushButton("Abrir pasta")
        folder_btn.setIcon(FluentIcon.FOLDER)
        folder_btn.clicked.connect(self._open_folder)

        self.add_header_widget(self.master_switch)
        self.add_header_widget(new_btn)
        self.add_header_widget(folder_btn)

        area, self._list = make_vscroll()
        self.add_body(area, 1)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self._list)
        self._list.addWidget(self._config_card())
        self._list.addWidget(self._store_card())
        self._list.addWidget(self._section("Produtos vigiados"))
        if self._manager.items:
            for item in self._manager.items:
                self._list.addWidget(self._item_card(item))
        else:
            self._list.addWidget(self._hint("Nenhum produto vigiado. Clique em Novo produto para configurar o que comprar."))
        self._list.addWidget(self._section("PIX gerados"))
        records = load_records()
        if records:
            for record in records:
                self._list.addWidget(self._record_card(record))
        else:
            self._list.addWidget(self._hint("Nenhum PIX gerado ainda. Quando o robô comprar, o código e o print aparecem aqui."))
        self._list.addStretch(1)

    def _store_card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(12)
        row.addWidget(BodyLabel("Loja:"))
        row.addWidget(StrongBodyLabel("KaBuM!"))
        row.addSpacing(18)
        self.store_status = BodyLabel(self._store_status_text())
        self.store_status.setStyleSheet(f"color: {self._store_status_color()};")
        row.addWidget(self.store_status)
        row.addStretch(1)
        verify_btn = TransparentPushButton("Verificar")
        verify_btn.setIcon(FluentIcon.SYNC)
        verify_btn.setEnabled(self._app is not None)
        verify_btn.clicked.connect(self._check_store)
        row.addWidget(verify_btn)
        connect_btn = TransparentPushButton("Conectar conta")
        connect_btn.setIcon(FluentIcon.GLOBE)
        connect_btn.setEnabled(self._app is not None)
        connect_btn.clicked.connect(self._connect_store)
        row.addWidget(connect_btn)
        return card

    def _store_status_text(self) -> str:
        if not self._manager.kabum_checked:
            return "Conexão não verificada"
        if self._manager.kabum_user:
            return f"Conectado como {self._manager.kabum_user}"
        return "Não conectado"

    def _store_status_color(self) -> str:
        if not self._manager.kabum_checked:
            return PALETTE["muted"]
        return PALETTE["success"] if self._manager.kabum_user else PALETTE["danger"]

    def _check_store(self) -> None:
        if self._app is None:
            return
        self.store_status.setText("Verificando conexão...")
        self.store_status.setStyleSheet(f"color: {PALETTE['muted']};")
        self._app.schedule(self._check_store_async())

    async def _check_store_async(self) -> None:
        user = await self._manager.check_login()
        self.refresh()
        self._notify(f"Conectado como {user}" if user else "Não conectado na KaBuM", "success" if user else "warning")

    def _connect_store(self) -> None:
        if self._app is None:
            return
        self._notify("Abrindo a KaBuM: faça login na janela e feche o navegador")
        self._app.schedule(self._connect_store_async())

    async def _connect_store_async(self) -> None:
        user = await self._manager.open_login()
        self.refresh()
        self._notify(f"Conectado como {user}" if user else "Login não concluído", "success" if user else "warning")

    def _section(self, title: str) -> QWidget:
        label = StrongBodyLabel(title)
        label.setContentsMargins(2, 8, 0, 0)
        return label

    def _hint(self, text: str) -> QWidget:
        label = BodyLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE['muted']};")
        return label

    def _config_card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(12)

        row.addWidget(BodyLabel("Modo:"))
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["Simulação (para antes de finalizar)", "Compra real (gera o PIX)"])
        self.mode_combo.setCurrentIndex(1 if self._manager.settings.real_mode else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        row.addWidget(self.mode_combo)

        row.addSpacing(18)
        row.addWidget(BodyLabel("Aplicar cupom da mensagem:"))
        self.coupon_switch = SwitchButton()
        self.coupon_switch.setOnText("Sim")
        self.coupon_switch.setOffText("Não")
        self.coupon_switch.setChecked(self._manager.settings.use_coupon)
        self.coupon_switch.checkedChanged.connect(self._on_coupon)
        row.addWidget(self.coupon_switch)
        row.addStretch(1)
        return card

    def _item_card(self, item: WatchItem) -> SimpleCardWidget:
        card = SimpleCardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(StrongBodyLabel(item.name))
        parts = [f"contém: {', '.join(item.include)}"]
        if item.exclude:
            parts.append(f"nunca: {', '.join(item.exclude)}")
        parts.append(f"até R$ {item.max_price:.2f}".replace(".", ","))
        meta = CaptionLabel("   |   ".join(parts))
        meta.setStyleSheet(f"color: {PALETTE['muted']};")
        titles.addWidget(meta)
        row.addLayout(titles, 1)

        switch = SwitchButton()
        switch.setOnText("Ligado")
        switch.setOffText("Desligado")
        switch.setChecked(item.enabled)
        switch.checkedChanged.connect(lambda checked, item_id=item.id: self._on_toggle_item(item_id, checked))
        row.addWidget(switch)

        edit_btn = TransparentPushButton("Editar")
        edit_btn.setIcon(FluentIcon.EDIT)
        edit_btn.clicked.connect(lambda _=False, target=item: self._edit_item(target))
        row.addWidget(edit_btn)

        delete_btn = TransparentPushButton("Excluir")
        delete_btn.setIcon(FluentIcon.DELETE)
        delete_btn.clicked.connect(lambda _=False, item_id=item.id: self._delete_item(item_id))
        row.addWidget(delete_btn)
        return card

    def _record_card(self, record: PixRecord) -> SimpleCardWidget:
        expanded = record.stamp in self._expanded
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = StrongBodyLabel(record.title)
        title.setWordWrap(True)
        titles.addWidget(title)
        meta_text = f"{record.created_at}   |   À vista no PIX: R$ {record.price:.2f}"
        if record.elapsed:
            meta_text += f"   |   comprado em {_seconds(record.elapsed)}"
        meta = CaptionLabel(meta_text)
        meta.setStyleSheet(f"color: {PALETTE['muted']};")
        titles.addWidget(meta)
        header.addLayout(titles, 1)

        if record.pix_code:
            copy_btn = PrimaryPushButton("Copiar código PIX")
            copy_btn.setIcon(FluentIcon.COPY)
            copy_btn.clicked.connect(lambda _=False, code=record.pix_code: self._copy(code))
            header.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)

        expand_btn = TransparentPushButton("Recolher" if expanded else "Detalhes")
        expand_btn.setIcon(FluentIcon.CHEVRON_DOWN_MED if expanded else FluentIcon.CHEVRON_RIGHT_MED)
        expand_btn.clicked.connect(lambda _=False, stamp=record.stamp: self._toggle_record(stamp))
        header.addWidget(expand_btn, 0, Qt.AlignmentFlag.AlignTop)

        delete_btn = TransparentPushButton()
        delete_btn.setIcon(FluentIcon.DELETE)
        delete_btn.setToolTip("Apagar este registro")
        delete_btn.clicked.connect(lambda _=False, stamp=record.stamp: self._delete_record(stamp))
        header.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        if expanded:
            if record.timings:
                steps = "      ".join(
                    f"{_STEP_LABELS.get(key, key)} {_seconds(value)}" for key, value in record.timings.items()
                )
                timings = CaptionLabel(steps)
                timings.setStyleSheet(f"color: {PALETTE['capture']};")
                layout.addWidget(timings)
            if record.pix_code:
                code = QLabel(record.pix_code)
                code.setObjectName("pixCode")
                code.setStyleSheet(_CODE_STYLE)
                code.setWordWrap(True)
                code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(code)
            pixmap = QPixmap(record.screenshot) if record.screenshot else QPixmap()
            if not pixmap.isNull():
                preview = QLabel()
                preview.setPixmap(
                    pixmap.scaledToWidth(_PREVIEW_WIDTH, Qt.TransformationMode.SmoothTransformation)
                )
                preview.setStyleSheet("border-radius: 8px;")
                layout.addWidget(preview)
                open_btn = TransparentPushButton("Abrir print em tamanho real")
                open_btn.setIcon(FluentIcon.PHOTO)
                open_btn.clicked.connect(lambda _=False, path=record.screenshot: self._open_file(path))
                layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _toggle_record(self, stamp: str) -> None:
        if stamp in self._expanded:
            self._expanded.discard(stamp)
        else:
            self._expanded.add(stamp)
        self.refresh()

    def _delete_record(self, stamp: str) -> None:
        removed = delete_record(stamp)
        self._expanded.discard(stamp)
        self.refresh()
        self._notify("Registro apagado" if removed else "Nada para apagar", "success" if removed else "warning")

    def _on_master(self, checked: bool) -> None:
        self._manager.set_enabled(checked)
        self._notify("Compra automática ativada" if checked else "Compra automática desligada")

    def _on_mode(self, index: int) -> None:
        self._manager.set_real_mode(index == 1)
        if index == 1:
            self._notify("Modo compra real: pedidos serão finalizados de verdade", "warning")
        else:
            self._notify("Modo simulação: para antes de finalizar o pedido")

    def _on_coupon(self, checked: bool) -> None:
        self._manager.set_use_coupon(checked)

    def _on_toggle_item(self, item_id: str, enabled: bool) -> None:
        self._manager.toggle_item(item_id, enabled)

    def _edit_item(self, item: WatchItem | None) -> None:
        if self._app is not None:
            self._app.open_watch_editor(item)

    def _delete_item(self, item_id: str) -> None:
        if self._app is not None:
            self._app.delete_watch_item(item_id)

    def _copy(self, code: str) -> None:
        QGuiApplication.clipboard().setText(code)
        self._notify("Código PIX copiado")

    def _notify(self, message: str, kind: str = "success") -> None:
        builder = InfoBar.warning if kind == "warning" else InfoBar.success
        builder(
            title="",
            content=message,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2600,
            parent=self.window(),
        )

    def _open_file(self, path: str) -> None:
        if Path(path).exists():
            os.startfile(path)

    def _open_folder(self) -> None:
        os.startfile(str(paths.autobuy_dir()))
