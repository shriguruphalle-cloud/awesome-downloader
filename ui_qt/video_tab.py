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

from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app import config
from app.core import downloader, ffmpeg_utils, size_estimate
from app.logging_setup import get_logger
from app.utils import (
    download_history, download_queue_state, formatting,
    settings as settings_store, video_queue_state,
)

from . import theme
from .widgets import Chip, make_card

logger = get_logger("video_tab")


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

    HEIGHT = 58
    THUMB_W = 76
    THUMB_H = 43

    def __init__(self, payload, parent=None):
        super().__init__(parent)
        self.payload = payload
        self._pending = False
        self._heights = []
        self._full_title = ""
        self._full_meta = ""
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("queueCard")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 7)
        lay.setSpacing(10)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(self.THUMB_W, self.THUMB_H)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet(
            "background: rgba(255,255,255,14); border-radius: 6px;")
        lay.addWidget(self.thumb_label, 0)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
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

        self.start_btn = QPushButton("Download")
        self.start_btn.setObjectName("accent")
        self.start_btn.setFixedHeight(26)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(lambda: self.download_requested.emit(self))
        lay.addWidget(self.start_btn, 0)

        # Drawn glyph, and padding reset to 0: the shared #quiet style carries
        # 4px/12px padding for normal text buttons, which on a 22px square
        # squeezes a label clean out of view (it rendered as an empty circle).
        self.remove_btn = QPushButton()
        self.remove_btn.setObjectName("quiet")
        self.remove_btn.setIcon(_close_glyph("#9c9c9d"))
        self.remove_btn.setIconSize(QSize(10, 10))
        self.remove_btn.setStyleSheet("padding: 0px;")
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
        # A dynamic property rather than an inline stylesheet: an inline one
        # would override the #queueCard rules wholesale, including the hover
        # state, and would have to re-declare them all to put them back.
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

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
        self.title_label.setStyleSheet("font-weight: 500; color: #9c9c9d;")
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

    def set_thumbnail(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self.thumb_label.setPixmap(pixmap.scaled(
            self.THUMB_W, self.THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation))

    def _populate_res_combo(self):
        """Only the heights this link actually has, highest first. An audio or
        image job has no resolution to choose, so the control is hidden rather
        than shown empty."""
        if (self.payload["mode"] == "audio" or self.payload.get("is_image")
                or not self._heights):
            self.res_combo.setVisible(False)
            return
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        for h in self._heights:
            self.res_combo.addItem("%dp" % h, h)
        self.res_combo.setCurrentIndex(0)
        self.res_combo.blockSignals(False)
        self.payload["height"] = self._heights[0]
        self.res_combo.setVisible(True)

    def _on_res_changed(self, _index):
        data = self.res_combo.currentData()
        if data is not None:
            self.payload["height"] = data

    def mark_failed(self, err):
        self._pending = False
        self._full_meta = "failed"
        self.meta_label.setText("failed")
        self.meta_label.setToolTip(err)
        self.setToolTip(err)
        self.res_combo.setVisible(False)
        self.start_btn.setEnabled(False)

    def mark_started(self):
        """Progress lives in the Download tab, so a started card stops
        offering its own button rather than duplicating that state."""
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Started")
        self.res_combo.setEnabled(False)
        self.remove_btn.setEnabled(False)

    def mark_not_started(self):
        """Puts a started card back to startable. Used when its download is
        cancelled: the link was never actually fetched, so leaving the card
        reading "Started" would strand it -- unable to be run again, and
        dropped on the next restart as though it had been downloaded."""
        self.start_btn.setEnabled(not self._pending)
        self.start_btn.setText("Download")
        self.res_combo.setEnabled(True)
        self.remove_btn.setEnabled(True)

    def is_started(self):
        return self.start_btn.text() == "Started"

    def to_entry(self):
        return video_queue_state.to_entry(self.payload, self._heights)

    def restore_heights(self, heights):
        self._heights = sorted(heights or [], reverse=True)
        chosen = self.payload.get("height")
        self._populate_res_combo()
        if chosen and self.res_combo.isVisibleTo(self) and chosen in self._heights:
            self.res_combo.setCurrentIndex(self._heights.index(chosen))
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

        self._fetch_done_sig.connect(self._on_fetch_done)
        self._fetch_error_sig.connect(self._on_fetch_error)
        self._progress_sig.connect(self._update_progress)
        self._download_done_sig.connect(self._on_download_done)
        self._download_error_sig.connect(self._on_download_error)
        self._playable_sig.connect(lambda jid: self.download_tab.set_playable(jid, True))
        self._strip_info_sig.connect(self._on_strip_info)
        self._strip_thumb_sig.connect(self._on_strip_thumb)

        self._build_ui()
        self._check_ffmpeg()
        self._resume_pending_downloads()
        self._restore_queue()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        # Every other tab puts its content in a scroll area; this one did not,
        # which is why a queue of seven links came out with the cards drawn on
        # top of each other. A QVBoxLayout given less height than its children
        # need does not overflow -- it squeezes, and fixed-height children then
        # overlap. The content scrolls now, so the tab is usable on a short
        # window and on a laptop screen, not only on a tall one.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Scoped to these two widgets by id. A bare "background: transparent"
        # is a widget-level stylesheet, and a widget stylesheet outranks the
        # application one for every descendant -- so an unscoped rule here
        # repainted the accent buttons inside the tab transparent too.
        self._scroll.setObjectName("videoScroll")
        self._scroll.setStyleSheet("#videoScroll { background: transparent; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("videoScrollBody")
        body.setStyleSheet("#videoScrollBody { background: transparent; }")
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll)

        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---- URL card ----
        url_card, url_layout = make_card()
        root.addWidget(url_card)
        url_layout.addWidget(self._label("Video / Post URL", "muted"))
        url_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste a link here...")
        self.url_entry.returnPressed.connect(self.on_fetch)
        self.url_entry.textChanged.connect(self._on_url_text_changed)
        url_row.addWidget(self.url_entry, 1)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.setFixedWidth(100)
        self.fetch_btn.clicked.connect(self.on_fetch)
        url_row.addWidget(self.fetch_btn)
        url_layout.addLayout(url_row)
        self.status_label = QLabel("Paste a link, then click Fetch.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        url_layout.addWidget(self.status_label)

        # ---- Info card (hidden until Fetch succeeds) ----
        self.info_card, info_layout = make_card()
        info_row = QHBoxLayout()
        info_layout.addLayout(info_row)
        thumb_col = QVBoxLayout()
        self.thumb_label = QLabel("No preview")
        self.thumb_label.setFixedSize(140, 79)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setObjectName("muted")
        thumb_col.addWidget(self.thumb_label)
        self.save_thumb_btn = QPushButton("Save Thumbnail")
        self.save_thumb_btn.setEnabled(False)
        self.save_thumb_btn.clicked.connect(self.on_save_thumbnail)
        thumb_col.addWidget(self.save_thumb_btn)
        info_row.addLayout(thumb_col)

        text_col = QVBoxLayout()
        self.info_title_label = QLabel("")
        self.info_title_label.setObjectName("heading")
        self.info_title_label.setWordWrap(True)
        text_col.addWidget(self.info_title_label)
        self.info_meta_label = QLabel("")
        self.info_meta_label.setObjectName("muted")
        text_col.addWidget(self.info_meta_label)
        text_col.addStretch(1)
        info_row.addLayout(text_col, 1)
        self.info_card.setVisible(False)
        root.addWidget(self.info_card)

        # ---- Download options card ----
        self.opts_card, opts_layout = make_card("DOWNLOAD OPTIONS")
        root.addWidget(self.opts_card)

        mode_row = QHBoxLayout()
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
        video_row_layout.setSpacing(0)

        # The resolution/format controls are grouped into their own widget so
        # image mode can hide *them* while leaving the Download button (which
        # image posts still need) sitting in the same place on the same row.
        self.fmt_group = QWidget()
        fmt_layout = QHBoxLayout(self.fmt_group)
        fmt_layout.setContentsMargins(0, 0, 0, 0)
        # Each label sits directly against its own dropdown, with a slightly
        # wider gap separating the two pairs, so the row reads as two groups
        # rather than four evenly-spaced controls.
        fmt_layout.setSpacing(6)
        video_row_layout = fmt_layout
        video_row_layout.addWidget(self._label("Resolution", "muted"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("best available")
        self.res_combo.currentTextChanged.connect(lambda _t: self._update_selection_summary())
        # AdjustToContents alone: it already sizes to the widest item this
        # ever holds ("2160p  •  ~250.4 MB"). The explicit
        # setMinimumContentsLength that used to sit here was padding it out
        # to 28 characters on top of that, which is where the dead space
        # between the box's text and its arrow came from (reported directly,
        # with the gap marked on a screenshot).
        self.res_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        video_row_layout.addWidget(self.res_combo)
        video_row_layout.addSpacing(18)
        video_row_layout.addWidget(self._label("Format", "muted"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp4", "mkv", "mov"])
        self.format_combo.currentTextChanged.connect(lambda _t: self._refresh_choices())
        # Same reasoning -- "mp4"/"mkv"/"mov" needs three characters, and the
        # old 8-character minimum was pure empty space.
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        video_row_layout.addWidget(self.format_combo)
        # Packs both groups to the left instead of letting the combos absorb
        # the leftover row width.
        video_row_layout.addStretch(1)

        # Download sits at the far right of this same row (asked for
        # directly, marked on a screenshot) rather than in the Save-to card
        # below -- that card is now purely about *where* files land, and
        # starting a download is an options-row action.
        outer_row = self.video_row.layout()
        outer_row.addWidget(self.fmt_group, 1)
        # "Queue" sits beside Download rather than replacing it: the common
        # case is still one link straight to Download, and queueing is the
        # opt-in for lining several up. Quiet styling, because only one
        # control on a view gets to be the accent one.
        self.queue_btn = QPushButton("Queue")
        self.queue_btn.setObjectName("quiet")
        self.queue_btn.setFixedWidth(90)
        self.queue_btn.setEnabled(False)
        self.queue_btn.setToolTip(
            "Add this to the queue below instead of starting it now.")
        self.queue_btn.clicked.connect(self.on_queue)
        outer_row.addWidget(self.queue_btn, 0, Qt.AlignRight)

        self.download_btn = QPushButton("Download")
        self.download_btn.setObjectName("accent")
        self.download_btn.setFixedWidth(150)
        self.download_btn.setEnabled(False)
        self.queue_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.on_download)
        outer_row.addWidget(self.download_btn, 0, Qt.AlignRight)
        opts_layout.addWidget(self.video_row)

        self.bitrate_row = QWidget()
        bitrate_row_layout = QHBoxLayout(self.bitrate_row)
        bitrate_row_layout.setContentsMargins(0, 0, 0, 0)
        bitrate_row_layout.addWidget(self._label("MP3 bitrate", "muted"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["128 kbps", "192 kbps", "256 kbps", "320 kbps"])
        self.bitrate_combo.setCurrentText("192 kbps")
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
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self._label("Start", "muted"))
        self.start_entry = QLineEdit("0:00")
        self.start_entry.setFixedWidth(80)
        self.start_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.start_entry)
        range_layout.addWidget(self._label("End", "muted"))
        self.end_entry = QLineEdit("0:00")
        self.end_entry.setFixedWidth(80)
        self.end_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.end_entry)
        self.range_info_label = QLabel("Format: HH:MM:SS or MM:SS")
        self.range_info_label.setObjectName("muted")
        range_layout.addWidget(self.range_info_label, 1)
        opts_layout.addWidget(self.range_fields)
        self.range_fields.setVisible(False)

        # A centered accent-tinted badge instead of a big left-aligned
        # heading floating in open space -- the previous version sat flush
        # against the left edge with a wide empty gap on the right and
        # nothing tying it visually to either card above or below it
        # (marked directly on a screenshot). A pill styled like the theme
        # toggle button reads as a proper summary chip, centered and
        # snug between the two cards it sits between.
        self.summary_chip = Chip()
        self._restyle_summary_chip()
        self.summary_label = self.summary_chip.label
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addStretch(1)
        summary_row.addWidget(self.summary_chip)
        summary_row.addStretch(1)
        root.addLayout(summary_row)
        self.summary_chip.setVisible(False)

        # ---- Save + progress + download card ----
        out_card, out_layout = make_card()
        root.addWidget(out_card)
        out_layout.addWidget(self._label("Save to", "muted"))
        dir_row = QHBoxLayout()
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self.open_download_folder)
        dir_row.addWidget(open_folder_btn)
        out_layout.addLayout(dir_row)

        # ---- Queue: pasted links, stacked at the foot of the page ----
        # Deliberately the lightest thing on the tab. The form above is
        # where a download gets configured; this only records what has been
        # lined up, so each entry is a single 34px line -- a queue of six is
        # still shorter than one options card. Hidden entirely until
        # something is actually queued, so the tab looks exactly as it did
        # before for anyone who never uses it. It sits immediately below
        # the options card that owns Queue/Download rather than at the
        # foot of the tab, so a link you just banked appears right where
        # you were looking when you banked it.
        self.queue_card, queue_layout = make_card("QUEUE")
        self.queue_list_layout = QVBoxLayout()
        self.queue_list_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_list_layout.setSpacing(1)
        queue_layout.addLayout(self.queue_list_layout)

        queue_actions = QHBoxLayout()
        queue_actions.addStretch(1)
        self.start_queue_btn = QPushButton("Start all")
        self.start_queue_btn.setObjectName("quiet")
        self.start_queue_btn.setFixedHeight(26)
        self.start_queue_btn.setCursor(Qt.PointingHandCursor)
        self.start_queue_btn.clicked.connect(self.start_queue)
        queue_actions.addWidget(self.start_queue_btn)
        queue_layout.addLayout(queue_actions)

        self.queue_card.setVisible(False)
        root.addWidget(self.queue_card)

        self.ffmpeg_warn_label = QLabel("")
        self.ffmpeg_warn_label.setObjectName("dangerText")
        self.ffmpeg_warn_label.setWordWrap(True)
        self.ffmpeg_warn_label.setStyleSheet("color: #ff6961;")
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


        # Held so _on_fetch_done can build the row against the URL that was
        # actually fetched -- the entry field is cleared the moment a row
        # lands, so it cannot be read back at that point.
        self._last_fetch_url = url
        self.status_label.setText("Fetching info...")
        self.fetch_btn.setEnabled(False)
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
            title = info.get("title", "Unknown title")
            uploader = info.get("uploader") or info.get("channel") or ""
            duration = info.get("duration") or 0
            has_video = any(
                f.get("vcodec") not in (None, "none") for f in (info.get("formats") or [])
            )
            is_image = not has_video
            thumbnail_url = downloader.best_thumbnail_url(info)
            thumb_image = downloader.fetch_thumbnail_image(thumbnail_url)
            self._fetch_done_sig.emit(title, uploader, duration, height_sizes,
                                       thumb_image, thumbnail_url, is_image)
        except Exception as e:
            logger.exception("Fetch failed for %s", url)
            self._fetch_error_sig.emit(str(e))

    def _on_fetch_done(self, title, uploader, duration, height_sizes, thumb_image, thumbnail_url, is_image):
        self.is_image_mode = is_image
        if is_image and not thumbnail_url:
            self.status_label.setText(f"Loaded: {title} -- but no video or downloadable image was found for this post.")
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
            self.thumb_label.setPixmap(pix.scaled(140, 79, Qt.KeepAspectRatio, Qt.SmoothTransformation))
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

        self._apply_mode_visibility()

        self.fetch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.queue_btn.setEnabled(True)
    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.status_label.setText("Fetch failed.")
        QMessageBox.critical(
            self, config.APP_NAME, "Couldn't read that link:\n\n" + str(err))

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
        # Solid (alpha=255) colors, not a translucent tint over the Acrylic
        # backdrop. A translucent chip's *effective* colour depends on
        # whatever's composited behind it, which is exactly what made the
        # first version of this badge unreadable: the background tint and
        # the accent-blue text measured only 3.45:1 apart against a light
        # backdrop (WCAG AA needs 4.5) -- "same colour of button and text."
        # A fixed, fully-opaque pair has a contrast ratio that's a plain
        # fact rather than a guess about what's behind the window; both
        # pairs below clear 6.5:1.
        if self._dark_mode():
            self.summary_chip.set_colors(QColor(28, 58, 92), QColor(45, 90, 140), "#96CDFF")
        else:
            self.summary_chip.set_colors(QColor(214, 234, 255), QColor(160, 205, 255), "#0A3278")

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
    def _refresh_choices(self):
        fraction, eff_duration, _, _ = self._current_fraction_and_duration()
        self._rebuild_resolution_choices(fraction, eff_duration)
        self._rebuild_bitrate_choices(eff_duration)
        self._update_selection_summary()

    def _rebuild_resolution_choices(self, fraction=1.0, effective_duration=None):
        prev_height = self.res_height_map.get(self.res_combo.currentText())
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
        self.bitrate_combo.setCurrentText(match or bitrate_list[min(1, len(bitrate_list) - 1)])
        self.bitrate_combo.blockSignals(False)

    def _update_selection_summary(self):
        fraction, eff_duration, valid, _ = self._current_fraction_and_duration()
        range_on = self.range_check.isChecked() and valid

        # Parenthesized size instead of another "• ~X MB" segment, and no
        # "You'll get:" preamble -- the badge's own shape and position
        # (centered, right above the Save-to card) already say "this is
        # what you're about to download," so the label can just state it.
        if self.audio_radio.isChecked():
            kbps = self.bitrate_value_map.get(self.bitrate_combo.currentText(), 192)
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
        """Any link dropped in the field stacks straight away -- one at a
        time or twenty at once. Stacking only batches of two or more was the
        obvious-looking rule and the wrong one: pasting links one by one is
        how a queue actually gets built, and under that rule none of them
        stacked at all.

        The form is still reachable for a download that needs more than a
        resolution (a clip range, a specific container): copy the link and
        press Fetch with the field empty, which reads the clipboard directly
        and fills the form without going through the queue."""
        if self._suppress_url_change:
            return
        links = self._split_links(text)
        if not links:
            return
        self._suppress_url_change = True
        try:
            self.url_entry.clear()
        finally:
            self._suppress_url_change = False
        self.queue_links(links)

    def queue_links(self, urls):
        """Stacks every link straight away, then fills in each title and best
        resolution as its lookup comes back."""
        added = 0
        for url in urls:
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
            "bitrate": "192",
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
            has_video = any(
                f.get("vcodec") not in (None, "none") for f in (info.get("formats") or [])
            )
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
                "is_image": not has_video,
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
            self.status_label.setText("One queued link could not be read: %s" % err)
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
        strip.resolve(info, _pil_to_pixmap(info.get("thumb_image")))
        if not any(st._pending for st in self._queue_strips):
            self.status_label.setText("Queue ready.")

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
            bitrate = int(m.group(1)) if m else 192

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
        self._add_queue_strip(payload)
        self.status_label.setText("Queued. Paste the next link.")
        self._clear_form_for_next()

    def _add_queue_strip(self, payload):
        strip = _QueueCard(payload)
        strip.download_requested.connect(self._on_strip_download)
        strip.remove_requested.connect(self._on_strip_remove)
        strip.selected.connect(self._on_card_selected)
        self.queue_list_layout.addWidget(strip)
        self._queue_strips.append(strip)
        self.queue_card.setVisible(True)
        # A link you just added should be somewhere you can see, not below the
        # fold of a queue that has grown past the window.
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(strip, 0, 8))
        return strip

    def _on_strip_remove(self, strip):
        if strip in self._queue_strips:
            self._queue_strips.remove(strip)
        self.queue_list_layout.removeWidget(strip)
        strip.deleteLater()
        if not self._queue_strips:
            self.queue_card.setVisible(False)

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
        self.on_fetch()

    def _drop_cards_for_url(self, url):
        """A link that has been pulled into the form and started from there
        must not also sit in the queue waiting to be started again."""
        for card in list(self._queue_strips):
            if card.payload["url"] == url and card.start_btn.isEnabled():
                self._on_strip_remove(card)

    def _on_strip_download(self, strip):
        job_id = self._start_payload(strip.payload)
        if job_id is not None and job_id in self._jobs:
            # Remembered so cancelling the download can hand the card back.
            self._jobs[job_id]["card"] = strip
        strip.mark_started()

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
        threading.Thread(target=self._download_thread, args=dl_args, daemon=True).start()
        return job_id

    def start_queue(self):
        """Fires every queued line that hasn't been started yet."""
        for strip in list(self._queue_strips):
            if strip.start_btn.isEnabled() and not strip._pending:
                self._on_strip_download(strip)

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

        # Links that were stacked but never run (or whose run was cancelled)
        # have no bytes on disk and nothing to resume -- they simply have to
        # still be in the queue next time, which is what this remembers.
        video_queue_state.save([
            card.to_entry() for card in self._queue_strips
            if not card.is_started()
        ])

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
                    threading.Thread(target=self._thumb_only_thread,
                                     args=(card, payload["thumbnail_url"]),
                                     daemon=True).start()
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
                threading.Thread(target=self._download_thread, args=dl_args, daemon=True).start()
            except Exception:
                logger.exception("Failed to resume a pending download: %s", e)

    def _cancel_job(self, job_id):
        job = self._jobs.get(job_id)
        if job:
            job["cancel"] = True
            # If this job was started from a queue card, that card goes back
            # to being startable rather than sitting there reading "Started"
            # for a download that is not happening.
            card = job.get("card")
            if card is not None and card in self._queue_strips:
                card.mark_not_started()
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
        else:
            job["pause_event"].set()

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
            f"to retry using an alternate connection method{detail} -- the quality "
            f"you selected may not have been fully honored. Updating yt-dlp "
            f"(the Updates button) often avoids this."
        )

    def _download_thread(self, job_id, url, save_dir, mode, height, container,
                          bitrate="192", time_range=None):
        hook = lambda d: self._progress_hook(job_id, d)
        try:
            if mode == "audio":
                _, final_path, used_fallback = downloader.download_audio(
                    url, save_dir, bitrate, hook, time_range)
                warning = self._fallback_client_warning(used_fallback) if used_fallback else ""
                self._download_done_sig.emit(job_id, warning, "audio", final_path)
                return

            info, merged_path, used_fallback = downloader.download_video(
                url, save_dir, height, hook, time_range
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
        cancelled = job["cancel"]
        self._forget_job(job_id)

        if cancelled:
            self.download_tab.mark_cancelled(job_id)
            return

        self.download_tab.mark_failed(
            job_id, f"Failed: {err.splitlines()[0][:120]}" if err else "Failed.")
        logger.error("Download job %s failed: %s", job_id, err)
