"""A song as a short video for Instagram or anywhere (the phone app's ShareVideo.kt): the player's moving background --
the same shader, moving to this stretch of the song as it does on screen -- with the player card over it (cover, name,
a progress bar that moves), and the song's sound.

  sound:  FFmpeg decodes the stretch chosen; faded in and out, AAC
  motion: per video frame, the four levels the screen's meter gives (bass, mid, high, loudness), measured the same way
          from the decoded sound, drive the background (music_backdrop)
  frames: the shader's picture and the card drawn over it, piped to FFmpeg's H.264 encoder
  file:   MP4, 30 frames a second, 1080 wide; 9:16 for a Story or Reel, 4:5 for a post

A frame is drawn at a time on the UI thread (the graphics context lives there), between which everything else runs; the
pipe is fed from a thread of its own, so a slow encoder never holds the window up.
"""
import math
import operator
import os
import queue
import re
import subprocess
import threading
import time
from array import array

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, \
    QRadialGradient, QTextLayout, QTextOption

from app import config
from app.core import music_sources
from app.logging_setup import get_logger

from . import music_look as look
from .browser_chrome import draw_icon

logger = get_logger("share_video")

FPS = 30
RATE = 48000
MOVING_MODES = ("aurora", "nebula", "glass", "neon")      # others ("still", your image, the betas) get the aurora
SHARE_DIR = os.path.join(config.APPDATA_DIR, "share")
BRAND = "♪  Awesome Music"
LINK = "Free on Android · awesomeguru.in/android"
APP_LINK = "https://awesomeguru.in/android"
FRAME_BEHIND = 6                         # frames waiting on the encoder before drawing waits for it


class Cancelled(Exception):
    pass


def clock(ms):
    s = max(0, int(ms // 1000))
    return "%d:%02d" % (s // 60, s % 60)


def output_path(title):
    """Where a video goes while it's new; what's over an hour old there is let go."""
    os.makedirs(SHARE_DIR, exist_ok=True)
    now = time.time()
    for name in os.listdir(SHARE_DIR):
        path = os.path.join(SHARE_DIR, name)
        try:
            if now - os.path.getmtime(path) > 3600:
                os.remove(path)
        except OSError:
            pass
    return os.path.join(SHARE_DIR, "AwesomeMusic-%s-%d.mp4" % (re.sub(r"[^A-Za-z0-9]+", "-", title)[:40].strip("-"),
                                                               int(now)))


# ---- the sound ----
def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)


def decode(src, start_ms, seconds, song_ms=0):
    """The stretch as 16-bit stereo; a stretch past the song's end starts earlier, so the video is never cut short.
    (pcm bytes, where it really starts in ms)"""
    if song_ms:
        start_ms = min(start_ms, max(0, song_ms - seconds * 1000))
    start_ms = max(0, start_ms)
    out = _run([music_sources._ffmpeg(), "-nostdin", "-v", "error", "-ss", "%.3f" % (start_ms / 1000.0),
                "-t", str(seconds), "-i", src, "-vn", "-ac", "2", "-ar", str(RATE), "-f", "s16le", "pipe:1"],
               timeout=120)
    if not out.stdout:
        raise music_sources.MusicError("This song couldn't be read.")
    return out.stdout, start_ms


def levels(pcm, frames):
    """Per video frame: (bass, mid, high, loudness) 0..1, measured as the player's meter does (Engine._measure)."""
    a = array("h")
    a.frombytes(pcm[:len(pcm) - len(pcm) % 4])
    left = a[::2]
    per = RATE // FPS
    lp, k = 0.0, 0.47
    out = []
    for fi in range(frames):
        frame = left[fi * per:(fi + 1) * per]
        bass = mid = hi = loud = 0.0
        n = 0
        for i in range(0, len(frame) - 31, 32):
            block = frame[i:i + 32]
            m = sum(block) / 32.0
            lp += (m - lp) * k
            bass += lp * lp
            mid += (m - lp) * (m - lp)
            hi += sum(map(abs, map(operator.sub, block[1:], block))) / 31.0
            loud += max(block) - min(block)
            n += 1
        out.append((math.sqrt(bass / n) / 32768.0, math.sqrt(mid / n) / 32768.0, hi / n / 32768.0, loud / n / 65536.0)
                   if n else (0.0, 0.0, 0.0, 0.0))
    return out


# ---- the card ----
def _font(px, weight):
    return look.font(px, weight)


def _elide(p, text, width):
    return QFontMetricsF(p.font()).elidedText(text or "", Qt.TextElideMode.ElideRight, width)


def _white(alpha=255):
    return QColor(255, 255, 255, alpha)


def _cover(p, art, r, radius, pal, shadow=False):
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    if shadow:
        for i in range(10, 0, -1):            # a soft shadow in rings
            grow = radius * 0.12 * i
            sh = QPainterPath()
            sh.addRoundedRect(r.adjusted(-grow, -grow * 0.4, grow, grow * 1.6), radius + grow, radius + grow)
            p.fillPath(sh, QColor(0, 0, 0, 7))
    if art is None or art.isNull():
        p.fillPath(path, QColor(pal["base"]))
        return
    p.save()
    p.setClipPath(path)
    scale = max(r.width() / art.width(), r.height() / art.height())
    w, h = art.width() * scale, art.height() * scale
    p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), art)
    p.restore()


def _text_block(p, text, width, font):
    """`text` wrapped to `width`, as a QTextLayout with its height."""
    lay = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    lay.setTextOption(opt)
    lay.beginLayout()
    y = 0.0
    while True:
        line = lay.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        line.setPosition(QPointF(0, y))
        y += line.height() * 1.08
    lay.endLayout()
    return lay, y


class Card:
    """What goes over the background, the size of the video, transparent around it. Redrawn each second (the times
    move); the bar's fill and knob go over it every frame."""

    def __init__(self, w, h, track, art, pal, total_ms, look_name="card", lines=()):
        self.w, self.h = w, h
        self.track, self.art, self.pal, self.total_ms = track, art, pal, total_ms
        self.story = h > w * 1.5
        self.image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        self._shown = None
        self.lines = list(lines or ())
        self.look = look_name if look_name in ("focus",) or (look_name == "lyrics" and self.lines) else "card"
        {"card": self._lay_card, "focus": self._lay_focus, "lyrics": self._lay_words}[self.look]()

    def at(self, ms):
        """The card at `ms` into the song; True when the picture changed."""
        key = int(ms // 1000) if self.look != "lyrics" else (int(ms // 1000), self._line_at(ms))
        if key == self._shown:
            return False
        self._shown = key
        self.image.fill(0)
        p = QPainter(self.image)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        {"card": self._paint_card, "focus": self._paint_focus, "lyrics": self._paint_words}[self.look](p, ms)
        p.end()
        return True

    # ---- the player ----
    def _lay_card(self):
        w, h = self.w, self.h
        cw = w * (0.68 if self.story else 0.527)
        k = self.k = cw / 864.0
        self.pad = 46 * k
        self.art_side = cw - self.pad * 2
        self.title_px, self.sub_px = 50 * k, 34 * k
        self.ctrl = 150 * k
        ch = self.pad + self.art_side + 34 * k + self.title_px + 12 * k + self.sub_px + 44 * k + 8 * k + 54 * k \
            + 30 * k + self.ctrl + self.pad
        left = (w - cw) / 2
        top = (h - ch) / 2 - (h * 0.03 if self.story else h * 0.015)
        self.rect = QRectF(left, top, cw, ch)
        self.radius = 64 * k
        self.art_rect = QRectF(left + self.pad, top + self.pad, self.art_side, self.art_side)
        self.title_y = self.art_rect.bottom() + 34 * k + self.title_px
        self.sub_y = self.title_y + 12 * k + self.sub_px
        bar_y = self.sub_y + 44 * k
        self.bar = QRectF(self.art_rect.left(), bar_y, self.art_side, 8 * k)
        self.times_y = self.bar.bottom() + 54 * k
        self.ctrl_cy = self.times_y + 30 * k + self.ctrl / 2

    def _paint_card(self, p, ms):
        k, rect, radius, pal = self.k, self.rect, self.radius, self.pal
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # the glass: a soft shadow, a dark tint of the cover's colour, light along the top, a fine rim
        for i in range(12, 0, -1):
            grow = 6 * k * i
            sh = QPainterPath()
            sh.addRoundedRect(rect.adjusted(-grow, -grow * 0.5, grow, grow * 1.4), radius + grow, radius + grow)
            p.fillPath(sh, QColor(0, 0, 0, 9))
        p.fillPath(path, QColor(10, 8, 8, 170))
        deep = QColor(pal["deep"])
        g = QLinearGradient(0, rect.top(), 0, rect.bottom())
        c1, c2 = QColor(deep), QColor(deep)
        c1.setAlphaF(0.45)
        c2.setAlphaF(0.25)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.fillPath(path, g)
        g = QLinearGradient(0, rect.top(), 0, rect.top() + 160 * k)
        g.setColorAt(0, _white(36))
        g.setColorAt(1, _white(0))
        p.fillPath(path, g)
        p.setPen(QPen(_white(60), 2.5 * k))
        p.drawPath(path)
        _cover(p, self.art, self.art_rect, 40 * k, pal)
        left, side = self.art_rect.left(), self.art_side
        p.setFont(_font(self.title_px, QFont.Weight.Bold))
        p.setPen(_white())
        p.drawText(QPointF(left, self.title_y), _elide(p, self.track.get("title", ""), side))
        p.setFont(_font(self.sub_px, QFont.Weight.Medium))
        p.setPen(_white(200))
        p.drawText(QPointF(left, self.sub_y), _elide(p, self.track.get("artist", ""), side))
        self._times(p, ms, self.times_y, 28 * k)
        # the controls: prev, back 10, the play ring (playing, so pause shows), forward 10, next
        cx, cy = rect.center().x(), self.ctrl_cy
        step = side / 4.6
        for kind, x, s in (("prev", cx - step * 2, 60), ("back10", cx - step, 62), ("fwd10", cx + step, 62),
                           ("next", cx + step * 2, 60)):
            s *= k
            draw_icon(p, kind, QRectF(x - s / 2, cy - s / 2, s, s), _white(), 1.6)
        ring = self.ctrl
        glow = QRadialGradient(cx, cy, ring * 0.85)
        accent = QColor(pal["accent"])
        c = QColor(accent)
        c.setAlphaF(0.40)
        glow.setColorAt(0, c)
        c = QColor(accent)
        c.setAlphaF(0.0)
        glow.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), ring * 0.85, ring * 0.85)
        p.setBrush(QColor(20, 14, 12, 120))
        p.drawEllipse(QPointF(cx, cy), ring / 2, ring / 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(accent, 5 * k))
        p.drawEllipse(QPointF(cx, cy), ring / 2 - 3 * k, ring / 2 - 3 * k)
        s = 58 * k
        draw_icon(p, "pause", QRectF(cx - s / 2, cy - s / 2, s, s), _white(), 1.6)
        self._brand(p, rect.bottom())

    def _times(self, p, ms, y, px):
        p.setFont(_font(px, QFont.Weight.Medium))
        p.setPen(_white(170))
        p.drawText(QPointF(self.bar.left(), y), clock(ms))
        if self.total_ms > 0:
            text = clock(self.total_ms)
            p.drawText(QPointF(self.bar.right() - QFontMetricsF(p.font()).horizontalAdvance(text), y), text)

    def _brand(self, p, below):
        w, h = self.w, self.h
        room = h - below
        mark_px = w * 0.030
        for text, px, y, alpha, weight in (
                (BRAND, mark_px, below + room * 0.40, 225, QFont.Weight.Bold),
                (LINK, w * 0.022, below + room * 0.40 + mark_px * 1.35, 170, QFont.Weight.Medium)):
            p.setFont(_font(px, weight))
            p.setPen(_white(alpha))
            p.drawText(QPointF((w - QFontMetricsF(p.font()).horizontalAdvance(text)) / 2, y), text)

    # ---- focus: the cover alone and large, the name under it, a thin bar ----
    def _lay_focus(self):
        w, h = self.w, self.h
        side = w * (0.72 if self.story else 0.50)
        k = self.k = side / 780.0
        self.side = side
        left = (w - side) / 2
        self.title_px, self.sub_px = 54 * k, 36 * k
        block = side + 56 * k + self.title_px + 14 * k + self.sub_px + 48 * k + 8 * k + 48 * k
        top = (h - block) / 2 - h * 0.02
        self.art_rect = self.rect = QRectF(left, top, side, side)
        self.radius = 44 * k
        self.title_y = self.art_rect.bottom() + 56 * k + self.title_px
        self.sub_y = self.title_y + 14 * k + self.sub_px
        self.bar = QRectF(left, self.sub_y + 48 * k, side, 8 * k)

    def _paint_focus(self, p, ms):
        k = self.k
        _cover(p, self.art, self.art_rect, self.radius, self.pal, shadow=True)
        for text, y, px, weight, alpha in ((self.track.get("title", ""), self.title_y, self.title_px,
                                            QFont.Weight.Bold, 255),
                                           (self.track.get("artist", ""), self.sub_y, self.sub_px,
                                            QFont.Weight.Medium, 200)):
            p.setFont(_font(px, weight))
            p.setPen(_white(alpha))
            text = _elide(p, text, self.side)
            p.drawText(QPointF((self.w - QFontMetricsF(p.font()).horizontalAdvance(text)) / 2, y), text)
        self._times(p, ms, self.bar.bottom() + 48 * k, 28 * k)
        self._brand(p, self.bar.bottom() + 70 * k)

    # ---- lyrics: a small cover and the name above; the line being sung bright and large, the others quieter ----
    def _lay_words(self):
        w, h = self.w, self.h
        width = self.width_ = w * (0.80 if self.story else 0.70)
        k = self.k = width / 864.0
        self.left = (w - width) / 2
        thumb = self.thumb = 132 * k
        top = h * 0.20 if self.story else h * 0.12
        self.art_rect = QRectF(self.left, top, thumb, thumb)
        self.words_top = self.art_rect.bottom() + 80 * k
        self.words_bottom = h * 0.74 if self.story else h * 0.78
        self.bar = QRectF(self.left, self.words_bottom + 30 * k, width, 8 * k)
        self.rect = QRectF(self.left, top, width, self.bar.bottom() - top)
        self.radius = 0.0

    def _line_at(self, ms):
        i = 0
        for n, (at, _text) in enumerate(self.lines):
            if at <= ms + 250:
                i = n
            else:
                break
        return i

    def _paint_words(self, p, ms):
        k = self.k
        _cover(p, self.art, self.art_rect, 22 * k, self.pal)
        tx = self.art_rect.right() + 30 * k
        room = self.left + self.width_ - tx
        p.setFont(_font(44 * k, QFont.Weight.Bold))
        p.setPen(_white())
        p.drawText(QPointF(tx, self.art_rect.top() + self.thumb * 0.46), _elide(p, self.track.get("title", ""), room))
        p.setFont(_font(32 * k, QFont.Weight.Medium))
        p.setPen(_white(200))
        p.drawText(QPointF(tx, self.art_rect.top() + self.thumb * 0.80), _elide(p, self.track.get("artist", ""), room))
        i = self._line_at(ms)
        now_font, dim_font = _font(64 * k, QFont.Weight.ExtraBold), _font(50 * k, QFont.Weight.Bold)
        y = self.words_top

        def put(text, font, alpha, must=False):
            nonlocal y
            lay, height = _text_block(p, text, self.width_, font)
            if not must and y + height > self.words_bottom:
                return False
            p.setPen(_white(alpha))
            lay.draw(p, QPointF(self.left, y))
            y += height + 30 * k
            return True
        if i > 0:
            put(self.lines[i - 1][1], dim_font, 95, must=True)
        put(self.lines[i][1] or "♪", now_font, 255, must=True)
        for _at, text in self.lines[i + 1:]:
            if not put(text, dim_font, 95):
                break
        self._times(p, ms, self.bar.bottom() + 46 * k, 28 * k)
        self._brand(p, self.bar.bottom() + 70 * k)

    # ---- the bar, every frame ----
    def draw_bar(self, p, at):
        b = self.bar
        h = b.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_white(56))
        p.drawRoundedRect(b, h / 2, h / 2)
        p.setBrush(QColor(self.pal["accent"]))
        p.drawRoundedRect(QRectF(b.left(), b.top(), max(h, b.width() * at), h), h / 2, h / 2)
        knob = h * 3.6
        p.setBrush(_white())
        p.drawEllipse(QPointF(b.left() + b.width() * at, b.center().y()), knob / 2, knob / 2)


def still(w, h, track, art, pal, total_ms, look_name, lines, ms):
    """A picture of the video as it'll look: the card over the song's colours (the sheet's preview)."""
    card = Card(w, h, track, art, pal, total_ms, look_name, lines)
    card.at(ms)
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(pal["base"]))
    g.setColorAt(0.6, QColor(pal["deep"]))
    g.setColorAt(1, QColor(0, 0, 0))
    p.fillRect(img.rect(), g)
    p.drawImage(0, 0, card.image)
    card.draw_bar(p, min(1.0, ms / total_ms) if total_ms else 0.0)
    p.end()
    return img


# ---- making it ----
class VideoMaker(QObject):
    """Makes one video: progress(0..1, what), then done(path) or failed(why); cancel() stops it and removes the file."""
    progress = Signal(float, str)
    done = Signal(str)
    failed = Signal(str)
    _ready = Signal(object)
    _broke = Signal(str)
    _finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready.connect(self._begin)
        self._finished.connect(self._done_on_ui)
        self._broke.connect(self._stop_failed)
        self._cancel = False
        self.running = False
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._frame)
        self._proc = None
        self._fluid = None
        self._path = None

    def make(self, src, track, art, pal, song_ms, start_ms, mode, mono, story=True, seconds=15, sound=True,
             look_name="card", lines=()):
        """`src`: a file. The sound is decoded off this thread; the frames then come one at a time."""
        self._cancel = False
        self.running = True
        self.progress.emit(0.02, "Getting the sound…")
        w, h = 1080, 1920 if story else 1350
        job = {"src": src, "track": track, "art": art, "pal": pal, "song_ms": song_ms, "mode": mode, "mono": mono,
               "w": w, "h": h, "seconds": seconds, "sound": sound, "look": look_name, "lines": lines}

        def work():
            try:
                pcm, at = decode(src, start_ms, seconds, song_ms)
                frames = seconds * FPS
                job.update(levels=levels(pcm, frames), at=at, frames=frames)
                self._ready.emit(job)
            except Exception as e:   # noqa: BLE001
                logger.warning("Couldn't read a song for its video", exc_info=True)
                self._broke.emit(music_sources.friendly(e))
        threading.Thread(target=work, daemon=True, name="share-sound").start()

    def _begin(self, job):
        if self._cancel:
            self.running = False
            return
        self._job = job
        self.progress.emit(0.08, "Making the video…")
        w, h, seconds = job["w"], job["h"], job["seconds"]
        self._path = output_path(job["track"].get("title") or "song")
        cmd = [music_sources._ffmpeg(), "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
               "-s", "%dx%d" % (w, h), "-r", str(FPS), "-i", "pipe:0"]
        if job["sound"]:
            cmd += ["-ss", "%.3f" % (job["at"] / 1000.0), "-t", str(seconds), "-i", job["src"], "-map", "0:v", "-map",
                    "1:a:0", "-af", "afade=t=in:d=0.3,afade=t=out:st=%.3f:d=1" % (seconds - 1.0), "-c:a", "aac",
                    "-b:a", "192k", "-ar", str(RATE), "-ac", "2"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-t", str(seconds), self._path]
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            self._stop_failed("Couldn't start FFmpeg: %s" % e)
            return
        self._err = []
        threading.Thread(target=lambda p=self._proc: self._err.extend(p.stderr.read().splitlines()[-6:]),
                         daemon=True).start()
        self._queue = queue.Queue(FRAME_BEHIND)
        self._writer = threading.Thread(target=self._write, args=(self._proc, self._queue), daemon=True,
                                        name="share-pipe")
        self._writer.start()
        from .music_backdrop import BeatFollower, FluidRenderer
        if self._fluid is None:
            self._fluid = FluidRenderer()
        self._beat = BeatFollower()
        self._card = Card(w, h, job["track"], job["art"], job["pal"], job["song_ms"], job["look"], job["lines"])
        self._frame_no = 0
        self._travel = 0.0
        self._clock = 0.0
        self._frame_img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        self._mode = job["mode"] if job["mode"] in MOVING_MODES else "aurora"
        # a moment of motion before the first frame, so the video doesn't open on a still picture
        for _i in range(FPS):
            self._beat.update(0.0, 0.2, 0.0, 0.0)
            self._beat._t -= 1.0 / FPS
        self._timer.start()

    @staticmethod
    def _write(proc, frames):
        while True:
            data = frames.get()
            if data is None:
                break
            try:
                proc.stdin.write(data)
            except OSError:
                while frames.get() is not None:     # drained: the failure is found at the end
                    pass
                break
        try:
            proc.stdin.close()
        except OSError:
            pass

    def _background(self, frame, lv):
        """The moving background, as on screen. No graphics card to draw it: the song's colours, breathing."""
        job = self._job
        bass, mid, hi, loud = lv
        self._beat._t = time.monotonic() - 1.0 / FPS          # its clock is this frame's, not the wall's
        beat, energy = self._beat.update(bass, loud, mid, hi)
        speed = self._beat.speed(1.0 / FPS)
        self._clock += 1.0 / FPS * speed
        self._travel += 1.0 / FPS * speed * 0.16
        w, h = job["w"], job["h"]
        img = None
        if self._fluid is not None and self._fluid.ok:
            img = self._fluid.render(self._mode, self._clock, beat, energy, job["pal"], w // 2, h // 2,
                                     mid=self._beat.mid.pulse, hi=self._beat.hi.pulse, travel=self._travel,
                                     mono=job["mono"], pace=speed)
        p = QPainter(frame)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if img is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(0, 0, w, h), img)
        else:
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0, QColor(job["pal"]["base"]).lighter(int(100 + 40 * beat)))
            g.setColorAt(1, QColor(job["pal"]["deep"]))
            p.fillRect(frame.rect(), g)
        p.end()

    def _frame(self):
        if self._cancel:
            self._finish_cancel()
            return
        if self._queue.full():
            return                              # the encoder is behind: this tick is for everything else
        job = self._job
        i = self._frame_no
        if i >= job["frames"]:
            self._timer.stop()
            self._queue.put(None)
            threading.Thread(target=self._close, args=(self._proc, self._path), daemon=True).start()
            return
        lv = job["levels"][i] if i < len(job["levels"]) else (0.0, 0.0, 0.0, 0.0)
        frame = self._frame_img
        self._background(frame, lv)
        ms = job["at"] + i * 1000 // FPS
        at = min(1.0, ms / job["song_ms"]) if job["song_ms"] else i / job["frames"]
        self._card.at(ms)
        p = QPainter(frame)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawImage(0, 0, self._card.image)
        self._card.draw_bar(p, at)
        p.end()
        self._queue.put(bytes(frame.constBits()))
        self._frame_no = i + 1
        if i % 6 == 0:
            self.progress.emit(0.08 + 0.92 * i / job["frames"], "Making the video…")

    def _close(self, proc, path):
        """After the last frame: the encoder finishes (off the UI thread)."""
        self._writer.join(timeout=120)
        rc = proc.wait()
        if self._cancel:
            return
        ok = rc == 0 and os.path.exists(path) and os.path.getsize(path) > 0
        if ok:
            self._finished.emit(path)
        else:
            why = (self._err[-1].decode("utf-8", "replace") if self._err else "FFmpeg stopped")
            self._broke.emit("The video couldn't be made: %s" % why)

    def _done_on_ui(self, path):
        self.running = False
        self._path = None
        self.progress.emit(1.0, "Done")
        self.done.emit(path)

    def _finish_cancel(self):
        self._timer.stop()
        self.running = False
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        q = getattr(self, "_queue", None)
        if q is not None:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put(None)
        self._remove_output(proc)

    def _remove_output(self, proc):
        path = self._path
        self._path = None

        def gone():
            if proc is not None:
                try:
                    proc.wait(timeout=10)
                except (OSError, subprocess.SubprocessError):
                    pass
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        threading.Thread(target=gone, daemon=True).start()

    def _stop_failed(self, why):
        self._timer.stop()
        self.running = False
        self._remove_output(self._proc)
        self._proc = None
        self.failed.emit(why)

    def cancel(self):
        self._cancel = True
        if not self._timer.isActive():
            self.running = False
