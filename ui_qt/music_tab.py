"""The Music tab: an open music player, with no account and no sign-in.

One search finds songs, albums and artists on YouTube Music, and free music
in the open libraries (Openverse's Creative Commons songs, the Internet
Archive's netlabels and live shows) -- see app/core/music_sources.py.

The look (music_look.py, docs/music-design.md): a quiet dark rail on the
left -- search, the library, moods -- and pages painted in the colours of
their picture: the album's cover, the artist's photo, the song playing. A
page opens with its name as large as it fits and the picture melting into
the page on the right; songs follow in numbered rows, albums and artists in
tiles. The player runs along the bottom in the playing cover's colours, and
opens into a full-screen view over the cover, with karaoke lyrics.

Playback is Qt's own media player (its FFmpeg backend). A YouTube song is
streamed without anyone's sign-in; if the stream won't open, the song is
fetched to a cache and played from there (music_sources.cache_audio).
"""
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QKeySequence, QLinearGradient, QPainter, QPen, \
    QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QStackedWidget, QVBoxLayout,
    QWidget,
)

from app import config
from app.core import lyrics as lyrics_db
from app.core import music_sources
from app.logging_setup import get_logger
from app.utils import music_library
from app.utils import settings as settings_store
from app.utils.formatting import format_eta

from . import music_look as look
from . import theme
from .browser_chrome import ChromeButton, style_menu
from .music_widgets import (
    Hero, Pill, PlayerBar, Rail, RingButton, SectionHeader, SuggestPanel, ThinSlider, TileGrid, Toast, TrackList,
    art, frost_strip, icon_button, thumb_url, total_length,
)
from .music_widgets import ellipsis_count as count

logger = get_logger("music_tab")

try:
    from shiboken6 import isValid as _alive
except ImportError:   # pragma: no cover
    def _alive(obj):
        return obj is not None


# ------------------------------------------------------------ the stage ----
class _Stage(QWidget):
    """Where the pages sit: painted in the current page's colours, which
    cross-fade when the page (or its picture) changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pal = look.DEFAULT
        self._cur = None             # the backdrop at this size, in self.pal
        self._old = None             # the one fading out
        self._stale = None           # shown stretched while the window is being resized
        self._t = 1.0
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._step)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(140)
        self._settle.timeout.connect(self._settled)

    def set_palette(self, pal):
        if pal["base"].rgb() == self.pal["base"].rgb() and pal["glow"].rgb() == self.pal["glow"].rgb():
            return
        self._old = self._cur
        self.pal = pal
        self._cur = None
        self._t = 0.0 if self._old is not None else 1.0
        if self._t < 1.0:
            self._fade.start()
        self.update()

    def _step(self):
        self._t = min(1.0, self._t + 0.045)
        if self._t >= 1.0:
            self._old = None
            self._fade.stop()
        self.update()

    def _settled(self):
        self._stale = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._cur is not None:
            self._stale = self._cur
        self._cur = None
        self._old = None
        self._settle.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        if self._cur is None and self._stale is not None and self._settle.isActive():
            p.drawPixmap(r, self._stale, QRectF(self._stale.rect()))
            p.end()
            return
        if self._cur is None:
            self._cur = look.render_backdrop(self.width(), self.height(), self.pal, self.devicePixelRatioF())
        if self._old is not None and self._t < 1.0:
            p.drawPixmap(r, self._old, QRectF(self._old.rect()))
            p.setOpacity(look.ease(self._t))
        p.drawPixmap(r, self._cur, QRectF(self._cur.rect()))
        p.end()


class _Page(QScrollArea):
    """One page: its header, then its sections, scrolling over the stage."""

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.art_url = None          # the picture the page is painted in
        self.wide_art = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.viewport().setAutoFillBackground(False)
        self.body = QWidget()
        self.body.setAutoFillBackground(False)
        self.body.setStyleSheet("background: transparent;")
        col = QVBoxLayout(self.body)
        col.setContentsMargins(0, 0, 0, 36)
        col.setSpacing(0)
        self.hero = Hero()
        col.addWidget(self.hero)
        self.sections = QWidget()
        self.sec = QVBoxLayout(self.sections)
        self.sec.setContentsMargins(look.GUTTER - 4, 4, look.GUTTER - 4, 0)
        self.sec.setSpacing(6)
        col.addWidget(self.sections)
        col.addStretch(1)
        self.setWidget(self.body)

    def add(self, widget, title=None, link=None, gap=16, grid=None):
        """A section: its label (and link), then `widget`. Returns the label.
        A grid's "See all" shows the rest of it, and only while some is hidden."""
        header = None
        if title:
            if self.sec.count():
                self.sec.addSpacing(gap)
            header = SectionHeader(title, link)
            self.sec.addWidget(header)
        self.sec.addWidget(widget)
        grid = grid or (widget if isinstance(widget, TileGrid) else None)
        if header is not None and header.link is not None and grid is not None:
            header.link.clicked.connect(grid.expand)
            grid.relaid.connect(lambda h=header, g=grid: _alive(h) and _alive(g)
                                and h.link.setVisible(g.hidden_count() > 0))
            header.link.setVisible(grid.hidden_count() > 0)
        return header

    def clear(self):
        while self.sec.count():
            item = self.sec.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.hero.clear_actions()
        self.hero.set_trio([])
        self.verticalScrollBar().setValue(0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.hero.preferred_h = int(max(360, min(560, self.viewport().height() * 0.62)))
        self.hero._layout()


class _Note(QLabel):
    """A quiet line in a section: searching, nothing found, an error."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setFont(look.font(13))
        self.setStyleSheet("color: rgba(255,255,255,165); background: transparent; padding: 4px 4px 10px 4px;")
        self.setWordWrap(True)


# ------------------------------------------------------- full screen --------
class _Art(QWidget):
    """A cover, or a note on glass while there isn't one."""

    def __init__(self, size=44, radius=8, parent=None):
        super().__init__(parent)
        self._size, self._radius, self._pix = size, radius, None
        self.setFixedSize(size, size)

    def set_pixmap(self, pix):
        self._pix = look.rounded(pix, self._size, self._size, self._radius) if pix is not None else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._pix is not None:
            p.drawPixmap(0, 0, self._pix)
        else:
            look.placeholder(p, QRectF(0, 0, self._size, self._size), look.DEFAULT, self._radius)
        p.end()


class _Glass(QWidget):
    """A frosted pane: a light tint, a brighter rim and a highlight along the
    top edge, over the (already softened) cover behind it."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        fill = QLinearGradient(r.topLeft(), r.bottomLeft())
        fill.setColorAt(0, QColor(255, 255, 255, 40))
        fill.setColorAt(1, QColor(255, 255, 255, 14))
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 28, 28)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        p.drawLine(int(r.left() + 28), int(r.top() + 1), int(r.right() - 28), int(r.top() + 1))
        p.end()


class _Lyrics(QWidget):
    """Karaoke: every line of the song in a column that scrolls with it --
    the line being sung large, its words lighting up in the logo's gradient
    as they're sung, the lines around it smaller and fading with distance.
    The wheel scrolls through the words (the column comes back to the song a
    few seconds later); a click on a line jumps the song there. Devanagari
    is set in Nirmala UI (Windows' own Hindi face, with room for its marks
    above and below), Roman letters in the logo's Instrument Serif."""
    seek_requested = Signal(int)
    CUR_PX, OTHER_PX, PLAIN_PX = 46, 30, 27
    ANCHOR = 0.40               # where the line being sung sits, down the column
    BACK_AFTER_S = 4.0          # the wheel's scroll gives way to the song after this

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(360)
        self.lines = []
        self.plain = ""
        self.state = "idle"          # idle | loading | none | ok
        self.script = "en"
        self.index, self.progress = -1, 0.0
        self._rows = None
        self._rows_key = None
        self._offset = None          # where the column is drawn from (px)
        self._manual = 0.0           # the wheel's own scroll (px)
        self._manual_at = 0.0
        self._glide = QTimer(self)
        self._glide.setInterval(16)
        self._glide.timeout.connect(self._step)
        self._hits = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_lyrics(self, state, result=None, script=None):
        self.state = state
        self.lines = list((result or {}).get("lines") or [])
        self.plain = (result or {}).get("plain") or ""
        text = " ".join(t for _ms, t in self.lines[:40]) or self.plain[:400]
        self.script = script or lyrics_db.script_of(text)
        self.index, self.progress = -1, 0.0
        self._rows, self._offset, self._manual = None, None, 0.0
        self.update()

    def set_position(self, ms):
        i, _frac = lyrics_db.current_line(self.lines, ms)
        prog = lyrics_db.sung_fraction(self.lines, i, ms)
        if (i, round(prog, 3)) != (self.index, round(self.progress, 3)):
            moved = i != self.index
            self.index, self.progress = i, prog
            if moved:
                self._rows = None
                if not self._glide.isActive():
                    self._glide.start()
            self.update()

    # ---- layout ----
    def _font(self, px, current=False):
        if self.script == "hi":
            f = QFont("Nirmala UI")
            f.setPixelSize(int(px))
            f.setWeight(QFont.Weight.Bold if current else QFont.Weight.DemiBold)
        else:
            f = QFont("Instrument Serif")
            f.setPixelSize(int(px))
            f.setItalic(True)
        return f

    @staticmethod
    def _wrap(text, f, width):
        fm = QFontMetricsF(f)
        out, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if fm.horizontalAdvance(trial) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = word
        return out + ([cur] if cur else []) or [""]

    def _layout(self):
        key = (self.width(), self.index, self.script, len(self.lines), len(self.plain))
        if self._rows is not None and self._rows_key == key:
            return self._rows
        width = max(120, self.width() - 64)
        rows, y = [], 0.0
        if self.lines:
            for i, (ms, text) in enumerate(self.lines):
                cur = i == self.index
                f = self._font(self.CUR_PX if cur else self.OTHER_PX, cur)
                fm = QFontMetricsF(f)
                lh = fm.height() * (1.12 if self.script == "hi" else 1.02)
                parts = self._wrap(text or "♪", f, width)
                h = lh * len(parts) + (26 if cur else 16)
                rows.append({"y": y, "h": h, "parts": parts, "font": f, "lh": lh, "ms": ms, "i": i})
                y += h
        else:
            f = self._font(self.PLAIN_PX)
            fm = QFontMetricsF(f)
            lh = fm.height() * (1.12 if self.script == "hi" else 1.04)
            for i, text in enumerate(self.plain.splitlines()):
                if not text.strip():
                    y += lh * 0.6
                    continue
                parts = self._wrap(text, f, width)
                rows.append({"y": y, "h": lh * len(parts), "parts": parts, "font": f, "lh": lh, "ms": None, "i": i})
                y += lh * len(parts)
        self._rows, self._rows_key = rows, key
        return rows

    def _target(self):
        rows = self._layout()
        if not rows:
            return 0.0
        if not self.lines:          # words without timings: only the wheel moves them
            return 0.0
        row = rows[max(0, self.index)]
        return row["y"] + row["h"] / 2 - self.height() * self.ANCHOR

    def _limit(self, value):
        rows = self._layout()
        total = (rows[-1]["y"] + rows[-1]["h"]) if rows else 0
        return max(-self.height() * self.ANCHOR, min(value, total - self.height() * 0.25))

    def _step(self):
        import time
        if self._manual and time.monotonic() - self._manual_at > self.BACK_AFTER_S:
            self._manual *= 0.86                # the song takes the column back
            if abs(self._manual) < 1:
                self._manual = 0.0
        want = self._limit(self._target() + self._manual)
        if self._offset is None:
            self._offset = want
        self._offset += (want - self._offset) * 0.16
        if abs(want - self._offset) < 0.5 and not self._manual:
            self._offset = want
            self._glide.stop()
        self.update()

    def wheelEvent(self, event):
        import time
        self._manual -= event.angleDelta().y() * 0.7
        self._manual = self._limit(self._target() + self._manual) - self._target()
        self._manual_at = time.monotonic()
        if not self._glide.isActive():
            self._glide.start()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rows = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        self._hits = []
        if self.state != "ok" or not (self.lines or self.plain):
            msg = {"loading": "Finding the words…",
                   "none": "No lyrics for this song",
                   "idle": "Lyrics show here as the song plays"}.get(self.state, "")
            p.setPen(QColor(234, 242, 255, 150))
            f = QFont("Instrument Serif")
            f.setPixelSize(32)
            f.setItalic(True)
            p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, msg)
            p.end()
            return
        rows = self._layout()
        if self._offset is None:
            self._offset = self._limit(self._target() + self._manual)
        brand_hi, brand, violet = QColor("#7dd3fc"), QColor("#38bdf8"), QColor("#818cf8")
        # the column fades out at its top and bottom edges
        for row in rows:
            top = row["y"] - self._offset
            if top > h or top + row["h"] < 0:
                continue
            edge = min(1.0, max(0.0, min(top + row["h"], h - top) / (h * 0.18)))
            current = self.lines and row["i"] == self.index
            dist = abs(row["i"] - self.index) if self.lines and self.index >= 0 else 0
            fade = max(0.22, 1.0 - dist * 0.17) if self.lines else 0.9
            f = row["font"]
            fm = QFontMetricsF(f)
            p.setFont(f)
            total = sum(len(x) for x in row["parts"]) + len(row["parts"]) - 1
            sung = self.progress * total if current else 0
            seen = 0
            for k, part in enumerate(row["parts"]):
                tw = fm.horizontalAdvance(part)
                x = (w - tw) / 2
                base = top + k * row["lh"] + (row["lh"] - fm.height()) / 2 + fm.ascent()
                if current:
                    p.setPen(QColor(234, 242, 255, int(105 * edge)))
                    p.drawText(QPointF(x, base), part)
                    n = min(max(sung - seen, 0.0), len(part))
                    if n > 0:
                        whole = int(n)
                        reach = fm.horizontalAdvance(part[:whole])
                        if whole < len(part):
                            reach += (n - whole) * fm.horizontalAdvance(part[whole])
                        grad = QLinearGradient(x, 0, x + tw, 0)
                        grad.setColorAt(0, brand_hi)
                        grad.setColorAt(.45, brand)
                        grad.setColorAt(1, violet)
                        p.save()
                        p.setClipRect(QRectF(x - 6, base - fm.ascent() - 8, reach + 6, row["lh"] + 16))
                        p.setPen(QPen(grad, 1))
                        p.drawText(QPointF(x, base), part)
                        p.restore()
                else:
                    past = self.lines and row["i"] < self.index
                    p.setPen(QColor(234, 242, 255, int(255 * fade * edge * (0.5 if past else 0.78))))
                    p.drawText(QPointF(x, base), part)
                seen += len(part) + 1
            if row["ms"] is not None:
                self._hits.append((QRectF(0, top, w, row["h"]), row["ms"]))
        p.end()

    def mousePressEvent(self, event):
        for rect, ms in self._hits:
            if rect.contains(event.position()):
                self.seek_requested.emit(ms)
                return


class _FullView(QWidget):
    """Now playing, full screen, for ambient listening: the album's own cover
    filling the screen -- softened, slowly drifting -- under a frosted-glass
    card with the sharp cover and the controls; and the lyrics beside it,
    karaoke-style, when they're on."""
    closed = Signal()
    lyrics_toggled = Signal(bool)
    script_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = None
        self._t = 0.0
        self._pal = look.DEFAULT
        self._drift = QTimer(self)
        self._drift.setInterval(33)
        self._drift.timeout.connect(self._tick)
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 36)
        outer.setSpacing(16)
        top = QHBoxLayout()
        top.addStretch(1)
        # a Hindi song's words: in Devanagari, or in Roman letters
        self.hi_btn = Pill("हिन्दी", style="outline", parent=self)
        self.en_btn = Pill("English", style="outline", parent=self)
        self.hi_btn.setToolTip("Lyrics in Hindi (Devanagari)")
        self.en_btn.setToolTip("Lyrics in English letters")
        self.hi_btn.clicked.connect(lambda: self.script_chosen.emit("hi"))
        self.en_btn.clicked.connect(lambda: self.script_chosen.emit("en"))
        for b in (self.hi_btn, self.en_btn):
            b.hide()
            top.addWidget(b)
        top.addSpacing(8)
        self.lyrics_btn = icon_button("mic", "Lyrics", 40, 17, self)
        self.lyrics_btn.clicked.connect(lambda: self.lyrics_toggled.emit(not self.lyrics.isVisible()))
        self.close_btn = icon_button("close", "Close (Esc)", 40, 14, self)
        self.close_btn.clicked.connect(self.closed)
        top.addWidget(self.lyrics_btn)
        top.addWidget(self.close_btn)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(36)
        body.addStretch(1)
        self.card = _Glass(self)
        self.card.setFixedWidth(420)
        card = QVBoxLayout(self.card)
        card.setContentsMargins(36, 36, 36, 30)
        card.setSpacing(12)
        self.art = _Art(348, 20)
        card.addWidget(self.art, 0, Qt.AlignmentFlag.AlignHCenter)
        card.addSpacing(6)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setFont(look.font(22, QFont.Weight.Bold))
        self.title.setStyleSheet("color: #ffffff; background: transparent;")
        self.artist = QLabel("")
        self.artist.setFont(look.font(15))
        self.artist.setStyleSheet("color: rgba(255,255,255,200); background: transparent;")
        self.credit = QLabel("")
        self.credit.setFont(look.font(11))
        self.credit.setStyleSheet("color: rgba(255,255,255,140); background: transparent;")
        card.addWidget(self.title)
        card.addWidget(self.artist)
        card.addWidget(self.credit)
        self.seek = ThinSlider()
        card.addWidget(self.seek)
        times = QHBoxLayout()
        self.pos_label = QLabel("0:00")
        self.len_label = QLabel("0:00")
        for lab in (self.pos_label, self.len_label):
            lab.setFont(look.font(11.5, QFont.Weight.Medium))
            lab.setStyleSheet("color: rgba(255,255,255,170); background: transparent;")
        times.addWidget(self.pos_label)
        times.addStretch(1)
        times.addWidget(self.len_label)
        card.addLayout(times)
        ctl = QHBoxLayout()
        ctl.addStretch(1)
        self.prev_btn = icon_button("prev", "Previous", 48, 22, self.card)
        self.play_btn = RingButton("play", "Play", size=64, icon_size=26, ring=0.7, parent=self.card)
        self.next_btn = icon_button("next", "Next", 48, 22, self.card)
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            ctl.addWidget(b)
        ctl.addStretch(1)
        card.addLayout(ctl)
        body.addWidget(self.card, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lyrics = _Lyrics(self)
        self.lyrics.hide()
        body.addWidget(self.lyrics, 2)
        body.addStretch(1)
        outer.addLayout(body, 1)

    def set_background(self, image):
        """The cover itself, lightly softened -- recognisable, not a smear."""
        self._bg = None
        if image is not None and not image.isNull():
            try:
                from PIL import Image, ImageFilter
                buf = image.convertToFormat(QImage.Format.Format_RGBA8888)
                pil = Image.frombuffer("RGBA", (buf.width(), buf.height()), bytes(buf.constBits()),
                                       "raw", "RGBA", buf.bytesPerLine(), 1)
                pil = pil.convert("RGB").resize((360, 360)).filter(ImageFilter.GaussianBlur(5))
                data = pil.tobytes("raw", "RGB")
                self._bg = QPixmap.fromImage(QImage(data, 360, 360, 360 * 3, QImage.Format.Format_RGB888).copy())
            except Exception:   # noqa: BLE001 -- a plain background then
                logger.debug("Couldn't soften the cover", exc_info=True)
        self.update()

    def set_palette(self, pal):
        self._pal = pal
        self.update()

    def set_scripts(self, available, current):
        """The Hindi / English choice, when the song's words come in both."""
        both = self.lyrics.isVisible() and "hi" in available and "en" in available
        for b, code in ((self.hi_btn, "hi"), (self.en_btn, "en")):
            b.setVisible(both)
            b.style = "glass" if code == current else "outline"
            b.update()

    def show_lyrics(self, on):
        self.lyrics.setVisible(on)
        if not on:
            self.hi_btn.hide()
            self.en_btn.hide()
        self.lyrics_btn.tint = "#7dd3fc" if on else None
        self.lyrics_btn.set_tip("Hide lyrics" if on else "Lyrics")
        self.lyrics_btn.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._drift.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._drift.stop()

    def _tick(self):
        self._t += 0.033
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        if self._bg is not None:
            # cover the screen, slowly breathing and drifting
            s = max(w, h) * (1.10 + 0.04 * math.sin(self._t / 9.0))
            dx = math.sin(self._t / 13.0) * w * 0.03
            dy = math.cos(self._t / 17.0) * h * 0.03
            p.drawPixmap(QRectF((w - s) / 2 + dx, (h - s) / 2 + dy, s, s), self._bg,
                         QRectF(0, 0, self._bg.width(), self._bg.height()))
        else:
            p.fillRect(self.rect(), self._pal["deep"])
        deep = self._pal["deep"]
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0, look.with_alpha(deep, 0.48))
        shade.setColorAt(.5, look.with_alpha(deep, 0.26))
        shade.setColorAt(1, look.with_alpha(deep, 0.70))
        p.fillRect(self.rect(), shade)
        look.paint_grain(p, QRectF(self.rect()), 0.7)
        p.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)


# ------------------------------------------------------------- the tab ------
class MusicTab(QWidget):
    GRACE_MS = 2500          # how long a song may take to arrive whole before it's streamed meanwhile
    STALL_MS = 9000          # a stream that hasn't moved for this long is treated as dropped
    _part_sig = Signal(int, str, object, str)
    _stream_sig = Signal(int, str, str)
    _file_sig = Signal(int, str, str)
    _album_sig = Signal(object, object, str)
    _page_sig = Signal(int, str, object, str)
    _keep_sig = Signal(str, str, int, str)
    _library_sig = Signal(object)
    _lyrics_sig = Signal(str, object)
    _suggest_sig = Signal(int, str, object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.music_dir = settings_store.get_save_dir(
            settings, "music", os.path.join(config.DEFAULT_DOWNLOAD_DIR, "Music"))
        self.queue = []
        self.index = -1
        self.shuffle = False
        self.repeat = "off"        # off | all | one
        self._gen = 0              # which search is current
        self._token = 0            # which song's stream is current
        self._page_gen = 0         # which artist / album page is current
        self._kept = {}            # track id -> "waiting" | "busy" | "done"
        self._keep_tracks = {}     # track id -> the track, to try again
        self._keep_queue = []
        self._keeping = None
        self._seeking = False
        self._history = []
        self._here = None
        self._lists = []           # every TrackList on screen, for the playing mark
        self._search = {}
        self._artist_cache = {}
        self._np_pal = look.DEFAULT
        self._results = []
        self._songs_list = None

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        vol = int((settings or {}).get("music_volume", 80))
        self.audio.setVolume((vol / 100.0) ** 2)

        self._part_sig.connect(self._on_part)
        self._stream_sig.connect(self._on_stream)
        self._file_sig.connect(self._on_file)
        self._album_sig.connect(self._on_album)
        self._page_sig.connect(self._on_page)
        self._keep_sig.connect(self._on_keep)
        self._library_sig.connect(self._on_library)
        self._lyrics_sig.connect(self._on_lyrics)
        self._suggest_sig.connect(self._on_suggestions)

        self._build()
        self.bar.volume.setValue(vol)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_error)
        self._reset_playback()
        self._prefetched = set()
        self._prefetcher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="music-prefetch")
        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(1000)
        self._stall_timer.timeout.connect(self._check_stall)
        self._stall_timer.start()
        self.go("home", push=False)

    # ------------------------------------------------------------- layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.rail = Rail()
        self.rail.navigate.connect(lambda k: self.go(k))
        self.rail.searched.connect(self.run_search)
        self.rail.mood.connect(self.run_search)
        row.addWidget(self.rail)
        self.stage = _Stage()
        stage_lay = QVBoxLayout(self.stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background: transparent;")
        stage_lay.addWidget(self.pages)
        self._pages = {}
        for key in ("home", "search", "artist", "album", "songs", "artists", "albums", "local", "queue"):
            page = _Page(key)
            self._pages[key] = page
            self.pages.addWidget(page)
            page.hero.track_clicked.connect(self._trio_clicked)
        corner = QWidget(self.stage)
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(8)
        self.back_btn = RingButton("back", "Back (Alt+Left)", size=34, icon_size=15, ring=0.30,
                                   fill=QColor(0, 0, 0, 90))
        self.back_btn.clicked.connect(self.back)
        self.back_btn.hide()
        self.reload_btn = RingButton("reload", "Reload (F5)", size=34, icon_size=15, ring=0.30,
                                     fill=QColor(0, 0, 0, 90))
        self.reload_btn.clicked.connect(self.reload)
        corner_row.addWidget(self.back_btn)
        corner_row.addWidget(self.reload_btn)
        corner.move(look.GUTTER - 6, 8)
        corner.resize(80, 34)
        self._corner = corner
        self.status = Toast(self.stage)
        # as-you-type suggestions under the search field
        self.suggest = SuggestPanel(self)
        self.suggest.chosen.connect(self._suggestion_chosen)
        self._suggest_gen = 0
        self._suggest_wait = QTimer(self)
        self._suggest_wait.setSingleShot(True)
        self._suggest_wait.setInterval(160)
        self._suggest_wait.timeout.connect(self._ask_suggestions)
        self.rail.search.textEdited.connect(self._typed)
        self.rail.search.installEventFilter(self)
        row.addWidget(self.stage, 1)
        root.addLayout(row, 1)

        self.bar = PlayerBar()
        root.addWidget(self.bar)
        b = self.bar
        b.play_btn.clicked.connect(self.toggle)
        b.next_btn.clicked.connect(self.next)
        b.prev_btn.clicked.connect(self.prev)
        b.shuffle_btn.clicked.connect(self._toggle_shuffle)
        b.repeat_btn.clicked.connect(self._cycle_repeat)
        b.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        b.seek.sliderReleased.connect(self._seek_release)
        b.volume.valueChanged.connect(self._set_volume)
        b.vol_icon.clicked.connect(lambda: self.audio.setMuted(not self.audio.isMuted()) or self._sync_controls())
        b.keep_btn.clicked.connect(lambda: self.current() and self.keep(self.current()))
        b.save_btn.clicked.connect(lambda: self.current() and self.toggle_saved(self.current()))
        b.full_btn.clicked.connect(lambda: self.open_full(False))
        b.lyrics_btn.clicked.connect(lambda: self.open_full(True))
        b.queue_btn.clicked.connect(lambda: self.go("queue"))
        b.art.clicked.connect(lambda: self.go("home"))

        self.full = _FullView(self)
        self.full.closed.connect(self.close_full)
        self.full.lyrics_toggled.connect(self._show_lyrics)
        self.full.script_chosen.connect(self._choose_script)
        self.full.lyrics.seek_requested.connect(lambda ms: self.player.setPosition(ms))
        self.full.play_btn.clicked.connect(self.toggle)
        self.full.next_btn.clicked.connect(self.next)
        self.full.prev_btn.clicked.connect(self.prev)
        self.full.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.full.seek.sliderReleased.connect(lambda: self._seek_release(self.full.seek))

        find = QShortcut(QKeySequence("Ctrl+F"), self)
        find.activated.connect(lambda: (self.rail.search.setFocus(), self.rail.search.selectAll()))
        QShortcut(QKeySequence("Alt+Left"), self).activated.connect(self.back)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.reload)
        self.apply_theme()

    # ---- as-you-type suggestions ----
    def _typed(self, text):
        if text.strip():
            self._suggest_wait.start()
        else:
            self._suggest_gen += 1
            self.suggest.hide()

    def _ask_suggestions(self):
        text = self.rail.search.text().strip()
        if not text:
            return
        self._suggest_gen += 1
        gen = self._suggest_gen

        def work():
            from app.core import ytmusic
            try:
                self._suggest_sig.emit(gen, text, ytmusic.suggestions(text))
            except Exception:   # noqa: BLE001 -- no suggestions; Enter still searches
                logger.debug("No suggestions for %r", text, exc_info=True)
        threading.Thread(target=work, daemon=True).start()

    def _on_suggestions(self, gen, text, found):
        if gen != self._suggest_gen or self.rail.search.text().strip() != text or not self.rail.search.hasFocus():
            return
        field = self.rail.search
        at = field.mapTo(self, QPoint(0, field.height() + 6))
        self.suggest.move(at)
        self.suggest.set_results(text, found.get("queries") or [], found.get("items") or [],
                                 max(field.width(), 430))

    def _suggestion_chosen(self, item):
        self._suggest_gen += 1
        self.suggest.hide()
        kind = item.get("kind")
        if kind == "query":
            self.run_search(item["title"])
        elif kind in ("artist", "album"):
            self.rail.search.clearFocus()
            self.go(kind, item)
        else:
            songs = [x for x in self.suggest.items() if x.get("kind", "track") == "track"] or [item]
            self.play_from(item, songs)

    def eventFilter(self, obj, event):
        if obj is self.rail.search:
            if event.type() == QEvent.Type.KeyPress and self.suggest.isVisible():
                key = event.key()
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    self.suggest.move_selection(1 if key == Qt.Key.Key_Down else -1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self.suggest.activate():
                        return True
                    self._suggest_gen += 1
                    self.suggest.hide()
                if key == Qt.Key.Key_Escape:
                    self._suggest_gen += 1
                    self.suggest.hide()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(120, lambda: not self.rail.search.hasFocus() and self.suggest.hide())
        return super().eventFilter(obj, event)

    # the old names, still read by tests and by anything that searched here
    @property
    def query(self):
        return self.rail.search

    @property
    def results(self):
        return self._songs_list

    def apply_theme(self):
        for b in self.findChildren(ChromeButton):
            b.apply_theme(look.ON_COLOUR)
        self._sync_controls()

    # ------------------------------------------------------------- pages
    def go(self, key, item=None, push=True):
        """Opens a page: home, search, artist / album (with `item`), songs,
        artists, albums, local, queue."""
        if push and self._here is not None and self._here != (key, item):
            self._history.append(self._here)
            del self._history[:-30]
        self._here = (key, item)
        page = self._pages[key]
        if key == "home":
            self._build_home()
        elif key == "search":
            if not self._search:
                self._build_search_empty()
        elif key == "artist":
            self._open_artist(item)
        elif key == "album":
            self._open_album(item)
        elif key in ("songs", "artists", "albums"):
            self._build_saved(key)
        elif key == "local":
            self._build_local()
        elif key == "queue":
            self._build_queue()
        self.pages.setCurrentWidget(page)
        self.rail.select(key)
        self.back_btn.setVisible(bool(self._history))
        self.reload_btn.setVisible(key in ("search", "artist", "album", "local", "home", "songs", "artists",
                                           "albums", "queue"))
        self._corner.raise_()
        self._paint_page(page)

    def show_page(self, key):
        """Kept for callers of the old tab: "search" | "library" | "queue"."""
        self.go({"library": "local"}.get(key, key))

    def reload(self):
        """The page asked for again: the search run again, the artist's or
        album's page fetched again, the folder read again."""
        if self._here is None:
            return
        key, item = self._here
        if key == "search" and self._search.get("query"):
            self.run_search(self._search["query"])
            return
        if key == "artist" and item:
            self._artist_cache.pop(item.get("browse_id"), None)
        self.go(key, item, push=False)

    def back(self):
        if not self._history:
            return
        key, item = self._history.pop()
        self._here = None
        self.go(key, item, push=False)

    def _paint_page(self, page):
        """The stage and the page's header in the colours of its picture --
        the playing song's, for pages without one of their own."""
        url = page.art_url
        if not url:
            cur = self.current()
            url = thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None
        if not url:
            self._set_page_palette(page, look.DEFAULT)
            page.hero.set_art(None)
            return
        pal = art().palette(url)
        if pal is not None:
            self._set_page_palette(page, pal)
        wide = page.wide_art if page.art_url else False

        def got(pm, page=page, url=url, wide=wide):
            if not _alive(page):
                return
            if (page.art_url or self._np_art_url()) != url:
                return
            page.hero.set_art(pm, wide)
            if self.pages.currentWidget() is page:
                self._set_page_palette(page, art().palette(url) or look.DEFAULT)
        art().want(url, got, first=True)

    def _np_art_url(self):
        cur = self.current()
        return thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None

    def _set_page_palette(self, page, pal):
        page.hero.set_palette(pal)
        if self.pages.currentWidget() is page or self.pages.currentWidget() is None:
            self.stage.set_palette(pal)
            self.rail.set_accent(pal["accent"])
            for lst in self._live_lists():
                lst.set_accent(pal["accent"])

    def _live_lists(self):
        self._lists = [lst for lst in self._lists if _alive(lst)]
        return self._lists

    def _track_list(self, **kw):
        lst = TrackList(self._wire_row, **kw)
        lst.set_accent(self.stage.pal["accent"])
        self._lists.append(lst)
        return lst

    def _tile_grid(self, mode="album", rows=2):
        return TileGrid(self._wire_tile, mode=mode, rows=rows)

    def _wire_row(self, row):
        row.play.connect(lambda t, r=row: self.play_from(t, self._context_of(r)))
        row.queue.connect(self.add_to_queue)
        row.keep.connect(self.keep)
        row.remove.connect(self.remove_from_queue)
        row.more.connect(self._row_menu)
        row.open_artist.connect(lambda t: self.go("artist", {"browse_id": t["artist_browse"],
                                                            "title": t.get("artist", "")}))
        row.open_album.connect(lambda t: self.go("album", {"browse_id": t["album_browse"], "title": t.get("album", ""),
                                                          "source": "youtube", "kind": "album"}))
        if self._kept.get(row.track["id"]) in ("busy", "done"):
            row.set_kept(self._kept[row.track["id"]])
        cur = self.current()
        if cur and cur["id"] == row.track["id"]:
            row.set_current(True, self._playing())

    def _context_of(self, row):
        lst = row.parent()
        if isinstance(lst, TrackList):
            return [r.track for r in lst.rows]
        return [row.track]

    def _wire_tile(self, tile):
        tile.clicked.connect(lambda item, t=tile: self._tile_clicked(t, item))
        tile.play.connect(lambda item, t=tile: self._tile_clicked(t, item, play=True))

    def _tile_clicked(self, tile, item, play=False):
        if item.get("kind", "track") == "track":
            grid = tile.parent()
            context = [t.item for t in grid.tiles] if isinstance(grid, TileGrid) else [item]
            self.play_from(item, context)
        elif play:
            self.play_item(item)
        else:
            self.open_item(item)

    def open_item(self, item):
        kind = item.get("kind")
        if kind == "artist":
            self.go("artist", item)
        elif kind == "album":
            self.go("album", item)
        elif kind == "mood":
            self.run_search(item["title"])
        else:
            self.play_from(item, [item])

    def play_item(self, item):
        """A tile's play button: an album plays from its first song."""
        if item.get("kind") == "album":
            self.queue, self.index = [item], 0
            self._play_current()
        else:
            self.play_from(item, [item])

    def _trio_clicked(self, track):
        page = self.pages.currentWidget()
        for lst in self._live_lists():
            if page is not None and page.isAncestorOf(lst) and any(r.track["id"] == track["id"] for r in lst.rows):
                self.play_from(track, [r.track for r in lst.rows])
                return
        self.play_from(track, list(page.hero.trio) if page is not None else [track])

    # ---- home ----
    def _build_home(self):
        page = self._pages["home"]
        page.clear()
        page.art_url = None
        h = page.hero
        cur = self.current()
        if cur:
            playing = self._playing()
            h.set_text("Now playing" if playing else "Paused", cur["title"],
                       " · ".join(x for x in (cur.get("artist"), cur.get("album"), cur.get("year")) if x))
            pp = h.add_action(Pill("Pause" if playing else "Play", "pause" if playing else "play"))
            pp.clicked.connect(self.toggle)
            h.add_action(self._ring("mic", "Lyrics", lambda: self.open_full(True)))
            h.add_action(self._ring("expand", "Full screen", lambda: self.open_full(False)))
            h.add_action(self._save_ring(cur))
            if cur.get("source") != "local":
                h.add_action(self._ring("download", "Download this song", lambda: self.keep(self.current())))
            if cur.get("artist_browse"):
                h.add_action(Pill("Artist", style="outline")).clicked.connect(
                    lambda: self.go("artist", {"browse_id": cur["artist_browse"], "title": cur.get("artist", "")}))
            if cur.get("album_browse"):
                h.add_action(Pill("Album", style="outline")).clicked.connect(
                    lambda: self.go("album", {"browse_id": cur["album_browse"], "title": cur.get("album", ""),
                                              "source": "youtube", "kind": "album"}))
            upcoming = self.queue[self.index + 1:self.index + 4]
            h.set_trio(upcoming, "Up next" if upcoming else "")
        else:
            h.set_text("Music", "Play anything",
                       "Songs, albums and artists from YouTube Music, and free music from open libraries — "
                       "no account, nothing to sign in to.")
            h.add_action(Pill("Search", "search")).clicked.connect(
                lambda: (self.rail.search.setFocus(), self.rail.search.selectAll()))
            h.add_action(Pill("On this PC", style="outline")).clicked.connect(lambda: self.go("local"))
        recent = [t for t in music_library.recent() if t.get("kind", "track") == "track"]
        if recent:
            grid = self._tile_grid("track", rows=1)
            grid.set_items(recent[:16])
            page.add(grid, "Recently played", "See all")
        moods = [{"kind": "mood", "id": "mood:" + k, "title": t, "hue": hue} for k, t, hue in Rail.MOODS]
        mg = self._tile_grid("mood", rows=1)
        mg.set_items(moods)
        page.add(mg, "Start with a mood")
        self._set_current_marks()

    def _ring(self, kind, tip, slot):
        b = RingButton(kind, tip)
        b.clicked.connect(slot)
        return b

    def _save_ring(self, item):
        saved = music_library.is_saved(item)
        b = RingButton("star_filled" if saved else "star", "Saved — click to remove" if saved
                       else "Save to Your music")

        def flip():
            on = self.toggle_saved(item)
            b.set_kind("star_filled" if on else "star")
            b.set_tip("Saved — click to remove" if on else "Save to Your music")
        b.clicked.connect(flip)
        return b

    # ---- search ----
    def _build_search_empty(self):
        page = self._pages["search"]
        page.clear()
        page.hero.set_text("Search", "Find anything",
                           "Type a song, an artist or an album on the left — every library is searched at once.")
        self._songs_list = self._track_list(limit=8)

    def run_search(self, text=None):
        q = (text if isinstance(text, str) else self.rail.search.text()).strip()
        if not q:
            return
        self._suggest_gen += 1
        self.suggest.hide()
        self.rail.search.setText(q)
        self._gen += 1
        gen = self._gen
        self._search = {"query": q, "gen": gen, "parts": {}}
        page = self._pages["search"]
        page.clear()
        page.art_url = None
        page.hero.set_text("Searching", q, "YouTube Music, Openverse and the Internet Archive")
        self._songs_list = self._track_list(limit=8)
        self._free_list = self._track_list(limit=6)
        self._album_grid = self._tile_grid("album", rows=1)
        self._artist_grid = self._tile_grid("artist", rows=1)
        self._live_grid = self._tile_grid("album", rows=1)
        self._sections = {}
        for key, title, widget, link in (("songs", "Songs", self._songs_list, None),
                                         ("albums", "Albums", self._album_grid, "See all"),
                                         ("artists", "Artists", self._artist_grid, "See all"),
                                         ("free", "Free to keep · Creative Commons", self._free_list, None),
                                         ("live", "Live shows & netlabels · Internet Archive", self._live_grid,
                                          "See all")):
            note = _Note("Searching...")
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            col.addWidget(note)
            col.addWidget(widget)
            header = page.add(box, title, link, grid=widget if isinstance(widget, TileGrid) else None)
            self._sections[key] = (header, box, note, widget)
        self.go("search")
        self.status.setText("")
        music_sources.search_everywhere(q, lambda part, value, err: self._part_sig.emit(gen, part, value, err))

    def _section_state(self, key, items, err, empty_text):
        header, box, note, widget = self._sections[key]
        if err:
            note.setText(err)
            note.show()
        elif not items:
            # nothing here: the whole section steps aside
            header.hide()
            box.hide()
            return
        else:
            note.hide()

    def _on_part(self, gen, part, value, err):
        if gen != self._gen:
            return
        self._search["parts"][part] = (value, err)
        page = self._pages["search"]
        if part == "music":
            found = value or {"top": None, "songs": [], "albums": [], "artists": []}
            self._results = found["songs"]
            self._songs_list.set_tracks(found["songs"])
            self._section_state("songs", found["songs"], err, "")
            self._album_grid.set_items(found["albums"])
            self._section_state("albums", found["albums"], "", "")
            self._artist_grid.set_items(found["artists"])
            self._section_state("artists", found["artists"], "", "")
            self._search_hero(found)
        elif part == "free":
            self._free_list.set_tracks(value or [])
            self._section_state("free", value or [], err, "")
            if not self._search["parts"].get("music", (None,))[0] and value and not page.art_url:
                self._search_hero({"top": value[0], "songs": value})
        elif part == "live":
            self._live_grid.set_items(value or [])
            self._section_state("live", value or [], err, "")
        if len(self._search["parts"]) == len(music_sources.PARTS):
            got = [v for v, _e in self._search["parts"].values() if v]
            if not any((v.get("songs") if isinstance(v, dict) else v) for v in got):
                page.hero.set_text("Nothing found", self._search["query"], "Try other words — an artist's name, "
                                   "a song title, or a mood.")
        self._set_current_marks()

    def _search_hero(self, found):
        page = self._pages["search"]
        h = page.hero
        h.clear_actions()
        top = found.get("top") or (found["songs"][0] if found.get("songs") else None)
        if not top:
            return
        if top.get("kind") == "artist":
            h.set_text("Artist" + (" · " + top["subtitle"] if top.get("subtitle") else ""), top["title"], "")
            songs = top.get("songs") or [t for t in found["songs"] if top["title"].lower() in t["artist"].lower()]
            h.set_trio(songs[:3], "Popular")
            h.add_action(Pill("Play", "play")).clicked.connect(
                lambda: self._play_artist(top, shuffle=False))
            h.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_artist(top, shuffle=True)))
            h.add_action(self._save_ring(top))
            h.add_action(Pill("All tracks", style="outline")).clicked.connect(lambda: self.go("artist", top))
            page.art_url, page.wide_art = top.get("artwork"), False
            self._fetch_artist(top["browse_id"], for_search=self._gen)
        elif top.get("kind") == "album":
            h.set_text(" · ".join(x for x in (top.get("type") or "Album", top.get("year")) if x), top["title"],
                       top.get("artist", ""))
            h.set_trio(found["songs"][:3], "Songs")
            h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_item(top))
            h.add_action(self._save_ring(top))
            h.add_action(Pill("Open album", style="outline")).clicked.connect(lambda: self.go("album", top))
            page.art_url, page.wide_art = top.get("artwork"), False
        else:
            h.set_text("Top result · song", top["title"],
                       " · ".join(x for x in (top.get("artist"), top.get("album")) if x))
            rest = [t for t in found["songs"] if t["id"] != top["id"]]
            h.set_trio(rest[:3], "More songs")
            songs = [top] + rest
            h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_from(top, songs))
            h.add_action(self._ring("plus", "Add to the queue", lambda: self.add_to_queue(top)))
            h.add_action(self._save_ring(top))
            if top.get("source") != "local":
                h.add_action(self._ring("download", "Download this song", lambda: self.keep(top)))
            page.art_url, page.wide_art = (thumb_url(top.get("artwork"), 900) if top.get("artwork") else None), False
        if self.pages.currentWidget() is page:
            self._paint_page(page)

    # ---- artist / album pages ----
    def _fetch_artist(self, browse_id, for_search=None):
        if browse_id in self._artist_cache:
            self._page_sig.emit(for_search if for_search is not None else self._page_gen, "artist",
                                self._artist_cache[browse_id], "search" if for_search is not None else "")
            return
        gen = for_search if for_search is not None else self._page_gen
        tag = "search" if for_search is not None else ""

        def work():
            from app.core import ytmusic
            try:
                data = ytmusic.artist(browse_id)
                self._artist_cache[browse_id] = data
                self._page_sig.emit(gen, "artist", data, tag)
            except Exception as e:   # noqa: BLE001
                logger.info("Artist page failed: %s", e)
                self._page_sig.emit(gen, "artist", None, tag or str(e).splitlines()[0][:160])
        threading.Thread(target=work, daemon=True).start()

    def _open_artist(self, item):
        self._page_gen += 1
        page = self._pages["artist"]
        page.clear()
        page.art_url = thumb_url(item.get("artwork"), 900) if item.get("artwork") else None
        page.wide_art = False
        page.hero.set_text("Artist", item.get("title") or "", "Opening...")
        self._fetch_artist(item["browse_id"])

    def _open_album(self, item):
        self._page_gen += 1
        gen = self._page_gen
        page = self._pages["album"]
        page.clear()
        page.art_url = thumb_url(item.get("artwork"), 900) if item.get("artwork") else None
        page.wide_art = False
        page.hero.set_text(item.get("type") or "Album", item.get("title") or "", item.get("artist") or "Opening...")

        def work():
            try:
                if item.get("source") == "archive":
                    tracks = music_sources.expand_archive(item)
                    data = dict(item, tracks=tracks, length=sum(t["duration"] for t in tracks))
                else:
                    from app.core import ytmusic
                    data = ytmusic.album(item["browse_id"])
                self._page_sig.emit(gen, "album", data, "")
            except Exception as e:   # noqa: BLE001
                logger.info("Album page failed: %s", e)
                self._page_sig.emit(gen, "album", None, str(e).splitlines()[0][:160] if str(e) else "failed")
        threading.Thread(target=work, daemon=True).start()

    def _on_page(self, gen, kind, data, tag):
        if tag == "search":
            # the top result's artist page: its banner for the search page
            if gen != self._gen or not data:
                return
            page = self._pages["search"]
            if data.get("artwork"):
                page.art_url, page.wide_art = data["artwork"], True
                if self.pages.currentWidget() is page:
                    self._paint_page(page)
            if data.get("songs"):
                page.hero.set_trio(data["songs"][:3], "Popular")
            return
        if gen != self._page_gen:
            return
        if kind == "artist":
            self._fill_artist(data, tag)
        else:
            self._fill_album(data, tag)

    def _fill_artist(self, a, err):
        page = self._pages["artist"]
        h = page.hero
        if not a:
            h.set_text("Artist", h.title, "Couldn't open this artist: %s" % (err or "no answer"))
            return
        h.set_text("Artist", a["title"], "")
        h.set_trio(a["songs"][:3], "Popular")
        h.clear_actions()
        h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_from(a["songs"][0], a["songs"])
                                                           if a["songs"] else None)
        h.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_artist(a, shuffle=True)))
        h.add_action(self._save_ring(a))
        if a["songs"]:
            lst = self._track_list(show_album=True)
            lst.set_tracks(a["songs"])
            page.add(lst, "Popular")
        for key, title in (("albums", "Albums"), ("singles", "Singles & EPs")):
            if a.get(key):
                grid = self._tile_grid("album", rows=1)
                grid.set_items(a[key])
                page.add(grid, title, "See all")
        if a.get("related"):
            grid = self._tile_grid("artist", rows=1)
            grid.set_items(a["related"])
            page.add(grid, "Fans also like")
        if a.get("about"):
            page.add(_Note(a["about"]), "About")
        if a.get("artwork"):
            page.art_url, page.wide_art = a["artwork"], True
        self._paint_page(page)
        self._set_current_marks()

    def _fill_album(self, al, err):
        page = self._pages["album"]
        h = page.hero
        if not al:
            h.set_text("Album", h.title, "Couldn't open this album: %s" % (err or "no answer"))
            return
        tracks = al.get("tracks") or []
        h.set_text(" · ".join(x for x in (al.get("type") or "Album", al.get("year")) if x), al["title"],
                   " · ".join(x for x in (al.get("artist"), count(len(tracks), "song") if tracks else "",
                                          total_length(tracks)) if x))
        # three of its songs under the name -- for an album with more than a couple
        h.set_trio(tracks[:3] if len(tracks) >= 3 else [], "Tracks")
        h.clear_actions()
        if tracks:
            h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_from(tracks[0], tracks))
            h.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_list(tracks, shuffle=True)))
            h.add_action(self._ring("plus", "Add all to the queue", lambda: [self.add_to_queue(t, quiet=True)
                                                                           for t in tracks]))
            h.add_action(self._ring("download", "Download the album", lambda: [self.keep(t) for t in tracks]))
        h.add_action(self._save_ring(al))
        if al.get("artist_browse"):
            h.add_action(Pill("Artist", style="outline")).clicked.connect(
                lambda: self.go("artist", {"browse_id": al["artist_browse"], "title": al.get("artist", "")}))
        lst = self._track_list(show_art=False, show_album=False)
        lst.set_tracks(tracks)
        page.add(lst, "Tracks")
        if not tracks:
            page.add(_Note("This album has no songs that can be played here."))
        if al.get("license"):
            page.add(_Note("%s — free to play and keep; credit %s." % (al["license"], al.get("artist") or
                                                                        "the artist")), "Licence")
        art_url = thumb_url(al.get("artwork"), 900) if al.get("artwork") else page.art_url
        page.art_url, page.wide_art = art_url, False
        self._paint_page(page)
        self._set_current_marks()

    def _play_artist(self, a, shuffle=False):
        songs = a.get("songs") or (self._artist_cache.get(a.get("browse_id"), {}) or {}).get("songs") or []
        if songs:
            self._play_list(songs, shuffle)
        else:
            self.go("artist", a)

    def _play_list(self, tracks, shuffle=False):
        tracks = list(tracks)
        if not tracks:
            return
        if shuffle:
            random.shuffle(tracks)
        self.play_from(tracks[0], tracks)

    # ---- your music ----
    def _build_saved(self, kind):
        page = self._pages[kind]
        page.clear()
        page.art_url = None
        items = music_library.saved(kind)
        title = {"songs": "Your songs", "artists": "Your artists", "albums": "Your albums"}[kind]
        noun = {"songs": "song", "artists": "artist", "albums": "album"}[kind]
        page.hero.set_text("Your music", title,
                           "%s saved" % count(len(items), noun) if items
                           else "Nothing saved yet — tap the star on a %s to keep it here." % noun)
        if kind == "songs":
            if items:
                page.hero.add_action(Pill("Play", "play")).clicked.connect(lambda: self._play_list(items))
                page.hero.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_list(items, True)))
                page.hero.set_trio(items[:3], "Latest")
            lst = self._track_list()
            lst.set_tracks(items)
            page.add(lst)
        else:
            grid = self._tile_grid("artist" if kind == "artists" else "album", rows=99)
            grid.set_items(items)
            page.add(grid)
        first = next((x for x in items if x.get("artwork")), None)
        if first:
            page.art_url = thumb_url(first["artwork"], 900)
        self._set_current_marks()

    def _build_local(self):
        page = self._pages["local"]
        page.clear()
        page.art_url = None
        page.hero.set_text("On this PC", "Downloaded", self.music_dir)
        page.hero.add_action(Pill("Open folder", "external", style="glass")).clicked.connect(
            lambda: (os.makedirs(self.music_dir, exist_ok=True), os.startfile(self.music_dir)))
        page.hero.add_action(Pill("Change folder", style="outline")).clicked.connect(self._choose_folder)
        self.library = self._track_list(show_art=False)
        self._local_note = _Note("Reading your music folder...")
        page.add(self._local_note)
        page.add(self.library, "Songs")
        self.refresh_library()

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Music folder", self.music_dir)
        if d:
            self.music_dir = os.path.normpath(d)
            settings_store.set_save_dir(self.settings, "music", self.music_dir)
            self._build_local()

    def refresh_library(self):
        folder = self.music_dir
        threading.Thread(target=lambda: self._library_sig.emit(music_sources.library(folder)), daemon=True).start()

    def _on_library(self, tracks):
        if not _alive(getattr(self, "library", None)):
            return
        self.library.set_tracks(tracks[:500])
        self._local_note.setText("" if tracks else "No songs here yet. Songs you download land in this folder "
                                                   "and play offline.")
        self._local_note.setVisible(not tracks)
        page = self._pages["local"]
        page.hero.set_text("On this PC", "Downloaded",
                           "%s · %s" % (count(len(tracks), "song"), self.music_dir))
        if tracks:
            page.hero.set_trio(tracks[:3], "Latest")
        self._set_current_marks()

    def _build_queue(self):
        page = self._pages["queue"]
        page.clear()
        page.art_url = None
        page.hero.set_text("Queue", "Up next",
                           " · ".join(x for x in (count(len(self.queue), "song") if self.queue else "Empty",
                                                  total_length(self.queue)) if x))
        if self.queue:
            page.hero.add_action(Pill("Shuffle", "shuffle", style="glass")).clicked.connect(self._shuffle_queue)
            page.hero.add_action(Pill("Clear", style="outline")).clicked.connect(self.clear_queue)
            page.hero.set_trio(self.queue[self.index + 1:self.index + 4], "Next")
        self.queue_list = self._track_list(in_queue=True)
        self.queue_list.set_tracks(self.queue)
        page.add(self.queue_list)
        if not self.queue:
            page.add(_Note("Add songs with + , or play one to start."))
        self._set_current_marks()

    def _render_queue(self):
        if self.pages.currentWidget() is self._pages["queue"]:
            self._build_queue()
        self.rail.items["queue"].set_count(len(self.queue))

    def _shuffle_queue(self):
        cur = self.current()
        rest = [t for i, t in enumerate(self.queue) if i != self.index]
        random.shuffle(rest)
        self.queue = ([cur] if cur else []) + rest
        self.index = 0 if cur else -1
        self._render_queue()

    def clear_queue(self):
        cur = self.current()
        self.queue = [cur] if cur else []
        self.index = 0 if cur else -1
        self._render_queue()

    # ---- the row menu ----
    def _row_menu(self, track, pos):
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Play next", lambda: self.play_next(track))
        menu.addAction("Add to the queue", lambda: self.add_to_queue(track))
        menu.addAction("Remove from Your music" if music_library.is_saved(track) else "Save to Your music",
                       lambda: self.toggle_saved(track))
        if track.get("source") != "local":
            menu.addAction("Download", lambda: self.keep(track))
        menu.addSeparator()
        if track.get("artist_browse"):
            menu.addAction("Go to artist", lambda: self.go("artist", {"browse_id": track["artist_browse"],
                                                                      "title": track.get("artist", "")}))
        if track.get("album_browse"):
            menu.addAction("Go to album", lambda: self.go("album", {"browse_id": track["album_browse"],
                                                                    "title": track.get("album", ""),
                                                                    "source": "youtube", "kind": "album"}))
        if track.get("page_url") and track.get("source") != "local":
            menu.addAction("Copy link", lambda: QApplication.clipboard().setText(track["page_url"]))
        elif track.get("source") == "local":
            menu.addAction("Show in folder", lambda: os.startfile(os.path.dirname(track["stream"])))
        if track.get("license"):
            menu.addAction("Licence: %s" % track["license"]).setEnabled(False)
        menu.exec(pos)

    def toggle_saved(self, item):
        on = music_library.toggle(item)
        self.status.setText(("Saved to Your music: %s" if on else "Removed from Your music: %s") % item["title"])
        self._sync_saved()
        if self._here and self._here[0] in ("songs", "artists", "albums"):
            self._build_saved(self._here[0])
        return on

    def _sync_saved(self):
        cur = self.current()
        on = bool(cur) and music_library.is_saved(cur)
        self.bar.save_btn.set_kind("star_filled" if on else "star")
        self.bar.save_btn.set_tip("Saved — click to remove" if on else "Save to Your music")
        self.rail.items["songs"].set_count(len(music_library.saved("songs")) or "")

    # ------------------------------------------------------------- queue
    def current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def play_from(self, track, tracks=None):
        """Plays `track`; the rest of the list it's in follows as the queue."""
        tracks = list(tracks) if tracks else [track]
        if not any(t["id"] == track["id"] for t in tracks):
            tracks = [track]
        self.queue = tracks
        self.index = next(i for i, t in enumerate(tracks) if t["id"] == track["id"])
        self._render_queue()
        self._play_current()

    def play_next(self, track):
        if self.index < 0:
            self.play_from(track)
            return
        self.queue.insert(self.index + 1, track)
        self.status.setText("Plays next: %s" % track["title"])
        self._render_queue()

    def add_to_queue(self, track, quiet=False):
        if any(t["id"] == track["id"] for t in self.queue):
            if not quiet:
                self.status.setText("Already in the queue.")
            return
        self.queue.append(track)
        if not quiet:
            self.status.setText("Added to the queue: %s" % track["title"])
        if self.index < 0:
            self.index = len(self.queue) - 1
            self._play_current()
        self._render_queue()

    def remove_from_queue(self, track):
        i = next((i for i, t in enumerate(self.queue) if t["id"] == track["id"]), None)
        if i is None:
            return
        playing = i == self.index
        del self.queue[i]
        if i < self.index:
            self.index -= 1
        if playing:
            if self.index >= len(self.queue):
                self.index = len(self.queue) - 1
            if self.index >= 0:
                self._play_current()
            else:
                self.player.stop()
                self._show_track(None)
        self._render_queue()

    # ------------------------------------------------------------- playing
    def _playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _play_current(self):
        track = self.current()
        if track is None:
            return
        self._token += 1
        token = self._token
        self._reset_playback()
        if track.get("kind") == "album" or (track.get("album_id") and not track.get("stream")):
            # an album: its songs take its place in the queue
            self.status.setText("Opening %s..." % track["title"])

            def work():
                try:
                    self._album_sig.emit(track, music_sources.album_tracks(track), "")
                except Exception as e:   # noqa: BLE001
                    self._album_sig.emit(track, [], music_sources.friendly(e))
            threading.Thread(target=work, daemon=True).start()
            return
        self._show_track(track)
        if track["source"] == "local":
            self._start_file(track["stream"])
            return
        if track["source"] == "youtube":
            path = music_sources.cached(track)
            if path:
                self._start_file(path)
                return
            # The whole song is fetched -- a couple of seconds on most
            # connections -- and played from the file: a stream can drop
            # part-way through a song (heard as the song skipping), a file
            # can't. On a slow connection the stream starts meanwhile, and
            # the file takes over if the stream stops early.
            self.status.setText("Getting the song...")
            self._t0 = time.monotonic()
            threading.Thread(target=self._fetch_file, args=(token, track), daemon=True).start()
            threading.Thread(target=self._fetch_stream, args=(token, track), daemon=True).start()
            return
        self._start_stream(track["stream"])

    def _on_album(self, album, tracks, err):
        i = next((i for i, t in enumerate(self.queue) if t["id"] == album["id"]), None)
        if i is None:
            return
        if err or not tracks:
            self.status.setText("Couldn't open that album: %s" % (err or "it has no audio files"))
            return
        self.queue[i:i + 1] = tracks
        if i == self.index:
            self._play_current()
        self.status.setText("%s: %d songs" % (album["title"], len(tracks)))
        self._render_queue()

    def _reset_playback(self):
        self._started = False
        self._local = False
        self._file = None           # the song's fetched file, once it's here
        self._file_err = ""
        self._stream_err = ""
        self._resume_at = None      # where to carry on, once the file is here
        self._pending_seek = None
        self._retries = 0
        self._refetched = False
        self._last_move = time.monotonic()

    def _fetch_file(self, token, track):
        try:
            self._file_sig.emit(token, music_sources.fetch_song(track), "")
        except Exception as e:   # noqa: BLE001
            self._file_sig.emit(token, "", music_sources.friendly(e))

    def _fetch_stream(self, token, track):
        try:
            self._stream_sig.emit(token, music_sources.resolve_stream(track), "")
        except Exception as e:   # noqa: BLE001
            self._stream_sig.emit(token, "", music_sources.friendly(e))

    def _on_file(self, token, path, err):
        if token != self._token:
            return                  # an earlier song's: it's in the cache for next time
        if err:
            self._file_err = err
            if (not self._started and self._stream_err) or self._resume_at is not None:
                self._failed(err)
            return
        self._file = path
        if not self._started:
            self._start_file(path)
        elif self._resume_at is not None:
            self._start_file(path, self._resume_at)

    def _on_stream(self, token, url, err):
        if token != self._token or self._started:
            return
        if err or not url:
            self._stream_err = err or "no stream"
            if self._file_err:
                self._failed(self._file_err)
            return
        if os.path.exists(url):
            self._start_file(url)
            return
        wait = int(self.GRACE_MS - (time.monotonic() - self._t0) * 1000)
        if wait > 0:
            QTimer.singleShot(wait, lambda: token == self._token and not self._started and self._start_stream(url))
        else:
            self._start_stream(url)

    def _start_file(self, path, at=None):
        self._local = True
        self._resume_at = None
        self._begin(QUrl.fromLocalFile(path), at)
        self._prefetch()

    def _start_stream(self, url, at=None):
        self._local = False
        self._begin(QUrl(url), at)

    def _begin(self, qurl, at=None):
        first = not self._started
        self._started = True
        self._pending_seek = at or None
        self._last_move = time.monotonic()
        self.player.setSource(qurl)
        if at:
            self.player.setPosition(at)
        self.player.play()
        track = self.current()
        if track and first:
            self.status.setText("")
            try:
                music_library.add_recent(track)
            except Exception:   # noqa: BLE001 -- the list of recent songs is a nicety
                logger.debug("Couldn't note a recent song", exc_info=True)

    def _recover(self, why):
        """The stream stopped before the song's end -- dropped, stalled or
        refused: carry on from the fetched file at the same place, rather
        than move on to the next song."""
        track = self.current()
        if track is None:
            return
        at = max(0, self.player.position() - 250)
        logger.info("Stream of %s stopped at %.1fs (%s); carrying on from the file", track["id"], at / 1000, why)
        if self._file:
            self._start_file(self._file, at)
            return
        if track["source"] == "youtube":
            self._resume_at = at
            self.status.setText("Reconnecting...")
            if self._file_err:          # the fetch had failed: once more
                self._file_err = ""
                threading.Thread(target=self._fetch_file, args=(self._token, track), daemon=True).start()
            return
        # an open library's file: opened again where it stopped, twice at most
        if self._retries < 2:
            self._retries += 1
            self._start_stream(track["stream"], at)
        else:
            self._failed(why)

    def _failed(self, why):
        """A song that can't be played at all: said, with a way to try again;
        the queue moves on after a few seconds unless that's taken."""
        track = self.current()
        token = self._token
        self.status.show_message("Couldn't play %s: %s" % (track["title"] if track else "that song", why),
                                 action=("Try again", self._play_current), seconds=7)
        if self.index + 1 < len(self.queue) or self.repeat == "all":
            QTimer.singleShot(7000, lambda: token == self._token and not self._started and self.next(auto=True))

    def _check_stall(self):
        """A stream that hasn't moved for a while is treated as one that dropped."""
        if (self._started and not self._local and self.current() is not None
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and (time.monotonic() - self._last_move) * 1000 > self.STALL_MS):
            self._recover("the stream stalled")

    def _prefetch(self):
        """The next songs fetched while this one plays: they start at once,
        and from a file."""
        for t in self.queue[self.index + 1:self.index + 3]:
            if t.get("source") != "youtube" or t.get("kind", "track") != "track" or t["id"] in self._prefetched:
                continue
            self._prefetched.add(t["id"])
            self._prefetcher.submit(self._prefetch_one, t)

    @staticmethod
    def _prefetch_one(track):
        try:
            music_sources.fetch_song(track)
        except Exception as e:   # noqa: BLE001 -- it's fetched again when it plays
            logger.info("Couldn't fetch %s ahead: %s", track.get("id"), e)

    def _show_track(self, track):
        b, f = self.bar, self.full
        if track is None:
            b.title.setText("Nothing playing")
            b.artist.setText("")
            b.art.set_pixmap(None)
            self._sync_saved()
            return
        b.title.setText(track["title"])
        b.artist.setText(track.get("artist") or "")
        credit = track.get("license") or ("On this PC" if track["source"] == "local" else "")
        b.title.setToolTip(" · ".join(x for x in (track["title"], track.get("artist"), track.get("album"), credit)
                                      if x))
        f.title.setText(track["title"])
        f.artist.setText(track.get("artist") or "")
        f.credit.setText(credit or ("YouTube Music" if track["source"] == "youtube" else ""))
        f.credit.setToolTip(track.get("attribution") or "")
        if f.lyrics.isVisible():
            self._fetch_lyrics(track)
        b.art.set_pixmap(None)
        f.art.set_pixmap(None)
        f.set_background(None)
        self._sync_saved()
        url = thumb_url(track["artwork"], 900) if track.get("artwork") else None
        if url:
            def got(pm, tid=track["id"], url=url):
                cur = self.current()
                if not cur or cur["id"] != tid:
                    return
                b.art.set_pixmap(pm)
                f.art.set_pixmap(pm)
                img = art().image(url)
                f.set_background(img)
                pal = art().palette(url) or look.DEFAULT
                self._np_pal = pal
                b.set_palette(pal, frost_strip(img))
                f.set_palette(pal)
                page = self.pages.currentWidget()
                if page is not None and not page.art_url:
                    self._paint_page(page)
            art().want(url, got, first=True)
        else:
            self._np_pal = look.DEFAULT
            b.set_palette(look.DEFAULT)
            f.set_palette(look.DEFAULT)
        if self.pages.currentWidget() is self._pages["home"]:
            self._build_home()
            self._paint_page(self._pages["home"])
        self._set_current_marks()

    def _set_current_marks(self):
        cur = self.current()
        tid = cur["id"] if cur else None
        playing = self._playing()
        for lst in self._live_lists():
            lst.set_current(tid, playing)

    def toggle(self):
        if self.current() is None:
            songs = self.results
            first = songs.rows[0] if songs is not None and _alive(songs) and songs.rows else None
            if first is not None:
                self.play_from(first.track, [r.track for r in songs.rows])
            return
        if self._playing():
            self.player.pause()
        else:
            self.player.play()

    def next(self, auto=False):
        if not self.queue:
            return
        if auto and self.repeat == "one":
            self.player.setPosition(0)
            self.player.play()
            return
        if self.shuffle and len(self.queue) > 1:
            choices = [i for i in range(len(self.queue)) if i != self.index]
            self.index = random.choice(choices)
        elif self.index + 1 < len(self.queue):
            self.index += 1
        elif self.repeat == "all":
            self.index = 0
        else:
            if auto:
                self.player.stop()
            return
        self._play_current()

    def prev(self):
        if self.player.position() > 3000 or self.index <= 0:
            self.player.setPosition(0)
            return
        self.index -= 1
        self._play_current()

    def _toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self._sync_controls()

    def _cycle_repeat(self):
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self._sync_controls()

    def _set_volume(self, value):
        self.audio.setVolume((value / 100.0) ** 2)     # a loudness curve, not a straight line
        if self.settings is not None:
            self.settings["music_volume"] = int(value)
        self._sync_controls()

    def _seek_release(self, slider=None):
        self._seeking = False
        self.player.setPosition((slider or self.bar.seek).value())

    def _sync_controls(self):
        playing = self._playing()
        for btn in (self.bar.play_btn, self.full.play_btn):
            btn.set_kind("pause" if playing else "play")
            btn.set_tip("Pause" if playing else "Play")
            btn.update()
        accent = self._np_pal["accent"].name()
        self.bar.shuffle_btn.tint = accent if self.shuffle else None
        self.bar.shuffle_btn.set_tip("Shuffle: on" if self.shuffle else "Shuffle: off")
        self.bar.repeat_btn.set_kind("repeat_one" if self.repeat == "one" else "repeat")
        self.bar.repeat_btn.tint = accent if self.repeat != "off" else None
        self.bar.repeat_btn.set_tip({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: this song"}
                                    [self.repeat])
        muted = self.audio.isMuted() or self.bar.volume.value() == 0
        self.bar.vol_icon.set_kind("mute" if muted else "speaker")
        for b in (self.bar.shuffle_btn, self.bar.repeat_btn, self.bar.vol_icon):
            b.update()

    def _on_position(self, ms):
        self._last_move = time.monotonic()
        if self._seeking:
            return
        for s in (self.bar.seek, self.full.seek):
            s.blockSignals(True)
            s.setValue(ms)
            s.blockSignals(False)
        self.bar.pos_label.setText(format_eta(ms // 1000))
        if self.full.isVisible():
            self.full.pos_label.setText(format_eta(ms // 1000))
            if self.full.lyrics.isVisible():
                self.full.lyrics.set_position(ms)

    def _on_duration(self, ms):
        for s in (self.bar.seek, self.full.seek):
            s.setRange(0, max(0, ms))
        self.bar.len_label.setText(format_eta(ms // 1000) if ms else "0:00")
        self.full.len_label.setText(self.bar.len_label.text())

    def _on_state(self, _state):
        self._sync_controls()
        self._set_current_marks()
        home = self._pages["home"]
        cur = self.current()
        if cur and self.pages.currentWidget() is home and home.hero.title == cur["title"]:
            playing = self._playing()
            home.hero.set_text("Now playing" if playing else "Paused", home.hero.title, home.hero.subline)
            first = home.hero.action_row.itemAt(0)
            if first and isinstance(first.widget(), Pill):
                first.widget().setText("Pause" if playing else "Play")
                first.widget().kind = "pause" if playing else "play"
                first.widget().update()

    def _on_media_status(self, status):
        S = QMediaPlayer.MediaStatus
        if status == S.EndOfMedia:
            if not self._local and self._ended_early():
                self._recover("the stream ended early")
                return
            self.next(auto=True)
        elif status in (S.LoadedMedia, S.BufferedMedia) and self._pending_seek:
            at, self._pending_seek = self._pending_seek, None
            self.player.setPosition(at)

    def _ended_early(self):
        # the song's length as listed, too: a stream cut short can report its own
        track = self.current() or {}
        dur = max(self.player.duration(), int(track.get("duration") or 0) * 1000)
        return dur > 0 and self.player.position() < dur - 3000

    def _on_error(self, _err, text):
        track = self.current()
        logger.warning("Music playback error: %s", text)
        if track is None:
            return
        if not self._local:
            self._recover(text or "the stream failed")
            return
        if track["source"] == "youtube" and self._file and not self._refetched:
            # a damaged fetched file: fetched once more
            self._refetched = True
            self._resume_at = max(0, self.player.position())
            try:
                os.remove(self._file)
            except OSError:
                pass
            self._file = None
            self.status.setText("Fetching the song again...")
            threading.Thread(target=self._fetch_file, args=(self._token, track), daemon=True).start()
            return
        self._failed(text or "the file won't play")

    # ------------------------------------------------------------- full view
    def open_full(self, lyrics=False):
        self.full.setGeometry(self.rect())
        self.full.show()
        self.full.raise_()
        self.full.setFocus()
        cur = self.current()
        url = thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None
        if url and art().image(url) is not None:
            self.full.set_background(art().image(url))
            self.full.art.set_pixmap(art().pixmap(url))
        self.full.set_palette(self._np_pal)
        self._show_lyrics(lyrics)

    # ---- karaoke ----
    def _show_lyrics(self, on):
        self.full.show_lyrics(on)
        if on and self.current():
            self._fetch_lyrics(self.current())

    def _fetch_lyrics(self, track):
        self._lyrics_for = track["id"]
        self.full.lyrics.set_lyrics("loading")
        self.full.set_scripts([], None)

        def work():
            self._lyrics_sig.emit(track["id"], lyrics_db.find_all(track))
        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics(self, track_id, variants):
        if track_id != getattr(self, "_lyrics_for", None):
            return
        self._lyric_variants = variants or {}
        self._apply_lyrics()

    def _apply_lyrics(self):
        variants = getattr(self, "_lyric_variants", {}) or {}
        available = [k for k in ("hi", "en") if variants.get(k)]
        if not available:
            self.full.lyrics.set_lyrics("none")
            self.full.set_scripts([], None)
            return
        liked = (self.settings or {}).get("lyrics_script")
        script = liked if liked in available else (variants.get("default") if variants.get("default") in available
                                                     else available[0])
        self.full.lyrics.set_lyrics("ok", variants[script], script)
        self.full.set_scripts(available, script)
        self.full.lyrics.set_position(self.player.position())

    def _choose_script(self, code):
        if self.settings is not None:
            self.settings["lyrics_script"] = code
        self._apply_lyrics()

    def close_full(self):
        self.full.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.full.isVisible():
            self.full.setGeometry(self.rect())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not self.rail.search.hasFocus():
            self.toggle()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------- keeping
    def keep(self, track):
        """Downloads `track` into the music folder -- one song at a time."""
        if not track or track["source"] == "local" or self._kept.get(track["id"]) in ("busy", "done", "waiting"):
            return
        if track.get("kind") == "album":
            self.status.setText("Open the album to download its songs.")
            return
        self._kept[track["id"]] = "waiting"
        self._keep_tracks[track["id"]] = track
        self._keep_queue.append(track)
        self._row_state(track["id"], "busy", 0)
        if self._keeping is None:
            self._keep_next()
        else:
            self.status.setText("Will download %s next" % track["title"])

    def _keep_next(self):
        if not self._keep_queue:
            self._keeping = None
            return
        track = self._keep_queue.pop(0)
        self._keeping = track["id"]
        self._kept[track["id"]] = "busy"
        self.status.setText("Downloading %s..." % track["title"])
        folder = self.music_dir

        def progress(got, total):
            if total:
                self._keep_sig.emit(track["id"], "busy", int(100 * got / total), "")

        def work():
            try:
                path = music_sources.download(track, folder, progress)
                self._keep_sig.emit(track["id"], "done", 100, path or "")
            except Exception as e:   # noqa: BLE001
                logger.exception("Couldn't download %s", track.get("title"))
                self._keep_sig.emit(track["id"], "error", 0, music_sources.friendly(e))
        threading.Thread(target=work, daemon=True).start()

    def _row_state(self, tid, state, pct):
        for lst in self._live_lists():
            for r in lst.rows:
                if r.track["id"] == tid:
                    r.set_kept(state, pct)

    def _on_keep(self, tid, state, pct, info):
        if state == "error":
            self._kept.pop(tid, None)
            self._row_state(tid, "error", None)
            track = self._keep_tracks.get(tid)
            self.status.show_message("Couldn't download %s: %s" % (track["title"] if track else "it", info),
                                     action=("Try again", lambda: track and self.keep(track)), seconds=8)
        else:
            self._kept[tid] = state
            self._row_state(tid, state, pct)
            if state == "done":
                self.status.setText("Saved to %s" % info)
        if state in ("done", "error") and self._keeping == tid:
            self._keeping = None
            self._keep_next()
