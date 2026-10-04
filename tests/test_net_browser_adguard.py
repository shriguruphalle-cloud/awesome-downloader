"""AdGuard, inside the Browser tab, against real sites (needs internet).

From a real page, well-known ad and tracker scripts must be refused
(net::ERR_BLOCKED_BY_CLIENT surfaces in a page as a failed fetch) while an
ordinary request goes through, and the shield's count must reflect it.

Also reports whether a YouTube watch page still carries ad placements --
AdGuard's YouTube rules strip them from the player data before the player
reads it. Reported, not asserted: YouTube changes often, and a consent page
in some regions hides the player entirely."""
import json
import sys
import time

import _support  # noqa: F401
from _support import check, qapp, settings

if sys.platform != "win32":
    sys.exit(0)

app = qapp()
from ui_qt import browser_engine, webview2  # noqa: E402
from ui_qt.browser_tab import BrowserTab  # noqa: E402
from ui_qt.download_tab import DownloadTab  # noqa: E402

if not webview2.available() or not browser_engine.adguard_bundled():
    print("WebView2 or the bundled AdGuard is missing -- skipped")
    sys.exit(0)


def wait(cond, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        try:
            if cond():
                return True
        except Exception:   # noqa: BLE001
            pass
        time.sleep(0.01)
    return False


def js(view, code, timeout=40):
    box = {}
    view.evaluate(code, lambda r: box.setdefault("r", r))
    wait(lambda: "r" in box, timeout)
    return box.get("r")


st = settings()
bt = BrowserTab(settings=st, download_tab=DownloadTab(settings=st))
bt.resize(1000, 700)
bt.show()
t0 = time.time()
check(wait(lambda: bt.services.adguard_ok, 120), "AdGuard never came up")
print("AdGuard ready after %.0fs" % (time.time() - t0))

bt.navigate("https://example.com/")
tab = bt._current()
check(wait(lambda: not tab.loading and "Example" in tab.title, 30), "example.com didn't load")

TARGETS = {
    "adsbygoogle": "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
    "doubleclick": "https://securepubads.g.doubleclick.net/tag/js/gpt.js",
    "google-analytics": "https://www.google-analytics.com/analytics.js",
    "facebook pixel": "https://connect.facebook.net/en_US/fbevents.js",
    "taboola": "https://cdn.taboola.com/libtrc/loader.js",
    "criteo": "https://static.criteo.net/js/ld/publishertag.js",
}
# Watch the page's own network log, then add the targets as <script> tags --
# the way a site loads them. AdGuard either refuses a request (it fails with
# ERR_BLOCKED_BY_CLIENT) or, for scripts a page would break without, swaps in
# a harmless stand-in (an internal redirect).
core = tab.view.core
requests, outcome = {}, {}


def on(kind):
    def handler(sender, args):
        d = json.loads(args.ParameterObjectAsJson)
        if kind == "will":
            requests[d["requestId"]] = d["request"]["url"]
            if d.get("redirectResponse"):
                outcome.setdefault(d["redirectResponse"].get("url"), "neutralized")
        elif kind == "failed":
            outcome.setdefault(requests.get(d["requestId"], "?"), "blocked")
        else:
            outcome.setdefault(requests.get(d["requestId"], d["response"]["url"]), "loaded")
    return handler


for event, kind in (("Network.requestWillBeSent", "will"), ("Network.loadingFailed", "failed"),
                    ("Network.responseReceived", "loaded")):
    core.GetDevToolsProtocolEventReceiver(event).DevToolsProtocolEventReceived += on(kind)
webview2.then(core.CallDevToolsProtocolMethodAsync("Network.enable", "{}"))
wait(lambda: False, 0.5)
CONTROL = "https://example.com/?control=%d" % int(time.time())
tags = "".join("var s=document.createElement('script');s.src=%s;document.head.appendChild(s);" % json.dumps(u)
               for u in list(TARGETS.values()) + [CONTROL])
tab.view.run_js("(function(){%s return 1})()" % tags)
wait(lambda: all(u in outcome for u in list(TARGETS.values()) + [CONTROL]), 25)
result = {name: outcome.get(url, "no request seen") for name, url in TARGETS.items()}
result["control"] = outcome.get(CONTROL, "no request seen")
for name, verdict in result.items():
    print("  %-18s %s" % (name, verdict))
check(result["control"] == "loaded", "an ordinary request was stopped too")
stopped = sum(1 for k, v in result.items() if k != "control" and v in ("blocked", "neutralized"))
check(stopped == len(TARGETS), "only %d of %d ad/tracker scripts were stopped" % (stopped, len(TARGETS)))

status = {}
bt.services.page_status(tab.url, lambda s: status.setdefault("s", s))
check(wait(lambda: "s" in status, 15), "no answer from AdGuard about the page")
print("AdGuard says:", status["s"])
check(status["s"] and status["s"]["blocked"] >= 1, "the shield's count didn't move")

# ---- YouTube's player data (reported only) ----------------------------------------
bt.navigate("https://www.youtube.com/watch?v=jNQXAC9IVRw")
if wait(lambda: not tab.loading and "youtube.com/watch" in tab.url, 40):
    wait(lambda: False, 3)
    ads = js(tab.view, "(() => { const r = window.ytInitialPlayerResponse; if (!r) return 'no player data';"
                       " return JSON.stringify({adPlacements: (r.adPlacements || []).length,"
                       " playerAds: (r.playerAds || []).length}); })()")
    print("YouTube player data after AdGuard:", ads)
else:
    print("YouTube didn't load in time -- not checked")

print("\nADGUARD OK")
