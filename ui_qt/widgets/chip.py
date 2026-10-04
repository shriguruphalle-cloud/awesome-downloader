"""A small glass capsule holding one line of text, painted directly.

QFrame + QSS (background + border-radius) did not reliably render in this
app -- a corner-vs-centre pixel probe showed no rounding and, here, not even
the background colour -- so the capsule is painted, the same way the cards,
the nav island and the progress bars are.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class Chip(QWidget):
    """A centered capsule containing one label. set_colors() re-themes it;
    the label itself is reachable via .label for setText().

    `dot` puts a small status light before the text -- the colour says what
    kind of information the chip carries without a word for it."""

    def __init__(self, text="", parent=None, dot=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16 if dot is None else 26, 7, 16, 7)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignCenter)
        # No wrap: a pill that wraps to two lines reads as broken. The chip
        # sizes to the label's single-line width instead.
        self.label.setWordWrap(False)
        layout.addWidget(self.label)
        self._bg = QColor(255, 255, 255, 16)
        self._border = QColor(255, 255, 255, 34)
        self._dot = QColor(dot) if dot is not None else None

    def set_colors(self, bg, border, text_color, dot=None):
        self._bg, self._border = QColor(bg), QColor(border)
        if dot is not None:
            self._dot = QColor(dot)
        self.label.setStyleSheet(
            f"color: {QColor(text_color).name()}; font-weight: 600; background: transparent;")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, radius, radius)
        # A lit top edge, like every other pane of glass in the app.
        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = QColor(self._border)
        bottom = QColor(self._border)
        bottom.setAlpha(max(0, self._border.alpha() // 3))
        edge.setColorAt(0.0, top)
        edge.setColorAt(1.0, bottom)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        if self._dot is not None:
            d = 6.0
            cy = rect.center().y()
            glow = QColor(self._dot)
            glow.setAlpha(60)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QRectF(13 - d, cy - d, d * 2, d * 2))
            painter.setBrush(self._dot)
            painter.drawEllipse(QRectF(13 - d / 2, cy - d / 2, d, d))
        painter.end()
        super().paintEvent(event)
