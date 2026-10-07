"""A calm empty state: a glyph in a glass disc, a title, one line of help.

An empty list used to be a single muted sentence floating at the top of an
otherwise blank tab, which reads as "something failed to load". A centred
composition with a title reads as "nothing here yet, and here is what to do".
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import cinema, theme
from .card import centered_column


class _Glyph(QWidget):
    SIZE = 64

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(self.SIZE, self.SIZE)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = cinema.is_dark(self)
        s = float(self.SIZE)
        c = QPointF(s / 2, s / 2)

        halo = QRadialGradient(c, s / 2)
        tone = theme.qcolor(theme.tokens(dark)["brand"])
        tone.setAlpha(46 if dark else 28)
        clear = QColor(tone)
        clear.setAlpha(0)
        halo.setColorAt(0.55, tone)
        halo.setColorAt(1.0, clear)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, s / 2, s / 2)

        disc = QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68)
        fill = QColor(255, 255, 255, 16) if dark else QColor(255, 255, 255, 200)
        p.setBrush(fill)
        edge = QLinearGradient(disc.topLeft(), disc.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 60 if dark else 255))
        edge.setColorAt(1.0, QColor(255, 255, 255, 12) if dark else QColor(15, 23, 42, 30))
        p.setPen(QPen(edge, 1.0))
        p.drawEllipse(disc)

        ink = QColor(238, 241, 246, 210) if dark else QColor(11, 18, 32, 190)
        pen = QPen(ink, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        getattr(self, "_draw_" + self._kind, self._draw_download)(p, c, s * 0.17)
        p.end()

    @staticmethod
    def _draw_music(p, c, r):
        """Two notes on a beam."""
        from PySide6.QtCore import QPointF as P
        x, y = c.x(), c.y()
        p.drawLine(P(x - .55 * r, y + .75 * r), P(x - .55 * r, y - .85 * r))
        p.drawLine(P(x - .55 * r, y - .85 * r), P(x + .95 * r, y - 1.15 * r))
        p.drawLine(P(x + .95 * r, y - 1.15 * r), P(x + .95 * r, y + .55 * r))
        p.drawEllipse(P(x - .9 * r, y + .8 * r), .38 * r, .3 * r)
        p.drawEllipse(P(x + .6 * r, y + .6 * r), .38 * r, .3 * r)

    @staticmethod
    def _draw_download(p, c, r):
        p.drawLine(QPointF(c.x(), c.y() - r * 1.1), QPointF(c.x(), c.y() + r * 0.45))
        head = QPainterPath()
        head.moveTo(c.x() - r * 0.62, c.y() - r * 0.15)
        head.lineTo(c.x(), c.y() + r * 0.47)
        head.lineTo(c.x() + r * 0.62, c.y() - r * 0.15)
        p.drawPath(head)
        p.drawLine(QPointF(c.x() - r * 1.05, c.y() + r * 1.1), QPointF(c.x() + r * 1.05, c.y() + r * 1.1))

    @staticmethod
    def _draw_history(p, c, r):
        p.drawEllipse(c, r * 1.2, r * 1.2)
        p.drawLine(c, QPointF(c.x(), c.y() - r * 0.72))
        p.drawLine(c, QPointF(c.x() + r * 0.55, c.y() + r * 0.32))

    @staticmethod
    def _draw_torrent(p, c, r):
        # A magnet: a U with two capped poles.
        path = QPainterPath()
        path.moveTo(c.x() - r * 0.95, c.y() - r * 1.0)
        path.lineTo(c.x() - r * 0.95, c.y() + r * 0.1)
        path.arcTo(QRectF(c.x() - r * 0.95, c.y() - r * 0.85, r * 1.9, r * 1.9), 180, 180)
        path.lineTo(c.x() + r * 0.95, c.y() - r * 1.0)
        p.drawPath(path)
        p.drawLine(QPointF(c.x() - r * 1.3, c.y() - r * 0.45), QPointF(c.x() - r * 0.6, c.y() - r * 0.45))
        p.drawLine(QPointF(c.x() + r * 0.6, c.y() - r * 0.45), QPointF(c.x() + r * 1.3, c.y() - r * 0.45))

    @staticmethod
    def _draw_images(p, c, r):
        frame = QRectF(c.x() - r * 1.2, c.y() - r * 0.95, r * 2.4, r * 1.9)
        p.drawRoundedRect(frame, r * 0.3, r * 0.3)
        hills = QPainterPath()
        hills.moveTo(frame.left() + r * 0.2, frame.bottom() - r * 0.25)
        hills.lineTo(c.x() - r * 0.25, c.y() - r * 0.05)
        hills.lineTo(c.x() + r * 0.3, c.y() + r * 0.5)
        hills.lineTo(c.x() + r * 0.62, c.y() + r * 0.2)
        hills.lineTo(frame.right() - r * 0.2, frame.bottom() - r * 0.25)
        p.drawPath(hills)
        p.drawEllipse(QPointF(c.x() + r * 0.55, c.y() - r * 0.4), r * 0.18, r * 0.18)

    @staticmethod
    def _draw_link(p, c, r):
        for sign in (-1, 1):
            p.save()
            p.translate(c.x() + sign * r * 0.42, c.y() - sign * r * 0.42)
            p.rotate(-45)
            p.drawRoundedRect(QRectF(-r * 0.95, -r * 0.42, r * 1.9, r * 0.84), r * 0.42, r * 0.42)
            p.restore()


class EmptyState(QWidget):
    """Glyph + title + help line, centred in whatever space it is given."""

    def __init__(self, kind, title, detail="", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 32, 24, 32)
        lay.setSpacing(0)
        lay.addStretch(3)
        self.glyph = _Glyph(kind)
        lay.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("heading")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title_label)
        lay.addSpacing(6)
        # Centred inside a capped column rather than with a layout alignment
        # flag: a word-wrapped QLabel given an alignment flag is sized to its
        # one-line hint and only ever shows its first line.
        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("muted")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        holder = QWidget()
        column = centered_column(holder, 440)
        inner = QVBoxLayout(column)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.detail_label)
        lay.addWidget(holder)
        lay.addStretch(4)

    def setText(self, text):  # noqa: N802 -- stands in for a QLabel in old call sites
        self.detail_label.setText(text)

    def text(self):
        return self.detail_label.text()
