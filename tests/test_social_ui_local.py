"""The people pages in the Music tab, driven against a local Awesome Music backend: signing in through
    the card (email, code, username), Friends with a friend playing something, Chats with one unread, a chat
    that gets a message live, your profile, and a friend's page. Skipped without the backend
    (`npx wrangler dev --local --port 8787` in the Android project's server/). Made-up people only."""
import os, sys, json, random, urllib.request
os.environ["AWESOME_MUSIC_API"] = "http://127.0.0.1:8787"

import _support
try:
    urllib.request.urlopen(os.environ["AWESOME_MUSIC_API"] + "/v1/health", timeout=3)
except OSError:
    print("SKIP: no local Awesome Music backend"); sys.exit(0)
from _support import build_window, settle, check
from app.core import social
win, tabs = build_window(tabs=("music", "download"), size=(1400, 860))
win.show(); settle(800)
mt = tabs["music"]
out = os.path.join(_support.STATE_DIR, "shots"); os.makedirs(out, exist_ok=True)
def shot(name):
    settle(500); mt.grab().save(os.path.join(out, name + ".png"))
mt.go("friends"); shot("1_signed_out")
# sign in through the card itself
B = social.BASE
urllib.request.urlopen(urllib.request.Request(B + "/v1/dev/reset", data=b"{}", method="POST"))
tag = "%05d" % random.randrange(10**5)
from ui_qt.music_social import SignInCard
card = mt.findChildren(SignInCard)[0]
card.field.edit.setText("pc.%s@example.test" % tag); card._go()
for _ in range(40):
    settle(100)
    if card._step == "code": break
check(card._step == "code", "the card didn't move on to the code")
shot("2_code")
code = json.loads(urllib.request.urlopen(B + "/v1/dev/code?email=pc.%s@example.test" % tag).read())["code"]
card.field.edit.setText(code); card._go()
for _ in range(40):
    settle(100)
    if card._step == "name": break
check(card._step == "name", "a new account wasn't asked for a username")
card.field.edit.setText("pc_" + tag); card.name_field.edit.setText("Desk Top"); card._go()
for _ in range(40):
    settle(100)
    if (social.me() or {}).get("handle"): break
check((social.me() or {}).get("handle") == "pc_" + tag, "username not set")
settle(600)
# a friend, made through the API (a second made-up person)
mine = social._state
social._state = None; os.environ  # switch
email2 = "phone.%s@example.test" % tag
social.start(email2)
code2 = json.loads(urllib.request.urlopen(B + "/v1/dev/code?email=" + email2).read())["code"]
saved = dict(mine)
social.verify(email2, code2); social.update_me(handle="ph_" + tag, name="Phone Friend")
friend = social.me(); ftok = social.token()
social.follow(saved["me"]["id"], True)
social.presence({"kind": "track", "id": "yt:aaaaaaaaaaa", "title": "Synthetic Tune", "artist": "Nobody Real",
                 "source": "youtube", "page_url": "https://music.youtube.com/watch?v=aaaaaaaaaaa"})
c = social.dm(saved["me"]["id"]); social.send_text(c["id"], "hi from my phone")
social.send_item(c["id"], {"kind": "track", "id": "yt:bbbbbbbbbbb", "title": "Shared Synthetic", "artist": "Test Band",
                           "source": "youtube", "page_url": "https://music.youtube.com/watch?v=bbbbbbbbbbb"})
social._state = saved; social._save()
mt.hub.refresh(); settle(1500)
mt.go("friends"); settle(1200); shot("3_friends")
mt.go("chats"); settle(1200); shot("4_chats")
check(mt.hub.unread() == 1, "the new chat isn't unread: %s" % mt.hub.chats)
mt.go("chat", {"id": c["id"], "title": "Phone Friend"}); settle(1800); shot("5_chat")
# a message from the phone arrives live while the chat is open
social._state = {"token": ftok, "me": friend}
social.send_text(c["id"], "this one arrived live")
social._state = saved
for _ in range(30):
    settle(100)
    if any(m == "this one arrived live" for m in [b.msg.get("text") for b in mt.findChildren(__import__("ui_qt.music_social", fromlist=["Bubble"]).Bubble)]): break
from ui_qt.music_social import Bubble
texts = [b.msg.get("text") for b in mt.findChildren(Bubble)]
check("this one arrived live" in texts, "the live message didn't arrive: %s" % texts)
shot("6_chat_live")
mt.go("account"); settle(800); shot("7_account")
mt.go("profile", {"handle": "ph_" + tag}); settle(1500); shot("8_profile")
print("UI OK")
