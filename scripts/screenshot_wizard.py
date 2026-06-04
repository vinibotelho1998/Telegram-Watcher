from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.setup_wizard import SetupWizard
from ui.theme import apply_theme

OUT = Path(__file__).resolve().parent.parent / ".screens"
OUT.mkdir(exist_ok=True)


def main() -> int:
    app = QApplication(sys.argv)
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{name}")
    app.setFont(QFont("Segoe UI", 10))
    apply_theme(dark=True)

    host = QWidget()
    host.setStyleSheet("background: #1b1b1b;")
    host.resize(1100, 760)
    host.show()
    QTest.qWait(120)

    wizard = SetupWizard(object(), host)
    wizard.show()
    QTest.qWait(400)
    host.grab().save(str(OUT / "wizard_0.png"))

    wizard._on_next()
    QTest.qWait(300)
    wizard.api_id.setText("12345678")
    wizard.api_hash.setText("a1b2c3d4e5f6a7b8")
    QTest.qWait(150)
    host.grab().save(str(OUT / "wizard_1.png"))

    wizard._on_next()
    QTest.qWait(300)
    wizard.phone.setText("+55 11 99999-9999")
    QTest.qWait(150)
    host.grab().save(str(OUT / "wizard_2.png"))

    wizard._on_next()
    QTest.qWait(300)
    host.grab().save(str(OUT / "wizard_3.png"))
    print("WIZARD SHOTS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
