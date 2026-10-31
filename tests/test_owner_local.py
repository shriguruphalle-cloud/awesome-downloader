"""The owner's dashboard, against a local copy of the backend, with made-up accounts: the hidden door, both keys held by
the server, every tab drawn from real answers, the PIN forgotten on leaving and never written anywhere; crash notes.
Needs `npx wrangler dev --local --port 8787` in the Android project's server/; skipped without it. The local test PIN
is read from that project's own test file."""
import json
import os
import random
import re
import sys
import urllib.request

os.environ["AWESOME_MUSIC_API"] = os.environ.get("AWESOME_MUSIC_API", "http://127.0.0.1:8787")
import _support  # noqa: E402
from _support import build_window, check, settle  # noqa: E402

BASE = os.environ["AWESOME_MUSIC_API"]
check("127.0.0.1" in BASE or "localhost" in BASE, "this test only runs against a local backend")
try:
    urllib.request.urlopen(BASE + "/v1/health", timeout=3).read()
except OSError:
    print("SKIP: no local Awesome Music backend at %s" % BASE)
    sys.exit(0)

from app.core import crash_report, social  # noqa: E402
from ui_qt import music_owner  # noqa: E402
from ui_qt.music_settings import MusicSettings  # noqa: E402

src = open(os.path.join(_support.REPO, "..", "Awesome Music Android", "server", "test", "api.test.mjs"), encoding="utf-8").read()
PIN = re.search(r'const PIN = "(\d+)"', src).group(1)
urllib.request.urlopen(urllib.request.Request(BASE + "/v1/dev/reset", data=b"{}", method="POST"), timeout=5)


def sign_in(email):
    social.start(email)
    with urllib.request.urlopen(BASE + "/v1/dev/code?email=" + email, timeout=5) as r:
        social.verify(email, json.loads(r.read())["code"])


def wait_for(cond, ms=8000):
    for _ in range(ms // 50):
        settle(50)
        if cond():
            return True
    return False


def status(fn):
    try:
        fn()
        return 200
    except social.SocialError as e:
        return e.status


win, tabs = build_window(tabs=("music", "download"), size=(1300, 860))
win.show()
settle(400)
mt = tabs["music"]
mt.taste.log = lambda *a, **k: None
sheet = mt._owner_sheet


def maker_taps(settings, n):
    for _ in range(n):
        settings.maker.clicked.emit()


# ---- someone else: the door isn't there, and the server knows nothing either ----
sign_in("somebody.%06d@example.test" % random.randrange(10 ** 6))
check(not music_owner.is_owner_account(), "a stranger taken for the owner")
settings = MusicSettings(mt)
maker_taps(settings, 10)
check(not sheet.isVisible(), "ten clicks opened the door on another account")
mt._owner_try(PIN)
settle(800)
check(mt._owner_pin is None and (not mt._here or mt._here[0] != "owner"), "the right PIN opened it on another account")
for headers in ({}, {"X-Owner-Pin": PIN}):
    check(status(lambda: social.call("GET", "/v1/owner/overview", headers=headers)) == 404, "the server let a stranger in")
mt.go("owner")
settle(300)
check(mt._here[0] == "home", "going straight to the page should leave it")
print("another account: no door; the server says 404, with the right PIN too")

# a stranger's email hashed as the phone does isn't the owner's; the local owner counts on a local server only
base = social.BASE
social._state["me"] = dict(social.me(), email=music_owner.LOCAL_TEST_OWNER)
social.BASE = "https://example.test"
check(not music_owner.is_owner_account(), "the test owner must count only on a local server")
social.BASE = base
social.sign_out()

# ---- the owner's account: nine clicks do nothing, ten ask; a wrong PIN just closes ----
sign_in(music_owner.LOCAL_TEST_OWNER)
check(music_owner.is_owner_account(), "the owner wasn't recognised")
check(status(lambda: social.call("GET", "/v1/owner/overview")) == 404, "the account alone should be shut out")
settings = MusicSettings(mt)
maker_taps(settings, 9)
check(not sheet.isVisible(), "nine clicks opened it")
maker_taps(settings, 1)
check(sheet.isVisible() and sheet.edit.echoMode() == sheet.edit.EchoMode.Password, "ten clicks should ask, masked")
sheet.edit.setText("0000" if PIN != "0000" else "1111")
check(not sheet.isVisible(), "a PIN of four digits goes at once")
settle(1000)
check(mt._owner_pin is None and mt._here[0] != "owner", "a wrong PIN opened it")
urllib.request.urlopen(urllib.request.Request(BASE + "/v1/dev/reset", data=b"{}", method="POST"), timeout=5)
print("the owner: ten clicks, masked; a wrong PIN closes without a word")

maker_taps(settings, 10)
sheet.edit.setText(PIN)
check(wait_for(lambda: mt._here[0] == "owner" and mt._owner_data), "the right PIN didn't open the dashboard")
check(sheet.edit.text() == "", "the PIN stayed in the box")
page = mt._pages["owner"]
check(page.hero.title == "Dashboard" and page.hero.subline.startswith("Live"), "the header: %s" % page.hero.subline)
shot = os.environ.get("AWD_SHOT")
settle(300)
if shot:
    win.grab().save(shot + "-overview.png")
check(mt._owner_data["cards"]["totalUsers"] >= 2, "the numbers aren't the server's")

for key, _name in music_owner.TABS:
    mt._owner_set_tab(key)
    settle(500)
check(wait_for(lambda: mt._owner_users), "the accounts never came")
mt._owner_set_tab("users")
settle(300)
if shot:
    win.grab().save(shot + "-users.png")
check(0 < sum(1 for i in range(mt._owner_rows_col.count()) if mt._owner_rows_col.itemAt(i).widget()) <= 41,
      "the rows should show, a few at a time")
check(all("email" not in u for u in mt._owner_users), "the users tab shouldn't carry emails")
mt._owner_only = False
mt._owner_query = "nobody-called-this"
mt._owner_load_users()
check(wait_for(lambda: mt._owner_users == []), "the search didn't narrow it")
mt._owner_query = ""
print("every tab draws; users come back and can be searched")

# ---- sending asks first ----
mt._owner_set_tab("system")
settle(200)
mt._owner_title.edit.setText("A made-up note")
mt._owner_text.edit.setText("For testing only")
mt._owner_confirm = lambda _t: False
mt._owner_announce()
settle(500)
check(mt._owner_send_btn.text() == "Send to everyone", "a declined note was sent")
mt._owner_confirm = lambda _t: True
mt._owner_announce()
check(wait_for(lambda: mt._owner_send_btn.text() == "Sent ✓"), "the note wasn't sent: %s" % mt._owner_send_btn.text())
check((social.latest_announcement(0).get("announcement") or {}).get("title") == "A made-up note", "not on the server")
print("a notification asks first, then reaches the server")

# ---- leaving forgets the PIN; coming back needs it again ----
mt.go("home")
settle(100)
mt._owner_tick()
check(mt._owner_pin is None and mt._owner_data is None, "the PIN outlived the page")
mt.go("owner")
settle(300)
check(mt._here[0] == "home", "the page opened without the PIN")
print("leaving forgets the PIN")

# ---- the app's version goes with requests; a crash note goes once, with no words of the error's ----
social.call("GET", "/v1/me")
crash_report.PENDING = os.path.join(_support.STATE_DIR, "crash.txt")
try:
    raise ValueError("https://secret.example.test/?token=made-up")
except ValueError as e:
    crash_report._note(type(e), e.__traceback__)
text = open(crash_report.PENDING, encoding="utf-8").read()
check("ValueError" in text and "made-up" not in text and "test_owner_local.py" in text, "the note: %s" % text[:80])
check(crash_report.send_pending() and not os.path.exists(crash_report.PENDING), "the note wasn't sent")
check(not crash_report.send_pending(), "nothing left to send")
social.call("POST", "/v1/owner/unlock", headers={"X-Owner-Pin": PIN})
d = social.call("GET", "/v1/owner/overview", headers={"X-Owner-Pin": PIN})
check(any("ValueError" in c["message"] and c["version"].startswith("pc-") for c in d["crashes"]), "not on the dashboard")
print("crash note on the dashboard, without the error's words")
social.sign_out()

# the PIN was written nowhere
needle = PIN.encode()
marker = ("X-Owner-Pin: " + PIN).encode()
for root, _dirs, files in os.walk(_support.STATE_DIR):
    for name in files:
        try:
            data = open(os.path.join(root, name), "rb").read()
        except OSError:
            continue
        check(marker not in data and b'"pin"' not in data, "the PIN reached %s" % name)
win.close()
settle(300)
print("OWNER OK")
