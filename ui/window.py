from __future__ import annotations

import asyncio
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    FluentTitleBar,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    NavigationItemPosition,
)

from watcher import APP_ID, APP_NAME, VERSION, paths
from watcher.autobuy.manager import AutobuyManager
from watcher.autobuy.models import SUCCESS_STATUSES, BuyResult, BuyStatus, WatchItem
from watcher.autobuy.notify import build_message, headline, send_pix_notification
from watcher.config import AppConfig
from watcher.engine import MonitorEngine
from watcher.models import EngineState, Filter, LogLevel
from watcher.storage import ChannelCache, FilterStore

from .dialogs import CodeDialog, FilterDialog, PasswordDialog, WatchDialog
from .pages import (
    AutobuyPage,
    ChannelsPage,
    ConsolePage,
    DashboardPage,
    FiltersPage,
    MessagesPage,
    SettingsPage,
)
from .setup_wizard import SetupWizard
from .theme import MUTED, apply_theme
from .tray import SystemTray

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRAY_FLAG = "--bandeja"


def _digits(value: str) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


class AppTitleBar(FluentTitleBar):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.version_label = CaptionLabel(f"v{VERSION}", self)
        self.version_label.setTextColor(QColor(MUTED[0]), QColor(MUTED[1]))
        self.hBoxLayout.insertWidget(
            2, self.version_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    def setTitle(self, title: str) -> None:  # noqa: N802
        super().setTitle(title.removesuffix(f" v{VERSION}"))


class AppWindow(FluentWindow):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = loop
        self._really_quit = False

        self.config = AppConfig.load()
        self.filter_store = FilterStore()
        self.channel_cache = ChannelCache()
        self.filters: list[Filter] = self.filter_store.load()
        self.channels = self.channel_cache.load()

        self.autobuy = AutobuyManager(
            on_log=self._on_log,
            on_result=self._on_autobuy_result,
            on_captcha=self._on_captcha,
            on_alert=self._on_autobuy_alert,
        )

        self.engine = MonitorEngine(
            self.config,
            lambda: self.filters,
            on_state=self._on_state,
            on_log=self._on_log,
            on_event=self._on_event,
            on_metrics=self._on_metrics,
            on_text=self.autobuy.handle_text,
            on_incoming=self._on_incoming,
            code_provider=self._request_code,
            password_provider=self._request_password,
        )

        self._icon = self._load_icon()
        self._init_window()
        self._init_pages()
        self._tray = self._init_tray()

        if self.config.is_ready:
            if self.config.auto_start:
                QTimer.singleShot(900, self.auto_start)
        else:
            QTimer.singleShot(500, self.open_setup_wizard)

        QTimer.singleShot(1500, self.autobuy_page.check_all_stores)

    def can_start_hidden(self) -> bool:
        return self._tray is not None and self.config.is_ready

    def _load_icon(self) -> QIcon:
        icon_path = paths.assets_path("final_icon.ico")
        return QIcon(str(icon_path)) if icon_path.exists() else QIcon()

    def _init_window(self) -> None:
        self.setTitleBar(AppTitleBar(self))
        self.navigationInterface.displayModeChanged.connect(self.titleBar.raise_)
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setWindowIcon(self._icon)
        self.setMinimumSize(960, 640)
        self.resize(1180, 800)
        self.navigationInterface.setExpandWidth(240)
        self.navigationInterface.expand(useAni=False)
        try:
            self.setMicaEffectEnabled(True)
        except Exception:
            pass

    def _init_pages(self) -> None:
        self.dashboard = DashboardPage(self)
        self.messages_page = MessagesPage(self)
        self.filters_page = FiltersPage(self)
        self.channels_page = ChannelsPage(self)
        self.autobuy_page = AutobuyPage(self)
        self.console_page = ConsolePage(self)
        self.settings_page = SettingsPage(self)

        self.addSubInterface(self.dashboard, FluentIcon.HOME, "Dashboard")
        self.addSubInterface(self.messages_page, FluentIcon.CHAT, "Conversas")
        self.addSubInterface(self.filters_page, FluentIcon.FILTER, "Filtros")
        self.addSubInterface(self.channels_page, FluentIcon.PEOPLE, "Canais")
        self.addSubInterface(self.autobuy_page, FluentIcon.SHOPPING_CART, "Auto-Compra")
        self.addSubInterface(self.console_page, FluentIcon.COMMAND_PROMPT, "Console")
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Configurações", position=NavigationItemPosition.BOTTOM
        )

    def _init_tray(self) -> SystemTray | None:
        if not QSystemTrayIcon_available():
            return None
        tray = SystemTray(self._icon, self._show_window, self.toggle_engine, self._quit, self)
        tray.show()
        return tray

    def _on_state(self, state: EngineState, detail: str = "") -> None:
        self.dashboard.set_state(state)
        if state == EngineState.ONLINE:
            QTimer.singleShot(600, self.messages_page.reload_chats)
            if not self.channels:
                QTimer.singleShot(900, self.request_sync)
        if self._tray is not None:
            self._tray.set_state(state)
        if state == EngineState.ERROR and detail:
            self._toast(detail, "error")

    def _on_log(self, message: str, level: LogLevel) -> None:
        self.console_page.append(message, level)

    def _on_event(self, data: dict) -> None:
        self.dashboard.add_event(data)

    def _on_metrics(self) -> None:
        self.dashboard.refresh()

    def _on_incoming(self, chat_id: int, message) -> None:
        self.messages_page.on_incoming(chat_id, message)

    def open_conversation(self, chat_id: int) -> None:
        self.switchTo(self.messages_page)
        self.messages_page.open_chat(chat_id)

    def toggle_engine(self) -> None:
        if self.engine.is_running:
            self.autobuy.stop_watchdog()
            self.schedule(self.engine.stop())
            return
        if not self.config.is_ready:
            self.open_setup_wizard()
            return
        self.schedule(self.engine.start())
        self.autobuy.start_watchdog()

    def auto_start(self) -> None:
        if self.engine.is_running or not self.config.is_ready:
            return
        self.schedule(self.engine.start(insistir=True))
        self.autobuy.start_watchdog()

    def schedule(self, coro):
        return self._loop.create_task(coro)

    def open_setup_wizard(self) -> None:
        wizard = SetupWizard(self, self)
        if wizard.exec() and wizard.connected:
            self.switchTo(self.channels_page)

    def confirm(self, title: str, text: str, ok_text: str = "Confirmar") -> bool:
        box = MessageBox(title, text, self)
        box.yesButton.setText(ok_text)
        box.cancelButton.setText("Cancelar")
        return bool(box.exec())

    def _call_attention(self, message: str) -> None:
        hidden = not self.isVisible() or self.isMinimized()
        self._show_window()
        if hidden and self._tray is not None:
            self._tray.notify(APP_NAME, message)

    async def _request_code(self, phone: str, aviso: str = "") -> str | None:
        self._call_attention("Digite o código de login do Telegram")
        dialog = CodeDialog(phone, self, aviso)
        return await self._await_dialog(dialog, dialog.code)

    async def _request_password(self, aviso: str = "") -> str | None:
        self._call_attention("Digite a senha de duas etapas do Telegram")
        dialog = PasswordDialog(self, aviso)
        return await self._await_dialog(dialog, dialog.password)

    def _await_dialog(self, dialog, getter):
        future = self._loop.create_future()

        def resolve(value):
            if not future.done():
                future.set_result(value)

        dialog.accepted.connect(lambda: resolve(getter()))
        dialog.rejected.connect(lambda: resolve(None))
        dialog.show()
        return future

    def _on_captcha(self) -> None:
        aviso = "Captcha na tela! Resolva na janela do navegador — o robô espera 3 minutos e continua sozinho."
        self.alert(aviso)
        self.schedule(self.engine.notify_me(f"[Watcher] {aviso}"))

    def _on_autobuy_alert(self, message: str) -> None:
        self.alert(message)
        self.schedule(self.engine.notify_me(f"[Watcher] {message}"))

    def alert(self, message: str) -> None:
        self._toast(message, "warning")
        if self._tray is not None:
            self._tray.notify(APP_NAME, message)
        self._beep()

    @staticmethod
    def _beep() -> None:
        if os.name != "nt":
            return
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    async def send_result(self, result: BuyResult) -> bool:
        message = build_message(result)
        sent = await self.engine.notify_me(message, result.screenshot or None)
        if not sent and self.engine.state in (EngineState.OFFLINE, EngineState.ERROR):
            sent = await send_pix_notification(self.config, result)
        return sent

    async def _on_autobuy_result(self, result: BuyResult) -> None:
        await self.send_result(result)
        self.autobuy_page.refresh()
        if result.status == BuyStatus.PIX_READY:
            self._toast("PIX gerado! Confira a aba Auto-Compra", "success")
        elif result.status == BuyStatus.DRY_RUN_OK:
            self._toast("Auto-Compra: simulação concluída, nenhum pedido foi feito", "success")
        elif result.status in (BuyStatus.LOGIN_REQUIRED, BuyStatus.BLOCKED):
            self.alert(headline(result))
        elif result.status not in SUCCESS_STATUSES:
            detalhe = f": {result.detail}" if result.detail else ""
            self._toast(f"{headline(result)}{detalhe}", "warning")

    def open_watch_editor(self, item: WatchItem | None) -> None:
        dialog = WatchDialog(item, self)
        if not dialog.exec():
            return
        self.autobuy.upsert_item(dialog.result_item())
        self.autobuy_page.refresh()
        self._toast("Produto salvo", "success")

    def delete_watch_item(self, item_id: str) -> None:
        item = next((i for i in self.autobuy.items if i.id == item_id), None)
        nome = item.name if item else "este produto"
        if not self.confirm("Excluir produto", f"Excluir o produto vigiado '{nome}'? Não dá para desfazer.", "Excluir"):
            return
        self.autobuy.remove_item(item_id)
        self.autobuy_page.refresh()
        self._toast("Produto removido", "info")

    def open_filter_editor(self, flt: Filter | None) -> None:
        dialog = FilterDialog(self.config, flt, self)
        if not dialog.exec():
            return
        result = dialog.result_filter()
        if any(f.id == result.id for f in self.filters):
            self.filters = [result if f.id == result.id else f for f in self.filters]
        else:
            self.filters.append(result)
        self.filter_store.save(self.filters)
        self.filters_page.refresh()
        self.messages_page.refresh_marks()
        self._toast("Filtro salvo", "success")

    def delete_filter(self, filter_id: str) -> None:
        flt = next((f for f in self.filters if f.id == filter_id), None)
        nome = flt.name if flt else "este filtro"
        if not self.confirm("Excluir filtro", f"Excluir o filtro '{nome}'? Não dá para desfazer.", "Excluir"):
            return
        self.filters = [f for f in self.filters if f.id != filter_id]
        self.filter_store.save(self.filters)
        self.filters_page.refresh()
        self.messages_page.refresh_marks()
        self._toast("Filtro removido", "info")

    def toggle_filter(self, filter_id: str, enabled: bool) -> None:
        for flt in self.filters:
            if flt.id == filter_id:
                flt.enabled = enabled
        self.filter_store.save(self.filters)
        self.messages_page.refresh_marks()

    def set_channel_role(self, chat_id: int, name: str, role: str, enabled: bool) -> None:
        target = self.config.source_ids if role == "source" else self.config.destination_ids
        if enabled and chat_id not in target:
            target.append(chat_id)
        elif not enabled and chat_id in target:
            target.remove(chat_id)
        label = self.config.channel_name(chat_id)
        if enabled and name and chat_id not in self.config.aliases:
            self.config.aliases[chat_id] = name
        elif not enabled and chat_id not in self.config.source_ids and chat_id not in self.config.destination_ids:
            self.config.aliases.pop(chat_id, None)
        self.config.save()
        if not enabled:
            campo = "source_ids" if role == "source" else "destination_ids"
            usados = [f.name for f in self.filters if chat_id in getattr(f, campo)]
            if usados:
                papel = "origem" if role == "source" else "destino"
                efeito = "não dispara mais para ele" if role == "source" else "não envia mais para ele"
                nomes = ", ".join(usados)
                self._toast(f"{label} era {papel} dos filtros {nomes}: cada um {efeito}", "warning")
        self.filters_page.refresh()
        self.dashboard.refresh()

    def request_sync(self) -> None:
        if not self.engine.is_running:
            self._toast("Inicie o monitor para sincronizar os canais", "warning")
            return
        self.channels_page.set_syncing(True)
        self.schedule(self._sync_channels())

    async def _sync_channels(self) -> None:
        try:
            channels = await self.engine.sync_channels()
            if channels:
                self.channels = channels
                self.channel_cache.save(channels)
                self.channels_page.refresh()
                self._toast(f"{len(channels)} canais sincronizados", "success")
        except Exception as exc:
            self._toast(f"Falha ao sincronizar os canais: {exc}", "error")
        finally:
            self.channels_page.set_syncing(False)

    def save_settings(self, payload: dict, silent: bool = False) -> bool:
        raw_id = str(payload.get("api_id", "")).strip()
        if raw_id and not raw_id.isdigit():
            self._toast("O API ID tem só números. Confira em my.telegram.org.", "error")
            return False
        antes = (self.config.api_id, _digits(self.config.phone), self.config.bot_token)
        self.config.api_id = int(raw_id) if raw_id else 0
        self.config.api_hash = str(payload.get("api_hash", "")).strip()
        self.config.bot_token = str(payload.get("bot_token", "")).strip()
        self.config.phone = str(payload.get("phone", "")).strip()
        self.config.allow_self = bool(payload.get("allow_self"))
        self.config.auto_start = bool(payload.get("auto_start"))
        saved = self.config.save()
        conta_mudou = bool(antes[0] and antes[1]) and antes[:2] != (self.config.api_id, _digits(self.config.phone))
        bot_mudou = bool(antes[2]) and antes[2] != self.config.bot_token
        if saved and (conta_mudou or bot_mudou):
            self._forget_sessions(conta_mudou, bot_mudou, silent)
        elif not silent:
            self._toast("Configurações salvas" if saved else "Não foi possível salvar", "success" if saved else "error")
        self.channels_page.refresh()
        self.dashboard.refresh_account()
        return saved

    def save_behavior(self, allow_self: bool, auto_start: bool) -> None:
        self.config.allow_self = allow_self
        self.config.auto_start = auto_start
        if not self.config.save():
            self._toast("Não foi possível salvar", "error")

    def _forget_sessions(self, conta: bool, bot: bool, silent: bool) -> None:
        if self.engine.is_running:
            self.schedule(self._stop_and_forget(conta, bot, silent))
        else:
            self._erase_sessions(conta, bot, silent)

    async def _stop_and_forget(self, conta: bool, bot: bool, silent: bool) -> None:
        self.autobuy.stop_watchdog()
        await self.engine.stop()
        self._erase_sessions(conta, bot, silent)

    def _erase_sessions(self, conta: bool, bot: bool, silent: bool) -> None:
        if conta:
            self.engine.forget_session("user")
        if bot:
            self.engine.forget_session("bot")
        if not silent:
            aviso = "Conta trocada" if conta else "Bot trocado"
            self._toast(f"{aviso}: inicie o monitor para entrar com os dados novos", "success")

    def logout_telegram(self) -> None:
        pergunta = (
            "Sair da conta do Telegram neste app? A sessão é encerrada e, para voltar, "
            "será preciso digitar um novo código de login."
        )
        if not self.confirm("Sair da conta", pergunta, "Sair da conta"):
            return

        async def sair() -> None:
            self.autobuy.stop_watchdog()
            encerrada = await self.engine.log_out()
            if encerrada:
                self._toast("Você saiu da conta do Telegram", "success")
            else:
                self._toast(
                    "Sessão apagada deste computador. Para encerrá-la também no Telegram, "
                    "use Configurações > Dispositivos no app",
                    "warning",
                )
            self.dashboard.refresh_account()

        self.schedule(sair())

    def toggle_theme(self, dark: bool) -> None:
        apply_theme(dark)
        self.config.dark_theme = dark
        self.config.save()

    def is_windows_autostart(self) -> bool:
        if os.name != "nt":
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
                return True
        except OSError:
            return False

    def set_windows_autostart(self, enabled: bool) -> None:
        if os.name != "nt":
            return
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, self._startup_command())
                else:
                    try:
                        winreg.DeleteValue(key, APP_ID)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self._toast(f"Falha ao configurar inicialização: {exc}", "error")

    def _startup_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" {TRAY_FLAG}'
        script = paths.resource_path("app.py")
        launcher = sys.executable.replace("python.exe", "pythonw.exe")
        return f'"{launcher}" "{script}" {TRAY_FLAG}'

    def toast(self, message: str, kind: str = "info") -> None:
        self._toast(message, kind)

    def _toast(self, message: str, kind: str = "info") -> None:
        builders = {
            "success": InfoBar.success,
            "error": InfoBar.error,
            "warning": InfoBar.warning,
            "info": InfoBar.info,
        }
        builder = builders.get(kind, InfoBar.info)
        builder(
            title="",
            content=message,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2600,
            parent=self,
        )

    def bring_to_front(self) -> None:
        self._show_window()

    def _show_window(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~self.windowState().__class__.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self._really_quit = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._really_quit and self._tray is not None:
            event.ignore()
            self.hide()
            if not self.config.tray_hint_shown:
                self._tray.notify(
                    APP_NAME,
                    "Continua rodando na bandeja. Para sair, clique com o botão direito no ícone e escolha Sair.",
                    warning=False,
                )
                self.config.tray_hint_shown = True
                self.config.save()
            return
        if self.engine.is_running:
            self.schedule(self.engine.stop())
        if self._tray is not None:
            self._tray.hide()
        QTimer.singleShot(300, QApplication.instance().quit)
        event.accept()


def QSystemTrayIcon_available() -> bool:
    from PyQt6.QtWidgets import QSystemTrayIcon

    return QSystemTrayIcon.isSystemTrayAvailable()
