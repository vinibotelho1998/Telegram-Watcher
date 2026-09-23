from __future__ import annotations

import html
import re
from datetime import date, datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    SearchLineEdit,
    SimpleCardWidget,
    TogglePushButton,
    TransparentPushButton,
)

from watcher.detector import detect
from watcher.filtering import matching_filters
from watcher.models import ChannelKind
from watcher.reader import ChatMessage, ChatSummary, preview_text

from ..chips import FlowLayout
from ..common import (
    ElidedCaption,
    ElidedStrong,
    ElidedSubtitle,
    PageView,
    clear_layout,
    format_int,
    make_vscroll,
    plain,
)
from ..theme import ACCENT, PALETTE, TEXT, text_tone

OFFLINE_TEXT = "Monitor desligado. Inicie o monitor para ler esta conversa."

_PILL_TONES = {
    "filter": ("rgba(59, 130, 246, 0.18)", "rgba(59, 130, 246, 0.45)", "info"),
    "detect": ("rgba(56, 189, 248, 0.14)", "rgba(56, 189, 248, 0.40)", "capture"),
    "warning": ("rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.45)", "warning"),
    "muted": ("rgba(128, 138, 152, 0.12)", "rgba(128, 138, 152, 0.35)", "muted"),
}


def _pill(text: str, tone: str) -> QLabel:
    fundo, borda, chave = _PILL_TONES.get(tone, _PILL_TONES["muted"])
    cor = text_tone(chave)
    pill = plain(CaptionLabel(text))
    pill.setObjectName("markPill")
    pill.setStyleSheet(
        f"QLabel#markPill {{ background: {fundo}; border: 1px solid {borda}; border-radius: 9px;"
        f" padding: 1px 8px; color: {cor}; }}"
    )
    return pill

_PAGE_SIZE = 40
_CHAT_LIMIT = 200
_AVATAR_PRELOAD = 60

_AVATAR_COLORS = (
    "#E17076",
    "#7BC862",
    "#65AADD",
    "#A695E7",
    "#EE7AAE",
    "#FAA774",
    "#6EC9CB",
    "#F0A45C",
)

_WEEKDAYS = (
    "segunda",
    "terça",
    "quarta",
    "quinta",
    "sexta",
    "sábado",
    "domingo",
)

_MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

_KIND_LABEL = {
    ChannelKind.CHANNEL: "Canal",
    ChannelKind.GROUP: "Grupo",
    ChannelKind.USER: "Conversa",
}

_MEDIA_ICON = {
    "photo": "🖼",
    "video": "🎬",
    "voice": "🎙",
    "audio": "🎵",
    "sticker": "🩵",
    "gif": "🎞",
    "poll": "📊",
    "contact": "👤",
    "location": "📍",
    "file": "📎",
}

_SCOPES: list[tuple[str, str]] = [
    ("Todas as conversas", "all"),
    ("Somente origens", "sources"),
    ("Somente destinos", "destinations"),
    ("Não lidas", "unread"),
]

_URL = re.compile(r"((?:https?://|www\.|t\.me/)[^\s<>\"]+)")

_BUBBLE_MIN = 200
_BUBBLE_MAX = 560


def _local(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    try:
        return moment.astimezone()
    except (ValueError, OSError):
        return moment


def _clock(moment: datetime | None) -> str:
    moment = _local(moment)
    return moment.strftime("%H:%M") if moment else ""


def _day_label(day: date) -> str:
    today = date.today()
    delta = (today - day).days
    if delta == 0:
        return "Hoje"
    if delta == 1:
        return "Ontem"
    if delta < 7:
        return _WEEKDAYS[day.weekday()].capitalize()
    if day.year == today.year:
        return f"{day.day} de {_MONTHS[day.month - 1]}"
    return f"{day.day} de {_MONTHS[day.month - 1]} de {day.year}"


def _list_stamp(moment: datetime | None) -> str:
    moment = _local(moment)
    if moment is None:
        return ""
    delta = (date.today() - moment.date()).days
    if delta == 0:
        return moment.strftime("%H:%M")
    if delta == 1:
        return "ontem"
    if delta < 365:
        return moment.strftime("%d/%m")
    return moment.strftime("%m/%Y")


def _initials(name: str) -> str:
    words = [w for w in (name or "").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def _tone(chat_id: int) -> str:
    return _AVATAR_COLORS[abs(int(chat_id)) % len(_AVATAR_COLORS)]


def _rich(text: str) -> str:
    out: list[str] = []
    for index, chunk in enumerate(_URL.split(text or "")):
        if index % 2:
            href = chunk if chunk.startswith("http") else f"https://{chunk}"
            out.append(f'<a style="color:{ACCENT}" href="{html.escape(href, quote=True)}">{html.escape(chunk)}</a>')
        else:
            out.append(html.escape(chunk).replace("\n", "<br>"))
    return "".join(out)


class Avatar(QLabel):
    def __init__(self, size: int = 44, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._chat_id = 0
        self._name = ""

    def set_identity(self, chat_id: int, name: str) -> None:
        self._chat_id = chat_id
        self._name = name
        self.setPixmap(self._placeholder())

    def set_photo(self, path: str) -> None:
        source = QPixmap(path)
        if source.isNull():
            return
        side = min(source.width(), source.height())
        square = source.copy(
            (source.width() - side) // 2,
            (source.height() - side) // 2,
            side,
            side,
        ).scaled(
            self._size,
            self._size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QPixmap(self._size, self._size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addEllipse(0, 0, self._size, self._size)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, square)
        painter.end()
        self.setPixmap(canvas)

    def _placeholder(self) -> QPixmap:
        canvas = QPixmap(self._size, self._size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_tone(self._chat_id)))
        painter.drawEllipse(0, 0, self._size, self._size)
        font = QFont()
        font.setPointSize(max(8, int(self._size * 0.32)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, _initials(self._name))
        painter.end()
        return canvas


class _ChatRow(CardWidget):
    opened = pyqtSignal(int)

    def __init__(self, summary: ChatSummary, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.chat_id = summary.id
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 12, 8)
        layout.setSpacing(10)

        self._marker = QFrame()
        self._marker.setFixedWidth(3)
        self._marker.setStyleSheet(f"background: {ACCENT}; border-radius: 2px;")
        self._marker.setVisible(False)
        layout.addWidget(self._marker)

        self.avatar = Avatar(42)
        self.avatar.set_identity(summary.id, summary.name)
        layout.addWidget(self.avatar)

        middle = QVBoxLayout()
        middle.setSpacing(1)
        self._name = ElidedStrong(summary.name)
        middle.addWidget(self._name)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._preview = ElidedCaption(summary.preview or _KIND_LABEL.get(summary.kind, ""))
        self._preview.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        bottom.addWidget(self._preview, 1)
        if role:
            bottom.addWidget(_pill(role, "filter"))
        middle.addLayout(bottom)
        layout.addLayout(middle, 1)

        right = QVBoxLayout()
        right.setSpacing(2)
        right.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._stamp = CaptionLabel(_list_stamp(summary.date))
        self._stamp.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        self._stamp.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._stamp.setMinimumWidth(self._stamp.fontMetrics().horizontalAdvance("00/00") + 6)
        right.addWidget(self._stamp)
        self._badge = CaptionLabel("")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setObjectName("unreadBadge")
        self._badge.setStyleSheet(
            f"QLabel#unreadBadge {{ background: {ACCENT}; color: white; border-radius: 9px; padding: 0px 6px; }}"
        )
        right.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(right)

        self.set_unread(summary.unread)
        self.clicked.connect(lambda: self.opened.emit(self.chat_id))

    def set_unread(self, count: int) -> None:
        self._badge.setText(("99+" if count > 99 else str(count)) if count > 0 else "")
        self._badge.setVisible(count > 0)

    def set_selected(self, selected: bool) -> None:
        self._marker.setVisible(selected)
        if selected:
            self._name.setTextColor(QColor(ACCENT), QColor(ACCENT))
        else:
            self._name.setTextColor(QColor(TEXT[0]), QColor(TEXT[1]))

    def touch(self, message: ChatMessage) -> None:
        self._preview.setText(preview_text(message))
        self._stamp.setText(_list_stamp(message.date))


class _DaySeparator(QWidget):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        label = CaptionLabel(text)
        label.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)


class _Bubble(QWidget):
    def __init__(
        self,
        message: ChatMessage,
        show_sender: bool,
        marks: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.message_id = message.id
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("bubble")
        tint = "rgba(59, 130, 246, 0.16)" if message.outgoing else "rgba(128, 138, 152, 0.14)"
        replica = any(tone in ("filter", "warning") for _text, tone in marks)
        edge = f"border-left: 3px solid {ACCENT};" if replica else ""
        frame.setStyleSheet(
            "QFrame#bubble {"
            f" background: {tint};"
            " border-radius: 12px;"
            f" {edge}"
            " }"
        )
        frame.setMinimumWidth(self._ideal_width(message, marks))
        frame.setMaximumWidth(_BUBBLE_MAX)

        body = QVBoxLayout(frame)
        body.setContentsMargins(14, 9, 14, 8)
        body.setSpacing(4)

        if show_sender and message.sender_name and not message.outgoing:
            sender = plain(CaptionLabel(message.sender_name))
            sender.setTextColor(QColor(_tone(message.sender_id or message.chat_id)), QColor(_tone(message.sender_id or message.chat_id)))
            body.addWidget(sender)

        if marks:
            chips = QWidget()
            flow = FlowLayout(chips, spacing=4)
            for text, tone in marks:
                flow.addWidget(_pill(text, tone))
            body.addWidget(chips)

        self.thumb = QLabel()
        self.thumb.setVisible(False)
        body.addWidget(self.thumb)

        if message.media_kind:
            icon = _MEDIA_ICON.get(message.media_kind, "📎")
            media = plain(BodyLabel(f"{icon}  {message.media_label}"))
            media.setTextColor(QColor(PALETTE["capture"]), QColor(PALETTE["capture"]))
            body.addWidget(media)

        if message.text:
            text = BodyLabel()
            text.setText(_rich(message.text))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            text.setOpenExternalLinks(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            body.addWidget(text)
        elif message.service:
            service = CaptionLabel(message.service)
            service.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            body.addWidget(service)

        footer = CaptionLabel(self._footer(message))
        footer.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        body.addWidget(footer)

        if message.outgoing:
            outer.addStretch(1)
            outer.addWidget(frame)
        else:
            outer.addWidget(frame)
            outer.addStretch(1)

    @staticmethod
    def _ideal_width(message: ChatMessage, marks: list[tuple[str, str]]) -> int:
        metrics = QFontMetrics(QFont())
        lines = (message.text or "").splitlines()
        lines.append(message.media_label)
        lines.append("    ".join(text for text, _tone in marks))
        widest = max((metrics.horizontalAdvance(line) for line in lines if line), default=0)
        return max(_BUBBLE_MIN, min(_BUBBLE_MAX, widest + 34))

    @staticmethod
    def _footer(message: ChatMessage) -> str:
        parts = [_clock(message.date)]
        if message.views:
            parts.append(f"{format_int(message.views)} visualizações")
        if message.edited:
            parts.append("editada")
        return "  ·  ".join(p for p in parts if p)

    def set_thumbnail(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self.thumb.setPixmap(
            pixmap.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        )
        self.thumb.setVisible(True)


class MessagesPage(PageView):
    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__("messagesPage", "Conversas", parent=parent)
        self._app = app
        self._summaries: list[ChatSummary] = []
        self._rows: dict[int, _ChatRow] = {}
        self._avatars: dict[int, str] = {}
        self._bubbles: dict[int, _Bubble] = {}
        self._chat_id: int | None = None
        self._oldest_id: int | None = None
        self._last_day: date | None = None
        self._last_sender: int | None = None
        self._loading = False
        self._reload_pending = False
        self._stale = False
        self._exhausted = False
        self._searching = False
        self._loading_chats = False
        self._opened_offline = False

        self.scope = ComboBox()
        for label, _ in _SCOPES:
            self.scope.addItem(label)
        self.scope.setFixedWidth(190)
        self.scope.currentIndexChanged.connect(self._render_chats)

        self.reload_btn = TransparentPushButton("Atualizar")
        self.reload_btn.setIcon(FluentIcon.SYNC)
        self.reload_btn.clicked.connect(self.reload_chats)
        self.add_header_widget(self.scope)
        self.add_header_widget(self.reload_btn)

        split = QHBoxLayout()
        split.setSpacing(14)

        sidebar = SimpleCardWidget()
        sidebar.setMinimumWidth(240)
        sidebar.setMaximumWidth(330)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 12, 8, 12)
        side_layout.setSpacing(10)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("Buscar conversa")
        self.search.textChanged.connect(self._render_chats)
        side_layout.addWidget(self.search)
        chats_area, self._chat_list = make_vscroll()
        chats_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chat_list.setSpacing(4)
        side_layout.addWidget(chats_area, 1)
        split.addWidget(sidebar, 1)

        thread = SimpleCardWidget()
        thread_layout = QVBoxLayout(thread)
        thread_layout.setContentsMargins(18, 14, 12, 14)
        thread_layout.setSpacing(10)
        thread_layout.addWidget(self._build_thread_header())
        self._thread_area, self._thread = make_vscroll()
        self._thread_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thread.setSpacing(6)
        self._scrollbar = self._thread_area.verticalScrollBar()
        self._scrollbar.valueChanged.connect(self._on_scroll)
        thread_layout.addWidget(self._thread_area, 1)
        split.addWidget(thread, 2)

        self.add_body_layout(split, 1)
        self._reset_thread()
        self._show_placeholder("Escolha uma conversa para ler as mensagens.")
        self._render_chats()

    def _build_thread_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.thread_avatar = Avatar(40)
        self.thread_avatar.setVisible(False)
        layout.addWidget(self.thread_avatar)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.thread_title = ElidedSubtitle("Conversas")
        self.thread_subtitle = ElidedCaption("Somente leitura, nada é enviado por aqui")
        self.thread_subtitle.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        titles.addWidget(self.thread_title)
        titles.addWidget(self.thread_subtitle)
        layout.addLayout(titles, 2)

        self.thread_search = SearchLineEdit()
        self.thread_search.setPlaceholderText("Buscar mensagem")
        self.thread_search.setMinimumWidth(120)
        self.thread_search.setMaximumWidth(220)
        self.thread_search.setEnabled(False)
        self.thread_search.searchSignal.connect(self._run_search)
        self.thread_search.clearSignal.connect(self._exit_search)
        self.thread_search.textChanged.connect(self._on_search_text)
        layout.addWidget(self.thread_search, 1)

        self.as_source = TogglePushButton("Origem")
        self.as_source.setEnabled(False)
        self.as_source.clicked.connect(lambda on: self._set_role("source", on))
        self.as_destination = TogglePushButton("Destino")
        self.as_destination.setEnabled(False)
        self.as_destination.clicked.connect(lambda on: self._set_role("destination", on))
        layout.addWidget(self.as_source)
        layout.addWidget(self.as_destination)
        self._paint_roles()
        return header

    def _paint_roles(self) -> None:
        textos = {
            self.as_source: (
                "Esta conversa é origem: o monitor lê o que chega nela. Clique para parar de monitorar.",
                "Clique para monitorar esta conversa e replicar o que chega nela.",
            ),
            self.as_destination: (
                "Esta conversa é destino: recebe as mensagens replicadas. Clique para parar de enviar para ela.",
                "Clique para replicar mensagens para esta conversa.",
            ),
        }
        for button, (ligado, desligado) in textos.items():
            marcado = button.isChecked()
            button.setIcon(FluentIcon.ACCEPT if marcado else FluentIcon.ADD)
            button.setToolTip(ligado if marcado else desligado)

    def _set_role(self, role: str, enabled: bool) -> None:
        if self._chat_id is None:
            return
        self._app.set_channel_role(self._chat_id, self._chat_name(self._chat_id), role, enabled)
        self._app.channels_page.refresh()
        self._paint_roles()
        self._render_chats()
        self.refresh_marks()

    def _chat_name(self, chat_id: int) -> str:
        for summary in self._summaries:
            if summary.id == chat_id:
                return summary.name
        return self._app.config.channel_name(chat_id)

    def _role_of(self, chat_id: int) -> str:
        tags = []
        if chat_id in self._app.config.source_ids:
            tags.append("origem")
        if chat_id in self._app.config.destination_ids:
            tags.append("destino")
        return " · ".join(tags)

    def reload_chats(self) -> None:
        if not self._app.engine.reader.available:
            self._app.toast("Inicie o monitor para carregar as conversas", "warning")
            return
        self.reload_btn.setEnabled(False)
        self._loading_chats = True
        if not self._summaries:
            self._render_chats()
        self._app.schedule(self._fetch_chats())

    async def _fetch_chats(self) -> None:
        try:
            self._summaries = await self._app.engine.reader.dialogs(_CHAT_LIMIT)
        except Exception as exc:
            self._app.toast(f"Não foi possível carregar as conversas: {exc}", "error")
            return
        finally:
            self.reload_btn.setEnabled(True)
            self._loading_chats = False
        self._render_chats()
        self._app.schedule(self._fetch_avatars())
        if self._opened_offline and self._chat_id is not None:
            self.open_chat(self._chat_id)

    async def _fetch_avatars(self) -> None:
        for summary in self._summaries[:_AVATAR_PRELOAD]:
            row = self._rows.get(summary.id)
            if row is None:
                continue
            path = await self._app.engine.reader.avatar(summary.id)
            if path is None:
                continue
            self._avatars[summary.id] = str(path)
            if summary.id in self._rows:
                self._rows[summary.id].avatar.set_photo(str(path))
            if self._chat_id == summary.id:
                self.thread_avatar.set_photo(str(path))

    def _visible_chats(self) -> list[ChatSummary]:
        query = self.search.text().strip().lower()
        scope = _SCOPES[self.scope.currentIndex()][1]
        out: list[ChatSummary] = []
        for summary in self._summaries:
            if scope == "sources" and summary.id not in self._app.config.source_ids:
                continue
            if scope == "destinations" and summary.id not in self._app.config.destination_ids:
                continue
            if scope == "unread" and summary.unread <= 0:
                continue
            if query and query not in summary.name.lower() and query not in str(summary.id):
                continue
            out.append(summary)
        out.sort(key=lambda s: (not s.pinned, -(s.date.timestamp() if s.date else 0)))
        return out

    def _render_chats(self) -> None:
        clear_layout(self._chat_list)
        self._rows.clear()
        chats = self._visible_chats()
        for summary in chats:
            row = _ChatRow(summary, self._role_of(summary.id))
            row.opened.connect(self.open_chat)
            row.set_selected(summary.id == self._chat_id)
            cached = self._avatars.get(summary.id)
            if cached:
                row.avatar.set_photo(cached)
            self._chat_list.addWidget(row)
            self._rows[summary.id] = row
        if not chats:
            if self._summaries:
                texto = "Nenhuma conversa com esse critério."
            elif self._loading_chats:
                texto = "Carregando conversas..."
            else:
                texto = "Nenhuma conversa carregada. Inicie o monitor e clique em Atualizar."
            hint = CaptionLabel(texto)
            hint.setWordWrap(True)
            hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
            self._chat_list.addWidget(hint)
        self._chat_list.addStretch(1)

    def open_chat(self, chat_id: int) -> None:
        self._chat_id = chat_id
        self._oldest_id = None
        self._exhausted = False
        self._searching = False
        self._last_day = None
        self._last_sender = None
        self._reset_thread()

        name = self._chat_name(chat_id)
        self.thread_title.setText(name)
        self.thread_avatar.setVisible(True)
        self.thread_avatar.set_identity(chat_id, name)
        cached = self._avatars.get(chat_id)
        if cached:
            self.thread_avatar.set_photo(cached)
        self.thread_search.clear()
        self.as_source.setEnabled(True)
        self.as_destination.setEnabled(True)
        self.as_source.setChecked(chat_id in self._app.config.source_ids)
        self.as_destination.setChecked(chat_id in self._app.config.destination_ids)
        self._paint_roles()
        for summary in self._summaries:
            if summary.id == chat_id:
                summary.unread = 0
        for cid, row in self._rows.items():
            row.set_selected(cid == chat_id)
            if cid == chat_id:
                row.set_unread(0)
        self._describe(chat_id)
        available = self._app.engine.reader.available
        self._opened_offline = not available
        self.thread_search.setEnabled(available)
        if not available:
            self._show_placeholder(OFFLINE_TEXT)
            return
        self._show_placeholder("Carregando mensagens...")
        self._app.schedule(self._fetch_history(initial=True))
        self._app.schedule(self._fetch_avatar(chat_id))

    async def _fetch_avatar(self, chat_id: int) -> None:
        path = await self._app.engine.reader.avatar(chat_id)
        if path is None:
            return
        self._avatars[chat_id] = str(path)
        if self._chat_id == chat_id:
            self.thread_avatar.set_photo(str(path))

    def _describe(self, chat_id: int) -> None:
        summary = next((s for s in self._summaries if s.id == chat_id), None)
        parts: list[str] = []
        if summary is not None:
            parts.append(_KIND_LABEL.get(summary.kind, "Conversa"))
            if summary.username:
                parts.append(f"@{summary.username}")
            if summary.participants:
                parts.append(f"{format_int(summary.participants)} membros")
        parts.append(f"ID {chat_id}")
        self.thread_subtitle.setText("  ·  ".join(parts))

    async def _fetch_history(self, initial: bool = False) -> None:
        chat_id = self._chat_id
        if chat_id is None:
            return
        if self._loading:
            self._reload_pending = self._reload_pending or initial
            return
        self._loading = True
        try:
            await self._load_history(chat_id, initial)
        finally:
            self._loading = False
        if self._reload_pending:
            self._reload_pending = False
            self._app.schedule(self._fetch_history(initial=True))

    async def _load_history(self, chat_id: int, initial: bool) -> None:
        anchor = self._scrollbar.maximum() - self._scrollbar.value()
        before_id = None if initial else self._oldest_id
        try:
            messages = await self._app.engine.reader.history(chat_id, _PAGE_SIZE, before_id)
        except Exception as exc:
            if self._chat_id == chat_id:
                self._show_placeholder(f"Não foi possível ler esta conversa: {exc}")
            return
        if self._chat_id != chat_id:
            return
        if initial:
            self._reset_thread()
            self._exhausted = False
        if not messages:
            if not self._app.engine.reader.available:
                self._opened_offline = True
                if initial:
                    self._show_placeholder(OFFLINE_TEXT)
                return
            self._exhausted = True
            if initial:
                self._show_placeholder("Nenhuma mensagem nesta conversa.")
            return
        self._oldest_id = messages[0].id
        if initial:
            self._last_day = None
            self._last_sender = None
            for message in messages:
                self._append(message)
            self._scroll_to_end()
        else:
            self._prepend(messages, anchor)

    def _prepend(self, messages: list[ChatMessage], anchor: int) -> None:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        previous_day: date | None = None
        previous_sender: int | None = None
        for message in messages:
            day = _local(message.date).date() if message.date else None
            if day is not None and day != previous_day:
                layout.addWidget(_DaySeparator(_day_label(day)))
                previous_day = day
                previous_sender = None
            bubble = self._make_bubble(message, message.sender_id != previous_sender)
            previous_sender = message.sender_id
            layout.addWidget(bubble)
        self._thread.insertWidget(0, block)
        self._thread_area.widget().adjustSize()
        self._scrollbar.setValue(max(0, self._scrollbar.maximum() - anchor))

    def _make_bubble(self, message: ChatMessage, show_sender: bool) -> _Bubble:
        bubble = _Bubble(message, show_sender, self._marks(message))
        self._bubbles[message.id] = bubble
        if message.media_kind in ("photo", "video", "gif", "sticker"):
            self._app.schedule(self._fetch_thumbnail(message.chat_id, message.id))
        return bubble

    async def _fetch_thumbnail(self, chat_id: int, message_id: int) -> None:
        path = await self._app.engine.reader.thumbnail(chat_id, message_id)
        bubble = self._bubbles.get(message_id)
        if path is not None and bubble is not None and self._chat_id == chat_id:
            bubble.set_thumbnail(str(path))

    def _marks(self, message: ChatMessage) -> list[tuple[str, str]]:
        if not message.text:
            return []
        config = self._app.config
        filters = self._app.filters
        is_source = message.chat_id in config.source_ids
        marcados = [d for d in config.destination_ids if d != message.chat_id]
        marks: list[tuple[str, str]] = []
        if is_source and not any(f.enabled for f in filters):
            if marcados:
                marks.append(("Replicaria: nenhum filtro ativo", "warning"))
        else:
            for flt in matching_filters(filters, message.chat_id, message.text):
                if not is_source:
                    marks.append((f"Casaria no filtro {flt.name}, mas o canal não é origem", "muted"))
                elif not [d for d in (flt.destination_ids or marcados) if d in marcados]:
                    marks.append((f"Filtro {flt.name}, sem destino", "muted"))
                else:
                    marks.append((f"Filtro {flt.name}", "filter"))
        detection = detect(message.text)
        if detection.has_offer:
            marks.append((detection.label, "detect"))
            if detection.store_label:
                marks.append((detection.store_label, "detect"))
            coupon = detection.best_coupon
            if coupon is not None and coupon.confidence > 0.5:
                marks.append((coupon.code, "detect"))
        return marks

    def _append(self, message: ChatMessage) -> None:
        day = _local(message.date).date() if message.date else None
        if day is not None and day != self._last_day:
            self._add_to_thread(_DaySeparator(_day_label(day)))
            self._last_day = day
            self._last_sender = None
        bubble = self._make_bubble(message, message.sender_id != self._last_sender)
        self._last_sender = message.sender_id
        self._add_to_thread(bubble)

    def _reset_thread(self) -> None:
        clear_layout(self._thread)
        self._bubbles.clear()
        self._thread.addStretch(1)

    def _add_to_thread(self, widget: QWidget) -> None:
        self._thread.insertWidget(max(0, self._thread.count() - 1), widget)

    def _show_placeholder(self, text: str) -> None:
        self._reset_thread()
        hint = CaptionLabel(text)
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 60, 0, 0)
        layout.addWidget(hint)
        self._add_to_thread(holder)

    def _scroll_to_end(self) -> None:
        self._thread_area.widget().adjustSize()
        self._scrollbar.setValue(self._scrollbar.maximum())
        QTimer.singleShot(0, self._pin_to_end)
        QTimer.singleShot(90, self._pin_to_end)

    def _pin_to_end(self) -> None:
        self._scrollbar.setValue(self._scrollbar.maximum())

    def _on_scroll(self, value: int) -> None:
        if value > 60 or self._searching or self._exhausted or self._loading:
            return
        if self._chat_id is None or self._oldest_id is None:
            return
        self._app.schedule(self._fetch_history())

    def _on_search_text(self, text: str) -> None:
        if not text.strip():
            self._exit_search()

    def _run_search(self, query: str) -> None:
        if self._chat_id is None:
            return
        if not query.strip():
            self._exit_search()
            return
        if not self._app.engine.reader.available:
            self._show_placeholder(OFFLINE_TEXT)
            return
        self._searching = True
        self._app.schedule(self._fetch_search(query))

    async def _fetch_search(self, query: str) -> None:
        chat_id = self._chat_id
        if chat_id is None:
            return
        try:
            results = await self._app.engine.reader.search(chat_id, query)
        except Exception as exc:
            if self._chat_id == chat_id:
                self._show_placeholder(f"Não foi possível buscar nesta conversa: {exc}")
            return
        if self._chat_id != chat_id:
            return
        self._reset_thread()
        if not results:
            self._show_placeholder(f"Nada encontrado para “{query}”.")
            return
        header = CaptionLabel(f"{len(results)} resultados para “{query}”. Limpe a busca para voltar.")
        header.setTextColor(QColor(PALETTE["muted"]), QColor(PALETTE["muted"]))
        self._add_to_thread(header)
        self._last_day = None
        self._last_sender = None
        for message in results:
            self._append(message)
        QTimer.singleShot(0, lambda: self._scrollbar.setValue(0))

    def _exit_search(self) -> None:
        if not self._searching:
            return
        self._searching = False
        if self._chat_id is not None:
            self._oldest_id = None
            self._exhausted = False
            self._app.schedule(self._fetch_history(initial=True))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._stale and self._app.engine.reader.available:
            self._stale = False
            self.refresh_marks()

    def refresh_marks(self) -> None:
        if self._chat_id is None or self._searching or not self._app.engine.reader.available:
            return
        self._oldest_id = None
        self._exhausted = False
        self._app.schedule(self._fetch_history(initial=True))

    def on_incoming(self, chat_id: int, message: ChatMessage) -> None:
        summary = next((s for s in self._summaries if s.id == chat_id), None)
        if summary is not None:
            summary.preview = preview_text(message)
            summary.date = message.date
            if chat_id != self._chat_id and not message.outgoing:
                summary.unread += 1
        row = self._rows.get(chat_id)
        if row is not None:
            row.touch(message)
            if summary is not None:
                row.set_unread(summary.unread)
        if chat_id != self._chat_id or self._searching:
            return
        if not self.isVisible():
            self._stale = True
            return
        at_end = self._scrollbar.value() >= self._scrollbar.maximum() - 80
        self._append(message)
        if at_end:
            self._scroll_to_end()
