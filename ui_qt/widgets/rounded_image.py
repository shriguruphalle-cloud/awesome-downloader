"""A thumbnail with properly rounded corners.

A QLabel holding a pixmap has square corners, and clipping it with a QSS
radius or a painter clip path gives stair-stepped ones -- Qt does not
antialias either. The image is masked instead: a rounded rect drawn with
antialiasing, and the picture composited into it (SourceIn), once per image
and size, then cached.

Stands in for the QLabel it replaces: setPixmap / pixmap / setText / text.
"""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .. import cinema


class RoundedImage(QWidget):
    def __init__(self, width, height, radius=8.0, placeholder="", parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._radius = radius
        self._pix = None
        self._text = placeholder
        self._cache_key = None
        self._cache = None

    # ---- QLabel-compatible surface ----
    def setPixmap(self, pixmap):  # noqa: N802
        self._pix = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._cache_key = None
        self.update()

    def pixmap(self):
        return self._pix if self._pix is not None else QPixmap()

    def setText(self, text):  # noqa: N802
        self._text = text or ""
        self.update()

    def text(self):
        return self._text

    def setAlignment(self, *_args):  # noqa: N802 -- always centred
        pass

    # ---- painting ----
    def _masked(self):
        dpr = self.devicePixelRatioF()
        key = (self._pix.cacheKey(), self.width(), self.height(), round(dpr, 3), self._radius)
        if key == self._cache_key and self._cache is not None:
            return self._cache
        target = QSize(max(1, int(round(self.width() * dpr))), max(1, int(round(self.height() * dpr))))
        # Cover the box, cropping the overflow evenly -- a 4:3 source in a
        # 16:9 box is trimmed top and bottom rather than letterboxed.
        scaled = self._pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - target.width()) // 2)
        y = max(0, (scaled.height() - target.height()) // 2)
        cropped = scaled.copy(x, y, target.width(), target.height())

        img = QImage(target, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        r = self._radius * dpr
        p.drawRoundedRect(QRectF(0, 0, target.width(), target.height()), r, r)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.drawPixmap(0, 0, cropped)
        p.end()
        result = QPixmap.fromImage(img)
        result.setDevicePixelRatio(dpr)
        self._cache, self._cache_key = result, key
        return result

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = cinema.is_dark(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._pix is not None:
            p.drawPixmap(0, 0, self._masked())
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 12) if dark else QColor(15, 23, 42, 10))
            p.drawRoundedRect(rect, self._radius, self._radius)
            if self._text:
                font = QFont(self.font())
                font.setPixelSize(11)
                p.setFont(font)
                p.setPen(QColor(143, 152, 170) if dark else QColor(70, 83, 103))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        # A hairline so a dark thumbnail doesn't dissolve into dark glass.
        edge = QColor(255, 255, 255, 26) if dark else QColor(15, 23, 42, 26)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, self._radius, self._radius)
        p.end()
