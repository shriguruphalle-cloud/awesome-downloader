"""The Awesome Music account from the PC, against a local copy of its backend:
signing in with an emailed code, a username, following, what friends are
playing, a chat with a song shared into it, and a jam.

Needs the backend running on this PC (in the Android project's server/:
`npx wrangler dev --local --port 8787`, which sends no email -- the code goes
to its own dev endpoint). Without it, this test is skipped. Made-up people only.
"""
import os
import random
import sys
import urllib.request

os.environ.setdefault("AWESOME_MUSIC_API", "http://127.0.0.1:8787")
import _support  # noqa: F401,E402 -- isolates the app's files
from _support import check  # noqa: E402

BASE = os.environ["AWESOME_MUSIC_API"]
try:
    urllib.request.urlopen(BASE + "/v1/health", timeout=3).read()
except OSError:
    print("SKIP: no local Awesome Music backend at %s" % BASE)
    sys.exit(0)

import json  # noqa: E402

from app.core import social  # noqa: E402
from app.utils import secure_store  # noqa: E402

check(social.BASE == BASE, "the app isn't pointed at the local backend: %s" % social.BASE)
urllib.request.urlopen(urllib.request.Request(BASE + "/v1/dev/reset", data=b"{}", method="POST"), timeout=5)


def dev_code(email):
    with urllib.request.urlopen(BASE + "/v1/dev/code?email=" + email, timeout=5) as r:
        return json.loads(r.read())["code"]


tag = "%06d" % random.randrange(10 ** 6)


def sign_in(who):
    email = "%s.%s@example.test" % (who, tag)
    social.start(email)
    try:
        social.verify(email, "0000" if dev_code(email) != "0000" else "1111")
        check(False, "a wrong code was taken")
    except social.SocialError as e:
        check(e.status == 400 and "tries left" in str(e), "a wrong code didn't say how many tries are left: %s" % e)
    new = social.verify(email, dev_code(email))
    check(new, "a first sign-in wasn't a new account")
    check(social.signed_in() and social.me()["email"] == email, "not signed in after the code")
    social.update_me(handle="%s_%s" % (who, tag), name=who.title())
    return social.token(), social.me()


def as_user(tok, user):
    social._state = {"token": tok, "me": user}


# signing in, and the sign-in kept encrypted
tok_a, ann = sign_in("ann")
check(os.path.exists(social.ACCOUNT_PATH), "the sign-in wasn't kept")
if secure_store.ENCRYPT:
    check(secure_store.is_encrypted(social.ACCOUNT_PATH), "the sign-in was kept unencrypted")
    check(tok_a.encode() not in open(social.ACCOUNT_PATH, "rb").read(), "the token is readable in the file")
check(social.ACCOUNT_PATH in secure_store.covered_paths(), "the sign-in file isn't among the encrypted ones")
social._state = None                                     # as after a restart
check(social.token() == tok_a and social.me()["handle"] == "ann_" + tag, "the sign-in didn't survive a restart")
try:
    social.update_me(handle="admin")
    check(False, "a reserved username was taken")
except social.SocialError as e:
    check(e.status == 409, "a reserved username: %s" % e)
print("signed in with an emailed code; username set; kept encrypted; survives a restart")

tok_b, bob = sign_in("bob")
ann = dict(ann, handle="ann_" + tag)

# people: search, follow both ways, what a friend is playing
found = social.search_people("ann_" + tag[:3])
check(any(u["id"] == ann["id"] for u in found), "search didn't find Ann: %s" % found)
social.follow(ann["id"], True)
song = {"kind": "track", "id": "yt:abcdefghijk", "title": "Synthetic Song", "artist": "Test Artist", "duration": 201,
        "source": "youtube", "page_url": "https://music.youtube.com/watch?v=abcdefghijk", "local_path": "C:/x.mp3"}
as_user(tok_a, ann)
social.follow(bob["id"], True)
social.presence(song)
as_user(tok_b, bob)
people = social.friends()
a = next((p for p in people if p["id"] == ann["id"]), None)
check(a and a["following"] and a["follows_you"], "Ann isn't a friend both ways: %s" % people)
check(a.get("now") and a["now"]["track"]["title"] == "Synthetic Song", "Ann's song isn't shown: %s" % a)
prof = social.profile("ann_" + tag)
check(prof["user"]["followers"] == 1 and prof["user"]["following"] == 1, "profile counts: %s" % prof["user"])
print("found by search; friends both ways; a friend's song shown; profile")

# a chat: a message and a song shared into it
c = social.dm(ann["id"])
check(c["kind"] == "dm" and c["title"] == "Ann", "the chat with Ann: %s" % c)
social.send_text(c["id"], "hello from the PC")
m = social.send_item(c["id"], song, "listen")
check(m["item"]["title"] == "Synthetic Song" and "local_path" not in m["item"], "the shared song: %s" % m)
as_user(tok_a, ann)
chats = social.chats()
check(chats and chats[0]["unread"] and chats[0]["last_text"].startswith("Shared a song"), "Ann's chats: %s" % chats)
msgs = social.messages(c["id"])
check([x["kind"] for x in msgs] == ["text", "item"] and msgs[0]["text"] == "hello from the PC", "messages: %s" % msgs)
social.mark_read(c["id"])
check(not social.chats()[0]["unread"], "still unread after reading")
print("a chat: text and a song card, unread until read")

# a jam started in the chat
j = social.new_jam(c["id"])
check(len(j["code"]) == 6 and j["host"] == ann["id"], "the jam: %s" % j)
social.send_jam(c["id"], j["code"])
as_user(tok_b, bob)
check(social.jam(j["code"])["code"] == j["code"], "Bob can't see the jam")
check(social.messages(c["id"])[-1]["kind"] == "jam", "the jam invite isn't in the chat")
print("a jam started from the chat; the invite is in it")

# signing out: the session ends on the server too
social.sign_out()
check(not social.signed_in() and not os.path.exists(social.ACCOUNT_PATH), "still signed in after signing out")
as_user(tok_b, bob)
try:
    social.friends()
    check(False, "a signed-out session still worked")
except social.SocialError as e:
    check(e.status == 401 and not social.signed_in(), "a dead session wasn't signed out here: %s" % e)
print("SOCIAL OK")
