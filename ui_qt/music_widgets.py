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
    QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
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
            elif "i.ytimg.com" in url and img.width() * 3 == img.height() * 4:
                # a video's 4:3 picture is 16:9 with black bars: the bars go,
                # and the frame is cut square from its middle
                h = img.width() * 9 // 16
                frame = img.copy(0, (img.height() - h) // 2, img.width(), h)
                img = frame.copy((frame.width() - h) // 2, 0, h, h)
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
        self.accent = QColor("#f4a47a")
        self.height_px = None        # a taller pill where it's the main control of a row
        self.ui_scale = 1.0          # its words and icon, smaller or larger (a card sized to its window)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _font(self):
        k = self.ui_scale
        if self.style == "outline":
            return look.label_font(10.5 * k)
        if self.style in ("tab", "tab_on"):
            return look.font(13 * k, QFont.Weight.DemiBold)
        return look.font(12.5 * k, QFont.Weight.Bold, spacing=0.6)

    def sizeHint(self):
        fm = QFontMetricsF(self._font())
        text = self.text().upper() if self.style == "outline" else self.text()
        w = fm.horizontalAdvance(text) + (34 if self.style != "outline" else 24) + (22 if self.kind else 0)
        h = self.height_px or (30 if self.style == "outline" else 38)
        if self.height_px:
            w += h * 0.5
        return QSize(int(w), int(h))

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
        elif self.style in ("tab", "tab_on"):
            on = self.style == "tab_on"
            p.setPen(QPen(QColor(255, 255, 255, 40), 1) if on else Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 34 if on else (14 if self._hover else 0)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = look.TEXT if on else look.with_alpha(look.TEXT, 0.68 if not self._hover else 0.9)
        elif self.style == "glow":
            # the accent, lit: a warm fill with a soft halo (Lyrics, when on)
            a = QColor(self.accent)
            halo = QRadialGradient(r.center(), r.width() * 0.62)
            halo.setColorAt(0, look.with_alpha(a, 0.30))
            halo.setColorAt(1, look.with_alpha(a, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, look.with_alpha(a.lighter(118), 0.42 + (0.08 if self._hover else 0)))
            g.setColorAt(1, look.with_alpha(a, 0.22 + (0.08 if self._hover else 0)))
            p.setBrush(g)
            p.setPen(QPen(look.with_alpha(a.lighter(135), 0.85), 1.2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), (r.height() - 2) / 2, (r.height() - 2) / 2)
            fg = look.TEXT
        elif self.style == "ghost":
            # a quiet frame on the glass (Lyrics, off; the player's worded buttons)
            p.setPen(QPen(look.with_alpha(look.TEXT, 0.22 if self._hover else 0.14), 1))
            p.setBrush(look.with_alpha(look.TEXT, 0.10 if self._hover else 0.045))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            fg = look.TEXT
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
        isz = (20 if (self.height_px or 0) >= 46 * self.ui_scale else 16) * self.ui_scale
        gap = 8 * self.ui_scale
        tw = fm.horizontalAdvance(text) + ((isz + gap) if self.kind else 0)
        x = (self.width() - tw) / 2
        if self.kind:
            draw_icon(p, self.kind, QRectF(x, self.height() / 2 - isz / 2, isz, isz), fg)
            x += isz + gap
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
            album = t.get("reason") or t.get("album") or t.get("license") or ""
            al_w = x_dur - 16 - x_album
            al_text = _elide(album, af, al_w)
            p.setPen(look.TEXT_3 if (t.get("reason") or not t.get("album")) else look.TEXT_2)
            p.drawText(QRectF(x_album, 0, al_w, h), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       al_text)
            if not t.get("reason"):
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
        if getattr(self, "picked", False):
            # chosen (the taste setup's artists): a white ring and a check
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255), 3))
            if circle:
                p.drawEllipse(cover.adjusted(1.5, 1.5, -1.5, -1.5))
            else:
                p.drawRoundedRect(cover.adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
            badge = QRectF(cover.right() - 30, cover.top() + 2, 28, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(badge)
            draw_icon(p, "check", badge.adjusted(6, 6, -6, -6), QColor(10, 11, 16))
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
        if it.get("kind") == "mix":
            return it.get("subtitle") or ""
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
    """A place in the rail: an icon and a name -- or a small cover, a name
    and a second line (a mix, a playlist) -- on a soft glass highlight while
    it's open, with the accent's bar at its edge."""

    def __init__(self, key, text, parent=None, sub="", art_url=None, icon=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.sub = sub
        self.icon = icon
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.has_art = bool(art_url) or (bool(sub) and not icon)
        self.setFixedHeight(50 if (self.has_art or sub) else 36)
        self.count = ""
        self.accent = QColor("#7dd3fc")
        self._hover = 0.0
        self._over = False
        self._pix = None
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._step)
        if art_url:
            art().want(thumb_url(art_url, 120), self._got)

    def _got(self, pm):
        self._pix = look.rounded(pm, 36, 36, 7)
        self.update()

    def set_count(self, n):
        self.count = str(n) if n else ""
        self.update()

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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        box = r.adjusted(0, 1, 0, -1)
        if on or self._hover:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1) if on else Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(24 if on else 12 * self._hover)))
            p.drawRoundedRect(box, 11, 11)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)
            p.drawRoundedRect(QRectF(5, r.height() / 2 - 8, 3, 16), 1.5, 1.5)
        x = 14.0
        colour = look.RAIL_TEXT if (on or self._over) else look.with_alpha(look.RAIL_TEXT, 0.80)
        if self.has_art:
            ar = QRectF(x, (r.height() - 36) / 2, 36, 36)
            if self._pix is not None:
                p.drawPixmap(ar.topLeft(), self._pix)
            else:
                look.placeholder(p, ar, look.DEFAULT, 7)
            x += 46
        elif self.icon and self.sub:
            # an action among the playlists: its icon on a tile of the accent
            ar = QRectF(x, (r.height() - 36) / 2, 36, 36)
            g = QLinearGradient(ar.topLeft(), ar.bottomRight())
            g.setColorAt(0, look.with_alpha(self.accent, 0.55 + 0.15 * self._hover))
            g.setColorAt(1, look.with_alpha(self.accent.darker(150), 0.45 + 0.15 * self._hover))
            p.setPen(QPen(look.with_alpha(self.accent.lighter(140), 0.6), 1))
            p.setBrush(g)
            p.drawRoundedRect(ar.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
            draw_icon(p, self.icon, ar.adjusted(8, 8, -8, -8), look.TEXT)
            x += 46
        elif self.icon:
            draw_icon(p, self.icon, QRectF(x, (r.height() - 20) / 2, 20, 20), colour)
            x += 32
        f = look.font(13.5, QFont.Weight.DemiBold if on else QFont.Weight.Medium)
        right = 34 if self.count else 10
        if self.sub:
            p.setFont(f)
            p.setPen(colour)
            p.drawText(QRectF(x, 6, r.width() - x - right, 20), int(Qt.AlignmentFlag.AlignVCenter),
                       _elide(self.text(), f, r.width() - x - right))
            sf = look.font(11.5)
            p.setFont(sf)
            p.setPen(look.with_alpha(look.RAIL_TEXT, 0.55 if on else 0.45))
            p.drawText(QRectF(x, 26, r.width() - x - right, 17), int(Qt.AlignmentFlag.AlignVCenter),
                       _elide(self.sub, sf, r.width() - x - right))
        else:
            p.setFont(f)
            p.setPen(colour)
            p.drawText(r.adjusted(x, 0, -right, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       _elide(self.text(), f, r.width() - x - right))
        if self.count:
            p.setFont(look.font(11.5, QFont.Weight.DemiBold))
            p.setPen(look.with_alpha(look.RAIL_TEXT, 0.55))
            p.drawText(r.adjusted(0, 0, -12, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       self.count)
        p.end()


class _RailLabel(QWidget):
    """A section's name in small capitals, with room for buttons on its right."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text
        self.setFixedHeight(32)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 6, 0, 0)
        self.row.setSpacing(2)
        self.row.addStretch(1)

    def add_button(self, btn):
        self.row.addWidget(btn)
        return btn

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(look.label_font(10))
        p.setPen(look.with_alpha(look.RAIL_TEXT, 0.48))
        p.drawText(QRectF(14, 8, self.width() - 14, 22), int(Qt.AlignmentFlag.AlignVCenter), self.text.upper())
        p.end()


class _RailCard(QWidget):
    """A pane of glass in the rail, holding a group of its places."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(8, 10, 8, 10)
        self.col.setSpacing(2)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor(255, 255, 255, 20))
        g.setColorAt(1, QColor(255, 255, 255, 9))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawRoundedRect(r, 18, 18)
        p.end()


class _FoldHeader(QAbstractButton):
    """A section's heading that opens and closes it ("Made for you  ›")."""

    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon = icon
        self.open = True
        self._turn = 1.0
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)

    def set_open(self, on):
        self.open = on
        self._anim.start()

    def _step(self):
        want = 1.0 if self.open else 0.0
        self._turn += (want - self._turn) * 0.28
        if abs(want - self._turn) < 0.02:
            self._turn = want
            self._anim.stop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        draw_icon(p, self.icon, QRectF(14, (r.height() - 20) / 2, 20, 20), look.RAIL_TEXT)
        p.setFont(look.font(13.5, QFont.Weight.DemiBold))
        p.setPen(look.RAIL_TEXT)
        p.drawText(r.adjusted(46, 0, -40, 0), int(Qt.AlignmentFlag.AlignVCenter), self.text())
        p.save()
        c = QPointF(r.width() - 22, r.height() / 2)
        p.translate(c)
        p.rotate(90 * self._turn)
        draw_icon(p, "chevron", QRectF(-8, -8, 16, 16), look.with_alpha(look.RAIL_TEXT, 0.7))
        p.restore()
        p.end()


class _Mark(QWidget):
    """The app's own mark -- the lightning in its ring -- on a disc of the accent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.accent = QColor("#38bdf8")
        import os
        from app import config
        path = os.path.join(getattr(config, "_ASSET_DIR", config.BASE_DIR), "app_icon.png")
        if not os.path.exists(path):
            path = os.path.join(config.BASE_DIR, "app_icon.png")
        self._pix = QPixmap(path) if os.path.exists(path) else None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        halo = QRadialGradient(r.center(), r.width() / 2)
        halo.setColorAt(0.55, look.with_alpha(self.accent, 0.55))
        halo.setColorAt(1, look.with_alpha(self.accent, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(r)
        if self._pix is not None and not self._pix.isNull():
            p.drawPixmap(r.adjusted(3, 3, -3, -3), self._pix, QRectF(self._pix.rect()))
        else:
            draw_icon(p, "note", r.adjusted(8, 8, -8, -8), QColor("#ffffff"))
        p.end()


class SearchField(QWidget):
    """The rail's search: a pill of glass, its edge lit while you type, and a
    clear button once there's something in it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.edit = QLineEdit(self)
        self.edit.setObjectName("railSearch")
        self.edit.setPlaceholderText("Search songs, artists…")
        self.edit.setFont(look.font(13))
        self.edit.setFrame(False)
        self.edit.setStyleSheet("QLineEdit#railSearch { background: transparent; border: none; color: #ffffff;"
                                " selection-background-color: rgba(125,211,252,110); }")
        self.clear_btn = icon_button("close", "Clear", 26, 11, self)
        self.clear_btn.clicked.connect(lambda: (self.edit.clear(), self.edit.setFocus()))
        self.clear_btn.hide()
        self.edit.textChanged.connect(lambda t: self.clear_btn.setVisible(bool(t)))
        self.edit.installEventFilter(self)
        self.accent = QColor("#7dd3fc")

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.edit.setGeometry(38, 0, self.width() - 38 - 52, self.height())
        self.clear_btn.move(self.width() - 32, (self.height() - 26) // 2)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        focus = self.edit.hasFocus()
        if focus:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(look.with_alpha(self.accent, 0.16))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), r.height() / 2 + 1, r.height() / 2 + 1)
        p.setBrush(QColor(0, 0, 0, 60) if not focus else QColor(0, 0, 0, 80))
        p.setPen(QPen(look.with_alpha(self.accent, 0.75) if focus else QColor(255, 255, 255, 30), 1.1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        draw_icon(p, "search", QRectF(13, (self.height() - 17) / 2, 17, 17),
                  look.RAIL_TEXT if focus else look.RAIL_MUTED)
        if not self.edit.text() and not focus:
            p.setFont(look.font(10.5, QFont.Weight.DemiBold))
            p.setPen(look.with_alpha(look.RAIL_TEXT, 0.40))
            p.drawText(r.adjusted(0, 0, -14, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       "Ctrl F")
        p.end()


class Rail(QWidget):
    """The column on the left, in the playing cover's colours: on glass at
    the top, the mark, search, Home and Explore (charts & trending); then
    your library, your playlists, and -- at the bottom, folding away --
    what's made for you from your listening."""
    navigate = Signal(str)
    searched = Signal(str)
    mood = Signal(str)
    menu_requested = Signal(QPoint)

    MOODS = (("chill", "Chill", 0.52), ("focus", "Focus", 0.60), ("workout", "Workout", 0.02),
             ("lofi", "Lo-fi beats", 0.78), ("party", "Party", 0.90), ("classical", "Classical", 0.10),
             ("jazz", "Jazz", 0.08), ("indie", "Indie", 0.40))

    def __init__(self, parent=None):
        super().__init__(parent)
        from .widgets.wordmark import GradientLine
        self.setFixedWidth(look.RAIL_W + 36)
        self._pal = look.DEFAULT
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 2, 8, 4)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                             " { background: transparent; } QScrollBar:vertical { width: 6px; background:"
                             " transparent; } QScrollBar::handle:vertical { background: rgba(255,255,255,40);"
                             " border-radius: 3px; min-height: 30px; } QScrollBar::add-line, QScrollBar::sub-line"
                             " { height: 0; }")
        body = QWidget()
        self.col = QVBoxLayout(body)
        self.col.setContentsMargins(10, 10, 6, 14)
        self.col.setSpacing(0)
        self.items = {}
        self._dynamic = {"made": [], "playlists": []}

        # on glass: the mark, search, Home, Explore
        top = _RailCard()
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 6)
        head.setSpacing(10)
        self.mark = _Mark()
        head.addWidget(self.mark)
        self.brand = GradientLine("", "Music", "", px=26)
        self.brand.set_colors("#ffffff", "#7dd3fc", True, "#a5b4fc")
        head.addWidget(self.brand)
        head.addStretch(1)
        self.menu_btn = icon_button("dots", "Music settings and import", 32, 18, self)
        self.menu_btn.clicked.connect(
            lambda: self.menu_requested.emit(self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height()))))
        head.addWidget(self.menu_btn)
        top.col.addLayout(head)
        self.search_box = SearchField()
        self.search = self.search_box.edit
        self.search.returnPressed.connect(lambda: self.searched.emit(self.search.text()))
        top.col.addWidget(self.search_box)
        top.col.addSpacing(6)
        for key, text, icon in (("home", "Home", "home"), ("explore", "Explore", "trend"),
                                ("search", "Search results", "search")):
            top.col.addWidget(self._item(key, text, icon))
        self.items["search"].hide()
        self.col.addWidget(top)
        self.col.addSpacing(12)

        # your library
        self.col.addWidget(_RailLabel("Your library"))
        for key, text, icon in (("songs", "Liked songs", "heart"), ("artists", "Artists", "person"),
                                ("albums", "Albums", "disc"), ("local", "On this PC", "folder"),
                                ("queue", "Queue", "queue"), ("taste", "Your taste", "target")):
            self.col.addWidget(self._item(key, text, icon))
        self.col.addSpacing(10)

        # playlists, with new and import on the heading
        pl = _RailLabel("Playlists")
        new = pl.add_button(icon_button("plus", "New playlist", 28, 16))
        new.clicked.connect(lambda: self.navigate.emit("newplaylist"))
        self.col.addWidget(pl)
        self._pl_box = QVBoxLayout()
        self._pl_box.setSpacing(2)
        self.col.addLayout(self._pl_box)
        # first in the section, always: bring playlists over from other apps
        self.import_item = NavItem("import", "Import a playlist", sub="Spotify, Apple Music, YouTube",
                                   icon="playlist_import")
        self.import_item.setCheckable(False)
        self.import_item.setToolTip("Bring a playlist over from Spotify, Apple Music, YouTube Music or YouTube "
                                    "-- or a list of songs")
        self.import_item.clicked.connect(lambda: self.navigate.emit("import"))
        self._pl_box.addWidget(self.import_item)
        self.col.addSpacing(12)

        # made for you, at the bottom: it folds away
        self.made_card = _RailCard()
        self.made_head = _FoldHeader("Made for you", "sparkle")
        self.made_head.clicked.connect(self._toggle_made)
        self.made_card.col.addWidget(self.made_head)
        self._made_box = QVBoxLayout()
        self._made_box.setSpacing(2)
        self.made_card.col.addLayout(self._made_box)
        self.made_card.hide()
        self.col.addWidget(self.made_card)
        self.col.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def _item(self, key, text, icon):
        item = NavItem(key, text, icon=icon)
        item.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
        self.items[key] = item
        return item

    def _toggle_made(self):
        on = not self.made_head.open
        self.made_head.set_open(on)
        for w in self._dynamic["made"]:
            w.setVisible(on)

    def _set_dynamic(self, which, box, entries):
        for w in self._dynamic[which]:
            self.items.pop(w.key, None)
            w.setParent(None)
            w.deleteLater()
        self._dynamic[which] = []
        for key, title, sub, art_url in entries:
            item = NavItem(key, title, sub=sub, art_url=art_url)
            item.accent = self.items["home"].accent
            item.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
            box.addWidget(item)
            self.items[key] = item
            self._dynamic[which].append(item)

    def set_made_for_you(self, entries):
        """[(key, title, second line, cover url)] -- the algorithm's picks."""
        self._set_dynamic("made", self._made_box, entries)
        self.made_card.setVisible(bool(entries))
        for w in self._dynamic["made"]:
            w.setVisible(self.made_head.open)

    def set_playlists(self, entries):
        self._set_dynamic("playlists", self._pl_box, entries)

    def select(self, key):
        for k, item in self.items.items():
            item.setChecked(k == key)
        if key == "search":
            self.items["search"].show()

    def set_accent(self, colour):
        self.search_box.accent = colour
        self.mark.accent = QColor(colour)
        self.mark.update()
        self.import_item.accent = QColor(colour)
        self.import_item.update()
        for item in self.items.values():
            item.accent = colour
            if item.isChecked():
                item.update()

    def set_palette(self, pal):
        self._pal = pal
        self.update()

    def _panel(self):
        return QRectF(self.rect()).adjusted(8, 0, -8, 0)

    def paintEvent(self, event):
        # a floating panel of its own, on the canvas, rounded like the page and the player
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), look.mix(self._pal["deep"], QColor(3, 4, 7), 0.80))
        r = self._panel()
        deep = self._pal["deep"]
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, look.mix(deep, QColor(10, 12, 18), 0.42))
        g.setColorAt(1, look.mix(deep, QColor(6, 7, 11), 0.62))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 18, 18)
        glow = QRadialGradient(QPointF(r.left() + r.width() * 0.3, r.top()), r.width() * 1.4)
        glow.setColorAt(0, look.with_alpha(self._pal["accent"], 0.18))
        glow.setColorAt(1, look.with_alpha(self._pal["accent"], 0.0))
        p.setBrush(glow)
        p.drawRoundedRect(r, 18, 18)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
        p.end()


# ------------------------------------------------------------- the player ---
class ThinSlider(QSlider):
    """A thin line with the accent running along it and a soft knob -- the
    song's progress, and the volume. Under the cursor the line thickens a
    little; `knob` keeps the knob showing (the player's own lines)."""

    def __init__(self, top=False, parent=None, knob=True):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.top = top
        self.knob = knob
        self._hover = False
        self.accent = QColor("#f4a47a")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(14 if top else 20)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _pad(self):
        return 7.0

    def _value_at(self, x):
        if self.maximum() <= self.minimum():
            return self.minimum()
        pad = self._pad()
        f = max(0.0, min(1.0, (x - pad) / max(1.0, self.width() - 2 * pad)))
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
        pad = self._pad()
        w = self.width() - 2 * pad
        active = self._hover or self.isSliderDown()
        thick = 5.0 if active else 4.0
        y = self.height() / 2
        span = self.maximum() - self.minimum()
        f = (self.value() - self.minimum()) / span if span > 0 else 0.0
        x = pad + w * f
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(pad, y - thick / 2, w, thick), thick / 2, thick / 2)
        if f > 0:
            g = QLinearGradient(pad, 0, max(pad + 1, x), 0)
            g.setColorAt(0, look.with_alpha(self.accent.lighter(125), 0.95))
            g.setColorAt(1, self.accent)
            p.setBrush(g)
            p.drawRoundedRect(QRectF(pad, y - thick / 2, x - pad, thick), thick / 2, thick / 2)
        if self.knob or active:
            r = 6.5 if active else 5.6
            halo = QRadialGradient(QPointF(x, y), r * 2.4)
            halo.setColorAt(0, look.with_alpha(self.accent, 0.45))
            halo.setColorAt(1, look.with_alpha(self.accent, 0.0))
            p.setBrush(halo)
            p.drawEllipse(QPointF(x, y), r * 2.4, r * 2.4)
            p.setBrush(self.accent.lighter(150))
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
            p.drawEllipse(QPointF(x, y), r, r)
        p.end()


class VolumeControl(QWidget):
    """Speaker, line and number in one: the player's volume."""

    def __init__(self, width=110, pill=False, parent=None):
        super().__init__(parent)
        self.pill = pill
        row = QHBoxLayout(self)
        row.setContentsMargins(14 if pill else 0, 0, 18 if pill else 0, 0)
        row.setSpacing(8)
        self.icon = icon_button("speaker", "Mute", 34, 19)
        self.slider = ThinSlider()
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(width)
        self.slider.setToolTip("Volume")
        self.label = QLabel("80")
        self.label.setFont(look.font(13, QFont.Weight.DemiBold))
        self.label.setStyleSheet("color: rgba(255,255,255,200); background: transparent;")
        self.label.setFixedWidth(28)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider.valueChanged.connect(lambda v: self.label.setText(str(v)))
        for w in (self.icon, self.slider, self.label):
            row.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        self._row = row
        if pill:
            self.setFixedHeight(58)

    def fit(self, width, k=1.0):
        """The whole control `width` wide: the line takes what's left. `k`
        sizes the speaker and the margins with a smaller card."""
        k = max(0.4, min(1.0, k))
        if self.pill:
            self._row.setContentsMargins(int(14 * k), 0, int(18 * k), 0)
        self._row.setSpacing(max(3, int(8 * k)))
        n = max(20, int(round(34 * k)))
        self.icon.setFixedSize(n, n)
        self.icon.icon_size = max(10, int(round(19 * k)))
        m = self._row.contentsMargins()
        fixed = m.left() + m.right() + self.icon.width() + 2 * self._row.spacing()
        self.label.setVisible(width - fixed - self.label.width() >= 70)
        if self.label.isVisible():
            fixed += self.label.width()
        else:
            fixed -= self._row.spacing()
        self.slider.setFixedWidth(max(16, int(width - fixed)))
        self.setFixedWidth(int(width))

    def paintEvent(self, event):
        if not self.pill:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(look.with_alpha(look.TEXT, 0.14), 1))
        p.setBrush(look.with_alpha(look.TEXT, 0.045))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()


class TileButton(QAbstractButton):
    """An icon over its word, in a soft rounded frame (the full-screen
    player's Queue and EQ). Lit in the accent while its panel is open."""

    def __init__(self, kind, text, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setText(text)
        self.lit = False
        self.accent = QColor("#f4a47a")
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(92, 58)

    def set_lit(self, on):
        self.lit = on
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
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = min(18.0, r.height() / 2.6)
        if self.lit:
            p.setPen(QPen(look.with_alpha(self.accent.lighter(130), 0.8), 1.2))
            p.setBrush(look.with_alpha(self.accent, 0.26))
        else:
            p.setPen(QPen(look.with_alpha(look.TEXT, 0.22 if self._hover else 0.14), 1))
            p.setBrush(look.with_alpha(look.TEXT, 0.10 if self._hover else 0.045))
        p.drawRoundedRect(r, rad, rad)
        k = min(1.0, r.height() / 58.0)          # icon and word in proportion to the tile
        if r.height() < 40:                      # too short for both: the icon alone (its tip names it)
            isz = r.height() * 0.5
            draw_icon(p, self.kind, QRectF(r.center().x() - isz / 2, r.center().y() - isz / 2, isz, isz),
                      look.TEXT)
            p.end()
            return
        isz = 19.0 * k
        f = look.font(11 * k, QFont.Weight.DemiBold)
        th = QFontMetricsF(f).height()
        top = r.top() + (r.height() - isz - 3 - th) / 2
        draw_icon(p, self.kind, QRectF(r.center().x() - isz / 2, top, isz, isz), look.TEXT)
        p.setFont(f)
        p.setPen(look.TEXT)
        p.drawText(QRectF(r.left(), top + isz + 3, r.width(), th), int(Qt.AlignmentFlag.AlignCenter), self.text())
        p.end()


class Divider(QWidget):
    """A hairline between groups of controls."""

    def __init__(self, height=28, parent=None):
        super().__init__(parent)
        self.setFixedSize(1, height)

    def paintEvent(self, event):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 52))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), g)
        p.end()


class LinkText(QLabel):
    """A title or a name that opens something: underlined under the cursor."""
    clicked = Signal(QPoint)

    def __init__(self, text="", px=13.5, weight=QFont.Weight.DemiBold, alpha=255, parent=None):
        super().__init__(text, parent)
        self._full = text
        self._f = look.font(px, weight)
        self.setFont(self._f)
        self._alpha = alpha
        self._style(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):   # noqa: N802 -- QLabel's name
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 28 else "")
        self._fit()

    def text(self):
        return self._full

    def setFont(self, f):   # noqa: N802
        super().setFont(f)
        self._fit()

    def _fit(self):
        full = getattr(self, "_full", "")
        w = max(10, self.width())
        super().setText(QFontMetricsF(self.font()).elidedText(full, Qt.TextElideMode.ElideRight, w - 2)
                        if full else "")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _style(self, hover):
        self.setStyleSheet("color: rgba(255,255,255,%d); background: transparent;%s"
                           % (255 if hover else self._alpha, " text-decoration: underline;" if hover else ""))

    def enterEvent(self, e):
        self._style(True)

    def leaveEvent(self, e):
        self._style(False)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(e.globalPosition().toPoint())


class PlayButton(RingButton):
    """The big round play button: a ring in the cover's accent over a warm
    glass fill, a soft halo around it that breathes while the song plays.
    Its circle sits in the middle of the widget (the halo has room all
    round), so it lines up with the buttons beside it."""

    def __init__(self, size=56, icon_size=20, parent=None):
        super().__init__("play", "Play", size=size, icon_size=icon_size, ring=0.9, parent=parent)
        self.accent = QColor("#f4a47a")
        self.playing = False
        self._phase = 0.0
        self._breathe = QTimer(self)
        self._breathe.setInterval(50)
        self._breathe.timeout.connect(self._tick)

    def set_playing(self, on):
        self.playing = on
        if on:
            self._breathe.start()
        else:
            self._breathe.stop()
        self.update()

    def _tick(self):
        self._phase += 0.05
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        m = full.width() * 0.13
        r = full.adjusted(m, m, -m, -m)
        c = full.center()
        a = QColor(self.accent)
        k = (0.5 + 0.5 * math.sin(self._phase * 2.0)) if self.playing else 0.0
        halo = QRadialGradient(c, full.width() / 2)
        halo.setColorAt(r.width() / full.width() * 0.92, look.with_alpha(a, 0.30 + 0.14 * k + 0.08 * self._hover.value))
        halo.setColorAt(1, look.with_alpha(a, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(full)
        fill = QRadialGradient(QPointF(c.x(), c.y() - r.height() * 0.25), r.width() * 0.75)
        fill.setColorAt(0, look.with_alpha(a.lighter(120), 0.30 + 0.10 * self._hover.value))
        fill.setColorAt(1, look.with_alpha(a.darker(160), 0.22))
        p.setBrush(fill)
        p.setPen(QPen(a.lighter(118), max(2.0, r.width() * 0.045)))
        p.drawEllipse(r)
        ir = QRectF(0, 0, self.icon_size, self.icon_size)
        ir.moveCenter(c)
        if self.kind == "play":
            ir.translate(self.icon_size * 0.07, 0)        # a triangle looks centred a touch to the right
        if self.isDown():
            ir.translate(0, 0.6)
        draw_icon(p, self.kind, ir, look.TEXT)
        p.end()


class HeartButton(ChromeButton):
    """Like: a heart that fills (red) and pops when a song is liked."""

    def __init__(self, size=34, parent=None):
        super().__init__("heart", "Like", size=size, icon_size=max(18, int(size * 0.42)), parent=parent)
        self.apply_theme(look.ON_COLOUR)
        self._base = self.icon_size
        self.ringed = False
        self.liked = False
        self._pop = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)

    def set_liked(self, on, animate=False):
        self.liked = on
        self.set_kind("heart_filled" if on else "heart")
        self.tint = "#ff4d6d" if on else None
        self.set_tip("Liked — click to remove" if on else "Like (add to Liked songs)")
        if animate and on:
            self._pop = 1.0
            self._anim.start()
        self.update()

    def _step(self):
        self._pop = max(0.0, self._pop - 0.07)
        if not self._pop:
            self._anim.stop()
        self.icon_size = int(self._base + 7 * math.sin(self._pop * math.pi))
        self.update()

    def paintEvent(self, event):
        if self.ringed:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(look.with_alpha(look.TEXT, 0.30 + 0.25 * self._hover.value), 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
            p.end()
        super().paintEvent(event)


class PlayerBar(QWidget):
    """The player along the bottom, on floating glass in the cover's colours:
    the song (its title opens a menu, its artist their page), like, add,
    more | shuffle, previous, back 10 s, play, forward 10 s, next, repeat --
    every button on one centre line -- over a thin progress line | volume,
    Lyrics, queue, equalizer, details, download, full screen."""
    H = 116

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._pal = look.DEFAULT
        self._frost = None
        self.accent = QColor("#f4a47a")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 12, 28, 14)
        lay.setSpacing(18)
        V = Qt.AlignmentFlag.AlignVCenter

        left = self.left = QWidget()
        left.setFixedWidth(look.RAIL_W + 130)
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(14)
        self.art = _CoverButton(64)
        ll.addWidget(self.art, 0, V)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = LinkText("Nothing playing", 15, QFont.Weight.DemiBold)
        self.artist = LinkText("Search for a song to start", 12.5, QFont.Weight.Medium, 185)
        self.credit = QLabel("")
        self.credit.hide()
        from .music_panels import QualityBadge
        self.quality = QualityBadge(small=True)
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.artist)
        col.addSpacing(2)
        col.addWidget(self.quality, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        ll.addLayout(col, 1)
        self.save_btn = HeartButton(36)
        self.add_btn = icon_button("plus", "Add to a playlist", 36, 19)
        self.more_btn = icon_button("dots", "More", 36, 19)
        for w in (self.save_btn, self.add_btn, self.more_btn):
            ll.addWidget(w, 0, V)
        lay.addWidget(left, 0, V)
        self.sep_l = Divider(40)
        lay.addWidget(self.sep_l, 0, V)

        mid = QVBoxLayout()
        mid.setSpacing(0)
        mid.setContentsMargins(0, 0, 0, 0)
        ctl = QHBoxLayout()
        ctl.setSpacing(14)
        ctl.addStretch(1)
        self.shuffle_btn = icon_button("shuffle", "Shuffle", 36, 19)
        self.prev_btn = icon_button("prev", "Previous", 38, 19)
        self.back10_btn = icon_button("back10", "Back 10 seconds", 38, 24)
        self.play_btn = PlayButton(62, 19)
        self.fwd10_btn = icon_button("fwd10", "Forward 10 seconds", 38, 24)
        self.next_btn = icon_button("next", "Next", 38, 19)
        self.repeat_btn = icon_button("repeat", "Repeat: off", 36, 19)
        for b in (self.shuffle_btn, self.prev_btn, self.back10_btn, self.play_btn, self.fwd10_btn, self.next_btn,
                  self.repeat_btn):
            ctl.addWidget(b, 0, V)
        ctl.addStretch(1)
        mid.addLayout(ctl)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.pos_label = QLabel("0:00")
        self.len_label = QLabel("0:00")
        for lab in (self.pos_label, self.len_label):
            lab.setFont(look.font(12, QFont.Weight.Medium))
            lab.setStyleSheet("color: rgba(255,255,255,170); background: transparent;")
            lab.setFixedWidth(44)
        self.len_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.pos_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.seek = ThinSlider()
        self.seek.setRange(0, 0)
        self.seek.setMaximumWidth(620)
        seek_row.addStretch(1)
        seek_row.addWidget(self.pos_label, 0, V)
        seek_row.addWidget(self.seek, 8, V)
        seek_row.addWidget(self.len_label, 0, V)
        seek_row.addStretch(1)
        mid.addLayout(seek_row)
        lay.addLayout(mid, 1)

        self.sep_r = Divider(40)
        lay.addWidget(self.sep_r, 0, V)
        right = QHBoxLayout()
        right.setSpacing(6)
        self.vol = VolumeControl(104)
        self.vol_icon, self.volume, self.vol_label = self.vol.icon, self.vol.slider, self.vol.label
        self.lyrics_btn = Pill("Lyrics", "mic", style="ghost")
        self.lyrics_btn.setToolTip("Lyrics")
        self.queue_btn = icon_button("queue", "Queue", 38, 20)
        self.eq_btn = icon_button("sliders", "Equalizer & effects", 38, 20)
        self.info_btn = icon_button("info", "Song info", 38, 20)
        self.keep_btn = icon_button("download", "Download this song", 38, 20)
        self.full_btn = icon_button("expand", "Full screen player", 38, 18)
        right.addWidget(self.vol, 0, V)
        right.addSpacing(8)
        for w in (self.lyrics_btn, self.queue_btn, self.eq_btn, self.info_btn, self.keep_btn, self.full_btn):
            right.addWidget(w, 0, V)
        lay.addLayout(right)

    def set_palette(self, pal, frost=None):
        self._pal = pal
        self._frost = frost
        self.set_accent(pal["accent"])
        self.update()

    def set_accent(self, colour):
        self.accent = QColor(colour)
        for w in (self.play_btn, self.seek, self.volume, self.lyrics_btn):
            w.accent = self.accent
            w.update()

    # (narrower than, what folds away) -- the least needed first
    _FOLD = ((1480, "keep_btn"), (1400, "info_btn"), (1330, "vol_label"), (1290, "volume"), (1220, "lyrics_btn"),
             (1150, "eq_btn"), (1060, "back10_btn"), (1060, "fwd10_btn"), (1000, "add_btn"), (940, "more_btn"),
             (900, "shuffle_btn"), (900, "repeat_btn"), (860, "queue_btn"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.width()
        for limit, name in self._FOLD:
            getattr(self, name).setVisible(w >= limit)
        self.sep_l.setVisible(w >= 1100)
        self.sep_r.setVisible(w >= 1100)
        self.left.setFixedWidth(look.RAIL_W + 130 if w >= 1250 else max(200, int(w * 0.26)))

    def glass_rect(self):
        return QRectF(self.rect()).adjusted(8, 8, -8, -8)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.glass_rect()
        rad = 18.0
        shape = QPainterPath()
        shape.addRoundedRect(r, rad, rad)
        p.save()
        p.setClipPath(shape)
        deep = self._pal["deep"]
        p.fillRect(r, look.mix(deep, QColor(0, 0, 0), 0.42))
        if self._frost is not None:
            p.setOpacity(0.42)
            p.drawPixmap(r, self._frost, QRectF(self._frost.rect()))
            p.setOpacity(1.0)
        a = self.accent
        # the cover's warmth: a glow behind the cover, and a softer one behind play
        g = QRadialGradient(QPointF(r.left() + 70, r.center().y()), r.height() * 3.2)
        g.setColorAt(0, look.with_alpha(a, 0.34))
        g.setColorAt(1, look.with_alpha(a, 0.0))
        p.fillRect(r, g)
        pc = self.play_btn.geometry().center()
        g2 = QRadialGradient(QPointF(pc.x(), r.top()), r.height() * 2.6)
        g2.setColorAt(0, look.with_alpha(a, 0.20))
        g2.setColorAt(1, look.with_alpha(a, 0.0))
        p.fillRect(r, g2)
        shade = QLinearGradient(0, r.top(), 0, r.bottom())
        shade.setColorAt(0, QColor(255, 255, 255, 10))
        shade.setColorAt(1, QColor(0, 0, 0, 70))
        p.fillRect(r, shade)
        look.paint_grain(p, r, 0.45)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        top = QLinearGradient(r.left(), 0, r.right(), 0)
        top.setColorAt(0, look.with_alpha(a, 0.0))
        top.setColorAt(0.5, look.with_alpha(a.lighter(130), 0.50))
        top.setColorAt(1, look.with_alpha(a, 0.0))
        p.setPen(QPen(top, 1.2))
        p.drawLine(QPointF(r.left() + rad, r.top() + 0.6), QPointF(r.right() - rad, r.top() + 0.6))
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
        self._pix = look.rounded(pm, self.width(), self.height(), 10) if pm is not None else None
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
