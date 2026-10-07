"""The Music tab's parts: the rail on the left, the big page header, song
rows, album and artist tiles, and the player along the bottom. The look
itself -- colours, type, the picture melting into the page -- is in
music_look.py; how they're put together is music_tab.py."""
import math
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QSlider, QVBoxLayout,
    QWidget,
)

from app.logging_setup import get_logger
from app.utils.formatting import format_eta

from . import music_look as look
from .browser_chrome import ChromeButton, draw_icon

logger = get_logger("music_widgets")


# ------------------------------------------------------------- pictures ----
class ArtLoader(QObject):
    """Covers and photos, fetched once each on a few worker threads and
    handed back on the UI thread; their page colours worked out once each.
    The pictures themselves are kept up to BUDGET bytes, the least recently
    used let go first (a 900 px cover is ~3 MB once decoded)."""
    _done = Signal(str, object)
    BUDGET = 160 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images = {}       # url -> QImage
        self._pixmaps = {}      # url -> QPixmap
        self._sizes = OrderedDict()   # url -> bytes held, oldest use first
        self._held = 0
        self._palettes = {}     # url -> palette (small; kept)
        self._waiting = {}      # url -> [callback(QPixmap)]
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="music-art")
        # a page's own picture never waits behind a list's thumbnails
        self._first = ThreadPoolExecutor(max_workers=2, thread_name_prefix="music-art-first")
        self._done.connect(self._deliver)

    def want(self, url, callback, first=False):
        """callback(QPixmap) once the picture at `url` is here (now, if it is).
        `first`: a page's own picture, fetched ahead of everything else."""
        if not url:
            return
        pm = self.pixmap(url)
        if pm is not None:
            callback(pm)
            return
        waiting = self._waiting.setdefault(url, [])
        waiting.append(callback)
        if len(waiting) == 1:
            (self._first if first else self._pool).submit(self._fetch, url, 20 if first else 10)

    def pixmap(self, url):
        pm = self._pixmaps.get(url)
        if pm is None and url in self._images:
            pm = self._pixmaps[url] = QPixmap.fromImage(self._images[url])
        if url in self._sizes:
            self._sizes.move_to_end(url)
        return pm

    def image(self, url):
        if url in self._sizes:
            self._sizes.move_to_end(url)
        return self._images.get(url)

    def _hold(self, url, img):
        size = img.sizeInBytes() * 2          # the image and its pixmap
        self._images[url] = img
        self._sizes[url] = size
        self._held += size
        while self._held > self.BUDGET and len(self._sizes) > 1:
            old, n = self._sizes.popitem(last=False)
            self._held -= n
            self._images.pop(old, None)
            self._pixmaps.pop(old, None)

    def palette(self, url):
        """The page colours for the picture at `url`, if it's here yet."""
        if url in self._palettes:
            return self._palettes[url]
        img = self._images.get(url)
        if img is None:
            return None
        pal = self._palettes[url] = look.palette_from_image(img)
        return pal

    def _fetch(self, url, timeout=10):
        img = None
        try:
            if url.startswith(("http://", "https://")):
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AwesomeDownloader"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read(6 * 1024 * 1024)
                img = QImage.fromData(data)
            else:
                img = QImage(url)
            if img.isNull():
                img = None
        except Exception:   # noqa: BLE001 -- a placeholder stays
            logger.debug("No picture from %s", url, exc_info=True)
        self._done.emit(url, img)

    def _deliver(self, url, img):
        if img is not None:
            self._hold(url, img)
        pm = self.pixmap(url) if img is not None else None
        for cb in self._waiting.pop(url, []):
            if pm is None:
                continue
            try:
                cb(pm)
            except RuntimeError:
                pass        # its widget went away meanwhile


_loader = None


def art():
    global _loader
    if _loader is None:
        _loader = ArtLoader()
    return _loader


def thumb_url(url, px):
    from app.core import ytmusic
    return ytmusic.sized(url, px) if url else url


# --------------------------------------------------------------- buttons ----
def icon_button(kind, tip, size=32, icon_size=16, parent=None):
    b = ChromeButton(kind, tip, size=size, icon_size=icon_size, parent=parent)
    b.apply_theme(look.ON_COLOUR)
    return b


class RingButton(ChromeButton):
    """An icon in a thin white ring -- the page header's round actions, and
    the player's play button."""

    def __init__(self, kind, tip, size=38, icon_size=16, ring=0.38, fill=None, parent=None):
        super().__init__(kind, tip, size=size, icon_size=icon_size, parent=parent)
        self.ring = ring
        self.fill = fill            # a dark disc behind it, where it floats over content
        self.apply_theme(look.ON_COLOUR)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(look.with_alpha(look.TEXT, self.ring + 0.25 * self._hover.value), 1.3))
        p.setBrush(self.fill if self.fill is not None else Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
        p.end()
        super().paintEvent(event)


class Pill(QAbstractButton):
    """A worded action. "solid": white, the page's one main action (Play);
    "outline": a thin sharp-cornered frame, small caps (All tracks);
    "glass": a quiet translucent fill."""

    def __init__(self, text, kind=None, style="solid", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.kind = kind
        self.style = style
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _font(self):
        if self.style == "outline":
            return look.label_font(10.5)
        return look.font(12.5, QFont.Weight.Bold, spacing=0.6)

    def sizeHint(self):
        fm = QFontMetricsF(self._font())
        text = self.text().upper() if self.style == "outline" else self.text()
        w = fm.horizontalAdvance(text) + (34 if self.style != "outline" else 24) + (22 if self.kind else 0)
        return QSize(int(w), 30 if self.style == "outline" else 38)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.style == "solid":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255) if not self._hover else QColor(236, 238, 244))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = QColor(10, 11, 16)
        elif self.style == "outline":
            p.setPen(QPen(look.with_alpha(look.TEXT, 0.85 if self._hover else 0.55), 1))
            p.setBrush(look.with_alpha(look.TEXT, 0.10) if self._hover else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 2, 2)
            fg = look.TEXT
        else:
            p.setPen(QPen(look.with_alpha(look.TEXT, 0.16), 1))
            p.setBrush(look.with_alpha(look.TEXT, 0.20 if self._hover else 0.12))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = look.TEXT
        if not self.isEnabled():
            fg = look.with_alpha(fg, 0.45)
        text = self.text().upper() if self.style == "outline" else self.text()
        f = self._font()
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(text) + (22 if self.kind else 0)
        x = (self.width() - tw) / 2
        if self.kind:
            draw_icon(p, self.kind, QRectF(x, self.height() / 2 - 8, 16, 16), fg)
            x += 22
        p.setFont(f)
        p.setPen(fg)
        p.drawText(QRectF(x, 0, self.width() - x, self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.end()


class LinkLabel(QAbstractButton):
    """A small worded link (a section's "See all")."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setText(text)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self):
        return QSize(int(QFontMetricsF(look.label_font(10.5)).horizontalAdvance(self.text().upper())) + 4, 22)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(look.label_font(10.5))
        p.setPen(look.TEXT if self._hover else look.TEXT_2)
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), self.text().upper())
        p.end()


class SectionHeader(QWidget):
    """A section's label -- small caps, widely tracked, like the reference's
    POPULAR / ALBUMS -- and an optional link on the right."""

    def __init__(self, title, link=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 4)
        lay.addStretch(1)
        self.link = LinkLabel(link) if link else None
        if self.link:
            lay.addWidget(self.link)
        self.note = ""

    def set_note(self, text):
        self.note = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        f = look.label_font(11)
        p.setFont(f)
        p.setPen(look.TEXT_2)
        r = QRectF(0, 6, self.width(), self.height() - 8)
        p.drawText(r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.title.upper())
        if self.note:
            x = QFontMetricsF(f).horizontalAdvance(self.title.upper()) + 14
            p.setFont(look.font(12))
            p.setPen(look.TEXT_3)
            p.drawText(r.adjusted(x, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       self.note)
        p.end()


# ------------------------------------------------------------------ rows ----
def _elide(text, font, width):
    return QFontMetricsF(font).elidedText(text or "", Qt.TextElideMode.ElideRight, max(0.0, width))


class TrackRow(QWidget):
    """One song, the way the reference lists them: its number (bars while it
    plays, a play mark under the cursor), add-to-queue, the cover, title over
    artist, the album, the length, download and more."""
    play = Signal(object)
    queue = Signal(object)
    keep = Signal(object)
    remove = Signal(object)
    more = Signal(object, QPoint)
    open_artist = Signal(object)
    open_album = Signal(object)

    def __init__(self, track, number=None, show_art=True, show_album=True, in_queue=False, parent=None):
        super().__init__(parent)
        self.track = track
        self.number = number
        self.show_art = show_art and (bool(track.get("artwork")) or track.get("source") != "local")
        self.show_album = show_album
        self._hover = False
        self._current = False
        self._playing = False
        self._phase = 0.0
        self._pix = None
        self._hits = {}
        self.accent = look.TEXT
        self.setFixedHeight(look.ROW_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn = icon_button("close" if in_queue else "plus",
                                   "Remove from the queue" if in_queue else "Add to the queue", 30, 14, self)
        self.add_btn.clicked.connect(lambda: (self.remove if in_queue else self.queue).emit(self.track))
        self.keep_btn = icon_button("download", "Download this song", 30, 15, self)
        self.keep_btn.clicked.connect(lambda: self.keep.emit(self.track))
        if track.get("source") == "local":
            self.keep_btn.setEnabled(False)
            self.keep_btn.set_tip("On this PC")
        self.more_btn = icon_button("menu", "More", 30, 14, self)
        self.more_btn.clicked.connect(
            lambda: self.more.emit(self.track, self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height()))))
        if self.show_art and track.get("artwork"):
            art().want(thumb_url(track["artwork"], 120), self._set_pix)

    def _set_pix(self, pm):
        self._pix = look.rounded(pm, 38, 38, 4)
        self.update()

    def set_current(self, on, playing=False):
        if (on, playing) != (self._current, self._playing):
            self._current, self._playing = on, playing
            self.update()

    def set_kept(self, state, pct=None):
        b = self.keep_btn
        if state == "busy":
            b.set_tip("Downloading... %d%%" % pct if pct is not None else "Downloading...")
            b.setEnabled(False)
        elif state == "done":
            b.set_kind("check")
            b.set_tip("Downloaded")
            b.setEnabled(False)
        elif state == "error":
            b.set_kind("reload")
            b.set_tip("Download failed — try again")
            b.setEnabled(True)
        else:
            b.set_kind("download")
            b.setEnabled(self.track.get("source") != "local")
            b.set_tip("Download this song")
        b.update()

    def tick(self, phase):
        self._phase = phase
        if self._current and self._playing:
            self.update(QRectF(0, 0, 48, self.height()).toRect())

    def _geometry(self):
        w = self.width()
        x_num = 12
        x_add = x_num + 30
        x_art = x_add + 34
        x_text = x_art + (48 if self.show_art else 6)
        x_more = w - 12 - 30
        x_keep = x_more - 32
        x_dur = x_keep - 12 - 46
        album = self.show_album and w >= 680
        if album:
            x_album = x_text + (x_dur - x_text) * 0.56
            title_w = x_album - 18 - x_text
        else:
            x_album, title_w = x_dur, x_dur - 14 - x_text
        return x_num, x_add, x_art, x_text, title_w, x_album, x_dur, x_keep, x_more

    def resizeEvent(self, event):
        super().resizeEvent(event)
        _n, x_add, _a, _t, _tw, _al, _d, x_keep, x_more = self._geometry()
        y = (self.height() - 30) // 2
        self.add_btn.move(int(x_add), y)
        self.keep_btn.move(int(x_keep), y)
        self.more_btn.move(int(x_more), y)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseMoveEvent(self, e):
        over = any(r.contains(e.position()) for k, r in self._hits.items() if k in ("artist", "album"))
        self.setCursor(Qt.CursorShape.PointingHandCursor if over or e.position().x() < 44
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.MouseButton.LeftButton:
            if pos.x() < 42:
                self.play.emit(self.track)
                return
            if "artist" in self._hits and self._hits["artist"].contains(pos) and self.track.get("artist_browse"):
                self.open_artist.emit(self.track)
                return
            if "album" in self._hits and self._hits["album"].contains(pos) and self.track.get("album_browse"):
                self.open_album.emit(self.track)
                return
        if e.button() == Qt.MouseButton.RightButton:
            self.more.emit(self.track, e.globalPosition().toPoint())
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.play.emit(self.track)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        h = self.height()
        if self._current or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(look.CURRENT if self._current else look.HOVER)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 6, 6)
        x_num, _x_add, x_art, x_text, title_w, x_album, x_dur, _k, _m = self._geometry()
        num_rect = QRectF(x_num, 0, 26, h)
        if self._current:
            look.equalizer(p, QRectF(x_num + 6, h / 2 - 7, 13, 14), self.accent, self._phase, self._playing)
        elif self._hover:
            draw_icon(p, "play", QRectF(x_num + 4, h / 2 - 8, 16, 16), look.TEXT)
        elif self.number is not None:
            p.setFont(look.font(12.5, QFont.Weight.Medium))
            p.setPen(look.TEXT_3)
            p.drawText(num_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter), str(self.number))
        if self.show_art:
            ar = QRectF(x_art, (h - 38) / 2, 38, 38)
            if self._pix is not None:
                p.drawPixmap(ar.topLeft(), self._pix)
            else:
                look.placeholder(p, ar, look.DEFAULT, 4)
        t = self.track
        tf = look.font(13.5, QFont.Weight.DemiBold)
        af = look.font(12)
        p.setFont(tf)
        p.setPen(look.TEXT)
        p.drawText(QRectF(x_text, 7, title_w, 20), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   _elide(t.get("title"), tf, title_w))
        artist = t.get("artist") or ("On this PC" if t.get("source") == "local" else "")
        if t.get("kind") == "album":
            artist = " · ".join(x for x in (t.get("type") or "Album", artist) if x)
        a_text = _elide(artist, af, title_w)
        p.setFont(af)
        p.setPen(look.TEXT_2)
        a_rect = QRectF(x_text, 26, QFontMetricsF(af).horizontalAdvance(a_text), 18)
        p.drawText(QRectF(x_text, 26, title_w, 18), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   a_text)
        self._hits = {"artist": a_rect}
        if self.show_album and self.width() >= 680:
            album = t.get("album") or t.get("license") or ""
            al_w = x_dur - 16 - x_album
            al_text = _elide(album, af, al_w)
            p.setPen(look.TEXT_2 if t.get("album") else look.TEXT_3)
            p.drawText(QRectF(x_album, 0, al_w, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       al_text)
            self._hits["album"] = QRectF(x_album, h / 2 - 9, QFontMetricsF(af).horizontalAdvance(al_text), 18)
        if t.get("duration"):
            p.setFont(look.font(12, QFont.Weight.Medium))
            p.setPen(look.TEXT_3)
            p.drawText(QRectF(x_dur, 0, 46, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       format_eta(t["duration"]))
        p.end()


class TrackList(QWidget):
    """Songs, numbered, as rows; the first `limit` shown until "Show all"."""

    def __init__(self, wire, show_art=True, show_album=True, numbered=True, in_queue=False, limit=None,
                 parent=None):
        super().__init__(parent)
        self.wire = wire
        self.show_art, self.show_album, self.numbered, self.in_queue = show_art, show_album, numbered, in_queue
        self.limit = limit
        self.expanded = False
        self.rows = []
        self.accent = look.TEXT
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(0)
        self.more = LinkLabel("")
        self.more.clicked.connect(self.expand)
        self.more.hide()
        self.col.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)

    def set_tracks(self, tracks):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for i, t in enumerate(tracks):
            r = TrackRow(t, (i + 1) if self.numbered else None, self.show_art, self.show_album, self.in_queue)
            r.accent = self.accent
            self.wire(r)
            self.col.insertWidget(i, r)
            self.rows.append(r)
        self._apply_limit()

    def _apply_limit(self):
        n = len(self.rows)
        cut = n if (self.expanded or not self.limit) else min(self.limit, n)
        for i, r in enumerate(self.rows):
            r.setVisible(i < cut)
        if cut < n:
            self.more.setText("Show all %d" % n)
            self.more.show()
            self.more.adjustSize()
        else:
            self.more.hide()

    def expand(self):
        self.expanded = True
        self._apply_limit()

    def set_accent(self, colour):
        self.accent = colour
        for r in self.rows:
            r.accent = colour
            if r._current:
                r.update()

    def set_current(self, track_id, playing):
        any_on = False
        for r in self.rows:
            on = r.track["id"] == track_id
            r.set_current(on, playing)
            any_on = any_on or (on and playing)
        if any_on and not self._timer.isActive():
            self._timer.start()
        elif not any_on:
            self._timer.stop()

    def _tick(self):
        self._phase += 0.32
        for r in self.rows:
            if r._current:
                r.tick(self._phase)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)


# ----------------------------------------------------------------- tiles ----
class Tile(QWidget):
    """An album (square cover), an artist (round photo), a song (square
    cover, plays on click) or a mood (a coloured card with its name)."""
    clicked = Signal(object)
    play = Signal(object)

    TEXT_H = 46

    def __init__(self, item, mode="album", parent=None):
        super().__init__(parent)
        self.item = item
        self.mode = mode
        self._pix = None
        self._raw = None
        self._hover = 0.0
        self._over = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._step)
        if mode != "mood" and item.get("artwork"):
            art().want(thumb_url(item["artwork"], 320), self._got)

    def _got(self, pm):
        self._raw = pm
        self._pix = None
        self.update()

    def set_width(self, w):
        self.setFixedSize(int(w), int(w + (0 if self.mode == "mood" else self.TEXT_H)))
        self._pix = None

    def enterEvent(self, e):
        self._over = True
        self._fade.start()

    def leaveEvent(self, e):
        self._over = False
        self._fade.start()

    def _step(self):
        target = 1.0 if self._over else 0.0
        self._hover += (target - self._hover) * 0.3
        if abs(target - self._hover) < 0.02:
            self._hover = target
            self._fade.stop()
        self.update()

    def _play_rect(self):
        s = self.width()
        d = 42
        return QRectF(s - d - 10, s - d - 10, d, d)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        if self.mode in ("album", "track") and self._play_rect().contains(e.position()):
            self.play.emit(self.item)
        else:
            self.clicked.emit(self.item)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = self.width()
        cover = QRectF(0, 0, s, s)
        if self.mode == "mood":
            self._paint_mood(p, cover)
            p.end()
            return
        circle = self.mode == "artist"
        lift = 3.0 * self._hover
        cover.translate(0, -lift)
        if self._raw is not None and (self._pix is None or self._pix.width() != int(s * 2)):
            self._pix = look.rounded(self._raw, s, s, 6, circle=circle)
        # a soft shadow under the cover
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(4):
            p.setBrush(QColor(0, 0, 0, int(16 + 10 * self._hover)))
            sh = cover.adjusted(4 - i, 8 + i * 2, -4 + i, 4 + i * 2)
            if circle:
                p.drawEllipse(sh)
            else:
                p.drawRoundedRect(sh, 8, 8)
        if self._pix is not None:
            p.drawPixmap(cover.topLeft(), self._pix)
        else:
            look.placeholder(p, cover, look.DEFAULT, 6, circle=circle)
        if self._hover > 0:
            p.setBrush(QColor(0, 0, 0, int(46 * self._hover)))
            if circle:
                p.drawEllipse(cover)
            else:
                p.drawRoundedRect(cover, 6, 6)
            if self.mode in ("album", "track"):
                pr = self._play_rect().translated(0, -lift)
                p.setBrush(look.with_alpha(QColor(255, 255, 255), self._hover))
                p.drawEllipse(pr)
                draw_icon(p, "play", pr.adjusted(12, 12, -11, -12), look.with_alpha(QColor(10, 11, 16), self._hover))
        tf = look.font(13, QFont.Weight.DemiBold)
        sf = look.font(11.5)
        align = Qt.AlignmentFlag.AlignHCenter if circle else Qt.AlignmentFlag.AlignLeft
        p.setFont(tf)
        p.setPen(look.TEXT)
        p.drawText(QRectF(0, s + 8, s, 18), int(align | Qt.AlignmentFlag.AlignVCenter),
                   _elide(self.item.get("title"), tf, s))
        p.setFont(sf)
        p.setPen(look.TEXT_2)
        p.drawText(QRectF(0, s + 26, s, 16), int(align | Qt.AlignmentFlag.AlignVCenter),
                   _elide(self._subtitle(), sf, s))
        p.end()

    def _subtitle(self):
        it = self.item
        if self.mode == "artist":
            return it.get("subtitle") or "Artist"
        if self.mode == "track":
            return it.get("artist") or ""
        kind = it.get("type") or ("Live show" if it.get("source") == "archive" else "Album")
        bits = [kind if it.get("source") != "archive" else "", it.get("year") or "", it.get("artist") or ""]
        if it.get("source") == "archive":
            bits = [it.get("artist") or "Internet Archive", it.get("year") or ""]
        return " · ".join(b for b in bits if b)

    def _paint_mood(self, p, r):
        hue = self.item.get("hue", 0.6)
        pal = look.make_palette(hue, 0.7, h2=(hue + 0.08) % 1.0, s2=0.8)
        g = QLinearGradient(r.bottomLeft(), r.topRight())
        g.setColorAt(0, pal["deep"])
        g.setColorAt(1, look.mix(pal["base"], pal["glow"], 0.35))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 8, 8)
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.setClipPath(path)
        look.paint_grain(p, r, 0.8)
        p.restore()
        if self._hover:
            p.setBrush(QColor(255, 255, 255, int(22 * self._hover)))
            p.drawRoundedRect(r, 8, 8)
        px, lines = look.fit_display(self.item["title"].upper(), r.width() - 30,
                                     max_px=min(28, r.width() * 0.17), min_px=12, lines=2)
        f = look.display_font(px)
        fm = QFontMetricsF(f)
        p.setFont(f)
        p.setPen(look.TEXT)
        y = r.bottom() - 14 - fm.descent()
        for line in reversed(lines):
            p.drawText(QPointF(r.left() + 15, y), line)
            y -= px * 0.98


class TileGrid(QWidget):
    """Tiles in as many columns as fit, `rows` rows of them until expanded."""
    relaid = Signal()

    def __init__(self, wire, mode="album", rows=2, min_w=look.TILE_MIN, max_w=look.TILE_MAX, gap=22, parent=None):
        super().__init__(parent)
        self.wire = wire
        self.mode = mode
        self.max_rows = rows
        self.min_w, self.max_w, self.gap = min_w, max_w, gap
        self.tiles = []
        self.expanded = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_items(self, items):
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []
        for it in items:
            t = Tile(it, self.mode, self)
            self.wire(t)
            self.tiles.append(t)
        self._relayout()

    def expand(self):
        self.expanded = True
        self._relayout()

    def columns(self, width=None):
        w = width or self.width()
        return max(1, int((w + self.gap) // (self.min_w + self.gap)))

    def hidden_count(self):
        if self.expanded:
            return 0
        return max(0, len(self.tiles) - self.columns() * self.max_rows)

    def _relayout(self):
        w = max(1, self.width())
        cols = self.columns(w)
        tw = min(self.max_w, (w - self.gap * (cols - 1)) / cols)
        shown = self.tiles if self.expanded else self.tiles[:cols * self.max_rows]
        th = 0
        for i, t in enumerate(self.tiles):
            if t in shown:
                t.set_width(tw)
                r, c = divmod(i, cols)
                t.move(int(c * (tw + self.gap)), int(r * (t.height() + self.gap)))
                t.show()
                th = max(th, t.y() + t.height())
            else:
                t.hide()
        self.setFixedHeight(int(th + 4) if shown else 0)
        self.relaid.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._relayout()


# ----------------------------------------------------------------- hero -----
class Hero(QWidget):
    """A page's header, the reference's way: a small overline, the name as
    large as it fits, a line under it, the actions, and three of its songs
    in columns along the bottom -- with the picture on the right melting
    into the page."""
    track_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.overline = ""
        self.title = ""
        self.subline = ""
        self.trio = []
        self.trio_label = ""
        self._raw = None
        self._wide = False
        self._blend = None
        self._blend_key = None
        self._pal = look.DEFAULT
        self._lines = []
        self._sub_lines = []
        self._trio_min_y = 0
        self._px = 64
        self._trio_hits = []
        self._hover_trio = -1
        self._fade = 1.0
        self._old = None
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)
        self.preferred_h = 0
        self.setMouseTracking(True)
        self.actions = QWidget(self)
        self.action_row = QHBoxLayout(self.actions)
        self.action_row.setContentsMargins(0, 0, 0, 0)
        self.action_row.setSpacing(10)
        self.setMinimumHeight(340)

    def set_text(self, overline, title, subline=""):
        self.overline, self.title, self.subline = overline, title, subline
        self._layout()
        self.update()

    def set_trio(self, tracks, label=""):
        self.trio = list(tracks)[:3]
        self.trio_label = label
        self._layout()
        self.update()

    def add_action(self, widget):
        self.action_row.addWidget(widget)
        widget.show()
        self.actions.adjustSize()
        self._layout()
        return widget

    def clear_actions(self):
        while self.action_row.count():
            w = self.action_row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def set_art(self, pix, wide=False):
        if pix is self._raw:
            return
        self._old = self._blend
        self._raw, self._wide = pix, wide
        self._blend_key = None
        self._fade = 0.0 if self._old is not None else 1.0
        if self._fade < 1.0:
            self._anim.start()
        self.update()

    def set_palette(self, pal):
        self._pal = pal
        self._blend_key = None
        self.update()

    def _step(self):
        self._fade = min(1.0, self._fade + 0.06)
        if self._fade >= 1.0:
            self._old = None
            self._anim.stop()
        self.update()

    def _text_width(self):
        return max(260.0, min(self.width() * 0.58, 780.0) - look.GUTTER)

    def _layout(self):
        w = self._text_width()
        self._px, self._lines = look.fit_display((self.title or "").upper(), w,
                                                 max_px=min(96, max(44, self.width() * 0.075)), min_px=34)
        top = 46
        y = top + (22 if self.overline else 0)
        line_h = self._px * 0.98
        y += line_h * len(self._lines)
        self._sub_lines = self._wrap(self.subline, look.font(14, QFont.Weight.Medium), w) if self.subline else []
        y += 10 + 21 * len(self._sub_lines)
        self._actions_y = y + 16
        self.actions.adjustSize()
        self.actions.move(look.GUTTER, int(self._actions_y))
        bottom = self._actions_y + self.actions.height()
        # the three songs sit along the foot of the header, never closer than
        # this to the actions (their label goes 22 px above them)
        self._trio_min_y = bottom + 52
        need = (self._trio_min_y + 64) if self.trio else (bottom + 40)
        self.setMinimumHeight(int(max(340, need, self.preferred_h)))

    @staticmethod
    def _wrap(text, f, width, lines=2):
        fm = QFontMetricsF(f)
        out, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if fm.horizontalAdvance(trial) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
        if len(out) > lines:
            out = out[:lines]
            out[-1] = fm.elidedText(out[-1] + " …", Qt.TextElideMode.ElideRight, width)
        return out

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _ensure_blend(self):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), id(self._raw), self._wide, self._pal["base"].rgb(), dpr)
        if key != self._blend_key:
            self._blend = look.blend_art(self._raw, self.width(), self.height(), self._pal, self._wide, dpr) \
                if self._raw is not None else None
            self._blend_key = key

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._ensure_blend()
        if self._old is not None and self._fade < 1.0:
            p.setOpacity(1.0 - self._fade)
            p.drawPixmap(0, 0, self._old)
        if self._blend is not None:
            p.setOpacity(look.ease(self._fade))
            p.drawPixmap(0, 0, self._blend)
        p.setOpacity(1.0)
        x = look.GUTTER
        y = 46
        if self.overline:
            p.setFont(look.label_font(11))
            p.setPen(look.TEXT_2)
            p.drawText(QRectF(x, y, self.width(), 16), int(Qt.AlignmentFlag.AlignVCenter), self.overline.upper())
            y += 22
        f = look.display_font(self._px)
        fm = QFontMetricsF(f)
        p.setFont(f)
        line_h = self._px * 0.98
        for line in self._lines:
            base = y + (line_h + fm.ascent() - fm.descent()) / 2
            p.setPen(QColor(0, 0, 0, 60))
            p.drawText(QPointF(x, base + 2), line)
            p.setPen(look.TEXT)
            p.drawText(QPointF(x, base), line)
            y += line_h
        y += 10
        if self._sub_lines:
            p.setFont(look.font(14, QFont.Weight.Medium))
            p.setPen(look.TEXT_2)
            for line in self._sub_lines:
                p.drawText(QRectF(x, y, self._text_width(), 20), int(Qt.AlignmentFlag.AlignVCenter), line)
                y += 21
        self._paint_trio(p)
        p.end()

    def _paint_trio(self, p):
        self._trio_hits = []
        if not self.trio:
            return
        x0 = look.GUTTER
        y = max(self._trio_min_y, self.height() - 88)
        if self.trio_label:
            p.setFont(look.label_font(9.5))
            p.setPen(look.TEXT_3)
            p.drawText(QRectF(x0, y - 22, 300, 14), int(Qt.AlignmentFlag.AlignVCenter), self.trio_label.upper())
        col_w = min(190.0, (self.width() * 0.62 - x0) / 3 - 18)
        tf = look.font(12, QFont.Weight.Bold, spacing=0.7)
        sf = look.font(11.5)
        for i, t in enumerate(self.trio):
            cx = x0 + i * (col_w + 22)
            hot = i == self._hover_trio
            p.setFont(tf)
            p.setPen(look.TEXT)
            title = _elide((t.get("title") or "").upper(), tf, col_w)
            p.drawText(QRectF(cx, y, col_w, 18), int(Qt.AlignmentFlag.AlignVCenter), title)
            if hot:
                p.fillRect(QRectF(cx, y + 17, QFontMetricsF(tf).horizontalAdvance(title), 1.2), look.TEXT)
            p.setFont(sf)
            p.setPen(look.TEXT_2)
            p.drawText(QRectF(cx, y + 20, col_w, 16), int(Qt.AlignmentFlag.AlignVCenter),
                       _elide(t.get("album") or t.get("artist") or "", sf, col_w))
            p.setPen(look.TEXT_3)
            p.drawText(QRectF(cx, y + 37, col_w, 16), int(Qt.AlignmentFlag.AlignVCenter),
                       t.get("year") or (format_eta(t["duration"]) if t.get("duration") else ""))
            self._trio_hits.append((QRectF(cx, y - 2, col_w, 58), t))

    def mouseMoveEvent(self, e):
        hot = next((i for i, (r, _t) in enumerate(self._trio_hits) if r.contains(e.position())), -1)
        if hot != self._hover_trio:
            self._hover_trio = hot
            self.setCursor(Qt.CursorShape.PointingHandCursor if hot >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        if self._hover_trio != -1:
            self._hover_trio = -1
            self.update()

    def mousePressEvent(self, e):
        if any(r.contains(e.position()) for r, _t in self._trio_hits):
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        for r, t in self._trio_hits:
            if r.contains(e.position()):
                self.track_clicked.emit(t)
                return


# ----------------------------------------------------------------- rail -----
class NavItem(QAbstractButton):
    def __init__(self, key, text, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(32)
        self.count = ""
        self.accent = QColor("#7dd3fc")
        self._hover = False

    def set_count(self, n):
        self.count = str(n) if n else ""
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 13 if on else 8))
            p.drawRoundedRect(r.adjusted(10, 1, -10, -1), 7, 7)
        if on:
            p.setBrush(self.accent)
            p.drawRoundedRect(QRectF(0, r.height() / 2 - 8, 3, 16), 1.5, 1.5)
        f = look.font(13.5, QFont.Weight.DemiBold if on else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(look.RAIL_TEXT if (on or self._hover) else look.RAIL_MUTED)
        p.drawText(r.adjusted(22, 0, -40, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   self.text())
        if self.count:
            p.setFont(look.font(11, QFont.Weight.DemiBold))
            p.setPen(look.RAIL_LABEL if not on else look.RAIL_MUTED)
            p.drawText(r.adjusted(0, 0, -22, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       self.count)
        p.end()


class _RailLabel(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setFixedHeight(34)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(look.label_font(10))
        p.setPen(look.RAIL_LABEL)
        p.drawText(QRectF(22, 12, self.width() - 22, 20), int(Qt.AlignmentFlag.AlignVCenter), self.text.upper())
        p.end()


class _Mark(QWidget):
    """The Music tab's mark: a note on the logo's sky-to-violet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, 30, 30)
        g.setColorAt(0, QColor("#7dd3fc"))
        g.setColorAt(0.5, QColor("#38bdf8"))
        g.setColorAt(1, QColor("#818cf8"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, 30, 30), 8, 8)
        draw_icon(p, "music", QRectF(6, 6, 18, 18), QColor(8, 14, 32))
        p.end()


class Rail(QWidget):
    """The quiet dark column on the left: search, then the library, then
    moods to start from."""
    navigate = Signal(str)
    searched = Signal(str)
    mood = Signal(str)

    MOODS = (("chill", "Chill", 0.52), ("focus", "Focus", 0.60), ("workout", "Workout", 0.02),
             ("lofi", "Lo-fi beats", 0.78), ("party", "Party", 0.90), ("classical", "Classical", 0.10),
             ("jazz", "Jazz", 0.08), ("indie", "Indie", 0.40))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(look.RAIL_W)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(20, 18, 16, 12)
        head.setSpacing(10)
        head.addWidget(_Mark())
        name = QLabel("Music")
        name.setFont(look.font(17, QFont.Weight.Bold))
        name.setStyleSheet("color: #ffffff; background: transparent;")
        head.addWidget(name)
        head.addStretch(1)
        outer.addLayout(head)

        self.search = QLineEdit()
        self.search.setObjectName("railSearch")
        self.search.setPlaceholderText("Songs, artists, albums")
        self.search.setFixedHeight(38)
        self.search.setTextMargins(26, 0, 0, 0)
        self.search.setFont(look.font(13))
        self.search.setStyleSheet(
            "QLineEdit#railSearch { background: %s; border: 1px solid rgba(255,255,255,16); border-radius: 10px;"
            " color: #ffffff; padding: 0 10px; selection-background-color: rgba(125,211,252,110); }"
            "QLineEdit#railSearch:focus { border: 1px solid rgba(255,255,255,70); }" % look.RAIL_FIELD.name())
        self.search.returnPressed.connect(lambda: self.searched.emit(self.search.text()))
        self._glass = _SearchGlyph(self.search)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(14, 0, 14, 6)
        wrap.addWidget(self.search)
        outer.addLayout(wrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                             " { background: transparent; }")
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 12)
        col.setSpacing(0)
        self.items = {}
        groups = (("Discover", (("home", "Home"), ("search", "Search results"))),
                  ("Your music", (("songs", "Songs"), ("artists", "Artists"), ("albums", "Albums"),
                                  ("local", "On this PC"), ("queue", "Queue"))))
        for label, entries in groups:
            col.addWidget(_RailLabel(label))
            for key, text in entries:
                item = NavItem(key, text)
                item.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
                col.addWidget(item)
                self.items[key] = item
        col.addWidget(_RailLabel("Moods"))
        for key, text, _hue in self.MOODS:
            item = NavItem("mood:" + key, text)
            item.setCheckable(False)
            item.clicked.connect(lambda _c=False, t=text: self.mood.emit(t))
            col.addWidget(item)
        col.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def select(self, key):
        for k, item in self.items.items():
            item.setChecked(k == key)

    def set_accent(self, colour):
        for item in self.items.values():
            item.accent = colour
            if item.isChecked():
                item.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), look.RAIL_BG)
        p.fillRect(QRectF(self.width() - 1, 0, 1, self.height()), QColor(255, 255, 255, 12))
        p.end()


class _SearchGlyph(QWidget):
    def __init__(self, field):
        super().__init__(field)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(30, 38)
        self.move(6, 0)

    def paintEvent(self, event):
        p = QPainter(self)
        draw_icon(p, "search", QRectF(7, 11, 16, 16), look.RAIL_MUTED)
        p.end()


# ------------------------------------------------------------- the player ---
class ThinSlider(QSlider):
    """A hairline that thickens under the cursor, with a knob -- the song's
    progress along the top of the player (`top`), or the volume."""

    def __init__(self, top=False, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.top = top
        self._hover = False
        self.accent = look.TEXT
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(14 if top else 18)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _value_at(self, x):
        if self.maximum() <= self.minimum():
            return self.minimum()
        f = max(0.0, min(1.0, x / max(1.0, self.width())))
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.maximum() <= self.minimum():
            return
        self.setSliderDown(True)
        self.setValue(self._value_at(e.position().x()))
        e.accept()

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self.setValue(self._value_at(e.position().x()))
        self.update()

    def mouseReleaseEvent(self, e):
        if self.isSliderDown():
            self.setValue(self._value_at(e.position().x()))
            self.setSliderDown(False)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        thick = 5.0 if (self._hover or self.isSliderDown()) else 3.0
        y = (thick / 2 + 1) if self.top else self.height() / 2
        span = self.maximum() - self.minimum()
        f = (self.value() - self.minimum()) / span if span > 0 else 0.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(QRectF(0, y - thick / 2, w, thick), thick / 2, thick / 2)
        p.setBrush(self.accent)
        p.drawRoundedRect(QRectF(0, y - thick / 2, w * f, thick), thick / 2, thick / 2)
        if self._hover or self.isSliderDown():
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(max(6.0, min(w - 6.0, w * f)), y), 6, 6)
        p.end()


class PlayerBar(QWidget):
    """The player along the bottom, the reference's way: the song on the
    left, the controls in the middle, time and volume on the right, its
    progress a line along its top -- all over the playing cover's colours."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(look.BAR_H)
        # never what holds the window wide: it folds its extras away instead (resizeEvent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._pal = look.DEFAULT
        self._frost = None
        self.seek = ThinSlider(top=True, parent=self)
        self.seek.setRange(0, 0)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 18, 10)
        lay.setSpacing(12)
        left = self.left = QWidget()
        left.setFixedWidth(look.RAIL_W + 70)
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(12)
        self.art = _CoverButton(50)
        ll.addWidget(self.art)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel("Nothing playing")
        self.title.setFont(look.font(13.5, QFont.Weight.DemiBold))
        self.title.setStyleSheet("color: #ffffff; background: transparent;")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.artist = QLabel("Search for a song to start")
        self.artist.setFont(look.font(12))
        self.artist.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        self.artist.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.credit = QLabel("")
        self.credit.setFont(look.font(10.5))
        self.credit.setStyleSheet("color: rgba(255,255,255,130); background: transparent;")
        self.credit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.credit.hide()
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.artist)
        col.addWidget(self.credit)
        col.addStretch(1)
        ll.addLayout(col, 1)
        self.save_btn = icon_button("star", "Save to Your music", 32, 16)
        ll.addWidget(self.save_btn)
        lay.addWidget(left)

        lay.addStretch(1)
        self.shuffle_btn = icon_button("shuffle", "Shuffle", 34, 16)
        self.prev_btn = icon_button("prev", "Previous", 36, 17)
        self.play_btn = RingButton("play", "Play", size=46, icon_size=18, ring=0.75)
        self.next_btn = icon_button("next", "Next", 36, 17)
        self.repeat_btn = icon_button("repeat", "Repeat: off", 34, 16)
        for b in (self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn):
            lay.addWidget(b)
        lay.addStretch(1)

        right = QHBoxLayout()
        right.setSpacing(4)
        self.pos_label = QLabel("0:00")
        self.len_label = QLabel("0:00")
        for lab in (self.pos_label, self.len_label):
            lab.setFont(look.font(12, QFont.Weight.Medium))
            lab.setStyleSheet("color: rgba(255,255,255,170); background: transparent;")
        slash = self.slash = QLabel("/")
        slash.setFont(look.font(12))
        slash.setStyleSheet("color: rgba(255,255,255,90); background: transparent;")
        right.addWidget(self.pos_label)
        right.addWidget(slash)
        right.addWidget(self.len_label)
        right.addSpacing(12)
        self.queue_btn = icon_button("list", "Queue", 34, 16)
        self.lyrics_btn = icon_button("mic", "Lyrics (karaoke)", 34, 16)
        self.keep_btn = icon_button("download", "Download this song", 34, 16)
        self.vol_icon = icon_button("speaker", "Mute", 34, 16)
        self.volume = ThinSlider()
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(96)
        self.volume.setToolTip("Volume")
        self.full_btn = icon_button("expand", "Full screen", 34, 15)
        for w in (self.queue_btn, self.lyrics_btn, self.keep_btn, self.vol_icon, self.volume, self.full_btn):
            right.addWidget(w)
        lay.addLayout(right)

    def set_palette(self, pal, frost=None):
        self._pal = pal
        self._frost = frost
        self.seek.accent = look.TEXT
        self.update()

    # (narrower than, what folds away) -- the least needed first
    _FOLD = ((1200, "volume"), (1090, "times"), (1000, "queue_btn"), (1000, "full_btn"), (930, "save_btn"),
             (880, "lyrics_btn"), (820, "shuffle_btn"), (820, "repeat_btn"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.seek.setGeometry(0, 0, self.width(), 14)
        self.seek.raise_()
        w = self.width()
        for limit, name in self._FOLD:
            show = w >= limit
            parts = (self.pos_label, self.slash, self.len_label) if name == "times" else (getattr(self, name),)
            for part in parts:
                part.setVisible(show)
        self.left.setFixedWidth(look.RAIL_W + 70 if w >= 1000 else max(150, int(w * 0.27)))

    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, look.mix(self._pal["deep"], QColor(0, 0, 0), 0.35))
        if self._frost is not None:
            p.setOpacity(0.55)
            p.drawPixmap(r, self._frost, QRectF(self._frost.rect()))
            p.setOpacity(1.0)
        g = QLinearGradient(0, 0, r.width(), 0)
        g.setColorAt(0, look.with_alpha(self._pal["deep"], 0.80))
        g.setColorAt(0.5, look.with_alpha(self._pal["deep"], 0.55))
        g.setColorAt(1, look.with_alpha(self._pal["deep"], 0.80))
        p.fillRect(r, g)
        look.paint_grain(p, r, 0.6)
        p.fillRect(QRectF(0, 0, r.width(), 1), QColor(255, 255, 255, 18))
        p.end()


class _CoverButton(QAbstractButton):
    """The playing song's cover in the player; a click opens the song's page."""

    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pix = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Now playing")

    def set_pixmap(self, pm):
        self._pix = look.rounded(pm, self.width(), self.height(), 5) if pm is not None else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._pix is not None:
            p.drawPixmap(0, 0, self._pix)
        else:
            look.placeholder(p, r, look.DEFAULT, 5)
        p.end()


def frost_strip(img):
    """The cover, smeared wide and soft, for the player's glass."""
    if img is None or img.isNull():
        return None
    try:
        from PIL import Image, ImageFilter
        small = img.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
        pil = Image.frombuffer("RGB", (64, 64), bytes(small.constBits()), "raw", "RGB", small.bytesPerLine(), 1)
        pil = pil.resize((240, 24)).filter(ImageFilter.GaussianBlur(6))
        data = pil.tobytes("raw", "RGB")
        return QPixmap.fromImage(QImage(data, 240, 24, 240 * 3, QImage.Format.Format_RGB888).copy())
    except Exception:   # noqa: BLE001
        return None


class SuggestPanel(QWidget):
    """What YouTube Music suggests as a search is typed: searches to run,
    then songs, artists and albums to open straight away. Arrow keys move
    through it, Enter picks; it never takes the typing's focus."""
    chosen = Signal(object)

    QUERY_H, ITEM_H = 36, 54

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.rows = []          # [(kind, item, rect)]
        self.sel = -1
        self.typed = ""
        self._pix = {}
        self.hide()

    def set_results(self, typed, queries, items, width):
        self.typed = typed
        self.rows = []
        y = 8.0
        for q in queries:
            self.rows.append(("query", {"kind": "query", "title": q}, QRectF(6, y, width - 12, self.QUERY_H)))
            y += self.QUERY_H
        if items and queries:
            y += 14                 # room for the "Top results" label
        self._items_y = y - 14
        for it in items:
            self.rows.append(("item", it, QRectF(6, y, width - 12, self.ITEM_H)))
            y += self.ITEM_H
            if it.get("artwork") and it["id"] not in self._pix:
                art().want(thumb_url(it["artwork"], 120),
                           lambda pm, i=it: (self._pix.__setitem__(i["id"], look.rounded(
                               pm, 38, 38, 4, circle=i.get("kind") == "artist")), self.update()))
        self.sel = -1
        if not self.rows:
            self.hide()
            return
        self.resize(int(width), int(y + 8))
        self.show()
        self.raise_()
        self.update()

    def move_selection(self, delta):
        if not self.rows:
            return
        self.sel = (self.sel + delta) % len(self.rows) if self.sel >= 0 or delta > 0 else len(self.rows) - 1
        self.update()

    def activate(self):
        """Picks the highlighted row. False when none is."""
        if not self.isVisible() or not (0 <= self.sel < len(self.rows)):
            return False
        self.chosen.emit(self.rows[self.sel][1])
        return True

    def items(self):
        return [r[1] for r in self.rows if r[0] == "item"]

    def mouseMoveEvent(self, e):
        hot = next((i for i, r in enumerate(self.rows) if r[2].contains(e.position())), -1)
        if hot != self.sel:
            self.sel = hot
            self.update()

    def mousePressEvent(self, e):
        hot = next((i for i, r in enumerate(self.rows) if r[2].contains(e.position())), -1)
        if hot >= 0:
            self.chosen.emit(self.rows[hot][1])
        e.accept()

    def leaveEvent(self, e):
        self.sel = -1
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(QColor(18, 19, 25, 250))
        p.drawRoundedRect(r, 12, 12)
        typed = self.typed.lower()
        for i, (kind, it, rect) in enumerate(self.rows):
            if i == self.sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 20))
                p.drawRoundedRect(rect, 8, 8)
            if kind == "query":
                draw_icon(p, "search", QRectF(rect.left() + 12, rect.center().y() - 8, 16, 16), look.TEXT_3)
                text = it["title"]
                x = rect.left() + 40
                # what was typed in the normal weight, the rest of the suggestion bold
                head = text[:len(typed)] if text.lower().startswith(typed) else ""
                for part, weight in ((head, QFont.Weight.Normal), (text[len(head):], QFont.Weight.Bold)):
                    if not part:
                        continue
                    f = look.font(13.5, weight)
                    p.setFont(f)
                    p.setPen(look.TEXT if weight == QFont.Weight.Bold else look.TEXT_2)
                    p.drawText(QRectF(x, rect.top(), rect.right() - x - 10, rect.height()),
                               int(Qt.AlignmentFlag.AlignVCenter), part)
                    x += QFontMetricsF(f).horizontalAdvance(part)
                continue
            ar = QRectF(rect.left() + 10, rect.center().y() - 19, 38, 38)
            pm = self._pix.get(it["id"])
            if pm is not None:
                p.drawPixmap(ar.topLeft(), pm)
            else:
                look.placeholder(p, ar, look.DEFAULT, 4, circle=it.get("kind") == "artist")
            tx = ar.right() + 12
            tf = look.font(13.5, QFont.Weight.DemiBold)
            sf = look.font(12)
            p.setFont(tf)
            p.setPen(look.TEXT)
            p.drawText(QRectF(tx, rect.top() + 8, rect.right() - tx - 10, 20), int(Qt.AlignmentFlag.AlignVCenter),
                       _elide(it.get("title"), tf, rect.right() - tx - 10))
            sub = {"artist": "Artist", "album": " · ".join(x for x in (it.get("type") or "Album", it.get("artist"))
                                                           if x)}.get(it.get("kind"),
                                                                      " · ".join(x for x in ("Song", it.get("artist"))
                                                                                 if x))
            p.setFont(sf)
            p.setPen(look.TEXT_2)
            p.drawText(QRectF(tx, rect.top() + 28, rect.right() - tx - 10, 18), int(Qt.AlignmentFlag.AlignVCenter),
                       _elide(sub, sf, rect.right() - tx - 10))
        if any(k == "item" for k, _i, _r in self.rows) and any(k == "query" for k, _i, _r in self.rows):
            p.setFont(look.label_font(9.5))
            p.setPen(look.TEXT_3)
            p.drawText(QRectF(18, self._items_y, 300, 16), int(Qt.AlignmentFlag.AlignVCenter), "TOP RESULTS")
        p.end()


class Toast(QWidget):
    """A short message, floating above the player for a few seconds -- with
    a button when there's something to do about it ("Try again")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._action = None
        self.button = Pill("", style="outline", parent=self)
        self.button.clicked.connect(self._act)
        self.button.hide()
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        self.hide()

    def text(self):
        return self._text

    def setText(self, text):   # noqa: N802 -- reads like the QLabel it replaces
        self.show_message(text)

    def show_message(self, text, action=None, seconds=4.2):
        """`action`: (label, callback) for a button on the message."""
        self._text = text or ""
        self._action = action[1] if action else None
        if not self._text:
            self.hide()
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not action)
        f = look.font(13, QFont.Weight.Medium)
        extra = 0
        if action:
            self.button.setText(action[0])
            self.button.adjustSize()
            self.button.resize(self.button.sizeHint())
            extra = self.button.width() + 14
            self.button.show()
        else:
            self.button.hide()
        h = 46
        w = min(int(QFontMetricsF(f).horizontalAdvance(self._text)) + 44 + extra,
                max(240, self.parent().width() - 80))
        self.resize(w, h)
        self.move((self.parent().width() - w) // 2, self.parent().height() - h - 18)
        if action:
            self.button.move(w - self.button.width() - 8, (h - self.button.height()) // 2)
        self.show()
        self.raise_()
        self.update()
        self._hide.start(int(seconds * 1000))

    def _act(self):
        cb, self._action = self._action, None
        self.hide()
        if cb:
            cb()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(QColor(14, 15, 20, 240))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        f = look.font(13, QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(look.TEXT)
        right = (self.button.width() + 14) if self.button.isVisible() else 0
        text_r = r.adjusted(18, 0, -18 - right, 0)
        p.drawText(text_r, int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if right
                                                                 else Qt.AlignmentFlag.AlignHCenter)),
                   _elide(self._text, f, text_r.width()))
        p.end()


def ellipsis_count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def total_length(tracks):
    s = sum(int(t.get("duration") or 0) for t in tracks)
    if s >= 3600:
        return "%d h %d min" % (s // 3600, (s % 3600) // 60)
    return "%d min" % max(1, math.ceil(s / 60)) if s else ""
