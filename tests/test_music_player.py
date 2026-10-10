"""The Music tab's player, round three: the equalizer (FFmpeg's filters) heard
at once and kept; back / forward 10 seconds; the heart (Liked songs) and
playlists -- made here, added to, imported from other apps; the side panel
and the full-screen player's cards; a song's title and artist opening its
album and artist; the rail's Made for you from the feed; a forgiving search
of your own songs; and the glass windows sized to the tab. No network:
made-up answers, tones made on the spot, muted."""
import json
import os
import subprocess
import tempfile
import time

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import eq as eq_core  # noqa: E402
from app.core import music_sources as ms  # noqa: E402
from app.core import playlist_import, ytmusic  # noqa: E402
from app.utils import music_library  # noqa: E402

# ---- the equalizer as filters ----------------------------------------------------------
check(eq_core.chain(eq_core.DEFAULT) == "", "a flat, off equalizer still changes the sound")
rock = eq_core.apply_preset(None, "Rock")
ch = eq_core.chain(rock)
check("equalizer=f=32:t=o:w=1:g=4.0" in ch and ch.startswith("volume=-2.0dB") and "alimiter" in ch,
      "a preset's chain: %s" % ch)
check(eq_core.settings({"gains": [99, -99]})["gains"][:2] == [12, -12], "gains aren't kept within ±12 dB")
fx = eq_core.chain(dict(eq_core.DEFAULT, bass=True, surround=True, normalize=True, mono=True))
check(all(k in fx for k in ("bass=", "stereowiden", "pan=stereo")) and "dynaudnorm" not in fx,
      "effects missing: %s" % fx)
print("the equalizer and effects become FFmpeg filters, with headroom; flat is untouched")

# ---- the player ------------------------------------------------------------------------------
ff = os.path.join(_support.REPO, "vendor", "ffmpeg.exe")
lib = tempfile.mkdtemp(prefix="awd-player-")
long_tone = os.path.join(lib, "long.mp3")
subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-q:a", "9",
                long_tone], check=True)
song = ms._track(id="local:" + long_tone, title="Long Tone", artist="Test Artist", source="local",
                 stream=long_tone, duration=30, album="Test Album")

win, tabs = build_window(tabs=("music", "download"), size=(1400, 860))
mt = tabs["music"]
win.tabs.setCurrentWidget(mt)
mt.audio.setMuted(True)
settle(500)


def wait(cond, s=5):
    t = time.time()
    while time.time() - t < s and not cond():
        settle(50)
    return cond()


def playing():
    return mt.player.playbackState() == mt.player.PlaybackState.PlayingState


mt.play_from(song, [song])
check(wait(lambda: playing() and mt.player.position() > 300), "the song didn't play through the new engine")
check(mt.player.info.get("codec") == "mp3" and mt.player.duration() > 29000, "the song's details: %r"
      % mt.player.info)
check(wait(lambda: mt.bar.quality.label == "MP3", 3), "the quality badge: %r" % mt.bar.quality.label)
before = mt.player.position()
mt.skip_by(10)
check(wait(lambda: mt.player.position() > before + 9000, 3), "forward 10 s didn't skip ahead")
at = mt.player.position()
mt.skip_by(-10)
check(wait(lambda: mt.player.position() < at - 8000, 3), "back 10 s didn't go back")
print("the song plays through FFmpeg, with its quality shown; back and forward 10 seconds")

# the equalizer: heard at once, kept, the same in both panels
mt.open_side("eq")
check(mt.side.isVisible() and mt.side.current == "eq", "the side panel didn't open on the equalizer")
mt.eq_panel._preset("Bass boost")
check(wait(lambda: mt.player.eq["preset"] == "Bass boost" and mt.player.eq["enabled"], 2),
      "the equalizer change wasn't heard")
check(playing(), "changing the equalizer stopped the song")
check(mt.settings.get("music_eq", {}).get("preset") == "Bass boost", "the equalizer setting wasn't kept")
check(mt.full.fx.s["preset"] == "Bass boost", "the full-screen equalizer doesn't match")
mt.side.show_tab("info")
check(mt.info_panel.values["Codec"].text() == "MP3", "song info doesn't show the codec")
mt.side.show_tab("queue")
check(mt.queue_panel.now.rows and mt.queue_panel.now.rows[0].track["id"] == song["id"], "the queue panel")
mt.side.close_panel()
print("the equalizer is heard at once without stopping the song, kept, and shown in both places")

# the equalizer changed while playing: joined in, the song carries on where it was (no restart)
at = mt.player.position()
mt.eq_panel._preset("Rock")
settle(900)
check(playing() and mt.player.position() > at + 400, "an equalizer change restarted or stopped the song")
mt.eq_panel._set_spatial("headphones")
settle(900)
check(playing() and mt.player.eq["spatial"] == "headphones", "spatial audio stopped the song")
mt.eq_panel._set_spatial("off")
print("equalizer and spatial-audio changes join in without stopping or restarting the song")

# ---- where you left off: kept, and brought back ------------------------------------------------------
mt._save_last()
from app.utils import ui_state  # noqa: E402
last = ui_state.load_music()
check(last.get("queue") and last["queue"][last["index"]]["id"] == song["id"] and last["pos"] > 0,
      "the song playing wasn't kept: %r" % {k: v for k, v in last.items() if k != "queue"})
print("the song playing and where it was are kept for next time")

# ---- the heart, and playlists ---------------------------------------------------------------------
mt.toggle_saved(song)
check(mt.bar.save_btn.kind == "heart_filled" and mt.full.heart.kind == "heart_filled", "the heart didn't fill")
check(any(t["id"] == song["id"] for t in music_library.saved("songs")), "a liked song isn't in Liked songs")
pl = music_library.create_playlist("Road trip")
mt._sync_rail()
check(pl["id"] in mt.rail.items, "a new playlist isn't in the rail")
mt._add_to_playlist(pl, song)
check(music_library.playlist(pl["id"])["tracks"][0]["id"] == song["id"], "a song wasn't added to the playlist")
mt.rail.items[pl["id"]].click()
check(mt.pages.currentWidget() is mt._pages["playlist"] and mt._pages["playlist"].hero.title == "Road trip",
      "the playlist's page didn't open")
mt._remove_from_playlist(pl["id"], song)
check(not music_library.playlist(pl["id"])["tracks"], "a song wasn't taken off the playlist")
print("the heart fills and adds to Liked songs; playlists are made, filled, opened and emptied")

# ---- importing from other apps ------------------------------------------------------------------------
items = playlist_import.parse_text("1. Test Band - First Song\nSecond Song by Other Band\nJust A Title\n")
check(items == [{"title": "First Song", "artist": "Test Band"}, {"title": "Second Song", "artist": "Other Band"},
                {"title": "Just A Title", "artist": ""}], "a pasted list read wrong: %r" % items)
csv_items = playlist_import.parse_text("Track Name,Artist Name(s),Album\nCsv Song,Csv Band;Guest,X\n")
check(csv_items == [{"title": "Csv Song", "artist": "Csv Band"}], "a CSV read wrong: %r" % csv_items)
next_data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Made Up List", "trackList": [
    {"title": "Song One", "subtitle": "Band One"}, {"title": "Song Two", "subtitle": "Band Two"}]}}}}}}
playlist_import._get = lambda url, timeout=20: (
    '<script id="__NEXT_DATA__" type="application/json">%s</script>' % json.dumps(next_data))
sp = playlist_import.read("https://open.spotify.com/playlist/abc123")
check(sp["title"] == "Made Up List" and [i["title"] for i in sp["items"]] == ["Song One", "Song Two"],
      "a Spotify playlist read wrong: %r" % sp)
ytmusic.search = lambda q: {"songs": [ms._track(id="yt:m%s" % q[:3].replace(" ", ""), title=q.split(" Band")[0],
                                                artist="Band", source="youtube")], "albums": [], "artists": []}
matched = playlist_import.match(sp["items"])
check([m["title"] for m in matched] == ["Song One", "Song Two"], "matching: %r" % matched)
got = []
mt.importer.imported.connect(got.append)
mt.importer.open("https://open.spotify.com/playlist/abc123")
check(mt.importer.isVisible() and mt.importer.geometry() == mt.rect(), "the import window isn't over the tab")
mt.importer.start()
check(wait(lambda: got, 5), "importing didn't finish")
check(got[0]["title"] == "Made Up List" and len(got[0]["tracks"]) == 2 and got[0]["source"] == "Spotify",
      "the imported playlist: %r" % got[0])
check(mt.pages.currentWidget() is mt._pages["playlist"], "the imported playlist didn't open")
print("playlists come in from Spotify, lists and CSVs, matched to songs, and open")

# ---- a song's title and artist open things -----------------------------------------------------------
ytmusic.album = lambda bid: dict(ytmusic.album_item(bid, "The Album", "Test Artist", "UCx", "2020"),
                                 tracks=[song], length=30)
yt_song = ms._track(id="yt:abc", title="Linked", artist="Test Artist", source="youtube", album="The Album",
                    album_browse="MPREb_x", page_url="https://www.youtube.com/watch?v=abc")
mt.open_album_of(yt_song)
check(wait(lambda: mt._pages["album"].hero.title == "The Album", 3), "the song's album didn't open")
ytmusic.search = lambda q: {"songs": [], "albums": [], "artists": [ytmusic.artist_item("UCfound", "Test Artist")]}
ytmusic.artist = lambda bid: {"kind": "artist", "id": "ytm:artist:" + bid, "browse_id": bid, "title": "Test Artist",
                              "artist": "Test Artist", "artwork": None, "about": "", "songs": [song], "albums": [],
                              "singles": [], "related": [], "source": "youtube"}
mt.open_artist_of(dict(song))
check(wait(lambda: mt._pages["artist"].hero.title == "Test Artist", 3), "the artist's page wasn't found by name")
print("a song's title opens its album, its artist their page -- found by name when not known")

# ---- the rail's Made for you, from the feed ---------------------------------------------------------------
mt.taste.state["feed"] = {"built": int(time.time()), "mixes": [
    {"kind": "mix", "id": "mix:1", "title": "Daily Mix 1", "subtitle": "Test Artist", "artwork": None,
     "tracks": [song]}], "discover": [song], "because": [], "new": [], "picked": []}
mt._sync_rail()
check("mix:1" in mt.rail.items and "discover" in mt.rail.items, "Made for you isn't in the rail")
mt.rail.items["mix:1"].click()
check(mt.pages.currentWidget() is mt._pages["mix"] and mt._pages["mix"].hero.title == "Daily Mix 1",
      "a Daily Mix didn't open from the rail")
print("the rail's Made for you comes from the feed, and opens each list")

# ---- forgiving search of your own songs ---------------------------------------------------------------------
music_library.toggle(ms._track(id="yt:tum", title="Tum Hi Ho", artist="Arijit Singh", source="youtube"))
check([t["title"] for t in mt._mine("tum hi hoo arijt")][:1] == ["Tum Hi Ho"], "a misspelt search missed it")
check(not mt._mine("completely different"), "an unrelated search matched")
print("a search finds your own songs through a spelling mistake or two")

# ---- the full-screen player's cards, and the glass windows -------------------------------------------------
mt.open_full()
settle(200)
fv = mt.full
fv.fx_btn.click()
check(fv.is_open(fv.fx_card) and fv.fx_card in fv._pops and fv.eq_tile.lit, "Effects didn't start popping in")
settle(80)
mid, alpha = fv._pop_rect(fv.fx_card, fv._pops[fv.fx_card])
check(mid.width() < fv.fx_card.width() and 0 < alpha, "a card popping in isn't growing into place")
settle(fv.POP_IN_MS + 200)
check(fv.fx_card.isVisible() and not fv._pops and not fv.info_card.isVisible(), "Effects didn't open")
fv.info_btn.click()
settle(fv.POP_IN_MS + 200)
check(fv.info_card.isVisible() and not fv.fx_card.isVisible(), "one card at a time on the right")
fv.queue_pill.click()
fv.queue_pill.click()                     # changed its mind half way: it turns back
check(not fv.is_open(fv.queue_card), "a card turned back mid-pop still counts as open")
fv.queue_pill.click()
settle(fv.POP_IN_MS + 200)
check(fv.queue_card.isVisible() and not fv._pops, "the queue card didn't open")
fv.queue_card.close_btn.click()
settle(fv.POP_OUT_MS + 150)
check(not fv.queue_card.isVisible() and not fv._pops, "the queue card didn't pop out")
print("the full-screen cards pop in and out (and turn back when changed mid-way)")
mt.close_full()
mt.open_taste()
win.resize(1200, 780)
settle(300)
check(mt.onboarding.geometry() == mt.rect(), "the taste window doesn't fill the tab after a resize")
mt.onboarding.hide()
print("the full-screen cards open one side at a time; the glass windows follow the tab's size")

# ---- the mini player ------------------------------------------------------------------------------------------
check(mt.current() is not None, "no song to show in the mini player")
mt.MINI_PLAYER = True
mt._played_once = True
mt._out_of_sight = lambda: True                 # (as if minimised)
mt._sync_mini()
settle(400)
m = mt.mini
check(m is not None and m.isVisible() and m.windowOpacity() > 0.95, "the mini player didn't come up")
check(m.ui == 0 and not m.pill.isVisible(), "the mini player's controls show without the mouse over it")
m.show_controls(True)
settle(350)
check(m.ui == 1.0 and m.pill.isVisible() and m.play_btn.isVisible(), "the controls didn't come up under the mouse")
m.show_controls(False)
settle(600)
check(m.ui == 0 and not m.play_btn.isVisible(), "the controls didn't fade away")
mt.player.stop()                                # the gap between one song and the next
mt._sync_mini()
settle(400)
check(m.isVisible(), "the mini player went away between songs")
m.hide_mini()
settle(60)
mt._sync_mini()                                 # wanted again while it's fading out: it stays
settle(500)
check(m.isVisible() and m.windowOpacity() > 0.95, "the mini player was lost fading out and in again")
m.resize_to(9999)
check(m.width() == m.MAX_SIDE == m.height(), "the mini player grew past its largest, or out of square")
m.resize_to(240)
check(m.width() == 240 == m.height() and m.play_btn.width() < 50, "a smaller mini player didn't scale its buttons")
del mt._out_of_sight
win.hide()
check(not mt._out_of_sight(), "a closed window counted as out of sight -- the music would carry on")
win.show()
settle(200)
mt._sync_mini()
settle(500)
check(not m.isVisible(), "the mini player stayed with the Music tab in front")
mt.MINI_PLAYER = False
print("the mini player comes up out of sight, stays through song changes, shows its controls under the mouse,"
      " scales, and isn't kept for a closed window")
mt.player.stop()
win.close()
settle(300)
print("\nMUSIC PLAYER OK")
