from __future__ import annotations

import asyncio
import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
)

from monitor import APP_ID, APP_NAME, VERSION, paths
from monitor.autobuy.manager import AutobuyManager
from monitor.autobuy.models import BuyResult, BuyStatus, WatchItem
from monitor.autobuy.notify import build_message, send_pix_notification
from monitor.config import AppConfig
from monitor.engine import MonitorEngine
from monitor.models import EngineState, Filter, LogLevel
from monitor.storage import ChannelCache, FilterStore

from .dialogs import CodeDialog, FilterDialog, PasswordDialog, WatchDialog
from .pages import AutobuyPage, ChannelsPage, ConsolePage, DashboardPage, FiltersPage, SettingsPage
from .setup_wizard import SetupWizard
from .theme import apply_theme
from .tray import SystemTray

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


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
            code_provider=self._request_code,
            password_provider=self._request_password,
        )

        self._icon = self._load_icon()
        self._init_window()
        self._init_pages()
        self._tray = self._init_tray()

        if self.config.is_ready:
            if self.config.auto_start:
                QTimer.singleShot(900, self.toggle_engine)
        else:
            QTimer.singleShot(500, self.open_setup_wizard)

        QTimer.singleShot(1500, self.autobuy_page.check_all_stores)

    def _load_icon(self) -> QIcon:
        icon_path = paths.assets_path("final_icon.ico")
        return QIcon(str(icon_path)) if icon_path.exists() else QIcon()

    def _init_window(self) -> None:
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.setWindowIcon(self._icon)
        self.resize(1180, 800)
        self.navigationInterface.setExpandWidth(240)
        self.navigationInterface.expand(useAni=False)
        try:
            self.setMicaEffectEnabled(True)
        except Exception:
            pass

    def _init_pages(self) -> None:
        self.dashboard = DashboardPage(self)
        self.filters_page = FiltersPage(self)
        self.channels_page = ChannelsPage(self)
        self.autobuy_page = AutobuyPage(self)
        self.console_page = ConsolePage(self)
        self.settings_page = SettingsPage(self)

        self.addSubInterface(self.dashboard, FluentIcon.HOME, "Dashboard")
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

    def schedule(self, coro):
        return self._loop.create_task(coro)

    def open_setup_wizard(self) -> None:
        SetupWizard(self, self).exec()

    async def _request_code(self, phone: str) -> str | None:
        dialog = CodeDialog(phone, self)
        return await self._await_dialog(dialog, dialog.code)

    async def _request_password(self) -> str | None:
        dialog = PasswordDialog(self)
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
        self.schedule(self.engine.notify_me(f"[DBX] {aviso}"))

    def _on_autobuy_alert(self, message: str) -> None:
        self.alert(message)
        self.schedule(self.engine.notify_me(f"[DBX] {message}"))

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

    async def _on_autobuy_result(self, result: BuyResult) -> None:
        message = build_message(result)
        sent = await self.engine.notify_me(message, result.screenshot or None)
        if not sent:
            await send_pix_notification(self.config, result)
        self.autobuy_page.refresh()
        if result.status == BuyStatus.PIX_READY:
            self._toast("PIX gerado! Confira a aba Auto-Compra", "success")

    def open_watch_editor(self, item: WatchItem | None) -> None:
        dialog = WatchDialog(item, self)
        if not dialog.exec():
            return
        self.autobuy.upsert_item(dialog.result_item())
        self.autobuy_page.refresh()
        self._toast("Produto salvo", "success")

    def delete_watch_item(self, item_id: str) -> None:
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
        self._toast("Filtro salvo", "success")

    def delete_filter(self, filter_id: str) -> None:
        self.filters = [f for f in self.filters if f.id != filter_id]
        self.filter_store.save(self.filters)
        self.filters_page.refresh()
        self._toast("Filtro removido", "info")

    def toggle_filter(self, filter_id: str, enabled: bool) -> None:
        for flt in self.filters:
            if flt.id == filter_id:
                flt.enabled = enabled
        self.filter_store.save(self.filters)

    def set_channel_role(self, chat_id: int, name: str, role: str, enabled: bool) -> None:
        target = self.config.source_ids if role == "source" else self.config.destination_ids
        if enabled and chat_id not in target:
            target.append(chat_id)
        elif not enabled and chat_id in target:
            target.remove(chat_id)
        if enabled and name and chat_id not in self.config.aliases:
            self.config.aliases[chat_id] = name
        elif not enabled and chat_id not in self.config.source_ids and chat_id not in self.config.destination_ids:
            self.config.aliases.pop(chat_id, None)
        self.config.save()

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
        finally:
            self.channels_page.set_syncing(False)

    def save_settings(self, payload: dict, silent: bool = False) -> None:
        raw_id = str(payload.get("api_id", "")).strip()
        try:
            self.config.api_id = int(raw_id) if raw_id else 0
        except ValueError:
            self.config.api_id = 0
        self.config.api_hash = str(payload.get("api_hash", "")).strip()
        self.config.bot_token = str(payload.get("bot_token", "")).strip()
        self.config.phone = str(payload.get("phone", "")).strip()
        self.config.allow_self = bool(payload.get("allow_self"))
        self.config.auto_start = bool(payload.get("auto_start"))
        saved = self.config.save()
        if not silent:
            self._toast("Configurações salvas" if saved else "Não foi possível salvar", "success" if saved else "error")
        self.channels_page.refresh()

    def toggle_theme(self, dark: bool) -> None:
        apply_theme(dark)

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
            return f'"{sys.executable}"'
        script = paths.resource_path("app.py")
        launcher = sys.executable.replace("python.exe", "pythonw.exe")
        return f'"{launcher}" "{script}"'

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
