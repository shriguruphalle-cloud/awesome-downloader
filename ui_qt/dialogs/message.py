"""The app's message box: the same glass panel as every other dialog.

Windows' own message box is a black slab with a system icon, and it was
landing in the middle of the navy glass every time something failed (a
fetch error, a confirmation). install() swaps QMessageBox's static helpers --
information, warning, critical, question -- for this dialog, so every
existing call site gets it without being rewritten.

Messages here are written as "headline, blank line, detail"; the headline is
set large and the detail muted and selectable (an error is often something
worth copying). Confirmations name their action -- "Delete", not "Yes".
"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from app import config

from .. import cinema, theme
from .base import CinematicDialog, button_row
from ..widgets.button import Button

SB = QMessageBox.StandardButton

_LABELS = {
    SB.Ok: "OK", SB.Cancel: "Cancel", SB.Yes: "Yes", SB.No: "No", SB.Retry: "Retry",
    SB.Close: "Close", SB.Save: "Save", SB.Discard: "Discard", SB.Abort: "Abort",
    SB.Ignore: "Ignore", SB.Apply: "Apply", SB.Open: "Open", SB.YesToAll: "Yes to all",
    SB.NoToAll: "No to all",
}
# Left to right as they appear; the affirmative one sits last, on the right.
_ORDER = [SB.Discard, SB.Abort, SB.Ignore, SB.NoToAll, SB.No, SB.Cancel, SB.Close,
          SB.Retry, SB.Apply, SB.Open, SB.Save, SB.YesToAll, SB.Yes, SB.Ok]
_AFFIRMATIVE = (SB.Yes, SB.Ok, SB.Save, SB.Retry, SB.Open, SB.Apply, SB.YesToAll)
_ESCAPES = (SB.Cancel, SB.No, SB.Close, SB.Abort, SB.Ok)

# "Permanently delete this file?" -> the Yes button says "Delete".
_ACTIONS = {"delete": "Delete", "clear": "Clear", "remove": "Remove", "replace": "Replace",
            "overwrite": "Overwrite", "quit": "Quit", "restart": "Restart", "install": "Install",
            "discard": "Discard", "reset": "Reset", "stop": "Stop"}
_DESTRUCTIVE = ("delete", "cannot be undone", "permanently")


def _has(buttons, button):
    try:
        return bool(buttons & button)
    except TypeError:
        return bool(int(buttons) & int(button.value))


def split_text(text):
    """(headline, detail) from a message written either way."""
    text = (text or "").strip()
    if "\n\n" in text:
        head, detail = text.split("\n\n", 1)
        return head.strip(), detail.strip()
    if "\n" in text:
        head, detail = text.split("\n", 1)
        if head.rstrip().endswith(":") or len(head) <= 90:
            return head.strip().rstrip(":"), detail.strip()
    if len(text) > 110:
        # One long sentence reads better as detail under a short heading.
        return "", text
    return text, ""


def _action_word(headline):
    for word in headline.lower().replace("?", " ").split()[:3]:
        if word in _ACTIONS:
            return _ACTIONS[word]
    return None


class _Glyph(QWidget):
    SIZE = 44

    def __init__(self, kind, dark, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._dark = dark
        self.setFixedSize(self.SIZE, self.SIZE)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = float(self.SIZE)
        c = QPointF(s / 2, s / 2)
        tone = {
            "warning": QColor(252, 211, 77) if self._dark else QColor(183, 121, 31),
            "critical": QColor(255, 107, 107) if self._dark else QColor(217, 45, 32),
        }.get(self._kind, theme.qcolor(theme.tokens(self._dark)["brand"]))

        halo = QRadialGradient(c, s / 2)
        inner = QColor(tone)
        inner.setAlpha(60 if self._dark else 40)
        outer = QColor(tone)
        outer.setAlpha(0)
        halo.setColorAt(0.5, inner)
        halo.setColorAt(1.0, outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, s / 2, s / 2)

        disc = QColor(tone)
        disc.setAlpha(34 if self._dark else 26)
        ring = QColor(tone)
        ring.setAlpha(150)
        p.setBrush(disc)
        p.setPen(QPen(ring, 1.2))
        p.drawEllipse(c, s * 0.34, s * 0.34)

        pen = QPen(tone, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        u = s / 44.0
        if self._kind == "critical":
            d = 5.5 * u
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        elif self._kind == "question":
            path = QPainterPath()
            path.moveTo(c.x() - 4.5 * u, c.y() - 4 * u)
            path.cubicTo(c.x() - 4.5 * u, c.y() - 9.5 * u, c.x() + 5 * u, c.y() - 9.5 * u,
                         c.x() + 5 * u, c.y() - 4 * u)
            path.cubicTo(c.x() + 5 * u, c.y() - 0.5 * u, c.x(), c.y() - 0.5 * u, c.x(), c.y() + 3 * u)
            p.drawPath(path)
            p.setBrush(tone)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(c.x(), c.y() + 7.5 * u), 1.6 * u, 1.6 * u)
        else:
            # "!" for a warning, "i" for information.
            top, bottom, dot = (-7.5, 2.5, 7.0) if self._kind == "warning" else (-1.5, 7.5, -6.5)
            p.drawLine(QPointF(c.x(), c.y() + top * u), QPointF(c.x(), c.y() + bottom * u))
            p.setBrush(tone)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(c.x(), c.y() + dot * u), 1.6 * u, 1.6 * u)
        p.end()


class MessageDialog(CinematicDialog):
    def __init__(self, parent, kind, title, text, buttons, default):
        dark = cinema.is_dark(parent) if parent is not None else True
        super().__init__(parent, title or config.APP_NAME, dark)
        self.clicked = None
        headline, detail = split_text(text)
        # A fixed width, so the wrapped text is measured at the width it will
        # actually have -- otherwise the height is worked out for a narrower
        # label and the dialog opens with a gap above its buttons.
        self._width = 520 if len(detail) > 180 or len(headline) > 70 else 440
        self.setFixedWidth(self._width)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(16)
        glyph_col = QVBoxLayout()
        glyph_col.addWidget(_Glyph(kind, dark))
        glyph_col.addStretch(1)
        row.addLayout(glyph_col)
        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        if headline:
            head = QLabel(headline)
            head.setObjectName("heading")
            head.setWordWrap(True)
            head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_col.addWidget(head)
        if detail:
            body = QLabel(detail)
            body.setObjectName("muted")
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_col.addWidget(body)
        text_col.addStretch(1)
        row.addLayout(text_col, 1)
        layout.addLayout(row)

        if not buttons:
            buttons = SB.Yes | SB.No if kind == "question" else SB.Ok
        present = [b for b in _ORDER if _has(buttons, b)] or [SB.Ok]
        if default in (None, SB.NoButton) or default not in present:
            default = next((b for b in reversed(present) if b in _AFFIRMATIVE), present[-1])
        self._escape = next((b for b in _ESCAPES if b in present), present[0])

        destructive = kind == "question" and any(w in (text or "").lower() for w in _DESTRUCTIVE)
        action = _action_word(headline or detail)
        widgets = []
        for b in present:
            label = _LABELS.get(b, "OK")
            if b == SB.Yes and action:
                label = action
            elif b == SB.No and action:
                label = "Cancel"
            btn = Button(label)
            affirmative = b in _AFFIRMATIVE
            if affirmative and destructive:
                btn.setObjectName("danger")
            elif affirmative and b == default:
                btn.setObjectName("accent")
            else:
                btn.setObjectName("quiet")
            btn.setMinimumWidth(92)
            btn.clicked.connect(lambda _c=False, sb=b: self._finish(sb))
            if b == default:
                btn.setDefault(True)
                btn.setAutoDefault(True)
                self._default_btn = btn
            else:
                btn.setAutoDefault(False)
            widgets.append(btn)
        layout.addLayout(button_row(*widgets))
        layout.activate()
        self.setFixedHeight(max(140, layout.totalHeightForWidth(self._width)))

    def showEvent(self, event):
        super().showEvent(event)
        btn = getattr(self, "_default_btn", None)
        if btn is not None:
            btn.setFocus()

    def _finish(self, button):
        self.clicked = button
        self.accept()

    def reject(self):
        if self.clicked is None:
            self.clicked = self._escape
        super().reject()

    @classmethod
    def ask(cls, parent, kind, title, text, buttons=None, default=None):
        dialog = cls(parent, kind, title, text, buttons, default)
        dialog.exec()
        return dialog.clicked if dialog.clicked is not None else dialog._escape


def _static(kind):
    def show(parent, title, text, buttons=None, defaultButton=None):  # noqa: N803 -- Qt's name
        return MessageDialog.ask(parent, kind, title, text, buttons, defaultButton)
    return staticmethod(show)


def install():
    """Routes QMessageBox.information/warning/critical/question here."""
    for kind in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, kind, _static(kind))
