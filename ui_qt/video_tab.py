"""Video tab: paste a link, pick a resolution or format, download. Same core-layer
calls (app/core/downloader.py, ffmpeg_utils.py, size_estimate.py), ported
from CustomTkinter widgets/threading-via-.after() to Qt widgets/threading-
via-signals. Every non-UI method (range math, choice-list rebuilding,
progress-hook shape) is copied as-is from the CTk version; only the widget
glue changed.
"""
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QRadioButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app import config
from app.core import link_router
from app.core import downloader, errors, ffmpeg_utils, size_estimate
from app.logging_setup import get_logger
from app.utils import (
    download_history, download_queue_state, formatting,
    settings as settings_store, video_queue_state,
)

from . import cinema, theme
from . import motion
from .widgets import Chip, RoundedImage, centered_column, make_card, section_label
from .widgets.button import Button

logger = get_logger("video_tab")

# The page is a centred column no wider than this -- see centered_column().
# It was 1080, which kept a maximized window's pages a narrow strip down the
# middle of the screen ("maximized, the UI stays small"); 1760 fills a
# 1920 px screen and still stops lines running across an ultrawide.
MAX_CONTENT_W = 1760


def _link_icon(color, size=16):
    """Two interlocked rounded links -- the leading glyph in the URL field."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for dx in (-2.4, 2.4):
        p.save()
        p.translate(size / 2.0 + dx, size / 2.0 - dx)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-4.6, -2.4, 9.2, 4.8), 2.4, 2.4)
        p.restore()
    p.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def _close_glyph(color, size=10):
    """Small vector X for the queue card's remove button -- drawn rather
    than typed, so it cannot fall foul of the bundled font's glyph coverage
    the way a literal multiplication sign would.

    Size is even, and the button it goes in is 22px, because a 9px glyph in
    a 22px box has no integer centre -- Qt has to round, and the X sat 2px
    up and to the left of true centre. The endpoints are QPointF as well:
    int()-truncating them made the two strokes span different distances, so
    the cross was lopsided before it was even placed."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    m = size * 0.2
    far = size - m
    painter.drawLine(QPointF(m, m), QPointF(far, far))
    painter.drawLine(QPointF(far, m), QPointF(m, far))
    painter.end()
    return QIcon(pixmap)


class _QueueCard(QFrame):
    """One stacked link, as a card with a thumbnail.

    This replaced two earlier shapes and sits deliberately between them. The
    first was a full-height row carrying its own thumbnail *and* its own mode
    radios, format combo and clip controls -- three of them were taller than
    the form that created them and read as clutter. The second was a bare
    34px text line, which was clean but gave you nothing to recognise a link
    by once six were stacked up.

    So: a thumbnail and a title to recognise it by, channel and duration
    underneath, and exactly one control -- resolution -- because that is the
    only setting that genuinely differs per link (each offers a different
    ladder of heights). Everything else stays on the form above.
    """

    download_requested = Signal(object)
    remove_requested = Signal(object)
    selected = Signal(object)

    HEIGHT = 62
    THUMB_W = 80
    THUMB_H = 45

    def __init__(self, payload, preferred=None, parent=None):
        super().__init__(parent)
        self.payload = payload
        # Settings > Preferred quality, as a height, or None for "best".
        self._preferred = preferred
        self._pending = False
        self._selected = False
        self._heights = []
        self._full_title = ""
        self._full_meta = ""
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("queueCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 8, 10, 8)
        lay.setSpacing(12)

        self.thumb_label = RoundedImage(self.THUMB_W, self.THUMB_H, radius=7.0)
        lay.addWidget(self.thumb_label, 0)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-weight: 500;")
        # A long title must not push the controls off the right edge, so the
        # label is free to shrink and elides its text instead of growing.
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Preferred)
        text_col.addWidget(self.title_label)
        self.meta_label = QLabel()
        self.meta_label.setObjectName("muted")
        self.meta_label.setStyleSheet(
            "font-family: %s; font-size: 10px; letter-spacing: 0.3px;" % theme.MONO_STACK)
        self.meta_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                      QSizePolicy.Policy.Preferred)
        text_col.addWidget(self.meta_label)
        lay.addLayout(text_col, 1)

        self.res_combo = QComboBox()
        self.res_combo.setObjectName("queueCombo")
        self.res_combo.setFixedWidth(92)
        self.res_combo.setCursor(Qt.PointingHandCursor)
        self.res_combo.currentIndexChanged.connect(self._on_res_changed)
        self.res_combo.setVisible(False)
        lay.addWidget(self.res_combo, 0)

        self.start_btn = Button("Download")
        self.start_btn.setObjectName("accent")
        self.start_btn.setFixedHeight(28)
        self.start_btn.setStyleSheet("padding: 0px 14px;")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(lambda: self.download_requested.emit(self))
        lay.addWidget(self.start_btn, 0)

        # Drawn glyph, and padding reset to 0: the shared #quiet style carries
        # 4px/12px padding for normal text buttons, which on a 22px square
        # squeezes a label clean out of view (it rendered as an empty circle).
        self.remove_btn = Button()
        self.remove_btn.setObjectName("quiet")
        self.remove_btn.setIcon(_close_glyph("#a9b4c9"))
        self.remove_btn.setIconSize(QSize(10, 10))
        self.remove_btn.setStyleSheet("padding: 0px; border-radius: 11px;")
        self.remove_btn.setFixedSize(22, 22)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setToolTip("Remove from queue")
        self.remove_btn.setAccessibleName("Remove from queue")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        lay.addWidget(self.remove_btn, 0)

        self.setCursor(Qt.PointingHandCursor)
        self._set_title(payload["title"] or payload["url"])
        self._refresh_meta()

    def mousePressEvent(self, event):
        """Selecting a card loads its link back into the form. The child
        buttons and the combo consume their own presses before this runs, so
        hitting Download or the remove glyph does not also select."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self)
        super().mousePressEvent(event)

    def set_selected(self, on):
        self._selected = bool(on)
        self.setProperty("selected", "true" if on else "false")
        self.update()

    def paintEvent(self, event):
        """A raised pane on the queue's glass. Painted, not QSS: a QSS
        radius isn't antialiased, and this row has no border to hide the
        stair-stepped corners behind. The row that is loaded in the form
        above carries a thin line of the brand blue -- the same mark the nav
        uses for "this is the one you're looking at"."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = cinema.is_dark(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = self.underMouse()
        if dark:
            fill = QColor(255, 255, 255, 20 if (hovered or self._selected) else 11)
            top, bottom = QColor(255, 255, 255, 34 if hovered else 20), QColor(255, 255, 255, 6)
        else:
            fill = QColor(255, 255, 255, 215 if (hovered or self._selected) else 150)
            top, bottom = QColor(255, 255, 255, 255), QColor(15, 23, 42, 26 if hovered else 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 12, 12)
        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        edge.setColorAt(0.0, top)
        edge.setColorAt(1.0, bottom)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 12, 12)
        if self._selected:
            brand = theme.qcolor(theme.tokens(dark)["brand"])
            painter.setPen(QPen(brand, 1.2))
            painter.drawRoundedRect(rect.adjusted(0.4, 0.4, -0.4, -0.4), 12, 12)
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    # ---- text ----

    def _elide(self, label, text):
        width = max(label.width(), 60)
        label.setText(label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide(self.title_label, self._full_title)
        self._elide(self.meta_label, self._full_meta)

    def _set_title(self, text):
        self._full_title = text
        self._elide(self.title_label, text)

    def _meta_text(self):
        if self._pending:
            return "reading..."
        bits = []
        if self.payload.get("meta"):
            bits.append(self.payload["meta"])
        # The form path hands over a meta string that already spells out the
        # duration; a pasted card's meta is just the channel. Testing for the
        # duration itself rather than for meta being empty keeps the runtime
        # on pasted cards, which was the common case.
        if self.payload.get("duration") and "Duration" not in (self.payload.get("meta") or ""):
            bits.append(formatting.format_eta(self.payload["duration"]))
        if self.payload.get("is_image"):
            bits.append("IMAGE")
        elif self.payload["mode"] == "audio":
            bits.append("MP3 " + self.payload["bitrate"] + "k")
        else:
            bits.append((self.payload.get("container") or "").upper())
        if self.payload.get("time_range"):
            a, b = self.payload["time_range"]
            bits.append("CLIP " + formatting.format_eta(a) + "-" + formatting.format_eta(b))
        return "  ".join(b for b in bits if b)

    def _refresh_meta(self):
        self._full_meta = self._meta_text()
        self._elide(self.meta_label, self._full_meta)

    # ---- states ----

    def mark_pending(self):
        """A freshly pasted link appears before its details are known. It
        shows the bare URL and refuses to start until the lookup lands --
        starting early would download at whatever the form happened to be set
        to rather than at a resolution this link actually offers."""
        self._pending = True
        self._set_title(self.payload["url"])
        self.title_label.setStyleSheet("font-weight: 500; color: #a9b4c9;")
        self._refresh_meta()
        self.start_btn.setEnabled(False)

    def resolve(self, info, pixmap):
        self._pending = False
        self.payload["title"] = info["title"] or self.payload["url"]
        self.payload["meta"] = info["uploader"]
        self.payload["duration"] = info["duration"]
        self.payload["is_image"] = info["is_image"]
        self.payload["thumbnail_url"] = info["thumbnail_url"]
        self.payload["thumb_pixmap"] = pixmap
        self._set_title(self.payload["title"])
        self.title_label.setStyleSheet("font-weight: 500;")
        self.setToolTip(self.payload["title"])

        self.set_thumbnail(pixmap)

        self._heights = sorted(info.get("heights") or [], reverse=True)
        self._populate_res_combo()
        self._refresh_meta()
        self.start_btn.setEnabled(True)

    def resolve_from_listing(self, entry):
        """A video that arrived as one entry of a playlist.

        A playlist is listed without extracting each video's formats -- that
        is what keeps a 200-video playlist from taking minutes -- so the real
        resolution ladder isn't known here. The card offers "Best" plus the
        standard ladder instead, and the downloader picks the nearest height
        the video really has, so nothing is lost by not knowing it yet.
        """
        self._pending = False
        self.payload["title"] = entry.get("title") or self.payload["url"]
        self.payload["meta"] = entry.get("uploader") or ""
        self.payload["duration"] = entry.get("duration") or 0
        self.payload["thumbnail_url"] = entry.get("thumbnail_url")
        self.payload["ladder"] = True
        self._set_title(self.payload["title"])
        self.title_label.setStyleSheet("font-weight: 500;")
        self.setToolTip(self.payload["title"])
        self._heights = list(downloader.STANDARD_LADDER)
        self._populate_res_combo()
        self._refresh_meta()
        self.start_btn.setEnabled(True)

    def set_thumbnail(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        # RoundedImage covers and crops itself; handing it the full pixmap
        # lets it render sharply at the display's own scaling.
        self.thumb_label.setPixmap(pixmap)

    def _populate_res_combo(self):
        """Only the heights this link actually has, highest first. An audio or
        image job has no resolution to choose, so the control is hidden rather
        than shown empty."""
        if (self.payload["mode"] == "audio" or self.payload.get("is_image")
                or not self._heights):
            self.res_combo.setVisible(False)
            return
        ladder = bool(self.payload.get("ladder"))
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        if ladder:
            self.res_combo.addItem("Best", None)
        for h in self._heights:
            self.res_combo.addItem("%dp" % h, h)
        index = self._default_index(ladder)
        self.res_combo.setCurrentIndex(index)
        self.res_combo.blockSignals(False)
        self.payload["height"] = self.res_combo.itemData(index)
        self.res_combo.setVisible(True)

    def _default_index(self, ladder):
        """The combo entry a fresh card starts on: the highest height at or
        below Settings > Preferred quality, falling back to the smallest the
        link has when it has nothing that low. "Best" (or the top height)
        when no preference is set."""
        offset = 1 if ladder else 0
        if not self._preferred:
            return 0
        at_or_below = [i for i, h in enumerate(self._heights) if h <= self._preferred]
        if at_or_below:
            return at_or_below[0] + offset
        return len(self._heights) - 1 + offset

    def _on_res_changed(self, _index):
        # Unconditional: "Best" carries None, and skipping None here meant
        # picking Best after 720p left the card downloading at 720p.
        self.payload["height"] = self.res_combo.currentData()

    def mark_failed(self, err):
        """The meta line says *why* in a few words -- it used to read just
        "failed", which told nobody whether to retry, sign in or give up.
        The full explanation is on the tooltip."""
        self._pending = False
        headline, advice = errors.friendly(err)
        self._failed = True
        self._full_meta = headline
        self._elide(self.meta_label, headline)
        self.meta_label.setProperty("state", "error")
        self.meta_label.style().unpolish(self.meta_label)
        self.meta_label.style().polish(self.meta_label)
        tip = f"{headline}\n{advice}".strip()
        self.meta_label.setToolTip(tip)
        self.setToolTip(tip)
        self.res_combo.setVisible(False)
        self.start_btn.setEnabled(False)

    def is_failed(self):
        return getattr(self, "_failed", False)

    def to_entry(self):
        return video_queue_state.to_entry(self.payload, self._heights)

    def restore_heights(self, heights):
        self._heights = sorted(heights or [], reverse=True)
        chosen = self.payload.get("height")
        self._populate_res_combo()
        if not self.res_combo.isVisibleTo(self):
            return
        if chosen is None:
            # Saved as "Best" -- which only a playlist card offers as an item;
            # a normal card's "best" is simply its top height, already chosen.
            if self.payload.get("ladder"):
                self.res_combo.setCurrentIndex(0)
                self.payload["height"] = None
            return
        # By item data rather than by index: a playlist card has "Best" at
        # the top, so the heights sit one row lower than their list position.
        index = self.res_combo.findData(chosen)
        if index >= 0:
            self.res_combo.setCurrentIndex(index)
            self.payload["height"] = chosen


class VideoTab(QWidget):
    # Background threads emit these; Qt auto-queues delivery to this
    # object's slots on the main/GUI thread (the thread it was created on),
    # the same role Tk's `self.root.after(0, ...)` plays.
    # height_sizes is a dict with int keys -- Qt's Signal(dict) marshals
    # through QVariantMap, which requires string keys and silently fails
    # ("Cannot copy-convert ... to C++") for this shape; `object` passes
    # the Python dict through untouched instead.
    _fetch_done_sig = Signal(str, str, int, object, object, object, bool)
    _fetch_error_sig = Signal(str)
    # Every download signal carries its job id as the first argument: several
    # downloads run at once now, so a bare "progress = 40%" is meaningless
    # without saying *which* card it belongs to.
    _progress_sig = Signal(int, float, str)
    _download_done_sig = Signal(int, object, str, str)
    _download_error_sig = Signal(int, str)
    # progress_hook runs on the download thread, same as the others above --
    # this can't touch download_tab's widgets directly, it has to cross back
    # to the GUI thread through a signal like everything else here does.
    _playable_sig = Signal(int)
    # A pasted batch resolves each link on its own thread; the strip it
    # belongs to rides along as `object` so the result lands on the right
    # line no matter what order the lookups finish in.
    _strip_info_sig = Signal(object, object, str)
    # Restored queue cards only need their picture back, not a whole lookup.
    _strip_thumb_sig = Signal(object, object)
    # Save Thumbnail's worker reports back through these. Both existed in
    # 2.0.0 and were lost when this class was merged with the queue rewrite,
    # which left the worker emitting on attributes that did not exist: the
    # save dialog opened, the thread died on an AttributeError, and the
    # button stayed disabled for the rest of the session.
    _thumb_save_done_sig = Signal(str)
    _thumb_save_error_sig = Signal(str)
    # The downloader couldn't read the chosen browser's cookies and carried
    # on without them (browser, reason) -- reported from a worker thread.
    _cookie_notice_sig = Signal(str, str)
    # A pasted link turned out to be a playlist or channel: the pending card
    # it made is replaced by one card per video. (card, title, entries)
    _playlist_sig = Signal(object, str, object)
    # The same, arriving through the form's Fetch rather than a paste --
    # there is no card to replace yet, so the handler makes one. (url, title,
    # entries)
    _form_playlist_sig = Signal(str, str, object)
    # "Sign in inside this app" from the sign-in dialog -- main_qt switches
    # to the Browser tab, same as it does for the Images tab.
    # A link to sign in for: the Browser tab opens it, so the site's own
    # login is one click away.
    open_browser_requested = Signal(str)
    # A link that belongs in another tab: ("images" | "torrent", [urls]).
    send_to_tab = Signal(str, object)
    # Pictures found while reading an account, a story or a post, for the
    # Images tab: (title, items, note, switch there, add to what it shows --
    # true for a pasted list, so its pictures arrive together).
    images_found = Signal(str, object, str, bool, bool)
    # The result of sorting a link into videos and pictures (worker thread ->
    # GUI): (url, result or None, error text, how it was asked for).
    _split_sig = Signal(str, object, str, str)
    _split_progress_sig = Signal(str)

    def __init__(self, settings, download_tab, parent=None):
        super().__init__(parent)
        self.settings = settings
        # Owns the actual progress cards/list UI now -- shared with
        # browser_tab.py's direct downloads, so both kinds of download
        # land in one universal Download tab instead of either tab's own
        # space. This tab keeps only the download *logic* (yt-dlp calls,
        # threading) and a lightweight per-job cancel flag.
        self.download_tab = download_tab

        self.download_dir = settings_store.get_save_dir(
            settings, "video", config.DEFAULT_DOWNLOAD_DIR)
        os.makedirs(self.download_dir, exist_ok=True)

        self.res_height_map = {}
        self.bitrate_value_map = {}
        self.duration = 0
        self.height_sizes = {}
        self.is_image_mode = False
        self.thumbnail_url = None
        self._thumb_pixmap = None

        # job_id -> {"cancel", "pause_event", "path", "title", "save_dir"} --
        # the card itself now lives in download_tab, keyed by this same id.
        self._jobs = {}
        self._last_fetch_url = ""
        # Thin queue lines under the form -- links lined up but not started.
        self._queue_strips = []
        # Guards the textChanged handler while it clears the field itself,
        # so consuming a pasted batch cannot re-enter and stack it twice.
        self._suppress_url_change = False

        # Download scheduling. At most Settings > Simultaneous downloads jobs
        # are _running at once; the rest wait in _waiting, in the order they
        # were started, and each one that finishes lets the next begin. A
        # paused job gives its slot up (it is doing nothing with it) and takes
        # it back when resumed, even if that briefly runs one over the limit
        # -- resuming is an explicit request, and it should not queue.
        self._running = set()
        self._waiting = []          # [(job_id, fn, args)]
        self._paused_jobs = set()

        # Thumbnails for stacked cards are fetched through a small pool. A
        # 300-video playlist used to mean 300 threads opening connections
        # at the same moment.
        self._thumb_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="awd-thumb")

        self._fetch_done_sig.connect(self._on_fetch_done)
        self._fetch_error_sig.connect(self._on_fetch_error)
        self._progress_sig.connect(self._update_progress)
        self._download_done_sig.connect(self._on_download_done)
        self._download_error_sig.connect(self._on_download_error)
        self._playable_sig.connect(lambda jid: self.download_tab.set_playable(jid, True))
        self._strip_info_sig.connect(self._on_strip_info)
        self._strip_thumb_sig.connect(self._on_strip_thumb)
        self._thumb_save_done_sig.connect(self._on_thumb_save_done)
        self._thumb_save_error_sig.connect(self._on_thumb_save_error)
        self._playlist_sig.connect(self._on_playlist)
        self._split_sig.connect(self._on_split)
        self._split_progress_sig.connect(self.status_label_set)
        self._form_playlist_sig.connect(self._on_form_playlist)
        self._cookie_notice_sig.connect(self._on_cookie_fallback)
        downloader.cookie_fallback_listeners.append(self._cookie_notice_sig.emit)

        self._build_ui()
        self._check_ffmpeg()
        self._resume_pending_downloads()
        self._restore_queue()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        # The content scrolls: a QVBoxLayout given less height than its
        # children need squeezes, and fixed-height queue cards then overlap.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Scoped by id: an unscoped "background: transparent" is a widget
        # stylesheet, which outranks the app's for every descendant and
        # repainted the accent buttons inside the tab transparent too.
        self._scroll.setObjectName("videoScroll")
        self._scroll.setStyleSheet("#videoScroll { background: transparent; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("videoScrollBody")
        body.setStyleSheet("#videoScrollBody { background: transparent; }")
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll)

        column = centered_column(body, MAX_CONTENT_W)
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(12)

        # ---- Hero: the link field ----
        # The one thing every visit starts with, so it gets the most room on
        # the page: a taller field, a larger face, and the only filled button
        # above the fold until a link has been read.
        url_card, url_layout = make_card("Paste a link")
        root.addWidget(url_card)
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        self.url_entry = QLineEdit()
        self.url_entry.setObjectName("heroField")
        self.url_entry.setPlaceholderText(
            "A video, a playlist or a post — paste several at once to queue them all")
        self.url_entry.setFixedHeight(46)
        self._url_icon_action = self.url_entry.addAction(
            _link_icon(theme.tokens(self._dark_mode())["text_faint"]),
            QLineEdit.ActionPosition.LeadingPosition)
        self.url_entry.returnPressed.connect(self.on_fetch)
        self.url_entry.textChanged.connect(self._on_url_text_changed)
        url_row.addWidget(self.url_entry, 1)
        self.fetch_btn = Button("Fetch")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.setFixedSize(116, 46)
        self.fetch_btn.setCursor(Qt.PointingHandCursor)
        self.fetch_btn.clicked.connect(self.on_fetch)
        url_row.addWidget(self.fetch_btn)
        url_layout.addLayout(url_row)
        self.status_label = QLabel("Paste a link, then click Fetch.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        url_layout.addWidget(self.status_label)

        # ---- What was fetched (hidden until Fetch succeeds) ----
        self.info_card, info_layout = make_card()
        info_row = QHBoxLayout()
        info_row.setSpacing(18)
        info_layout.addLayout(info_row)
        self.thumb_label = RoundedImage(192, 108, radius=10.0, placeholder="No preview")
        info_row.addWidget(self.thumb_label, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        self.info_title_label = QLabel("")
        self.info_title_label.setObjectName("heading")
        self.info_title_label.setWordWrap(True)
        text_col.addWidget(self.info_title_label)
        self.info_meta_label = QLabel("")
        self.info_meta_label.setObjectName("muted")
        text_col.addWidget(self.info_meta_label)
        text_col.addStretch(1)
        self.save_thumb_btn = Button("Save Thumbnail")
        self.save_thumb_btn.setObjectName("quiet")
        self.save_thumb_btn.setCursor(Qt.PointingHandCursor)
        self.save_thumb_btn.setEnabled(False)
        self.save_thumb_btn.clicked.connect(self.on_save_thumbnail)
        thumb_btn_row = QHBoxLayout()
        thumb_btn_row.setContentsMargins(0, 0, 0, 0)
        thumb_btn_row.addWidget(self.save_thumb_btn)
        thumb_btn_row.addStretch(1)
        text_col.addLayout(thumb_btn_row)
        info_row.addLayout(text_col, 1)
        self.info_card.setVisible(False)
        root.addWidget(self.info_card)

        # ---- Options ----
        self.opts_card, opts_layout = make_card("Download options")
        opts_layout.setSpacing(10)
        root.addWidget(self.opts_card)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(18)
        self.video_radio = QRadioButton("Video")
        self.audio_radio = QRadioButton("Audio only (MP3)")
        self.video_radio.setChecked(True)
        self.video_radio.toggled.connect(self.on_mode_change)
        mode_row.addWidget(self.video_radio)
        mode_row.addWidget(self.audio_radio)
        mode_row.addStretch(1)
        opts_layout.addLayout(mode_row)

        self.video_row = QWidget()
        video_row_layout = QHBoxLayout(self.video_row)
        video_row_layout.setContentsMargins(0, 0, 0, 0)
        video_row_layout.setSpacing(8)

        # Resolution and format are grouped so image mode can hide *them*
        # while Download (which image posts still need) stays put.
        self.fmt_group = QWidget()
        fmt_layout = QHBoxLayout(self.fmt_group)
        fmt_layout.setContentsMargins(0, 0, 0, 0)
        fmt_layout.setSpacing(8)
        fmt_layout.addWidget(self._label("Resolution", "muted"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("best available")
        self.res_combo.setCursor(Qt.PointingHandCursor)
        self.res_combo.currentTextChanged.connect(lambda _t: self._update_selection_summary())
        # AdjustToContents alone: it already sizes to the widest item this
        # ever holds; a minimum contents length on top only added dead space
        # between the text and the arrow.
        self.res_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        fmt_layout.addWidget(self.res_combo)
        fmt_layout.addSpacing(14)
        fmt_layout.addWidget(self._label("Format", "muted"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp4", "mkv", "mov"])
        self.format_combo.setCursor(Qt.PointingHandCursor)
        default_format = (self.settings or {}).get("default_format", "mp4")
        if default_format in ("mp4", "mkv", "mov"):
            self.format_combo.setCurrentText(default_format)
        self.format_combo.currentTextChanged.connect(lambda _t: self._refresh_choices())
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        fmt_layout.addWidget(self.format_combo)
        fmt_layout.addStretch(1)
        video_row_layout.addWidget(self.fmt_group, 1)

        # Queue beside Download rather than instead of it: one link straight
        # to Download is still the common case; queueing is the opt-in.
        self.queue_btn = Button("Queue")
        self.queue_btn.setFixedWidth(96)
        self.queue_btn.setCursor(Qt.PointingHandCursor)
        self.queue_btn.setEnabled(False)
        self.queue_btn.setToolTip("Add this to the queue below instead of starting it now.")
        self.queue_btn.clicked.connect(self.on_queue)
        video_row_layout.addWidget(self.queue_btn, 0, Qt.AlignRight)

        self.download_btn = Button("Download")
        self.download_btn.setObjectName("accent")
        self.download_btn.setFixedWidth(150)
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.on_download)
        video_row_layout.addWidget(self.download_btn, 0, Qt.AlignRight)
        opts_layout.addWidget(self.video_row)

        self.bitrate_row = QWidget()
        bitrate_row_layout = QHBoxLayout(self.bitrate_row)
        bitrate_row_layout.setContentsMargins(0, 0, 0, 0)
        bitrate_row_layout.setSpacing(8)
        bitrate_row_layout.addWidget(self._label("MP3 bitrate", "muted"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["128 kbps", "192 kbps", "256 kbps", "320 kbps"])
        self.bitrate_combo.setCurrentText("320 kbps")
        self.bitrate_combo.setCursor(Qt.PointingHandCursor)
        self.bitrate_combo.currentTextChanged.connect(lambda _t: self._update_selection_summary())
        bitrate_row_layout.addWidget(self.bitrate_combo, 1)
        opts_layout.addWidget(self.bitrate_row)
        self.bitrate_row.setVisible(False)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        opts_layout.addWidget(divider)

        self.range_check = QCheckBox("Download only part of this video (clip range)")
        self.range_check.setEnabled(False)
        self.range_check.toggled.connect(self.on_range_toggle)
        opts_layout.addWidget(self.range_check)

        self.range_fields = QWidget()
        range_layout = QHBoxLayout(self.range_fields)
        range_layout.setContentsMargins(24, 0, 0, 0)
        range_layout.setSpacing(8)
        range_layout.addWidget(self._label("Start", "muted"))
        self.start_entry = QLineEdit("0:00")
        self.start_entry.setObjectName("timecode")
        self.start_entry.setFixedWidth(88)
        self.start_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.start_entry)
        range_layout.addSpacing(6)
        range_layout.addWidget(self._label("End", "muted"))
        self.end_entry = QLineEdit("0:00")
        self.end_entry.setObjectName("timecode")
        self.end_entry.setFixedWidth(88)
        self.end_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.end_entry)
        range_layout.addSpacing(6)
        self.range_info_label = QLabel("Format: HH:MM:SS or MM:SS")
        self.range_info_label.setObjectName("muted")
        range_layout.addWidget(self.range_info_label, 1)
        opts_layout.addWidget(self.range_fields)
        self.range_fields.setVisible(False)

        # What Download will produce, stated once, centred between the form
        # that decides it and the folder it lands in.
        self.summary_chip = Chip(dot=theme.tokens(self._dark_mode())["accent"])
        self._restyle_summary_chip()
        self.summary_label = self.summary_chip.label
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addStretch(1)
        summary_row.addWidget(self.summary_chip)
        summary_row.addStretch(1)
        root.addLayout(summary_row)
        self.summary_chip.setVisible(False)

        # ---- Where it lands ----
        out_card, out_layout = make_card("Save to")
        root.addWidget(out_card)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = Button("Browse")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        open_folder_btn = Button("Open Folder")
        open_folder_btn.setCursor(Qt.PointingHandCursor)
        open_folder_btn.clicked.connect(self.open_download_folder)
        dir_row.addWidget(open_folder_btn)
        out_layout.addLayout(dir_row)

        # ---- Queue: links lined up but not started ----
        # Hidden until something is queued, so the page looks the same as
        # ever to anyone who never uses it; it sits right under the form that
        # fills it, so a link you just banked appears where you were looking.
        self.queue_card, queue_layout = make_card()
        queue_layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(section_label("Queue"))
        self.queue_count_label = QLabel("")
        self.queue_count_label.setObjectName("mono")
        head.addWidget(self.queue_count_label)
        head.addStretch(1)
        self.clear_queue_btn = Button("Clear queue")
        self.clear_queue_btn.setObjectName("quiet")
        self.clear_queue_btn.setFixedHeight(30)
        self.clear_queue_btn.setToolTip("Remove every link waiting in the queue")
        self.clear_queue_btn.clicked.connect(self.clear_queue)
        head.addWidget(self.clear_queue_btn)
        self.start_queue_btn = Button("Download all")
        self.start_queue_btn.setObjectName("accent")
        self.start_queue_btn.setFixedHeight(30)
        self.start_queue_btn.setStyleSheet("padding: 0px 16px;")
        self.start_queue_btn.setCursor(Qt.PointingHandCursor)
        self.start_queue_btn.clicked.connect(self.start_queue)
        head.addWidget(self.start_queue_btn)
        queue_layout.addLayout(head)

        self.queue_list_layout = QVBoxLayout()
        self.queue_list_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_list_layout.setSpacing(6)
        queue_layout.addLayout(self.queue_list_layout)

        self.queue_card.setVisible(False)
        root.addWidget(self.queue_card)

        self.ffmpeg_warn_label = QLabel("")
        self.ffmpeg_warn_label.setObjectName("dangerText")
        self.ffmpeg_warn_label.setWordWrap(True)
        root.addWidget(self.ffmpeg_warn_label)
        root.addStretch(1)

    def _dark_mode(self):
        """Current theme, for widgets that paint themselves and so can't get
        their colours from the stylesheet."""
        return (self.settings or {}).get("theme", "dark") != "light"

    def apply_theme(self):
        """Called by MainWindow after a live theme toggle -- re-applies
        colours for widgets that paint themselves and so don't pick up the
        new theme from the stylesheet the way ordinary QSS-styled widgets
        do. The download cards themselves are download_tab's own -- it's a
        real tab now, so MainWindow re-themes it the same duck-typed way."""
        self._restyle_summary_chip()
        self._url_icon_action.setIcon(_link_icon(theme.tokens(self._dark_mode())["text_faint"]))
        for card in self._queue_strips:
            card.update()

    @staticmethod
    def _label(text, object_name=None):
        lbl = QLabel(text)
        if object_name:
            lbl.setObjectName(object_name)
        return lbl

    def _check_ffmpeg(self):
        if not ffmpeg_utils.ffmpeg_available():
            self.ffmpeg_warn_label.setText(
                "⚠ ffmpeg not found on PATH. Merging/format conversion will fail until it's installed."
            )

    def browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.dir_entry.text())
        if d:
            self.dir_entry.setText(d)
            # Remembered across restarts -- previously this reset to the
            # default folder on every launch.
            settings_store.set_save_dir(self.settings, "video", d)

    def open_download_folder(self):
        path = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception:
            logger.exception("Failed to open folder %s", path)
            QMessageBox.information(self, config.APP_NAME, f"Your files are saved at:\n{path}")

    # ----------------------------------------------------------- Fetching ---
    def on_fetch(self):
        url = self.url_entry.text().strip()
        if not url:
            from PySide6.QtWidgets import QApplication
            clip_text = (QApplication.clipboard().text() or "").strip()
            if clip_text:
                url = clip_text
                # Written under the guard: this is the *form* path, and the
                # field handler would otherwise see a link arrive and stack it
                # as a queue card at the same time, giving one link two homes.
                self._suppress_url_change = True
                try:
                    self.url_entry.setText(url)
                finally:
                    self._suppress_url_change = False
        if not url:
            QMessageBox.warning(self, config.APP_NAME, "Paste a link first, or copy one to your clipboard.")
            return


        # Every link is sent where it can be downloaded (app/core/link_router.py):
        # a picture to Images, a magnet to Torrent; an Instagram account, its
        # stories or a post -- which can hold pictures and videos together --
        # is read and sorted first.
        route = link_router.classify(url)
        if route.tab in (link_router.IMAGES, link_router.TORRENT):
            self._hand_over(route, [url])
            return
        if route.tab in (link_router.SPLIT, link_router.POST) or route.kind == "ig_reel":
            # a reel too: signed out, it's read from its own page (yt-dlp needs a sign-in)
            self._last_fetch_url = url
            self._start_split(url, route, "form")
            return

        # Held so _on_fetch_done can build the row against the URL that was
        # actually fetched -- the entry field is cleared the moment a row
        # lands, so it cannot be read back at that point.
        self._last_fetch_url = url
        self.status_label.setText("Fetching info...")
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.set_busy(True)
        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _cookies_from_browser(self):
        """None means "use the app's own Browser tab session" -- see
        app/utils/browser_cookies.py. A browser name here means the user
        explicitly chose to borrow that browser's cookies instead."""
        return (self.settings or {}).get("cookies_from_browser")

    def _fetch_thread(self, url):
        try:
            info, height_sizes = downloader.fetch_info_with_sizes(
                url, cookies_from_browser=self._cookies_from_browser())
            if downloader.is_playlist(info):
                # The form describes one video; a playlist goes to the queue,
                # one card per video.
                entries = downloader.playlist_entries(info, self._cookies_from_browser())
                self._form_playlist_sig.emit(url, info.get("title") or "", entries)
                return
            title = info.get("title", "Unknown title")
            uploader = info.get("uploader") or info.get("channel") or ""
            duration = info.get("duration") or 0
            has_video = any(
                f.get("vcodec") not in (None, "none") for f in (info.get("formats") or [])
            )
            # something with a running time or sound is a video (or a song), never a picture
            is_image = not (has_video or duration or any(
                f.get("acodec") not in (None, "none") for f in (info.get("formats") or [])))
            thumbnail_url = downloader.best_thumbnail_url(info)
            thumb_image = downloader.fetch_thumbnail_image(thumbnail_url)
            self._fetch_done_sig.emit(title, uploader, duration, height_sizes,
                                       thumb_image, thumbnail_url, is_image)
        except Exception as e:
            logger.exception("Fetch failed for %s", url)
            self._fetch_error_sig.emit(str(e))

    def _on_fetch_done(self, title, uploader, duration, height_sizes, thumb_image, thumbnail_url, is_image):
        if is_image and thumbnail_url:
            # A picture, not a video: it opens in the Images tab, where it can
            # be seen and saved at full size, instead of an image mode here.
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.set_busy(False)
            self._clear_form_for_next()
            self.images_found.emit(title, [{"title": title, "url": thumbnail_url, "is_video": False}], "", True,
                                   False)
            self.status_label.setText("That link is a picture -- it's open in the Images tab.")
            return
        self.is_image_mode = is_image
        if is_image and not thumbnail_url:
            self.status_label.setText(f"Loaded: {title} — but no video or downloadable image was found for this post.")
        elif is_image:
            self.status_label.setText(f"Loaded image post: {title}")
        else:
            self.status_label.setText(f"Loaded: {title}")
        self.duration = duration
        self.height_sizes = height_sizes
        self.thumbnail_url = thumbnail_url

        self.info_title_label.setText(title)
        meta_bits = []
        if uploader:
            meta_bits.append(uploader)
        if duration:
            meta_bits.append(f"Duration: {formatting.format_eta(duration)}")
        self.info_meta_label.setText("  •  ".join(meta_bits))

        pix = _pil_to_pixmap(thumb_image)
        # Held at full size so each download card can crop its own 16:9 tile
        # from it -- the label below scales a copy down to the preview box.
        self._thumb_pixmap = pix
        if pix is not None:
            self.thumb_label.setPixmap(pix)
            self.thumb_label.setText("")
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText("No preview")
        self.save_thumb_btn.setText("Save Image" if is_image else "Save Thumbnail")
        self.save_thumb_btn.setEnabled(bool(thumbnail_url))

        self.info_card.setVisible(True)

        self.range_check.setEnabled(bool(duration))
        self.start_entry.setText("0:00")
        self.end_entry.setText(formatting.format_eta(duration) if duration else "0:00")
        self.range_check.setChecked(False)
        self.on_range_toggle()
        self._refresh_choices(fresh=True)

        self._apply_mode_visibility()

        self.fetch_btn.setEnabled(True)
        self.fetch_btn.set_busy(False)
        self.download_btn.setEnabled(True)
        self.queue_btn.setEnabled(True)
    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.set_busy(False)
        headline, advice = errors.friendly(err)
        self.status_label.setText(headline)
        # Same treatment the Images tab gives a login wall: offer the two
        # ways to get a session instead of printing yt-dlp's advice about
        # command-line flags.
        if errors.needs_sign_in(err):
            self._offer_sign_in()
            return
        QMessageBox.warning(self, config.APP_NAME, f"{headline}\n\n{advice}".strip())

    def _on_cookie_fallback(self, browser, message):
        from .dialogs import signin_dialog
        self.status_label.setText(
            signin_dialog.handle_cookie_fallback(self.settings, browser, message))

    def _offer_sign_in(self):
        from .dialogs import signin_dialog
        choice = signin_dialog.show_sign_in_help(
            self, url=self._last_fetch_url, dark_mode=self._dark_mode(),
            current=self._cookies_from_browser())
        if choice is None:
            return
        if choice == "browser_tab":
            self.open_browser_requested.emit(self._last_fetch_url or "")
            return
        self.settings["cookies_from_browser"] = choice
        settings_store.save_settings(self.settings)
        self.status_label.setText("Using your %s sign-in — fetching again..." % choice)
        self.on_fetch()

    def _image_download_thread(self, job_id, url, dest, save_dir):
        try:
            self._progress_sig.emit(job_id, 10, "Saving image...")
            dest = downloader.save_thumbnail(url, os.path.splitext(dest)[0])
            size_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
            job = self._jobs.get(job_id)
            download_history.add_entry(
                "image", (job or {}).get("title") or os.path.basename(dest),
                dest, save_dir, size_bytes,
            )
            self._download_done_sig.emit(job_id, "", "image", dest)
        except Exception as e:
            logger.exception("Failed to save image to %s", dest)
            self._download_error_sig.emit(job_id, str(e))

    # ------------------------------------------------------- Clip range ---
    def _restyle_summary_chip(self):
        # Glass with near-white text. An earlier blue-on-blue version of this
        # chip measured 3.45:1 against a light backdrop; the text here is the
        # theme's own body colour on a pane over the page's own painted
        # backdrop, so its contrast is the body text's (well past 7:1). The
        # ember dot ties it to the Download button it describes.
        t = theme.tokens(self._dark_mode())
        if self._dark_mode():
            self.summary_chip.set_colors(QColor(255, 255, 255, 16), QColor(255, 255, 255, 46),
                                         t["text"], dot=t["accent"])
        else:
            self.summary_chip.set_colors(QColor(255, 255, 255, 200), QColor(15, 23, 42, 36),
                                         t["text"], dot=t["accent"])

    def on_mode_change(self):
        if self.audio_radio.isChecked():
            self.fmt_group.setVisible(False)
            self.bitrate_row.setVisible(True)
        else:
            self.bitrate_row.setVisible(False)
            self.fmt_group.setVisible(True)
        self._update_selection_summary()

    def _apply_mode_visibility(self):
        if self.is_image_mode:
            self.opts_card.setVisible(False)
            self.summary_label.setText("")
            self.summary_chip.setVisible(False)
            self.download_btn.setText("Save Image")
            try:
                self.download_btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.download_btn.clicked.connect(self._on_image_download)
        else:
            self.opts_card.setVisible(True)
            self.download_btn.setText("Download")
            try:
                self.download_btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.download_btn.clicked.connect(self.on_download)
            self._refresh_choices()

    def on_range_toggle(self):
        self.range_fields.setVisible(self.range_check.isChecked())
        self.on_range_change()

    def on_range_change(self):
        _, eff_duration, valid, err = self._current_fraction_and_duration()
        if not self.range_check.isChecked():
            self.range_info_label.setText("Format: HH:MM:SS or MM:SS")
        elif not valid:
            self.range_info_label.setText(err or "Format: HH:MM:SS or MM:SS")
        else:
            self.range_info_label.setText(f"Clip length: {formatting.format_eta(eff_duration)}")
        self._refresh_choices()

    def _current_fraction_and_duration(self):
        if not self.range_check.isChecked() or not self.duration:
            return 1.0, self.duration, True, None
        try:
            start = formatting.parse_timecode(self.start_entry.text()) or 0
            end = formatting.parse_timecode(self.end_entry.text())
        except ValueError as e:
            return 1.0, self.duration, False, str(e)
        if end is None:
            return 1.0, self.duration, False, "Enter an end time."
        if end <= start:
            return 1.0, self.duration, False, "End must be after start."
        if end > self.duration:
            return 1.0, self.duration, False, f"Video is only {formatting.format_eta(self.duration)} long."
        clip_len = end - start
        fraction = clip_len / self.duration if self.duration else 1.0
        return fraction, clip_len, True, None

    def _get_selected_range_seconds(self):
        if not self.range_check.isChecked():
            return None
        _, _, valid, _ = self._current_fraction_and_duration()
        if not valid:
            return None
        start = formatting.parse_timecode(self.start_entry.text()) or 0
        end = formatting.parse_timecode(self.end_entry.text())
        return (start, end)

    # ------------------------------------------------- Dynamic choice lists ---
    def _refresh_choices(self, fresh=False):
        fraction, eff_duration, _, _ = self._current_fraction_and_duration()
        self._rebuild_resolution_choices(fraction, eff_duration, fresh=fresh)
        self._rebuild_bitrate_choices(eff_duration)
        self._update_selection_summary()

    def _rebuild_resolution_choices(self, fraction=1.0, effective_duration=None, fresh=False):
        """`fresh` is a newly fetched link: it starts on the highest resolution
        it has (or the one Settings > Preferred quality asks for), not on
        whatever the previous link was left at."""
        prev_height = None if fresh else self.res_height_map.get(self.res_combo.currentText())
        heights = sorted(self.height_sizes.keys(), reverse=True) if self.height_sizes else []
        container = self.format_combo.currentText()
        eff_duration = self.duration if effective_duration is None else effective_duration
        res_list, res_map = [], {}
        for h in heights:
            total_size = size_estimate.estimate_video_size(
                self.height_sizes, h, eff_duration, fraction, container
            )
            label = f"{h}p   •   ~{formatting.humanize_size(total_size)}"
            if container == "mov":
                label += " (est.)"
            res_list.append(label)
            res_map[label] = h
        if not res_list:
            res_list, res_map = ["best available"], {"best available": None}

        self.res_height_map = res_map
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        self.res_combo.addItems(res_list)
        match = next((lbl for lbl, h in res_map.items() if h == prev_height), None)
        if match is None and fresh:
            preferred = self._preferred_height()
            if preferred:
                match = next((lbl for lbl, h in res_map.items() if h and h <= preferred), None)
        self.res_combo.setCurrentText(match or res_list[0])
        self.res_combo.blockSignals(False)

    def _rebuild_bitrate_choices(self, effective_duration):
        prev_kbps = self.bitrate_value_map.get(self.bitrate_combo.currentText())
        bitrate_list, bitrate_map = [], {}
        for kbps in (128, 192, 256, 320):
            label = f"{kbps} kbps"
            if effective_duration:
                size_bytes = size_estimate.estimate_audio_size(effective_duration, kbps)
                label += f"   •   ~{formatting.humanize_size(size_bytes)}"
            bitrate_list.append(label)
            bitrate_map[label] = kbps

        self.bitrate_value_map = bitrate_map
        self.bitrate_combo.blockSignals(True)
        self.bitrate_combo.clear()
        self.bitrate_combo.addItems(bitrate_list)
        match = next((lbl for lbl, k in bitrate_map.items() if k == prev_kbps), None)
        # 320 kbps -- the best MP3 there is -- unless something else was picked.
        self.bitrate_combo.setCurrentText(match or bitrate_list[-1])
        self.bitrate_combo.blockSignals(False)

    def _update_selection_summary(self):
        fraction, eff_duration, valid, _ = self._current_fraction_and_duration()
        range_on = self.range_check.isChecked() and valid

        # Parenthesized size instead of another "• ~X MB" segment, and no
        # "You'll get:" preamble -- the badge's own shape and position
        # (centered, right above the Save-to card) already say "this is
        # what you're about to download," so the label can just state it.
        if self.audio_radio.isChecked():
            kbps = self.bitrate_value_map.get(self.bitrate_combo.currentText(), 320)
            size = size_estimate.estimate_audio_size(eff_duration, kbps)
            text = f"MP3 · {kbps} kbps  ({formatting.humanize_size(size)})"
        else:
            height = self.res_height_map.get(self.res_combo.currentText())
            container = self.format_combo.currentText()
            total = size_estimate.estimate_video_size(self.height_sizes, height, eff_duration, fraction, container)
            res_text = f"{height}p" if height else "Best available"
            text = f"{res_text} {container.upper()}  ({formatting.humanize_size(total)})"
            if container == "mov":
                text += "  · re-encoded"

        if range_on:
            text += f"  ·  Clip {formatting.format_eta(eff_duration)}"
        self.summary_label.setText(text)
        self.summary_chip.setVisible(bool(text))

    # ---------------------------------------------------------- Download ---
    def on_save_thumbnail(self):
        if not self.thumbnail_url:
            return
        default_name = re.sub(r"[^\w\-. ]", "_", self.info_title_label.text() or "thumbnail")[:80] + ".jpg"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Thumbnail",
            os.path.join(self.dir_entry.text().strip() or self.download_dir, default_name),
            "JPEG image (*.jpg);;All files (*.*)",
        )
        if not dest:
            return
        self.save_thumb_btn.setEnabled(False)
        threading.Thread(target=self._save_thumbnail_thread, args=(self.thumbnail_url, dest), daemon=True).start()

    def _save_thumbnail_thread(self, url, dest):
        try:
            # save_thumbnail appends the source image's *real* extension
            # (it saves the original bytes losslessly rather than re-encoding
            # everything to JPEG), so hand it a path without one and use the
            # path it actually wrote -- a WebP source stays .webp instead of
            # being mislabeled .jpg.
            dest = downloader.save_thumbnail(url, os.path.splitext(dest)[0])
            size_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
            download_history.add_entry(
                "image", self.info_title_label.text() or os.path.basename(dest),
                dest, os.path.dirname(dest), size_bytes,
            )
            self._thumb_save_done_sig.emit(dest)
        except Exception as e:
            logger.exception("Failed to save thumbnail to %s", dest)
            self._thumb_save_error_sig.emit(str(e))

    def _on_thumb_save_done(self, dest):
        # An inline, self-clearing confirmation on the button itself instead
        # of a blocking popup dialog on every single save -- reported
        # directly as disruptive interrupting the flow each time.
        self.save_thumb_btn.setEnabled(True)
        original_text = self.save_thumb_btn.text()
        self.save_thumb_btn.setText("✓ Saved")
        QTimer.singleShot(2000, lambda: self.save_thumb_btn.setText(original_text))

    def _on_thumb_save_error(self, err):
        self.save_thumb_btn.setEnabled(True)
        QMessageBox.critical(self, config.APP_NAME, f"Couldn't save image:\n{err}")

    # ------------------------------------------------------ Image download ---

    _URL_RE = re.compile(r"(?:https?://|magnet:)\S+", re.I)

    @classmethod
    def _split_links(cls, text):
        """Pulls every link out of a blob of pasted text. People copy link
        lists out of notes apps and chat, so the separator could be newlines,
        spaces, commas or bullet characters -- matching the URLs themselves
        rather than splitting on any one of those handles all of them, and
        quietly drops the surrounding prose."""
        seen, out = set(), []
        for raw in cls._URL_RE.findall(text or ""):
            url = raw.rstrip(".,;)]}'\"")
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out

    def _on_url_text_changed(self, text):
        """Pasting works exactly like Fetch. One link opens in the form with
        its download options, the same as clicking Fetch; several at once (a
        list copied from notes or chat) stack in the queue, one card each.

        Only a paste counts, not typing: the field reacts when a whole link
        arrives in one go, so a link being typed isn't fetched a letter at a
        time. Typed links go with Enter or the Fetch button."""
        previous = getattr(self, "_previous_url_text", "")
        self._previous_url_text = text
        if self._suppress_url_change:
            return
        if len(text) - len(previous) < 8:
            return
        links = self._split_links(text)
        if not links:
            return
        if len(links) == 1:
            self._suppress_url_change = True
            try:
                self.url_entry.setText(links[0])
            finally:
                self._suppress_url_change = False
            self._previous_url_text = links[0]
            self.on_fetch()
            return
        self._suppress_url_change = True
        try:
            self.url_entry.clear()
        finally:
            self._suppress_url_change = False
        self._previous_url_text = ""
        self.queue_links(links)

    def queue_links(self, urls):
        """Stacks every link straight away, then fills in each title and best
        resolution as its lookup comes back. Links that belong elsewhere go
        there (pictures to Images, magnets to Torrent), and accounts, stories
        and posts are read and sorted."""
        videos, elsewhere = [], {}
        for url in urls:
            route = link_router.classify(url)
            if route.tab in (link_router.IMAGES, link_router.TORRENT):
                elsewhere.setdefault(route.tab, []).append(url)
            elif route.tab in (link_router.SPLIT, link_router.POST) or route.kind == "ig_reel":
                self._start_split(url, route, "queue")
            else:
                videos.append(url)
        for tab, links in elsewhere.items():
            self.send_to_tab.emit(tab, links)
        added = 0
        for url in videos:
            if any(st.payload["url"] == url for st in self._queue_strips):
                continue
            strip = self._add_queue_strip(self._blank_payload(url))
            strip.mark_pending()
            threading.Thread(target=self._strip_info_thread, args=(strip, url),
                             daemon=True).start()
            added += 1
        if added:
            self.status_label.setText(
                "Stacked %d link%s. Reading details..."
                % (added, "" if added == 1 else "s"))
        return added

    def _blank_payload(self, url):
        """The form's current mode/format applies to a pasted batch; height is
        left unset until the lookup says what the link actually offers."""
        return {
            "url": url,
            "mode": "audio" if self.audio_radio.isChecked() else "video",
            "height": None,
            "container": self.format_combo.currentText(),
            "bitrate": "320",
            "time_range": None,
            "title": url,
            "meta": "",
            "duration": 0,
            "is_image": False,
            "thumbnail_url": None,
            "thumb_pixmap": None,
        }

    def _strip_info_thread(self, strip, url):
        try:
            info, height_sizes = downloader.fetch_info_with_sizes(
                url, cookies_from_browser=self._cookies_from_browser())
            if downloader.is_playlist(info):
                entries = downloader.playlist_entries(info, self._cookies_from_browser())
                self._playlist_sig.emit(strip, info.get("title") or "", entries)
                return
            has_video = any(
                f.get("vcodec") not in (None, "none") for f in (info.get("formats") or [])
            )
            # something with a running time or sound is a video (or a song), never a picture --
            # even when a site held its formats back (that's a failed fetch, said as one)
            playable = has_video or bool(info.get("duration")) or any(
                f.get("acodec") not in (None, "none") for f in (info.get("formats") or []))
            if not playable and link_router.classify(url).tab == link_router.VIDEO and                     "youtube" in (info.get("extractor_key") or "").lower():
                raise downloader.yt_dlp.utils.DownloadError("YouTube didn't hand over this video just now -- "
                                                            "try again in a minute.")
            thumbnail_url = downloader.best_thumbnail_url(info)
            # The image is fetched here but NOT turned into a QPixmap: pixmaps
            # may only be built on the GUI thread, so the raw image rides the
            # signal across and _on_strip_info converts it.
            thumb_image = None
            try:
                thumb_image = downloader.fetch_thumbnail_image(thumbnail_url)
            except Exception:
                logger.debug("Thumbnail fetch failed for %s", url, exc_info=True)
            self._strip_info_sig.emit(strip, {
                "title": info.get("title", "Unknown title"),
                "uploader": info.get("uploader") or info.get("channel") or "",
                "duration": info.get("duration") or 0,
                "heights": sorted(height_sizes.keys()) if height_sizes else [],
                "height": max(height_sizes) if height_sizes else None,
                "height_sizes": dict(height_sizes or {}),
                "is_image": not playable,
                "thumbnail_url": thumbnail_url,
                "thumb_image": thumb_image,
            }, "")
        except Exception as e:
            logger.exception("Queue lookup failed for %s", url)
            self._strip_info_sig.emit(strip, None, str(e))

    def _on_strip_info(self, strip, info, err):
        # The line can be gone by the time a slow lookup returns.
        if strip not in self._queue_strips:
            return
        if info is None:
            strip.mark_failed(err)
            self.status_label.setText(
                "One queued link couldn't be read: %s" % errors.friendly(err)[0])
            return
        # A partial extraction (age gate, region block, a dead link) comes back
        # looking like an image post with no image: no video formats and no
        # thumbnail either. Left alone it resolved to a startable line whose
        # spec was a bare container with no resolution, and Start all would
        # queue a job that could only fail. Nothing downloadable means the
        # line says so instead.
        if info["is_image"] and not info["thumbnail_url"]:
            strip.mark_failed("No video or downloadable image was found for this link.")
            self.status_label.setText(
                "One queued link has nothing downloadable: %s" % strip.payload["url"])
            return
        if info["is_image"]:
            # a picture: to the Images tab, not a card here
            self._on_strip_remove(strip)
            self.images_found.emit(info["title"], [{"title": info["title"], "url": info["thumbnail_url"],
                                                    "is_video": False}], "", False, True)
            self.status_label.setText("One link was a picture -- it's in the Images tab.")
            return
        strip.resolve(info, _pil_to_pixmap(info.get("thumb_image")))
        # Kept so selecting the card later fills the form at once, without
        # reading the link from the site a second time.
        strip.fetched = {k: info.get(k) for k in ("title", "uploader", "duration", "height_sizes",
                                                  "thumbnail_url", "is_image")}
        if not any(st._pending for st in self._queue_strips):
            self.status_label.setText("Queue ready.")

    # ------------------------------------------------------- Playlists ---
    def _on_playlist(self, strip, title, entries):
        """Replaces the pending card a playlist link made with one card per
        video, in the same place in the queue."""
        if strip not in self._queue_strips:
            return
        if not entries:
            strip.mark_failed("This playlist has no videos that can be downloaded.")
            return
        index = self.queue_list_layout.indexOf(strip)
        self._on_strip_remove(strip)
        added = self._stack_entries(entries, index)
        self._report_playlist(title, added, len(entries))

    def _on_form_playlist(self, url, title, entries):
        """A playlist fetched through the form. The form can only describe one
        video, so it clears and the videos are stacked instead."""
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.set_busy(False)
        self._clear_form_for_next()
        if not entries:
            self.status_label.setText("That playlist has no videos that can be downloaded.")
            return
        added = self._stack_entries(entries, None)
        self._report_playlist(title, added, len(entries))

    def _stack_entries(self, entries, index):
        """One ready-to-start card per playlist entry, skipping any link that
        is already stacked. Returns how many were added."""
        existing = {st.payload["url"] for st in self._queue_strips}
        added = 0
        for entry in entries:
            if entry["url"] in existing:
                continue
            existing.add(entry["url"])
            card = self._add_queue_strip(
                self._blank_payload(entry["url"]),
                index=None if index is None or index < 0 else index + added,
                reveal=False)
            card.resolve_from_listing(entry)
            if entry.get("thumbnail_url"):
                self._thumb_pool.submit(self._thumb_only_thread, card, entry["thumbnail_url"])
            added += 1
        return added

    def _report_playlist(self, title, added, total):
        name = f"“{title}”" if title else "the playlist"
        if added == 0:
            text = f"Every video from {name} is already in the queue."
        else:
            text = f"Stacked {added} video{'s' if added != 1 else ''} from {name}."
            if total >= downloader.PLAYLIST_LIMIT:
                text += f" Only the first {downloader.PLAYLIST_LIMIT} are listed."
        self.status_label.setText(text)

    def _preferred_height(self):
        """Settings > Preferred quality as a height, or None for "best"."""
        value = (self.settings or {}).get("preferred_quality", "best")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _current_form_payload(self):
        """Reads the form exactly the way on_download() does, so a queued
        item carries the same settings it would have downloaded with."""
        url = self.url_entry.text().strip() or getattr(self, "_last_fetch_url", "")
        if not url:
            return None, "Fetch a link first."

        mode = "audio" if self.audio_radio.isChecked() else "video"
        container = self.format_combo.currentText()

        res_display = self.res_combo.currentText()
        height = self.res_height_map.get(res_display)
        if height is None and res_display not in self.res_height_map:
            m = re.match(r"(\d+)", res_display)
            height = int(m.group(1)) if m else None

        bitrate_display = self.bitrate_combo.currentText()
        bitrate = self.bitrate_value_map.get(bitrate_display)
        if bitrate is None:
            m = re.match(r"(\d+)", bitrate_display)
            bitrate = int(m.group(1)) if m else 320

        time_range = None
        if self.range_check.isChecked():
            _, _, valid, err = self._current_fraction_and_duration()
            if not valid:
                return None, "Fix the clip range first: %s" % err
            time_range = self._get_selected_range_seconds()

        return {
            "url": url,
            "mode": mode,
            "height": height,
            "container": container,
            "bitrate": str(bitrate),
            "time_range": time_range,
            "title": self.info_title_label.text() or url,
            "meta": self.info_meta_label.text(),
            "is_image": self.is_image_mode,
            "thumbnail_url": self.thumbnail_url,
            "thumb_pixmap": self._thumb_pixmap,
        }, None

    def on_queue(self):
        """Adds the current form to the queue instead of starting it now, so
        several links can be lined up and fired off later. The form then
        clears for the next link, same as it does after a download."""
        payload, err = self._current_form_payload()
        if payload is None:
            QMessageBox.warning(self, config.APP_NAME, err)
            return
        self._drop_cards_for_url(payload["url"])
        strip = self._add_queue_strip(payload)
        strip.fetched = {
            "title": self.info_title_label.text(), "uploader": payload.get("meta", ""),
            "duration": self.duration, "height_sizes": dict(self.height_sizes or {}),
            "thumbnail_url": self.thumbnail_url, "is_image": self.is_image_mode,
        }
        self.status_label.setText("Queued. Paste the next link.")
        self._clear_form_for_next()

    def _add_queue_strip(self, payload, index=None, reveal=True):
        strip = _QueueCard(payload, preferred=self._preferred_height())
        strip.download_requested.connect(self._on_strip_download)
        strip.remove_requested.connect(self._on_strip_remove)
        strip.selected.connect(self._on_card_selected)
        if index is None:
            self.queue_list_layout.addWidget(strip)
            self._queue_strips.append(strip)
        else:
            self.queue_list_layout.insertWidget(index, strip)
            self._queue_strips.insert(min(index, len(self._queue_strips)), strip)
        self.queue_card.setVisible(True)
        self._update_queue_header()
        # A link you just added should be somewhere you can see, not below the
        # fold of a queue that has grown past the window. Skipped while a
        # playlist is being stacked: scrolling to each of 300 cards in turn
        # is wasted work, and the first one is what the status line points at.
        if reveal:
            motion.grow_in(strip)
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(strip, 0, 8))
        return strip

    def _update_queue_header(self):
        """Hook for the queue card's header count; set up in _build_ui."""
        label = getattr(self, "queue_count_label", None)
        if label is None:
            return
        n = len(self._queue_strips)
        label.setText(f"{n} link{'s' if n != 1 else ''}" if n else "")
        self.start_queue_btn.setText("Download all" if n != 1 else "Download")

    def _on_strip_remove(self, strip):
        if strip in self._queue_strips:
            self._queue_strips.remove(strip)
        self._update_queue_header()

        def drop():
            self.queue_list_layout.removeWidget(strip)
            strip.deleteLater()
            if not self._queue_strips:
                self.queue_card.setVisible(False)
        motion.shrink_out(strip, drop)

    def _on_card_selected(self, card):
        """Puts the card's link back in the fetch bar and fills the form from
        it, so a stacked link can still be given a clip range, a different
        container or audio-only -- everything the card itself deliberately
        does not carry."""
        if card._pending:
            self.status_label.setText("Still reading that link's details...")
            return
        for other in self._queue_strips:
            other.set_selected(other is card)
        self._suppress_url_change = True
        try:
            self.url_entry.setText(card.payload["url"])
        finally:
            self._suppress_url_change = False
        cached = getattr(card, "fetched", None)
        if not cached or (not cached.get("height_sizes") and not cached.get("is_image")):
            # A card restored from last session, or from a playlist listing,
            # never had its formats read: read them now.
            self.on_fetch()
            return
        # Everything the form needs was read when the card was stacked: fill
        # it straight away instead of asking the site again.
        self._last_fetch_url = card.payload["url"]
        self._on_fetch_done(cached.get("title") or card.payload.get("title") or "", cached.get("uploader") or "",
                            cached.get("duration") or 0, cached.get("height_sizes") or {}, None,
                            cached.get("thumbnail_url"), bool(cached.get("is_image")))
        pix = card.payload.get("thumb_pixmap")
        if pix is not None:
            self._thumb_pixmap = pix
            self.thumb_label.setPixmap(pix)
            self.thumb_label.setText("")
        if card.payload.get("mode") == "audio":
            self.audio_radio.setChecked(True)
        height = card.payload.get("height")
        if height:
            label = next((lbl for lbl, h in self.res_height_map.items() if h == height), None)
            if label:
                self.res_combo.setCurrentText(label)
        self.status_label.setText("Loaded from the queue: %s" % (cached.get("title") or card.payload["url"]))

    def _drop_cards_for_url(self, url):
        """A link that has been pulled into the form and started from there
        must not also sit in the queue waiting to be started again."""
        for card in list(self._queue_strips):
            if card.payload["url"] == url and card.start_btn.isEnabled():
                self._on_strip_remove(card)

    def _on_strip_download(self, strip):
        """Starts a queued link and takes its card out of the queue -- the
        download carries on as a card in the Download tab, so leaving it
        here too would list one download in two places."""
        self._start_payload(strip.payload)
        self._on_strip_remove(strip)

    def _start_payload(self, payload):
        """Shared by the queue and by on_download() -- one place that turns a
        settings dict into a real job, so a queued item and a directly
        downloaded one cannot drift apart."""
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)
        self._thumb_pixmap = payload.get("thumb_pixmap")

        if payload["is_image"]:
            default_name = re.sub(r"[^\w\-. ]", "_", payload["title"] or "image")[:80] + ".jpg"
            dest = os.path.join(save_dir, default_name)
            job_id = self._start_job(payload["title"], payload["meta"], save_dir)
            img_args = (job_id, payload["thumbnail_url"], dest, save_dir)
            self._jobs[job_id]["relaunch"] = (self._image_download_thread, img_args)
            threading.Thread(target=self._image_download_thread, args=img_args,
                             daemon=True).start()
            return job_id

        job_id = self._start_job(payload["title"], payload["meta"], save_dir)
        dl_args = (job_id, payload["url"], save_dir, payload["mode"], payload["height"],
                   payload["container"], payload["bitrate"], payload["time_range"])
        self._jobs[job_id]["relaunch"] = (self._download_thread, dl_args)
        self._jobs[job_id]["resume_info"] = {
            "url": payload["url"], "save_dir": save_dir, "mode": payload["mode"],
            "height": payload["height"], "container": payload["container"],
            "bitrate": payload["bitrate"], "time_range": payload["time_range"],
            "title": payload["title"], "meta": payload["meta"],
        }
        self._launch(job_id, self._download_thread, dl_args)
        return job_id

    def start_queue(self):
        """Fires every queued line that hasn't been started yet."""
        for strip in list(self._queue_strips):
            if strip.start_btn.isEnabled() and not strip._pending:
                self._on_strip_download(strip)

    def clear_queue(self):
        """Empties the queue. Downloads already running aren't in it any more
        (a started link moves to the Download tab), so nothing is cancelled."""
        for strip in list(self._queue_strips):
            self._on_strip_remove(strip)
        self.status_label.setText("Queue cleared.")

    # ------------------------------------------------------- Link routing ---
    def status_label_set(self, text):
        self.status_label.setText(text)

    def _hand_over(self, route, urls):
        """A link that belongs in another tab goes there, and the field is
        cleared for the next one."""
        self._suppress_url_change = True
        try:
            self.url_entry.clear()
        finally:
            self._suppress_url_change = False
        self._previous_url_text = ""
        self.send_to_tab.emit(route.tab, list(urls))
        where = "Images" if route.tab == link_router.IMAGES else "Torrent"
        self.status_label.setText("That's %s -- opened it in the %s tab." % (route.describe(), where))

    def _start_split(self, url, route, how):
        if how == "form":
            self.fetch_btn.setEnabled(False)
            self.fetch_btn.set_busy(True)
        name = route.name if route.kind in ("ig_profile", "ig_story") and route.name not in ("", "highlights") else ""
        self.status_label.setText("Reading %s%s..." % (route.describe(), " " + name if name else ""))
        cookies = self._cookies_from_browser()

        def progress(n):
            self._split_progress_sig.emit("Reading %s's posts... %d so far" % (name or "the account", n))

        def work():
            try:
                res = downloader.split_media(url, cookies, progress=progress)
                self._split_sig.emit(url, res, "", how)
            except Exception as e:   # noqa: BLE001 -- reported in the tab
                logger.exception("Couldn't read %s", url)
                self._split_sig.emit(url, None, str(e), how)
        threading.Thread(target=work, daemon=True).start()

    def _on_split(self, url, res, err, how):
        route = link_router.classify(url)
        if how == "form":
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.set_busy(False)
        if res is None:
            if how == "form":
                self._on_fetch_error(err)
            else:
                self.status_label.setText("Couldn't read %s: %s" % (url, errors.friendly(err)[0]))
            return
        videos, images = res["videos"], res["images"]
        # One video and nothing else -- a reel posted as /p/, an X video --
        # opens in the form like any video, with its resolutions and sizes.
        # (one read from the page as its file -- Instagram signed out -- is queued as that file)
        direct = any(x in videos[0]["url"] for x in ("cdninstagram.com", "fbcdn.net", "v.redd.it", ".m3u8",
                                                      ".mp4")) if len(videos) == 1 else False
        if how == "form" and len(videos) == 1 and not images and not direct:
            self._last_fetch_url = url
            self.status_label.setText("Fetching info...")
            self.fetch_btn.setEnabled(False)
            self.fetch_btn.set_busy(True)
            threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()
            return
        if how == "form":
            self._clear_form_for_next()
        added = self._stack_entries(videos, None) if videos else 0
        if images:
            self.images_found.emit(res["title"], images, res.get("note", ""), not videos, how == "queue")
        parts = []
        if videos:
            parts.append("stacked %d video%s" % (added, "" if added == 1 else "s"))
        if images:
            parts.append("sent %d picture%s to the Images tab" % (len(images), "" if len(images) == 1 else "s"))
        if not parts:
            text = "Nothing to download was found in %s." % (res["title"] or "that link")
        else:
            text = "%s: %s." % (res["title"] or route.describe().capitalize(), " and ".join(parts))
        if res.get("note"):
            text += " " + res["note"]
        self.status_label.setText(text)

    def open_link(self, url):
        """A link handed over from another tab: fetched exactly as if it had
        been pasted here."""
        self._suppress_url_change = True
        try:
            self.url_entry.setText(url)
        finally:
            self._suppress_url_change = False
        self._previous_url_text = url
        self.on_fetch()

    def stack_found(self, title, entries, note=""):
        """Videos the Images tab found in an account, story or post."""
        added = self._stack_entries(entries, None)
        text = "Stacked %d video%s from %s." % (added, "" if added == 1 else "s", title or "the link")
        self.status_label.setText(text + (" " + note if note else ""))
        return added

    def queue_url(self, url):
        """Entry point for the Browser tab's download button.

        This used to write the link into the URL field and press Fetch. Once
        the field started stacking whatever it was given, that wrote the link,
        stacked it, cleared the field, and then ran Fetch against an empty
        field -- which fell through to the clipboard and put up a "Paste a
        link first" dialog over a queue that had in fact just worked. Stacking
        it directly is what the button always meant."""
        self.queue_links([url])

    def on_download(self):
        """Starts the link currently in the form.

        This used to assemble and launch the job itself, in parallel with
        _start_payload. The two drifted: _start_payload records "relaunch" and
        "resume_info" against the job, which is what the Retry button re-runs
        and what survives a restart, and this copy never did -- so a download
        started from the form could not be retried or resumed, while the same
        download started from a queue card could. One path now, so they cannot
        disagree again.
        """
        if not (self.url_entry.text().strip() or getattr(self, "_last_fetch_url", "")):
            return
        payload, err = self._current_form_payload()
        if payload is None:
            QMessageBox.warning(self, config.APP_NAME, err)
            return
        # The same link must not be left stacked, waiting to be started a
        # second time, once it has been pulled into the form and run.
        self._drop_cards_for_url(payload["url"])
        self._start_payload(payload)
        # The form resets rather than locking: the whole point of batching is
        # that the next link can be pasted while this one runs.
        self._clear_form_for_next()

    def _clear_form_for_next(self):
        """Puts the tab back into 'ready for the next link' state -- the URL
        box is cleared and focused, and the fetched-info panels collapse."""
        self.url_entry.clear()
        self.url_entry.setFocus()
        self.info_card.setVisible(False)
        self.summary_chip.setVisible(False)
        self.download_btn.setEnabled(False)
        self.queue_btn.setEnabled(False)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.set_busy(False)
        self.status_label.setText("Paste the next link, then click Fetch.")
        self.height_sizes = {}
        self.is_image_mode = False
        self.thumbnail_url = None
        self._thumb_pixmap = None
        self.height_sizes = {}
        self.duration = 0
        self.range_check.setChecked(False)
        self.range_check.setEnabled(False)
        self.is_image_mode = False
        self._apply_mode_visibility()

    def _on_image_download(self):
        """Image posts run through the same starter as everything else, so
        they land in the shared Download tab with a Retry that works. A mixed
        batch (a few videos plus an image post) all reports in one place."""
        if not self.thumbnail_url:
            return
        payload, err = self._current_form_payload()
        if payload is None:
            QMessageBox.warning(self, config.APP_NAME, err)
            return
        payload["is_image"] = True
        payload["title"] = self.info_title_label.text() or "image"
        self._drop_cards_for_url(payload["url"])
        self._start_payload(payload)
        self._clear_form_for_next()

    def _start_job(self, title, meta, save_dir):
        """Creates the card in the shared Download tab and registers the
        job locally (cancel flag, pause event, current file path -- the
        card itself lives in download_tab)."""
        job_id = self.download_tab.start_job(
            title, meta, self._thumb_pixmap,
            make_on_cancel=lambda jid: (lambda: self._cancel_job(jid)),
            make_on_pause_toggle=lambda jid: (lambda paused: self._toggle_pause(jid, paused)),
            make_on_play=lambda jid: (lambda: self._play_job(jid)),
            make_on_retry=lambda jid: (lambda: self._retry_job(jid)),
        )
        pause_event = threading.Event()
        pause_event.set()  # set == not paused; progress_hook blocks only while cleared
        self._jobs[job_id] = {
            "cancel": False, "pause_event": pause_event, "path": None,
            "title": title, "save_dir": save_dir, "finished": False,
            # Set by the caller right after this returns: "relaunch" is a
            # (fn, args) pair Retry can re-run as-is; "resume_info" is the
            # same download's plain, JSON-serializable args, used only for
            # cross-restart persistence (image saves set "relaunch" but
            # deliberately not "resume_info" -- not worth resuming a
            # near-instant single-file save across an app restart).
            "relaunch": None, "resume_info": None,
        }
        return job_id

    def save_state(self):
        """Flushes the currently in-progress downloads to disk so they can
        be resumed on the next launch -- mirrors torrent_tab.py's own
        save_state(), wired to app.aboutToQuit the same way. Only jobs with
        "resume_info" (real video/audio downloads, not the near-instant
        single-image-save path) and not yet "finished" (done/cancelled/
        failed) are worth remembering."""
        entries = [
            job["resume_info"] for job in self._jobs.values()
            if job.get("resume_info") and not job.get("finished")
        ]
        download_queue_state.save(entries)

        # Links still waiting in the queue (a started one has left it for the
        # Download tab) have nothing on disk to resume -- they simply have to
        # still be in the queue next time, which is what this remembers.
        video_queue_state.save([card.to_entry() for card in self._queue_strips])

    def _restore_queue(self):
        """Puts back the links that were still stacked when the app closed.

        They come back already resolved -- title, duration and the resolution
        ladder were all saved with them -- so no lookup runs and the queue is
        usable immediately. Only the thumbnail image needs fetching again,
        since a decoded pixmap is not something to write to a settings file.
        """
        for entry in video_queue_state.load():
            try:
                payload, heights = video_queue_state.from_entry(entry)
                card = self._add_queue_strip(payload)
                # A card whose title is still just its URL never resolved --
                # it failed, or the app closed while it was still reading.
                # Restoring it as startable would offer a Download button for
                # a link whose resolutions are unknown, so it gets a fresh
                # lookup instead, exactly as if it had just been pasted.
                if not payload.get("title") or payload["title"] == payload["url"]:
                    card.mark_pending()
                    threading.Thread(target=self._strip_info_thread,
                                     args=(card, payload["url"]), daemon=True).start()
                    continue
                card.restore_heights(heights)
                if payload.get("thumbnail_url"):
                    self._thumb_pool.submit(self._thumb_only_thread, card,
                                            payload["thumbnail_url"])
            except Exception:
                logger.exception("Failed to restore a queued link: %s", entry)

    def _thumb_only_thread(self, card, thumbnail_url):
        """Restored cards already know everything except their picture."""
        # The whole body is guarded, emit included. This runs on a daemon
        # thread that can still be in flight while the tab is being torn
        # down, and an emit into a half-destroyed receiver raises there --
        # where nothing catches it, so it prints a traceback over the app's
        # own output for a thumbnail nobody is waiting for any more.
        try:
            image = downloader.fetch_thumbnail_image(thumbnail_url)
            self._strip_thumb_sig.emit(card, image)
        except Exception:
            logger.debug("Thumbnail refetch failed for %s", thumbnail_url, exc_info=True)

    def _on_strip_thumb(self, card, image):
        if card not in self._queue_strips:
            return
        pixmap = _pil_to_pixmap(image)
        if pixmap is not None and not pixmap.isNull():
            card.payload["thumb_pixmap"] = pixmap
            card.set_thumbnail(pixmap)

    def _resume_pending_downloads(self):
        """Re-launches whatever was still downloading the last time the app
        closed -- called once at startup. Each entry replays through the
        exact same _start_job()/_download_thread() path a fresh download
        would use, so it's correctly wired up (progress card, cancel/pause/
        retry, history on completion); yt-dlp resumes the existing .part
        file on disk via Range requests rather than starting over, since
        the url+save_dir (and so the destination filename) are unchanged."""
        entries = download_queue_state.load()
        for e in entries:
            try:
                save_dir = e["save_dir"]
                os.makedirs(save_dir, exist_ok=True)
                title = e.get("title") or e.get("url") or "Resuming download"
                job_id = self._start_job(title, e.get("meta", "Resuming..."), save_dir)
                dl_args = (
                    job_id, e["url"], save_dir, e["mode"], e.get("height"),
                    e.get("container"), e.get("bitrate", "192"), e.get("time_range"),
                )
                self._jobs[job_id]["relaunch"] = (self._download_thread, dl_args)
                self._jobs[job_id]["resume_info"] = e
                self._launch(job_id, self._download_thread, dl_args)
            except Exception:
                logger.exception("Failed to resume a pending download: %s", e)

    def _cancel_job(self, job_id):
        job = self._jobs.get(job_id)
        if job:
            job["cancel"] = True
            # A job still waiting for a slot has no thread to notice the flag,
            # so it is taken out of line and finished here.
            waiting = next((w for w in self._waiting if w[0] == job_id), None)
            if waiting is not None:
                self._waiting.remove(waiting)
                job["finished"] = True
                self.download_tab.mark_cancelled(job_id)
                self._refresh_waiting_positions()
                return
            # Cancelling a paused job must actually wake the download thread
            # back up -- otherwise it sits forever inside pause_event.wait()
            # and never reaches the cancel check that would raise
            # DownloadCancelled and let the thread exit.
            job["pause_event"].set()

    def _toggle_pause(self, job_id, paused):
        job = self._jobs.get(job_id)
        if not job:
            return
        if paused:
            job["pause_event"].clear()
            # A paused download is doing nothing with its slot; the next one
            # in line can have it.
            if job_id in self._running:
                self._running.discard(job_id)
                self._paused_jobs.add(job_id)
                self._pump()
        else:
            if job_id in self._paused_jobs:
                self._paused_jobs.discard(job_id)
                self._running.add(job_id)
            job["pause_event"].set()

    # ---------------------------------------------------------- Scheduling ---
    def _slot_limit(self):
        return settings_store.max_concurrent(self.settings)

    def _launch(self, job_id, fn, args):
        """Starts a download now if a slot is free, otherwise puts it in line.
        Every video/audio job goes through here -- fresh, retried and resumed
        at startup alike -- so none of them can bypass the limit."""
        if len(self._running) < self._slot_limit():
            self._start_thread(job_id, fn, args)
        else:
            self._waiting.append((job_id, fn, args))
            self._refresh_waiting_positions()

    def _start_thread(self, job_id, fn, args):
        self._running.add(job_id)
        self.download_tab.set_started(job_id)
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _release_slot(self, job_id):
        """A job finished, failed or was cancelled: hand its slot on."""
        self._running.discard(job_id)
        self._paused_jobs.discard(job_id)
        self._pump()

    def _pump(self):
        """Starts waiting jobs until the slots are full again. Also called
        when Settings raises the limit, so a larger number takes effect for
        jobs already in line rather than only for new ones."""
        while self._waiting and len(self._running) < self._slot_limit():
            job_id, fn, args = self._waiting.pop(0)
            job = self._jobs.get(job_id)
            if job is None or job.get("cancel"):
                continue
            self._start_thread(job_id, fn, args)
        self._refresh_waiting_positions()

    def _refresh_waiting_positions(self):
        for position, (job_id, _fn, _args) in enumerate(self._waiting, start=1):
            self.download_tab.set_waiting(job_id, position)

    def settings_changed(self):
        """Called by the Settings panel after any change: a new concurrency
        limit may free slots at once, and the save folder and default format
        chosen there should be what the form shows."""
        self._pump()
        folder = settings_store.get_save_dir(self.settings, "video", config.DEFAULT_DOWNLOAD_DIR)
        if folder and folder != self.dir_entry.text():
            self.dir_entry.setText(folder)
        fmt = (self.settings or {}).get("default_format", "mp4")
        if fmt in ("mp4", "mkv", "mov") and fmt != self.format_combo.currentText():
            self.format_combo.setCurrentText(fmt)
        preferred = self._preferred_height()
        for card in self._queue_strips:
            card._preferred = preferred

    def _play_job(self, job_id):
        job = self._jobs.get(job_id)
        path = job.get("path") if job else None
        if path and os.path.exists(path):
            os.startfile(path)

    def _retry_job(self, job_id):
        """Re-runs the exact same download call that originally started
        this job (its url/save_dir/quality never changed, only "cancel"
        needs resetting). yt-dlp resumes the existing .part file via HTTP
        Range requests automatically since the outtmpl -- and so the
        destination filename -- is deterministic given the same url and
        save_dir, so this picks up roughly where the failed attempt left
        off rather than starting over from zero."""
        job = self._jobs.get(job_id)
        if job is None or not job.get("relaunch"):
            return
        job["cancel"] = False
        job["finished"] = False
        job["pause_event"].set()
        self.download_tab.reset_for_retry(job_id)
        fn, args = job["relaunch"]
        if fn == self._download_thread:
            self._launch(job_id, fn, args)
        else:
            # Image saves take a moment and don't count against the limit.
            threading.Thread(target=fn, args=args, daemon=True).start()

    def _forget_job(self, job_id):
        """Used to pop the job record outright once a download finished --
        but the card's Play button stays live for several seconds after
        completion (download_tab.mark_done's own removal delay), and it
        needs this job's "path" to still resolve during that window, so the
        record is kept around indefinitely instead. Harmless: job_id never
        repeats within a run, so a finished job's small dict just sits idle
        rather than causing any stale-state mixups with later ones."""
        pass

    def _progress_hook(self, job_id, d):
        job = self._jobs.get(job_id)
        if job is None or job["cancel"]:
            raise downloader.DownloadCancelled("Cancelled by user")
        # Blocks the *same* thread that's actually reading/writing the
        # download's bytes -- a genuine pause (no more network reads happen
        # at all), not a cosmetic one, since progress_hook is called
        # synchronously from yt-dlp's own download loop. Re-check cancel
        # after waking: the job may have been cancelled while paused.
        job["pause_event"].wait()
        if job["cancel"]:
            raise downloader.DownloadCancelled("Cancelled by user")

        # yt-dlp's own hook dict carries the real destination path once it's
        # known -- tmpfilename while a .part file is still being written,
        # filename once it's the final name. Either way, once a real file
        # exists on disk the Play button can open it, even mid-download.
        path = d.get("filename") or d.get("tmpfilename")
        if path and path != job.get("path") and os.path.exists(path):
            job["path"] = path
            self._playable_sig.emit(job_id)

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else 0

            parts = []
            if total:
                parts.append(f"{formatting.humanize_size(downloaded)} / {formatting.humanize_size(total)}")
            if speed:
                parts.append(f"{formatting.humanize_size(speed)}/s")
            eta_str = formatting.format_eta(eta) if eta is not None else None
            if eta_str:
                parts.append(f"ETA {eta_str}")
            detail = "  •  ".join(parts) if parts else "Downloading..."
            self._progress_sig.emit(job_id, pct, detail)
        elif d.get("status") == "finished":
            self._progress_sig.emit(job_id, 100, "Merging...")

    def _update_progress(self, job_id, pct, label):
        if job_id in self._jobs:
            self.download_tab.update_progress(job_id, pct, label)

    def _fallback_client_warning(self, client, actual_height=None):
        """YouTube 403'd the default client mid-request and downloader.py
        retried with an alternate one (see run_with_client_fallback's own
        docstring) -- those alternate clients routinely cap out at a much
        lower resolution than the default, so the format selector can end
        up matching a far smaller stream than what was actually requested,
        with nothing else distinguishing that from a normal successful
        download. Reported directly: a requested 4320p/~875MB download
        silently came back as a 21MB file. Surfacing this explicitly beats
        a user assuming the app corrupted their download."""
        detail = f" (actual result: {actual_height}p)" if actual_height else ""
        return (
            f"This download hit a YouTube site restriction partway through and had "
            f"to retry using an alternate connection method{detail} — the quality "
            f"you selected may not have been fully honored. Updating yt-dlp "
            f"(the Updates button) often avoids this."
        )

    def _download_thread(self, job_id, url, save_dir, mode, height, container,
                          bitrate="192", time_range=None):
        hook = lambda d: self._progress_hook(job_id, d)
        # Read at run time, not stored with the job: the download must go out
        # with the same session the fetch used. It didn't -- the fetch honoured
        # Settings > Sign-in while the download ignored it, so a link that only
        # read successfully *because* of a chosen browser's cookies then failed
        # to download without them.
        cookies = self._cookies_from_browser()
        name = (self._jobs.get(job_id) or {}).get("title")
        try:
            if mode == "audio":
                _, final_path, used_fallback = downloader.download_audio(
                    url, save_dir, bitrate, hook, time_range, cookies_from_browser=cookies, name=name)
                warning = self._fallback_client_warning(used_fallback) if used_fallback else ""
                self._download_done_sig.emit(job_id, warning, "audio", final_path)
                return

            info, merged_path, used_fallback = downloader.download_video(
                url, save_dir, height, hook, time_range, cookies_from_browser=cookies, name=name
            )

            warning = ""
            final_path = merged_path
            if merged_path and container != "mkv":
                self._progress_sig.emit(job_id, 100, f"Converting to {container.upper()}...")
                final_path, convert_warning = ffmpeg_utils.convert_container(merged_path, container)
                if convert_warning:
                    warning = (
                        f"Converting to your chosen format failed, so the file was "
                        f"kept as .mkv instead.\n\nDetails: {convert_warning}"
                    )

            if used_fallback:
                fallback_msg = self._fallback_client_warning(used_fallback, info.get("height"))
                warning = f"{warning}\n\n{fallback_msg}" if warning else fallback_msg

            self._download_done_sig.emit(job_id, warning, "video", final_path)
        except Exception as e:
            logger.exception("Download failed for %s", url)
            self._download_error_sig.emit(job_id, str(e))

    def _on_download_done(self, job_id, warning, kind, final_path):
        job = self._jobs.get(job_id)
        if job is None:
            return
        job["finished"] = True
        self._release_slot(job_id)
        save_dir = job["save_dir"]

        if final_path and os.path.exists(final_path):
            # The path progress_hook saw mid-download is the raw/intermediate
            # file -- audio extraction and container conversion can produce
            # a differently-named final file afterward, which is the one
            # Play should actually open now that it exists. Set *before*
            # mark_done()/job_finished below, not after -- job_finished is a
            # "this download is done" announcement, and anything reacting to
            # it should be able to trust job["path"] is already correct by
            # then, not still pointing at a file the postprocessor deleted.
            job["path"] = final_path
            self.download_tab.set_playable(job_id, True)
            try:
                size_bytes = os.path.getsize(final_path)
            except OSError:
                size_bytes = 0
            if kind != "image":  # image path already recorded its own history entry
                download_history.add_entry(
                    kind, job["title"] or os.path.basename(final_path),
                    final_path, save_dir, size_bytes,
                )

        self.download_tab.mark_done(job_id, "✓ Completed" if not warning else "⚠ Completed (see note)")
        self._forget_job(job_id)

        if warning:
            # A real, non-routine problem (container conversion failed, or a
            # YouTube 403 forced a quality-losing client fallback) -- worth
            # an actual popup, unlike the plain-success case above, which
            # used to *also* pop a blocking dialog on every single
            # completion (reported directly as disruptive) and now just
            # updates the inline status text/button instead. warning itself
            # already carries the full, specific explanation from
            # _download_thread -- this is deliberately generic wrapping,
            # not a hardcoded assumption about *which* warning occurred.
            QMessageBox.warning(
                self, config.APP_NAME,
                f"Download finished, but with a note:\n\nSaved to:\n{save_dir}\n\n{warning}",
            )

    def _on_download_error(self, job_id, err):
        job = self._jobs.get(job_id)
        if job is None:
            return
        job["finished"] = True
        self._release_slot(job_id)
        cancelled = job["cancel"]
        self._forget_job(job_id)

        if cancelled:
            self.download_tab.mark_cancelled(job_id)
            return

        self.download_tab.mark_failed(job_id, errors.one_line(err) if err else "Failed.")
        logger.error("Download job %s failed: %s", job_id, err)
