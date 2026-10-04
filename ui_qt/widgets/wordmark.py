"""Awesome Downloader, the name as the website and the home page set it.

Instrument Serif: "Awesome" upright in the text colour, "Downloader" in
italic, lit by the palette's gradient (a lighter brand, the brand, the
palette's second light) -- with, at night, a faint glow behind it. The
fonts are the website's own (OFL), unpacked from its WOFF2 files into
ui_qt/fonts so Qt can use them (NOTICE.md).

Painted, not two QLabels, so the gradient and the glow can be drawn. It takes
no mouse input: in the title bar it sits on the window's drag handle, and a
press on the name should move the window.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

FAMILY = "Instrument Serif"


def _mix(a, b, t):
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class Wordmark(QWidget):
    def __init__(self, px=22, parent=None, **_old_style):
        super().__init__(parent)
        from .. import theme
        theme.load_custom_fonts()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = QColor("#eaf2ff")
        self._brand = QColor("#38bdf8")
        self._brand_2 = QColor("#818cf8")
        self._dark = True
        self._glow = None
        self._f1 = QFont(FAMILY)
        self._f1.setPixelSize(px)
        self._f1.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 99)
        self._f2 = QFont(self._f1)
        self._f2.setItalic(True)
        fm1, fm2 = QFontMetricsF(self._f1), QFontMetricsF(self._f2)
        self._w1 = fm1.horizontalAdvance("Awesome")
        self._gap = fm1.horizontalAdvance(" ") * 0.95
        self._w2 = fm2.horizontalAdvance("Downloader") + px * 0.12   # the italic leans out past its advance
        self._pad = max(4, int(px * 0.3))                             # room for the glow
        self.setFixedSize(int(math.ceil(self._w1 + self._gap + self._w2 + self._pad * 2)),
                          int(math.ceil(max(fm1.height(), fm2.height()) + self._pad)))

    def set_colors(self, text, brand, dark, second=None):
        """`second`: the palette's second light, for the far end of the gradient."""
        self._text = QColor(text)
        self._brand = QColor(brand)
        self._dark = dark
        if second is not None:
            sec = QColor(*second) if isinstance(second, (tuple, list)) else QColor(second)
            self._brand_2 = _mix(sec, QColor(255, 255, 255), 0.15) if dark else _mix(sec, QColor(20, 24, 40), 0.35)
        else:
            self._brand_2 = _mix(self._brand, QColor(129, 140, 248), 0.6)
        self._glow = None
        self.update()

    def _baseline(self):
        fm = QFontMetricsF(self._f1)
        return (self.height() - fm.height()) / 2.0 + fm.ascent()

    def _gradient(self):
        x0 = self._pad + self._w1 + self._gap
        g = QLinearGradient(x0, 0, x0 + self._w2, self.height() * 0.35)
        hi = _mix(self._brand, QColor(255, 255, 255), 0.4 if self._dark else 0.12)
        g.setColorAt(0.0, hi)
        g.setColorAt(0.45, self._brand)
        g.setColorAt(1.0, self._brand_2)
        return g

    def _glow_image(self):
        """The italic word, blurred: drawn, shrunk, and scaled back up."""
        if self._glow is not None:
            return self._glow
        dpr = self.devicePixelRatioF()
        img = QImage(int(self.width() * dpr), int(self.height() * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.scale(dpr, dpr)
        p.setFont(self._f2)
        p.setPen(QPen(QBrush(self._gradient()), 1))
        p.drawText(QPointF(self._pad + self._w1 + self._gap, self._baseline()), "Downloader")
        p.end()
        small = img.scaled(max(1, img.width() // 6), max(1, img.height() // 6),
                           Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._glow = small.scaled(img.width(), img.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        self._glow.setDevicePixelRatio(dpr)
        return self._glow

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._dark:
            p.setOpacity(0.55)
            p.drawImage(QPointF(0, 0), self._glow_image())
            p.setOpacity(1.0)
        y = self._baseline()
        p.setFont(self._f1)
        p.setPen(self._text)
        p.drawText(QPointF(self._pad, y), "Awesome")
        p.setFont(self._f2)
        p.setPen(QPen(QBrush(self._gradient()), 1))
        p.drawText(QPointF(self._pad + self._w1 + self._gap, y), "Downloader")
        p.end()

    def text_rect(self):
        """Where the letters are, without the glow margin."""
        return QRectF(self._pad, 0, self._w1 + self._gap + self._w2, self.height())
