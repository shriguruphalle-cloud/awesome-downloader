"""A jam between two players, against a local Awesome Music backend: the host
starts it with their queue, a guest joins by code and is brought to the same
song at the same place; the guest's pause pauses the host, a skip moves both on,
and leaving ends it for the one who left. Each "player" here is a JamClient with
a stand-in player, so it's the protocol and the clock that are tested, not
audio. Skipped without the backend. Made-up people only."""
import json
import os
import random
import sys
import time
import urllib.request

os.environ.setdefault("AWESOME_MUSIC_API", "http://127.0.0.1:8787")
import _support  # noqa: E402
from _support import check, qapp, settle  # noqa: E402

try:
    urllib.request.urlopen(os.environ["AWESOME_MUSIC_API"] + "/v1/health", timeout=3)
except OSError:
    print("SKIP: no local Awesome Music backend")
    sys.exit(0)

from app.core import social  # noqa: E402
from ui_qt.music_social import Hub, JamClient  # noqa: E402

app = qapp()
B = social.BASE
urllib.request.urlopen(urllib.request.Request(B + "/v1/dev/reset", data=b"{}", method="POST"), timeout=5)
tag = "%05d" % random.randrange(10 ** 5)


def account(who):
    email = "%s.jam%s@example.test" % (who, tag)
    social._state = None
    social.start(email)
    code = json.loads(urllib.request.urlopen(B + "/v1/dev/code?email=" + email, timeout=5).read())["code"]
    social.verify(email, code)
    social.update_me(handle="%s_j%s" % (who, tag), name=who.title())
    return dict(social._state)


class Player:
    """What a Music tab does with jam_follow: the song, playing or paused, the place."""

    def __init__(self, queue=None):
        self.queue, self.index, self.playing, self.pos, self.at = list(queue or []), 0, False, 0, time.time()

    def position(self):
        return self.pos + (int((time.time() - self.at) * 1000) if self.playing else 0)

    def follow(self, queue, index, playing, target):
        self.queue, self.index = list(queue), index
        if target >= 0 and abs(self.position() - target) > 700:
            self.pos, self.at = int(target), time.time()
        if playing != self.playing:
            self.pos, self.at, self.playing = self.position(), time.time(), playing


def wait_for(cond, ms=6000):
    for _ in range(ms // 50):
        settle(50)
        if cond():
            return True
    return False


songs = [{"kind": "track", "id": "yt:%011d" % i, "title": "Synthetic %d" % i, "artist": "Nobody",
          "source": "youtube", "page_url": "https://music.youtube.com/watch?v=%011d" % i} for i in range(3)]
host_acct, guest_acct = account("host"), account("guest")

hub = Hub()
host_p = Player(songs)
host_p.playing, host_p.pos, host_p.at = True, 30_000, time.time()       # the host is 30 s into song 1
social._state = host_acct
host = JamClient(hub)
host.follow_fn = host_p.follow
host.here_fn = lambda: (host_p.queue, host_p.index, host_p.position(), host_p.playing)
host.start()
check(wait_for(lambda: host.state is not None and len(host.state.get("queue") or []) == 3),
      "the host's jam didn't take their queue: %s" % host.state)
code = host.code
print("host started jam", code, "with their queue")

social._state = guest_acct
guest_p = Player()
guest = JamClient(hub)
guest.follow_fn = guest_p.follow
guest.join(code)
check(wait_for(lambda: guest.state is not None and len(guest.state.get("members") or []) == 2),
      "the guest didn't get into the jam: %s" % guest.state)
check(wait_for(lambda: guest_p.queue and guest_p.queue[0]["id"] == songs[0]["id"] and guest_p.playing),
      "the guest isn't playing the jam's song")
settle(600)
gap = abs(guest_p.position() - host_p.position())
check(gap < 800, "host and guest are %d ms apart" % gap)
check(abs(guest_p.position() - 30_000) < 3000, "the guest isn't where the host was: %d" % guest_p.position())
print("guest joined by code: same song, %d ms apart" % gap)

guest.op("pause")
check(wait_for(lambda: not host_p.playing and not guest_p.playing), "the guest's pause didn't pause the host")
guest.op("play")
check(wait_for(lambda: host_p.playing and guest_p.playing), "play didn't resume both")
host.op("next")
check(wait_for(lambda: host_p.index == 1 and guest_p.index == 1), "a skip didn't move both on")
print("pause, play and next reach everyone")

guest.leave()
check(wait_for(lambda: len((host.state or {}).get("members") or []) == 1), "the guest is still in the host's jam")
check(guest.state is None and not guest.active, "the guest still thinks they're in the jam")
host.leave()
print("JAM OK")
