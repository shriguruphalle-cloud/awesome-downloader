"""The browser home page's mini player: it appears when any tab plays
something, shows what, and its buttons drive that tab; the toolbar has the
Download button right after the address and the music player after it."""
import json

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from ui_qt import webview2  # noqa: E402

win, tabs = build_window(tabs=("video", "browser", "download"), size=(1280, 800))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
win.show()
settle(5000)

# toolbar order
dl, np = bt.download_btn, bt.now_playing
check(dl.mapTo(bt, dl.rect().topLeft()).x() < np.mapTo(bt, np.rect().topLeft()).x() or not np.isVisible(),
      "the Download button isn't before the music player")
check(bt.address.mapTo(bt, bt.address.rect().topRight()).x() <= dl.mapTo(bt, dl.rect().topLeft()).x(),
      "the Download button isn't right after the address bar")
print("toolbar: address, Download, then the music player")


def home_player():
    out = {}
    params = json.dumps({"expression": "window.__home && window.__home.player()", "returnByValue": True})
    webview2.then(bt.home.view.core.CallDevToolsProtocolMethodAsync("Runtime.evaluate", params),
                  lambda raw: out.update(v=json.loads(raw).get("result", {}).get("value")))
    settle(500)
    return out.get("v") or {}


check(bt.home.view is not None and bt._current().on_home, "the home page isn't showing")
check(not home_player().get("on"), "the mini player shows with nothing playing")

# a background tab starts playing music
music = bt._create_tab(url="https://example.invalid/music", activate=False)
settle(800)
bt._on_message(music, {"type": "media", "present": True, "playing": True, "title": "Test Track",
                       "artist": "Test Artist", "artwork": None, "position": 10, "duration": 200,
                       "canSkip": True})
settle(600)
p = home_player()
check(p.get("on") and p.get("title") == "Test Track" and not p.get("paused"),
      "the home page's mini player didn't show the music: %r" % p)
print("the home page's mini player shows what's playing")


def overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


for width in (1280, 1150, 980):
    win.resize(width, 800)
    settle(900)
    p = home_player()
    check(p.get("on"), "the player went away at width %d" % width)
    check(not overlaps(p["rect"], p["links"]), "at %d px the player covers the links: %r" % (width, p))
    check(not overlaps(p["rect"], p["customize"]), "at %d px the player covers Customize: %r" % (width, p))
win.resize(1280, 800)
settle(600)
print("the mini player never covers the links or Customize, wide or narrow")

sent = []
bt._media_command = lambda cmd: sent.append(cmd)
gone = []
bt._goto_media_tab = lambda: gone.append(True)
bt.home._on_message({"type": "np-cmd", "cmd": "toggle"})
bt.home._on_message({"type": "np-cmd", "cmd": "next"})
bt.home._on_message({"type": "np-cmd", "cmd": "goto"})
check(sent == ["toggle", "next"] and gone, "the mini player's buttons don't reach the playing tab: %r" % sent)
print("its buttons drive the playing tab")

bt._on_message(music, {"type": "media", "present": False})
settle(600)
check(not home_player().get("on"), "the mini player stayed after the music stopped")
print("it goes away when nothing plays")

win.close()
settle(800)
print("\nHOME PLAYER OK")
