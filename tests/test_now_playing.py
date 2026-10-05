"""The toolbar's music player: compact at rest (about half its old width),
open under the pointer with previous/next and mute, a progress line, the wheel
skipping 10 s, and a right-click menu that does what it says."""
import _support
from _support import check, qapp, settle

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QEnterEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QMenu

qapp()
from ui_qt import browser_chrome
from ui_qt.browser_chrome import NowPlaying

menus = []


class RecordedMenu(QMenu):
    def exec(self, *a, **k):
        menus.append(self)


browser_chrome.QMenu = RecordedMenu

w = NowPlaying()
w.show()
fired = []
for name in ("activated", "toggle_clicked", "next_clicked", "prev_clicked", "mute_clicked", "download_clicked", "close_clicked"):
    getattr(w, name).connect(lambda n=name: fired.append(n))
w.seek_requested.connect(lambda s: fired.append(("seek", s)))

w.set_media({"title": "A rather long song title — live at the hall", "artist": "Someone", "playing": True,
             "canSkip": True, "position": 30, "duration": 240})
settle(600)
OLD = 10 + 16 + 8 + 150 + 6 + 22 + 48 + 5
rest = w.width()
check(rest <= OLD * 0.5 + 6, "at rest it is %d px, the old control was %d" % (rest, OLD))
check(not w.next_btn.isVisibleTo(w) and not w.mute_btn.isVisibleTo(w) and w.play_btn.isVisibleTo(w),
      "at rest it should show play/pause only")
print("at rest: %d px (old %d), play/pause only" % (rest, OLD))

QApplication.sendEvent(w, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
settle(600)
opened = w.width()
check(opened > rest * 1.8, "it didn't open under the pointer (%d px)" % opened)
check(w.next_btn.isVisibleTo(w) and w.prev_btn.isVisibleTo(w) and w.mute_btn.isVisibleTo(w),
      "open, it should show previous/next and mute")
for b in (w.prev_btn, w.play_btn, w.next_btn, w.mute_btn):
    check(0 <= b.x() and b.x() + b.width() <= w.width(), "a button sits outside the control")
QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))
settle(900)
check(w.width() == rest, "it didn't fold back after the pointer left")
print("under the pointer: %d px with previous/next/mute; folds back" % opened)

frac = w.progress()
check(frac is not None and 0.12 < frac < 0.2, "progress %s for 30 s of 240" % frac)
w.set_muted(True)
check(w.mute_btn.kind == "mute", "the mute icon didn't change")

QApplication.sendEvent(w, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
QApplication.sendEvent(w, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
check(("seek", 10) in fired and ("seek", -10) in fired, "the wheel didn't skip: %s" % fired)

QApplication.sendEvent(w, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5)))
texts = [a.text() for a in menus[-1].actions() if not a.isSeparator()]
want = ["Go to the tab", "Pause", "Back 10 seconds", "Forward 10 seconds", "Previous", "Next", "Unmute tab",
        "Download it in Awesome Downloader", "Close the tab"]
check(texts == want, "menu: %s" % texts)
for a in menus[-1].actions():
    if not a.isSeparator():
        a.trigger()
for name in ("activated", "toggle_clicked", "prev_clicked", "next_clicked", "mute_clicked", "download_clicked", "close_clicked"):
    check(name in fired, "%s never fired from the menu" % name)
print("wheel skips 10 s; the menu's nine actions all do something")

# A live stream (no length): no progress line, no skipping.
fired.clear()
w.set_media({"title": "Radio", "playing": True, "canSkip": False, "position": 0, "duration": 0})
check(w.progress() is None, "a live stream got a progress line")
QApplication.sendEvent(w, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                                      Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
check(not fired, "a live stream was skipped")
print("live streams: no line, no skipping")
print("\nNOW PLAYING OK")
