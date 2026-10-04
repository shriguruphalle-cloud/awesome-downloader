"""The shared base for the app's panels (Settings, Updates, About, ...).

A panel is its own top-level window, so the main window's backdrop is not
behind it. It paints the same light rig at its own size instead, offers a
frosted copy of it to the glass inside (cinema.backdrop_for finds
`backdrop_surface` on the window), and asks Windows for a dark title bar
so a white caption strip doesn't sit on top of a night-dark panel.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from app import config

from .. import cinema, motion, theme


class CinematicDialog(QDialog):
    def __init__(self, parent=None, title="", dark_mode=True):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.setWindowTitle(title or config.APP_NAME)
        self._backdrop = cinema.DialogBackdrop()
        self._solid = getattr(parent, "_backdrop_mode", None) == cinema.SOLID if parent else False
        self.setStyleSheet(theme.build_stylesheet(dark_mode=dark_mode)
                           + "QDialog { background: transparent; }")

    # cinema.backdrop_for() looks for this on the window.
    @property
    def backdrop_surface(self):
        return self

    def frost(self):
        if self._solid:
            return None
        self._backdrop.ensure(self, self.dark_mode)
        return self._backdrop.frost

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._solid:
            painter.fillRect(self.rect(), cinema.INK[self.dark_mode])
        else:
            self._backdrop.paint(self, painter, self.dark_mode)
        painter.end()

    def showEvent(self, event):
        super().showEvent(event)
        cinema.set_dark_title_bar(self, self.dark_mode)
        # Panels fade in rather than popping into existence.
        if not motion.reduced() and not getattr(self, "_faded_in", False):
            self._faded_in = True
            self.setWindowOpacity(0.0)
            motion.tween(self, 0.0, 1.0, motion.MEDIUM, self.setWindowOpacity)


def header(title, subtitle=""):
    """A panel's title block: display-size title, one muted line under it."""
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(3)
    t = QLabel(title)
    t.setObjectName("display")
    col.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        col.addWidget(s)
    return col


def button_row(*buttons, stretch_first=True):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    if stretch_first:
        row.addStretch(1)
    for b in buttons:
        if b is None:
            row.addStretch(1)
            continue
        b.setCursor(Qt.PointingHandCursor)
        row.addWidget(b)
    return row
