"""Motion for the whole UI: one switch (Settings > Appearance > Reduce
motion) and two small helpers every painted widget can use.

Durations are short on purpose. An animation here exists to show where
something came from or went (a tab growing out of the + button, a panel
dropping from the button that opened it, a progress bar that eases rather
than jumps) -- not to decorate. 120-220 ms reads as responsive; anything
longer starts to read as waiting.
"""
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QVariantAnimation

_reduce = False

FAST = 120      # hover, press
MEDIUM = 180    # things moving into place
SLOW = 260      # panels, whole-page changes


def set_reduce_motion(on):
    global _reduce
    _reduce = bool(on)


def reduced():
    return _reduce


def tween(owner, start, end, ms, on_value, on_done=None, curve=QEasingCurve.Type.OutCubic):
    """Calls on_value(v) for v running start -> end over `ms`, then on_done().
    With reduced motion it jumps straight to the end. Returns the animation
    (parented to `owner`, deleted when it stops) or None."""
    if _reduce or ms <= 0 or start == end:
        on_value(float(end))
        if on_done:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(int(ms))
    anim.setEasingCurve(QEasingCurve(curve))
    anim.valueChanged.connect(lambda v: on_value(float(v)))
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class Fader(QObject):
    """A 0..1 value that eases toward a target and repaints its widget on
    every step -- hover glows, focus rings, a toggle's knob."""

    def __init__(self, widget, ms=FAST, value=0.0):
        super().__init__(widget)
        self._widget = widget
        self._ms = ms
        self.value = float(value)
        self._target = float(value)
        self._anim = None

    def to(self, target):
        target = float(target)
        if target == self._target:
            return
        self._target = target
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        # Time scales with the distance left, so reversing halfway through a
        # hover doesn't take a full duration.
        ms = int(self._ms * abs(target - self.value))
        self._anim = tween(self, self.value, target, ms, self._set, self._done)

    def snap(self, value):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._target = self.value = float(value)
        self._widget.update()

    def _set(self, v):
        self.value = v
        self._widget.update()

    def _done(self):
        self._anim = None


def lerp(a, b, t):
    return a + (b - a) * t


_UNLIMITED = 16777215


def grow_in(widget, ms=MEDIUM):
    """A row just added to a list opens to its height instead of appearing
    at full size -- the eye catches where it arrived. Works on fixed-height
    rows too: their height limits are lifted for the opening and put back."""
    if _reduce:
        return
    min_h, max_h = widget.minimumHeight(), widget.maximumHeight()
    target = max(1, widget.sizeHint().height() if max_h >= _UNLIMITED else max_h)
    widget.setMinimumHeight(0)
    widget.setMaximumHeight(0)

    def done():
        widget.setMinimumHeight(min_h)
        widget.setMaximumHeight(max_h)
    tween(widget, 0, target, ms, lambda v: widget.setMaximumHeight(round(v)), done)


def shrink_out(widget, on_done, ms=MEDIUM):
    """Folds a row away, then calls on_done() (which removes it). The rows
    below slide up to close the gap rather than jumping."""
    if _reduce or not widget.isVisible():
        on_done()
        return
    widget.setEnabled(False)
    widget.setMinimumHeight(0)
    tween(widget, widget.height(), 0, ms, lambda v: widget.setMaximumHeight(round(v)), on_done,
          curve=QEasingCurve.Type.InOutCubic)
