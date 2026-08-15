"""Rounded accent-tinted badge, painted directly.

QFrame + QSS (background + border-radius) does not reliably render in this
app's environment -- confirmed twice independently (the tab island, and this
chip): a corner-vs-centre pixel probe showed no rounding at all, and in this
case not even the background colour was applied, radius aside. Rather than
spend a third round chasing WA_StyledBackground/style-engine quirks, this
paints the capsule itself the same way the tab island and progress bars do.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class Chip(QWidget):
    """A centered, rounded pill containing one label. set_colors() re-themes
    it; the label itself is reachable via .label for setText()."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignCenter)
        # No wrap: a pill that wraps to two lines with the second line half
        # the width of the first reads as broken, not compact (reported
        # directly, with a screenshot). The chip has no fixed width of its
        # own -- it sizes to the label's single-line sizeHint, so it grows
        # to fit whatever the caller sets instead of wrapping.
        self.label.setWordWrap(False)
        layout.addWidget(self.label)
        self._bg = QColor(10, 132, 255, 30)
        self._border = QColor(10, 132, 255, 70)

    def set_colors(self, bg, border, text_color):
        self._bg, self._border = QColor(bg), QColor(border)
        self.label.setStyleSheet(f"color: {QColor(text_color).name()}; font-weight: 600; background: transparent;")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()
        super().paintEvent(event)
