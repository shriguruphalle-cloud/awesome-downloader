"""A row of labelled numbers -- a torrent's vital signs.

Each column is a small caption over a value set in mono, so figures line up
from row to row and don't jitter as they change. Columns come in priority
order; when the row is too narrow for all of them, the last ones step out.

Behind them, the row's card draws its speed over the last minute (widgets/speed_graph.py).
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QWidget

from .. import cinema, theme
from ..theme import qcolor

MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"]


class StatsStrip(QWidget):
    H = 46
    MIN_COL = 92

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self._items = []

    def set_items(self, items):
        """[(caption, value, role)], role None / "down" / "up" / "done" / "warn" / "faint"."""
        if items != self._items:
            self._items = list(items)
            self.update()

    def items(self):
        return list(self._items)

    def paintEvent(self, event):
        if not self._items:
            return
        t = theme.tokens(cinema.is_dark(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cap_font = QFont(self.font())
        cap_font.setPixelSize(9)
        cap_font.setWeight(QFont.Weight.DemiBold)
        cap_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        val_font = QFont(self.font())
        val_font.setFamilies(MONO_FAMILIES)
        val_font.setPixelSize(12)
        fm = QFontMetricsF(val_font)
        w = max(1.0, float(self.width()) - 14.0)
        cols = max(1, min(len(self._items), int(w // self.MIN_COL)))
        col_w = w / cols
        colors = {
            None: qcolor(t["text"]),
            "down": QColor(t["brand"]),
            "up": QColor(t["success"]),
            "done": QColor(t["success"]),
            "warn": QColor(t["warning"]),
            "faint": qcolor(t["text_faint"]),
        }
        for i, (caption, value, role) in enumerate(self._items[:cols]):
            x = i * col_w
            p.setFont(cap_font)
            p.setPen(qcolor(t["text_faint"]))
            p.drawText(QRectF(x, 3, col_w - 8, 14), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       caption.upper())
            p.setFont(val_font)
            p.setPen(colors.get(role, colors[None]))
            text = fm.elidedText(value, Qt.TextElideMode.ElideRight, col_w - 10)
            p.drawText(QRectF(x, 19, col_w - 8, 20), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       text)
        p.end()
