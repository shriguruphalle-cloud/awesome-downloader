"""A browser page is drawn where its tab's page area is -- always.

Reported: after switching tabs, a page was drawn far off to the side, offset
by exactly where the page area had been on screen before the window was
maximized, with the app's backdrop showing in the gap. A page is a native
window that Qt positions from its widget; this checks that it is where the
widget is through tab switches, window moves and size changes, and that a
page window knocked out of place (as in the report) is put back. Local pages
only, no internet."""
import ctypes
import http.server
import socketserver
import sys
import threading
import time
from ctypes import wintypes

import _support  # noqa: F401
from _support import build_window, check, qapp, settle

if sys.platform != "win32":
    print("Windows only")
    sys.exit(0)

app = qapp()
from PySide6.QtCore import QPoint  # noqa: E402

from ui_qt import webview2  # noqa: E402

if not webview2.available():
    print("No WebView2 runtime -- skipped")
    sys.exit(0)

user32 = ctypes.windll.user32


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = ("<!doctype html><title>%s</title><body style='margin:0;background:#2a6'>" % self.path).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]

win, tabs = build_window(tabs=("video", "browser", "download"), size=(1100, 760))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
settle(1200)


def wait(cond, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def rects(view):
    hwnd = int(view.host_window.winId())
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(int(win.winId()), ctypes.byref(origin))
    dpr = view.devicePixelRatioF()
    at = view._container.mapTo(win, QPoint(0, 0))
    want = (origin.x + round(at.x() * dpr), origin.y + round(at.y() * dpr))
    pages = bt.pages.mapTo(win, QPoint(0, 0))
    return (r.left, r.top), want, (origin.x + round(pages.x() * dpr), origin.y + round(pages.y() * dpr))


def check_placed(label):
    for i, tab in enumerate(bt._tabs):
        v = tab.view
        if v is None or v.controller is None or not v.isVisible():
            continue
        have, want, pages = rects(v)
        print("  %-36s tab %d native=%s widget=%s page area=%s" % (label, i, have, want, pages))
        check(abs(have[0] - want[0]) <= 2 and abs(have[1] - want[1]) <= 2,
              "%s: tab %d's page is drawn at %s, its widget is at %s" % (label, i, have, want))
        check(abs(want[0] - pages[0]) <= 2 and abs(want[1] - pages[1]) <= 2,
              "%s: tab %d's page widget is at %s, not the page area %s" % (label, i, want, pages))


cur = bt._current()
bt.navigate(BASE + "/first", tab=cur)
check(wait(lambda: cur.view is not None and cur.view.core is not None), "the first page never started")
settle(800)
for path in ("/b", "/c"):
    bt._open_link(cur, BASE + path, background=True)
settle(300)
check_placed("first page")
win.showMaximized()
settle(900)
for i, tab in enumerate(bt._tabs):
    bt._switch_to(tab)
    settle(700)
    check_placed("maximized, switched to %d" % i)

# ---- knocked out of place, as in the report: put back -----------------------------
view = bt._current().view
hwnd = int(view.host_window.winId())
user32.SetWindowPos(hwnd, 0, 410, 293, 900, 600, 0x0004 | 0x0010)
settle(100)
have, want, _ = rects(view)
check(abs(have[0] - want[0]) > 50, "the test couldn't move the page window out of place")
bt._switch_to(bt._tabs[0])
settle(300)
bt._switch_to(bt._tabs[-1])
settle(600)
check_placed("knocked out, then switched back")

user32.SetWindowPos(int(bt._current().view.host_window.winId()), 0, 410, 293, 900, 600, 0x0004 | 0x0010)
win.showNormal()
settle(900)
check_placed("knocked out, then the window restored")
win.move(win.x() + 120, win.y() + 40)
settle(700)
check_placed("window moved")
win.close()
print("\nBROWSER PLACEMENT OK")
