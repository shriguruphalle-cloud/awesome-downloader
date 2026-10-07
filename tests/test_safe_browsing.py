"""Known dangerous sites are stopped before they load: the open lists are
read right (hosts files and plain lists), a listed domain catches its
subdomains, the Browser tab shows the warning page in its place with "Go
back" and "Continue anyway", and it can be turned off. Made-up sites on a
made-up list -- no list is downloaded and no site is visited."""
import json

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import safe_browsing as sb  # noqa: E402
from app.utils import browser_data  # noqa: E402
from ui_qt import webview2  # noqa: E402

# ---- reading the lists, and matching ----------------------------------------
parsed = sb.parse("# URLhaus\n127.0.0.1 evil-malware.test\n0.0.0.0 localhost\n"
                  "phish-login.test\n\n  scam-shop.test  # trailing comment\n")
check(parsed == {"evil-malware.test", "phish-login.test", "scam-shop.test"}, "lists read wrong: %r" % parsed)
sb.add_for_test(["phish-login.test"])
check(sb.check("https://phish-login.test/signin") is not None, "a listed site wasn't caught")
check(sb.check("https://accounts.phish-login.test/x") is not None, "a listed site's subdomain wasn't caught")
check(sb.check("https://login.test/") is None, "a parent of a listed site was caught")
check(sb.check("https://example.com/") is None, "an unlisted site was caught")
check(sb.check("file:///C:/phish-login.test") is None, "a local file was checked")
print("lists are read, and a listed site and its subdomains are caught")

# ---- in the Browser tab -------------------------------------------------------
browser_data.set_pref("safe_browsing", True)
win, tabs = build_window(tabs=("video", "browser", "download"), size=(1200, 780))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
win.show()
settle(4000)
tab = bt._create_tab(url="https://example.invalid/start", activate=True)
settle(2500)
view = tab.view
blocked = []
view.navigationBlocked.connect(lambda u, hit: blocked.append(u))
view.load("https://accounts.phish-login.test/signin")
settle(2500)
check(blocked == ["https://accounts.phish-login.test/signin"], "the listed page wasn't stopped: %r" % blocked)


def page_text():
    out = {}
    params = json.dumps({"expression": "document.body ? document.body.innerText : ''", "returnByValue": True})
    webview2.then(view.core.CallDevToolsProtocolMethodAsync("Runtime.evaluate", params),
                  lambda raw: out.update(v=json.loads(raw).get("result", {}).get("value")))
    settle(600)
    return out.get("v") or ""


text = page_text()
check("This site may harm you" in text and "accounts.phish-login.test" in text,
      "the warning page didn't show: %r" % text[:200])
check(tab.url == "https://accounts.phish-login.test/signin", "the address bar doesn't show the stopped address")
print("a listed site is stopped and the warning page shows in its place")

# "Continue anyway": let through for this session
sb_allowed_before = sb.check("https://accounts.phish-login.test/signin")
bt._on_message(tab, {"type": "sb-continue", "url": "https://accounts.phish-login.test/signin"})
settle(1500)
check(sb_allowed_before is not None and sb.check("https://accounts.phish-login.test/signin") is None,
      "continue anyway didn't let the site through")
check(len(blocked) == 1, "the site was stopped again after continue anyway")
print("'continue anyway' lets it through, for this session")

# turned off: nothing is stopped
sb.add_for_test(["another-phish.test"])
bt._set_safe_browsing(False)
view.load("https://another-phish.test/")
settle(1500)
check(len(blocked) == 1, "a site was stopped with the protection turned off")
bt._set_safe_browsing(True)
print("it can be turned off from the browser menu")

win.close()
settle(800)
print("\nSAFE BROWSING OK")
