"""With a page open, nothing could be typed or deleted in the address bar.

The suggestions list under the field is a popup; every time it opened or
refreshed, Qt sent the field a focus-out and focus-in "because of a popup",
and the field took them as real: on focus-in it put the page's address back
and selected all of it, on focus-out the short form -- between keystrokes.

Checked two ways: the popup's focus events sent straight at the field, and
real-looking typing (WM_KEYDOWN/WM_KEYUP posted to the window that has
Windows focus, which the event loop translates like a keyboard's) on a page
made from a string -- no network, nothing outside this test's window."""
import ctypes
from ctypes import wintypes

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFocusEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

user32 = ctypes.windll.user32
user32.GetFocus.restype = wintypes.HWND

win, tabs = build_window(tabs=("video", "browser", "download"), size=(1280, 800))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
win.show()
win.activateWindow()
settle(5000)
bt._create_tab(url="https://example.invalid/", activate=True)
settle(3000)
view = bt.current_view()
check(view is not None and view.core is not None, "the page didn't start")
view.core.NavigateToString("<html><body style='background:#123'><p>page</p></body></html>")
settle(2000)
edit = bt.address.edit

# 1. The popup's own focus events leave the text being typed alone.
bt._focus_page()
settle(600)
QTest.mouseClick(edit, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, edit.height() // 2))
settle(400)
check(edit.editing(), "clicking the address bar didn't start editing")
edit.setText("my search")
for kind in (QEvent.Type.FocusOut, QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.FocusIn):
    QApplication.sendEvent(edit, QFocusEvent(kind, Qt.FocusReason.PopupFocusReason))
settle(200)
check(edit.text() == "my search", "the suggestions popup's focus events replaced the text: %r" % edit.text())
check(not edit.hasSelectedText(), "the suggestions popup's focus events selected the text")
check(edit.editing(), "the suggestions popup ended editing")
bt.address.set_url("https://example.invalid/other")      # the page moving on meanwhile
check(edit.text() == "my search", "a page update overwrote the text being typed")
print("the suggestions popup's focus events leave the typing alone")

# 2. Typing, as a keyboard would, on an open page.
bt._focus_page()
settle(800)
QTest.mouseClick(edit, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, edit.height() // 2))
settle(600)
check(user32.GetFocus() == int(win.winId()), "the keyboard didn't come back to the window from the page")


def key(vk):
    h = user32.GetFocus()
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.PostMessageW(h, 0x0100, vk, 1 | (scan << 16))
    user32.PostMessageW(h, 0x0101, vk, 1 | (scan << 16) | (1 << 30) | (1 << 31))
    settle(60)


for ch in "hello":
    key(ord(ch.upper()))
key(0x08)                                                 # Backspace
settle(800)
check(edit.text() == "hell", "typing 'hello' + Backspace in the address bar gave %r" % edit.text())
print("typing and Backspace work in the address bar on an open page")

# 3. Leaving for real still shows the page's address again.
bt._focus_page()
settle(800)
check(not edit.editing(), "clicking into the page didn't end editing")
check(edit.text() == "example.invalid/other", "leaving the field didn't show the page's address: %r" % edit.text())
print("leaving the field shows the page's address")

win.close()
settle(1500)
print("\nADDRESS BAR TYPING OK")
