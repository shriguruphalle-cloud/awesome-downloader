"""The Browser tab on WebView2, against a local server (no internet).

What has broken before, or is the reason the engine changed:
  * H.264 video: Qt WebEngine couldn't play it at all;
  * the video overlay and the toolbar's Download button;
  * middle-click opening a tab in the background -- lazily, and without it
    starting to play;
  * pop-ups: a page opening windows by itself is blocked, a click isn't, and
    window.open keeps its opener (sign-in pop-ups need it);
  * downloads the page starts land in the Download tab;
  * magnet links reach the Torrent tab;
  * closing the last tab, Back/Forward through the home page, mute, and
    Escape getting the window out of a stuck fullscreen."""
import http.server
import json
import os
import socketserver
import sys
import threading
import time

import _support
from _support import check, qapp, settings

if sys.platform != "win32":
    print("WebView2 is Windows-only")
    sys.exit(0)

app = qapp()

from app.utils import browser_data  # noqa: E402
from ui_qt import webview2  # noqa: E402
from ui_qt.browser_tab import BrowserTab, normalize_address  # noqa: E402
from ui_qt.download_tab import DownloadTab  # noqa: E402

if not webview2.available():
    print("No WebView2 runtime on this machine -- skipped:", webview2.load()[1])
    sys.exit(0)

# ---- addresses ------------------------------------------------------------------
check(normalize_address("example.com") == "https://example.com", normalize_address("example.com"))
check(normalize_address("localhost:8080/x") == "http://localhost:8080/x", "localhost")
check(normalize_address("192.168.1.2") == "http://192.168.1.2", "ip")
check(normalize_address("https://a.b/c d") == "https://a.b/c d", "scheme kept")
check(normalize_address("how to cook rice").startswith(
    browser_data.SEARCH_ENGINES[browser_data.get_search_engine()][1]), "search")
check("q=a+b" in normalize_address("a b").replace("%20", "+"), normalize_address("a b"))

# ---- a local site -----------------------------------------------------------------
PAGES = {
    "/video.html": b"""<!doctype html><title>Video page</title><body>
<video id=v width=480 height=270></video>
<p><a id=bg href="/other.html">other</a></p>
<p><button id=opener onclick="window.open('/popup.html')" style="width:300px;height:40px">open</button></p>
<p><a id=mag href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=x">magnet</a></p>
</body>""",
    "/other.html": b"<!doctype html><title>Other page</title><p>other",
    "/popup.html": b"<!doctype html><title>Popup page</title><script>window.__opener=!!window.opener</script>",
    "/autopopup.html": b"<!doctype html><title>Auto popup</title>"
                       b"<script>setTimeout(function(){window.open('/popup.html')},200)</script>",
}


def _tone_wav(seconds=6, rate=8000):
    """A quiet 440 Hz tone, as a WAV -- something for a page to play."""
    import math
    import struct
    frames = bytes(128 + int(20 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(seconds * rate))
    return (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8) + b"data" + struct.pack("<I", len(frames)) + frames)


TONE = _tone_wav()
PAGES["/adplayer.html"] = (
    b"<!doctype html><title>Ad player</title>"
    b"<div id=player style='position:relative;width:480px;height:270px'>"
    b"<video width=480 height=270></video>"
    b"<button id=intro style='position:absolute;left:8px;top:8px' onclick='window.__intro=1'>Skip intro</button>"
    b"</div>"
    b"<p id=prose>You can skip ads with a subscription.</p>"
    b"<script>window.__skipped=0;setTimeout(function(){var b=document.createElement('div');"
    b"b.style.cssText='cursor:pointer;width:120px;height:40px;position:absolute;right:8px;bottom:8px';"
    b"b.onclick=function(){window.__skipped++};"
    b"var t=document.createElement('span');t.textContent='Skip Ad \\u203a';b.appendChild(t);"
    b"document.getElementById('player').appendChild(b)},500)</script>")
PAGES["/media.html"] = (b"<!doctype html><title>Media page</title>"
                        b"<audio id=a src='/tone.wav' autoplay muted loop></audio>")
SLOW_SIZE = 3 * 1024 * 1024


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/tone.wav"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(TONE)))
            self.end_headers()
            self.wfile.write(TONE)
            return
        if self.path.startswith("/slow.bin"):
            # About two seconds end to end: long enough to pause in the middle.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="slow-file.bin"')
            self.send_header("Content-Length", str(SLOW_SIZE))
            self.end_headers()
            chunk = b"y" * (64 * 1024)
            try:
                for _ in range(SLOW_SIZE // len(chunk)):
                    self.wfile.write(chunk)
                    time.sleep(0.04)
            except (ConnectionError, OSError):
                pass
            return
        if self.path.startswith("/file.bin"):
            body = b"x" * (512 * 1024)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="example-file.bin"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = PAGES.get(self.path.split("?")[0])
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


server = Server(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]


def wait(cond, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        try:
            if cond():
                return True
        except Exception:   # noqa: BLE001 -- not ready yet
            pass
        time.sleep(0.01)
    return False


def js(view, code):
    box = {}
    view.run_js(code, lambda r: box.setdefault("r", r))
    wait(lambda: "r" in box, 10)
    return box.get("r")


def click(view, element_id):
    rect = json.loads(js(view, "JSON.stringify(document.getElementById('%s').getBoundingClientRect())"
                            % element_id))
    for kind in ("mousePressed", "mouseReleased"):
        webview2.then(view.core.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", json.dumps(
            {"type": kind, "x": rect["x"] + 10, "y": rect["y"] + 10, "button": "left", "clickCount": 1})))


st = settings()
dl = DownloadTab(settings=st)
bt = BrowserTab(settings=st, download_tab=dl)
bt.download_dir = os.path.join(_support.STATE_DIR, "downloads")
sent, magnets = [], []
bt.open_in_video_tab.connect(sent.append)
bt.magnet_requested.connect(magnets.append)
bt.resize(1000, 700)
bt.show()

# ---- closing the last tab leaves one fresh home tab; nothing loads for it ----------
check(len(bt._tabs) == 1 and bt._tabs[0].on_home, "a fresh browser isn't one home tab")
bt._close(bt._tabs[0])
check(len(bt._tabs) == 1 and bt._tabs[0].on_home and bt._current() is bt._tabs[0],
      "closing the last tab didn't leave a fresh home tab")
check(bt.home.is_surface(bt.pages.currentWidget()), "the home page isn't showing")

# ---- the home page: the app's own HTML page, given its state, live ---------------------
check(wait(lambda: bt.home._loaded, 25), "the home page never loaded and asked for its state")
check(bt.pages.currentWidget() is bt.home.view, "the home page's view isn't the one showing")
tiles = js(bt.home.view, "document.querySelectorAll('#tiles .tile').length")
check(tiles and tiles >= 2, "the home page shows no tiles: %r" % tiles)
font = js(bt.home.view, "document.fonts.check('40px \"Instrument Serif\"')")
check(font is True, "the name isn't set in Instrument Serif")
layers = js(bt.home.view, "window.__home.layers().length")
check(layers and layers >= 1, "no wallpaper layers: %r" % layers)
# The default wallpaper moves: a live scene, drawn by the GPU, running.
live = js(bt.home.view, "JSON.stringify(window.__home.live())")
live = json.loads(live) if live else {}
check(live.get("name") == "silk" and live.get("running"), "the default live wallpaper isn't running: %r" % live)
# ...and stops, still frame shown, when animation is switched off.
bt.home._on_message({"type": "set", "key": "home_animate", "value": False})
check(wait(lambda: not json.loads(js(bt.home.view, "JSON.stringify(window.__home.live())"))["running"], 5),
      "switching the animation off didn't stop it")
bt.home._on_message({"type": "set", "key": "home_animate", "value": True})
before = len(bt._tabs)
js(bt.home.view, "window.__home.click(0, 1)")
check(wait(lambda: len(bt._tabs) == before + 1, 5), "a middle-click on a home tile didn't open a tab")
check(bt._current().on_home, "a middle-click took you away from the home page")
bt._close(bt._tabs[-1])
print("home page: loaded, %d tiles, Instrument Serif, %d wallpaper layers, middle-click opens behind"
      % (tiles, layers))

# ---- a page, and H.264 --------------------------------------------------------------
check(wait(lambda: bt.services.engine is not None and bt.services.engine.env is not None, 20),
      "WebView2 didn't start: %s" % bt.services.error)
bt.navigate(BASE + "/video.html")
tab = bt._current()
check(wait(lambda: not tab.loading and tab.title == "Video page", 20), "page didn't load: %r" % tab.title)
check(bt.address.url() == BASE + "/video.html", bt.address.url())
codecs = json.loads(js(tab.view, "JSON.stringify(['video/mp4; codecs=\"avc1.42E01E, mp4a.40.2\"',"
                                 "'video/webm; codecs=\"vp9\"'].map(c=>document.createElement('video').canPlayType(c)))"))
print("canPlayType H.264, VP9:", codecs)
check(codecs[0] == "probably", "H.264 isn't playable")

# ---- the downloader can use this browser's sign-in -----------------------------------
# A download thread asks for the site's cookies; the answer comes from the
# engine's own cookie store, through the GUI thread.
js(tab.view, "document.cookie = 'awd_session=abc123; max-age=3600; path=/'; 1")
box = {}
asker = threading.Thread(target=lambda: box.setdefault("rows", bt.services._cookie_provider({"127.0.0.1"})))
asker.start()
check(wait(lambda: "rows" in box, 12), "the cookie provider never answered")
names = [row[1] for row in (box["rows"] or [])]
print("cookies handed to the downloader:", names)
check("awd_session" in names, "a cookie the page set didn't reach the downloader")

# ---- the overlay button and the Download button -----------------------------------
check(wait(lambda: tab.has_video, 8), "the page's video wasn't noticed")
check(bt.download_btn.isEnabled() and bt.download_btn._lit._target == 1, "Download button didn't light")
js(tab.view, "document.getElementById('__awd_overlay_btn').click(); 1")
check(wait(lambda: sent, 5) and sent[-1] == BASE + "/video.html", "overlay click didn't send the page: %s" % sent)
bt.download_btn.click()
check(len(sent) == 2, "the toolbar's Download button didn't send the page")

# ---- a player's own "Skip Ad" is pressed as soon as it appears; nothing else is ----
tab.view.load(BASE + "/adplayer.html")
check(wait(lambda: tab.title == "Ad player", 10), "ad player page didn't load")
check(wait(lambda: js(tab.view, "window.__skipped") >= 1, 6), "the Skip Ad button wasn't pressed")
check(not js(tab.view, "window.__intro"), "a 'Skip intro' button was pressed too")
print("skip ad: pressed, 'Skip intro' left alone")
tab.view.load(BASE + "/video.html")
check(wait(lambda: not tab.loading and tab.title == "Video page", 10), "video page didn't come back")

# ---- middle-click: a background tab that doesn't load until opened ----------------
n = len(bt._tabs)
js(tab.view, "document.getElementById('bg').dispatchEvent("
             "new MouseEvent('auxclick',{button:1,bubbles:true,cancelable:true})); 1")
check(wait(lambda: len(bt._tabs) == n + 1, 5), "middle-click didn't open a tab")
bg = bt._tabs[bt._tabs.index(tab) + 1]
check(bt._current() is tab, "the background tab took focus")
check(bg.view is None and bg.pending_url == BASE + "/other.html", "the background tab loaded early")
bt._switch_to(bg)
check(wait(lambda: bg.title == "Other page", 15), "the background tab didn't load when opened")
bt._switch_to(tab)

# ---- pop-ups ---------------------------------------------------------------------
n = len(bt._tabs)
tab.view.load(BASE + "/autopopup.html")
check(wait(lambda: tab.title == "Auto popup", 10), "autopopup didn't load")
wait(lambda: False, 1.2)
check(len(bt._tabs) == n, "a page opened a window by itself")
tab.view.load(BASE + "/video.html")
check(wait(lambda: not tab.loading and tab.title == "Video page", 10), "video page didn't reload")
click(tab.view, "opener")
check(wait(lambda: len(bt._tabs) == n + 1, 8), "a clicked window.open didn't open a tab")
pop = bt._current()
check(wait(lambda: pop.title == "Popup page", 10), "the pop-up tab didn't load")
check(js(pop.view, "window.__opener") is True, "the pop-up lost its opener")
js(pop.view, "window.close(); 1")
check(wait(lambda: pop not in bt._tabs, 5), "window.close() didn't close the tab")
check(bt._current() is tab, "window.close() didn't go back to the opener")

# ---- magnet links ----------------------------------------------------------------
js(tab.view, "document.getElementById('mag').click(); 1")
check(wait(lambda: magnets, 6), "the magnet link didn't reach the app")

# ---- a download lands in the Download tab ------------------------------------------
cards = len(dl._cards)
tab.view.load(BASE + "/file.bin")
check(wait(lambda: len(dl._cards) == cards + 1, 10), "no Download-tab card for a page download")
target = os.path.join(bt.download_dir, "example-file.bin")
check(wait(lambda: os.path.exists(target) and os.path.getsize(target) == 512 * 1024 and not bt._downloads, 20),
      "the download didn't finish in the download folder")

# ---- a download paused in the middle stops, and resumed it finishes ---------------
tab.view.load(BASE + "/slow.bin")
check(wait(lambda: bt._downloads, 10), "the slow download didn't start")
job = bt._downloads[-1]
check(wait(lambda: int(job.op.BytesReceived) > 256 * 1024, 10), "the slow download isn't arriving")
job.set_paused(True)
wait(lambda: False, 0.6)
held = int(job.op.BytesReceived)
wait(lambda: False, 0.8)
print("paused at %d bytes, %d after 0.8s; state %s" % (held, int(job.op.BytesReceived), job.op.State))
check(int(job.op.BytesReceived) == held, "a paused download kept receiving")
check(held < SLOW_SIZE, "the download finished before it could be paused")
job.set_paused(False)
slow_path = os.path.join(bt.download_dir, "slow-file.bin")
check(wait(lambda: os.path.exists(slow_path) and os.path.getsize(slow_path) == SLOW_SIZE and job.finished, 20),
      "the resumed download didn't finish")

# ---- Now Playing follows whatever plays in any tab ---------------------------------
media_tab = bt._create_tab(url=BASE + "/media.html", activate=True)
check(wait(lambda: media_tab.view is not None and media_tab.title == "Media page", 15), "media page didn't load")
wait(lambda: False, 1.0)
check(bt.now_playing.media() is None, "Now Playing showed media that never played")
js(media_tab.view, "document.getElementById('a').play(); 1")
check(wait(lambda: bt.now_playing.media() is not None and bt.now_playing.media().get("playing"), 15),
      "Now Playing never showed the playing tab")
check(bt.now_playing.isVisible(), "the Now Playing control stayed hidden")
bt._media_command("toggle")
check(wait(lambda: not (bt.now_playing.media() or {}).get("playing", True), 8), "play/pause didn't reach the page")
print("now playing: shown, and pausing from the toolbar reached the page")
bt._close(media_tab)
check(wait(lambda: bt.now_playing.media() is None, 5), "Now Playing outlived its tab")

# ---- a background tab goes to sleep, and wakes when opened -------------------------
bt.SLEEP_AFTER_S = 0
bt._switch_to(tab)
check(bg.view is not None, "the background tab has no page to put to sleep")
bt._sleep_idle_tabs()
check(wait(lambda: bg.view.is_suspended(), 8), "the idle background tab wasn't put to sleep")
check(bg.pill.sleeping, "a sleeping tab doesn't look it")
bt._switch_to(bg)
check(wait(lambda: not bg.view.is_suspended(), 8) and not bg.pill.sleeping, "the tab didn't wake when opened")
bt.SLEEP_AFTER_S = BrowserTab.SLEEP_AFTER_S
bt._switch_to(tab)
print("sleeping tabs: asleep in the background, awake when opened")

# ---- dragging a tab reorders them -------------------------------------------------
first = bt._tabs[0]
last_pill = bt._tabs[-1].pill
target_x = last_pill.mapToGlobal(last_pill.rect().center()).x() + 40
bt.strip._on_drag(first.pill, first.pill.mapToGlobal(first.pill.rect().center()).x())
bt.strip._on_drag(first.pill, target_x)
bt.strip._on_drop(first.pill)
check(bt._tabs[-1] is first and bt.strip.pills[-1] is first.pill, "dragging the first tab to the end didn't move it")
print("tab drag: reordered")

# ---- Back through the tab's first page lands on home; Forward returns ---------------
for _ in range(15):
    if tab.on_home:
        break
    before = tab.url
    bt.go_back()
    wait(lambda: tab.on_home or (tab.url != before and not tab.loading), 6)
    wait(lambda: False, 0.15)
check(tab.on_home and bt.home.is_surface(bt.pages.currentWidget()), "Back never reached the home page")
check(bt.fwd_btn.isEnabled(), "Forward disabled on the home page")
bt.go_forward()
check(not tab.on_home and bt.pages.currentWidget() is tab.view, "Forward didn't return to the page")

# ---- mute ------------------------------------------------------------------------
bt._toggle_mute(tab)
check(wait(lambda: tab.muted, 5), "mute didn't take")
bt._toggle_mute(tab)
check(wait(lambda: not tab.muted, 5), "unmute didn't take")

# ---- Escape restores the window if a page left it stuck fullscreen ----------------
restored = []


class _Win:
    def isFullScreen(self):
        return True

    def set_video_fullscreen(self, on):
        restored.append(on)


real_window = bt.window
bt.window = lambda: _Win()
bt._set_page_fullscreen(True)
check(not bt.chrome.isVisibleTo(bt), "fullscreen left the browser chrome showing")
bt._escape_fallback()
check(restored and restored[-1] is False, "Escape didn't bring the window out of fullscreen")
check(bt.chrome.isVisibleTo(bt), "Escape left the browser chrome hidden")
bt.window = real_window

# ---- the open tabs are saved for next time; private ones never are ----------------
bt._create_tab(url=BASE + "/other.html", activate=False, private=True)
bt.save_state()
urls, _current = browser_data.load_session()
check(sorted(urls) == sorted([BASE + "/video.html", BASE + "/other.html"]),
      "saved %s -- expected the two normal tabs and not the private one" % urls)
check(urls == [t.url for t in bt._tabs if not t.private and not t.on_home],
      "the saved order isn't the tab strip's order: %s" % urls)

# ---- Ctrl+Shift+T still works after a restart ----------------------------------
gone = bt._create_tab(url=BASE + "/autopopup.html?closed", activate=False)
bt._close(gone)
bt.save_state()
session_urls, _ = browser_data.load_session()

after = BrowserTab(settings=st, download_tab=dl)          # the next launch
check(sorted(t.url for t in after._tabs) == sorted(session_urls), "the open tabs didn't come back at start")
n = len(after._tabs)
after.reopen_closed_tab()
check(len(after._tabs) == n + 1 and after._current().url == BASE + "/autopopup.html?closed",
      "Ctrl+Shift+T after a restart didn't bring back the tab closed before it")

browser_data.set_pref("restore_tabs", False)
bt.save_state()
fresh = BrowserTab(settings=st, download_tab=dl)          # a launch that doesn't reopen tabs
check(len(fresh._tabs) == 1 and fresh._tabs[0].on_home, "tabs reopened at start with that switched off")
fresh.reopen_closed_tab()
reopened = [t.url for t in fresh._tabs if not t.on_home]
check(sorted(reopened) == sorted(session_urls), "Ctrl+Shift+T didn't bring back the last session: %s" % reopened)
check(all(t.view is None for t in fresh._tabs), "restored tabs loaded pages before being opened")
browser_data.set_pref("restore_tabs", True)
print("Ctrl+Shift+T across restarts: closed tab and whole last session come back, unloaded")


# ---- a click on the bars doesn't hand the keyboard to the page ------------------------
# (handing it over makes the app lose focus, which closed a panel the click
# had just opened -- reported as All bookmarks not opening)
from PySide6.QtCore import QPoint  # noqa: E402
page_tab = next((t for t in bt._tabs if not t.on_home and t.view is not None and not t.private), None)
if page_tab is not None:
    bt._switch_to(page_tab)
    wait(lambda: bt.pages.currentWidget() is page_tab.view, 5)
    btn = bt.bookmarks_bar.all_btn if bt.bookmarks_bar.isVisible() else bt.bookmarks_btn
    on_bar = btn.mapToGlobal(QPoint(btn.width() // 2, btn.height() // 2))
    on_page = page_tab.view.mapToGlobal(QPoint(page_tab.view.width() // 2, page_tab.view.height() // 2))
    check(not bt._should_refocus_page(on_bar), "a click on All bookmarks would hand the page the keyboard")
    check(bt._should_refocus_page(on_page), "a click on the page no longer gives it the keyboard")
    print("focus: a click on the bars keeps it, a click on the page gets it")

server.shutdown()
print("\nBROWSER OK")
