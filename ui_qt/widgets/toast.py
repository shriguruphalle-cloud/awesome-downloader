"""A short message that rises into view at the bottom of part of the window
and fades a few seconds later -- "Downloading report.pdf", "Pop-up blocked".
It can carry one action ("Show", "Open").

A top-level tool window rather than a child widget, so it can sit over a web
page (a native window that would otherwise cover anything Qt paints).
One toast per area: a new one replaces the one already showing.
"""
from PySide6.QtCore import QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .. import cinema, motion, theme
from ..theme import qcolor

_showing = {}


class Toast(QWidget):
    action_clicked = Signal()
    MARGIN = 12

    def __init__(self, anchor, text, action=None, kind="info", ms=3600):
        super().__init__(anchor.window(), Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._anchor = anchor
        self._dark = cinema.is_dark(anchor)
        self._t = theme.tokens(self._dark)
        self._kind = kind
        self._ms = ms
        t = self._t
        m = self.MARGIN
        lay = QHBoxLayout(self)
        lay.setContentsMargins(m + 30, m + 9, m + (8 if action else 16), m + 9)
        lay.setSpacing(12)
        label = QLabel(text)
        label.setStyleSheet(f"color: {t['text']}; font-size: 12px; font-weight: 500; background: transparent;")
        lay.addWidget(label)
        if action:
            btn = QPushButton(action)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {t['brand']}; border: none;"
                f" border-radius: 8px; padding: 4px 10px; font-size: 12px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: {t['hover_overlay']}; }}")
            btn.clicked.connect(self._on_action)
            lay.addWidget(btn)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def _on_action(self):
        self.action_clicked.emit()
        self.dismiss()

    def popup(self):
        old = _showing.get(id(self._anchor))
        if old is not None and old is not self:
            try:
                old.close()
            except RuntimeError:
                pass
        _showing[id(self._anchor)] = self
        self.adjustSize()
        area = self._anchor.rect()
        bottom_centre = self._anchor.mapToGlobal(QPoint(area.center().x(), area.bottom()))
        x = bottom_centre.x() - self.width() // 2
        y = bottom_centre.y() - self.height() - 18 + self.MARGIN
        self.move(x, y + 10)
        self.setWindowOpacity(0.0)
        self.show()
        motion.tween(self, 0.0, 1.0, motion.SLOW, lambda v: (
            self.setWindowOpacity(v), self.move(x, round(y + 10 * (1 - v)))))
        self._timer.start(self._ms)

    def dismiss(self):
        if _showing.get(id(self._anchor)) is self:
            _showing.pop(id(self._anchor), None)
        motion.tween(self, self.windowOpacity(), 0.0, motion.MEDIUM, self.setWindowOpacity, self.close)

    def enterEvent(self, event):
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._timer.start(1800)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t, dark = self._t, self._dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.MARGIN
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        rad = r.height() / 2
        for i in range(m, 0, -2):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, round((5 if dark else 3) * (m - i + 2) / 2)))
            p.drawRoundedRect(r.adjusted(-i + 2, -i + 4, i - 2, i), rad + i / 2, rad + i / 2)
        p.setPen(QPen(qcolor(t["card_border"]), 1))
        p.setBrush(qcolor(t["card_bg_solid"]))
        p.drawRoundedRect(r, rad, rad)
        sheen = QLinearGradient(r.topLeft(), r.bottomLeft())
        sheen.setColorAt(0, QColor(255, 255, 255, 18 if dark else 80))
        sheen.setColorAt(0.6, QColor(255, 255, 255, 0))
        p.setBrush(sheen)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, rad, rad)
        dot = QColor({"success": t["success"], "warning": t["warning"], "error": t["danger"]}.get(
            self._kind, t["brand"]))
        halo = QColor(dot)
        halo.setAlpha(60)
        c = QRectF(r.left() + 14, r.center().y() - 4, 8, 8)
        p.setBrush(halo)
        p.drawEllipse(c.adjusted(-3, -3, 3, 3))
        p.setBrush(dot)
        p.drawEllipse(c)
        p.end()


def show_toast(anchor, text, action=None, on_action=None, kind="info", ms=3600):
    """Shows `text` at the bottom of `anchor`. Returns the toast."""
    toast = Toast(anchor, text, action, kind, ms)
    if on_action is not None:
        toast.action_clicked.connect(on_action)
    toast.popup()
    return toast
