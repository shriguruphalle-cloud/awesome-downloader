"""Closing the window while a private tab is open raised, from WebView2:
"CoreWebView2Controller members cannot be accessed after the WebView2 control
is disposed" -- leaving the private tab recoloured a page whose controller
WebView2 had already disposed. Nothing may escape on that path now."""
import sys

import _support
from _support import build_window, check, qapp, settle

app = qapp()
escaped = []
sys.excepthook = lambda t, v, tb: escaped.append("%s: %s" % (t.__name__, v))

win, tabs = build_window(tabs=("video", "browser", "download"))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
settle(6000)
bt._create_tab(activate=True, private=True)
settle(4000)
win.close()
settle(2500)
check(not escaped, "closing on a private tab raised: %s" % escaped[:2])
print("closing on a private tab: nothing raised")

# The guard itself: a disposed controller is dropped, not touched again.
from ui_qt.webview2 import WebView2Widget  # noqa: E402


class Dead:
    def __setattr__(self, name, value):
        raise RuntimeError("CoreWebView2Controller members cannot be accessed after the WebView2 control is disposed.")


view = WebView2Widget.__new__(WebView2Widget)
view.controller = Dead()
view.core = object()
view._closed = False
check(view._call(lambda c: setattr(c, "IsVisible", True)) is False, "a disposed controller was used")
check(view.controller is None and view._closed, "a disposed controller was kept")
print("a disposed controller is dropped")
print("\nBROWSER SHUTDOWN OK")
