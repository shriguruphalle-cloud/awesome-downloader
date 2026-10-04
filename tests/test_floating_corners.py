"""A floating window has Windows 11's rounded corners and nothing else at
them -- no painted cut of its own, which once left a sliver of desktop
showing between two curves. Square when snapped (here) or maximized
(tests/test_maximized_corners.py).

Reads the real screen at the window's own corners, as the maximized test
does. The backdrop is painted magenta for the test (with the real corner
cutting on top), and a solid green window of the test's own sits behind it,
so every pixel read belongs to this test: green (or green in shadow) means
a cut corner, magenta means painted."""
import ctypes
from ctypes import wintypes

import _support  # noqa: F401
from _support import build_window, check, keep_on_top, qapp

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

app = qapp()
from ui_qt import main_window as mw  # noqa: E402


def _magenta(self, event):
    p = QPainter(self)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    p.fillRect(self.rect(), QColor(255, 0, 255))
    p.end()


mw.BackdropSurface.paintEvent = _magenta

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


def pixel(x, y):
    dc = user32.GetDC(0)
    c = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(0, dc)
    return c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF


def magenta(rgb):
    r, g, b = rgb
    return r > 200 and b > 200 and g < 80


win, _tabs = build_window(tabs=("video", "download"), size=(900, 600))
win.move(120, 120)
backing = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
backing.setStyleSheet("background: #00ff00;")
backing.setAutoFillBackground(True)
backing.setGeometry(win.frameGeometry().adjusted(-20, -20, 20, 20))
result = {}


def arrange():
    backing.show()
    win.raise_()
    win.activateWindow()
    keep_on_top(backing, win)


def shoot():
    r = wintypes.RECT()
    user32.GetWindowRect(int(win.winId()), ctypes.byref(r))
    scale = win.devicePixelRatioF()
    inset = int(round(8 * scale))       # the centre of an 8 px arc: always painted
    result["floating"] = not win.isMaximized()
    result["radius"] = win._central.corner_radius()
    hwnd = int(win.winId())
    probes = {"top-left": (r.left + inset, r.top + inset), "top-right": (r.right - 1 - inset, r.top + inset),
              "bottom-left": (r.left + inset, r.bottom - 1 - inset),
              "bottom-right": (r.right - 1 - inset, r.bottom - 1 - inset)}
    result["covered"] = {k: user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(*v)), 2) != hwnd
                         for k, v in probes.items()}
    result["corners"] = {
        "top-left": (pixel(r.left, r.top), pixel(r.left + inset, r.top + inset)),
        "top-right": (pixel(r.right - 1, r.top), pixel(r.right - 1 - inset, r.top + inset)),
        "bottom-left": (pixel(r.left, r.bottom - 1), pixel(r.left + inset, r.bottom - 1 - inset)),
        "bottom-right": (pixel(r.right - 1, r.bottom - 1), pixel(r.right - 1 - inset, r.bottom - 1 - inset)),
    }
    app.quit()


QTimer.singleShot(300, arrange)
QTimer.singleShot(1500, shoot)
app.exec()
backing.close()

check(result.get("floating"), "the window wasn't floating")
check(result["radius"] == mw.BackdropSurface.CORNER_RADIUS, "no corner radius while floating")
read = 0
for name, (corner, inside) in result["corners"].items():
    if result["covered"][name]:
        # Another program's window in front: the screen there isn't ours to read.
        print("  %-13s covered by another program's window -- not read" % name)
        continue
    read += 1
    print("  %-13s corner rgb%s  inside rgb%s" % (name, corner, inside))
    check(not magenta(corner), "the %s corner is square" % name)
    check(magenta(inside), "the %s corner is cut too deep" % name)

# ---- snapped to a screen edge: square, as Windows 11 does it ---------------------
# (app.quit() above closed the window: Qt 6 closes windows on quit.)
from _support import settle  # noqa: E402

win.show()
settle(300)
area = win.screen().availableGeometry()
win.setGeometry(area.x(), area.y(), area.width() // 2, area.height())     # the left half
settle(400)
print("left-half snap: snapped=%s radius=%s" % (win._snapped, win._central.corner_radius()))
check(win._snapped and win._central.corner_radius() == 0, "a window snapped to the left half kept round corners")
win.setGeometry(area.x() + 120, area.y() + 120, 900, 600)
settle(400)
check(not win._snapped and win._central.corner_radius() == mw.BackdropSurface.CORNER_RADIUS,
      "unsnapped, the corners didn't come back")
print("snapped -> square, floating -> round")

print("\nFLOATING CORNERS OK")
