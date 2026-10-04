"""A maximized window has square corners -- rounded ones cut holes into the
screen's own corners with the desktop showing through.

Reads the real screen: DWM applies the corner shape at composite time, so
nothing the window can render for itself shows it. The window's backdrop is
painted pure magenta for the test, which makes the check exact: a corner
pixel that isn't magenta is the desktop showing through a hole. (An earlier
version of this test called anything near-black a hole, which stops working
the moment the app's own backdrop is near-black.)
"""
import ctypes
from ctypes import wintypes

import _support
from _support import build_window, check, qapp

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter

app = qapp()
from ui_qt import main_window as mw


def _magenta(self, event):
    p = QPainter(self)
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


win, _tabs = build_window(tabs=("video", "download"))
result = {}


def shoot():
    r = wintypes.RECT()
    user32.GetWindowRect(int(win.winId()), ctypes.byref(r))
    corners = {"top-left": (r.left, r.top), "top-right": (r.right - 1, r.top),
               "bottom-left": (r.left, r.bottom - 1), "bottom-right": (r.right - 1, r.bottom - 1)}
    result["maximized"] = win.isMaximized()
    result["corners"] = {k: pixel(*v) for k, v in corners.items()}
    # Whose window is at each corner: another program's always-on-top window
    # (an overlay, a floating toolbar) can sit over a corner, and then the
    # screen there says nothing about this window.
    hwnd = int(win.winId())
    result["covered"] = {k: user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(*v)), 2) != hwnd
                         for k, v in corners.items()}
    pref = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    result["corner_pref"] = pref.value
    app.quit()


QTimer.singleShot(500, win.showMaximized)


def on_top():
    # Above every other window while the corners are read: this test is about
    # the corners' shape, and the screen is shared with whatever else is open
    # (a window left in front read as "the desktop through a corner").
    user32.SetWindowPos(int(win.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_TOPMOST


QTimer.singleShot(1200, on_top)
QTimer.singleShot(2500, shoot)
app.exec()

check(result.get("maximized"), "the window never maximized")
# DWMWA_WINDOW_CORNER_PREFERENCE (33): DWMWCP_DONOTROUND (1) while maximized.
check(result["corner_pref"] == 1, "a maximized window asked for corner preference %d, not square (1)"
      % result["corner_pref"])
read = 0
for name, (r, g, b) in result["corners"].items():
    if result["covered"][name]:
        print("  %-13s covered by another program's window -- not read" % name)
        continue
    read += 1
    painted = r > 200 and b > 200 and g < 80
    print("  %-13s rgb(%3d,%3d,%3d)  %s" % (name, r, g, b, "painted" if painted else "HOLE"))
    check(painted, "the %s corner shows the desktop through it" % name)

print("\nMAXIMIZED CORNERS OK")
