"""Card surface -- the translucent rgba panel that sits over the window's
Acrylic blur (see ../mica.py, ../theme.py). One shared builder so every tab
gets the same padding/heading treatment instead of each tab re-deriving it.

Painted directly rather than via QSS (QFrame#card { background: ...;
border-radius: ...; }). That QSS rule renders correctly for a card that
sits directly in a tab's own layout (confirmed: pixel-probed a Video tab
card, got its real translucent tint back), but a QFrame with the same
objectName painted NOTHING at all -- solid black, no background, no border
-- once nested inside a QScrollArea's viewport, which is exactly how every
per-row card in History and Torrent is parented. Confirmed on both: same
probe, same empty result. Painting the rect ourselves has no dependency on
where the widget ends up in the tree, which is what actually makes it
reliable regardless of nesting.
"""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

# Mirrors ui_qt/theme.py's DARK/LIGHT card_bg* tokens, as real (r,g,b,a)
# tuples instead of "rgba(...)" strings -- QColor() silently returns black
# for a CSS-style rgba string (only #RRGGBB/named colors parse), which bit
# this exact app twice already in other painted widgets. Tuples sidestep
# that class of bug entirely rather than risking a third repeat.
_CARD_COLORS = {
    True: {  # dark
        False: ((40, 40, 43, 150), (255, 255, 255, 28)),   # translucent
        True: ((30, 30, 32, 235), (255, 255, 255, 28)),    # solid
    },
    False: {  # light
        False: ((255, 255, 255, 160), (0, 0, 0, 18)),
        True: ((255, 255, 255, 240), (0, 0, 0, 18)),
    },
}

# Only used when the window is maximized/fullscreen AND in light mode.
# Windowed light mode isn't touched here -- it was never reported broken,
# and it works because the real Acrylic blur is what gives a translucent
# white card its contrast there. Maximizing kills that blur (DWM stops
# compositing it), so _TranslucentSurface substitutes a flat opaque grey
# backdrop -- but a translucent white card blended over THAT flat grey
# does the alpha math into near-white-on-near-white: measured contrast was
# 1.07:1, i.e. invisible (confirmed directly, from a real screenshot: no
# card edges visible at all). Alpha blending two colors that both live near
# the top of the luminance range can't produce much separation no matter
# how the base is tuned -- verified across several base greys, fill
# contrast never broke 1.5:1. An opaque, distinctly darker border is what
# actually solves it: 2.67:1 against the card fill, a real, visible edge.
_LIGHT_FULLSCREEN = {
    False: ((255, 255, 255, 225), (150, 155, 163, 255)),
    True: ((255, 255, 255, 250), (150, 155, 163, 255)),
}
_FULLSCREEN_OPAQUE_BASE_LIGHT = (205, 209, 215)  # must match main_window.py's


class _PaintedCard(QFrame):
    def __init__(self, solid=False, parent=None):
        super().__init__(parent)
        self._solid = solid

    def paintEvent(self, event):
        # Read dark_mode from the top-level window at paint time rather
        # than caching it at construction -- since this repaints on its
        # own after MainWindow.toggle_theme() (like every widget does), it
        # self-corrects on a live theme switch with no separate re-theme
        # wiring needed for every card in every tab.
        win = self.window()
        dark = getattr(win, "dark_mode", True)
        fullscreen = bool(win) and (win.isMaximized() or win.isFullScreen())
        if not dark and fullscreen:
            bg, border = _LIGHT_FULLSCREEN[self._solid]
        else:
            bg, border = _CARD_COLORS[dark][self._solid]

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(*border), 1))
        painter.setBrush(QColor(*bg))
        painter.drawRoundedRect(rect, 20, 20)
        painter.end()


def make_card(title=None, solid=False):
    """Returns (frame, inner_layout). `solid` uses the less-translucent
    card_bg_solid token, for panels that must stay legible regardless of
    what's behind them (used sparingly -- most surfaces should let the
    blur show through)."""
    frame = _PaintedCard(solid=solid)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(6)
    if title:
        heading = QLabel(title)
        heading.setObjectName("muted")
        layout.addWidget(heading)
    return frame, layout
