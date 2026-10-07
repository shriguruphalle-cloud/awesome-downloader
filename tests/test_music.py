"""The Music tab: the open libraries' answers read right, songs on this PC
listed, a song kept as a file with its credit, each page painted in its
cover's colours (and white text always readable on them), and the player
itself -- one search filling every section, play, next / previous, shuffle,
repeat, the queue, artist and album pages, saving to Your music, the
full-screen view, an album that opens into its tracks, and a song that won't
stream played from the cache instead. No network: the libraries get made-up
answers, covers are pictures made on the spot, and playback is of short
tones made on the spot, muted."""
import io
import os
import subprocess
import tempfile
import urllib.error

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402

from app.core import music_sources as ms  # noqa: E402
from app.core import ytmusic  # noqa: E402
from app.utils import download_history, music_library  # noqa: E402
from ui_qt import music_look as look  # noqa: E402

# ---- the open libraries' answers ----------------------------------------------
ov = {"results": [
    {"id": "a1", "title": "Rainy Caf&eacute;", "creator": "Lo &amp; Fi", "duration": 165000, "filetype": "mp32",
     "url": "https://cdn.test/a1.mp3", "thumbnail": "https://img.test/a1", "license": "by", "license_version": "4.0",
     "license_url": "https://creativecommons.org/licenses/by/4.0/", "attribution": "\"Rainy\" by Lo &amp; Fi",
     "foreign_landing_url": "https://jamendo.test/a1", "mature": False},
    {"id": "a2", "title": "Hidden", "creator": "X", "duration": 1000, "url": "https://cdn.test/a2.mp3",
     "mature": True},
]}
calls = []
real_get_json = ms._get_json
ms._get_json = lambda url, timeout=20: (calls.append(url), ov)[1]
tracks = ms.search("lofi", "openverse")
check(len(tracks) == 1, "a mature result wasn't left out: %r" % tracks)
t = tracks[0]
check((t["title"], t["artist"], t["duration"], t["ext"], t["license"]) ==
      ("Rainy Café", "Lo & Fi", 165, "mp3", "CC BY 4.0"), "Openverse result read wrong: %r" % t)
check("page_size=20" in calls[-1], "asked Openverse for more than a signed-out search may")

# the anonymous daily limit used up: said plainly, with the other libraries offered
real_urlopen = ms.urllib.request.urlopen
ms._get_json = real_get_json
ms.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
    urllib.error.HTTPError("https://api.openverse.org/", 429, "Too Many Requests", {}, io.BytesIO(b"")))
try:
    ms.search_openverse("x")
    check(False, "a used-up daily limit wasn't reported")
except ms.MusicError as e:
    check("Internet Archive" in str(e), "the limit message doesn't mention the other libraries: %s" % e)
finally:
    ms.urllib.request.urlopen = real_urlopen
print("Openverse: read right, mature songs left out, its daily limit explained")

archive_search = {"response": {"docs": [{"identifier": "netlabel-album-1", "title": "Night Album",
                                         "creator": ["Band", "Guest"], "year": "2009",
                                         "licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/"}]}}
archive_meta = {"metadata": {"creator": "Band"}, "files": [
    {"name": "02 Second.mp3", "title": "Second", "track": "2", "length": "3:05"},
    {"name": "01 First.mp3", "title": "First", "track": "1", "length": "214.4"},
    {"name": "01 First.flac", "title": "First", "track": "1"},
    {"name": "cover.jpg"}]}
ms._get_json = lambda url, timeout=20: archive_meta if "/metadata/" in url else archive_search
albums = ms.search("night", "archive")
check(albums[0]["album_id"] == "netlabel-album-1" and albums[0]["license"] == "CC BY-NC 3.0"
      and albums[0]["artist"] == "Band, Guest" and albums[0]["kind"] == "album" and albums[0]["year"] == "2009",
      "Archive album read wrong: %r" % albums[0])
songs = ms.album_tracks(albums[0])
check([(s["title"], s["duration"]) for s in songs] == [("First", 214), ("Second", 185)],
      "an album's tracks (MP3 only, in order, with lengths): %r" % [(s["title"], s["duration"]) for s in songs])
check(songs[0]["stream"].endswith("/netlabel-album-1/01%20First.mp3") and songs[0]["album"] == "Night Album",
      "a track's address and album: %r" % songs[0])
print("Internet Archive: albums, opened into their tracks in order")

# ---- songs on this PC, and keeping one ---------------------------------------
lib = tempfile.mkdtemp(prefix="awd-music-")
for name in ("Band - Tune.mp3", "Loose.ogg", "notes.txt", "Half.mp3.part"):
    open(os.path.join(lib, name), "wb").write(b"x")
found = ms.library(lib)
check(sorted((t["artist"], t["title"]) for t in found) == [("", "Loose"), ("Band", "Tune")],
      "the music folder listed wrong: %r" % found)


class Resp(io.BytesIO):
    headers = {"Content-Length": "6"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ms.urllib.request.urlopen = lambda *a, **k: Resp(b"ID3abc")
path = ms.download(t, lib)
check(os.path.basename(path) == "Lo & Fi - Rainy Café.mp3" and open(path, "rb").read() == b"ID3abc",
      "a kept song: %s" % path)
check(os.path.exists(os.path.splitext(path)[0] + " (credit).txt"), "the licence's credit wasn't kept beside it")
check(download_history.load()[0]["title"] == "Rainy Café", "a kept song isn't in History")
ms.urllib.request.urlopen = real_urlopen
print("a kept song is saved by artist and title, with its credit, and listed in History")


# ---- the page colours ---------------------------------------------------------------
def solid(rgb, size=64, second=None):
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor(*rgb))
    if second:
        p = QPainter(img)
        p.fillRect(size // 2, 0, size - size // 2, size, QColor(*second))
        p.end()
    return img


purple = look.palette_from_image(solid((150, 40, 200)))
check(abs(purple["base"].hslHueF() - QColor(150, 40, 200).hslHueF()) < 0.04,
      "a purple cover didn't give a purple page: %s" % purple["base"].name())
for name, img in (("purple", solid((150, 40, 200))), ("yellow", solid((245, 225, 40))),
                  ("white", solid((250, 250, 250))), ("pale pink", solid((255, 200, 220)))):
    pal = look.palette_from_image(img)
    check(look.contrast(pal["base"], look.TEXT) >= 4.5 and look.contrast(pal["deep"], look.TEXT) >= 7,
          "white text doesn't read on a %s cover's page: %s / %s" % (name, pal["base"].name(), pal["deep"].name()))
grey = look.palette_from_image(solid((128, 128, 128)))
check(grey["base"].hslSaturationF() < 0.15, "a grey cover gave a colourful page: %s" % grey["base"].name())
duo = look.palette_from_image(solid((230, 120, 20), second=(20, 80, 220)))
check(look._hue_gap(duo["base"].hslHueF(), duo["glow"].hslHueF()) > 0.1,
      "a two-colour cover's second colour isn't used: %s / %s" % (duo["base"].name(), duo["glow"].name()))
check(look.palette_from_image(QImage()) is look.DEFAULT, "no picture didn't give the app's own colours")
print("each cover gives its page's colours; white text reads on every one of them")

# ---- the player ----------------------------------------------------------------
real_download = ms.download
covers = tempfile.mkdtemp(prefix="awd-covers-")


def cover(name, rgb):
    p = os.path.join(covers, name + ".png")
    solid(rgb, 120).save(p)
    return p


ff = os.path.join(_support.REPO, "vendor", "ffmpeg.exe")
tones = []
for i, freq in enumerate((330, 440, 550)):
    p = os.path.join(lib, "tone%d.mp3" % i)
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=%d:duration=1.5" % freq,
                    "-q:a", "9", p], check=True)
    tones.append(ms._track(id="local:" + p, title="Tone %d" % i, artist="Test", source="local", stream=p,
                           artwork=cover("tone%d" % i, ((200, 40, 60), (40, 160, 90), (40, 90, 200))[i])))

win, tabs = build_window(tabs=("video", "music", "download"), size=(1280, 820))
mt = tabs["music"]
win.tabs.setCurrentWidget(mt)
mt.audio.setMuted(True)
settle(800)


def playing():
    from PySide6.QtMultimedia import QMediaPlayer
    return mt.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState


def wait(cond, ms_=4000):
    for _ in range(ms_ // 50):
        if cond():
            return True
        settle(50)
    return cond()


check(mt._pages["home"].hero.title == "Play anything", "the home page didn't open first")
fake_album = ytmusic.album_item("MPREb_fake1", "Fake Album", "Test Artist", "UCfake", "2020", "Album",
                                cover("album", (230, 150, 20)))
fake_artist = ytmusic.artist_item("UCfake", "Test Artist", cover("artist", (40, 160, 90)), "1 monthly audience")
asked = []


def fake_everywhere(q, deliver):
    asked.append(q)
    deliver("music", {"top": None, "songs": list(tones), "albums": [fake_album], "artists": [fake_artist]}, "")
    deliver("free", [], "")
    deliver("live", None, "The archive is resting")
    return []


ms.search_everywhere = fake_everywhere
mt.query.setText("tones")
mt.run_search()
settle(600)
check(asked == ["tones"], "one search didn't ask every library at once: %r" % asked)
check(len(mt.results.rows) == 3, "search results didn't show: %d rows" % len(mt.results.rows))
check(len(mt._album_grid.tiles) == 1 and len(mt._artist_grid.tiles) == 1, "albums / artists didn't show")
check(mt._sections["free"][0].isHidden(), "an empty section still shows")
live_note = mt._sections["live"][2]
check(not live_note.isHidden() and "resting" in live_note.text(), "a library that failed didn't say so")
check(mt._pages["search"].hero.title == "Tone 0" and mt._pages["search"].hero.trio,
      "the top result isn't the page's header: %r" % mt._pages["search"].hero.title)
check(wait(lambda: mt.stage.pal["base"].rgb() != look.DEFAULT["base"].rgb(), 4000),
      "the page didn't take its colours from the top song's cover")
check(abs(mt.stage.pal["base"].hslHueF() - QColor(200, 40, 60).hslHueF()) < 0.05,
      "the page's colour isn't the cover's: %s" % mt.stage.pal["base"].name())
print("one search fills every section; an empty one steps aside, a failed one says so; the page wears the cover")

mt.results.rows[0].play.emit(tones[0])        # a click on the first song's number
check(wait(lambda: playing() and mt.player.position() > 0), "the song didn't start playing")
check(mt.bar.title.text() == "Tone 0" and mt.index == 0 and len(mt.queue) == 3,
      "the player didn't take the list as its queue: %r" % [t["title"] for t in mt.queue])
check(mt.results.rows[0]._current, "the playing song isn't marked in the list")
mt.next()
check(wait(lambda: mt.index == 1 and playing()), "Next didn't move on")
mt.prev()                                  # early in a song: back to the one before
check(wait(lambda: mt.index == 0), "Previous didn't go back")
print("play, next and previous")

check(wait(lambda: mt.index == 1, 6000), "the next song didn't follow when one ended")
print("the next song follows when one ends")

mt._cycle_repeat()
mt._cycle_repeat()
check(mt.repeat == "one", "repeat didn't reach 'this song'")
mt.next(auto=True)
check(mt.index == 1, "repeat-one moved on to another song")
mt._cycle_repeat()
mt._toggle_shuffle()
check(mt.shuffle and mt.bar.shuffle_btn.tint is not None, "shuffle didn't show as on")
mt.next()
check(mt.index != 1, "shuffle played the same song again")
mt._toggle_shuffle()
print("repeat and shuffle")

extra = ms._track(id="x:extra", title="Extra", artist="Test", source="local", stream=tones[2]["stream"])
mt.add_to_queue(extra)
check(mt.queue[-1]["id"] == "x:extra", "add to queue didn't add")
mt.show_page("queue")
check(len(mt.queue_list.rows) == len(mt.queue), "the Queue page doesn't list the queue")
check(mt.rail.items["queue"].count == str(len(mt.queue)), "the rail doesn't count the queue")
mt.remove_from_queue(extra)
check(all(t["id"] != "x:extra" for t in mt.queue), "remove from queue didn't remove")
print("the queue: add, list, remove")

check(any(r["title"].startswith("Tone") for r in music_library.recent()), "played songs aren't remembered")
mt.go("home")
settle(300)
check(mt._pages["home"].hero.title.startswith("Tone"), "home doesn't show what's playing")
print("home shows the song playing; played songs are remembered")

mt.open_full()
check(mt.full.isVisible() and mt.full.title.text() == mt.current()["title"], "the full-screen view didn't open")
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
app.sendEvent(mt.full, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
check(not mt.full.isVisible(), "Esc didn't close the full-screen view")
print("the full-screen view opens and closes")

# ---- an artist's page and an album's page ------------------------------------------------
ytmusic.artist = lambda bid: {"kind": "artist", "id": "ytm:artist:" + bid, "browse_id": bid, "title": "Test Artist",
                              "artist": "Test Artist", "artwork": cover("banner", (40, 160, 90)), "about": "Made up.",
                              "songs": list(tones), "albums": [fake_album], "singles": [], "related": [],
                              "source": "youtube"}
album_tracks = [dict(tones[0], id="ytx:a1", title="Album track 1", album="Fake Album"),
                dict(tones[1], id="ytx:a2", title="Album track 2", album="Fake Album")]
ytmusic.album = lambda bid: dict(fake_album, tracks=list(album_tracks), length=3)
mt.go("artist", fake_artist)
check(wait(lambda: mt._pages["artist"].sec.count() > 2), "the artist's page didn't fill")
ap = mt._pages["artist"]
check(ap.hero.title == "Test Artist" and len(ap.hero.trio) == 3, "the artist's header: %r" % ap.hero.title)
mt.go("album", fake_album)
check(wait(lambda: mt._pages["album"].sec.count() > 1), "the album's page didn't fill")
check(mt._pages["album"].hero.title == "Fake Album", "the album's header")
lists = [w for w in mt._live_lists() if mt._pages["album"].isAncestorOf(w)]
check(lists and [r.track["title"] for r in lists[0].rows] == ["Album track 1", "Album track 2"],
      "the album's tracks aren't listed")
check(wait(lambda: abs(mt.stage.pal["base"].hslHueF() - QColor(230, 150, 20).hslHueF()) < 0.05, 4000),
      "the album's page isn't in its cover's colours: %s" % mt.stage.pal["base"].name())
mt.back()
check(mt.pages.currentWidget() is mt._pages["artist"], "Back didn't return to the artist")
print("an artist's page and an album's page, each in its own picture's colours; Back returns")

# an album played from a tile opens into its tracks
ms.album_tracks = lambda item: list(album_tracks)
mt.play_item(fake_album)
check(wait(lambda: [t["title"] for t in mt.queue] == ["Album track 1", "Album track 2"] and playing()),
      "an album didn't open into its tracks: %r" % [t["title"] for t in mt.queue])
print("an album opens into its tracks and plays")

# ---- saving to Your music -------------------------------------------------------------------
check(mt.toggle_saved(album_tracks[0]) and music_library.is_saved(album_tracks[0]), "saving a song didn't save it")
check(mt.bar.save_btn.kind == "star_filled", "the player's star doesn't show the song is saved")
mt.go("songs")
saved_lists = [w for w in mt._live_lists() if mt._pages["songs"].isAncestorOf(w)]
check(saved_lists and [r.track["title"] for r in saved_lists[0].rows] == ["Album track 1"],
      "Your music > Songs doesn't list it")
mt.toggle_saved(fake_album)
mt.go("albums")
check(any(t.item["title"] == "Fake Album" for t in mt._pages["albums"].findChildren(type(mt._album_grid.tiles[0]))),
      "Your music > Albums doesn't list a saved album")
check(not mt.toggle_saved(album_tracks[0]) and not music_library.is_saved(album_tracks[0]),
      "a second star didn't un-save")
print("songs and albums saved to Your music, and un-saved")

# ---- YouTube songs: played from the fetched file; a stream cut short carries on --------------
import time  # noqa: E402

from PySide6.QtCore import QUrl  # noqa: E402

long_tone = os.path.join(lib, "long.mp3")
short_tone = os.path.join(lib, "short.mp3")
for path_, secs in ((long_tone, 6), (short_tone, 1.5)):
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=%s" % secs,
                    "-q:a", "9", path_], check=True)
yt = ms._track(id="yt:vidtest00001", title="Streamy", artist="Test", source="youtube", duration=6,
               page_url="https://www.youtube.com/watch?v=vidtest00001")
nxt = ms._track(id="yt:vidtest00002", title="After", artist="Test", source="youtube", duration=6,
                page_url="https://www.youtube.com/watch?v=vidtest00002")
ms.cached = lambda track: None

# the whole song arrives at once: it plays from the file, the stream unused
fetched_ahead = []


def quick_fetch(track, progress=None):
    fetched_ahead.append(track["id"])
    return long_tone


ms.fetch_song = quick_fetch
ms.resolve_stream = lambda track: (time.sleep(1.0), QUrl.fromLocalFile(short_tone).toString())[1]
mt.play_from(yt, [yt, nxt])
check(wait(lambda: playing() and mt.player.position() > 300, 6000), "a song that arrived whole didn't play")
check(mt._local and mt.player.source().isLocalFile() and mt.player.source().toLocalFile().endswith("long.mp3"),
      "a song that arrived whole wasn't played from its file: %s" % mt.player.source().toString())
check(wait(lambda: "yt:vidtest00002" in fetched_ahead, 3000), "the next song wasn't fetched ahead: %r" % fetched_ahead)
print("a YouTube song is played from its fetched file, and the next one is fetched ahead")

# a slow connection: the stream starts meanwhile; when it stops short of the song's end the
# song carries on from the file, at the same place -- the same song, not the next one
mt.player.stop()


def slow_fetch(track, progress=None):
    time.sleep(3.4)
    return long_tone


ms.fetch_song = slow_fetch
ms.resolve_stream = lambda track: QUrl.fromLocalFile(short_tone).toString()
mt.play_from(yt, [yt, nxt])
check(wait(lambda: mt._started and not mt._local, 5000), "the stream didn't start while the song was fetched")
check(wait(lambda: mt._local and playing(), 9000), "a stream that stopped short didn't carry on from the file")
check(mt.current()["id"] == "yt:vidtest00001" and mt.index == 0,
      "a stream that stopped short skipped to another song: %r" % mt.current()["title"])
check(wait(lambda: mt.player.position() > 1800, 4000),
      "the song didn't carry on past where the stream stopped (at %.1fs)" % (mt.player.position() / 1000))
print("a stream cut short carries on from the file at the same place, instead of skipping the song")

# nothing to play at all: said, with Try again
mt.player.stop()
ms.fetch_song = lambda track, progress=None: (_ for _ in ()).throw(ms.MusicError("YouTube said no"))
ms.resolve_stream = lambda track: (_ for _ in ()).throw(RuntimeError("no stream either"))
mt.play_from(dict(yt, id="yt:vidtest00003"), [dict(yt, id="yt:vidtest00003")])
check(wait(lambda: "Couldn't play" in mt.status.text(), 5000), "a song that can't play didn't say so")
check(mt.status.button.isVisible() and mt.status.button.text() == "Try again",
      "no Try again on a song that couldn't play")
tries = []
ms.fetch_song = lambda track, progress=None: (tries.append(1), long_tone)[1]
mt.status.button.click()
check(wait(lambda: tries and playing(), 5000), "Try again didn't play the song")
print("a song that can't be played says why, with Try again that works")

# keeping a YouTube song: the fetched file saved as "Artist - Title.mp3", tagged
mt.player.stop()
ms.fetch_song = lambda track, progress=None: long_tone
keep_dir = tempfile.mkdtemp(prefix="awd-keep-")
saved = real_download(dict(yt, title="Kept Song", artist="Test Band", album="Test Album"), keep_dir)
check(os.path.basename(saved) == "Test Band - Kept Song.mp3" and open(saved, "rb").read(3) == b"ID3",
      "a kept YouTube song isn't a tagged MP3 named by artist and title: %s" % saved)
print("a kept YouTube song is saved from the fetched file as a tagged MP3")

# ---- keeping songs: one at a time ------------------------------------------------------------
kept = []
ms.download = lambda track, folder, progress=None: (progress and progress(5, 10), kept.append(track["id"]),
                                                    os.path.join(folder, "k.mp3"))[2]
one = ms._track(id="ov:keep-1", title="Keep 1", artist="Test", source="openverse", stream="https://cdn.test/1.mp3")
two = ms._track(id="ov:keep-2", title="Keep 2", artist="Test", source="openverse", stream="https://cdn.test/2.mp3")
mt.keep(one)
mt.keep(two)
check(wait(lambda: mt._kept.get("ov:keep-1") == "done" and mt._kept.get("ov:keep-2") == "done"),
      "keeping songs didn't finish: %r" % mt._kept)
check(kept == ["ov:keep-1", "ov:keep-2"], "the songs weren't kept in order: %r" % kept)
mt.keep(tones[2])
check("local:" not in "".join(kept), "a song already on this PC was downloaded again")
ms.download = lambda track, folder, progress=None: (_ for _ in ()).throw(
    RuntimeError("ERROR: [youtube] abcdefghijk: The page needs to be reloaded."))
three = ms._track(id="ov:keep-3", title="Keep 3", artist="Test", source="openverse", stream="https://cdn.test/3.mp3")
mt.keep(three)
check(wait(lambda: "Couldn't download" in mt.status.text(), 3000), "a failed download didn't say so")
check("fresh start" in mt.status.text() and "[youtube]" not in mt.status.text(),
      "a failed download's message is yt-dlp's, not in words: %s" % mt.status.text())
again = []
ms.download = lambda track, folder, progress=None: (again.append(track["id"]), os.path.join(folder, "k3.mp3"))[1]
mt.status.button.click()
check(wait(lambda: again == ["ov:keep-3"] and mt._kept.get("ov:keep-3") == "done", 3000),
      "Try again didn't download it: %r" % again)
print("songs are downloaded one after another, never one already on this PC")

# ---- suggestions as a search is typed ----------------------------------------------------------
typed_for = []
ytmusic.suggestions = lambda text: (typed_for.append(text), {
    "queries": [text + " songs", text + " live"],
    "items": [fake_artist, dict(tones[0], title="Suggested song")]})[1]
mt.rail.search.hasFocus = lambda: True
mt.rail.search.setText("tes")
mt._typed("tes")
check(wait(lambda: mt.suggest.isVisible(), 3000), "nothing was suggested while typing")
check(typed_for == ["tes"] and [r[1]["title"] for r in mt.suggest.rows][:2] == ["tes songs", "tes live"],
      "the suggestions shown: %r" % [r[1]["title"] for r in mt.suggest.rows])
asked.clear()
mt.suggest.move_selection(1)
check(mt.suggest.activate() and asked == ["tes songs"] and not mt.suggest.isVisible(),
      "picking a suggested search didn't run it: %r" % asked)
mt._typed("tes")
check(wait(lambda: mt.suggest.isVisible(), 3000), "suggestions didn't come back")
artist_row = next(i for i, r in enumerate(mt.suggest.rows) if r[1].get("kind") == "artist")
mt.suggest.sel = artist_row
mt.suggest.activate()
check(mt.pages.currentWidget() is mt._pages["artist"], "picking a suggested artist didn't open their page")
print("as a search is typed, suggestions drop down; a suggested search or artist opens with a pick")

mt.player.stop()
win.close()
settle(500)
print("\nMUSIC OK")
