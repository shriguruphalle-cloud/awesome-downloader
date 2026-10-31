"""The features from the phone app: the DJ's run of named sets (started, steered, extended, stopped by choosing a song
yourself), a mood's mix, charts, an artist's every song, Undo after un-liking, and a song dragged in the queue. The
choosing is stood in for: this is about what the tab does with it. Made-up songs; no network."""
import json

import _support
from _support import build_window, check, settle

from app.core import music_brain as brain
from app.utils import music_library
from ui_qt import music_more

music_more.EXTEND_LEFT = 1      # (a short run here: the "nearly over" step is tried on purpose, below)

# 1300 x 800: a first show at this size used to kill the app (the page's scroll bar chasing its own width)
win, tabs = build_window(tabs=("music", "download"), size=(1300, 800))
win.show()
settle(300)
mt = tabs["music"]
started = []
mt._play_current = lambda: (started.append(mt.current()), mt._open_listen(mt.current()))
mt._arm_next = lambda: None
mt._prefetch = lambda: None
mt.taste.log = lambda *a, **k: None
mt.taste.started = lambda *a, **k: None
mt.taste.autoplay = lambda *a, **k: []


def song(n, theme=""):
    return {"kind": "track", "id": "yt:%011d" % n, "title": "Synthetic %d" % n, "artist": "Nobody", "source": "youtube",
            "page_url": "https://music.youtube.com/watch?v=%011d" % n, "duration": 180}


def ok(v):
    return json.dumps({"ok": v})


def wait_for(cond, ms=4000):
    for _ in range(ms // 50):
        settle(50)
        if cond():
            return True
    return False


asked = []
nxt = [100]


def fake_dj(exclude=None, options=None):
    o = json.loads(options)
    asked.append(dict(o, exclude=json.loads(exclude)))
    sets = []
    for name in ("Your favourites", "New for you"):
        sets.append({"theme": name, "tracks": [song(nxt[0] + i) for i in range(2)]})
        nxt[0] += 2
    return ok(sets)


brain.dj_session = fake_dj

# the page
mt.rail.items["dj"].click()
settle(200)
check(mt._here[0] == "dj" and mt._pages["dj"].hero.title == "DJ", "the DJ page didn't open")

# started: the first set plays, its name is said
mt.dj_start()
check(wait_for(lambda: mt._dj_on and started), "the DJ didn't start")
check(started[0]["_dj_theme"] == "Your favourites" and mt._dj_theme == "Your favourites", "the set isn't named")
check(len(mt.queue) == 4 and mt.index == 0, "the run isn't queued: %d" % len(mt.queue))
print("DJ: starts with its first named set")

# steering: a dial and a request remake what's to come, leaving the song on alone
first = mt.current()
n = len(asked)
mt._dj_set("energy", "hype")
check(wait_for(lambda: len(asked) > n and not mt._dj_busy), "the energy dial didn't remake the run")
check(asked[-1]["energy"] == "hype" and mt.current() is first, "the dial changed the song playing")
mt.dj_ask("90s Hindi")
check(wait_for(lambda: asked[-1]["request"] == "90s Hindi" and not mt._dj_busy), "the request didn't reach the DJ")
check(mt.current() is first and asked[-1]["exclude"] == [first["id"]], "what's heard isn't kept out")
print("DJ: dials and requests change what's next, not what's on")

# a new set's name when it comes; more when the run is nearly over
n = len(asked)
mt.index = len(mt.queue) - 2
mt.queue[mt.index]["_dj_theme"] = "New for you"
mt.dj_track_started(mt.queue[mt.index])
check(mt._dj_theme == "New for you", "the new set wasn't named: %r" % mt._dj_theme)
check(wait_for(lambda: len(asked) > n and not mt._dj_busy), "no more songs asked for")
check(len(mt.queue) - mt.index > 2, "the run didn't grow: %d" % len(mt.queue))
print("DJ: the next set is named, and the run keeps going")

# choosing a song yourself ends it
s = song(1)
mt.play_from(s, [s], context="browse")
settle(50)
check(not mt._dj_on, "the DJ kept going after a song was chosen")
print("DJ: choosing a song yourself ends it")

# a mood's mix opens as a list
brain.mood_mix = lambda key: ok({"kind": "mix", "id": "mood:" + key, "title": "Chill", "tracks": [song(2), song(3)]})
brain.home_charts = lambda region, langs, base, fresh: ok({"charts": [
    {"kind": "mix", "id": "chart:t", "title": "Top Synthetic", "over": "Chart", "subtitle": "Today", "chart": True,
     "tracks": [song(4)], "artwork": None}], "key": "k"})
mt.rail.items["discover"].click()
check(wait_for(lambda: getattr(mt, "_chart_grid", None) is not None), "the charts didn't show")
mt._open_mood("chill")
check(wait_for(lambda: mt._here[0] == "mix"), "the mood didn't open")
check(mt._here[1]["title"] == "Chill" and len(mt._here[1]["tracks"]) == 2, "the mood's list: %s" % (mt._here[1],))
print("moods and charts open")

# an artist's every song, page by page
pages = iter([{"tracks": [song(5), song(6)], "next": 8, "total": 9, "first_year": "2001", "last_year": "2020"},
              {"tracks": [song(6), song(7)], "next": None, "total": 9, "first_year": "2001", "last_year": "2020"}])
brain.artist_catalog = lambda bid, start, count: ok(next(pages))
mt.open_catalog({"browse_id": "UCx", "title": "Nobody"})
check(wait_for(lambda: len(mt._catalog.get("tracks") or []) == 2), "the catalogue's first page didn't load")
from ui_qt.music_widgets import Pill  # noqa: E402
more = [b for b in mt._pages["catalog"].findChildren(Pill) if b.text() == "More songs"][0]
more.click()
check(wait_for(lambda: len(mt._catalog["tracks"]) == 3 and mt._catalog["next"] is None), "the next page didn't load")
print("every song: page by page, each song once")

# Undo after un-liking: back in its place
mine = [song(10), song(11), song(12)]
for t in reversed(mine):
    if not music_library.is_saved(t):
        music_library.toggle(t)
order = [t["id"] for t in music_library.saved("songs")]
mt.toggle_saved(mine[1])
check(not music_library.is_saved(mine[1]), "not un-liked")
check(mt.status.text().startswith("Removed") and mt.status._action is not None, "no Undo offered")
mt.status._action()
check([t["id"] for t in music_library.saved("songs")] == order, "Undo didn't put it back in its place")
print("Undo after un-liking: back in its place")

# the queue: a song moved, the one playing still playing
a, b, c, d = song(20), song(21), song(22), song(23)
mt.queue, mt.index = [a, b, c, d], 1
mt.move_in_queue(3, 0)
check([t["id"] for t in mt.queue] == [d["id"], a["id"], b["id"], c["id"]], "the move: %s" % [t["id"] for t in mt.queue])
check(mt.current()["id"] == b["id"] and mt.index == 2, "the playing song lost its place")
print("queue: a dragged song moves; the one playing keeps playing")
print("MUSIC MORE OK")
