from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QPixmap
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

from watcher import paths
from watcher.autobuy import registry
from watcher.autobuy.manager import AutobuyManager
from watcher.autobuy.models import BuyStatus, WatchItem, status_label
from watcher.autobuy.records import PixRecord, delete_record, load_records

from ..chips import FlowLayout
from ..common import ElidedBody, ElidedStrong, PageView, clear_layout, format_brl, make_vscroll, plain
from ..test_dialog import BUY_MODE, TestDialog
from ..theme import PALETTE, hairline, surface, text_tone

_PREVIEW_WIDTH = 460
_CART_NOTE = "Atenção: o robô remove os outros itens do seu carrinho nessa loja."

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

def _code_style() -> str:
    return f"""
QLabel#pixCode {{
    background: {surface()};
    border: 1px solid {hairline()};
    border-radius: 8px;
    padding: 10px;
    font-family: 'Cascadia Mono', Consolas, monospace;
    font-size: 11px;
    color: {text_tone('capture')};
}}
"""


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "s"


class AutobuyPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("autobuyPage", "Auto-Compra (BETA)", "Compra automática até gerar o PIX", parent=parent)
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
        self._list.addWidget(self._section("Produtos vigiados"))
        if self._manager.items:
            for item in self._manager.items:
                self._list.addWidget(self._item_card(item))
        else:
            self._list.addWidget(self._hint("Nenhum produto vigiado. Clique em Novo produto para configurar o que comprar."))
        self._list.addWidget(self._section("Lojas"))
        self._widgets = {}
        for spec in registry.all_stores():
            self._list.addWidget(self._store_card(spec))
        self._list.addStretch(1)

    def _state_text(self) -> tuple[str, str]:
        settings = self._manager.settings
        if getattr(self._manager, "login_open", False):
            return (
                "Robô ocupado: janela de login aberta. Promoções que chegarem agora esperam até 2 minutos na fila, "
                "feche o navegador quando terminar o login.",
                "warning",
            )
        if not settings.enabled:
            return "Compra automática desligada: nenhum produto será comprado.", "muted"
        if settings.real_mode:
            return "Compra real ativa: quando uma promoção casar com um produto, o pedido é finalizado e gera PIX de verdade.", "warning"
        return "Simulação: o robô percorre o checkout e para antes de finalizar, nenhum pedido é feito.", "success"

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
        status = ElidedBody("")
        self._status_labels[spec.id] = status
        header.addWidget(status, 1)

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

        result = plain(BodyLabel(self._test_status.get(spec.id, "Nenhum teste executado ainda.")))
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

        from watcher.autobuy.browser import session_expiry

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
        QTimer.singleShot(300, self._paint_state)

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
        if mode == BUY_MODE:
            pergunta = (
                f"Isto finaliza um pedido de verdade e gera um PIX para pagar.\n\n{url}\n\n"
                "Se você não pagar, o pedido expira e cancela sozinho. Continuar?"
            )
            if not self._app.confirm("Comprar de verdade", pergunta, "Comprar e gerar PIX"):
                return
        self._test_url[store_id] = url
        self._test_mode[store_id] = 0 if mode == BUY_MODE else mode
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
        estoque = "disponível" if product.available else "sem estoque"
        self._set_test_status(
            store_id,
            f"{product.title[:70]} — à vista no PIX {format_brl(product.pix_price)} ({estoque})",
            "success" if product.available else "warning",
        )

    async def _checkout_test(self, store_id: str, url: str, confirm: bool, coupon: str = "") -> None:
        result = await self._manager.run_test(url, max_price=0.0, confirm=confirm, coupon=coupon)
        if result is None:
            self._set_test_status(store_id, "O teste não pôde ser executado, veja o Console.", "danger")
            return
        if result.status == BuyStatus.PIX_READY:
            self._open_stores.add(store_id)
            records = load_records(store=store_id)
            if records:
                self._expanded.add(records[0].stamp)
            enviado = await self._app.send_result(result)
            onde = "Ele está aqui embaixo e também no seu Telegram." if enviado else (
                "Ele está aqui embaixo. Não consegui mandar para o seu Telegram."
            )
            self._set_test_status(store_id, f"PIX gerado em {_seconds(result.elapsed)}. {onde}", "success")
            return
        if result.status == BuyStatus.DRY_RUN_OK:
            await self._app.send_result(result)
            self._set_test_status(
                store_id,
                f"Simulação percorreu o checkout em {_seconds(result.elapsed)} e parou antes de finalizar. {result.detail}",
                "success",
            )
            return
        detalhe = f": {result.detail}" if result.detail else ""
        self._set_test_status(store_id, f"Não concluiu, {status_label(result.status)}{detalhe}", "warning")

    def _section(self, title: str) -> QWidget:
        label = StrongBodyLabel(title)
        label.setContentsMargins(2, 8, 0, 0)
        return label

    def _hint(self, text: str) -> QWidget:
        label = BodyLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE['muted']};")
        return label

    @staticmethod
    def _pair(label: str, widget: QWidget, tooltip: str = "") -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 18, 0)
        row.setSpacing(10)
        row.addWidget(BodyLabel(label))
        row.addWidget(widget)
        if tooltip:
            box.setToolTip(tooltip)
        return box

    def _config_card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        column = QVBoxLayout(card)
        column.setContentsMargins(22, 14, 22, 14)
        column.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(StrongBodyLabel("Compra automática"))
        self.master_switch = SwitchButton()
        self.master_switch.setOnText("Ligada")
        self.master_switch.setOffText("Desligada")
        self.master_switch.setChecked(self._manager.settings.enabled)
        self.master_switch.checkedChanged.connect(self._on_master)
        top.addWidget(self.master_switch)
        top.addStretch(1)
        column.addLayout(top)

        self.state_label = BodyLabel("")
        self.state_label.setWordWrap(True)
        column.addWidget(self.state_label)

        host = QWidget()
        flow = FlowLayout(host, spacing=10)

        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["Simulação (para antes de finalizar)", "Compra real (gera o PIX)"])
        self.mode_combo.setCurrentIndex(1 if self._manager.settings.real_mode else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        flow.addWidget(self._pair("Modo:", self.mode_combo, _CART_NOTE))

        self.coupon_switch = SwitchButton()
        self.coupon_switch.setOnText("Sim")
        self.coupon_switch.setOffText("Não")
        self.coupon_switch.setChecked(self._manager.settings.use_coupon)
        self.coupon_switch.checkedChanged.connect(self._on_coupon)
        flow.addWidget(self._pair("Aplicar cupom da mensagem:", self.coupon_switch))

        self.window_switch = SwitchButton()
        self.window_switch.setOnText("Sim")
        self.window_switch.setOffText("Não")
        self.window_switch.setChecked(not self._manager.settings.headless)
        self.window_switch.checkedChanged.connect(self._on_window)
        flow.addWidget(
            self._pair(
                "Mostrar o navegador:",
                self.window_switch,
                "Com a janela visível a loja desconfia menos e você consegue resolver um captcha na hora. "
                "Com ela oculta, um captcha encerra a compra.",
            )
        )
        column.addWidget(host)

        note = CaptionLabel(
            "Lê as promoções dos canais marcados como Origem, enquanto o monitor está ligado. " + _CART_NOTE
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {PALETTE['muted']};")
        column.addWidget(note)
        self._paint_state()
        return card

    def _paint_state(self) -> None:
        label = getattr(self, "state_label", None)
        if label is None:
            return
        texto, tom = self._state_text()
        label.setText(texto)
        peso = "600" if tom == "warning" else "400"
        label.setStyleSheet(f"color: {text_tone(tom)}; font-weight: {peso};")

    def _item_card(self, item: WatchItem) -> SimpleCardWidget:
        card = SimpleCardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(22, 14, 22, 14)
        row.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(ElidedStrong(item.name))
        criteria = item.criteria()
        parts = [criteria.summary()]
        parts.append(f"até {format_brl(item.max_price)}")
        spec = registry.get(item.store)
        parts.append(spec.label if spec else "qualquer loja")
        meta = CaptionLabel("   |   ".join(parts))
        meta.setWordWrap(True)
        meta.setStyleSheet(f"color: {PALETTE['muted']};")
        titles.addWidget(meta)
        if not self._manager.settings.enabled and item.enabled:
            pausado = CaptionLabel("Pausado: a compra automática está desligada no topo da página")
            pausado.setStyleSheet(f"color: {PALETTE['warning']};")
            titles.addWidget(pausado)
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
        title = plain(StrongBodyLabel(record.title))
        title.setWordWrap(True)
        titles.addWidget(title)
        meta_text = f"{record.created_at}   |   À vista no PIX: {format_brl(record.price)}"
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
        delete_btn.clicked.connect(
            lambda _=False, r=record: self._delete_record(r.stamp, r.title, bool(r.pix_code))
        )
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
                code = plain(QLabel(record.pix_code))
                code.setObjectName("pixCode")
                code.setStyleSheet(_code_style())
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

    def _delete_record(self, stamp: str, title: str = "", has_code: bool = False) -> None:
        if self._app is not None:
            aviso = (
                " O código PIX copia e cola e o print somem desta tela: se ainda não pagou, copie o código antes."
                if has_code
                else ""
            )
            pergunta = f"Apagar o registro de '{title or stamp}'?{aviso}"
            if not self._app.confirm("Apagar registro de PIX", pergunta, "Apagar"):
                return
        removed = delete_record(stamp)
        self._expanded.discard(stamp)
        self.refresh()
        self._notify("Registro apagado" if removed else "Nada para apagar", "success" if removed else "warning")

    def _on_master(self, checked: bool) -> None:
        self._manager.set_enabled(checked)
        self._notify("Compra automática ativada" if checked else "Compra automática desligada")
        QTimer.singleShot(0, self.refresh)

    def _on_mode(self, index: int) -> None:
        if index == 1 and self._app is not None:
            pergunta = (
                "Com a Compra real, quando uma promoção casar com um produto ligado, o pedido é finalizado "
                "de verdade e gera um PIX para pagar. Continuar?"
            )
            if not self._app.confirm("Ligar a compra real", pergunta, "Ligar compra real"):
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(0)
                self.mode_combo.blockSignals(False)
                return
        self._manager.set_real_mode(index == 1)
        if index == 1:
            self._notify("Modo compra real: pedidos serão finalizados de verdade", "warning")
        else:
            self._notify("Modo simulação: para antes de finalizar o pedido")
        self._paint_state()

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
        target = Path(path).absolute()
        if not target.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._notify(f"Não foi possível abrir {target.name}.", "warning")

    def _open_log(self) -> None:
        from watcher.autobuy import journal

        caminho = journal.log_path()
        if not caminho.exists():
            caminho.write_text("Nenhuma compra automática registrada ainda.\n", encoding="utf-8")
        self._open_file(str(caminho))

    def _open_folder(self) -> None:
        self._open_file(str(paths.autobuy_dir()))
