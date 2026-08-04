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
    LineEdit,
    PrimaryPushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SwitchButton,
    TransparentPushButton,
)

from monitor import paths
from monitor.autobuy import registry
from monitor.autobuy.criteria import Criteria
from monitor.autobuy.manager import AutobuyManager
from monitor.autobuy.models import WatchItem
from monitor.autobuy.records import PixRecord, delete_record, load_records

from ..common import PageView, clear_layout, make_vscroll
from ..test_dialog import TestDialog
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
        self._open_stores: set[str] = set()
        self._test_url: dict[str, str] = {}
        self._test_mode: dict[str, int] = {}
        self._test_coupon: dict[str, str] = {}
        self._test_status: dict[str, str] = {}
        self._test_color: dict[str, str] = {}
        self._widgets: dict[str, QLabel] = {}
        self._status_labels: dict[str, QLabel] = {}
        self._status_dots: dict[str, QLabel] = {}
        self._verify_buttons: dict[str, TransparentPushButton] = {}
        self._checking: set[str] = set()
        self._connecting: set[str] = set()
        self._running = False

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

        log_btn = TransparentPushButton("Ver log")
        log_btn.setIcon(FluentIcon.DOCUMENT)
        log_btn.setToolTip("Histórico completo das compras automáticas")
        log_btn.clicked.connect(self._open_log)

        self.add_header_widget(self.master_switch)
        self.add_header_widget(new_btn)
        self.add_header_widget(log_btn)
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
        self._list.addWidget(self._section("Lojas"))
        self._widgets = {}
        for spec in registry.all_stores():
            self._list.addWidget(self._store_card(spec))
        self._list.addWidget(self._section("Produtos vigiados"))
        if self._manager.items:
            for item in self._manager.items:
                self._list.addWidget(self._item_card(item))
        else:
            self._list.addWidget(self._hint("Nenhum produto vigiado. Clique em Novo produto para configurar o que comprar."))
        self._list.addStretch(1)

    def _store_card(self, spec) -> SimpleCardWidget:
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        dot = QLabel("●")
        self._status_dots[spec.id] = dot
        header.addWidget(dot)
        header.addWidget(StrongBodyLabel(spec.label))
        status = BodyLabel("")
        self._status_labels[spec.id] = status
        header.addWidget(status)
        header.addStretch(1)

        ocupado = self._checking or self._connecting

        verify_btn = TransparentPushButton("Verificar")
        verify_btn.setIcon(FluentIcon.SYNC)
        verify_btn.setEnabled(self._app is not None and not ocupado)
        verify_btn.clicked.connect(lambda _=False, s=spec.id: self._check_store(s))
        self._verify_buttons[spec.id] = verify_btn
        header.addWidget(verify_btn)

        connect_btn = TransparentPushButton("Conectar conta")
        connect_btn.setIcon(FluentIcon.GLOBE)
        connect_btn.setEnabled(self._app is not None and not ocupado)
        connect_btn.clicked.connect(lambda _=False, s=spec.id: self._connect_store(s))
        header.addWidget(connect_btn)
        layout.addLayout(header)
        self._paint_status(spec.id)

        row = QHBoxLayout()
        row.setSpacing(10)
        test_btn = PrimaryPushButton("Testar fluxo de compra")
        test_btn.setIcon(FluentIcon.PLAY)
        test_btn.setEnabled(self._app is not None and not self._running)
        test_btn.clicked.connect(lambda _=False, s=spec.id: self._open_test(s))
        row.addWidget(test_btn)
        row.addStretch(1)
        layout.addLayout(row)

        result = BodyLabel(self._test_status.get(spec.id, "Nenhum teste executado ainda."))
        result.setWordWrap(True)
        result.setStyleSheet(f"color: {PALETTE[self._test_color.get(spec.id, 'muted')]};")
        layout.addWidget(result)
        self._widgets[spec.id] = result

        records = load_records(store=spec.id)
        expanded = spec.id in self._open_stores
        toggle = TransparentPushButton(
            f"{'Ocultar' if expanded else 'Ver'} PIX gerados na {spec.label} ({len(records)})"
        )
        toggle.setIcon(FluentIcon.CHEVRON_DOWN_MED if expanded else FluentIcon.CHEVRON_RIGHT_MED)
        toggle.clicked.connect(lambda _=False, s=spec.id: self._toggle_store(s))
        layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)

        if expanded:
            if records:
                for record in records:
                    layout.addWidget(self._record_card(record))
            else:
                layout.addWidget(self._hint(f"Nenhum PIX gerado na {spec.label} ainda."))
        return card

    def _paint_status(self, store_id: str) -> None:
        text = self._store_status_text(store_id)
        color = self._store_status_color(store_id)
        label = self._status_labels.get(store_id)
        if label is not None:
            label.setText(text)
            label.setStyleSheet(f"color: {color};")
        dot = self._status_dots.get(store_id)
        if dot is not None:
            dot.setText("◍" if store_id in self._checking or store_id in self._connecting else "●")
            dot.setStyleSheet(f"color: {color}; font-size: 16px;")

    def _store_status_text(self, store_id: str) -> str:
        if store_id in self._checking:
            return "verificando conexão..."
        if store_id in self._connecting:
            return "aguardando o login na janela do navegador..."
        if getattr(self._manager, "busy_profile", False):
            return "navegador do robô aberto: feche a janela 'Chrome for Testing'"
        if not self._manager.was_checked(store_id):
            return "conexão não verificada"
        user = self._manager.account(store_id)
        if not user:
            return "não conectado"
        prazo = self._session_left(store_id)
        if user == "conta salva":
            return f"conta salva{prazo} — clique em Verificar para confirmar"
        return f"conectado como {user}{prazo}"

    def _session_left(self, store_id: str) -> str:
        import time

        from monitor.autobuy.browser import session_expiry

        spec = registry.get(store_id)
        if spec is None:
            return ""
        quando = session_expiry(spec.cookie_domain, spec.session_cookies)
        if quando is None:
            return " (sem prazo fixo: expira por inatividade)"
        dias = (quando - time.time()) / 86400
        if dias <= 0:
            return " (cookie expirado)"
        if dias < 2:
            return f" (expira em {dias * 24:.0f} horas)"
        return f" (expira em {dias:.0f} dias)"

    def _store_status_color(self, store_id: str) -> str:
        if store_id in self._checking or store_id in self._connecting:
            return PALETTE["capture"]
        if getattr(self._manager, "busy_profile", False):
            return PALETTE["warning"]
        if not self._manager.was_checked(store_id):
            return PALETTE["muted"]
        user = self._manager.account(store_id)
        if not user:
            return PALETTE["danger"]
        return PALETTE["capture"] if user == "conta salva" else PALETTE["success"]

    def _set_busy(self, store_id: str, bucket: set[str], busy: bool) -> None:
        if busy:
            bucket.add(store_id)
        else:
            bucket.discard(store_id)
        for button in self._verify_buttons.values():
            button.setEnabled(self._app is not None and not self._checking and not self._connecting)
        self._paint_status(store_id)

    def _toggle_store(self, store_id: str) -> None:
        if store_id in self._open_stores:
            self._open_stores.discard(store_id)
        else:
            self._open_stores.add(store_id)
        self.refresh()

    def check_all_stores(self) -> None:
        if self._app is None or self._checking or self._connecting:
            return
        for spec in registry.all_stores():
            self._checking.add(spec.id)
            self._paint_status(spec.id)
        for button in self._verify_buttons.values():
            button.setEnabled(False)
        self._app.schedule(self._check_all_async())

    async def _check_all_async(self) -> None:
        for spec in registry.all_stores():
            try:
                await self._manager.check_login(spec.id, deep=False)
            except Exception:
                pass
            finally:
                self._set_busy(spec.id, self._checking, False)
            self.refresh()

    def _check_store(self, store_id: str) -> None:
        if self._app is None or self._checking or self._connecting:
            return
        self._set_busy(store_id, self._checking, True)
        self._app.schedule(self._check_store_async(store_id))

    async def _check_store_async(self, store_id: str) -> None:
        spec = registry.get(store_id)
        label = spec.label if spec else store_id
        try:
            user = await self._manager.check_login(store_id)
        finally:
            self._set_busy(store_id, self._checking, False)
        self.refresh()
        self._notify(
            f"{label}: conectado como {user}" if user else f"{label}: não conectado",
            "success" if user else "warning",
        )

    def _connect_store(self, store_id: str) -> None:
        if self._app is None or self._checking or self._connecting:
            return
        spec = registry.get(store_id)
        label = spec.label if spec else store_id
        self._set_busy(store_id, self._connecting, True)
        self._notify(f"Abrindo a {label}: faça login na janela e feche o navegador")
        self._app.schedule(self._connect_store_async(store_id))

    async def _connect_store_async(self, store_id: str) -> None:
        spec = registry.get(store_id)
        label = spec.label if spec else store_id
        try:
            user = await self._manager.open_login(store_id)
        finally:
            self._set_busy(store_id, self._connecting, False)
        self.refresh()
        self._notify(
            f"{label}: conectado como {user}" if user else f"{label}: login não concluído",
            "success" if user else "warning",
        )

    def _set_test_status(self, store_id: str, text: str, color_key: str = "muted") -> None:
        self._test_status[store_id] = text
        self._test_color[store_id] = color_key
        label = self._widgets.get(store_id)
        if label is not None:
            label.setText(text)
            label.setStyleSheet(f"color: {PALETTE[color_key]};")

    def _open_test(self, store_id: str) -> None:
        if self._app is None or self._running:
            return
        dialog = TestDialog(
            store_id,
            self._test_url.get(store_id, ""),
            self._test_mode.get(store_id, 0),
            self._test_coupon.get(store_id, ""),
            self.window(),
        )
        if not dialog.exec():
            return
        url = dialog.result_url()
        mode = dialog.result_mode()
        coupon = dialog.result_coupon()
        self._test_url[store_id] = url
        self._test_mode[store_id] = mode
        self._test_coupon[store_id] = coupon
        self._running = True
        self._set_test_status(store_id, "Teste em andamento, acompanhe na aba Console...", "capture")
        self._app.schedule(self._run_test_async(store_id, url, mode, coupon))

    async def _run_test_async(self, store_id: str, url: str, mode: int, coupon: str = "") -> None:
        try:
            if mode == 0:
                await self._probe(store_id, url)
            else:
                await self._checkout_test(store_id, url, confirm=mode == 2, coupon=coupon)
        finally:
            self._running = False
            self.refresh()

    async def _probe(self, store_id: str, url: str) -> None:
        product = await self._manager.probe(url)
        if product is None:
            self._set_test_status(store_id, "Não consegui ler os dados do produto nessa página.", "danger")
            return
        estoque = "disponível" if product.available else "indisponível"
        price = f"{product.pix_price:.2f}".replace(".", ",")
        self._set_test_status(
            store_id,
            f"{product.title[:70]} — à vista no PIX R$ {price} ({estoque})",
            "success" if product.available else "warning",
        )

    async def _checkout_test(self, store_id: str, url: str, confirm: bool, coupon: str = "") -> None:
        result = await self._manager.run_test(url, max_price=0.0, confirm=confirm, coupon=coupon)
        if result is None:
            self._set_test_status(store_id, "O teste não pôde ser executado, veja o Console.", "danger")
            return
        if result.status.value == "pix_ready":
            self._set_test_status(
                store_id,
                f"PIX gerado em {_seconds(result.elapsed)}. O código está aqui embaixo e no seu Telegram.",
                "success",
            )
            return
        if result.status.value == "dry_run_ok":
            self._set_test_status(
                store_id,
                f"Simulação percorreu o checkout em {_seconds(result.elapsed)} e parou antes de finalizar. {result.detail}",
                "success",
            )
            return
        self._set_test_status(
            store_id, f"Terminou como {result.status.value}: {result.detail or 'sem detalhes'}", "warning"
        )

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

        row.addSpacing(18)
        row.addWidget(BodyLabel("Mostrar o navegador:"))
        self.window_switch = SwitchButton()
        self.window_switch.setOnText("Sim")
        self.window_switch.setOffText("Não")
        self.window_switch.setChecked(not self._manager.settings.headless)
        self.window_switch.checkedChanged.connect(self._on_window)
        self.window_switch.setToolTip(
            "Com a janela visível a loja desconfia menos e você consegue resolver um captcha na hora."
        )
        row.addWidget(self.window_switch)
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
        criteria = Criteria.parse(item.include, item.exclude)
        parts = [criteria.summary()]
        parts.append(f"até R$ {item.max_price:.2f}".replace(".", ","))
        spec = registry.get(item.store)
        parts.append(spec.label if spec else "qualquer loja")
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

    def _on_window(self, checked: bool) -> None:
        self._manager.set_headless(not checked)

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

    def _open_log(self) -> None:
        from monitor.autobuy import journal

        caminho = journal.log_path()
        if not caminho.exists():
            caminho.write_text("Nenhuma compra automática registrada ainda.\n", encoding="utf-8")
        os.startfile(str(caminho))

    def _open_folder(self) -> None:
        os.startfile(str(paths.autobuy_dir()))
