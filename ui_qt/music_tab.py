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

Playback is FFmpeg into Qt's audio sink (audio_engine.py): the equalizer
and effects are heard at once, the next song follows with no gap or blends
in. A YouTube song starts streaming the moment its link is known -- links
are asked for ahead, for the next songs and the first search results -- and
is fetched whole meanwhile, to carry on from if the stream breaks.

What plays after a song you chose is its radio, ordered by the listening
session (app/core/upnext.py): same language and mood, nothing repeated.
"""
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QUrl, \
    QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QKeySequence, QLinearGradient, \
    QPainter, QPen, QPixmap, QRegion, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QSpacerItem,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app import config
from app.core import lyrics as lyrics_db
from app.core import music_sources
from app.core import eq as eq_core
from app.core import taste as taste_core
from app.core import upnext
from app.logging_setup import get_logger
from app.utils import music_library
from app.utils import settings as settings_store
from app.utils import ui_state
from app.utils.formatting import format_eta

from . import music_look as look
from . import theme
from .browser_chrome import ChromeButton, style_menu
from .audio_engine import Engine, loudness
from .music_import import ImportSheet
from .music_onboarding import Onboarding
from .music_panels import EqPanel, InfoPanel, QueuePanel, SidePanel
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
PANEL_GAP = 8        # the dark canvas between the rail, the page and the player
PANEL_RADIUS = 18


def canvas_colour(pal):
    """The canvas the three panels float on: the page's colour, nearly black."""
    return look.mix(pal["deep"], QColor(3, 4, 7), 0.80)


class _Corners(QWidget):
    """The page's panel rounded: its corners filled with the canvas, smooth
    (antialiased), over whatever page is showing. Lets clicks through."""

    def __init__(self, stage):
        super().__init__(stage)
        self.stage = stage
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        outside = QPainterPath()
        outside.addRect(r)
        inner = QPainterPath()
        inner.addRoundedRect(r, PANEL_RADIUS, PANEL_RADIUS)
        p.fillPath(outside.subtracted(inner), canvas_colour(self.stage.pal))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), PANEL_RADIUS, PANEL_RADIUS)
        p.end()


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
        corners = getattr(self, "corners", None)
        if corners is not None:
            corners.setGeometry(self.rect())
            corners.raise_()
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


class _ChipBar(QScrollArea):
    """A row of filter chips that scrolls sideways: the one chosen is lit."""
    chosen = Signal(str)

    def __init__(self, names, current, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                           " { background: transparent; }")
        self.setFixedHeight(48)
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(8)
        for name in names:
            chip = Pill(name, style="tab_on" if name == current else "glass")
            chip.clicked.connect(lambda _c=False, n=name: self.chosen.emit(n))
            row.addWidget(chip)
        row.addStretch(1)
        self.setWidget(body)

    def wheelEvent(self, e):
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - e.angleDelta().y())
        e.accept()


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
        self._size, self._radius, self._pix, self._src = size, radius, None, None
        self.setFixedSize(size, size)

    def set_size(self, size, radius=None):
        self._size = int(size)
        if radius is not None:
            self._radius = radius
        self.setFixedSize(self._size, self._size)

    def set_pixmap(self, pix):
        self._src = pix
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


class _PlayerCard(QWidget):
    """The full-screen player's own card: the cover, the song (its title
    and artist open things), like and more; a thin progress line with the
    time either side; shuffle | previous, back 10 s, play, forward 10 s,
    next | repeat on one centre line; and Lyrics, Queue, EQ and the volume
    along the bottom. Sized from the width it's given (set_width)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from .music_widgets import Divider, HeartButton, LinkText, PlayButton, TileButton, VolumeControl
        self.accent = QColor("#f4a47a")
        V = Qt.AlignmentFlag.AlignVCenter
        col = self.col = QVBoxLayout(self)
        col.setSpacing(0)
        self._gaps = []                     # (spacer, its height at full size): scaled with the card
        self.art = _Art(400, 26)
        col.addWidget(self.art, 0, Qt.AlignmentFlag.AlignHCenter)
        self._gap(col, 22)
        row = QHBoxLayout()
        row.setSpacing(12)
        names = QVBoxLayout()
        names.setSpacing(4)
        self.title = LinkText("", 25, QFont.Weight.Bold)
        self.artist = LinkText("", 16.5, QFont.Weight.Medium, 175)
        from .music_panels import QualityBadge
        self.quality = QualityBadge()
        names.addWidget(self.title)
        names.addWidget(self.artist)
        self._gap(names, 4)
        names.addWidget(self.quality, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(names, 1)
        self.heart = HeartButton(50)
        self.heart.ringed = True
        self.more_btn = RingButton("dots", "More", size=50, icon_size=20)
        row.addWidget(self.heart, 0, V)
        row.addWidget(self.more_btn, 0, V)
        col.addLayout(row)
        self._gap(col, 18)
        self.seek = ThinSlider()
        col.addWidget(self.seek)
        times = QHBoxLayout()
        times.setContentsMargins(4, 0, 4, 0)
        self.pos_label = QLabel("0:00")
        self.len_label = QLabel("0:00")
        for lab in (self.pos_label, self.len_label):
            lab.setFont(look.font(12.5, QFont.Weight.Medium))
            lab.setStyleSheet("color: rgba(255,255,255,175); background: transparent;")
        times.addWidget(self.pos_label)
        times.addStretch(1)
        times.addWidget(self.len_label)
        col.addLayout(times)
        self._gap(col, 16)
        ctl = QHBoxLayout()
        ctl.setSpacing(0)
        self.shuffle_btn = icon_button("shuffle", "Shuffle", 44, 21, self)
        self.prev_btn = icon_button("prev", "Previous", 48, 24, self)
        self.back10_btn = icon_button("back10", "Back 10 seconds", 48, 29, self)
        self.play_btn = PlayButton(96, 28, parent=self)
        self.fwd10_btn = icon_button("fwd10", "Forward 10 seconds", 48, 29, self)
        self.next_btn = icon_button("next", "Next", 48, 24, self)
        self.repeat_btn = icon_button("repeat", "Repeat: off", 44, 21, self)
        self.div_a, self.div_b = Divider(34), Divider(34)
        ctl.addWidget(self.shuffle_btn, 0, V)
        ctl.addStretch(2)
        ctl.addWidget(self.div_a, 0, V)
        ctl.addStretch(2)
        for w in (self.prev_btn, self.back10_btn, self.play_btn, self.fwd10_btn, self.next_btn):
            ctl.addWidget(w, 0, V)
            if w is not self.next_btn:
                ctl.addStretch(1)
        ctl.addStretch(2)
        ctl.addWidget(self.div_b, 0, V)
        ctl.addStretch(2)
        ctl.addWidget(self.repeat_btn, 0, V)
        col.addLayout(ctl)
        self._gap(col, 18)
        foot = self.foot = QHBoxLayout()
        foot.setSpacing(9)
        self.lyrics_pill = Pill("Lyrics", "note", style="ghost")
        self.lyrics_pill.height_px = 58
        self.lyrics_pill.setToolTip("Lyrics")
        self.queue_tile = TileButton("queue", "Queue")
        self.queue_tile.setToolTip("Queue")
        self.eq_tile = TileButton("eq", "EQ")
        self.eq_tile.setToolTip("Equalizer & effects")
        self.div_c = Divider(36)
        self.vol = VolumeControl(96, pill=True)
        self.vol_icon, self.volume, self.vol_label = self.vol.icon, self.vol.slider, self.vol.label
        for w in (self.lyrics_pill, self.queue_tile, self.eq_tile):
            foot.addWidget(w, 0, V)
        foot.addWidget(self.div_c, 0, V)
        foot.addWidget(self.vol, 0, V)
        col.addLayout(foot)
        self.set_width(470)

    def _gap(self, layout, px):
        sp = QSpacerItem(0, px, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout.addSpacerItem(sp)
        self._gaps.append((sp, px))

    def set_width(self, w):
        """Everything in proportion to the card's width -- the buttons, their
        icons, the words, the gaps -- with nothing held at a minimum size, so
        a small window gets a smaller card that still fits together."""
        w = int(w)
        k = w / 520.0                       # sizes: as designed at 520 px
        ki = w / 470.0                      # icons and words: as designed at 470 px
        pad = int(round(w * 0.072))
        self.col.setContentsMargins(pad, pad, pad, int(pad * 0.85))
        inner = w - 2 * pad
        if inner != self.art._size:
            pix = self.art._src
            self.art.set_size(inner, max(10, int(inner * 0.085)))
            if pix is not None:
                self.art.set_pixmap(pix)
        for sp, px in self._gaps:
            sp.changeSize(0, max(2, int(round(px * k))), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.title._f = look.font(max(12.0, 25 * k), QFont.Weight.Bold)
        self.title.setFont(self.title._f)
        self.artist._f = look.font(max(9.5, 16.5 * k), QFont.Weight.Medium)
        self.artist.setFont(self.artist._f)
        for lab in (self.pos_label, self.len_label):
            lab.setFont(look.font(max(9.0, 12.5 * ki), QFont.Weight.Medium))
        ring = int(round(50 * min(1.0, ki)))
        for b, icon in ((self.heart, 0.42), (self.more_btn, 0.40)):
            b.setFixedSize(ring, ring)
            b.icon_size = max(10, int(ring * icon))
        self.heart._base = self.heart.icon_size
        play = int(round(96 * k))
        self.play_btn.setFixedSize(play, play)
        self.play_btn.icon_size = int(play * 0.29)
        for b, base, icon in ((self.prev_btn, 48, 24), (self.next_btn, 48, 24), (self.back10_btn, 48, 29),
                              (self.fwd10_btn, 48, 29), (self.shuffle_btn, 44, 21), (self.repeat_btn, 44, 21)):
            n = int(round(base * k))
            b.setFixedSize(n, n)
            b.icon_size = max(9, int(round(icon * min(1.15, ki))))
        for dv, h in ((self.div_a, 34), (self.div_b, 34), (self.div_c, 36)):
            dv.setFixedSize(1, max(10, int(h * k)))
        # the bottom row in the design's proportions: Lyrics, Queue, EQ, then the volume takes the rest
        tile_h = int(round(inner * 0.125))
        tile_w = int(round(inner * 0.155))
        lyr_w = int(round(inner * 0.235))
        for t in (self.queue_tile, self.eq_tile):
            t.setFixedSize(tile_w, tile_h)
        self.lyrics_pill.ui_scale = min(1.0, ki)
        self.lyrics_pill.height_px = tile_h
        self.lyrics_pill.setFixedSize(lyr_w, tile_h)
        self.vol.setFixedHeight(tile_h)
        self.foot.setSpacing(max(4, int(round(9 * k))))
        sp = self.foot.spacing()
        self.vol.fit(inner - lyr_w - 2 * tile_w - self.div_c.width() - 4 * sp - 2, ki)
        self.radius = max(18.0, w * 0.085)        # round, like the cover inside it
        self.setFixedWidth(w)
        self.layout().invalidate()
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())

    def set_accent(self, colour):
        self.accent = QColor(colour)
        for wdg in (self.play_btn, self.seek, self.volume, self.lyrics_pill, self.queue_tile, self.eq_tile):
            wdg.accent = self.accent
            wdg.update()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = getattr(self, "radius", 36.0)
        frosted = look.paint_frost(p, self, r, rad)
        look.paint_glass(p, r, rad, None, None, look.mix(QColor(20, 16, 14), self.accent, 0.10),
                         strength=0.60 if frosted else 0.72)
        p.end()


class _FullView(QWidget):
    """Now playing, full screen, for listening: the album's own cover
    filling the screen, softened and slowly drifting, and the player's card
    of frosted glass in the middle -- always in the middle. What opens from
    it opens round it, the same size on either side: the queue on the left;
    the equalizer and effects, the song's details, or the lyrics on the
    right. Everything is sized from the window (_relayout), so it keeps its
    proportions on any screen."""
    closed = Signal()
    lyrics_toggled = Signal(bool)
    lang_requested = Signal(QPoint)
    theme_requested = Signal(QPoint)
    TOP, BOTTOM = 78, 40

    def __init__(self, make_list, eq_settings, parent=None):
        super().__init__(parent)
        from .music_lyrics import LyricsView
        from .music_panels import EqPanel, GlassCard, InfoPanel, QueuePanel, _scrolling
        self._bg = None
        self._t = 0.0
        self._pal = look.DEFAULT
        self._anims = []
        self._pops = {}                     # card -> popping in or out: {"pix", "v", "opening", "anim"}
        # it paints every pixel itself (the fluid, or the cover over its shade): without this
        # Qt repaints the whole window's backdrop under it on every frame -- most of a frame's cost
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._drift = QTimer(self)
        self._drift.setTimerType(Qt.TimerType.PreciseTimer)
        self._drift.setInterval(16)                  # 60 frames a second
        self._drift.timeout.connect(self._tick)
        self._bg_scaled = None
        # the living background: a fluid animation in the cover's colours, on the beat (music_backdrop)
        self.backdrop_mode = "aurora"
        self.levels = None                  # () -> (bass, loudness) of what's playing
        self._fluid = None
        self._fluid_img = None
        self._beat = None
        self._clock = 0.0
        self._frame = 0
        self._last_tick = time.monotonic()
        self.hide()

        self.card = _PlayerCard(self)
        c = self.card
        (self.art, self.title, self.artist, self.heart, self.more_btn, self.seek, self.pos_label, self.len_label,
         self.shuffle_btn, self.prev_btn, self.back10_btn, self.play_btn, self.fwd10_btn, self.next_btn,
         self.repeat_btn, self.lyrics_pill, self.queue_tile, self.eq_tile, self.vol_icon, self.volume,
         self.vol_label) = (c.art, c.title, c.artist, c.heart, c.more_btn, c.seek, c.pos_label, c.len_label,
                            c.shuffle_btn, c.prev_btn, c.back10_btn, c.play_btn, c.fwd10_btn, c.next_btn,
                            c.repeat_btn, c.lyrics_pill, c.queue_tile, c.eq_tile, c.vol_icon, c.volume,
                            c.vol_label)
        self.queue_pill, self.fx_btn = self.queue_tile, self.eq_tile
        self.credit = QLabel("")            # (not shown: the card keeps to the song and its controls)
        self.credit.hide()

        # round it: the queue on the left; the equalizer, the details or the lyrics on the right
        self.queue_card = GlassCard("Queue", "queue", self)
        self.queue = QueuePanel(make_list)
        self.queue_card.col.addWidget(self.queue, 1)
        self.fx_card = GlassCard("Audio & effects", "sliders", self)
        self.fx = EqPanel(eq_settings)
        self.fx_card.col.addWidget(_scrolling(self.fx), 1)
        self.info_card = GlassCard("Song information", "info", self)
        self.info = InfoPanel()
        self.info_card.col.addWidget(_scrolling(self.info), 1)
        for card in (self.queue_card, self.fx_card, self.info_card):
            card.hide()
            if card.close_btn is not None:
                card.close_btn.clicked.disconnect()
                card.close_btn.clicked.connect(lambda _c=False, k=card: self.show_card(k, False))
        self.lyrics = LyricsView(self)
        self.lyrics.hide()

        # along the top: the lyrics' language (while they show), the song's details, and out
        self.lang_btn = Pill("Lyrics language", style="ghost", parent=self)
        self.lang_btn.setToolTip("Lyrics in another script or language, and a translation under each line")
        self.lang_btn.hide()
        self.lang_btn.clicked.connect(lambda: self.lang_requested.emit(
            self.lang_btn.mapToGlobal(QPoint(0, self.lang_btn.height() + 6))))
        self.info_btn = icon_button("info", "Song information", 44, 20, self)
        self.theme_btn = icon_button("sparkle", "Background: Liquid aurora, Electric nebula or a still cover",
                                     44, 20, self)
        self.theme_btn.clicked.connect(lambda: self.theme_requested.emit(
            self.theme_btn.mapToGlobal(QPoint(0, self.theme_btn.height() + 6))))
        self.close_btn = icon_button("shrink", "Leave full screen (Esc)", 44, 18, self)
        self.close_btn.clicked.connect(self.closed)
        self.lyrics_btn = self.lyrics_pill

        self.queue_tile.clicked.connect(lambda: self.show_card(self.queue_card))
        self.eq_tile.clicked.connect(lambda: self.show_card(self.fx_card))
        self.info_btn.clicked.connect(lambda: self.show_card(self.info_card))
        self.lyrics_pill.clicked.connect(lambda: self.lyrics_toggled.emit(not self.lyrics.isVisible()))

    # ---- the cards round the player ----
    def is_open(self, card):
        """Showing, or popping in (not popping out)."""
        pop = self._pops.get(card)
        return pop["opening"] if pop is not None else card.isVisible()

    def show_card(self, card, on=None):
        on = (not self.is_open(card)) if on is None else on
        if on and card in (self.fx_card, self.info_card):
            other = self.info_card if card is self.fx_card else self.fx_card
            self._pop(other, False)
            if self.lyrics.isVisible():
                self.lyrics_toggled.emit(False)
        if on:
            self._relayout()
        self._pop(card, on)
        self._sync_lit()

    def _sync_lit(self):
        self.queue_tile.set_lit(self.is_open(self.queue_card))
        self.eq_tile.set_lit(self.is_open(self.fx_card))
        self.info_btn.tint = "#ffd2b8" if self.is_open(self.info_card) else None
        self.info_btn.update()

    POP_IN_MS, POP_OUT_MS = 380, 200

    def _pop(self, card, opening):
        """A card pops in -- grows out of its place from a little smaller,
        fading in, just past its size and settling -- or pops out: shrinks a
        little and fades away. Drawn from a picture of the card (taken once),
        so the move costs no more than the background under it; the card
        itself is back the moment it lands."""
        if self.is_open(card) == opening and card not in self._pops:
            return
        old = self._pops.pop(card, None)
        start = 0.0
        if old is not None:
            old["anim"].stop()
            start = 1.0 - min(1.0, max(0.0, old["v"]))      # turn back from where it got to
        if not self.isVisible() or self.width() < 200:
            card.setVisible(opening)
            if opening:
                card.stackUnder(self.card)
            return
        if opening:
            card.show()
            card.stackUnder(self.card)
        pix = card.grab()
        card.hide()
        anim = QVariantAnimation(self)
        anim.setDuration(int((self.POP_IN_MS if opening else self.POP_OUT_MS) * (1.0 - start * 0.6)))
        if opening:
            curve = QEasingCurve(QEasingCurve.Type.OutBack)
            curve.setOvershoot(1.15)
        else:
            curve = QEasingCurve(QEasingCurve.Type.InCubic)
        anim.setEasingCurve(curve)
        anim.setStartValue(start)
        anim.setEndValue(1.0)
        entry = {"pix": pix, "v": start, "opening": opening, "anim": anim}
        self._pops[card] = entry
        anim.valueChanged.connect(lambda v, c=card, e=entry: self._pop_step(c, e, v))
        anim.finished.connect(lambda c=card, e=entry: self._pop_done(c, e))
        anim.start()

    def _pop_rect(self, card, e):
        """Where the picture of a popping card is drawn, and how strongly."""
        r = QRectF(card.geometry())
        v = e["v"]
        if e["opening"]:
            scale, alpha = 0.86 + 0.14 * v, min(1.0, max(0.0, v * 1.7))
        else:
            scale, alpha = 1.0 - 0.10 * v, max(0.0, 1.0 - v)
        w, h = r.width() * scale, r.height() * scale
        return QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), alpha

    def _pop_step(self, card, e, v):
        e["v"] = float(v)
        self.update(card.geometry().adjusted(-24, -24, 24, 24))

    def _pop_done(self, card, e):
        if self._pops.get(card) is not e:
            return
        del self._pops[card]
        if e["opening"]:
            card.show()
            card.stackUnder(self.card)
        self.update(card.geometry().adjusted(-24, -24, 24, 24))
        self._sync_lit()

    def _paint_pops(self, p):
        for card, e in self._pops.items():
            rect, alpha = self._pop_rect(card, e)
            if alpha <= 0.01:
                continue
            p.save()
            p.setOpacity(alpha)
            p.drawPixmap(rect, e["pix"], QRectF(e["pix"].rect()))
            p.restore()

    # ---- proportions ----
    def _relayout(self):
        W, H = self.width(), self.height()
        if W < 200 or H < 200:
            return
        top, bottom = self.TOP, self.BOTTOM
        room = H - top - bottom
        # the card: 15% under what fills the height, and smaller still in a small window -- it shrinks
        # until it fits, all of it in proportion (_PlayerCard.set_width)
        cw = int(max(220, min(540, H * 0.47, W * 0.34) * 0.85))
        for _ in range(4):
            self.card.set_width(cw)
            ch = self.card.height()
            if ch <= room:
                break
            cw = int(max(200, cw * room / float(ch) - 4))
        self.card.set_width(cw)
        ch = self.card.height()
        cx = (W - cw) // 2
        cy = top + max(0, (room - ch) // 2)
        self.card.move(cx, cy)
        k = max(0.75, min(1.3, min(W / 1700.0, H / 1000.0)))
        gap = int(34 * k)
        side_w = int(min(cw * 0.96, (W - cw) // 2 - gap - 36))
        side_h = ch
        self.queue_card.setGeometry(cx - gap - side_w, cy, side_w, side_h)
        for card in (self.fx_card, self.info_card):
            card.setGeometry(cx + cw + gap, cy, side_w, side_h)
        lx = cx + cw + gap + 10
        self.lyrics.setGeometry(lx, top, max(200, W - lx - 56), H - top - bottom)
        # the top bar
        self.close_btn.move(W - 36 - self.close_btn.width(), 22)
        self.info_btn.move(self.close_btn.x() - 10 - self.info_btn.width(), 22)
        self.theme_btn.move(self.info_btn.x() - 10 - self.theme_btn.width(), 22)
        self.lang_btn.adjustSize()
        self.lang_btn.move(self.theme_btn.x() - 14 - self.lang_btn.width(), 25)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    # ---- the song ----
    def set_background(self, image):
        """The cover itself, lightly softened -- recognisable, not a smear."""
        self._bg = None
        if image is not None and not image.isNull():
            try:
                from PIL import Image, ImageFilter
                buf = image.convertToFormat(QImage.Format.Format_RGBA8888)
                pil = Image.frombuffer("RGBA", (buf.width(), buf.height()), bytes(buf.constBits()),
                                       "raw", "RGBA", buf.bytesPerLine(), 1)
                pil = pil.convert("RGB").resize((360, 360)).filter(ImageFilter.GaussianBlur(10))
                data = pil.tobytes("raw", "RGB")
                self._bg = QPixmap.fromImage(QImage(data, 360, 360, 360 * 3, QImage.Format.Format_RGB888).copy())
            except Exception:   # noqa: BLE001 -- a plain background then
                logger.debug("Couldn't soften the cover", exc_info=True)
        self._bg_scaled = None
        self.update()

    def _backdrop(self):
        """The cover and its shade, drawn once at a little over the window's
        size; each frame only moves it."""
        w, h = self.width(), self.height()
        key = (w, h, id(self._bg), self._pal["deep"].rgb())
        if self._bg_scaled is not None and self._bg_scaled[0] == key:
            return self._bg_scaled[1]
        s = int(max(w, h) * 1.16)
        img = QPixmap(s, s)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._bg is not None:
            p.drawPixmap(QRectF(0, 0, s, s), self._bg, QRectF(self._bg.rect()))
        else:
            p.fillRect(img.rect(), self._pal["deep"])
        p.end()
        self._bg_scaled = (key, img)
        return img

    def set_palette(self, pal):
        self._pal = pal
        self._bg_scaled = None
        self.fx.set_accent(pal["accent"])
        self.card.set_accent(pal["accent"])
        self.update()

    def set_lang_label(self, text):
        self.lang_btn.setText(text or "Lyrics language")
        self.lang_btn.setVisible(bool(text) and self.lyrics.isVisible())
        self._relayout()

    def show_lyrics(self, on):
        if on:
            self._pop(self.fx_card, False)
            self._pop(self.info_card, False)
            self._relayout()
            self.lyrics.show()
            self.lyrics.raise_()
        else:
            self.lyrics.hide()
            self.lang_btn.hide()
        self.lyrics_pill.style = "glow" if on else "ghost"
        self.lyrics_pill.setToolTip("Hide lyrics" if on else "Lyrics")
        self.lyrics_pill.update()
        self._sync_lit()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout()
        self._drift.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._drift.stop()

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        self._t += dt
        if self.backdrop_mode == "still":
            # nothing moves: drawn once (and again only when the song or the size changes)
            if self._fluid_img is not None:
                self._fluid_img = None
                self.update()
            return
        if self.backdrop_mode in ("aurora", "nebula"):
            from .music_backdrop import BeatFollower, FluidRenderer
            if self._fluid is None:
                self._fluid = FluidRenderer()
                self._beat = BeatFollower()
            if self._fluid.ok:
                bass, loud = self.levels() if self.levels is not None else (0.0, 0.0)
                _beat, energy = self._beat.update(bass, loud)
                # the music sets the pace: faster on the beat and when it's loud -- nothing pops or flashes
                self._clock += dt * self._beat.speed(dt)
                scale = 3 if self.backdrop_mode == "aurora" else 2
                img = self._fluid.render(self.backdrop_mode, self._clock, 0.0, 0.4, self._pal,
                                         max(64, self.width() // scale), max(36, self.height() // scale))
                if img is not None:
                    # the shade the cover had, laid into the frame itself -- so the glass cards,
                    # which paint the frame under them, match the open part exactly
                    sp = QPainter(img)
                    deep = self._pal["deep"]
                    shade = QLinearGradient(0, 0, 0, img.height())
                    shade.setColorAt(0, look.with_alpha(deep, 0.28))
                    shade.setColorAt(.5, look.with_alpha(deep, 0.10))
                    shade.setColorAt(1, look.with_alpha(deep, 0.45))
                    sp.fillRect(img.rect(), shade)
                    sp.end()
                self._fluid_img = img
                self._frosted = None
        else:
            self._fluid_img = None
        if self._fluid_img is None:
            self.update()
            return
        # the open background moves every frame; the glass cards -- which frost it
        # themselves -- take turns, one a frame (their buttons cost the most to draw),
        # so no frame costs more than another
        self._frame += 1
        cards = [c for c in (self.card, self.queue_card, self.fx_card, self.info_card) if c.isVisible()]
        open_part = QRegion(self.rect())
        for c in cards:
            open_part -= QRegion(c.geometry())
        self.update(open_part)
        if cards:
            cards[self._frame % len(cards)].update()

    def frost_source(self, rect):
        """(the background under `rect`, the same frosted), each at the
        rect's size -- for a glass card to paint itself on; None without the
        moving background."""
        img = self._fluid_img
        if img is None or self.width() < 2:
            return None
        if getattr(self, "_frosted", None) is None:
            small = img.scaled(max(8, img.width() // 6), max(8, img.height() // 6),
                               Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._frosted = small.scaled(img.width(), img.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        sx, sy = img.width() / float(self.width()), img.height() / float(self.height())
        src = QRectF(rect.x() * sx, rect.y() * sy, rect.width() * sx, rect.height() * sy).toAlignedRect()
        return img.copy(src), self._frosted.copy(src)

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        if self._fluid_img is not None:
            # the fluid, drawn small and scaled up smoothly: soft, as if through glass -- under the
            # same shade and film grain as the cover was, so it sits in the app's look
            p.drawImage(QRectF(self.rect()), self._fluid_img)
            look.paint_grain(p, QRectF(self.rect()), 0.85 * look.FULL_GRAIN)
            self._paint_pops(p)
            p.end()
            return
        # the cover across the screen, drifting slowly (moved, not re-scaled: smooth)
        bg = self._backdrop()
        s = bg.width()
        moving = self.backdrop_mode != "still"
        dx = math.sin(self._t / 13.0) * (s - w) * 0.45 if moving else 0.0
        dy = math.cos(self._t / 17.0) * (s - h) * 0.30 if moving else 0.0
        p.drawPixmap(QPointF((w - s) / 2 + dx, (h - s) / 2 + dy), bg)
        deep = self._pal["deep"]
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0, look.with_alpha(deep, 0.46))
        shade.setColorAt(.5, look.with_alpha(deep, 0.30))
        shade.setColorAt(1, look.with_alpha(deep, 0.66))
        p.fillRect(self.rect(), shade)
        look.paint_grain(p, QRectF(self.rect()), 0.6 * look.FULL_GRAIN)
        self._paint_pops(p)
        p.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)


class MusicTab(QWidget):
    SHOW_ONBOARDING = True   # the taste setup on first run
    RESUME_ON_START = True   # the last song back (and playing) when the app opens
    MINI_PLAYER = True       # the floating mini player while the app is out of sight
    FEED_STALE_S = 6 * 3600  # the home feed is built again after this
    BLEND_S = 5              # "Blend songs": seconds of crossfade between songs, unless set otherwise
    STALL_MS = 9000          # a stream that hasn't moved for this long is treated as dropped
    _part_sig = Signal(int, str, object, str)
    _stream_sig = Signal(int, str, str)
    _file_sig = Signal(int, str, str)
    _album_sig = Signal(object, object, str)
    _page_sig = Signal(int, str, object, str)
    _keep_sig = Signal(str, str, int, str)
    _library_sig = Signal(object)
    _lyrics_sig = Signal(str, object)
    _lyrics_sub_sig = Signal(int, object, str)
    _suggest_sig = Signal(int, str, object)
    _feed_sig = Signal(object)
    _warm_sig = Signal(str)
    _gain_sig = Signal(str, float)
    _found_sig = Signal(str, object)
    _upnext_sig = Signal(int, object)
    _explore_sig = Signal(int, str, object, str)
    _cover_sig = Signal(str, str)

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

        # FFmpeg decodes, Qt's audio sink plays: the equalizer can shape the sound (audio_engine)
        self.player = Engine(self)
        self.audio = self.player.audio
        self.eq = eq_core.settings((settings or {}).get("music_eq"))
        self.player.set_eq(self.eq)
        self.player.infoChanged.connect(lambda _info: self._refresh_info())
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
        self._lyrics_sub_sig.connect(self._on_lyrics_sub)
        self._lyr = None
        self._lyr_gen = 0
        self._suggest_sig.connect(self._on_suggestions)
        self._feed_sig.connect(self._on_feed)
        self._warm_sig.connect(self._on_warm)
        self._gain_sig.connect(self._on_gain)
        self._found_sig.connect(self._on_found)
        self._eq_wait = QTimer(self)
        self._eq_wait.setSingleShot(True)
        self._eq_wait.setInterval(180)
        self._eq_wait.timeout.connect(lambda: self.player.set_eq(self.eq))
        self._upnext_sig.connect(self._on_upnext)
        self._explore_sig.connect(self._on_explore)
        self._cover_sig.connect(self._on_cover)
        self._explore_gen = 0
        self._explore_chip = "All"
        self._moods = None
        self._covers_asked = set()
        self._cover_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="music-covers")
        self.player.advanced.connect(self._on_advanced)
        self.player.set_blend(int(float((settings or {}).get("music_blend_s", self.BLEND_S)) * 1000))
        self._armed = None          # (track id, from a file?) prepared to follow the song playing
        self._chosen = False        # the song starting was picked by hand (not by autoplay)
        self._shuffle_pick = None   # with shuffle on, the song chosen to come next
        self._upnext_gen = 0
        self._awaiting_upnext = False   # the queue ran out: the next songs play as soon as they're found
        self.taste = taste_core.Taste()
        self._listen = None
        self._last_pos = 0
        self._context = "browse"
        self._ended = 0
        self._feed_building = False

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
        self._sync_rail()
        # where you were: kept every few seconds and on quitting, brought back on opening
        self._last_saved = None
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(10000)
        self._save_timer.timeout.connect(self._save_last)
        self._save_timer.start()
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(self._save_last)
            QApplication.instance().aboutToQuit.connect(self.player.shutdown)
        self._resume_pos = 0
        # the mini player: floats above everything while the app's minimised or another tab's in front
        self.mini = None
        self._mini_dismissed = False
        self.player.positionChanged.connect(self._mini_position)
        self.player.playbackStateChanged.connect(lambda _s: self._sync_mini())
        QTimer.singleShot(0, self._watch_window)
        if self.RESUME_ON_START:
            QTimer.singleShot(700, self._resume_last)
        if self.SHOW_ONBOARDING and self.taste.needs_onboarding():
            QTimer.singleShot(400, self.open_taste)
        elif not self.taste.needs_onboarding() and (self.taste.feed_age() or 1e9) > self.FEED_STALE_S:
            self.refresh_feed()

    # ------------------------------------------------------------- layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, PANEL_GAP, PANEL_GAP, 0)
        row.setSpacing(0)
        self.rail = Rail()
        self.rail.navigate.connect(self._rail_go)
        self.rail.menu_requested.connect(self._settings_menu)
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
        for key in ("home", "explore", "search", "artist", "album", "songs", "artists", "albums", "local", "queue",
                    "mix", "playlist"):
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
        self.stage.corners = _Corners(self.stage)
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
        b.queue_btn.clicked.connect(lambda: self.open_side("queue"))
        b.eq_btn.clicked.connect(lambda: self.open_side("eq"))
        b.info_btn.clicked.connect(lambda: self.open_side("info"))
        b.art.clicked.connect(lambda: self.go("home"))
        b.back10_btn.clicked.connect(lambda: self.skip_by(-10))
        b.fwd10_btn.clicked.connect(lambda: self.skip_by(10))
        b.title.clicked.connect(self._song_menu)
        b.artist.clicked.connect(lambda _pos: self.open_artist_of(self.current()))
        b.add_btn.clicked.connect(lambda: self._playlist_menu(self.current(), QCursor.pos()))
        b.more_btn.clicked.connect(lambda: self._song_menu(QCursor.pos()))

        # the side panel: queue, equalizer, song details -- on frosted glass
        self.queue_panel = QueuePanel(self._panel_list)
        self.queue_panel.clear.connect(self.clear_queue)
        self.eq_panel = EqPanel(self.eq)
        self.eq_panel.changed.connect(self._eq_changed)
        self.info_panel = InfoPanel()
        self.side = SidePanel(self.stage, {"queue": self.queue_panel, "eq": self.eq_panel,
                                           "info": self.info_panel}, self)
        self.importer = ImportSheet(self)
        self.importer.imported.connect(self._imported)

        self.onboarding = Onboarding(self)
        self.onboarding.done.connect(self._taste_done)
        self.onboarding.skipped.connect(self._taste_skipped)
        self.onboarding.reset_requested.connect(self._taste_reset)

        self.full = _FullView(self._panel_list, self.eq, self)
        self.full.levels = self.player.levels
        self.full.backdrop_mode = (self.settings or {}).get("music_backdrop", "aurora")
        self.full.closed.connect(self.close_full)
        self.full.lyrics_toggled.connect(self._show_lyrics)
        self.full.lang_requested.connect(self._lyrics_menu)
        self.full.theme_requested.connect(self._theme_menu)
        self.full.lyrics.seek_requested.connect(lambda ms: self.player.setPosition(ms))
        self.full.play_btn.clicked.connect(self.toggle)
        self.full.next_btn.clicked.connect(self.next)
        self.full.prev_btn.clicked.connect(self.prev)
        self.full.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.full.seek.sliderReleased.connect(lambda: self._seek_release(self.full.seek))
        f = self.full
        f.back10_btn.clicked.connect(lambda: self.skip_by(-10))
        f.fwd10_btn.clicked.connect(lambda: self.skip_by(10))
        f.shuffle_btn.clicked.connect(self._toggle_shuffle)
        f.repeat_btn.clicked.connect(self._cycle_repeat)
        f.heart.clicked.connect(lambda: self.current() and self.toggle_saved(self.current()))
        f.more_btn.clicked.connect(lambda: self._song_menu(QCursor.pos()))
        f.title.clicked.connect(self._song_menu)
        f.artist.clicked.connect(lambda _pos: (self.close_full(), self.open_artist_of(self.current())))
        f.volume.valueChanged.connect(lambda v: self.bar.volume.value() != v and self.bar.volume.setValue(v))
        self.bar.volume.valueChanged.connect(lambda v: f.volume.value() != v and f.volume.setValue(v))
        f.vol_icon.clicked.connect(lambda: self.audio.setMuted(not self.audio.isMuted()) or self._sync_controls())
        f.fx.changed.connect(self._eq_changed)
        f.queue.clear.connect(self.clear_queue)
        for w in (f.queue_pill, f.fx_btn, f.info_btn):
            w.clicked.connect(self._refresh_panels)

        find = QShortcut(QKeySequence("Ctrl+F"), self)
        find.activated.connect(lambda: (self.rail.search.setFocus(), self.rail.search.selectAll()))
        QShortcut(QKeySequence("Alt+Left"), self).activated.connect(self.back)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.reload)
        self.apply_theme()

    # ---- the side panel, the equalizer, the song's details ----
    def _panel_list(self, **kw):
        return self._track_list(**kw)

    def open_side(self, key):
        self._refresh_panels()
        self.side.open(key)

    def _refresh_panels(self):
        cur = self.current()
        url = thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None
        cover = art().pixmap(url) if url else None
        for q in (self.queue_panel, self.full.queue):
            q.set_queue(self.queue, self.index)
        for i in (self.info_panel, self.full.info):
            i.set_track(cur, self.player.info, cover)
        for e in (self.eq_panel, self.full.fx):
            e.set_accent(self._np_pal["accent"])
        self._set_current_marks()

    def _refresh_info(self):
        # the song's quality, as soon as FFmpeg has read it: Lossless, Hi-Res, Dolby Atmos...
        self.bar.quality.set_info(self.player.info)
        self.full.card.quality.set_info(self.player.info)
        self.full._relayout() if self.full.isVisible() else None
        cur = self.current()
        url = thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None
        cover = art().pixmap(url) if url else None
        for i in (self.info_panel, self.full.info):
            i.set_track(cur, self.player.info, cover)

    def _eq_changed(self, s):
        self.eq = eq_core.settings(s)
        for panel in (self.eq_panel, self.full.fx):
            if panel.s != self.eq:
                panel.set_settings(self.eq)
        if self.settings is not None:
            self.settings["music_eq"] = dict(self.eq)
            try:
                settings_store.save_settings(self.settings)
            except Exception:   # noqa: BLE001 -- applied for now, saved next time
                logger.debug("Couldn't save the equalizer", exc_info=True)
        self._eq_wait.start()

    def skip_by(self, seconds):
        if self.current() is None:
            return
        dur = self.player.duration() or int(self.current().get("duration") or 0) * 1000
        pos = self.player.position() + int(seconds * 1000)
        self.player.setPosition(max(0, min(pos, max(0, dur - 1000)) if dur else max(0, pos)))

    def _measure(self, path, track):
        """The song's loudness, for "Level the volume" -- measured once."""
        if not path or not track or not os.path.exists(path):
            return
        known = music_sources.loudness_of(track)
        if known is not None:
            self.player.set_gain(track["id"], eq_core.normal_gain(known))
            return

        def work():
            lufs = loudness(path)
            if lufs is not None:
                music_sources.note_loudness(track, lufs)
                self._gain_sig.emit(track["id"], lufs)
        self._prefetcher.submit(work)

    def _want_cover(self, tracks):
        """The record's own cover for songs showing a video's picture --
        looked up in the background, and put in everywhere the song shows."""
        for t in tracks or ():
            if not t or t.get("kind", "track") != "track" or t.get("source") != "youtube":
                continue
            if music_sources.is_cover(t.get("artwork")) or t["id"] in self._covers_asked:
                continue
            self._covers_asked.add(t["id"])
            self._cover_pool.submit(self._find_cover, dict(t))

    def _find_cover(self, track):
        url = music_sources.original_art(track)
        if url:
            self._cover_sig.emit(track["id"], url)
        elif not music_sources.cover_settled(track["id"]):
            self._covers_asked.discard(track["id"])     # it failed (not "there's none"): ask again later

    def _on_cover(self, tid, url):
        for t in self.queue:
            if t.get("id") == tid:
                t["artwork"] = url
        for lst in self._live_lists():
            for r in lst.rows:
                if r.track.get("id") == tid:
                    r.track["artwork"] = url
                    art().want(thumb_url(url, 120), r._set_pix)
        cur = self.current()
        if cur and cur.get("id") == tid:
            self._show_track(cur)

    def _on_gain(self, tid, lufs):
        self.player.set_gain(tid, eq_core.normal_gain(lufs))

    # ---- a song's title and artist open things ----
    def _song_menu(self, pos=None):
        track = self.current()
        if not track:
            return
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Go to album", lambda: self.open_album_of(track))
        menu.addAction("Go to artist", lambda: self.open_artist_of(track))
        menu.addAction("Song info", lambda: self.open_side("info") if not self.full.isVisible()
                       else self.full.show_card(self.full.info_card, True))
        menu.addSeparator()
        liked = music_library.is_saved(track)
        menu.addAction("Remove from Liked songs" if liked else "Like", lambda: self.toggle_saved(track))
        menu.addAction("Add to a playlist…", lambda: self._playlist_menu(track, QCursor.pos()))
        if track.get("source") != "local":
            menu.addAction("Download", lambda: self.keep(track))
            menu.addAction("Copy link", lambda: QApplication.clipboard().setText(track.get("page_url", "")))
        menu.addAction("Start a radio from this song", lambda: self._radio_from(track))
        menu.addSeparator()
        menu.addAction("Not for me", lambda: (self._not_for_me(track), self.next()))
        menu.exec(pos or QCursor.pos())

    def open_artist_of(self, track):
        """The artist's page: straight there when the song knows it, else
        found by name."""
        if not track:
            return
        if self.full.isVisible():
            self.close_full()
        if track.get("artist_browse"):
            self.go("artist", {"browse_id": track["artist_browse"], "title": track.get("artist", "")})
            return
        name = (track.get("artist") or "").split(",")[0].strip()
        if not name:
            return
        self.status.setText("Finding %s..." % name)

        def work():
            from app.core import ytmusic
            try:
                found = ytmusic.search(name).get("artists") or []
            except Exception:   # noqa: BLE001
                found = []
            self._found_sig.emit("artist:" + name, found[:1])
        threading.Thread(target=work, daemon=True).start()

    def open_album_of(self, track):
        """The album the song is on: straight there when it's known, else
        found by its name and artist."""
        if not track:
            return
        if self.full.isVisible():
            self.close_full()
        if track.get("album_browse"):
            self.go("album", {"browse_id": track["album_browse"], "title": track.get("album", ""),
                              "source": "youtube", "kind": "album"})
            return
        if track.get("source") == "archive" and track.get("album_id"):
            self.go("album", dict(track, kind="album"))
            return
        q = " ".join(x for x in (track.get("album") or track.get("title"), track.get("artist", "")) if x)
        self.status.setText("Finding the album...")

        def work():
            from app.core import ytmusic
            try:
                found = ytmusic.search(q).get("albums") or []
            except Exception:   # noqa: BLE001
                found = []
            self._found_sig.emit("album:" + q, found[:1])
        threading.Thread(target=work, daemon=True).start()

    def _open_ytplaylist(self, item, play=False):
        """A chart or a mood's playlist: its songs fetched, then opened as a list
        (or played)."""
        from app.core import ytmusic
        self.status.setText("Opening %s..." % item["title"])

        def work():
            try:
                pl = ytmusic.playlist(item["browse_id"])
            except Exception as e:   # noqa: BLE001
                pl = {"error": music_sources.friendly(e)}
            self._found_sig.emit("ytplaylist", (item, pl, play))
        threading.Thread(target=work, daemon=True).start()

    def _on_found(self, what, items):
        if what == "ytplaylist":
            item, pl, play = items
            tracks = pl.get("tracks") or []
            if not tracks:
                self.status.setText("Couldn't open %s%s" % (item["title"], (": " + pl["error"]) if pl.get("error")
                                                            else " — it's empty."))
                return
            self.status.setText("")
            self._want_cover(tracks[:40])
            if play:
                self.play_from(tracks[0], tracks, context="mix")
                return
            self.go("mix", {"id": item["id"], "title": pl.get("title") or item["title"], "over": "Chart"
                            if "chart" in (item.get("subtitle") or "").lower() or "top" in item["title"].lower()
                            else "Playlist", "subtitle": item.get("subtitle") or "", "tracks": tracks})
            return
        kind, _, name = what.partition(":")
        if items:
            self.status.setText("")
            self.go(kind, items[0])
        else:
            self.status.setText("Couldn't find that %s — searching instead." % kind)
            self.run_search(name)

    def _radio_from(self, track):
        if not track or not str(track.get("id", "")).startswith("yt:"):
            return
        self.status.setText("Starting a radio from %s..." % track["title"])
        self.queue = self.queue[:self.index + 1]
        self._fill_upnext(force=True, chosen=True)

    # ---- playlists ----
    def _playlist_menu(self, track, pos):
        if not track:
            return
        menu = style_menu(QMenu(self), theme.tokens(True))
        liked = music_library.is_saved(track)
        act = menu.addAction("♥  Liked songs", lambda: self.toggle_saved(track))
        act.setCheckable(True)
        act.setChecked(liked)
        menu.addSeparator()
        for pl in music_library.playlists():
            has = any(t["id"] == track["id"] for t in pl["tracks"])
            a = menu.addAction(pl["title"], lambda p=pl: self._add_to_playlist(p, track))
            a.setCheckable(True)
            a.setChecked(has)
        menu.addSeparator()
        menu.addAction("New playlist…", lambda: self.new_playlist(track))
        menu.exec(pos)

    def _add_to_playlist(self, pl, track):
        if music_library.add_to_playlist(pl["id"], track):
            self.taste.log(track, "playlist_add")
            self.status.setText("Added to %s" % pl["title"])
        else:
            self.status.setText("Already in %s" % pl["title"])
        self._sync_rail()

    def new_playlist(self, track=None):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New playlist", "Name:", text="My playlist")
        if not ok or not name.strip():
            return None
        pl = music_library.create_playlist(name, [track] if track else [], source="",
                                           artwork=(track or {}).get("artwork"))
        self.status.setText("Made %s%s" % (pl["title"], " with %s" % track["title"] if track else ""))
        self._sync_rail()
        return pl

    def _remove_from_playlist(self, pid, track):
        music_library.remove_from_playlist(pid, track["id"])
        self._sync_rail()
        self.go("playlist", pid, push=False)

    def _imported(self, pl):
        self.status.setText("Imported %s: %d songs" % (pl["title"], len(pl["tracks"])))
        self._sync_rail()
        self.go("playlist", pl["id"])

    def _build_list_page(self, key, item):
        """A Daily Mix, Discover, a radio, or one of your playlists: its
        songs, under a header in its cover's colours."""
        page = self._pages[key]
        page.clear()
        if key == "playlist":
            pl = music_library.playlist(item) if isinstance(item, str) else item
            if not pl:
                page.hero.set_text("Playlist", "Not found", "")
                return
            tracks = pl["tracks"]
            page.hero.set_text("Playlist" + (" · from %s" % pl["source"] if pl.get("source") else ""),
                               pl["title"], " · ".join(x for x in (count(len(tracks), "song"),
                                                                    total_length(tracks)) if x))
        else:
            tracks = item.get("tracks") or []
            page.hero.set_text(item.get("over") or "Made for you", item["title"],
                               " · ".join(x for x in (item.get("subtitle"), count(len(tracks), "song"),
                                                       total_length(tracks)) if x))
        h = page.hero
        if tracks:
            h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_from(tracks[0], tracks,
                                                                                      context="mix"))
            h.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_list(tracks, shuffle=True)))
            h.add_action(self._ring("download", "Download all", lambda: [self.keep(t) for t in tracks]))
        if key == "mix" and tracks:
            h.add_action(Pill("Save as playlist", style="outline")).clicked.connect(
                lambda: (music_library.create_playlist(item["title"], tracks, "Made for you",
                                                       tracks[0].get("artwork")), self._sync_rail(),
                         self.status.setText("Saved %s to your playlists" % item["title"])))
        if key == "playlist" and isinstance(pl, dict):
            h.add_action(Pill("Rename", style="outline")).clicked.connect(lambda: self._rename_playlist(pl))
            h.add_action(Pill("Delete", style="outline")).clicked.connect(lambda: self._delete_playlist(pl))
        h.set_trio(tracks[:3], "In this list")
        lst = self._track_list()
        lst.set_tracks(tracks)
        page.add(lst)
        self._want_cover(tracks[:40])
        if not tracks:
            page.add(_Note("Nothing here yet — add songs with + or the ⋯ menu on any song."))
        first = next((t for t in tracks if t.get("artwork")), None)
        page.art_url = thumb_url(first["artwork"], 900) if first else None
        self._set_current_marks()

    def _rename_playlist(self, pl):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Rename playlist", "Name:", text=pl["title"])
        if ok and name.strip():
            music_library.rename_playlist(pl["id"], name)
            self._sync_rail()
            self.go("playlist", pl["id"], push=False)

    def _delete_playlist(self, pl):
        music_library.delete_playlist(pl["id"])
        self._sync_rail()
        self.status.setText("Deleted %s" % pl["title"])
        self.go("home")

    # ---- the rail: what's made for you, your playlists, the ⋯ menu ----
    def _feed_items(self):
        """The rail's "Made for you": the feed's mixes and lists, by key."""
        feed = self.taste.feed()
        items = {}
        for m in feed.get("mixes") or []:
            items[m["id"]] = dict(m, over="Daily Mix")
        if feed.get("discover"):
            items["discover"] = {"id": "discover", "title": "Discover for you", "over": "Made for you",
                                 "subtitle": "New artists your artists' fans play", "tracks": feed["discover"]}
        for i, b in enumerate((feed.get("because") or [])[:2]):
            items["because:%d" % i] = {"id": "because:%d" % i, "title": "Because you played %s" % b["title"],
                                       "over": "Made for you", "subtitle": "Songs like it", "tracks": b["tracks"]}
        if feed.get("picked"):
            items["picked"] = {"id": "picked", "title": "Picked for your taste", "over": "Made for you",
                               "subtitle": "From your languages and genres", "tracks": feed["picked"]}
        return items

    def _sync_rail(self):
        made = []
        for key, it in self._feed_items().items():
            art_url = next((t.get("artwork") for t in it["tracks"] if t.get("artwork")), None)
            sub = it.get("subtitle") if key.startswith("mix:") else it.get("subtitle", "")
            made.append((key, it["title"], sub, art_url))
        self.rail.set_made_for_you(made)
        self.rail.set_playlists([(p["id"], p["title"], "%s · %s" % (count(len(p["tracks"]), "song"),
                                                                    p.get("source") or "Playlist"),
                                  p.get("artwork") or next((t.get("artwork") for t in p["tracks"]
                                                            if t.get("artwork")), None))
                                 for p in music_library.playlists()])
        self.rail.set_accent(self.stage.pal["accent"])

    def _rail_go(self, key):
        if key == "taste":
            self.open_taste(editing=True)
        elif key == "import":
            self.importer.open()
        elif key == "newplaylist":
            pl = self.new_playlist()
            if pl:
                self.go("playlist", pl["id"])
        elif key.startswith("pl:"):
            self.go("playlist", key)
        elif key in self._feed_items():
            self.go("mix", self._feed_items()[key])
        else:
            self.go(key)

    def _settings_menu(self, pos):
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        menu.addAction("Import a playlist…", self.importer.open)
        menu.addAction("New playlist…", lambda: self._rail_go("newplaylist"))
        menu.addSeparator()
        menu.addAction("Your taste…", lambda: self.open_taste(editing=True))
        menu.addAction("Equalizer & effects…", lambda: self.open_side("eq"))
        auto = menu.addAction("Autoplay similar songs when the queue ends")
        auto.setCheckable(True)
        auto.setChecked(self.taste.state["prefs"].get("autoplay", True))
        auto.toggled.connect(lambda on: (self.taste.state["prefs"].__setitem__("autoplay", on), self.taste.save()))
        menu.addAction("Refresh my mixes", lambda: (self.refresh_feed(force=True),
                                                   self.status.setText("Making your mixes again...")))
        menu.addSeparator()
        menu.addAction("Music folder…", self._choose_folder)
        menu.addAction("Open the music folder", lambda: (os.makedirs(self.music_dir, exist_ok=True),
                                                        os.startfile(self.music_dir)))
        size = sum(os.path.getsize(os.path.join(music_sources.CACHE_DIR, f))
                   for f in (os.listdir(music_sources.CACHE_DIR) if os.path.isdir(music_sources.CACHE_DIR) else []))
        menu.addAction("Clear fetched songs (%d MB)" % (size // (1024 * 1024)), self._clear_cache)
        menu.exec(pos)

    # ---- the mini player ----
    def _watch_window(self):
        win = self.window()
        if win is not None and win is not self:
            win.installEventFilter(self)
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(lambda: self.mini is not None and self.mini.close())

    def _out_of_sight(self):
        """Minimised, or another tab in front. A closed window isn't out of
        sight -- it's gone, and the music goes with it."""
        win = self.window()
        if win is None or win is self:
            return False
        if win.windowState() & Qt.WindowState.WindowMinimized:
            return True
        return win.isVisible() and not self.isVisible()

    def _sync_mini(self):
        """Shows the mini player when a song's playing (or paused mid-song)
        and the app is out of sight; hides it when the Music tab is back."""
        if not self.MINI_PLAYER:
            return
        cur = self.current()
        # once a song has played this session it stays up -- through the gap while the next one loads too
        want = (cur is not None and getattr(self, "_played_once", False) and self._out_of_sight()
                and not self._mini_dismissed)
        if not want:
            if self.mini is not None and self.mini.isVisible():
                self.mini.hide_mini()
            return
        if self.mini is None:
            from .music_mini import MiniPlayer
            self.mini = MiniPlayer()
            m = self.mini
            m.play_btn.clicked.connect(self.toggle)
            m.next_btn.clicked.connect(self.next)
            m.prev_btn.clicked.connect(self.prev)
            m.back10_btn.clicked.connect(lambda: self.skip_by(-10))
            m.fwd10_btn.clicked.connect(lambda: self.skip_by(10))
            m.seek.sliderReleased.connect(lambda: self.player.setPosition(m.seek.value()))
            m.restore_requested.connect(self._restore_from_mini)
            m.closed_by_user.connect(lambda: setattr(self, "_mini_dismissed", True))
            m.like_requested.connect(lambda: self.current() and self.toggle_saved(self.current()))
        self._mini_track()
        self.mini.set_playing(self._playing())
        self.mini.show_mini()

    def _mini_track(self):
        cur = self.current()
        if self.mini is None or cur is None:
            return
        url = thumb_url(cur["artwork"], 900) if cur.get("artwork") else None
        pm = art().pixmap(url) if url else None
        img = art().image(url) if url else None
        self.mini.set_track(cur["title"], cur.get("artist") or "", pm, self._np_pal,
                            frost_strip(img) if img is not None else None)
        self.mini.set_liked(music_library.is_saved(cur))
        self.mini.set_position(self.player.position(), self.player.duration())

    def _mini_position(self, ms):
        if self.mini is not None and self.mini.isVisible():
            self.mini.set_position(ms, self.player.duration())

    def _restore_from_mini(self):
        win = self.window()
        if win is not None:
            if win.windowState() & Qt.WindowState.WindowMinimized:
                win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
            win.showNormal() if win.isMinimized() else win.show()
            win.raise_()
            win.activateWindow()
            tabs = getattr(win, "tabs", None)
            if tabs is not None:
                tabs.setCurrentWidget(self)
        if self.mini is not None:
            self.mini.hide_mini()

    def showEvent(self, event):
        super().showEvent(event)
        self._mini_dismissed = False        # back on the Music tab: it may come again next time
        self._sync_mini()

    def hideEvent(self, event):
        super().hideEvent(event)
        QTimer.singleShot(0, self._sync_mini)

    # ---- where you left off ----
    def _save_last(self):
        try:
            self._save_last_now()
        except Exception:   # noqa: BLE001 -- a timer's slot: say so, where it can be seen
            logger.warning("Couldn't keep the song you were playing", exc_info=True)

    def _save_last_now(self):
        cur = self.current()
        if cur is None or cur.get("kind", "track") != "track":
            return
        lo = max(0, self.index - 20)
        queue = []
        for t in self.queue[lo:self.index + 60]:
            slim = taste_core._slim(t)
            if t.get("_auto"):
                slim["_auto"] = True
            queue.append(slim)
        state = {"queue": queue, "index": self.index - lo, "pos": int(self.player.position() if self._started
                                                                     else self._resume_pos),
                 "shuffle": self.shuffle, "repeat": self.repeat, "context": self._context}
        if state == self._last_saved:
            return
        if ui_state.save_music(state):
            if self._last_saved is None:
                logger.info("Keeping the song playing for next time (%s)", os.path.basename(ui_state.MUSIC_PATH))
            self._last_saved = state

    def _resume_last(self):
        """The song you were playing (and the queue around it) back where it
        was, paused -- unless that's turned off in Settings."""
        if not (self.settings or {}).get("music_resume_play", True):
            return
        st = ui_state.load_music() or (self.settings or {}).get("music_last") or {}
        if self.current() is not None or not st.get("queue"):
            logger.info("Nothing to bring back in the Music tab" if not st.get("queue") else
                        "A song's already playing; not bringing back the last one")
            return
        queue = [dict(t, kind=t.get("kind", "track")) for t in st["queue"] if t.get("id")]
        i = int(st.get("index", 0))
        if not 0 <= i < len(queue):
            return
        if queue[i].get("source") == "local" and not os.path.exists(queue[i].get("stream") or ""):
            return
        self.queue, self.index = queue, i
        self.shuffle, self.repeat = bool(st.get("shuffle")), st.get("repeat", "off")
        self._context = st.get("context", "browse")
        self._resume_pos = int(st.get("pos") or 0)
        self._sync_controls()
        self._render_queue()
        # back, and paused where it was -- never playing on its own when the app opens; play carries on
        self._show_track(self.current())
        if self._resume_pos:
            self.bar.pos_label.setText(format_eta(self._resume_pos // 1000))
            self.full.pos_label.setText(self.bar.pos_label.text())
        if self.pages.currentWidget() is self._pages["home"]:
            self._build_home()

    # ---- settings ----
    def open_settings(self):
        from .music_settings import MusicSettings
        old = getattr(self, "_settings_sheet", None)
        if old is not None and _alive(old):
            old.hide()
            old.deleteLater()
        self._settings_sheet = MusicSettings(self)
        self._settings_sheet.open()

    def set_pref(self, key, value):
        if self.settings is not None:
            self.settings[key] = value
            try:
                settings_store.save_settings(self.settings)
            except Exception:   # noqa: BLE001
                logger.debug("Couldn't save a music setting", exc_info=True)

    def _theme_menu(self, pos):
        from .music_backdrop import MODES
        menu = style_menu(QMenu(self), theme.tokens(True))
        head = menu.addAction("BACKGROUND")
        head.setEnabled(False)
        for key, name in MODES:
            a = menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.full.backdrop_mode == key)
            a.triggered.connect(lambda _c=False, k=key: self.set_backdrop(k))
        menu.exec(pos)

    def set_backdrop(self, mode):
        """The full-screen player's background: "aurora", "nebula" or "still"."""
        self.set_pref("music_backdrop", mode)
        self.full.backdrop_mode = mode

    def set_blend(self, seconds):
        self.set_pref("music_blend_s", int(seconds))
        self.player.set_blend(int(seconds) * 1000)
        self._arm_next()

    def set_autoplay(self, on):
        self.taste.state["prefs"]["autoplay"] = bool(on)
        self.taste.save()
        if on:
            self._fill_upnext()

    def set_normalize(self, on):
        self._eq_changed(dict(self.eq, normalize=bool(on)))
        self.player._update_gain()

    def _clear_cache(self):
        import shutil
        shutil.rmtree(music_sources.CACHE_DIR, ignore_errors=True)
        self.status.setText("Fetched songs cleared — they'll be fetched again when played.")

    def _mine(self, q):
        """Songs of your own (liked, recent, in your playlists) close to `q` --
        forgiving of a letter or two out of place."""
        import difflib
        ql = q.lower().strip()
        pool, seen = [], set()
        for t in music_library.saved("songs") + music_library.recent() + [
                x for p in music_library.playlists() for x in p["tracks"]]:
            if t.get("id") not in seen and t.get("kind", "track") == "track":
                seen.add(t["id"])
                pool.append(t)
        scored = []
        for t in pool:
            hay = ("%s %s" % (t.get("title", ""), t.get("artist", ""))).lower()
            words = ql.split()
            hits = sum(1 for w in words if any(difflib.SequenceMatcher(None, w, h).ratio() > 0.75
                                               for h in hay.split()))
            score = hits / max(1, len(words))
            if score >= 0.6:
                scored.append((score, t))
        scored.sort(key=lambda x: -x[0])
        return [t for _s, t in scored[:4]]

    def _fade_in(self, page):
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(page)
        eff.setOpacity(0.0)
        page.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", page)
        anim.setDuration(170)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(lambda: _alive(page) and page.setGraphicsEffect(None))
        anim.start()

    # ---- your taste, and the feed made from it ----
    def open_taste(self, editing=False):
        self.onboarding.open(self.taste.state.get("seeds"), editing=editing)

    def _taste_done(self, languages, genres, artists):
        self.taste.set_seeds(languages, genres, artists)
        self.status.setText("Making your mixes...")
        self.refresh_feed(force=True)
        self.go("home")

    def _taste_skipped(self):
        self.onboarding.hide()
        if self.taste.needs_onboarding():
            self.taste.skip_onboarding()

    def _taste_reset(self):
        self.taste.reset()
        self.onboarding.open({}, editing=False)
        self.status.setText("Your taste was reset — pick again, or skip.")

    def refresh_feed(self, force=False):
        """The home feed built again in the background (a moment), shown when ready."""
        if self._feed_building or self.taste.needs_onboarding():
            return
        self._feed_building = True

        def work():
            try:
                feed = self.taste.build_feed()
            except Exception:   # noqa: BLE001 -- the old feed stays
                logger.exception("Couldn't build the music feed")
                feed = None
            self._feed_sig.emit(feed)
        threading.Thread(target=work, daemon=True).start()

    def _on_feed(self, feed):
        self._feed_building = False
        self._sync_rail()
        if self.pages.currentWidget() is self._pages["home"]:
            self._build_home()
            self._paint_page(self._pages["home"])

    def _add_feed(self, page):
        """The suggestions on the home page: Daily Mixes, Discover, Because
        you played X, New from your artists, Picked for your taste."""
        feed = self.taste.feed()
        if feed.get("mixes"):
            grid = self._tile_grid("album", rows=1)
            grid.set_items(feed["mixes"])
            page.add(grid, "Your Daily Mixes")
        if feed.get("discover"):
            lst = self._track_list(limit=6)
            lst.set_tracks(feed["discover"])
            page.add(lst, "Discover for you")
        for b in (feed.get("because") or [])[:2]:
            lst = self._track_list(limit=5)
            lst.set_tracks(b["tracks"])
            page.add(lst, "Because you played %s" % b["title"])
        if feed.get("new"):
            grid = self._tile_grid("album", rows=1)
            grid.set_items(feed["new"])
            page.add(grid, "New from your artists", "See all")
        if feed.get("picked"):
            lst = self._track_list(limit=6)
            lst.set_tracks(feed["picked"])
            page.add(lst, "Picked for your taste")

    # ---- what you listen to, told to the taste model ----
    def _open_listen(self, track):
        self._end_listen()
        self._listen = track
        self._last_pos = 0
        self.taste.log(track, "search_play" if self._context == "search" and self._chosen else "play")
        self.taste.started(track, chosen=self._chosen)

    def _end_listen(self, natural=False):
        track, self._listen = self._listen, None
        if not track:
            return
        dur = (self.player.duration() or int(track.get("duration") or 0) * 1000) / 1000.0
        self.taste.listen_ended(track, self._last_pos / 1000.0, dur, natural)
        self._ended += 1
        if self._ended % 6 == 0 and (self.taste.feed_age() or 1e9) > 600:
            self.refresh_feed()

    # ---- up next: the song's radio, ordered by the session (app/core/upnext.py) ----
    def _fill_upnext(self, force=False, chosen=False):
        """Songs to follow the one playing, worked out again as each song
        starts -- so they follow what you're listening to (and skipping)."""
        cur = self.current()
        if cur is None or cur.get("source") != "youtube" or not str(cur.get("id", "")).startswith("yt:"):
            return
        if not force and not self.taste.state["prefs"].get("autoplay", True):
            return
        chosen_list = [t for t in self.queue[self.index + 1:] if not t.get("_auto")]
        if chosen_list and not force:
            return              # a list you chose plays first; autoplay follows it
        self._upnext_gen += 1
        gen, token = self._upnext_gen, self._token
        exclude = [t["id"] for t in self.queue[:self.index + 1]]

        def work():
            try:
                tracks = self.taste.autoplay(cur, exclude=exclude, n=15, chosen=chosen)
            except Exception:   # noqa: BLE001
                logger.info("Up next found nothing", exc_info=True)
                tracks = []
            self._upnext_sig.emit(gen, (token, tracks))
        threading.Thread(target=work, daemon=True).start()

    def _on_upnext(self, gen, got):
        token, tracks = got
        if gen != self._upnext_gen or token != self._token:
            return
        if not tracks:
            if self._awaiting_upnext:
                self._awaiting_upnext = False
                self.status.setText("")
            return
        after = self.queue[self.index + 1:]
        chosen_list = [t for t in after if not t.get("_auto")]
        keep = []
        # the next song stays if it's already prepared to follow (gapless / blended)
        if after and after[0].get("_auto") and self._armed and self._armed[0] == after[0]["id"]:
            keep = [after[0]]
        have = {t["id"] for t in self.queue[:self.index + 1] + chosen_list + keep}
        new = [dict(t, _auto=True) for t in tracks if t["id"] not in have]
        self.queue = self.queue[:self.index + 1] + chosen_list + keep + new
        self._render_queue()
        self._prefetch()
        self._arm_next()
        if self._awaiting_upnext and self.index + 1 < len(self.queue):
            # the queue had run out and was waiting for these (not a song being recovered)
            self._awaiting_upnext = False
            self.status.setText("")
            self.next(auto=True)

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
        elif obj is self.window() and event.type() == QEvent.Type.WindowStateChange:
            # minimised: the mini player takes over; restored: it steps aside
            if not (obj.windowState() & Qt.WindowState.WindowMinimized) and self.isVisible():
                self._mini_dismissed = False
            QTimer.singleShot(0, self._sync_mini)
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
        elif key in ("mix", "playlist"):
            self._build_list_page(key, item)
        elif key == "explore":
            self._build_explore(item if isinstance(item, str) else None)
        if self.pages.currentWidget() is not page:
            self._fade_in(page)
        self.pages.setCurrentWidget(page)
        self.rail.select((item if isinstance(item, str) else item.get("id")) if key in ("mix", "playlist")
                         and item else key)
        self.back_btn.setVisible(bool(self._history))
        self.reload_btn.setVisible(key in ("search", "artist", "album", "local", "home", "songs", "artists",
                                           "albums", "queue", "explore"))
        self._corner.raise_()
        self.stage.corners.raise_()
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
        url = self._page_art(page)
        if not url:
            self._set_page_palette(page, look.DEFAULT)
            page.hero.set_art(None)
            return
        pal = art().palette(url)
        if pal is not None:
            self._set_page_palette(page, pal)
        wide = page.wide_art if url == page.art_url else False

        def got(pm, page=page, url=url, wide=wide):
            if not _alive(page):
                return
            if self._page_art(page) != url:
                return
            page.hero.set_art(pm, wide)
            if self.pages.currentWidget() is page:
                self._set_page_palette(page, art().palette(url) or look.DEFAULT)
        art().want(url, got, first=True)

    def _page_art(self, page):
        """The picture a page shows: the song playing, as it changes -- but an
        artist's page keeps the artist's photo."""
        np = self._np_art_url()
        if np and page.key != "artist":
            return np
        return page.art_url or np

    def _np_art_url(self):
        cur = self.current()
        return thumb_url(cur["artwork"], 900) if cur and cur.get("artwork") else None

    def _set_page_palette(self, page, pal):
        page.hero.set_palette(pal)
        if self.pages.currentWidget() is page or self.pages.currentWidget() is None:
            self.stage.set_palette(pal)
            self.rail.set_accent(pal["accent"])
            self.rail.set_palette(pal)
            self.stage.corners.update()
            self.update()
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
        row.play.connect(lambda t, r=row: self.play_from(
            t, self._context_of(r), context="search" if self.pages.currentWidget() is self._pages["search"]
            else "browse"))
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
        if item.get("kind") == "ytplaylist":
            self._open_ytplaylist(item, play)
            return
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
        elif kind == "mix":
            self.play_item(item)
        elif kind == "ytplaylist":
            self._open_ytplaylist(item)
        else:
            self.play_from(item, [item])

    def play_item(self, item):
        """A tile's play button: an album plays from its first song."""
        if item.get("kind") == "album":
            self.queue, self.index = [item], 0
            self._play_current()
        elif item.get("kind") == "mix" and item.get("tracks"):
            self._context = "mix"
            self.play_from(item["tracks"][0], item["tracks"], context="mix")
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
            h.add_action(self._add_ring(cur))
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
        elif self.taste.feed().get("mixes"):
            mix = self.taste.feed()["mixes"][0]
            h.set_text("Made for you", mix["title"], "%s · %d songs" % (mix["subtitle"], len(mix["tracks"])))
            h.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_item(mix))
            h.add_action(self._ring("shuffle", "Shuffle", lambda: self._play_list(mix["tracks"], shuffle=True)))
            h.add_action(Pill("Your taste", style="outline")).clicked.connect(lambda: self.open_taste(editing=True))
            h.set_trio(mix["tracks"][:3], "In this mix")
            page.art_url = thumb_url(mix.get("artwork"), 900) if mix.get("artwork") else None
        else:
            h.set_text("Music", "Play anything",
                       "Songs, albums and artists from YouTube Music, and free music from open libraries — "
                       "no account, nothing to sign in to.")
            h.add_action(Pill("Search", "search")).clicked.connect(
                lambda: (self.rail.search.setFocus(), self.rail.search.selectAll()))
            h.add_action(Pill("Your taste", style="outline")).clicked.connect(lambda: self.open_taste(editing=True))
        self._add_feed(page)
        recent = [t for t in music_library.recent() if t.get("kind", "track") == "track"]
        if recent:
            grid = self._tile_grid("track", rows=1)
            grid.set_items(recent[:16])
            page.add(grid, "Recently played", "See all")
        if not self.taste.feed().get("mixes"):
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
        """Like (a song) / save (an album or artist): a heart that fills."""
        saved = music_library.is_saved(item)
        b = RingButton("heart_filled" if saved else "heart", "Liked — click to remove" if saved
                       else ("Like" if item.get("kind", "track") == "track" else "Save to Your music"))
        b.tint = "#ff4d6d" if saved else None

        def flip():
            on = self.toggle_saved(item)
            b.set_kind("heart_filled" if on else "heart")
            b.tint = "#ff4d6d" if on else None
            b.set_tip("Liked — click to remove" if on else "Like")
            b.update()
        b.clicked.connect(flip)
        return b

    def _add_ring(self, track):
        b = RingButton("plus", "Add to a playlist")
        b.clicked.connect(lambda: self._playlist_menu(track, QCursor.pos()))
        return b

    # ---- explore ----
    EXPLORE_CHIPS = ("All", "Bollywood", "Indian pop", "Indian indie", "Punjabi", "Desi hip-hop", "Romance", "Sad",
                     "Party", "Chill", "Feel good", "Workout", "Focus", "Devotional", "Ghazal/sufi",
                     "Hindustani classical", "Tamil", "Telugu", "Marathi", "Bengali", "Pop", "Hip-hop", "K-Pop",
                     "Indie & alternative", "Dance & electronic", "Rock", "R&B & soul", "Jazz", "Latin")
    CHIP_CATEGORY = {"Bollywood": "Hindi"}

    @staticmethod
    def _region():
        from PySide6.QtCore import QLocale
        try:
            code = QLocale.territoryToCode(QLocale.system().territory())
        except Exception:   # noqa: BLE001
            code = ""
        return code if code and len(code) == 2 and code != "ZZ" else "IN"

    def _build_explore(self, chip=None):
        page = self._pages["explore"]
        page.clear()
        page.art_url = None
        chip = chip or self._explore_chip or "All"
        self._explore_chip = chip
        everything = chip == "All"
        page.hero.set_text("Explore", "Charts & trending" if everything else chip,
                           "What everyone's playing now — the charts, new releases, and every mood and genre."
                           if everything else "The best of %s on YouTube Music, as it is this week." % chip)
        bar = _ChipBar(self.EXPLORE_CHIPS, chip)
        bar.chosen.connect(lambda name: self.go("explore", name))
        page.add(bar)
        self._explore_note = _Note("Loading…")
        page.add(self._explore_note)
        self._explore_gen += 1
        gen, region = self._explore_gen, self._region()

        def work():
            from app.core import ytmusic
            data, err = {}, ""
            try:
                if everything:
                    shelves = ytmusic.charts(region)
                    lists = [x for sh in shelves for x in sh["items"] if x.get("kind") == "ytplaylist"]
                    if lists:
                        top = ytmusic.playlist(lists[0]["browse_id"])
                        data["trending"] = top["tracks"]
                        data["trending_title"] = top.get("title") or lists[0]["title"]
                    data["shelves"] = shelves
                    try:
                        data["new"] = ytmusic.new_releases()[:24]
                    except Exception:   # noqa: BLE001 -- the charts alone will do
                        data["new"] = []
                else:
                    if self._moods is None:
                        self._moods = ytmusic.moods()
                    name = self.CHIP_CATEGORY.get(chip, chip)
                    ep = self._moods.get(name)
                    if not ep:
                        raise music_sources.MusicError("YouTube Music has no %s page here." % chip)
                    data["shelves"] = ytmusic.category(ep)
            except Exception as e:   # noqa: BLE001
                err = music_sources.friendly(e)
            self._explore_sig.emit(gen, chip, data, err)
        threading.Thread(target=work, daemon=True).start()

    def _on_explore(self, gen, chip, data, err):
        if gen != self._explore_gen:
            return
        page = self._pages["explore"]
        if _alive(self._explore_note):
            self._explore_note.hide()
        if err:
            page.add(_Note("Couldn't load this right now: %s — press F5 to try again." % err))
            return
        trending = data.get("trending") or []
        if trending:
            lst = self._track_list(limit=10)
            lst.set_tracks(trending)
            page.add(lst, data.get("trending_title") or "Trending now", gap=10)
            page.hero.set_trio(trending[:3], "Trending")
            self._want_cover(trending)
            first = next((t for t in trending if music_sources.is_cover(t.get("artwork"))), None)
            if first:
                page.art_url = thumb_url(first["artwork"], 900)
        for sh in data.get("shelves") or []:
            items = sh["items"]
            songs = [x for x in items if x.get("kind", "track") == "track"]
            if songs and len(songs) == len(items):
                lst = self._track_list(limit=8)
                lst.set_tracks(songs)
                page.add(lst, sh["title"] or "Songs")
                self._want_cover(songs[:16])
                if page.art_url is None and songs[0].get("artwork"):
                    page.art_url = thumb_url(songs[0]["artwork"], 900)
                continue
            mode = "artist" if all(x.get("kind") == "artist" for x in items) else "album"
            grid = self._tile_grid(mode, rows=1)
            grid.set_items(items)
            page.add(grid, sh["title"] or chip, "See all")
            if page.art_url is None and mode == "album" and items[0].get("artwork"):
                page.art_url = thumb_url(items[0]["artwork"], 900)
        if data.get("new"):
            grid = self._tile_grid("album", rows=1)
            grid.set_items(data["new"])
            page.add(grid, "New releases", "See all")
        if self.pages.currentWidget() is page:
            self._paint_page(page)
        self._set_current_marks()

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
        self._free_list = self._track_list(limit=4)
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
        mine = self._mine(q)
        if mine:
            lst = self._track_list(limit=4)
            lst.set_tracks(mine)
            page.sec.insertWidget(0, lst)
            page.sec.insertWidget(0, SectionHeader("From your music"))
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
        if (self.side.isVisible() and self.side.current == "queue") or self.full.is_open(self.full.queue_card):
            self._refresh_panels()
        self.rail.items["queue"].set_count(len(self.queue))

    def _shuffle_queue(self):
        cur = self.current()
        rest = [t for i, t in enumerate(self.queue) if i != self.index]
        random.shuffle(rest)
        self.queue = ([cur] if cur else []) + rest
        self.index = 0 if cur else -1
        self._render_queue()
        self._arm_next()

    def clear_queue(self):
        cur = self.current()
        self.queue = [cur] if cur else []
        self.index = 0 if cur else -1
        self._render_queue()
        self._arm_next()

    # ---- the row menu ----
    def _row_menu(self, track, pos):
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Play next", lambda: self.play_next(track))
        menu.addAction("Add to the queue", lambda: self.add_to_queue(track))
        menu.addAction("Remove from Your music" if music_library.is_saved(track) else "Save to Your music",
                       lambda: self.toggle_saved(track))
        if track.get("source") != "local":
            menu.addAction("Download", lambda: self.keep(track))
        menu.addAction("Not for me", lambda: self._not_for_me(track))
        if track.get("artist"):
            menu.addAction("Don't suggest %s" % track["artist"].split(",")[0], lambda: self._block_artist(track))
        menu.addAction("Add to a playlist…", lambda: self._playlist_menu(track, QCursor.pos()))
        here = self._here or (None, None)
        if here[0] == "playlist" and here[1]:
            menu.addAction("Remove from this playlist", lambda: self._remove_from_playlist(here[1], track))
        if track.get("reason"):
            menu.addAction("Why: %s" % track["reason"]).setEnabled(False)
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

    def _not_for_me(self, track):
        self.taste.dislike(track)
        for lst in self._live_lists():
            for r in list(lst.rows):
                if r.track["id"] == track["id"] and not lst.in_queue:
                    r.hide()
        self.status.setText("Got it — you'll hear less like %s." % track["title"])

    def _block_artist(self, track):
        self.taste.block_artist(track)
        self.status.setText("%s won't be suggested again." % track["artist"].split(",")[0])

    def toggle_saved(self, item):
        on = music_library.toggle(item)
        self._just_liked = on
        if on:
            self.taste.log(item, "like")
        where = "Liked songs" if item.get("kind", "track") == "track" else "Your music"
        self.status.setText(("Added to %s: %s" if on else "Removed from %s: %s") % (where, item["title"]))
        self._sync_saved()
        if self._here and self._here[0] in ("songs", "artists", "albums"):
            self._build_saved(self._here[0])
        return on

    def _sync_saved(self):
        cur = self.current()
        on = bool(cur) and music_library.is_saved(cur)
        self.bar.save_btn.set_liked(on, animate=getattr(self, "_just_liked", False))
        self.full.heart.set_liked(on, animate=getattr(self, "_just_liked", False))
        if getattr(self, "mini", None) is not None:
            self.mini.set_liked(on)
        self._just_liked = False
        self.rail.items["songs"].set_count(len(music_library.saved("songs")) or "")
        self.rail.items["queue"].set_count(len(self.queue) or "")

    # ------------------------------------------------------------- queue
    def current(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def play_from(self, track, tracks=None, context="browse"):
        """Plays `track`; the rest of the list it's in follows as the queue --
        an album's, a playlist's, a mix's. A song picked from search results
        plays on its own, and its radio follows (up next)."""
        self._context = context
        if context in ("search", "single"):
            tracks = [track]
        tracks = list(tracks) if tracks else [track]
        if not any(t["id"] == track["id"] for t in tracks):
            tracks = [track]
        self.queue = tracks
        self.index = next(i for i, t in enumerate(tracks) if t["id"] == track["id"])
        self._chosen = True
        self._render_queue()
        self._play_current()

    def play_next(self, track):
        if self.index < 0:
            self.play_from(track)
            return
        self.queue.insert(self.index + 1, track)
        self.status.setText("Plays next: %s" % track["title"])
        self._render_queue()
        self._arm_next()

    def add_to_queue(self, track, quiet=False):
        if any(t["id"] == track["id"] for t in self.queue):
            if not quiet:
                self.status.setText("Already in the queue.")
            return
        self.queue.append(track)
        if not quiet:
            self.status.setText("Added to the queue: %s" % track["title"])
            self.taste.log(track, "playlist_add")
        if self.index < 0:
            self.index = len(self.queue) - 1
            self._play_current()
        self._render_queue()
        self._arm_next()

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
        self._arm_next()

    # ------------------------------------------------------------- playing
    def _playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _play_current(self):
        track = self.current()
        if track is None:
            return
        self._awaiting_upnext = False
        self._end_listen()
        self._token += 1
        token = self._token
        self._reset_playback()
        # the song you chose takes over at once: the last one doesn't play on while this one loads
        if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.player.stop()
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
            # Streamed the moment its link is known (asked for ahead for the
            # next songs and the first search results, so usually at once),
            # and fetched whole meanwhile: if the stream breaks off, the song
            # carries on from the file, where it was.
            url = music_sources.known_stream(track)
            if url:
                self._start_stream(url)
            else:
                self.status.setText("")
                threading.Thread(target=self._fetch_stream, args=(token, track), daemon=True).start()
            threading.Thread(target=self._fetch_file, args=(token, track), daemon=True).start()
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
        self._shuffle_pick = None

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
        self._measure(path, self.current())
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
            else:
                self.status.setText("Getting the song...")
            return
        if os.path.exists(url):
            self._start_file(url)
        else:
            self._start_stream(url)

    def _start_file(self, path, at=None):
        self._local = True
        self._resume_at = None
        self._file = path
        self._measure(path, self.current())
        self._begin(QUrl.fromLocalFile(path), at)
        self._prefetch()

    def _start_stream(self, url, at=None):
        self._local = False
        self._begin(QUrl(url), at)
        self._prefetch()

    def _begin(self, qurl, at=None):
        first = not self._started
        track = self.current()
        if first and track:
            self._open_listen(track)
            QTimer.singleShot(1500, self._save_last)    # a new song: kept for next time straight away
        self._started = True
        self._played_once = True
        self._pending_seek = None
        self._last_move = time.monotonic()
        self.player.setSource(qurl, key=track["id"] if track else None,
                              duration_ms=int((track or {}).get("duration") or 0) * 1000)
        if first and not at and self._resume_pos:
            at, self._resume_pos = self._resume_pos, 0
        if at:
            self.player.setPosition(at)
        self.player.play()
        if track and first:
            self.status.setText("")
            try:
                music_library.add_recent(track)
            except Exception:   # noqa: BLE001 -- the list of recent songs is a nicety
                logger.debug("Couldn't note a recent song", exc_info=True)
            chosen, self._chosen = self._chosen, False
            self._fill_upnext(chosen=chosen)
        self._arm_next()

    # ---- the next song, prepared before this one ends (gapless, or blended in) ----
    def _next_index(self):
        if not self.queue or self.index < 0:
            return None
        if self.shuffle and len(self.queue) > 1:
            if self._shuffle_pick is None or not (0 <= self._shuffle_pick < len(self.queue)) or \
                    self._shuffle_pick == self.index:
                self._shuffle_pick = random.choice([i for i in range(len(self.queue)) if i != self.index])
            return self._shuffle_pick
        if self.index + 1 < len(self.queue):
            return self.index + 1
        if self.repeat == "all":
            return 0
        return None

    def _blend_ok(self, cur, nxt):
        """No blend between an album's songs played in order -- they're
        meant to run into each other as they are."""
        if not cur or not nxt:
            return False
        same_album = cur.get("album_browse") and cur.get("album_browse") == nxt.get("album_browse")
        return not (same_album and self._context != "search")

    def _arm_next(self):
        """Tells the player what follows the song playing, so it can be
        decoded before this one ends."""
        if not self._started or self.current() is None:
            return
        cur = self.current()
        if self.repeat == "one":
            track = cur
        else:
            i = self._next_index()
            track = self.queue[i] if i is not None else None
        if track is None or track.get("kind", "track") != "track" or (track.get("album_id") and
                                                                      not track.get("stream")):
            self.player.set_next(None)
            self._armed = None
            return
        src, local = None, False
        if track["source"] == "local":
            src, local = QUrl.fromLocalFile(track["stream"]), True
        elif track["source"] == "youtube":
            path = music_sources.cached(track)
            if path:
                src, local = QUrl.fromLocalFile(path), True
            elif track is cur and self._file:
                src, local = QUrl.fromLocalFile(self._file), True
            else:
                url = music_sources.known_stream(track)
                if url:
                    src = QUrl(url)
                else:
                    music_sources.warm([track], done=lambda t: self._warm_sig.emit(t["id"]))
        elif track.get("stream"):
            src = QUrl(track["stream"])
        if src is None:
            self.player.set_next(None)
            self._armed = None
            return
        self.player.set_next(src, key=track["id"], duration_ms=int(track.get("duration") or 0) * 1000,
                             blend=self._blend_ok(cur, track) and self.repeat != "one")
        self._armed = (track["id"], local, src.toLocalFile() if local else None)
        known = music_sources.loudness_of(track)
        if known is not None:
            self.player.set_gain(track["id"], eq_core.normal_gain(known))

    def _on_warm(self, tid):
        i = self._next_index()
        if i is not None and 0 <= i < len(self.queue) and self.queue[i]["id"] == tid and \
                (self._armed is None or self._armed[0] != tid):
            self._arm_next()

    def _on_advanced(self, key):
        """The player moved on to the prepared song by itself (gapless or
        blended): the queue, the screen and the session follow."""
        cur = self.current()
        if cur is not None and cur["id"] == key and self.repeat == "one":
            self._end_listen(natural=True)
            self.taste.log(cur, "repeat")
            self._open_listen(cur)
            return
        i = self._next_index()
        if i is None or not (0 <= i < len(self.queue)) or self.queue[i]["id"] != key:
            i = next((k for k, t in enumerate(self.queue) if t["id"] == key and k != self.index), None)
        if i is None:
            return
        armed = self._armed or (None, False, None)
        self._end_listen(natural=True)
        self.index = i
        self._token += 1
        self._reset_playback()
        self._started = True
        self._played_once = True
        QTimer.singleShot(1500, self._save_last)
        self._local = bool(armed[1])
        self._file = armed[2]
        self._armed = None
        track = self.current()
        self._show_track(track)
        self._chosen = False
        self._open_listen(track)
        try:
            music_library.add_recent(track)
        except Exception:   # noqa: BLE001
            logger.debug("Couldn't note a recent song", exc_info=True)
        if track.get("source") == "youtube" and not self._file:
            threading.Thread(target=self._fetch_file, args=(self._token, track), daemon=True).start()
        self._render_queue()
        self._prefetch()
        self._arm_next()
        self._fill_upnext()

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
        """The next songs' links asked for, and the next two fetched whole,
        while this one plays: they start at once."""
        upcoming = self.queue[self.index + 1:self.index + 4]
        music_sources.warm(upcoming, done=lambda t: self._warm_sig.emit(t["id"]))
        for t in upcoming[:2]:
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
        self._want_cover([track] + self.queue[self.index + 1:self.index + 6])
        if self.mini is not None and self.mini.isVisible():
            QTimer.singleShot(0, self._mini_track)
        self.bar.quality.set_info(self.player.info if self._started else {})
        self.full.card.quality.set_info(self.player.info if self._started else {})
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
                if self.mini is not None and self.mini.isVisible():
                    self._mini_track()
                page = self.pages.currentWidget()
                if page is not None:
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
        self._refresh_panels()

    def _set_current_marks(self):
        cur = self.current()
        tid = cur["id"] if cur else None
        playing = self._playing()
        for lst in self._live_lists():
            lst.set_current(tid, playing)

    def toggle(self):
        if self.current() is not None and not self._started:
            self._play_current()            # a song brought back from last time, not begun yet
            return
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
            self.taste.log(self.current(), "repeat")
            self._listen = self.current()
            self.player.setPosition(0)
            self.player.play()
            return
        i = self._next_index()
        if i is None:
            cur = self.current()
            if auto and cur and cur.get("source") == "youtube" and self.taste.state["prefs"].get("autoplay", True):
                self.status.setText("Finding songs like %s..." % cur["title"])
                self._awaiting_upnext = True
                self._fill_upnext(force=True)
                return
            if auto:
                self.player.stop()
            return
        self.index = i
        self._play_current()

    def prev(self):
        if self.player.position() > 3000 or self.index <= 0:
            self.player.setPosition(0)
            return
        self.index -= 1
        self._play_current()

    def _toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self._shuffle_pick = None
        self._sync_controls()
        self._arm_next()

    def _cycle_repeat(self):
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self._sync_controls()
        self._arm_next()

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
            btn.set_playing(playing)
        accent = self._np_pal["accent"].name()
        self.bar.shuffle_btn.tint = accent if self.shuffle else None
        self.bar.shuffle_btn.set_tip("Shuffle: on" if self.shuffle else "Shuffle: off")
        self.bar.repeat_btn.set_kind("repeat_one" if self.repeat == "one" else "repeat")
        self.bar.repeat_btn.tint = accent if self.repeat != "off" else None
        self.bar.repeat_btn.set_tip({"off": "Repeat: off", "all": "Repeat: all", "one": "Repeat: this song"}
                                    [self.repeat])
        f = self.full
        f.shuffle_btn.tint = self.bar.shuffle_btn.tint
        f.repeat_btn.tint = self.bar.repeat_btn.tint
        f.repeat_btn.set_kind(self.bar.repeat_btn.kind)
        muted = self.audio.isMuted() or self.bar.volume.value() == 0
        for b in (self.bar.vol_icon, f.vol_icon):
            b.set_kind("mute" if muted else "speaker")
        for b in (self.bar.shuffle_btn, self.bar.repeat_btn, self.bar.vol_icon, f.shuffle_btn, f.repeat_btn,
                  f.vol_icon):
            b.update()

    def _on_position(self, ms):
        self._last_move = time.monotonic()
        if ms > 0:
            self._last_pos = ms
        if self._seeking:
            return
        for s in (self.bar.seek, self.full.seek):
            s.blockSignals(True)
            s.setValue(ms)
            s.blockSignals(False)
        self.bar.pos_label.setText(format_eta(ms // 1000))
        if self.full.isVisible():
            self.full.pos_label.setText(format_eta(ms // 1000))
            dur = self.player.duration()
            if dur:
                self.full.len_label.setText(format_eta(dur // 1000))
            if self.full.lyrics.isVisible():
                self.full.lyrics.set_position(ms, self._playing())

    def _on_duration(self, ms):
        for s in (self.bar.seek, self.full.seek):
            s.setRange(0, max(0, ms))
        self.bar.len_label.setText(format_eta(ms // 1000) if ms else "0:00")
        self.full.len_label.setText(self.bar.len_label.text())
        cur = self.current()
        if (ms and cur is not None and self.full.lyrics.isVisible()
                and getattr(self, "_lyrics_for", None) == cur.get("id")
                and abs(self._song_seconds(cur) - getattr(self, "_lyrics_secs", 0)) > 2):
            self._fetch_lyrics(cur)    # the song's real length: the lyrics that fit it

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
            self._end_listen(natural=True)
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
        self.side.close_panel() if self.side.isVisible() else None
        self._refresh_panels()
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

    def _song_seconds(self, track):
        """How long `track` is -- by the sound itself once it's playing (a
        video's cut of a song can be shorter than the album's)."""
        key = getattr(self.player, "current_key", lambda: None)()
        ms = self.player.duration() if key == track.get("id") else 0
        return int(round(ms / 1000.0)) if ms else int(track.get("duration") or 0)

    def _fetch_lyrics(self, track):
        self._lyrics_for = track["id"]
        self._lyr = None
        self.full.lyrics.set_lyrics("loading")
        self.full.set_lang_label("")
        secs = self._lyrics_secs = self._song_seconds(track)
        if not secs and getattr(self.player, "current_key", lambda: None)() == track.get("id"):
            return   # its length is on the way (lyrics timed for another cut would drift) -- _on_duration asks again
        hint = upnext.features(track, self.taste.memory)["lang"]
        look = dict(track, duration=secs)

        def work():
            self._lyrics_sig.emit(track["id"], lyrics_db.find_versions(look, hint))
        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics(self, track_id, found):
        if track_id != getattr(self, "_lyrics_for", None):
            return
        self._lyr = found or None
        self._apply_lyrics()

    def _lyrics_choice(self):
        """(the version shown, what's under each line): "" nothing, "v:Deva"
        another version, "tr:en" a translation, "pron" how it's said."""
        found = self._lyr or {}
        versions = found.get("versions") or {}
        st = self.settings or {}
        main = st.get("lyrics_main") if st.get("lyrics_main") in versions else found.get("main")
        under = st.get("lyrics_sub", "auto")
        if under == "auto" or (under.startswith("v:") and under[2:] not in versions
                               and under[2:] != found.get("spell")):
            dflt = found.get("sub")
            under = ("v:" + dflt) if dflt and dflt != main else ""
        if main in versions and not versions[main].get("synced"):
            timed = [c for c in (found.get("order") or list(versions)) if versions[c].get("synced")]
            if timed:
                main = timed[0]          # words that move with the song beat words that don't
        if under == "v:" + str(main):
            under = ""
        return main, under

    def _apply_lyrics(self):
        found = self._lyr or {}
        versions = found.get("versions") or {}
        if not versions:
            self.full.lyrics.set_lyrics("none")
            self.full.set_lang_label("")
            return
        main, under = self._lyrics_choice()
        res = versions[main]
        self.full.lyrics.set_lyrics("ok", res["lines"], [], res.get("plain") or "", script=main)
        self.full.lyrics.set_position(self.player.position(), self._playing())
        self.full.set_lang_label(self._lyrics_label(main, under))
        self._lyr_gen += 1
        if not under:
            return
        texts = [t for _ms, t in res["lines"]] if res["lines"] else (res.get("plain") or "").splitlines()
        kind, _, arg = under.partition(":")
        if kind == "v" and arg in versions:
            other = versions[arg]
            subs = lyrics_db.align(res["lines"], other["lines"]) if res["lines"] else \
                (other.get("plain") or "").splitlines()
            self.full.lyrics.set_subs(subs, arg)
            return
        gen, lang = self._lyr_gen, found.get("lang")

        def work():
            subs, script = [], "Latn"
            try:
                if kind == "v":                      # spelt out from English letters
                    subs, script = lyrics_db.spell(texts, lang), arg
                    if subs and res["lines"]:
                        versions.setdefault(arg, {"lines": [(ms, t) for (ms, _x), t in zip(res["lines"], subs)],
                                                  "plain": "", "synced": True, "spelt_out": True})
                elif kind == "tr":
                    # from the song's own script where there is one: a better translation
                    native = next((c for c in found.get("order") or [] if c != "Latn"), None)
                    src = texts
                    if native and native != main and res["lines"]:
                        src = lyrics_db.align(res["lines"], versions[native]["lines"])
                    elif main == "Latn" and found.get("spell"):
                        src = lyrics_db.spell(texts, lang) or texts
                    subs = lyrics_db.translate(src, arg, lang or "auto")
                    script = lyrics_db.script_code(" ".join(subs[:20]))
                elif kind == "pron":
                    subs = lyrics_db.pronounce(texts, main)
            except Exception:   # noqa: BLE001
                logger.info("Couldn't get the lyrics' other line", exc_info=True)
            self._lyrics_sub_sig.emit(gen, subs, script)
        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics_sub(self, gen, subs, script):
        if gen != self._lyr_gen:
            return
        if subs:
            self.full.lyrics.set_subs(subs, script)
        else:
            self.status.setText("That isn't available for this song right now.")

    @staticmethod
    def _lyrics_label(main, under):
        name = lyrics_db.SHORT.get(main, main)
        if not under:
            return name + "  ▾"
        kind, _, arg = under.partition(":")
        if kind == "v":
            other = lyrics_db.SHORT.get(arg, arg)
        elif kind == "tr":
            other = dict(lyrics_db.TRANSLATE_TO).get(arg, arg)
        else:
            other = "Pronunciation"
        return "%s  ·  %s  ▾" % (name, other)

    def _lyrics_menu(self, pos):
        """The lyrics' languages: the version shown (each script the words come
        in), and what's under each line -- another version, the words spelt in
        their own script, how they're said, or a translation into any of two
        dozen languages."""
        found = self._lyr or {}
        versions = found.get("versions") or {}
        if not versions:
            return
        main, under = self._lyrics_choice()
        menu = style_menu(QMenu(self), theme.tokens(True))
        head = menu.addAction("SHOW THE WORDS IN")
        head.setEnabled(False)
        for code in found.get("order") or list(versions):
            a = menu.addAction(lyrics_db.SCRIPT_NAMES.get(code, code) + (" (spelt out)" if versions[code].get(
                "spelt_out") else ""))
            a.setCheckable(True)
            a.setChecked(code == main)
            a.triggered.connect(lambda _c=False, c=code: self._set_lyrics_pref(main=c))
        menu.addSeparator()
        head = menu.addAction("UNDER EACH LINE")
        head.setEnabled(False)
        options = [("", "Nothing")]
        for code in found.get("order") or []:
            if code != main:
                options.append(("v:" + code, lyrics_db.SCRIPT_NAMES.get(code, code)))
        spelt = found.get("spell")
        if spelt and main == "Latn" and spelt not in (found.get("order") or []):
            options.append(("v:" + spelt, lyrics_db.SCRIPT_NAMES.get(spelt, spelt) + " — spelt out"))
        if main != "Latn" and "Latn" not in versions:
            options.append(("pron", "How it's said (in English letters)"))
        for value, text in options:
            a = menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(value == under)
            a.triggered.connect(lambda _c=False, v=value: self._set_lyrics_pref(sub=v))
        tr = style_menu(menu.addMenu("Translation"), theme.tokens(True))
        for code, name in lyrics_db.TRANSLATE_TO:
            if code == found.get("lang"):
                continue
            a = tr.addAction(name)
            a.setCheckable(True)
            a.setChecked(under == "tr:" + code)
            a.triggered.connect(lambda _c=False, v="tr:" + code: self._set_lyrics_pref(sub=v))
        menu.exec(pos)

    def _set_lyrics_pref(self, main=None, sub=None):
        if self.settings is not None:
            if main is not None:
                self.settings["lyrics_main"] = main
            if sub is not None:
                self.settings["lyrics_sub"] = sub
            try:
                settings_store.save_settings(self.settings)
            except Exception:   # noqa: BLE001
                logger.debug("Couldn't save the lyrics choice", exc_info=True)
        self._apply_lyrics()

    def close_full(self):
        self.full.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.full.isVisible():
            self.full.setGeometry(self.rect())
        for sheet in (self.onboarding, self.importer, getattr(self, "_settings_sheet", None)):
            if sheet is not None and _alive(sheet) and sheet.isVisible():
                sheet.setGeometry(self.rect())
        QTimer.singleShot(0, lambda: hasattr(self, "side") and self.side.relayout())

    def paintEvent(self, event):
        # the canvas under the floating panels
        p = QPainter(self)
        p.fillRect(self.rect(), canvas_colour(self.stage.pal))
        p.end()

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
        self.taste.log(track, "download")
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
