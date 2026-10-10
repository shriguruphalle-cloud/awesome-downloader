"""What plays next (app/core/upnext.py), offline: radios made up here. A
Hindi ballad is followed by Hindi ballads, not a party song or another
language; a song never comes back, nor another version of it; a skip
moves the session; the spatial-audio graph, the quality badge and the
lyrics' line matching."""
import _support  # noqa: F401
from _support import check

from app.core import eq as eq_core
from app.core import lyrics as lyrics_db
from app.core import upnext as U


def t(i, title, artist, aid=None):
    return {"id": "yt:%s" % i, "title": title, "artist": artist, "artist_browse": aid or "UC" + artist[:4],
            "source": "youtube", "kind": "track"}


seed = t("seed", "Tum Hi Ho", "Arijit Singh", "UCarijit")
radio = [seed,
         t("a", "Channa Mereya", "Arijit Singh", "UCarijit"),
         t("b", "Kala Chashma Party Remix", "Badshah", "UCbad"),
         t("c", "Tum Hi Ho (Lofi Flip)", "DJ X", "UCdjx"),
         t("d", "Sun Saathiya", "Priya Saraiya", "UCpriya"),
         t("e", "Blinding Lights", "The Weeknd", "UCweek"),
         t("f", "Tujh Mein Rab Dikhta Hai", "Roop Kumar Rathod", "UCroop"),
         t("g", "Janam Janam", "Arijit Singh", "UCarijit")]
chill = [seed, radio[1], radio[4], radio[6], radio[7]]
hindi = [seed, radio[1], radio[4], radio[6], radio[7], radio[2]]
CHIPS = {"Romance": {"playlistId": "RDATmd"}, "Chill": {"playlistId": "RDATmX"}, "Hindi": {"playlistId": "RDATgq"},
         "Upbeat": {"playlistId": "RDATmb"}}


def fetch(vid, tune=None):
    if tune is None:
        return {"tracks": radio, "chips": CHIPS if vid == "seed" else {}}
    return {"tracks": {"RDATmX": chill, "RDATgq": hindi}.get(tune["playlistId"], []), "chips": {}}


mem = U.Memory({})
mem.remember(radio[5], {"lang": "en"})          # the Weeknd is known to be English
session = U.Session()
session.started(seed, U.features(seed, mem), chosen=True)
out = U.choose(seed, session, mem, fetch, n=5, chosen=True)
titles = [x["title"] for x in out]
check(titles and "Channa Mereya" in titles[:3], "a Hindi ballad's next songs: %r" % titles)
check("Blinding Lights" not in titles, "an English song followed a Hindi ballad: %r" % titles)
check(not any("Tum Hi Ho" in x for x in titles), "the same song came back as another version: %r" % titles)
check(titles.index("Kala Chashma Party Remix") > 2 if "Kala Chashma Party Remix" in titles else True,
      "a party remix came straight after a ballad: %r" % titles)
check(all(x.get("reason") for x in out), "a pick without its reason")
print("after a Hindi ballad: Hindi ballads first; no other language, no other version of it")

# a skip moves the session; a song started can't come again
session.heard(radio[2], U.features(radio[2], mem), 8, 200)
check(session.artist.get(U.artist_key(radio[2]), 0) < 0, "a skip in the first seconds didn't count against")
again = U.choose(radio[1], session, mem, fetch, n=8)
check(seed["id"] not in [x["id"] for x in again], "a song already played came back")
check(U.song_key({"title": 'Tum Hi Ho (From "Aashiqui 2") [Lo-fi]'}) == U.song_key({"title": "tum hi ho - lofi"}) or
      U.song_key({"title": "Tum Hi Ho (Lofi Flip)"}) == U.song_key({"title": "Tum Hi Ho"}),
      "versions of one song aren't seen as the same")
print("a skip counts against what was different; played songs and their versions don't come back")

# spatial audio: an open filter graph, from the song to the ears
g = eq_core.spatial_graph(dict(eq_core.DEFAULT, spatial="headphones"))
check("surround=chl_out=7.1" in g and "headphone=map=" in g and "{i}" in g, "the spatial graph: %s" % g[:120])
check(eq_core.spatial_graph(eq_core.DEFAULT) == "", "spatial audio on while it's off")
check("extrastereo" in eq_core.chain(dict(eq_core.DEFAULT, spatial="speakers")), "the speakers mode")
print("spatial audio for headphones and speakers")

# the quality badge
from ui_qt.music_panels import quality_badge  # noqa: E402
check(quality_badge({"codec": "flac", "sample_rate": 96000, "sample_format": "s32 (24 bit)"})[:2]
      == ("Hi-Res Lossless", "24-bit/96 kHz"), "hi-res")
check(quality_badge({"codec": "flac", "sample_rate": 44100, "sample_format": "s16"})[0] == "Lossless", "lossless")
check(quality_badge({"codec": "eac3", "profile": "eac3 (Dolby Digital Plus + Dolby Atmos)", "channels": "5.1"})[0]
      == "Dolby Atmos", "atmos")
check(quality_badge({"codec": "opus", "sample_rate": 48000})[0] == "Opus", "opus")
print("the quality badge: Hi-Res Lossless, Lossless, Dolby Atmos, or the codec")

# lyrics: versions told apart by script, lines matched by time
check(lyrics_db.script_code("तुम ही हो") == "Deva" and lyrics_db.script_code("tum hi ho") == "Latn", "scripts")
lines = [(0, "a"), (1000, "b"), (2000, "c")]
check(lyrics_db.align(lines, [(10, "A"), (2020, "C")]) == ["A", "A", "C"] or
      lyrics_db.align(lines, [(10, "A"), (2020, "C")])[2] == "C", "lines matched by time")
print("lyrics versions told apart by script, lines matched by time")
print("\nUP NEXT OK")
