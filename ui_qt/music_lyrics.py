"""The full-screen player's lyrics, the way Apple Music shows them.

Big bold lines on the left, the one being sung bright and the rest dimmed --
softer and a little smaller the further they are from it. Under each line,
smaller, its other version (a translation, the words in another script,
how they're pronounced), when one is chosen. As a line is sung its words
light up from the left with a soft edge, at the pace of their letters
(LRCLIB gives a time per line, not per word: lyrics.sung_fraction).

When the next line comes, the column glides up: the lines below follow a
beat later each, so it settles like a list on a spring rather than jumping.
Before the first words, and in a long instrumental break, three dots fill
in as the music plays toward the next line. The column fades out at its
top and bottom edges. The wheel scrolls through the words (the column comes
back to the song a few seconds later); a click on a line plays from there.
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from app.core import lyrics as lyrics_db

from . import music_look as look

# fonts for the scripts lyrics come in (Windows' own faces, with Inter for Latin)
_FAMILIES = {
    "Deva": ["Nirmala UI", "Mangal"], "Guru": ["Nirmala UI", "Raavi"], "Gujr": ["Nirmala UI", "Shruti"],
    "Beng": ["Nirmala UI", "Vrinda"], "Taml": ["Nirmala UI", "Latha"], "Telu": ["Nirmala UI", "Gautami"],
    "Knda": ["Nirmala UI", "Tunga"], "Mlym": ["Nirmala UI", "Kartika"], "Arab": ["Segoe UI", "Arial"],
    "Hang": ["Malgun Gothic"], "Jpan": ["Yu Gothic UI", "Meiryo UI"], "Hani": ["Microsoft YaHei UI"],
}


def _font(script, px, weight=QFont.Weight.Bold):
    f = QFont()
    fams = _FAMILIES.get(script)
    f.setFamilies((fams or []) + [look.family(), "Segoe UI"])
    f.setPixelSize(max(8, int(px)))
    f.setWeight(weight)
    if not fams:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98)
    return f


def _wrap(text, f, width):
    fm = QFontMetricsF(f)
    out, cur = [], ""
    for word in (text or "").split():
        trial = (cur + " " + word).strip()
        if fm.horizontalAdvance(trial) <= width or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = word
    return out + ([cur] if cur else [])


def _soft(img, k):
    """A blurred copy: down by `k` and back up, smoothly (cheap and soft)."""
    w, h = max(1, int(img.width() / k)), max(1, int(img.height() / k))
    small = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(img.width(), img.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


class LyricsView(QWidget):
    seek_requested = Signal(int)
    ANCHOR = 0.26               # where the line being sung sits, down the column
    BACK_AFTER_S = 3.5          # the wheel's scroll gives way to the song after this
    DIM, PAST_DIM, UNSUNG = 0.34, 0.26, 0.40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        self.lines, self.subs, self.plain = [], [], ""
        self.state = "idle"             # idle | loading | none | ok
        self.script = "Latn"
        self.sub_script = "Latn"
        self.index = -1
        self._ms, self._ms_at, self.playing = 0, time.monotonic(), False
        self._rows = None
        self._key = None
        self._target = 0.0
        self._row_scroll = {}           # row -> where that row is scrolled to now
        self._row_start = {}            # row -> when it may start moving
        self._emph = {}                 # row -> 0..1, how lit it is
        self._manual, self._manual_at = 0.0, 0.0
        self._last_t = time.monotonic()
        self._hits = []
        self._cache = {}
        self._buf = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ---- what to show ----
    def set_lyrics(self, state, lines=None, subs=None, plain="", script=None, sub_script=None):
        self.state = state
        self.lines = list(lines or [])
        self.subs = list(subs or [])
        self.plain = plain or ""
        self.script = script or "Latn"
        self.sub_script = sub_script or "Latn"
        self.index = -1
        self._rows = None
        self._row_scroll, self._row_start, self._emph = {}, {}, {}
        self._manual = 0.0
        self._cache.clear()
        self.update()

    def set_subs(self, subs, sub_script=None):
        self.subs = list(subs or [])
        if sub_script:
            self.sub_script = sub_script
        self._rows = None
        self._cache.clear()
        self.update()

    def set_position(self, ms, playing=True):
        self._ms, self._ms_at, self.playing = ms, time.monotonic(), playing
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def now_ms(self):
        if not self.playing:
            return self._ms
        return self._ms + min(400.0, (time.monotonic() - self._ms_at) * 1000.0)

    # ---- layout ----
    def _sizes(self):
        px = max(24.0, min(50.0, self.width() * 0.058))
        return px, px * 0.54

    def _layout(self):
        key = (self.width(), len(self.lines), len(self.plain), tuple(self.subs[:1]), len(self.subs), self.script,
               self.sub_script)
        if self._rows is not None and self._key == key:
            return self._rows
        px, spx = self._sizes()
        width = max(120.0, self.width() - 24)
        mf, sf = _font(self.script, px), _font(self.sub_script, spx, QFont.Weight.DemiBold)
        lh = QFontMetricsF(mf).height() * (1.10 if self.script != "Latn" else 1.04)
        slh = QFontMetricsF(sf).height() * 1.12
        rows, y = [], 0.0
        texts = [t for _ms, t in self.lines] if self.lines else [x for x in self.plain.splitlines()]
        for i, text in enumerate(texts):
            if not self.lines and not text.strip():
                y += lh * 0.5
                continue
            parts = _wrap(text or "♪", mf, width) or ["♪"]
            sub = self.subs[i] if i < len(self.subs) else ""
            sparts = _wrap(sub, sf, width) if sub and sub.strip() and sub.strip() != (text or "").strip() else []
            h = len(parts) * lh + ((slh * 0.25 + len(sparts) * slh) if sparts else 0)
            ms = self.lines[i][0] if self.lines else None
            end = (self.lines[i + 1][0] if i + 1 < len(self.lines) else (ms or 0) + 5000) if self.lines else None
            rows.append({"i": i, "ms": ms, "end": end, "parts": parts, "sparts": sparts, "y": y, "h": h,
                         "mf": mf, "sf": sf, "lh": lh, "slh": slh})
            y += h + px * 0.78
        self._rows, self._key = rows, key
        self._cache.clear()
        return rows

    def _image(self, row, level=0):
        """The row drawn once, white (level 1, 2: softened)."""
        ck = (row["i"], level, self.width())
        img = self._cache.get(ck)
        if img is not None:
            return img
        if level:
            img = _soft(self._image(row, 0), 2.2 if level == 1 else 3.6)
        else:
            dpr = self.devicePixelRatioF()
            w, h = int((self.width()) * dpr), int((row["h"] + 16) * dpr)
            img = QImage(max(1, w), max(1, h), QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            p.setPen(QColor(255, 255, 255))
            fm = QFontMetricsF(row["mf"])
            p.setFont(row["mf"])
            y = 8.0
            for part in row["parts"]:
                p.drawText(QPointF(2, y + (row["lh"] - fm.height()) / 2 + fm.ascent()), part)
                y += row["lh"]
            if row["sparts"]:
                y += row["slh"] * 0.25
                sfm = QFontMetricsF(row["sf"])
                p.setFont(row["sf"])
                p.setPen(QColor(255, 255, 255, 215))
                for part in row["sparts"]:
                    p.drawText(QPointF(2, y + (row["slh"] - sfm.height()) / 2 + sfm.ascent()), part)
                    y += row["slh"]
            p.end()
        if len(self._cache) > 240:
            self._cache.clear()
        self._cache[ck] = img
        return img

    def _sweep_mask(self, row, frac):
        """Where the sung words end, as an alpha mask over the row's image:
        whole lines before it lit, the line being sung lit up to a soft edge."""
        fm = QFontMetricsF(row["mf"])
        total = sum(len(x) for x in row["parts"]) + len(row["parts"]) - 1
        sung = frac * total
        bands = []
        seen = 0
        for k, part in enumerate(row["parts"]):
            n = min(max(sung - seen, 0.0), len(part))
            whole = int(n)
            reach = fm.horizontalAdvance(part[:whole])
            if whole < len(part):
                reach += (n - whole) * fm.horizontalAdvance(part[whole])
            top = 8.0 + k * row["lh"]
            bands.append((top, row["lh"], reach + 2, n >= len(part)))
            seen += len(part) + 1
        return bands

    # ---- motion ----
    def _anchor(self):
        return self.height() * self.ANCHOR

    def _goal(self, rows):
        if not rows:
            return 0.0
        if not self.lines:
            return -self.height() * 0.08
        row = rows[max(0, self.index)] if self.index >= 0 else rows[0]
        return row["y"] - self._anchor() + (0 if self.index >= 0 else -self._sizes()[0] * 1.6)

    def _limit(self, value, rows):
        total = (rows[-1]["y"] + rows[-1]["h"]) if rows else 0.0
        return max(-self.height() * 0.6, min(value, total - self.height() * 0.3))

    def _step(self):
        rows = self._layout()
        now = time.monotonic()
        dt = min(0.05, now - self._last_t)
        self._last_t = now
        if self.lines:
            i, _f = lyrics_db.current_line(self.lines, self.now_ms())
            if i != self.index:
                old = self.index
                self.index = i
                # the rows below the new line follow it a beat later each: a cascade
                for k in range(len(rows)):
                    lag = max(0, k - max(i, 0)) if (old < i) else 0
                    self._row_start[k] = now + min(0.32, lag * 0.042)
        if self._manual and now - self._manual_at > self.BACK_AFTER_S:
            self._manual *= 0.88
            if abs(self._manual) < 1:
                self._manual = 0.0
        goal = self._limit(self._goal(rows) + self._manual, rows)
        moving = False
        ease = 1.0 - math.exp(-dt * 7.5)
        for k in range(len(rows)):
            cur = self._row_scroll.get(k)
            if cur is None:
                self._row_scroll[k] = goal
                continue
            if now >= self._row_start.get(k, 0):
                nxt = cur + (goal - cur) * ease
                if abs(goal - nxt) < 0.3:
                    nxt = goal
                self._row_scroll[k] = nxt
            if abs(goal - self._row_scroll[k]) > 0.3:
                moving = True
            want = 1.0 if (self.lines and k == self.index) else 0.0
            e = self._emph.get(k, want)
            e += (want - e) * (1.0 - math.exp(-dt * 9.0))
            self._emph[k] = e
            if abs(want - e) > 0.01:
                moving = True
        self.update()
        if not moving and not self.playing and not self._manual:
            self._timer.stop()

    def wheelEvent(self, event):
        rows = self._layout()
        self._manual -= event.angleDelta().y() * 0.8
        base = self._goal(rows)
        self._manual = self._limit(base + self._manual, rows) - base
        self._manual_at = time.monotonic()
        for k in range(len(rows)):
            self._row_start[k] = 0
        if not self._timer.isActive():
            self._timer.start()
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._last_t = time.monotonic()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rows = None
        self._buf = None

    # ---- drawing ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        self._hits = []
        if self.state != "ok" or not (self.lines or self.plain):
            msg = {"loading": "Finding the words…", "none": "No lyrics for this song",
                   "idle": "Lyrics show here as the song plays"}.get(self.state, "")
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(_font("Latn", max(22.0, min(34.0, w * 0.045))))
            p.drawText(QRectF(0, 0, w, h * 0.6), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
                                                      | Qt.TextFlag.TextWordWrap), msg)
            p.end()
            return
        rows = self._layout()
        q = p
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ms = self.now_ms()
        goal = self._limit(self._goal(rows) + self._manual, rows)
        top_fade, bottom_fade = h * 0.10, h * 0.24
        for k, row in enumerate(rows):
            top = row["y"] - self._row_scroll.get(k, goal) - 8.0
            if top > h or top + row["h"] + 16 < 0:
                continue
            # the column fades at its top and bottom: each line by where it stands
            mid = top + row["h"] / 2 + 8
            edge = min(1.0, max(0.0, mid / top_fade), max(0.0, (h - mid) / bottom_fade))
            if edge <= 0.01:
                continue
            e = self._emph.get(k, 0.0)
            d = abs(k - self.index) if (self.lines and self.index >= 0) else (0 if not self.lines else k + 1)
            scale = 0.955 + 0.045 * e
            q.save()
            q.translate(0, top + row["h"] / 2 + 8)
            q.scale(scale, scale)
            q.translate(0, -(row["h"] / 2 + 8))
            if not self.lines:
                q.setOpacity(0.82 * edge)
                q.drawImage(QPointF(0, 0), self._image(row, 0))
            elif e > 0.02 and k == self.index:
                frac = lyrics_db.sung_fraction(self.lines, k, ms)
                self._edge = edge
                self._draw_sung(q, row, frac, e)
            else:
                level = 0 if d <= 1 else (1 if d <= 3 else 2)
                dim = self.PAST_DIM if (self.lines and k < self.index) else self.DIM
                q.setOpacity((dim + (0.9 - dim) * e) * edge)
                q.drawImage(QPointF(0, 0), self._image(row, level))
            q.restore()
            if row["ms"] is not None:
                self._hits.append((QRectF(0, top, w, row["h"] + 16), row["ms"]))
        self._draw_dots(q, rows, ms, goal)
        p.end()

    def _draw_sung(self, q, row, frac, e):
        """The line being sung: dim where it's still to come, bright (with a
        faint glow) where it's been sung, a soft edge between."""
        base = self._image(row, 0)
        edge = getattr(self, "_edge", 1.0)
        q.setOpacity(self.UNSUNG * edge)
        q.drawImage(QPointF(0, 0), base)
        bands = self._sweep_mask(row, frac)
        dpr = base.devicePixelRatio()
        lit = QImage(base.size(), QImage.Format.Format_ARGB32_Premultiplied)
        lit.setDevicePixelRatio(dpr)
        lit.fill(Qt.GlobalColor.transparent)
        lp = QPainter(lit)
        lp.setOpacity(0.55)
        lp.drawImage(QPointF(0, 0), self._image(row, 1))        # the glow
        lp.setOpacity(1.0)
        lp.drawImage(QPointF(0, 0), base)
        lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        lw = base.width() / dpr
        mask_w = lw
        edge = 26.0
        y_end = 8.0
        for top, hgt, reach, done in bands:
            band = QRectF(0, top - 6, mask_w, hgt + 6)
            g = QLinearGradient(reach - edge, 0, reach + edge * 0.6, 0)
            g.setColorAt(0, QColor(0, 0, 0, 255))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            lp.fillRect(band, QColor(0, 0, 0, 255) if done else g)
            y_end = top + hgt
        # the line under it (its other version) stays as it is: lit with the row
        lp.fillRect(QRectF(0, y_end, mask_w, base.height() / dpr - y_end), QColor(0, 0, 0, int(200 * e)))
        lp.end()
        q.setOpacity(min(1.0, 0.25 + e) * edge)
        q.drawImage(QPointF(0, 0), lit)

    def _draw_dots(self, q, rows, ms, goal):
        """Three dots filling in before the first words, and in a long break."""
        if not self.lines or not rows:
            return
        first = self.lines[0][0]
        where = None
        if ms < first and first > 3500:
            where, frac = rows[0], ms / float(first)
            y = rows[0]["y"] - self._row_scroll.get(0, goal) - self._sizes()[0] * 1.5
        elif 0 <= self.index < len(self.lines) - 1:
            row = rows[self.index]
            sung_end = row["ms"] + max(900, min(row["end"] - row["ms"], 70 * len(self.lines[self.index][1]) + 300))
            gap = self.lines[self.index + 1][0] - sung_end
            if gap > 6500 and ms > sung_end + 800:
                where = row
                frac = (ms - sung_end) / float(gap)
                y = row["y"] - self._row_scroll.get(self.index, goal) + row["h"] + self._sizes()[0] * 0.35
        if where is None:
            return
        px = self._sizes()[0]
        r = px * 0.16
        breathe = 1.0 + 0.08 * math.sin(time.monotonic() * 3.0)
        q.save()
        q.setOpacity(1.0)
        q.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            lit = max(0.0, min(1.0, frac * 3.0 - k))
            q.setBrush(QColor(255, 255, 255, int(70 + 185 * lit)))
            q.drawEllipse(QPointF(r * 1.6 + k * r * 3.4, y + px * 0.5), r * breathe, r * breathe)
        q.restore()

    def mousePressEvent(self, event):
        for rect, ms in self._hits:
            if rect.contains(event.position()):
                self.seek_requested.emit(ms)
                return
