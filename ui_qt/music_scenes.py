"""Backgrounds drawn as crisp vector art (QPainter) rather than by the shader: thin lines, type, dots.

  Synthesis -- after the person's own motion piece ("Inversion"): a black page, thin grey curves sweeping
      in from the edges, dotted rails down each side, the playing song's title set in wide-spaced
      capitals in rows whose letters drop out and reshuffle on the beat, a web of straight lines in the
      cover's colour that snaps into a new shape on each kick, and a dotted ring with small glyphs
      orbiting it that scatter on the hats.

render(...) returns a QImage the size asked for; the full-screen player shows it like any other frame.
"""
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

GLYPHS = "0123456789@&%#$*+=?§"


def _mix(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _vivid(c, floor=0.55):
    """The cover's colour made bright enough to read as a thin line on black."""
    c = QColor(c)
    h, s, v, _a = c.getHsvF()
    return QColor.fromHsvF(max(0.0, h), max(0.55, s), max(floor, v))


class Synthesis:
    """One instance per player: it keeps the web's shape and eases it toward the next on each kick."""

    def __init__(self):
        self.kicks = 0
        self._was = 0.0
        self.web = None          # [(x, y)] current anchor points, 0..1
        self.target = None
        self.shuffle = 0
        self._hat_was = 0.0
        self.scatter = 0.0

    def _new_web(self, seed):
        """A fan: one or two meeting points, lines out to a horizontal band of points (as in the piece)."""
        rnd = random.Random(seed)
        band = rnd.uniform(0.44, 0.60)
        mode = seed % 3
        if mode == 0:            # up from below the band
            hubs = [(0.5 + rnd.uniform(-0.06, 0.06), band + rnd.uniform(0.14, 0.24))]
        elif mode == 1:          # in from one side
            side = rnd.choice((0.06, 0.94))
            hubs = [(side, band + rnd.uniform(-0.10, 0.10)), (side, band + rnd.uniform(-0.04, 0.04))]
        else:                    # two hubs crossing
            hubs = [(0.42, band + 0.16), (0.58, band + 0.20)]
        lo, hi_ = rnd.uniform(0.15, 0.30), rnd.uniform(0.70, 0.88)
        ends = [(lo + (hi_ - lo) * i / 15.0 + rnd.uniform(-0.02, 0.02), band + rnd.uniform(-0.025, 0.025)) for i in range(16)]
        return hubs + ends + [hubs[0]] * (2 - len(hubs))      # always 18 points

    def render(self, w, h, t, beat, mid, hi, pal, title="", mono=False):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        cover = pal.get("cover") or {}
        accent = _vivid(cover.get("hot") or pal.get("accent", QColor(80, 200, 90)))
        second = _vivid(cover.get("second") or pal.get("glow", QColor(110, 70, 220)), 0.6)
        if mono:
            accent, second = QColor(235, 235, 235), QColor(150, 150, 150)
        grey = QColor(140, 140, 140)
        k = min(w, h) / 720.0

        # a kick: the web snaps to a new shape, the type reshuffles
        if beat > 0.6 and self._was <= 0.6:
            self.kicks += 1
            self.target = self._new_web(self.kicks)
            if self.kicks % 2 == 0:
                self.shuffle += 1
        self._was = beat
        if hi > 0.6 and self._hat_was <= 0.6:
            self.scatter = 1.0
        self._hat_was = hi
        self.scatter *= 0.90
        if self.web is None:
            self.web = self._new_web(0)
            self.target = self.web
        self.web = [(a[0] + (b[0] - a[0]) * 0.22, a[1] + (b[1] - a[1]) * 0.22) for a, b in zip(self.web, self.target)]

        cx, cy = w * 0.5, h * 0.5
        # thin curves sweeping in from the edges
        p.setPen(QPen(QColor(grey.red(), grey.green(), grey.blue(), 70), max(0.6, 0.8 * k)))
        for i in range(14):
            side = -1 if i % 2 else 1
            x0 = w * (0.5 + side * (0.48 - 0.03 * (i // 2)))
            y0 = 0 if i % 4 < 2 else h
            sway = math.sin(t * 0.25 + i) * 40 * k
            path = QPainterPath(QPointF(x0, y0))
            path.cubicTo(QPointF(x0 - side * 30 * k, (y0 + cy) * 0.5), QPointF(cx + side * (60 * k + sway), cy * (0.8 + 0.1 * (i % 3))),
                         QPointF(cx + side * 8 * k * (i % 5), cy))
            p.drawPath(path)
        # dotted rails
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(200, 200, 200, 150))
        for y in range(int(30 * k), h, int(max(14, 34 * k))):
            for x in (w * 0.085, w * 0.915):
                p.drawEllipse(QPointF(x, y), 1.1 * k, 1.1 * k)

        # the title, wide-spaced, in rows; letters drop out and reshuffle
        word = "".join(ch for ch in (title or "AWESOME MUSIC").upper() if ch.isalnum() or ch == " ").strip() or "MUSIC"
        word = word.replace(" ", "")[:14]
        f = QFont("Segoe UI")
        f.setPixelSize(max(9, int(17 * k)))
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
        p.setFont(f)
        fm = QFontMetricsF(f)
        left, right = w * 0.09, w * 0.91
        line = (word + " ") * 6
        for band_i, by in enumerate((0.08, 0.30, 0.62, 0.90)):
            for row in range(3 if band_i in (1, 2) else 2):
                y = h * by + row * fm.height() * 1.15
                rnd = random.Random(band_i * 97 + row * 13 + self.shuffle * 31)
                tight = rnd.random() < 0.35            # some rows set close, the rest spread across the page
                gap = fm.horizontalAdvance("M") * (1.05 if tight else 2.6)
                x = left + (rnd.uniform(0, (right - left) * 0.5) if tight else 0)
                drop = rnd.uniform(0.0, 0.55)
                for j, ch in enumerate(line):
                    if x > right:
                        break
                    if ch != " " and rnd.random() > drop:
                        p.setPen(QColor(215, 215, 215, 205 if tight else 160))
                        p.drawText(QPointF(x, y), ch)
                    x += gap

        # the web: from each meeting point out to every point on the band, and along the band
        pts = [QPointF(x * w, y * h) for x, y in self.web]
        hubs, ends = pts[:2], pts[2:]
        col = QColor(accent)
        col.setAlpha(int(120 + 120 * beat))
        p.setPen(QPen(col, max(0.6, (0.6 + 0.7 * beat) * k)))
        for hb in hubs:
            for e in ends:
                p.drawLine(hb, e)
        for i in range(len(ends) - 1):
            p.drawLine(ends[i], ends[i + 1])
            if i + 3 < len(ends):
                p.drawLine(ends[i], ends[i + 3])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(second)
        for a in ends[::2]:
            p.drawEllipse(a, 1.8 * k, 1.8 * k)

        # the dotted ring, and glyphs orbiting it
        r = min(w, h) * (0.17 + 0.02 * mid)
        rc = QPointF(cx, cy - h * 0.01)
        ring = QColor(second)
        ring.setAlpha(220)
        p.setBrush(ring)
        for i in range(72):
            a = i / 72.0 * math.tau
            p.drawEllipse(QPointF(rc.x() + math.cos(a) * r, rc.y() + math.sin(a) * r), 1.4 * k, 1.4 * k)
        gf = QFont("Segoe UI")
        gf.setPixelSize(max(8, int(14 * k)))
        gf.setBold(True)
        p.setFont(gf)
        for i in range(16):
            rr = random.Random(i * 13)
            a = rr.uniform(0, math.tau) + t * (0.15 + 0.25 * rr.random()) * (1 if i % 2 else -1)
            out = r * (1.0 + rr.uniform(-0.25, 0.45) + self.scatter * rr.uniform(0.3, 1.2))
            pos = QPointF(rc.x() + math.cos(a) * out, rc.y() + math.sin(a) * out)
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(pos, GLYPHS[(i + self.shuffle) % len(GLYPHS)])
        p.end()
        return img
