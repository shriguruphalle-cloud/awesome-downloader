"""Progress bar with a sweeping highlight while a download is running.

Painted directly rather than styled through QSS. QSS can colour a
QProgressBar chunk, but it can't animate one -- there's no way to move a
gradient stop over time from a stylesheet -- and driving it by rewriting the
stylesheet every frame would re-parse the QSS on each tick for every visible
row, which is exactly the kind of cost that shows up as jank once a few
torrents are going at once.

A custom paintEvent gives the moving highlight for the price of one repaint
per frame, and only while the bar is actually animating: the shared timer
below stops itself as soon as no bar on screen still wants it, so a finished
or idle window costs nothing.
"""
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QProgressBar

# One timer shared by every bar in the process. N bars each with their own
# QTimer would mean N wakeups per frame and N independent phases, so the
# shimmer would visibly drift out of sync between rows.
_TICK_MS = 33  # ~30fps: smooth enough to read as motion, cheap enough to ignore
_timer = None
_animated = set()


def _ensure_timer():
    global _timer
    if _timer is None:
        _timer = QTimer()
        _timer.setInterval(_TICK_MS)
        _timer.timeout.connect(_on_tick)
    if not _timer.isActive():
        _timer.start()


def _on_tick():
    for bar in list(_animated):
        # A bar whose widget has been destroyed or hidden shouldn't keep the
        # timer alive; RuntimeError is what PySide raises once the underlying
        # C++ object is gone, which happens when a torrent row is removed.
        try:
            if not bar.isVisible():
                continue
            bar._phase = (bar._phase + 0.022) % 1.0
            bar.update()
        except RuntimeError:
            _animated.discard(bar)
    if not _animated and _timer is not None:
        _timer.stop()


class AnimatedProgressBar(QProgressBar):
    """Rounded progress bar; call set_complete(True) to switch it to the
    finished colour and stop the animation."""

    def __init__(self, track_color, fill_color, complete_color, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        # 30% thinner than the original 10px -- a download bar is an
        # instrument readout, not a UI element that needs presence.
        self.setFixedHeight(7)
        self._track = QColor(track_color)
        self._fill = QColor(fill_color)
        self._complete_color = QColor(complete_color)
        self._complete = False
        self._animating = False
        self._phase = 0.0

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
        # Nothing is still arriving once it's done, so a bar that keeps
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
        ratio = max(0.0, min(1.0, (self.value() - self.minimum()) / span))
        if ratio <= 0:
            painter.end()
            return

        fill = QRectF(rect)
        fill.setWidth(rect.width() * ratio)
        base = self._complete_color if self._complete else self._fill

        # Clip to the rounded fill so the highlight can't spill past the cap.
        path = QPainterPath()
        path.addRoundedRect(fill, radius, radius)
        painter.setClipPath(path)
        painter.setBrush(base)
        painter.drawRoundedRect(fill, radius, radius)

        if self._animating and fill.width() > 0:
            # A soft band, lighter than the fill, travelling left to right.
            # Drawn over the fill (already clipped to it) so it reads as
            # light moving through the bar rather than a separate shape.
            highlight = QColor(255, 255, 255, 115)
            mid = QColor(255, 255, 255, 38)
            transparent = QColor(255, 255, 255, 0)
            band = max(70.0, fill.width() * 0.40)
            start = -band + (fill.width() + band) * self._phase
            grad = QLinearGradient(start, 0, start + band, 0)
            grad.setColorAt(0.0, transparent)
            grad.setColorAt(0.35, mid)
            grad.setColorAt(0.5, highlight)
            grad.setColorAt(0.65, mid)
            grad.setColorAt(1.0, transparent)
            painter.setBrush(QBrush(grad))
            painter.drawRect(fill)

            # Leading-edge cap: a small bright nib riding the front of the
            # fill. At 7px tall the travelling band alone reads as a faint
            # wash, so the nib is what actually signals "this is moving".
            nib_w = 2.0
            nib = QRectF(max(fill.left(), fill.right() - nib_w), fill.top(),
                         min(nib_w, fill.width()), fill.height())
            painter.setBrush(QColor(255, 255, 255, 150))
            painter.drawRect(nib)

        painter.end()
