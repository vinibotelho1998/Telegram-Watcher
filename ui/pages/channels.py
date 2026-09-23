from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentToolButton,
)

from watcher.models import Channel

from ..common import PageView, clear_layout, make_vscroll, plain
from ..theme import PALETTE

_RENDER_LIMIT = 200


class _ChannelRow(CardWidget):
    def __init__(self, channel: Channel, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(1)
        info.addWidget(plain(StrongBodyLabel(channel.name or "Sem nome")))
        meta = CaptionLabel(f"{channel.kind.value}  ·  {channel.id}")
        meta.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        info.addWidget(meta)
        layout.addLayout(info, 1)

        open_btn = TransparentToolButton(FluentIcon.CHAT)
        open_btn.setToolTip("Ler as mensagens desta conversa")
        open_btn.clicked.connect(lambda: app.open_conversation(channel.id))
        layout.addWidget(open_btn)

        source = CheckBox("Origem")
        source.setChecked(channel.id in app.config.source_ids)
        source.toggled.connect(lambda on: app.set_channel_role(channel.id, channel.name, "source", on))
        destination = CheckBox("Destino")
        destination.setChecked(channel.id in app.config.destination_ids)
        destination.toggled.connect(lambda on: app.set_channel_role(channel.id, channel.name, "destination", on))
        layout.addWidget(source)
        layout.addWidget(destination)


class ChannelsPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("channelsPage", "Canais", parent=parent)
        self._app = app

        self.search = SearchLineEdit()
        self.search.setPlaceholderText("Buscar canal")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.refresh)
        self.sync_btn = PrimaryPushButton("Sincronizar")
        self.sync_btn.setIcon(FluentIcon.SYNC)
        self.sync_btn.clicked.connect(app.request_sync)
        self.add_header_widget(self.search)
        self.add_header_widget(self.sync_btn)

        area, self._list = make_vscroll()
        self.add_body(area, 1)
        self.refresh()

    def set_syncing(self, syncing: bool) -> None:
        self.sync_btn.setEnabled(not syncing)
        self.sync_btn.setText("Sincronizando..." if syncing else "Sincronizar")

    def _display_channels(self) -> list[Channel]:
        seen: set[int] = set()
        merged: list[Channel] = []
        for cid in (*self._app.config.source_ids, *self._app.config.destination_ids):
            if cid not in seen:
                merged.append(Channel(id=cid, name=self._app.config.channel_name(cid)))
                seen.add(cid)
        for channel in self._app.channels:
            if channel.id not in seen:
                merged.append(channel)
                seen.add(channel.id)
        return merged

    def refresh(self) -> None:
        clear_layout(self._list)
        query = self.search.text().strip().lower()
        channels = self._display_channels()
        shown = 0
        for channel in channels:
            if query and query not in channel.name.lower() and query not in str(channel.id):
                continue
            self._list.addWidget(_ChannelRow(channel, self._app))
            shown += 1
            if shown >= _RENDER_LIMIT:
                break

        if shown == 0:
            empty = QWidget()
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(0, 80, 0, 0)
            title = SubtitleLabel("Nenhum canal encontrado")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint = CaptionLabel("Conecte o monitor e clique em Sincronizar para listar seus canais.")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            layout.addWidget(title)
            layout.addWidget(hint)
            self._list.addWidget(empty)
        self._list.addStretch(1)
