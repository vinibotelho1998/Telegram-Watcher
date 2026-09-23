from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
)

from watcher.models import EngineState

from ..common import PageView, make_vscroll, plain
from ..theme import PALETTE
from ..widgets import ActivityChart, StatCard, StatusPill

_FEED_LIMIT = 9
_STATUS_KEY = {"captured": "capture", "forwarded": "success", "failed": "danger"}


class _FeedRow(QWidget):
    def __init__(self, data: dict, on_open=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chat_id = data.get("chat_id")
        self._on_open = on_open
        if self._chat_id and on_open is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Abrir esta conversa")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        key = _STATUS_KEY.get(data.get("status", "captured"), "muted")
        dot.setStyleSheet(f"background: {PALETTE[key]}; border-radius: 4px;")

        time_label = CaptionLabel(data.get("time", ""))
        time_label.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        source = plain(StrongBodyLabel(str(data.get("source", ""))))
        text = plain(BodyLabel(str(data.get("text", ""))))
        text.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))

        layout.addWidget(dot)
        layout.addWidget(time_label)
        layout.addWidget(source)
        layout.addWidget(text, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._chat_id and self._on_open is not None:
            self._on_open(int(self._chat_id))
        super().mousePressEvent(event)


class DashboardPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("dashboardPage", "Dashboard", parent=parent)
        self._app = app
        self._events = 0

        self.status_pill = StatusPill()
        self.account_btn = PushButton("Conectar conta")
        self.account_btn.setIcon(FluentIcon.PEOPLE)
        self.account_btn.clicked.connect(app.open_setup_wizard)
        self.toggle_btn = PrimaryPushButton("Iniciar monitor")
        self.toggle_btn.setIcon(FluentIcon.PLAY)
        self.toggle_btn.clicked.connect(app.toggle_engine)
        self.add_header_widget(self.status_pill)
        self.add_header_widget(self.account_btn)
        self.add_header_widget(self.toggle_btn)

        area, body = make_vscroll()
        self.add_body(area, 1)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.card_captured = StatCard("Capturadas", "info")
        self.card_sent = StatCard("Enviadas", "success")
        self.card_filtered = StatCard("Filtradas", "muted")
        self.card_failed = StatCard("Falhas", "danger")
        for index, card in enumerate((self.card_captured, self.card_sent, self.card_filtered, self.card_failed)):
            cards.addWidget(card, 0, index)
        body.addLayout(cards)

        chart_card = SimpleCardWidget()
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(20, 16, 20, 14)
        chart_layout.setSpacing(10)
        chart_layout.addWidget(StrongBodyLabel("Atividade nas últimas 24 horas"))
        self.chart = ActivityChart()
        chart_layout.addWidget(self.chart)
        body.addWidget(chart_card)

        feed_card = SimpleCardWidget()
        feed_layout = QVBoxLayout(feed_card)
        feed_layout.setContentsMargins(20, 16, 20, 18)
        feed_layout.setSpacing(10)
        feed_header = QHBoxLayout()
        feed_header.addWidget(StrongBodyLabel("Atividade recente"))
        feed_header.addStretch(1)
        self.feed_count = CaptionLabel("0 eventos")
        feed_header.addWidget(self.feed_count)
        feed_layout.addLayout(feed_header)
        self._feed = QVBoxLayout()
        self._feed.setSpacing(8)
        feed_layout.addLayout(self._feed)
        self._empty = CaptionLabel("Nenhuma atividade ainda. Inicie o monitor para começar.")
        self._feed.addWidget(self._empty)
        body.addWidget(feed_card)
        body.addStretch(1)

        self.refresh()
        self.set_state(EngineState.OFFLINE)

    def set_state(self, state: EngineState) -> None:
        self.status_pill.set_state(state)
        running = state in (EngineState.ONLINE, EngineState.CONNECTING, EngineState.AUTH, EngineState.STOPPING)
        self.toggle_btn.setText("Parar monitor" if running else "Iniciar monitor")
        self.toggle_btn.setIcon(FluentIcon.PAUSE if running else FluentIcon.PLAY)
        self.toggle_btn.setEnabled(state not in (EngineState.CONNECTING, EngineState.AUTH, EngineState.STOPPING))

    def refresh(self) -> None:
        metrics = self._app.engine.metrics
        self.card_captured.set_value(metrics.received)
        self.card_sent.set_value(metrics.forwarded)
        self.card_filtered.set_value(metrics.filtered)
        self.card_failed.set_value(metrics.failed)
        self.chart.set_data(metrics.by_hour)

    def add_event(self, data: dict) -> None:
        if self._empty is not None:
            self._empty.setParent(None)
            self._empty.deleteLater()
            self._empty = None
        self._feed.insertWidget(0, _FeedRow(data, self._app.open_conversation))
        self._events += 1
        self.feed_count.setText(f"{self._events} eventos")
        while self._feed.count() > _FEED_LIMIT:
            item = self._feed.takeAt(self._feed.count() - 1)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
