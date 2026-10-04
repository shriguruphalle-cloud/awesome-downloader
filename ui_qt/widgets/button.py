"""The app's button: a QPushButton that paints itself so it can move.

A stylesheet button can only jump between two looks. This one eases: its
fill rises on hover and settles on leave; a press sinks it a pixel and
darkens it; the primary (ember) button catches a band of light across its
face when the pointer arrives and glows softly while it's under it; a
keyboard focus gets the app's sky ring. `set_busy(True)` swaps the label's
start for a small spinner -- "this is working" -- without the button
changing size.

It is a drop-in: same API, same objectName variants as before ("accent",
"quiet", "pill", "danger", "success", "historyPlain", "historyGreen"), and
the stylesheet's padding and font still size it, so layouts don't move.
"""
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QPushButton

from .. import cinema, motion, theme
from ..theme import qcolor


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


def _alpha(color, a):
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * a)))
    return c


class Button(QPushButton):
    SHEEN_MS = 520

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = motion.Fader(self, 150)
        self._press = motion.Fader(self, 90)
        self._busy = False
        self._spin = 0.0
        self._sheen_started = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.pressed.connect(lambda: self._press.to(1))
        self.released.connect(lambda: self._press.to(0))

    # ---- busy ----
    def set_busy(self, busy):
        """A spinner in front of the label while something runs."""
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        self._run_timer()
        self.update()

    # ---- motion ----
    def _run_timer(self):
        need = (self._busy and not motion.reduced()) or self._sheen_started is not None
        if need and not self._timer.isActive():
            self._timer.start()
        elif not need:
            self._timer.stop()

    def _tick(self):
        if self._busy:
            self._spin = (self._spin + 8.0) % 360
        if self._sheen_started is not None and (time.monotonic() - self._sheen_started) * 1000 > self.SHEEN_MS:
            self._sheen_started = None
        self._run_timer()
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        if self.objectName() == "accent" and self.isEnabled() and not motion.reduced():
            self._sheen_started = time.monotonic()
            self._run_timer()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        self.update()

    # ---- paint ----
    def _variant(self):
        name = self.objectName()
        if name in ("accent", "quiet", "pill", "danger", "success"):
            return name
        if name == "historyGreen":
            return "success"
        return "plain"

    def _radius(self, r):
        name = self.objectName()
        if name in ("quiet", "historyPlain", "historyGreen"):
            return min(8.0, r.height() / 2)
        return min(10.0, r.height() / 2)

    def paintEvent(self, event):
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        rad = self._radius(r)
        h = self._hover.value if self.isEnabled() else 0.0
        down = self._press.value if self.isEnabled() else 0.0
        if self.isChecked():
            down = max(down, 0.6)
        variant = self._variant()
        # Busy reads as working, not as unavailable, even while it's
        # disabled to stop a second click.
        enabled = self.isEnabled() or self._busy
        if down:
            r.translate(0, 0.6 * down)

        if variant == "accent" and enabled:
            fg = QColor(t["accent_text"])
            if h > 0.01:                              # soft ember glow while hovered
                glow = QColor(t["accent"])
                for spread, a in ((6.0, 0.05), (3.5, 0.08), (1.5, 0.12)):
                    gr = r.adjusted(-spread, -spread, spread, spread)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_alpha(glow, a * h * (1 - 0.5 * down)))
                    p.drawRoundedRect(gr, rad + spread, rad + spread)
            top = _mix(QColor(t["accent_top"]), QColor(t["accent_hover"]).lighter(110), 0.5 * h)
            bottom = _mix(QColor(t["accent"]), QColor(t["accent_hover"]), 0.6 * h)
            bottom = _mix(bottom, QColor(t["accent_pressed"]), down)
            top = _mix(top, QColor(t["accent_pressed"]), down * 0.8)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.setPen(QPen(_mix(bottom, QColor(0, 0, 0), 0.08), 1))
            p.setBrush(grad)
            p.drawRoundedRect(r, rad, rad)
            # the lit upper edge
            edge = QLinearGradient(r.topLeft(), r.center())
            edge.setColorAt(0, QColor(255, 255, 255, round(70 * (1 - down))))
            edge.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(edge)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad - 1, rad - 1)
            if self._sheen_started is not None:
                k = min(1.0, (time.monotonic() - self._sheen_started) * 1000 / self.SHEEN_MS)
                k = 1 - (1 - k) ** 3
                x = r.left() - r.width() * 0.4 + (r.width() * 1.8) * k
                band = QLinearGradient(QPointF(x - 26, r.top()), QPointF(x + 26, r.bottom()))
                band.setColorAt(0.0, QColor(255, 255, 255, 0))
                band.setColorAt(0.5, QColor(255, 255, 255, 70))
                band.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.save()
                clip = cinema.glass_path(r, rad)
                p.setClipPath(clip)
                p.fillRect(r, band)
                p.restore()
        elif variant in ("pill", "danger", "success"):
            line = QColor({"pill": t["accent"], "danger": t["danger"], "success": t["success"]}[variant])
            if not enabled:
                line = qcolor(t["divider"])
            wash = _alpha(line, (0.10 + 0.08 * down) * h + 0.14 * down)
            p.setPen(QPen(line, 1))
            p.setBrush(wash)
            p.drawRoundedRect(r, rad, rad)
            fg = line if enabled else qcolor(t["text_faint"])
        elif variant == "quiet":
            if h > 0.01 or down > 0.01:
                fill = _mix(_alpha(qcolor(t["hover_overlay"]), h), qcolor(t["pressed_overlay"]), down)
                p.setPen(QPen(_alpha(qcolor(t["card_border"]), h), 1))
                p.setBrush(fill)
                p.drawRoundedRect(r, rad, rad)
            fg = _mix(qcolor(t["text_muted"]), qcolor(t["text"]), h) if enabled else qcolor(t["text_faint"])
        else:  # plain secondary: glass that brightens under the pointer
            if enabled:
                fill = _mix(qcolor(t["hover_overlay"]), qcolor(t["pressed_overlay"]), max(h * 0.85, down))
                border = _mix(qcolor(t["card_border"]), qcolor(t["field_hover"]), h)
                p.setPen(QPen(border, 1))
                p.setBrush(fill)
                p.drawRoundedRect(r, rad, rad)
                top = QLinearGradient(r.topLeft(), r.center())
                top.setColorAt(0, QColor(255, 255, 255, round((22 if dark else 90) * (1 - down))))
                top.setColorAt(1, QColor(255, 255, 255, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(top)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad - 1, rad - 1)
                fg = qcolor(t["text"])
            else:
                p.setPen(QPen(qcolor(t["divider"]), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, rad, rad)
                fg = qcolor(t["text_faint"])

        if not enabled and variant == "accent":
            p.setPen(QPen(qcolor(t["divider"]), 1))
            p.setBrush(qcolor(t["hover_overlay"]))
            p.drawRoundedRect(r, rad, rad)
            fg = qcolor(t["text_faint"])

        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus and self._keyboard_focus:
            ring = qcolor(t["focus"])
            p.setPen(QPen(ring, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), rad + 1.5, rad + 1.5)

        self._paint_label(p, r, fg)
        p.end()

    _keyboard_focus = False

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason,
                                                  Qt.FocusReason.BacktabFocusReason,
                                                  Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(event)

    def _paint_label(self, p, r, fg):
        text = self.text()
        font = self.font()
        fm = QFontMetricsF(font)
        icon = self.icon()
        isz = self.iconSize() if not icon.isNull() else QSize(0, 0)
        lead = 0.0
        if self._busy:
            lead = 14.0 + (6.0 if text else 0.0)
        elif not icon.isNull():
            lead = isz.width() + (6.0 if text else 0.0)
        text_w = fm.horizontalAdvance(text) if text else 0.0
        x = r.center().x() - (lead + text_w) / 2
        cy = r.center().y()
        if self._busy:
            sr = QRectF(x, cy - 7, 14, 14)
            track = QColor(fg)
            track.setAlphaF(0.25)
            p.setPen(QPen(track, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(sr.adjusted(1, 1, -1, -1))
            pen = QPen(fg, 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(sr.adjusted(1, 1, -1, -1), int(-self._spin * 16), int(100 * 16))
        elif not icon.isNull():
            mode = icon.Mode.Normal if self.isEnabled() else icon.Mode.Disabled
            pm = icon.pixmap(isz, self.devicePixelRatioF(), mode)
            p.drawPixmap(QPointF(x, cy - isz.height() / 2), pm)
        if text:
            p.setFont(font)
            p.setPen(fg)
            p.drawText(QRectF(x + lead, r.top(), text_w + 2, r.height()),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
