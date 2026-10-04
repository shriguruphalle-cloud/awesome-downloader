"""The window fits the screen it opens on, a long queue scrolls instead of
squeezing cards over each other, and the Browser runs edge to edge while the
card tabs keep their margins."""
import _support
from _support import build_window, check, pump, qapp, settle, stub_network

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

qapp()
win, tabs = build_window()
vt, bt = tabs["video"], tabs["browser"]
stub_network(vt)

avail = QApplication.primaryScreen().availableGeometry()
g = win.frameGeometry()
print("screen %dx%d, window %dx%d, minimum %dx%d" % (
    avail.width(), avail.height(), win.width(), win.height(),
    win.minimumWidth(), win.minimumHeight()))
check(win.width() <= avail.width() and win.height() <= avail.height(), "window larger than screen")

# ---- a long queue scrolls; no card is squeezed or overlapped ---------------------
win.tabs.setCurrentWidget(vt)
vt.url_entry.setText("\n".join("https://a.com/%d" % i for i in range(12)))
for i, c in enumerate(vt._queue_strips):
    c.resolve({"title": "Video number %d" % i, "uploader": "Chan", "duration": 200,
               "heights": [2160, 1080], "height": 2160, "is_image": False,
               "thumbnail_url": None, "thumb_image": None}, None)
pump(3)
settle()    # the pasted rows open into place
H = type(vt._queue_strips[0]).HEIGHT
for w, h in ((1280, 800), (1006, 706), (900, 600), (760, 480)):
    win.resize(w, h)
    pump(3)
    squeezed = [c.height() for c in vt._queue_strips if c.height() != H]
    tops = sorted(c.mapTo(vt, QPoint(0, 0)).y() for c in vt._queue_strips)
    overlaps = [(a, b) for a, b in zip(tops, tops[1:]) if b < a + H]
    sideways = vt._scroll.horizontalScrollBar().maximum()
    print("  %4dx%-4d squeezed=%d overlaps=%d scrolls=%s sideways=%d" % (
        w, h, len(squeezed), len(overlaps), vt._scroll.verticalScrollBar().maximum() > 0, sideways))
    check(not squeezed and not overlaps, "cards squeezed/overlapping at %dx%d" % (w, h))
    check(sideways == 0, "page scrolls sideways at %dx%d" % (w, h))

# ---- margins follow the tab --------------------------------------------------------
win.resize(1006, 706)
win.tabs.setCurrentWidget(bt)
pump(3)
o = bt.mapTo(win, QPoint(0, 0))
check(o.x() == 0 and o.y() == win.titleBar.height() and bt.width() == win.width(),
      "Browser isn't edge to edge: origin %s width %d" % (o, bt.width()))
win.tabs.setCurrentWidget(vt)
pump(3)
settle()
o = vt.mapTo(win, QPoint(0, 0))
check(o.x() > 0 and o.y() > win.titleBar.height(), "a card tab lost its margins: %s" % o)
print("browser full bleed, card tabs padded")

# ---- a page's fullscreen video covers the whole screen, no dead bands -------------
bt.fullscreen_requested.connect(win.set_video_fullscreen)
win.tabs.setCurrentWidget(bt)
settle()
bt._set_page_fullscreen(True)
settle(900)
o = bt.mapTo(win, QPoint(0, 0))
print("fullscreen: window %dx%d, browser at (%d,%d) %dx%d" % (win.width(), win.height(), o.x(), o.y(),
                                                             bt.width(), bt.height()))
check(win.isFullScreen(), "the window didn't go fullscreen")
check(o.x() == 0 and o.y() == 0 and bt.width() == win.width() and bt.height() == win.height(),
      "the fullscreen page left dead bands around it")
# Touching the top of the screen drops Chrome's round exit button; it leaves fullscreen.
area = win.screen().geometry()
bt._fs_poll(QPoint(area.center().x(), area.top()))
settle(400)
check(bt._fs_exit is not None and bt._fs_exit.isVisible() and bt._fs_exit.is_down(),
      "the exit button didn't drop from the top of the screen")
check(abs(bt._fs_exit.geometry().center().x() - area.center().x()) <= 2, "the exit button isn't centred")
bt._fs_exit.clicked.emit()
settle(900)
check(not win.isFullScreen() and bt.mapTo(win, QPoint(0, 0)).y() == win.titleBar.height(),
      "the exit button didn't restore the window")
check(not bt._fs_exit.is_down(), "the exit button stayed down after leaving fullscreen")
print("page fullscreen: edge to edge; the top-edge exit button brings it back")

print("\nWINDOW LAYOUT OK")
