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
mt.go("discover")
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

# Mix like a DJ: the setting reaches the engine and is kept; a jam never blends; the DJ moves the best follower up
from app.core import mixer
from ui_qt.music_settings import MusicSettings

check(mt.player.smart_mix, "Mix like a DJ should start on, as on the phone")
sheet = MusicSettings(mt)
from PySide6.QtWidgets import QLabel
check(any(w.text() == "Mix like a DJ" for w in sheet.findChildren(QLabel)), "the setting isn't beside Blend songs")
mt.set_smart_mix(False)
check(not mt.player.smart_mix and mt.settings["music_smart_mix"] is False, "the switch didn't reach the engine")
mt.set_smart_mix(True)
x, y = song(30), song(31)
check(mt._blend_ok(x, y), "two songs should blend")
type(mt.jam).active = property(lambda self: True)
check(not mt._blend_ok(x, y), "a jam moves on together, song by song")
type(mt.jam).active = property(lambda self: False)

mt.dj_stop()
mt._dj_on = True
now = dict(song(40), stream="now.flac", source="local", _dj=True)
slow = dict(song(41), stream="slow.flac", source="local", _dj=True)
same = dict(song(42), stream="same.flac", source="local", _dj=True)
mine_next = dict(song(43), stream="mine.flac", source="local")
fake = {"now.flac": (124.0, 0.2), "slow.flac": (80.0, 0.2), "same.flac": (123.0, 0.2)}
real_analyse = mixer.analyse
mixer.analyse = lambda key, src, dur=0: mixer.Profile(200000, fake[src][0], 0.8, 0, 0, 0, 200000, fake[src][1])
mt.queue, mt.index = [now, slow, same, mine_next], 0
mt.player.mix_window.emit(now["id"])
check(wait_for(lambda: mt.queue[1]["id"] == same["id"]), "the better match didn't move up: %s" % [t["id"] for t in mt.queue])
check(mt.queue[3]["id"] == mine_next["id"], "your own pick should stay put")
print("DJ: the song that follows on best moves up; your own picks stay")
mt.queue, mt.index = [now, mine_next, slow, same], 0
mt._dj_chose_for = None
mt.player.mix_window.emit(now["id"])
settle(300)
check(mt.queue[1]["id"] == mine_next["id"], "a song of your own next is never moved")
mixer.analyse = real_analyse

with mixer._lock:
    mixer._profiles[now["id"]] = mixer.Profile(200000, 124.0, 0.8, 0, 0, 0, 200000, 0.2)
mt.queue, mt.index = [now, same], 0
mt.player._plan = mixer.Plan(now["id"], same["id"], 1, 2, 3, 0, 1.0)
mt._dj_on = True
mt.rail.items["dj"].click()
settle(200)
mt._dj_show()
sub = mt._pages["dj"].hero.subline
check("124 BPM" in sub and "on the beat" in sub, "the DJ page should say how the mix will go: %s" % sub)
print("DJ page says: %s" % sub)
sheet.open()
settle(400)
import os
shot = os.environ.get("AWD_SHOT")
if shot:
    win.grab().save(shot)
check(sheet.col.parentWidget().width() <= sheet.col.parentWidget().parentWidget().width(),
      "the settings shouldn't be wider than their window (a switch gets cut off)")
sheet.hide()
mt.player._plan = None
mt.dj_stop()

print("MUSIC MORE OK")
