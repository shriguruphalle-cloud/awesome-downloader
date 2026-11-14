"""The update card shown at start: a sheet over the window, which dims behind it, and stays until it's answered
(Later, Update now, Esc, or a click outside it) -- not a popup that a focus change can close."""
import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app import config

from . import motion, theme
from .widgets.button import Button

CARD_W = 440


class UpdateCard(QWidget):
    later = Signal()
    update_now = Signal()

    def __init__(self, window, release, dark):
        super().__init__(window)
        self._dark = dark
        t = self._t = theme.tokens(dark)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.card = QWidget(self)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(28, 28, 28, 22)
        col.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(14)
        icon = QLabel()
        pm = QPixmap(os.path.join(config.BASE_DIR, "app_icon.png"))
        if not pm.isNull():
            icon.setPixmap(pm.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        icon.setStyleSheet("background: transparent;")
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(self._label("A new version is ready", t["text_muted"], 13, QFont.Weight.Medium))
        words.addWidget(self._label("Awesome Downloader %s" % release.get("version", ""), t["text"], 20,
                                    QFont.Weight.Bold))
        words.addWidget(self._label("You have %s" % config.APP_VERSION, t["text_faint"], 12, QFont.Weight.Normal))
        head.addLayout(words, 1)
        col.addLayout(head)

        notes = self._notes(release.get("notes") or "")
        if notes:
            col.addSpacing(18)
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet("background: %s;" % t["divider"])
            col.addWidget(line)
            col.addSpacing(14)
            col.addWidget(self._label("WHAT'S NEW", t["text_muted"], 11, QFont.Weight.DemiBold, spacing=1.4))
            col.addSpacing(8)
            body = self._label(notes, t["text"], 13, QFont.Weight.Normal)
            body.setTextFormat(Qt.TextFormat.MarkdownText)
            body.setWordWrap(True)
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
            area.setWidget(body)
            area.setMaximumHeight(240)
            area.setMinimumHeight(min(240, body.sizeHint().height() + 4))
            col.addWidget(area)

        col.addSpacing(22)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        later = Button("Later")
        later.setObjectName("quiet")
        now = Button("Update now")
        now.setObjectName("accent")
        for b in (later, now):
            b.setMinimumHeight(36)
            b.setMinimumWidth(110)
        later.clicked.connect(self._later)
        now.clicked.connect(lambda: (self.close(), self.update_now.emit()))
        row.addWidget(later)
        row.addWidget(now)
        col.addLayout(row)
        self.card.setFixedWidth(CARD_W)

    @staticmethod
    def _notes(text):
        """The release's own "What's new" list: its title and the download section left out."""
        lines = text.replace("\r\n", "\n").split("\n")
        start = next((i + 1 for i, l in enumerate(lines) if l.lower().lstrip("# ").startswith("what's new")), 0)
        out = []
        for l in lines[start:]:
            if l.startswith("## ") and out:
                break
            if not l.startswith("# "):
                out.append(l)
        return "\n".join(out).strip()[:3000]

    def _label(self, text, color, px, weight, spacing=0.0):
        lab = QLabel(text)
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setWeight(weight)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        lab.setFont(f)
        lab.setWordWrap(True)
        lab.setStyleSheet("color: %s; background: transparent;" % color)
        return lab

    def show_over(self):
        self.setGeometry(self.parentWidget().rect())
        self.card.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self.setFocus()
        try:
            motion.fade_in(self)
        except Exception:   # noqa: BLE001 -- no animation is fine
            pass

    def _place(self):
        self.card.move((self.width() - self.card.width()) // 2, max(40, (self.height() - self.card.height()) // 2))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _later(self):
        self.later.emit()
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._later()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if not self.card.geometry().contains(e.position().toPoint()):
            self._later()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120 if self._dark else 70))
        r = QRectF(self.card.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
        for spread, a in ((18, 18), (10, 26), (4, 34)):         # a soft shadow under the sheet
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(r.adjusted(-spread / 2, -spread / 4, spread / 2, spread), 18 + spread / 2, 18 + spread / 2)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.fillPath(path, theme.qcolor(self._t["card_bg_solid"]))
        p.setPen(QPen(theme.qcolor(self._t["card_border"]), 1))
        p.drawPath(path)
