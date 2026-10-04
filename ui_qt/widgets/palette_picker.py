"""The colour palette picker: a row of small plain dots (Settings > Appearance)."""
from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from .. import motion, palettes, theme


class _Swatch(QAbstractButton):
    """One palette as a flat dot of its colour. The chosen one gets a thin
    ring standing off it; nothing else decorates it."""

    D = 20           # dot diameter
    SIZE = 30        # hit area, and room for the ring

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(palettes.label(name))
        self.setAccessibleName(palettes.label(name) + " colour palette")
        self.setFixedSize(self.SIZE, self.SIZE)
        self._hover = motion.Fader(self)
        self._sel = motion.Fader(self, motion.MEDIUM)
        self.toggled.connect(lambda on: self._sel.to(1 if on else 0))

    def sizeHint(self):
        return QSize(self.SIZE, self.SIZE)

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        dark = bool(getattr(self.window(), "dark_mode", True))
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2.0, self.height() / 2.0)
        r = self.D / 2.0
        ring = max(self._sel.value, 0.45 * self._hover.value)
        if ring > 0.01:
            col = theme.qcolor(t["text"])
            col.setAlphaF(0.85 * ring)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r + 3.5, r + 3.5)
        fill = QColor(*palettes.swatch(self.name))
        # A hairline so the darkest dot (Obsidian) still reads on a dark panel.
        edge = QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 36)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(fill)
        p.drawEllipse(c, r - 0.5, r - 0.5)
        p.end()


class PalettePicker(QWidget):
    """The palettes in a row, with the chosen one's name beside them."""

    chosen = Signal(str)

    def __init__(self, current=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.swatches = []
        for name in palettes.ORDER:
            sw = _Swatch(name)
            sw.clicked.connect(lambda _c=False, n=name: self._pick(n))
            lay.addWidget(sw)
            self.swatches.append(sw)
        lay.addSpacing(8)
        self.name_label = QLabel("")
        self.name_label.setObjectName("muted")
        lay.addWidget(self.name_label)
        lay.addStretch(1)
        self.set_current(current or palettes.current())

    def set_current(self, name):
        for sw in self.swatches:
            sw.blockSignals(True)
            sw.setChecked(sw.name == name)
            sw.blockSignals(False)
            sw._sel.snap(1 if sw.name == name else 0)
            sw.update()
        self.name_label.setText(palettes.label(name))

    def _pick(self, name):
        self.set_current(name)
        self.chosen.emit(name)
