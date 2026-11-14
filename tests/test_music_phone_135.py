"""From the phone app's 1.3.x: Up next never brings back a song heard lately or already queued, matched by what the
song is; playlists imported by link stay in sync (new songs arrive, one taken out by hand stays out); a fetched copy
far longer than its song is thrown away and fetched again, once. Made-up songs; no network."""
import _support
from _support import build_window, check, settle

import os
import time

from app.core import music_brain, playlist_import
from app.utils import music_library


def song(n, title=None, artist="Nobody"):
    return {"kind": "track", "id": "yt:%011d" % n, "title": title or "Synthetic %d" % n, "artist": artist,
            "source": "youtube", "page_url": "https://music.youtube.test/%d" % n, "duration": 180}


win, tabs = build_window(tabs=("music", "download"), size=(1300, 860))
win.show()
settle(300)
mt = tabs["music"]
mt._arm_next = lambda: None
mt._prefetch = lambda: None

# ---- Up next ----
now = song(1, "Tum Hi Ho")
music_library.add_recent(song(2, "Raabta", "Arijit Singh"))
mt.queue, mt.index = [now], 0
mt._upnext_gen = 5
mt._token = getattr(mt, "_token", 0)
offered = [song(11, "Tum Hi Ho (Official Video)"), song(12, "Raabta - Lyric Video", "Arijit Singh"),
           song(13, "Brand New"), song(14, "Brand New (Audio)"), song(1, "Tum Hi Ho")]
mt._on_upnext(5, (mt._token, offered))
ids = [t["id"] for t in mt.queue[1:]]
check(ids == ["yt:00000000013"], "Up next brought back songs heard or queued: %s" % [t["title"] for t in mt.queue[1:]])
print("Up next: the same song by another upload, heard lately or queued, isn't brought back; each once")

# ---- playlists kept in sync ----
playlist_import.read = lambda link: {"tracks": [song(21), song(22), song(23, "Third One")], "songs": None, "items": []}
playlist_import.prefer_songs = lambda tracks, songs, progress, workers=8: tracks
pl = music_library.create_playlist("Linked", [song(21), song(22)], source="spotify", link="https://open.spotify.test/p/1")
check(pl.get("link") and pl.get("synced"), "a playlist imported by link should remember it")
music_library.remove_from_playlist(pl["id"], "yt:00000000022")
got = music_brain.value(music_brain.sync_playlist, pl["id"])
check(got["added"] == 1, "one new song should arrive: %s" % got)
titles = [t["title"] for t in music_library.playlist(pl["id"])["tracks"]]
check(titles == ["Synthetic 21", "Third One"], "the removed song came back, or the new one is missing: %s" % titles)
check(music_brain.value(music_brain.sync_playlist, pl["id"])["added"] == 0, "a second sync added again")
plain = music_library.create_playlist("Mine", [song(31)])
check(music_brain.sync_due(0) and "link" not in music_library.playlist(plain["id"]), "only linked ones sync")
done = []
next(p for p in music_library._load()["playlists"] if p["id"] == pl["id"])["synced"] = 0
check(music_brain.value(music_brain.sync_due, 3600, done.append) >= 1, "an old sync should be due")
for _ in range(50):
    settle(50)
    if done:
        break
check(done == [0], "the background sync didn't finish: %s" % done)
print("synced playlists: new songs arrive, a song taken out stays out, nothing twice")

# ---- a copy far longer than its song ----
fetched = []
mt._fetch_file = lambda token, track: fetched.append(track["id"])
path = os.path.join(_support.STATE_DIR, "long.m4a")
open(path, "wb").write(b"x")
mt.queue, mt.index = [song(41)], 0
mt._file = path
mt._check_length(500 * 1000)
for _ in range(20):
    settle(25)
    if fetched:
        break
check(fetched == ["yt:00000000041"] and not os.path.exists(path) and mt._file is None, "the long copy wasn't refetched")
check(mt._resume_at is not None and mt._resume_at <= 179000, "it should carry on where it was")
mt._file = path
mt._check_length(500 * 1000)
settle(100)
check(fetched == ["yt:00000000041"], "it should be refetched once per song")
mt._file, fetched[:] = path, []
mt.queue, mt.index = [song(42)], 0
mt._check_length(200 * 1000)
settle(100)
check(not fetched, "a copy a little longer is left alone")
print("a fetched copy twice as long is thrown away and fetched again, once")
win.close()
settle(200)
print("PHONE 1.3.5 OK")
