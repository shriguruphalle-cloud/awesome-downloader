"""The Music tab's panels -- the queue, the equalizer and the song's details
-- sliding in on frosted glass from the right of the page (SidePanel), or as
glass cards beside the full-screen player; and the wave-shaped seek bar."""
from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, \
    Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSlider, QStackedWidget, QVBoxLayout,
    QWidget,
)

from app.core import eq as eq_core
from app.utils.formatting import format_eta

from . import music_look as look
from .browser_chrome import draw_icon
from .music_widgets import Pill, icon_button


def _label(text, px=12.5, weight=QFont.Weight.Medium, alpha=190):
    lab = QLabel(text)
    lab.setFont(look.font(px, weight))
    lab.setStyleSheet("color: rgba(255,255,255,%d); background: transparent;" % alpha)
    return lab


# ------------------------------------------------------------ the quality ---
LOSSLESS = ("flac", "alac", "wavpack", "ape", "tta", "mlp", "truehd", "wav")
_CODEC_NAMES = {"aac": "AAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis", "eac3": "Dolby Digital Plus",
                "ac3": "Dolby Digital", "dts": "DTS", "flac": "FLAC", "alac": "ALAC", "wmav2": "WMA"}


def quality_badge(info):
    """(label, detail, kind) for the song's own quality, from what FFmpeg
    reads in it: "Dolby Atmos", "Hi-Res Lossless 24-bit/96 kHz", "Lossless
    16-bit/44.1 kHz" -- or, for a compressed song, its codec and bit rate.
    kind: "atmos" | "hires" | "lossless" | "surround" | "lossy" | "" """
    import re
    info = info or {}
    codec = (info.get("codec") or "").lower()
    if not codec:
        return "", "", ""
    profile = (info.get("profile") or "").lower()
    rate = int(info.get("sample_rate") or 0)
    fmt = (info.get("sample_format") or "").lower()
    chans = (info.get("channels") or "").lower()
    khz = ("%g kHz" % (rate / 1000.0)) if rate else ""
    if "atmos" in profile or "joc" in profile:
        return "Dolby Atmos", chans if chans not in ("stereo", "mono") else "", "atmos"
    m = re.search(r"(\d+)\s*bit", fmt)
    bits = int(m.group(1)) if m else (16 if "s16" in fmt else 24 if "s24" in fmt else 32 if "s32" in fmt else 0)
    if codec in LOSSLESS or codec.startswith("pcm"):
        bits = bits or 16
        detail = "%d-bit/%s" % (bits, khz) if khz else "%d-bit" % bits
        if bits >= 24 or rate > 48000:
            return "Hi-Res Lossless", detail, "hires"
        return "Lossless", detail, "lossless"
    if any(c in chans for c in ("5.1", "7.1", "6.1", "quad")):
        return _CODEC_NAMES.get(codec, codec.upper()), chans, "surround"
    kbps = info.get("kbps")
    return _CODEC_NAMES.get(codec, codec.upper()), ("%d kbps" % kbps) if kbps else khz, "lossy"


class QualityBadge(QWidget):
    """The song's quality, as a small badge: Hi-Res Lossless in gold, Dolby
    Atmos in white with its mark, Lossless outlined -- a compressed song's
    codec and bit rate, quietly."""

    def __init__(self, small=False, parent=None):
        super().__init__(parent)
        self.small = small
        self.label = self.detail = self.kind = ""
        self.hide()

    def set_info(self, info):
        self.label, self.detail, self.kind = quality_badge(info)
        self.setToolTip({"atmos": "Dolby Atmos: sound placed all around you",
                         "hires": "Hi-Res Lossless: more detail than a CD, nothing thrown away",
                         "lossless": "Lossless: every bit of the original recording",
                         "surround": "Surround sound",
                         "lossy": "Compressed audio, as streamed"}.get(self.kind, ""))
        self.setVisible(bool(self.label))
        self.updateGeometry()
        self.adjustSize()
        self.update()

    def _fonts(self):
        return (look.font(10 if self.small else 11, QFont.Weight.Bold, spacing=0.6),
                look.font(10 if self.small else 11, QFont.Weight.Medium))

    def sizeHint(self):
        lf, df = self._fonts()
        w = QFontMetricsF(lf).horizontalAdvance(self.label.upper()) + 18
        if self.detail:
            w += QFontMetricsF(df).horizontalAdvance(self.detail) + 10
        if self.kind in ("atmos", "hires", "lossless"):
            w += 16
        return QSize(int(w), 20 if self.small else 22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        lf, df = self._fonts()
        if self.kind == "hires":
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#f7e3a1"))
            g.setColorAt(1, QColor("#c9a24a"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            fg = QColor(30, 22, 6)
        elif self.kind == "atmos":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            fg = QColor(10, 12, 18)
        elif self.kind == "lossless":
            p.setPen(QPen(QColor(255, 255, 255, 190), 1))
            p.setBrush(QColor(255, 255, 255, 18))
            fg = QColor(255, 255, 255)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(QColor(255, 255, 255, 10))
            fg = QColor(255, 255, 255, 190)
        p.drawRoundedRect(r, 5, 5)
        x = r.left() + 8
        if self.kind in ("atmos", "hires", "lossless"):
            # a small waveform for lossless; Dolby's double D for Atmos
            p.setPen(QPen(fg, 1.4))
            cy = r.center().y()
            if self.kind == "atmos":
                p.setBrush(fg)
                p.drawChord(QRectF(x, cy - 5, 7, 10), 90 * 16, 180 * 16)
                p.drawChord(QRectF(x + 5, cy - 5, 7, 10), -90 * 16, 180 * 16)
            else:
                for k, hgt in enumerate((4, 9, 6, 10, 5)):
                    p.drawLine(QPointF(x + k * 2.6, cy - hgt / 2), QPointF(x + k * 2.6, cy + hgt / 2))
            x += 16
        p.setPen(fg)
        p.setFont(lf)
        text = self.label.upper()
        p.drawText(QRectF(x, r.top(), r.width(), r.height()), int(Qt.AlignmentFlag.AlignVCenter), text)
        if self.detail:
            x += QFontMetricsF(lf).horizontalAdvance(text) + 8
            p.setFont(df)
            c = QColor(fg)
            c.setAlpha(int(c.alpha() * 0.78))
            p.setPen(c)
            p.drawText(QRectF(x, r.top(), r.width(), r.height()), int(Qt.AlignmentFlag.AlignVCenter), self.detail)
        p.end()


# ------------------------------------------------------------ the seek bar ---


class VSlider(QSlider):
    """One band of the equalizer: a line, filled from 0 dB to its gain."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setRange(-eq_core.MAX_DB * 2, eq_core.MAX_DB * 2)     # half-dB steps
        self.setFixedWidth(28)
        self.setMinimumHeight(230)
        self.accent = QColor("#38bdf8")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.valueChanged.connect(lambda v: self.setToolTip("%+.1f dB" % (v / 2.0)))

    def _value_at(self, y):
        f = 1 - max(0.0, min(1.0, (y - 8) / max(1.0, self.height() - 16)))
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def mousePressEvent(self, e):
        self.setSliderDown(True)
        self.setValue(self._value_at(e.position().y()))

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self.setValue(self._value_at(e.position().y()))
            self.update()

    def mouseReleaseEvent(self, e):
        self.setSliderDown(False)
        self.update()

    def wheelEvent(self, e):
        # half a dB a notch, for fine adjustments
        self.setValue(self.value() + (1 if e.angleDelta().y() > 0 else -1))
        e.accept()

    def mouseDoubleClickEvent(self, e):
        self.setValue(0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        top, bot = 8.0, h - 8.0
        x = w / 2

        def y_of(v):
            return bot - (v - self.minimum()) / float(self.maximum() - self.minimum()) * (bot - top)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for v in (self.maximum(), self.maximum() // 2, 0, self.minimum() // 2, self.minimum()):
            y = y_of(v)
            p.drawLine(QPointF(x - (7 if v == 0 else 4), y), QPointF(x + (7 if v == 0 else 4), y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(x - 2, top, 4, bot - top), 2, 2)
        y0, y1 = y_of(0), y_of(self.value())
        p.setBrush(self.accent if self.isEnabled() else QColor(255, 255, 255, 90))
        p.drawRoundedRect(QRectF(x - 2, min(y0, y1), 4, abs(y1 - y0)), 2, 2)
        p.setBrush(QColor(255, 255, 255) if self.isEnabled() else QColor(200, 200, 200))
        p.drawEllipse(QPointF(x, y1), 7, 7)
        if self.isSliderDown():
            p.setFont(look.font(10, QFont.Weight.Bold))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(0, max(0.0, y1 - 26), w, 14), int(Qt.AlignmentFlag.AlignCenter),
                       "%+g" % (self.value() / 2.0))
        p.end()


class Switch(QAbstractButton):
    """On / off, in the accent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent = QColor("#38bdf8")

    def sizeHint(self):
        return QSize(42, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.accent if on else QColor(255, 255, 255, 50))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(r.right() - d - 3 if on else r.left() + 3, r.top() + 3, d, d))
        p.end()


# --------------------------------------------------------------- the queue ---
class QueuePanel(QWidget):
    """What's playing, and what's next -- a click plays one, ✕ takes it out."""
    clear = Signal()

    def __init__(self, make_list, parent=None):
        super().__init__(parent)
        self.make_list = make_list
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(_label("NOW PLAYING", 10.5, QFont.Weight.Bold, 150))
        self.now = make_list(show_art=True, show_album=False, numbered=False)
        col.addWidget(self.now)
        head = QHBoxLayout()
        head.addWidget(_label("UP NEXT", 10.5, QFont.Weight.Bold, 150))
        head.addStretch(1)
        self.clear_btn = Pill("Clear", style="glass")
        self.clear_btn.clicked.connect(self.clear)
        head.addWidget(self.clear_btn)
        col.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                             " { background: transparent; }")
        self.up = make_list(show_art=True, show_album=False, in_queue=True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.up)
        hl.addStretch(1)
        scroll.setWidget(holder)
        col.addWidget(scroll, 1)
        self.empty = _label("Nothing up next — add songs with +", 13, alpha=150)
        col.addWidget(self.empty)

    def set_queue(self, queue, index):
        cur = queue[index] if 0 <= index < len(queue) else None
        self.now.set_tracks([cur] if cur else [])
        self.up.set_tracks(queue[index + 1:] if index >= 0 else list(queue))
        self.empty.setVisible(not self.up.rows)


# ------------------------------------------------------------ the equalizer ---
class EqPanel(QWidget):
    """Ten bands, presets, and the effects: bass boost, virtual surround
    (for headphones), volume levelling and mono. Changes are heard at once."""
    changed = Signal(object)
    SHOWN = ("Flat", "Bass boost", "Bollywood", "Pop", "Rock", "Vocal")

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        from .music_onboarding import Chip, _Flow
        self.s = eq_core.settings(settings)
        self._quiet = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(_label("Equalizer", 14, QFont.Weight.DemiBold, 235))
        top.addStretch(1)
        self.enabled = Switch()
        self.enabled.toggled.connect(lambda on: self._set("enabled", on))
        top.addWidget(self.enabled)
        col.addLayout(top)
        bands = QGridLayout()
        bands.setHorizontalSpacing(2)
        bands.setVerticalSpacing(4)
        scale = QVBoxLayout()
        for t in ("+12", "+6", "0", "−6", "−12"):
            lab = _label(t, 10.5, alpha=130)
            scale.addWidget(lab)
            if t != "−12":
                scale.addStretch(1)
        bands.addLayout(scale, 0, 0)
        self.sliders = []
        for i, name in enumerate(eq_core.LABELS):
            s = VSlider()
            s.valueChanged.connect(lambda _v, i=i: self._band(i))
            bands.addWidget(s, 0, i + 1, Qt.AlignmentFlag.AlignHCenter)
            lab = _label(name, 10.5, alpha=150)
            lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            bands.addWidget(lab, 1, i + 1)
            self.sliders.append(s)
        col.addLayout(bands)
        col.addWidget(_label("PRESETS", 10.5, QFont.Weight.Bold, 150))
        self.presets = _Flow()
        self.chips = {}
        for name in self.SHOWN:
            c = Chip(name, name)
            c.setFixedHeight(34)
            c.clicked.connect(lambda _c=False, n=name: self._preset(n))
            self.presets.add(c)
            self.chips[name] = c
        self.more = Chip("more", "More  ▾")
        self.more.setCheckable(False)
        self.more.setFixedHeight(34)
        self.more.clicked.connect(self._more_presets)
        self.presets.add(self.more)
        col.addWidget(self.presets)
        col.addWidget(_label("EFFECTS", 10.5, QFont.Weight.Bold, 150))
        self.toggles = {}
        for key, text, tip in (("bass", "Bass boost", "More low end"),
                               ("surround", "Virtual surround", "Wider sound, for headphones"),
                               ("normalize", "Level the volume", "Quiet and loud songs evened out"),
                               ("mono", "Mono", "Both ears hear everything")):
            row = QHBoxLayout()
            lab = _label(text, 13.5, QFont.Weight.Medium, 225)
            lab.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch(1)
            sw = Switch()
            sw.toggled.connect(lambda on, k=key: self._set(k, on))
            row.addWidget(sw)
            col.addLayout(row)
            self.toggles[key] = sw
        col.addWidget(_label("SPATIAL AUDIO", 10.5, QFont.Weight.Bold, 150))
        note = _label("Open source: the song upmixed to 7.1 virtual speakers and placed around your head "
                      "(FFmpeg's surround and headphone filters, with a head model). Not Dolby Atmos itself — "
                      "an open alternative to it.", 11.5, alpha=150)
        note.setWordWrap(True)
        col.addWidget(note)
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.spatial = {}
        for key, text in (("off", "Off"), ("headphones", "Headphones"), ("speakers", "Speakers")):
            b = Pill(text, style="glass")
            b.clicked.connect(lambda _c=False, k=key: self._set_spatial(k))
            modes.addWidget(b)
            self.spatial[key] = b
        modes.addStretch(1)
        col.addLayout(modes)
        reset = Pill("Reset to default", "reload", style="glass")
        reset.clicked.connect(lambda: self.set_settings(eq_core.DEFAULT, emit=True))
        col.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        self.set_settings(self.s)

    def set_accent(self, colour):
        for w in self.sliders + [self.enabled] + list(self.toggles.values()):
            w.accent = colour
            w.update()

    def set_settings(self, s, emit=False):
        self.s = eq_core.settings(s)
        self._quiet = True
        self.enabled.setChecked(self.s["enabled"])
        for sl, g in zip(self.sliders, self.s["gains"]):
            sl.setValue(int(round(g * 2)))
        for k, sw in self.toggles.items():
            sw.setChecked(bool(self.s[k]))
        for k, b in self.spatial.items():
            b.style = "tab_on" if self.s["spatial"] == k else "glass"
            b.update()
        for name, c in self.chips.items():
            c.setChecked(self.s["enabled"] and name == self.s["preset"])
        other = self.s["enabled"] and self.s["preset"] not in self.chips and self.s["preset"] in eq_core.PRESETS
        self.more.setText(("%s  ▾" % self.s["preset"]) if other else "More  ▾")
        self.more.setChecked(other)
        self.more.update()
        self._quiet = False
        if emit:
            self.changed.emit(dict(self.s))

    def _set(self, key, value):
        if self._quiet:
            return
        self.s[key] = bool(value)
        self.changed.emit(dict(self.s))

    def _set_spatial(self, mode):
        self.s["spatial"] = mode
        for k, b in self.spatial.items():
            b.style = "tab_on" if k == mode else "glass"
            b.update()
        self.changed.emit(dict(self.s))

    def _band(self, i):
        if self._quiet:
            return
        self.s["gains"][i] = self.sliders[i].value() / 2.0
        self.s["enabled"] = True
        self.s["preset"] = "Custom"
        self._quiet = True
        self.enabled.setChecked(True)
        for c in self.chips.values():
            c.setChecked(False)
        self._quiet = False
        self.changed.emit(dict(self.s))

    def _preset(self, name):
        self.set_settings(eq_core.apply_preset(self.s, name), emit=True)

    def _more_presets(self):
        from PySide6.QtWidgets import QMenu
        from . import theme
        from .browser_chrome import style_menu
        menu = style_menu(QMenu(self), theme.tokens(True))
        for name in eq_core.PRESETS:
            if name not in self.SHOWN:
                a = menu.addAction(name, lambda n=name: self._preset(n))
                a.setCheckable(True)
                a.setChecked(self.s["enabled"] and self.s["preset"] == name)
        menu.exec(self.more.mapToGlobal(QPoint(0, self.more.height())))


# --------------------------------------------------------- the song's details ---
class InfoPanel(QWidget):
    """The song: album, artist, year, length -- and how it sounds: codec,
    bitrate, sample rate, and a plain quality badge."""

    FIELDS = ("Album", "Artist", "Year", "Duration", "Source", "Codec", "Bitrate", "Sample rate", "Channels",
              "Quality", "Licence")

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        head = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(84, 84)
        head.addWidget(self.cover)
        names = QVBoxLayout()
        self.title = _label("", 15, QFont.Weight.DemiBold, 245)
        self.title.setWordWrap(True)
        self.sub = _label("", 12.5, alpha=180)
        self.badge = _label("", 11, QFont.Weight.Bold, 240)
        names.addWidget(self.title)
        names.addWidget(self.sub)
        names.addWidget(self.badge)
        names.addStretch(1)
        head.addLayout(names, 1)
        col.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        self.values = {}
        for i, name in enumerate(self.FIELDS):
            grid.addWidget(_label(name, 12.5, alpha=140), i, 0)
            v = _label("—", 12.5, QFont.Weight.Medium, 235)
            v.setWordWrap(True)
            grid.addWidget(v, i, 1)
            self.values[name] = v
        col.addLayout(grid)
        col.addStretch(1)

    @staticmethod
    def quality(info, track):
        label, detail, _kind = quality_badge(info)
        return " · ".join(x for x in (label, detail) if x)

    def set_track(self, track, info=None, cover=None):
        info = info or {}
        t = track or {}
        self.title.setText(t.get("title", "Nothing playing"))
        self.sub.setText(t.get("artist", ""))
        q = self.quality(info, t)
        self.badge.setText(q.upper())
        if cover is not None:
            self.cover.setPixmap(look.rounded(cover, 84, 84, 10))
        else:
            self.cover.clear()
        source = {"youtube": "YouTube Music", "openverse": "Openverse (Creative Commons)",
                  "archive": "Internet Archive", "local": "On this PC"}.get(t.get("source"), t.get("source", ""))
        vals = {"Album": t.get("album"), "Artist": t.get("artist"), "Year": t.get("year"),
                "Duration": format_eta(int(t["duration"])) if t.get("duration") else (
                    format_eta(info["duration_ms"] // 1000) if info.get("duration_ms") else ""),
                "Source": source, "Codec": (info.get("codec") or "").upper(),
                "Bitrate": "%d kbps" % info["kbps"] if info.get("kbps") else "",
                "Sample rate": "%.1f kHz" % (info["sample_rate"] / 1000.0) if info.get("sample_rate") else "",
                "Channels": (info.get("channels") or "").capitalize(), "Quality": q, "Licence": t.get("license")}
        for k, v in vals.items():
            self.values[k].setText(v or "—")


def _scrolling(widget):
    """`widget` in a transparent scroll area, for when the room is short."""
    if isinstance(widget, QueuePanel):
        return widget           # it scrolls its own list
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                         " { background: transparent; }")
    scroll.setWidget(widget)
    return scroll


# ------------------------------------------------------------ the side panel ---
class SidePanel(QWidget):
    """A frosted-glass panel that slides in over the page's right side, with
    the queue, the equalizer and the song's details as tabs."""
    closed = Signal()
    TABS = (("queue", "Queue", "queue"), ("eq", "Equalizer", "sliders"), ("info", "Song info", "info"))
    W = 420

    def __init__(self, stage, pages, parent=None):
        super().__init__(parent)
        self.stage = stage
        self._backdrop = None
        self._src = QRectF()
        self.hide()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 20, 20)
        outer.setSpacing(14)
        head = QHBoxLayout()
        self.heading = _label("", 19, QFont.Weight.Bold, 250)
        head.addWidget(self.heading)
        head.addStretch(1)
        close = icon_button("close", "Close", 34, 14, self)
        close.clicked.connect(self.close_panel)
        head.addWidget(close)
        outer.addLayout(head)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_btns = {}
        for key, text, _icon in self.TABS:
            b = Pill(text, style="tab")
            b.clicked.connect(lambda _c=False, k=key: self.show_tab(k))
            tabs.addWidget(b)
            self.tab_btns[key] = b
        tabs.addStretch(1)
        outer.addLayout(tabs)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        self.pages = dict(pages)
        for key, _t, _i in self.TABS:
            self.stack.addWidget(_scrolling(self.pages[key]))
        outer.addWidget(self.stack, 1)
        self.current = "queue"
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._refresh = QTimer(self)
        self._refresh.setInterval(900)
        self._refresh.timeout.connect(self._capture)

    def target_rect(self):
        g = self.stage.geometry()
        return QRect(g.right() - self.W - 14, g.top() + 12, self.W, g.height() - 24)

    def show_tab(self, key):
        self.current = key
        self.stack.setCurrentIndex([k for k, _t, _i in self.TABS].index(key))
        self.heading.setText(dict((k, t) for k, t, _i in self.TABS)[key])
        for k, b in self.tab_btns.items():
            b.style = "tab_on" if k == key else "tab"
            b.update()

    def open(self, key):
        if self.isVisible() and self.current == key:
            self.close_panel()
            return
        self.show_tab(key)
        r = self.target_rect()
        self.setGeometry(r)
        self._capture()
        if not self.isVisible():
            self.move(r.left() + 60, r.top())
            self.show()
            self._anim.stop()
            self._anim.setStartValue(QPoint(r.left() + 60, r.top()))
            self._anim.setEndValue(r.topLeft())
            self._anim.start()
        self.raise_()
        self._refresh.start()

    def close_panel(self):
        self._refresh.stop()
        self.hide()
        self.closed.emit()

    def relayout(self):
        if self.isVisible():
            self.setGeometry(self.target_rect())
            self._capture()

    def _capture(self):
        """What's behind the panel, blurred: the frost."""
        if not self.stage.isVisible():
            return
        r = self.target_rect()
        local = QRect(r.topLeft() - self.stage.geometry().topLeft(), r.size())
        shot = self.stage.grab(local)
        self._backdrop = look.blurred(shot, scale=0.2, radius=10)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        look.paint_glass(p, r, 28, self._backdrop, None, QColor(14, 16, 26))
        p.end()


class GlassCard(QWidget):
    """A card of frosted glass over the full-screen player's blurred cover."""

    def __init__(self, title="", icon=None, parent=None, closable=True):
        super().__init__(parent)
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(24, 20, 22, 20)
        self.col.setSpacing(12)
        self.close_btn = None
        if title:
            head = QHBoxLayout()
            if icon:
                ic = _Icon(icon)
                head.addWidget(ic)
            head.addWidget(_label(title, 17, QFont.Weight.Bold, 250))
            head.addStretch(1)
            if closable:
                self.close_btn = icon_button("close", "Close", 32, 13, self)
                self.close_btn.clicked.connect(self.hide)
                head.addWidget(self.close_btn)
            self.col.addLayout(head)

    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        frosted = look.paint_frost(p, self, r, 36)
        look.paint_glass(p, r, 36, None, None, QColor(20, 26, 40), strength=0.62 if frosted else 0.75)
        p.end()


class _Icon(QWidget):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(24, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        draw_icon(p, self.kind, QRectF(2, 2, 20, 20), look.TEXT)
        p.end()


class GlassSheet(QWidget):
    """A window of frosted glass over the whole tab: the tab behind it
    blurred and dimmed, and a centred card with large rounded corners that
    fades in. Always sized to the tab (MusicTab keeps it so)."""
    RADIUS = 32

    def __init__(self, parent, max_w=1000, max_h=720):
        super().__init__(parent)
        self.max_w, self.max_h = max_w, max_h
        self._shot = None
        self.hide()
        self.card = QWidget(self)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.card_lay = QVBoxLayout(self.card)
        self.card_lay.setContentsMargins(44, 34, 44, 28)
        self.card_lay.setSpacing(10)
        self._fade = None

    def open_sheet(self):
        host = self.parent()
        self.hide()
        self._shot = look.blurred(host.grab(), scale=0.16, radius=12)
        self.setGeometry(host.rect())
        self._place()
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self.show()
        self.raise_()
        self._fade = QPropertyAnimation(eff, b"opacity", self)
        self._fade.setDuration(200)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(lambda: self.setGraphicsEffect(None))
        self._fade.start()

    def _place(self):
        w = int(min(self.max_w, self.width() - 80))
        h = int(min(self.max_h, self.height() - 60))
        self.card.setGeometry((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def mousePressEvent(self, e):
        e.accept()          # the tab underneath waits until the sheet is closed

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self._shot is not None:
            p.drawPixmap(r, self._shot, QRectF(self._shot.rect()))
        else:
            look.paint_backdrop(p, r, look.DEFAULT)
        p.fillRect(r, QColor(4, 6, 14, 120))
        c = QRectF(self.card.geometry())
        look.paint_shadow(p, c, self.RADIUS)
        src = None
        if self._shot is not None:
            sx, sy = self._shot.width() / max(1.0, r.width()), self._shot.height() / max(1.0, r.height())
            src = QRectF(c.left() * sx, c.top() * sy, c.width() * sx, c.height() * sy)
        look.paint_glass(p, c, self.RADIUS, self._shot, src, QColor(18, 22, 36), strength=0.9)
        p.end()
