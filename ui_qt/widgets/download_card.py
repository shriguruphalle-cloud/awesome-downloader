"""One active download, rendered as a compact card.

16:9 thumbnail on the left, title/meta on the right, progress bar underneath.
Several of these stack in the Video tab so a batch of downloads is all
visible at once, newest on top.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .card import _PaintedCard
from .progress import AnimatedProgressBar

# 16:9 exactly -- asked for directly. Everything downstream (the placeholder,
# the scaled pixmap) derives from these two numbers so the ratio can't drift.
THUMB_W, THUMB_H = 128, 72


def _play_icon(color, size=22):
    """A plain filled triangle -- not a Unicode "▶" glyph, which has the
    same faint/invisible-glyph risk already hit (and fixed) on the
    browser toolbar's own icons."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    w, h = size * 0.36, size * 0.42
    cx, cy = size / 2 + size * 0.03, size / 2
    path = QPainterPath()
    path.moveTo(cx - w / 2, cy - h / 2)
    path.lineTo(cx + w / 2, cy)
    path.lineTo(cx - w / 2, cy + h / 2)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class DownloadCard(_PaintedCard):
    def __init__(self, title, meta, pixmap, dark_mode, on_cancel,
                 on_pause_toggle=None, on_play=None, on_retry=None, parent=None):
        super().__init__(parent=parent)
        self._on_cancel = on_cancel
        self._on_pause_toggle = on_pause_toggle
        self._on_play = on_play
        self._on_retry = on_retry
        self._paused = False
        self._playable = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setObjectName("muted")
        self.thumb_label.setScaledContents(False)
        self.set_thumbnail(pixmap)
        top.addWidget(self.thumb_label, 0, Qt.AlignTop)

        # Play button overlaid on the thumbnail itself (Chrome-downloads-
        # bar-style), not a separate control elsewhere -- hidden until
        # set_playable() confirms a real file exists on disk, since a
        # download can be played partway through (whatever bytes have
        # landed so far), not just once fully complete.
        self.play_btn = QPushButton(self.thumb_label)
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self._handle_play)
        self.play_btn.setVisible(False)
        self.play_btn.move((THUMB_W - 30) // 2, (THUMB_H - 30) // 2)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.title_label.setWordWrap(True)
        text_col.addWidget(self.title_label)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("muted")
        self.meta_label.setWordWrap(True)
        text_col.addWidget(self.meta_label)
        self.detail_label = QLabel("Starting...")
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        text_col.addWidget(self.detail_label)
        text_col.addStretch(1)
        top.addLayout(text_col, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("historyPlain")
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.clicked.connect(self._handle_pause_toggle)
        self.pause_btn.setVisible(self._on_pause_toggle is not None)
        btn_col.addWidget(self.pause_btn)
        # Hidden until a failure -- a stuck/failed download's only way
        # forward besides dismissing it entirely (Chrome's own downloads
        # bar has the same "Retry" affordance on a failed item).
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setObjectName("historyPlain")
        self.retry_btn.setCursor(Qt.PointingHandCursor)
        self.retry_btn.clicked.connect(self._handle_retry)
        self.retry_btn.setVisible(False)
        btn_col.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("historyPlain")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._handle_cancel)
        btn_col.addWidget(self.cancel_btn)
        top.addLayout(btn_col)

        root.addLayout(top)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self.progress_bar = _make_bar(dark_mode)
        bar_row.addWidget(self.progress_bar, 1)
        self.pct_label = QLabel("0%")
        self.pct_label.setFixedWidth(42)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar_row.addWidget(self.pct_label)
        root.addLayout(bar_row)

        self._icon_color = "#f2f2f4" if dark_mode else "#1c1c1e"
        self._restyle_overlay_icons()

    # ------------------------------------------------------------ content ---
    def set_thumbnail(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            # KeepAspectRatioByExpanding + crop keeps the tile exactly 16:9
            # even when the source thumbnail isn't (YouTube serves 4:3 for
            # some older uploads), instead of letterboxing it inside the box.
            scaled = pixmap.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding,
                                    Qt.SmoothTransformation)
            x = max(0, (scaled.width() - THUMB_W) // 2)
            y = max(0, (scaled.height() - THUMB_H) // 2)
            self.thumb_label.setPixmap(scaled.copy(x, y, THUMB_W, THUMB_H))
            self.thumb_label.setText("")
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText("No preview")

    def set_title(self, text):
        self.title_label.setText(text)

    def set_meta(self, text):
        self.meta_label.setText(text)

    def set_playable(self, playable):
        """Called once a real, openable file exists on disk for this job --
        a direct download knows its destination path from the start, a
        yt-dlp video download only once ffmpeg/yt-dlp has actually written
        real bytes to a real path (its progress hook's own "filename")."""
        self._playable = playable
        self.play_btn.setVisible(playable)

    # ----------------------------------------------------------- progress ---
    def set_progress(self, pct, detail):
        self.progress_bar.setValue(int(max(0.0, min(100.0, pct))))
        self.progress_bar.set_animating(True)
        self.pct_label.setText(f"{pct:.0f}%")
        if detail:
            self.detail_label.setText(detail)

    def set_complete(self, text="✓ Completed"):
        self.progress_bar.setValue(100)
        self.progress_bar.set_complete(True)
        self.pct_label.setText("100%")
        self.detail_label.setText(text)
        self.cancel_btn.setVisible(False)
        self.pause_btn.setVisible(False)
        self.retry_btn.setVisible(False)

    def set_failed(self, text):
        self.progress_bar.set_animating(False)
        self.detail_label.setText(text)
        self.cancel_btn.setText("Dismiss")
        self.cancel_btn.setEnabled(True)
        self.pause_btn.setVisible(False)
        self.retry_btn.setVisible(self._on_retry is not None)

    def reset_for_retry(self):
        """Puts the card back into a fresh "downloading" look right before
        the job actually relaunches -- same starting state _start_job's
        card begins in, so a retried download isn't left showing its old
        failure text/progress while the new attempt is already running."""
        self.retry_btn.setVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.set_complete(False)
        self.progress_bar.set_animating(True)
        self.pct_label.setText("0%")
        self.detail_label.setText("Retrying...")
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setEnabled(True)
        # Undo set_failed()'s raw rewiring of this signal (Dismiss ->
        # remove_job, connected directly by download_tab.py's mark_failed,
        # bypassing this card's own _handle_cancel) back to normal.
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self._handle_cancel)
        self.pause_btn.setVisible(self._on_pause_toggle is not None)

    def apply_theme(self, dark_mode):
        self.progress_bar.set_colors(*_bar_colors(dark_mode))
        self._icon_color = "#f2f2f4" if dark_mode else "#1c1c1e"
        self._restyle_overlay_icons()

    def _restyle_overlay_icons(self):
        self.play_btn.setIcon(_play_icon(self._icon_color))
        self.play_btn.setIconSize(self.play_btn.size() * 0.55)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 140);
                border: none;
                border-radius: 15px;
            }
            QPushButton:hover { background: rgba(0, 0, 0, 190); }
        """)
        self._update_pause_icon()

    def _update_pause_icon(self):
        if not self.pause_btn.isVisible() and self.pause_btn.text() not in ("Pause", "Resume"):
            return
        self.pause_btn.setText("Resume" if self._paused else "Pause")

    # -------------------------------------------------------- interactions --
    def _handle_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling...")
        if self._on_cancel:
            self._on_cancel()

    def _handle_pause_toggle(self):
        self._paused = not self._paused
        self.pause_btn.setText("Resume" if self._paused else "Pause")
        self.progress_bar.set_animating(not self._paused)
        if self._on_pause_toggle:
            self._on_pause_toggle(self._paused)

    def _handle_play(self):
        if self._on_play:
            self._on_play()

    def _handle_retry(self):
        if self._on_retry:
            self._on_retry()


def _bar_colors(dark_mode):
    from .. import theme
    t = theme.tokens(dark_mode=dark_mode)
    track = QColor(255, 255, 255, 26) if dark_mode else QColor(0, 0, 0, 26)
    return track, t["progress"], t["success"]


def _make_bar(dark_mode):
    return AnimatedProgressBar(*_bar_colors(dark_mode))
