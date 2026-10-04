"""Closing the About or Updates panel must not take the app with it.

The Updates panel's check runs on a QThread; parented to the dialog, closing
the dialog destroyed a thread that was still running and Qt terminated the
process. That abort never reaches Python, so the real assertion is this
script's exit code -- run_all.py checks it.
"""
import _support
from _support import build_window, check, pump, qapp

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

app = qapp()
from ui_qt.dialogs.update_dialog import _LIVE_WORKERS, wait_for_workers

win, _tabs = build_window(tabs=("video", "download"))
quits = []
app.aboutToQuit.connect(lambda: quits.append(1))


def close_visible_dialog():
    for w in app.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            w.close()
            return
    raise AssertionError("no dialog opened")


for label in ("about", "updates"):
    QTimer.singleShot(250, close_visible_dialog)
    win.open_panel(label)
    pump()
    print("%s: window still up=%s, in-flight workers=%d" % (label, win.isVisible(), len(_LIVE_WORKERS)))
    check(win.isVisible() and not quits, "closing %s took the app down" % label)

wait_for_workers()
check(not _LIVE_WORKERS, "workers left running at shutdown")
print("\nDIALOG CLOSE OK (the exit code is the other half of this test)")
