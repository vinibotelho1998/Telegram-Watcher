from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from monitor import APP_NAME
from monitor.models import EngineState


class SystemTray(QSystemTrayIcon):
    def __init__(
        self,
        icon: QIcon,
        on_open: Callable[[], None],
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(icon, parent)
        self.setToolTip(APP_NAME)
        self._on_open = on_open

        menu = QMenu()
        open_action = QAction("Abrir", self)
        open_action.triggered.connect(on_open)
        self._toggle_action = QAction("Iniciar monitor", self)
        self._toggle_action.triggered.connect(on_toggle)
        quit_action = QAction("Sair", self)
        quit_action.triggered.connect(on_quit)
        menu.addAction(open_action)
        menu.addAction(self._toggle_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_open()

    def set_state(self, state: EngineState) -> None:
        running = state in (EngineState.ONLINE, EngineState.CONNECTING, EngineState.AUTH)
        self._toggle_action.setText("Parar monitor" if running else "Iniciar monitor")
        label = "Online" if state == EngineState.ONLINE else "Offline"
        self.setToolTip(f"{APP_NAME} — {label}")
