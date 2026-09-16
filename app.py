from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import qasync
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from watcher import APP_ID, APP_NAME, paths
from ui.theme import apply_theme
from ui.window import AppWindow


def _setup_logging() -> None:
    handler = RotatingFileHandler(
        paths.logs_dir() / "watcher.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"))
    root = logging.getLogger("watcher")
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _set_app_id() -> None:
    if os.name == "nt":
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


_INSTANCE_KEY = "TelegramWatcher.singleton"


def _another_instance_running() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_INSTANCE_KEY)
    if socket.waitForConnected(250):
        socket.write(b"show")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def _listen_for_instances(window: AppWindow) -> QLocalServer:
    QLocalServer.removeServer(_INSTANCE_KEY)
    server = QLocalServer()
    server.listen(_INSTANCE_KEY)

    def on_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.close()
        window.bring_to_front()

    server.newConnection.connect(on_connection)
    return server


def main() -> int:
    _setup_logging()
    _set_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if _another_instance_running():
        return 0

    apply_theme(dark=True)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = AppWindow(loop)
    window.instance_server = _listen_for_instances(window)
    window.show()

    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
