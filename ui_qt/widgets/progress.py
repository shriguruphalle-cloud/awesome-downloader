"""Progress bar: a beam of the action colour with a light at its tip.

Painted directly rather than styled through QSS. QSS can colour a
QProgressBar chunk but can't animate one, and rewriting the stylesheet every
frame would re-parse the QSS on each tick for every visible row -- the kind
of cost that shows up as jank once a few torrents are running.

A custom paintEvent gives the moving highlight for one repaint per frame,
and only while the bar is actually animating: the shared timer stops itself
as soon as no bar on screen still wants it, so an idle window costs nothing.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QProgressBar

from .. import motion

# One timer shared by every bar, so the shimmer stays in phase across rows.
_TICK_MS = 33  # ~30fps
_timer = None
_animated = set()
# Settings > Appearance > Reduce motion: the beam still fills, but the band
# of light no longer travels along it.
_reduce_motion = False


def set_reduce_motion(on):
    global _reduce_motion
    _reduce_motion = bool(on)
    if _reduce_motion:
        for bar in list(_animated):
            try:
                bar.update()
            except RuntimeError:
                _animated.discard(bar)


def _ensure_timer():
    global _timer
    if _timer is None:
        _timer = QTimer()
        _timer.setInterval(_TICK_MS)
        _timer.timeout.connect(_on_tick)
    if not _timer.isActive():
        _timer.start()


def _on_tick():
    if _reduce_motion:
        return
    for bar in list(_animated):
        # RuntimeError is what PySide raises once the C++ object is gone,
        # which happens when a row is removed.
        try:
            if not bar.isVisible():
                continue
            bar._phase = (bar._phase + 0.018) % 1.0
            bar.update()
        except RuntimeError:
            _animated.discard(bar)
    if not _animated and _timer is not None:
        _timer.stop()


class AnimatedProgressBar(QProgressBar):
    """Rounded progress bar; set_complete(True) switches it to the finished
    colour and stops the animation."""

    HEIGHT = 6

    def __init__(self, track_color, fill_color, complete_color, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setFixedHeight(self.HEIGHT)
        self._track = QColor(track_color)
        self._fill = QColor(fill_color)
        self._complete_color = QColor(complete_color)
        self._complete = False
        self._animating = False
        self._phase = 0.0
        # What's drawn eases toward value(): a jump from 12% to 40% slides
        # rather than snapping, so a bar reads as motion, not as a counter.
        self._shown = None
        self._ease = None
        # A single sweep of light along the whole bar the moment it completes.
        self._sweep = -1.0

    def setValue(self, value):  # noqa: N802 -- Qt's name
        super().setValue(value)
        target = float(self.value())
        if self._shown is None or motion.reduced() or not self.isVisible() or target < self._shown:
            if self._ease is not None:
                self._ease.stop()
                self._ease = None
            self._shown = target
            self.update()
            return
        if self._ease is not None:
            self._ease.stop()
        self._ease = motion.tween(self, self._shown, target, 420, self._set_shown, self._eased)

    def _set_shown(self, v):
        self._shown = v
        self.update()

    def _eased(self):
        self._ease = None

    def _set_sweep(self, v):
        self._sweep = v
        self.update()

    def set_colors(self, track_color, fill_color, complete_color):
        """Re-themed on a light/dark switch."""
        self._track = QColor(track_color)
        self._fill = QColor(fill_color)
        self._complete_color = QColor(complete_color)
        self.update()

    def set_complete(self, complete):
        if complete == self._complete:
            return
        self._complete = complete
        if complete and not motion.reduced() and self.isVisible():
            motion.tween(self, 0.0, 1.0, 900, self._set_sweep, lambda: self._set_sweep(-1.0))
        # Nothing is still arriving once it's done; a bar that kept
        # shimmering would be actively misleading.
        self.set_animating(not complete and self._animating)
        self.update()

    def set_animating(self, animating):
        animating = bool(animating) and not self._complete
        if animating == self._animating:
            return
        self._animating = animating
        if animating:
            _animated.add(self)
            _ensure_timer()
        else:
            _animated.discard(self)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._track)
        painter.drawRoundedRect(rect, radius, radius)

        span = self.maximum() - self.minimum()
        if span <= 0:
            painter.end()
            return
        shown = self._shown if self._shown is not None else float(self.value())
        ratio = max(0.0, min(1.0, (shown - self.minimum()) / span))
        if ratio <= 0:
            painter.end()
            return

        fill = QRectF(rect)
        fill.setWidth(max(rect.height(), rect.width() * ratio))
        base = self._complete_color if self._complete else self._fill

        # The beam: deeper where it started, brightest at the tip.
        beam = QLinearGradient(fill.topLeft(), fill.topRight())
        tail = QColor(base)
        tail.setAlpha(150)
        beam.setColorAt(0.0, tail)
        beam.setColorAt(0.7, base)
        beam.setColorAt(1.0, base.lighter(118))
        path = QPainterPath()
        path.addRoundedRect(fill, radius, radius)
        painter.setClipPath(path)
        painter.setBrush(QBrush(beam))
        painter.drawRoundedRect(fill, radius, radius)

        if self._animating and fill.width() > 0 and not _reduce_motion:
            # A soft band of light travelling along the beam.
            highlight = QColor(255, 255, 255, 110)
            mid = QColor(255, 255, 255, 34)
            transparent = QColor(255, 255, 255, 0)
            band = max(60.0, fill.width() * 0.35)
            start = -band + (fill.width() + band) * self._phase
            grad = QLinearGradient(start, 0, start + band, 0)
            grad.setColorAt(0.0, transparent)
            grad.setColorAt(0.35, mid)
            grad.setColorAt(0.5, highlight)
            grad.setColorAt(0.65, mid)
            grad.setColorAt(1.0, transparent)
            painter.setBrush(QBrush(grad))
            painter.drawRect(fill)
        if self._sweep >= 0.0:
            # Finished: one band of light runs the full length and is gone.
            band = max(80.0, rect.width() * 0.3)
            start = -band + (rect.width() + band) * self._sweep
            grad = QLinearGradient(start, 0, start + band, 0)
            grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            grad.setColorAt(0.5, QColor(255, 255, 255, 170))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(grad))
            painter.drawRect(fill)
        painter.setClipping(False)

        if not self._complete and ratio < 1.0:
            # The tip light: a small flare at the leading edge, flattened by
            # the bar's own height into a streak -- the backdrop's anamorphic
            # light, in miniature.
            tip = QPointF(fill.right() - radius * 0.6, rect.center().y())
            glow = QRadialGradient(tip, rect.height() * 2.6)
            hot = QColor(base.lighter(135))
            hot.setAlpha(200)
            clear = QColor(base)
            clear.setAlpha(0)
            glow.setColorAt(0.0, hot)
            glow.setColorAt(1.0, clear)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(tip, rect.height() * 2.6, rect.height() * 2.6)

        painter.end()
