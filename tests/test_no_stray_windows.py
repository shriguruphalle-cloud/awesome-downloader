"""Building a panel or a card never flashes up a window of its own.

A widget with no parent yet that is made visible opens as a top-level window
-- for a few ms, until a layout adopts it. The All bookmarks panel did this
with its list: the stray window took the activation as it came and went, and
Qt closed the panel with it, so a click on All bookmarks opened and shut it
in a blink (reported twice). The download card's Pause button did the same."""
import _support
from _support import check, qapp, settle

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

qapp()
from ui_qt import theme
from ui_qt.browser_chrome import BookmarksPanel
from ui_qt.widgets.download_card import DownloadCard


class ShownWindows(QObject):
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.shown.append(type(obj).__name__)
        return False


spy = ShownWindows()
QApplication.instance().installEventFilter(spy)
t = theme.tokens(True)
host = QWidget()
host.resize(600, 400)
host.show()
settle(200)

# ---- All bookmarks, full and empty -------------------------------------------------
for n in (12, 0):
    items = [{"url": "https://site%02d.example/" % i, "title": "Example site %02d" % i} for i in range(n)]
    spy.shown.clear()
    panel = BookmarksPanel(host, t, True, items, bar_visible=True)
    check(not spy.shown, "building the bookmarks panel (%d bookmarks) opened windows: %s" % (n, spy.shown))
    panel.open_under(host)
    settle(300)
    check(spy.shown == ["BookmarksPanel"], "opening the panel showed %s" % spy.shown)
    check(panel.isVisible(), "the bookmarks panel (%d bookmarks) closed as it opened" % n)
    panel.close()
    settle(100)
print("bookmarks panel: opens on its own, and stays open")

# ---- a download card with a Pause button ---------------------------------------------
spy.shown.clear()
card = DownloadCard("A sample video", "720p · mp4", QPixmap(), True, on_cancel=lambda: None,
                    on_pause_toggle=lambda: None)
check(not spy.shown, "building a download card opened windows: %s" % spy.shown)
check(not card.pause_btn.isHidden(), "the Pause button is missing")
print("download card: no stray windows")

host.close()
print("\nNO STRAY WINDOWS OK")
