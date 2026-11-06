"""Abre a janela e o assistente de primeiro acesso com um perfil descartável."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def smoke() -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import app
    from ui.setup_wizard import SetupWizard

    verified = False

    class SmokeWindow(app.AppWindow):
        def __init__(self, *args):
            super().__init__(*args)
            QTimer.singleShot(10000, lambda: QApplication.exit(2))
            QTimer.singleShot(1600, self.inspect)

        def inspect(self):
            nonlocal verified
            wizard = next((w for w in QApplication.allWidgets()
                           if isinstance(w, SetupWizard) and w.isVisible()), None)
            verified = self.isVisible() and wizard is not None
            print(f"{QApplication.platformName()} | {self.windowTitle()} | assistente visível: {verified}", flush=True)
            if wizard:
                wizard.reject()
            QTimer.singleShot(0, self._quit)

    app.AppWindow = SmokeWindow
    # O teste nunca deve focar uma instância pessoal aberta.
    app._INSTANCE_KEY = f"watcher-smoke-{os.getpid()}"
    return app.main() or int(not verified)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(HOME=directory, XDG_CONFIG_HOME=directory + "/config",
                          XDG_DATA_HOME=directory + "/data")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.exit(smoke())
