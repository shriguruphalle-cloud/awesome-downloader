"""The real yt-dlp path when a browser's cookie file is locked.

Reported with an Instagram link in the Images tab: with Chrome open, yt-dlp
can't even copy Chrome's cookie database -- "Could not copy Chrome cookie
database. See https://github.com/yt-dlp/yt-dlp/issues/7271" -- and the fetch
failed with that message in a dialog.

This builds the same situation without touching any real browser: a
stand-in Chrome profile inside the test's own LOCALAPPDATA, its Cookies file
held open with no sharing (the way a running Chrome holds it), and a real
YoutubeDL making a real request -- to a local server, so no network."""
import ctypes
import http.server
import os
import socketserver
import sys
import threading

import _support
from _support import check

if sys.platform != "win32":
    print("Windows only (file locking by share mode)")
    sys.exit(0)

from app.core import downloader  # noqa: E402

# ---- a Chrome profile whose cookie database is in use --------------------------
network_dir = os.path.join(_support.STATE_DIR, "Google", "Chrome", "User Data", "Default", "Network")
os.makedirs(network_dir)
cookies_db = os.path.join(network_dir, "Cookies")
with open(cookies_db, "wb") as f:
    f.write(b"SQLite format 3\x00" + b"\x00" * 4080)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
GENERIC_READ, NO_SHARING, OPEN_EXISTING = 0x80000000, 0, 3
lock = kernel32.CreateFileW(cookies_db, GENERIC_READ, NO_SHARING, None, OPEN_EXISTING, 0, None)
check(lock not in (None, ctypes.c_void_p(-1).value), "couldn't hold the stand-in cookie file")
try:
    open(cookies_db, "rb").close()
    check(False, "the stand-in cookie file wasn't actually locked")
except PermissionError:
    pass


# ---- a local page to fetch --------------------------------------------------------
class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"public post"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = socketserver.TCPServer(("127.0.0.1", 0), Page)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/p/example/" % server.server_address[1]

heard = []
downloader.cookie_fallback_listeners.append(lambda browser, msg: heard.append((browser, msg)))

try:
    opts = downloader.base_ydl_opts("chrome", url)
    check(opts.get("cookiesfrombrowser") == ("chrome",), "Chrome's sign-in wasn't requested")
    body = downloader._run(opts, url, lambda ydl: ydl.urlopen(url).read())
    print("fetched:", body)
    check(body == b"public post", "the fetch didn't go through: %r" % body)
    check("cookiesfrombrowser" not in opts, "the retry still asked for Chrome's cookies")
    check(heard and heard[0][0] == "chrome", "the UI wasn't told: %s" % heard)
    check("could not copy chrome cookie database" in heard[0][1].lower(), heard[0][1])
    print("yt-dlp said:", heard[0][1])

    # The Sign-in setting moves to this app's Browser tab, with a reason.
    from ui_qt.dialogs import signin_dialog  # noqa: E402
    st = {"cookies_from_browser": "chrome"}
    line = signin_dialog.handle_cookie_fallback(st, "chrome", heard[0][1])
    print("notice:", line)
    check(st["cookies_from_browser"] is None, "Sign-in stayed on Chrome")
    check("Browser tab" in line, line)
finally:
    kernel32.CloseHandle(lock)
    server.shutdown()

print("\nLOCKED COOKIE DATABASE OK")
