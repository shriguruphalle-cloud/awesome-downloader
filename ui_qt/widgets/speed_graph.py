"""A torrent's speed over the last minute, drawn into its card.

The torrent's own card carries the graph in its glass, behind everything on
it, the way the first version ran its graph behind the row: download as a
faint line with a soft fill under it, ending in a small dot at "now"; upload
as little spikes, one for each second's rate, that rise out of the floor as
they come in at the right edge (a plain dotted line read as lifeless --
asked for spikes). Everything slides by continuously. It is a shape to read at
a glance -- speeding up, stalling, done -- with the exact numbers in the row's
own columns on top of it. (A graph in a box of its own beside the numbers
was reported as ugly; this is the background it asked for.)

The scale only ever tops out at a round number (100 KB/s, 2 MB/s, 5 MB/s ...)
and never below 100 KB/s: the very first graph stretched whatever it had to
full height, so a few bytes a second drew the same mountains as megabytes
and read as noise (reported).

Every card shares one slow clock (12 frames a second), and it runs only
while a card on screen has something moving: an idle or hidden tab draws
nothing at all.
"""
import math
import time
from collections import deque

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

from .. import cinema, motion, theme
from .card import SHADOW, _PaintedCard

WINDOW_S = 60.0
KEEP_S = WINDOW_S + 8
LAG_S = 1.0             # a new sample slides in from the right over one poll
FLOOR = 100 * 1024      # the scale's least peak
KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3
IDLE_BELOW = 512        # under 0.5 KB/s throughout is "nothing moving"
TOP_GAP = 36            # the graph starts under the card's title line
SPIKE_GROW_S = 0.5      # how long a new upload spike takes to rise


def nice_top(value, floor=FLOOR):
    """The round rate at or above `value` that the scale tops out at."""
    value = max(float(value), floor)
    base = GB if value >= GB else MB if value >= MB else KB
    x = value / base
    k = 10 ** math.floor(math.log10(x))
    top = 10 * k
    for m in (1, 2, 4, 5, 8, 10):
        if m * k >= x - 1e-9:
            top = m * k
            break
    if top >= 1000 and base < GB:
        return float(base * 1024)     # "1 MB/s", not "1000 KB/s"
    return top * base


def rate_label(value):
    """A scale label: '512 KB/s', '2 MB/s', '2.5 MB/s'."""
    for base, unit in ((GB, "GB"), (MB, "MB"), (KB, "KB")):
        if value >= base:
            return "%s %s/s" % (("%.2f" % (value / base)).rstrip("0").rstrip("."), unit)
    return "%d B/s" % value


def smooth_path(points):
    """A curve through `points` that never overshoots between them -- a
    monotone cubic (Fritsch-Carlson). A plain spline bulges past its points,
    and a speed curve dipping below zero, or above a peak, says something
    that never happened."""
    path = QPainterPath()
    n = len(points)
    if not n:
        return path
    path.moveTo(points[0])
    if n == 1:
        return path
    xs = [pt.x() for pt in points]
    ys = [pt.y() for pt in points]
    d = []
    for i in range(n - 1):
        h = xs[i + 1] - xs[i]
        d.append((ys[i + 1] - ys[i]) / h if h else 0.0)
    m = [d[0]] + [0.0] * (n - 2) + [d[-1]]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            tau = 3 / math.sqrt(s)
            m[i], m[i + 1] = tau * a * d[i], tau * b * d[i]
    for i in range(n - 1):
        h = (xs[i + 1] - xs[i]) / 3
        path.cubicTo(QPointF(xs[i] + h, ys[i] + m[i] * h),
                     QPointF(xs[i + 1] - h, ys[i + 1] - m[i + 1] * h), points[i + 1])
    return path


class SpeedTrace:
    """The last minute of one torrent's speeds, and the scale they're drawn
    on (eased toward its round target, so a new peak doesn't jolt)."""

    def __init__(self):
        self.samples = deque()        # (monotonic time, down B/s, up B/s)
        self.top = nice_top(0)

    def push(self, down, up, now=None):
        now = time.monotonic() if now is None else now
        self.samples.append((now, max(0.0, float(down)), max(0.0, float(up))))
        while self.samples and now - self.samples[0][0] > KEEP_S:
            self.samples.popleft()

    def _recent(self, now):
        return [s for s in self.samples if now - s[0] <= WINDOW_S + LAG_S + 2]

    def target_top(self, now=None):
        now = time.monotonic() if now is None else now
        peak = max((max(d, u) for _, d, u in self._recent(now)), default=0.0)
        return nice_top(peak * 1.15)

    def is_idle(self, now=None):
        now = time.monotonic() if now is None else now
        return all(max(d, u) < IDLE_BELOW for _, d, u in self._recent(now))

    def wants_frames(self, now=None):
        now = time.monotonic() if now is None else now
        return not self.is_idle(now) or abs(self.top - self.target_top(now)) > self.top * 0.004

    def ease_scale(self, now=None):
        target = self.target_top(now)
        self.top += (target - self.top) * 0.22
        if abs(target - self.top) < target * 0.003:
            self.top = target

    def snap_scale(self, now=None):
        self.top = self.target_top(now)

    @staticmethod
    def x_of(t, now, area):
        return area.right() - (now - LAG_S - t) / WINDOW_S * area.width()

    def series(self, now, area):
        """Each line's points across `area`, (x, bytes a second), eased a
        little: a torrent's speed jumps about from one second to the next,
        and drawn raw that is a saw blade."""
        samples = [s for s in self.samples
                   if area.left() - 40 <= self.x_of(s[0], now, area) <= area.right() + 40]
        xs = [self.x_of(s[0], now, area) for s in samples]
        out = []
        for k in (1, 2):
            vals = [s[k] for s in samples]
            eased = []
            for i in range(len(vals)):
                acc = weight = 0.0
                for j, w in enumerate((1, 2, 3, 2, 1)):
                    idx = i + j - 2
                    if 0 <= idx < len(vals):
                        acc += vals[idx] * w
                        weight += w
                eased.append(acc / weight)
            out.append(list(zip(xs, eased)))
        return out

    @staticmethod
    def value_at_x(points, x):
        if not points or x < points[0][0]:
            return None
        for (x0, v0), (x1, v1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                return v0 + (v1 - v0) * ((x - x0) / (x1 - x0) if x1 > x0 else 1.0)
        return points[-1][1]


def paint_trace(p, area, trace, now, dark):
    """The speed lines in `area`, faint enough for anything on top to read."""
    if trace.is_idle(now):
        return
    series = trace.series(now, area)
    if len(series[0]) < 2:
        return
    t = theme.tokens(dark)
    down_c, up_c = QColor(t["brand"]), QColor(t["success"])
    top = max(trace.top, 1.0)

    def y_of(v):
        return area.bottom() - min(v / top, 1.04) * area.height()

    dpts = [QPointF(x, y_of(v)) for x, v in series[0]]
    if any(v >= IDLE_BELOW for _, v in series[0]):
        curve = smooth_path(dpts)
        fill = QPainterPath(curve)
        fill.lineTo(dpts[-1].x(), area.bottom() + 2)
        fill.lineTo(dpts[0].x(), area.bottom() + 2)
        fill.closeSubpath()
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        hi, lo = QColor(down_c), QColor(down_c)
        hi.setAlphaF(0.20 if dark else 0.16)
        lo.setAlphaF(0.0)
        grad.setColorAt(0, hi)
        grad.setColorAt(1, lo)
        p.fillPath(fill, grad)
        line = QColor(down_c)
        line.setAlphaF(0.55 if dark else 0.6)
        pen = QPen(line, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(curve, pen)
    spikes = upload_spikes(area, trace, now, y_of)
    if not spikes.isEmpty():
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        tip, foot = QColor(up_c), QColor(up_c)
        tip.setAlphaF(0.75 if dark else 0.8)
        foot.setAlphaF(0.32 if dark else 0.34)
        grad.setColorAt(0, tip)
        grad.setColorAt(1, foot)
        p.fillPath(spikes, grad)
    # Where download is now: a small dot at the right edge, breathing.
    pulse = 0.5 + 0.5 * math.sin(now * 2 * math.pi / 1.8)
    v = trace.value_at_x(series[0], area.right())
    if v is not None and v >= IDLE_BELOW:
        c = QPointF(area.right(), y_of(v))
        halo = QColor(down_c)
        halo.setAlphaF(0.10 + 0.10 * pulse)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, 5.5 + 1.2 * pulse, 5.5 + 1.2 * pulse)
        dot = QColor(down_c)
        dot.setAlphaF(0.85)
        p.setBrush(dot)
        p.drawEllipse(c, 2.6, 2.6)


def upload_spikes(area, trace, now, y_of):
    """Upload as a row of slim spikes: one per sample (a second apart), as
    tall as that second's rate, on the same scale as download. Raw rates,
    not eased -- a spike is meant to be spiky. Each one rises out of the
    floor over SPIKE_GROW_S as it comes in at the right edge."""
    path = QPainterPath()
    spacing = area.width() / WINDOW_S            # px between seconds
    half = max(0.9, min(2.2, spacing * 0.17))    # half the spike's base
    floor = area.bottom() + 1
    for t, _down, up in trace.samples:
        if up < IDLE_BELOW:
            continue
        x = trace.x_of(t, now, area)
        if not (area.left() - half <= x <= area.right() + half):
            continue
        age = now - LAG_S - t                    # 0 at the right edge
        if age <= 0:
            continue
        rise = min(1.0, age / SPIKE_GROW_S)
        rise = 1.0 - (1.0 - rise) ** 3           # ease out
        tip = floor - (floor - y_of(up)) * rise
        if floor - tip < 1.0:
            continue
        path.moveTo(x - half, floor)
        path.lineTo(x, tip)
        path.lineTo(x + half, floor)
        path.closeSubpath()
    return path


class _Clock(QObject):
    """The one timer every graph shares."""

    FRAME_MS = 83

    def __init__(self):
        super().__init__()
        self.cards = set()
        self.timer = QTimer(self)
        self.timer.setInterval(self.FRAME_MS)
        self.timer.timeout.connect(self._tick)

    def kick(self):
        if not self.timer.isActive() and not motion.reduced():
            self.timer.start()

    def _tick(self):
        busy = False
        for card in list(self.cards):
            try:
                if card.isVisible() and card.trace.wants_frames():
                    card.frame()
                    busy = True
            except RuntimeError:         # the widget is gone
                self.cards.discard(card)
        if not busy:
            self.timer.stop()


_clock = None


def clock():
    global _clock
    if _clock is None:
        _clock = _Clock()
    return _clock


class GraphCard(_PaintedCard):
    """A torrent's card, its speed over the last minute drawn in its glass
    behind everything on it."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.trace = SpeedTrace()
        clock().cards.add(self)

    def push(self, down, up, now=None):
        self.trace.push(down, up, now)
        if motion.reduced() or not clock().timer.isActive():
            self.trace.snap_scale(now)
        if self.isVisible():
            self.update()
            if self.trace.wants_frames():
                clock().kick()

    def frame(self):
        self.trace.ease_scale()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.trace.snap_scale()
        if self.trace.wants_frames():
            clock().kick()

    def graph_area(self):
        glass = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5 - SHADOW)
        return QRectF(glass.left() + 2, glass.top() + TOP_GAP, glass.width() - 4, glass.height() - TOP_GAP - 3)

    def paintEvent(self, event):
        super().paintEvent(event)            # the glass
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glass = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5 - SHADOW)
        clip = QPainterPath()
        clip.addRoundedRect(glass, self._radius - 1, self._radius - 1)
        p.setClipPath(clip)
        paint_trace(p, self.graph_area(), self.trace, time.monotonic(), cinema.is_dark(self))
        p.end()
