"""A panel's own title bar (Windows draws it) is in the current palette.

Reported: an emerald About panel under a navy title bar -- the caption colour
was Sapphire's whatever the palette. Each palette is set, a panel opened,
and the caption as Windows composites it read back from the panel's own
window (PrintWindow), compared with the palette's colour."""
import ctypes
import sys

import _support  # noqa: F401
from _support import check, qapp, settle

if sys.platform != "win32":
    print("Windows only")
    sys.exit(0)

app = qapp()
import win32gui  # noqa: E402
import win32ui  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from ui_qt import palettes  # noqa: E402
from ui_qt.dialogs.base import CinematicDialog  # noqa: E402

user32 = ctypes.windll.user32


def capture(hwnd):
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    wdc = win32gui.GetWindowDC(hwnd)
    dc = win32ui.CreateDCFromHandle(wdc)
    mem = dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(dc, w, h)
    mem.SelectObject(bmp)
    user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    img = QImage(bmp.GetBitmapBits(True), w, h, QImage.Format.Format_ARGB32).copy()
    mem.DeleteDC()
    dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, wdc)
    win32gui.DeleteObject(bmp.GetHandle())
    cr = win32gui.GetClientRect(hwnd)
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    return img, cy - t, cx - l


tested = 0
for name in ("emerald", "ruby", "sapphire"):
    if name not in palettes.PALETTES:
        continue
    palettes.set_current(name)
    dialog = CinematicDialog(None, "Palette check", True)
    dialog.resize(420, 260)
    dialog.show()
    settle(700)
    hwnd = int(dialog.winId())
    img, caption_h, left = capture(hwnd)
    if caption_h < 12:
        print("no native caption on this system -- nothing to read")
        dialog.close()
        break
    # A point in the caption well clear of the title text and the buttons.
    c = img.pixelColor(img.width() // 2 + 40, max(4, caption_h // 2 + 6))
    base = palettes.ink(True)
    want = tuple(round(v * 0.72) for v in base)
    got = (c.red(), c.green(), c.blue())
    print("  %-9s caption %s, palette's %s" % (name, got, want))
    check(all(abs(a - b) <= 14 for a, b in zip(got, want)),
          "%s: the panel's title bar is %s, not the palette's %s" % (name, got, want))
    tested += 1
    dialog.close()
    settle(150)
palettes.set_current(palettes.DEFAULT)
print("\nDIALOG THEME OK (%d palettes)" % tested)
