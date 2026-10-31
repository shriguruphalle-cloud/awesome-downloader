"""The rest of the phone's social features from the PC, against a local copy of the backend: the library kept the same
on "phone" and PC, replies, edit and unsend, seen-by, suggestions, follower lists, removing a follower, a private
profile, listening time, and being told of new messages and followers. Skipped without the backend. Made-up people."""
import json
import os
import random
import sys
import urllib.request

os.environ.setdefault("AWESOME_MUSIC_API", "http://127.0.0.1:8787")
import _support  # noqa: E402
from _support import build_window, check, settle  # noqa: E402

BASE = os.environ["AWESOME_MUSIC_API"]
try:
    urllib.request.urlopen(BASE + "/v1/health", timeout=3).read()
except OSError:
    print("SKIP: no local Awesome Music backend at %s" % BASE)
    sys.exit(0)

from app.core import social  # noqa: E402
from app.utils import music_library  # noqa: E402

check(social.BASE == BASE, "not pointed at the local backend")
urllib.request.urlopen(urllib.request.Request(BASE + "/v1/dev/reset", data=b"{}", method="POST"), timeout=5)
tag = "%05d" % random.randrange(10 ** 5)


def account(who):
    email = "%s.m%s@example.test" % (who, tag)
    social._state = None
    social.start(email)
    code = json.loads(urllib.request.urlopen(BASE + "/v1/dev/code?email=" + email, timeout=5).read())["code"]
    social.verify(email, code)
    social.update_me(handle="%s_m%s" % (who, tag), name=who.title())
    return dict(social._state)


def be(acct):
    social._state = dict(acct)


phone, friend = account("phone"), account("friend")
song = {"kind": "track", "id": "yt:aaaaaaaaaaa", "title": "Synthetic From Phone", "artist": "Nobody", "source": "youtube",
        "page_url": "https://music.youtube.com/watch?v=aaaaaaaaaaa", "duration": 200}

# the phone has a like and a playlist on the server already
be(phone)
social.call("POST", "/v1/sync", {"since": 0, "changes": [
    {"kind": "like", "key": song["id"], "data": song, "updated": 1000},
    {"kind": "playlist", "key": "pl_phone", "updated": 1001,
     "data": {"id": "pl_phone", "title": "Made On Phone", "tracks": [song], "public": True}}]})

win, tabs = build_window(tabs=("music", "download"))
mt = tabs["music"]
hub = mt.hub
told = []
hub.notify.connect(lambda t, x, target: told.append((t, x, target)))


def wait_for(cond, ms=8000):
    for _ in range(ms // 50):
        settle(50)
        if cond():
            return True
    return False


# 1) signing in on the PC: the phone's library arrives, and this PC's like goes up
pc_song = dict(song, id="yt:bbbbbbbbbbb", title="Synthetic From PC", page_url="https://music.youtube.com/watch?v=bbbbbbbbbbb")
music_library.toggle(pc_song)
be(phone)
hub._started()
check(wait_for(lambda: music_library.is_saved(song)), "the phone's like didn't arrive")
check(any(p["id"] == "pl_phone" for p in music_library.playlists()), "the phone's playlist didn't arrive")
check(music_library.is_saved(pc_song), "this PC's own like was lost")
check(wait_for(lambda: hub.synced_at > 0 and not hub._syncing), "the sync didn't finish: %s" % hub.sync_error)
hub.sync()
check(wait_for(lambda: not hub._syncing), "the second sync didn't finish")
docs = social.call("POST", "/v1/sync", {"since": 0, "changes": []})["docs"]
check(any(d["key"] == pc_song["id"] and not d["deleted"] for d in docs), "this PC's like didn't reach the server")
print("library: phone's likes and playlists arrive, this PC's like goes up, nothing lost")

# un-liking here reaches the phone
music_library.toggle(song)
hub.sync()
check(wait_for(lambda: not hub._syncing), "sync after un-like didn't finish")
docs = social.call("POST", "/v1/sync", {"since": 0, "changes": []})["docs"]
check(any(d["key"] == song["id"] and d["deleted"] for d in docs), "an un-like didn't reach the server")
print("library: un-liking here reaches the phone")

# 2) follows, suggestions, lists, removing a follower
be(friend)
social.follow(phone["me"]["id"], True)
be(phone)
check(any(p["id"] == friend["me"]["id"] for p in social.suggest()), "a follower isn't suggested back")
check(mt._why("followed_by:ann:2") == "Followed by @ann and 2 more", "why wording")
followers = social.follow_list(phone["me"]["handle"], "followers")
check([p["id"] for p in followers] == [friend["me"]["id"]], "followers: %s" % followers)
social.remove_follower(friend["me"]["id"])
check(not social.follow_list(phone["me"]["handle"], "followers"), "remove follower didn't work")
social.follow(friend["me"]["id"], True)
be(friend)
social.follow(phone["me"]["id"], True)
print("people: suggestions, follower lists, removing a follower")

# 3) private profile
be(phone)
social.update_me(private=True)
social.follow(friend["me"]["id"], False)
stranger = account("stranger")
be(stranger)
check(social.profile(phone["me"]["handle"])["user"]["locked"], "a private profile opened to a stranger")
be(phone)
social.follow(friend["me"]["id"], True)
social.update_me(private=False)
print("privacy: a private profile stays closed to strangers")

# 4) chat: reply, edit, unsend, seen
be(friend)
chat = social.dm(phone["me"]["id"])
first = social.send_text(chat["id"], "first from friend")
be(phone)
reply = social.send_text(chat["id"], "my answer", reply_to=first["id"])
check(reply["reply"]["text"] == "first from friend", "the reply carries no quote: %s" % reply)
social.edit_text(chat["id"], reply["id"], "my better answer")
msgs = social.messages(chat["id"])
check(msgs[-1]["text"] == "my better answer" and msgs[-1].get("edited"), "the edit: %s" % msgs[-1])
gone = social.send_text(chat["id"], "regretted")
social.unsend(chat["id"], gone["id"])
check(all(m["id"] != gone["id"] for m in social.messages(chat["id"])), "unsend left the message")
print("chat: replies quote, edits show, unsend removes")

# on the page: bubbles, edits and unsends live, Sent / Seen
hub.refresh()
check(wait_for(lambda: any(c["id"] == chat["id"] for c in hub.chats)), "the chat isn't listed")
mt.go("chat", {"id": chat["id"], "title": "Friend"})
check(wait_for(lambda: reply["id"] in mt._bubbles), "the chat page didn't load")
check(mt._seen_label.text() == "Sent" and mt._seen_label.isVisibleTo(mt), "not Sent: %r" % mt._seen_label.text())
be(friend)
social.edit_text(chat["id"], first["id"], "first, edited live")
social.mark_read(chat["id"])
be(phone)
check(wait_for(lambda: mt._bubbles[first["id"]].msg["text"] == "first, edited live"), "a live edit didn't arrive")
hub.refresh()
check(wait_for(lambda: mt._seen_label.text() == "Seen"), "not Seen: %r" % mt._seen_label.text())
be(friend)
live = social.send_text(chat["id"], "will vanish")
be(phone)
check(wait_for(lambda: live["id"] in mt._bubbles), "a live message didn't arrive")
be(friend)
social.unsend(chat["id"], live["id"])
be(phone)
check(wait_for(lambda: live["id"] not in mt._bubbles), "a live unsend didn't arrive")
mt._compose.answer(first, "Friend")
mt._chat_send(chat["id"], "answered from the box")
check(wait_for(lambda: any((b.msg.get("reply") or {}).get("id") == first["id"] and b.msg["text"] == "answered from the box"
                           for b in mt._bubbles.values())), "the compose box didn't reply")
print("chat page: live edit and unsend, Sent then Seen, replying from the box")

# 5) listening time
hub.is_playing = lambda: True
hub._unsent = 170
hub._listened()
check(wait_for(lambda: social.profile(phone["me"]["handle"])["user"].get("listen_total", 0) >= 60),
      "listening time didn't reach the server")
print("listening time reaches your profile")

# 6) being told: the first look only takes note; then a new message and a new follower, once each
mt.go("home")
settle(200)
told.clear()
be(friend)
social.send_text(chat["id"], "are you there")
newbie = account("newbie")
social.follow(phone["me"]["id"], True)
be(phone)
hub.refresh()
check(wait_for(lambda: any("are you there" in x for _t, x, _g in told)), "a new message wasn't told: %s" % told)
check(wait_for(lambda: any(t == "New follower" for t, _x, _g in told)), "a new follower wasn't told: %s" % told)
n = len(told)
hub.refresh()
settle(1500)
check(len(told) == n, "told twice: %s" % told[n:])
print("told once of a new message and a new follower")

hub._signed_out()
print("SOCIAL MORE OK")
