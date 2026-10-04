"""Buy me a coffee: the website's donate card, dropping from the title bar's
coffee button.

Two ways to give, as on the website: PayPal (any currency -- opens the
PayPal page in the browser) and UPI (India -- shows the website's own QR, to
scan with GPay, PhonePe, Paytm or any UPI app). The website also offers
"Open in UPI App"; a Windows PC has no UPI app to open, so the QR is the way.
"""
import os
import re

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout,
                               QWidget)

from app import config

from .. import motion
from ..browser_chrome import GlassPopup, draw_icon
from ..theme import qcolor

QR_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "upi-qr.svg")


def _font(px, weight=QFont.Weight.Normal):
    f = QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


class _Method(QAbstractButton):
    """One way to give: an icon, a name, a line under it, and a chevron."""

    H = 58

    def __init__(self, kind, name, sub, t, parent=None):
        super().__init__(parent)
        self.kind, self.name, self.sub, self._t = kind, name, sub, t
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(self.H)
        self._hover = motion.Fader(self)

    def sizeHint(self):
        return QSize(280, self.H)

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = self._hover.value
        fill = qcolor(t["pressed_overlay"] if self.isDown() else t["hover_overlay"])
        if not self.isDown():
            fill.setAlphaF(fill.alphaF() * (0.5 + 0.5 * h))
        border = qcolor(t["card_border"])
        if h > 0:
            hi = qcolor(t["brand"])
            hi.setAlphaF(0.45 * h)
            border = hi if h > 0.5 else border
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 12, 12)
        # the icon on a soft brand disc
        disc = QRectF(12, (self.height() - 34) / 2, 34, 34)
        tint = qcolor(t["brand"])
        tint.setAlphaF(0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tint)
        p.drawEllipse(disc)
        draw_icon(p, self.kind, disc.adjusted(8, 8, -8, -8), qcolor(t["brand"]))
        x = disc.right() + 12
        p.setPen(qcolor(t["text"]))
        p.setFont(_font(13, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, 10, self.width() - x - 30, 20),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.name)
        p.setPen(qcolor(t["text_muted"]))
        p.setFont(_font(11))
        p.drawText(QRectF(x, 29, self.width() - x - 30, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.sub)
        chev = QColor(qcolor(t["text"] if h > 0.5 else t["text_faint"]))
        draw_icon(p, "forward", QRectF(self.width() - 28, (self.height() - 16) / 2, 16, 16), chev)
        p.end()


class _QR(QWidget):
    """The website's UPI QR, drawn from its SVG, on a white rounded tile (a
    QR needs its quiet zone light to scan).

    Drawn at a whole number of screen pixels per square and without
    antialiasing: scaled freely, its rows (strokes in the SVG) left hairline
    seams between them, which also make a QR harder to scan."""

    SIDE = 198
    PAD = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(QR_PATH)
        try:
            with open(QR_PATH, encoding="utf-8") as f:
                m = re.search(r"M0 0h(\d+)v", f.read())     # the light square behind it: N modules
            self.modules = int(m.group(1)) if m else 37
        except OSError:
            self.modules = 37
        self._img = None
        self.setFixedSize(self.SIDE, self.SIDE)
        self.setAccessibleName("UPI QR code")

    def image(self):
        if not self.renderer.isValid():
            return None
        dpr = self.devicePixelRatioF()
        k = max(1, int((self.SIDE - 2 * self.PAD) * dpr // self.modules))
        side = k * self.modules
        if self._img is None or self._img.width() != side:
            img = QImage(side, side, QImage.Format.Format_RGB32)
            img.fill(QColor(255, 255, 255))
            p = QPainter(img)
            self.renderer.render(p, QRectF(0, 0, side, side))
            p.end()
            img.setDevicePixelRatio(dpr)
            self._img = img
        return self._img

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(self.rect()), 14, 14)
        img = self.image()
        if img is not None:
            w = img.width() / img.devicePixelRatio()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(QPointF(round((self.width() - w) / 2), round((self.height() - w) / 2)), img)
        p.end()


class _Back(QAbstractButton):
    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._t = t
        self.setText("Back")
        self.setAccessibleName("Back")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(64, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = qcolor(self._t["text"] if self.underMouse() else self._t["text_muted"])
        draw_icon(p, "back", QRectF(0, 4, 16, 16), color)
        p.setPen(color)
        p.setFont(_font(12, QFont.Weight.DemiBold))
        p.drawText(QRectF(20, 0, self.width() - 20, self.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), "Back")
        p.end()


class DonatePanel(GlassPopup):
    """Support the project: PayPal, or UPI by QR."""

    open_url = Signal(str)

    def __init__(self, parent, t, dark):
        super().__init__(parent, 300, t, dark)
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background: transparent;")

        # ---- choose ----
        choose = QWidget()
        col = QVBoxLayout(choose)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(self.label("Support the project", px=15, weight=QFont.Weight.Bold))
        col.addWidget(self.label("Pick any amount — every coffee helps.", role="muted", px=12))
        col.addSpacing(4)
        self.paypal = _Method("coffee", "PayPal", "Any currency", t)
        self.paypal.clicked.connect(self._paypal)
        col.addWidget(self.paypal)
        self.upi = _Method("card", "UPI", "India · GPay, PhonePe…", t)
        self.upi.clicked.connect(lambda: self._show(1))
        col.addWidget(self.upi)
        self.pages.addWidget(choose)

        # ---- UPI: the QR ----
        upi = QWidget()
        ucol = QVBoxLayout(upi)
        ucol.setContentsMargins(0, 0, 0, 0)
        ucol.setSpacing(10)
        head = QHBoxLayout()
        self.back = _Back(t)
        self.back.clicked.connect(lambda: self._show(0))
        head.addWidget(self.back)
        head.addStretch(1)
        ucol.addLayout(head)
        ucol.addWidget(self.label("Pay via UPI", px=15, weight=QFont.Weight.Bold),
                       0, Qt.AlignmentFlag.AlignHCenter)
        self.qr = _QR()
        ucol.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        hint = self.label("Scan with GPay, PhonePe, Paytm or any UPI app", role="muted", px=11, wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ucol.addWidget(hint)
        self.pages.addWidget(upi)

        self.body.addWidget(self.pages)
        self._show(0)

    def _show(self, index):
        self.pages.setCurrentIndex(index)
        # The stack sizes to its tallest page; the panel fits the one shown.
        for i in range(self.pages.count()):
            pol = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.pages.widget(i).setSizePolicy(pol, pol)
        self.pages.adjustSize()
        self.adjustSize()

    def _paypal(self):
        self.open_url.emit(config.DONATE_PAYPAL)
        QDesktopServices.openUrl(QUrl(config.DONATE_PAYPAL))
        self.close()
