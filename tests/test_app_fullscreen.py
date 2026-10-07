"""The app's own full screen: F11 (and the button beside the caption dots)
fills the screen borderless and puts the title bar away; touching the top
edge brings the bar back over the page and it steps away again after; Esc
or F11 leaves, back to the window as it was. The pointer is never moved:
where it is gets made up."""
import time

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut  # noqa: E402

from ui_qt import main_window  # noqa: E402

win, tabs = build_window(tabs=("video", "music", "download"), size=(1200, 760))
settle(300)
check(win.fullscreen_btn.isVisible() and "Full screen" in win.fullscreen_btn.toolTip(),
      "no full-screen button in the title bar")
f11 = next((s for s in win.findChildren(QShortcut) if s.key() == QKeySequence(Qt.Key.Key_F11)), None)
check(f11 is not None, "F11 isn't wired")
f11.activated.emit()
settle(500)
check(win.isFullScreen() and win._app_fullscreen and not win.titleBar.isVisible(),
      "F11 didn't fill the screen with the title bar put away")
check("Leave full screen" in win.fullscreen_btn.toolTip(), "the button doesn't offer to leave")
print("F11 fills the screen, borderless, the title bar put away")


class Pointer:
    at = QPoint(0, 0)

    @classmethod
    def pos(cls):
        return cls.at


main_window.QCursor = Pointer
top_left = win.mapToGlobal(QPoint(0, 0))
Pointer.at = top_left + QPoint(300, 0)                 # the very top edge
win._watch_top_edge()
check(win.titleBar.isVisible(), "touching the top edge didn't bring the title bar back")
Pointer.at = top_left + QPoint(300, 12)                # on the bar: it stays
win._watch_top_edge()
check(win.titleBar.isVisible(), "the bar went away while the pointer was on it")
Pointer.at = top_left + QPoint(300, 400)               # down in the page
win._watch_top_edge()
time.sleep(0.8)
win._watch_top_edge()
check(not win.titleBar.isVisible(), "the bar didn't step away once the pointer left it")
print("the top edge brings the title bar back, and it steps away after")

app.sendEvent(win, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
settle(500)
check(not win.isFullScreen() and not win._app_fullscreen and win.titleBar.isVisible(),
      "Esc didn't leave full screen")
win.fullscreen_btn.click()
settle(400)
check(win.isFullScreen(), "the full-screen button didn't fill the screen")
f11.activated.emit()
settle(400)
check(not win.isFullScreen() and win.titleBar.isVisible(), "F11 didn't leave full screen")
print("Esc, the button and F11 all leave it, back to the window as it was")
win.close()
settle(300)
print("\nAPP FULLSCREEN OK")
