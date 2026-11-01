"""Browser extensions, with a package made here and a local site (no internet): the .crx read and unpacked as the
engine needs it, the Store's install taken over from the download, a site unable to install anything, and an extension
really running in a page: added, switched off and on, and removed."""
import base64
import hashlib
import http.server
import io
import json
import os
import socketserver
import struct
import threading
import time
import zipfile

import _support
from _support import build_window, check, qapp, settle

from app.core import extensions as core

KEY = base64.b64decode("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzXUHjqYvCo7m9GiNmNNkZebRZ6P7fMrFJXi1Ddxx0fSffkRLbuL8MapnHVxvHMrTFfDKhAuOz6XC9EKz/zsQPcffizhMK25HQAt1CFdEr7st9szKZHQ4bzmJ/bpYq5+v9kkkC5kgos7lVfUumlO8mIBhW5xXBUmuDSXYdAMwOPy1J0BTfKhX9ZTm1dmZhRO1UT8dW+JLmi2/mRkWBkmqjgna6x9MYzdYatT9TSEl7kPX+fu2/KOG0TVvZf8G1zCauIw4kKsX4APUbWrgmC8n+AGg/FVnYDSZJ6HooRDwAd7gpu5rjKqcIZC36yEHS7hay/1o7cDOgkbcAdxL1wnH6QIDAQAB")     # a made-up RSA public key
ID = core.id_of_key(KEY)


def varint(n):
    out = b""
    while True:
        b = n & 0x7f
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(number, data):
    return varint(number << 3 | 2) + varint(len(data)) + data


MANIFEST = {"manifest_version": 3, "name": "__MSG_appName__", "version": "1.2", "default_locale": "en",
            "description": "Made up", "action": {"default_popup": "popup.html"}, "options_page": "popup.html",
            "update_url": "https://clients2.google.com/service/update2/crx",
            "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["mark.js"]}]}


def package(extra=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(MANIFEST))
        z.writestr("_locales/en/messages.json", json.dumps({"appName": {"message": "Test Marker"}}))
        z.writestr("_metadata/verified_contents.json", "[]")
        z.writestr("mark.js", "document.documentElement.dataset.marked = 'yes';")
        z.writestr("popup.html", "<!doctype html><title>Popup</title><p>made up</p>")
        for name, data in extra:
            z.writestr(name, data)
    proof = field(1, KEY) + field(2, b"signature")
    head = field(2, proof) + field(10000, field(1, hashlib.sha256(KEY).digest()[:16]))
    return b"Cr24" + struct.pack("<II", 3, len(head)) + head + buf.getvalue()


# ---- which downloads are the Store's ----
store = "https://chromewebstore.google.com/detail/some-name/" + ID + "?hl=en"
check(core.store_id(store) == ID, "the Store page's ID")
check(core.store_id("https://example.com/detail/x/" + ID) is None, "another site isn't the Store")
uri = "https://clients2.googleusercontent.com/crx/blobs/abc/x.crx"
check(core.store_download(store, uri, ID + "_53342.crx") == ID, "the Store's download")
check(core.store_download("https://evil.example/", uri, ID + "_1.crx") is None, "a site could install by downloading")
check(core.store_download(store, "https://evil.example/x.crx", ID + "_1.crx") is None, "a .crx from elsewhere")
check(core.store_download(store, "https://clients2.google.com/other", "a.pdf") is None, "another download")
print("only the Store's own downloads install anything")

# ---- the package ----
info = core.inspect(package())
check(info["id"] == ID and info["key"] == KEY, "the ID and key: %s" % info["id"])
check(info["name"] == "Test Marker" and info["popup"] == "popup.html", "its details: %s" % info["name"])
check(any("127.0.0.1" in c for c in info["can"]), "what it can do: %s" % info["can"])
bad = bytearray(package())
bad[:4] = b"Nope"
for blob in (bytes(bad), b"", package()[:20]):
    try:
        core.inspect(blob)
        check(False, "a bad package was accepted")
    except core.ExtensionError:
        pass
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("manifest.json", json.dumps(MANIFEST))
    z.writestr("../../outside.txt", "x")
try:
    core.unpack(core.inspect(buf.getvalue()), "file")
    check(False, "a file landed outside its folder")
except core.ExtensionError:
    pass
print("bad packages are refused")

# ---- a local site ----
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>Plain</title><p>a page</p>")

    def log_message(self, *a):
        pass


server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d/" % server.server_address[1]
app = qapp()
win, tabs = build_window(tabs=("browser", "download"))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)


def wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        settle(100)
    return False


check(wait(lambda: bt.services.extensions_ready(), 60), "the engine didn't start")
tab = bt._current()
bt.navigate(BASE, tab=tab)
check(wait(lambda: tab.title == "Plain"), "the page didn't load")


def marked():
    box = {}
    tab.view.evaluate("document.documentElement.dataset.marked || 'no'", lambda v: box.update(v=v))
    check(wait(lambda: "v" in box, 10), "the page didn't answer")
    return box["v"]


def loaded():
    box = {}
    bt.services.extensions_list(lambda items: box.update(items=items))
    check(wait(lambda: "items" in box, 15), "the list didn't come")
    return box["items"]


check(marked() == "no", "marked before anything was added")
check(loaded() == [], "extensions at the start: %s" % loaded())

# the Store's install: the download is taken over and nothing lands in Downloads
class Op:
    Uri = uri


class Args:
    DownloadOperation = Op
    ResultFilePath = "C:/x/" + ID + "_53342.crx"
    Cancel = False
    Handled = False


class StoreTab:
    url = store
    private = False


asked, fetched = [], []
core.fetch = lambda ext_id: (fetched.append(ext_id), core.inspect(package()))[1]
bt.extensions.confirm = lambda i: (asked.append(i["name"]), True)[1]
n = len(bt._downloads)
a = Args()
bt._on_download_starting(StoreTab, a)
check(a.Cancel is True and len(bt._downloads) == n, "the .crx went to Downloads")
check(wait(lambda: asked and len(loaded()) == 1, 40), "the extension wasn't added: %s" % loaded())
check(fetched == [ID] and asked == ["Test Marker"], "asked: %s" % asked)
e = loaded()[0]
check(e["id"] == ID, "the engine's ID isn't the Store's: %s" % e["id"])
check(e["name"] == "Test Marker" and e["enabled"], "its row: %s" % e["name"])
check(e["meta"]["popup"] == "popup.html", "its details: %s" % e["meta"])
folder = e["meta"]["folder"]
check(not os.path.exists(os.path.join(folder, "_metadata")), "_metadata would stop it loading")
print("the Store's install: added under its own ID, after asking")

check(wait(lambda: marked() == "yes", 15), "the extension didn't run in the page")
print("it runs in pages")

# using it: the panel, its popup
bt.extensions.show_panel()
check(wait(lambda: bt._panel is not None and bt._panel.isVisible(), 10), "the panel didn't open")
check([r[0]["name"] for r in bt._panel.rows] == ["Test Marker"], "the panel's rows")
settle(300)
win.grab().save(os.path.join(os.environ.get("TEMP", "."), "awd_ext_toolbar.png"))
bt._panel.close()
settle(100)
w = bt.extensions.open_window("chrome-extension://%s/popup.html" % ID, "Test Marker")
seen = {}


def ask_title():
    if w.view.core is not None:
        w.view.evaluate("document.title", lambda v: seen.update(t=v))
    return seen.get("t") == "Popup"


check(wait(ask_title, 20), "the popup didn't open: %r" % seen.get("t"))
w.close()
settle(200)
print("the panel lists it; its popup opens")

# off, then on
done = []
bt.services.extension_enable(e["ext"], False, lambda *_a: done.append(1), lambda m: check(False, m))
check(wait(lambda: done, 10), "it didn't switch off")
check(not loaded()[0]["enabled"], "still on")
tab.view.reload()
settle(800)
check(wait(lambda: tab.title == "Plain") and wait(lambda: marked() == "no", 10), "it still ran while off")
done.clear()
bt.services.extension_enable(e["ext"], True, lambda *_a: done.append(1), lambda m: check(False, m))
check(wait(lambda: done, 10), "it didn't switch on")
tab.view.reload()
check(wait(lambda: marked() == "yes", 15), "it didn't run once back on")
print("off and on")

# a new version over the old one keeps its ID
MANIFEST["version"] = "1.3"
bt.extensions.add(core.inspect(package()), "store")
check(wait(lambda: any(x["meta"].get("version") == "1.3" for x in loaded()), 25), "the update wasn't kept")
check([x["id"] for x in loaded()] == [ID], "the update: %s" % [x["id"] for x in loaded()])
print("an update replaces it, same ID")

# a site's own .crx stays an ordinary file; and it's refused in a private tab
class SiteTab:
    url = "https://evil.example/"
    private = False


b = Args()
check(bt.extensions.take_download(SiteTab, b) is False and b.Cancel is False, "a site's .crx was taken as an install")

# removed
gone = []
bt.extensions.remove(loaded()[0], lambda: gone.append(1))
check(wait(lambda: gone, 15), "it wasn't removed")
check(loaded() == [] and not os.path.exists(folder), "still there after removing")
tab.view.reload()
settle(800)
check(wait(lambda: marked() == "no", 10), "it still ran after removing")
print("removed, and its files with it")

win.close()
settle(300)
print("\nEXTENSIONS OK")
