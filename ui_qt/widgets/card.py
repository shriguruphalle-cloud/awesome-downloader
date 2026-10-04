"""Glass panel -- the surface every tab builds on (see ../cinema.py).

Painted rather than styled through QSS. A QSS card rendered correctly
directly in a tab's layout but painted nothing at all once nested in a
QScrollArea's viewport, which is how every per-row card in History and
Torrent is parented; and QSS cannot sample the backdrop behind a panel,
which is what makes it read as glass rather than as a grey box.
"""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import cinema

RADIUS = 16.0
# The strip under a top-level panel its contact shadow is drawn in.
SHADOW = 3


class _PaintedCard(QFrame):
    """A glass panel. `solid` asks for a denser pane, for surfaces that must
    stay legible whatever is behind them. A panel inside another panel
    paints as a raised pane on its parent's glass instead of frosting the
    backdrop a second time, which would cut a window straight through the
    parent."""

    def __init__(self, solid=False, parent=None, radius=RADIUS):
        super().__init__(parent)
        self._solid = solid
        self._radius = radius

    def _nested(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, _PaintedCard):
                return True
            parent = parent.parentWidget()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        nested = self._nested()
        dark = cinema.is_dark(self)
        radius = self._radius if not nested else min(self._radius, 12.0)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5 - (0 if nested else SHADOW))
        if not nested:
            cinema.paint_contact_shadow(painter, rect, radius, dark, SHADOW)
        cinema.paint_glass(painter, self, rect, radius=radius, tier=1 if nested else 0,
                           sample=not nested, dark=dark)
        if self._solid and not nested:
            # A second, denser pass of the same shade -- used sparingly.
            cinema.paint_glass(painter, self, rect, radius=self._radius, tier=1, sample=False, dark=dark)
        painter.end()


def section_label(text):
    """The small-caps, widely tracked label that heads a panel."""
    label = QLabel(text.upper())
    label.setObjectName("sectionLabel")
    return label


def centered_column(host, max_width):
    """Lays `host` out as one centred column no wider than `max_width` and
    returns the column. On a wide or maximized window a form stretched to the
    full width puts its label and its button a monitor apart; a column keeps
    the page composed and lets the backdrop frame it."""
    # Two zero-stretch spacers around a stretch-1 column: below the cap the
    # column takes everything; past it the spacers split what's left. (An
    # AlignHCenter item was tried first and shrank the column to its size
    # hint -- a 23px-wide page.)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    column = QWidget()
    column.setMaximumWidth(max_width)
    row.addStretch(0)
    row.addWidget(column, 1)
    row.addStretch(0)
    return column


def make_card(title=None, solid=False):
    """Returns (frame, inner_layout)."""
    frame = _PaintedCard(solid=solid)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16 + SHADOW)
    layout.setSpacing(8)
    if title:
        heading = section_label(title)
        frame.title_label = heading
        layout.addWidget(heading)
        layout.addSpacing(2)
    return frame, layout
