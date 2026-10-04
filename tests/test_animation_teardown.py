"""Widgets torn down in the middle of an animation must not crash the app.

Buttons ease their hover, the primary one sweeps a band of light, panels
fade in, list rows fold away -- and any of them can be destroyed while that
is still running: a dialog closed while the pointer rests on its button, a
download card removed mid-fold. A native crash here never reaches Python,
so the exit code is the real assertion (run_all.py checks it)."""
import _support  # noqa: F401
from _support import check, qapp

from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

app = qapp()
from ui_qt import motion  # noqa: E402
from ui_qt.dialogs.base import CinematicDialog  # noqa: E402
from ui_qt.widgets.button import Button  # noqa: E402

host = QWidget()
host.resize(400, 300)
host.show()


def pump(n=3):
    for _ in range(n):
        app.processEvents()


def hover(widget):
    QApplication.sendEvent(widget, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


for round_ in range(60):
    dialog = CinematicDialog(host, "Teardown", True)
    lay = QVBoxLayout(dialog)
    primary = Button("Download")
    primary.setObjectName("accent")
    plain = Button("Close")
    busy = Button("Fetch")
    busy.set_busy(True)
    for b in (primary, plain, busy):
        lay.addWidget(b)
    dialog.show()
    pump(2)
    hover(primary)          # hover glow + light sweep start
    hover(plain)
    primary.pressed.emit()  # press animation
    pump(1)
    # Torn down mid-animation, three different ways.
    if round_ % 3 == 0:
        dialog.deleteLater()
    elif round_ % 3 == 1:
        dialog.close()
        dialog.setParent(None)
        dialog.deleteLater()
    else:
        primary.deleteLater()
        dialog.deleteLater()
    pump(4)

# A list row folding away while its parent goes.
for _ in range(30):
    box = QWidget(host)
    row = QWidget(box)
    row.resize(200, 40)
    box.show()
    motion.grow_in(row)
    motion.shrink_out(row, lambda r=row: r.deleteLater())
    pump(1)
    box.deleteLater()
    pump(3)

check(True, "")
print("\nANIMATION TEARDOWN OK")
