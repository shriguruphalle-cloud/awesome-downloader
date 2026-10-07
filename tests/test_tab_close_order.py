"""Closing the tab in view goes back to the tab used before it (as Chrome
and Edge do), not to whichever happens to sit next to it in the strip."""
import _support
from _support import build_window, check, qapp, settle

app = qapp()
win, tabs = build_window(tabs=("video", "browser", "download"))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
settle(2500)

first = bt._current()
a = bt._create_tab(activate=True)
b = bt._create_tab(activate=True)
c = bt._create_tab(activate=True)
settle(300)
check(bt._current() is c, "a new tab didn't come to the front")

bt._close(c)
settle(200)
check(bt._current() is b, "closing the newest tab didn't go back to the one before it")
print("closing a tab goes back to the tab used before it")

bt._switch_to(first)
bt._switch_to(b)
settle(200)
bt._close(b)
settle(200)
check(bt._current() is first, "closing went to a neighbour, not to the tab used before (the first one)")
print("...even when that tab is at the other end of the strip")

bt._switch_to(a)
bt._close(first)          # closing a tab that isn't in view changes nothing in view
settle(200)
check(bt._current() is a, "closing a background tab changed the tab in view")
print("closing a tab in the background leaves the one in view alone")

win.close()
settle(500)
print("\nTAB CLOSE ORDER OK")
