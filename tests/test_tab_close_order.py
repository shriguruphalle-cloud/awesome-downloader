"""Closing the tab in view goes to the one beside it: on its right, or on its left if it was the last."""
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

bt._switch_to(first)
bt._switch_to(a)
settle(200)
bt._close(a)              # the tab used before it was the first one: closing doesn't go there
settle(200)
check(bt._current() is b, "closing a tab didn't go to the one on its right")
print("closing a tab goes to the one on its right")

bt._switch_to(c)
settle(200)
bt._close(c)
settle(200)
check(bt._current() is b, "closing the last tab didn't go to the one on its left")
print("...or on its left, when it was the last")

bt._switch_to(b)
bt._close(first)          # closing a tab that isn't in view changes nothing in view
settle(200)
check(bt._current() is b, "closing a background tab changed the tab in view")
print("closing a tab in the background leaves the one in view alone")

win.close()
settle(500)
print("\nTAB CLOSE ORDER OK")
