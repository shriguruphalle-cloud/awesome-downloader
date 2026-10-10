"""The Music tab's suggestions: the first-run picks seed the taste and fade
as listening takes over; listens, skips, stars and "Not for me" move it;
the feed (Daily Mixes, Discover, Because you played X, New from your
artists, Picked for you) is built from it, ranked, kept varied, every song
with its reason; the home page shows it fast; Autoplay carries on when the
queue runs out; the taste setup screen picks languages, genres and artists.
No network: YouTube Music's pages are made up."""
import time

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import music_sources as ms  # noqa: E402
from app.core import taste as taste_core  # noqa: E402
from app.core import taste_config as C  # noqa: E402
from app.utils import secure_store  # noqa: E402

YEAR = time.localtime().tm_year


def song(n, artist_id, artist):
    return ms._track(id="yt:%s%05d" % (artist_id[-3:], n), title="%s song %d" % (artist, n), artist=artist,
                     artist_browse=artist_id, source="youtube", duration=200,
                     page_url="https://www.youtube.com/watch?v=x%d" % n)


ARTISTS = {"UCaaa": "Artist A", "UCbbb": "Artist B", "UCrel1": "Related One", "UCrel2": "Related Two",
           "UCccc": "Artist C"}


class FakeFetch:
    def __init__(self):
        self.asked = []

    def artist(self, bid):
        self.asked.append(("artist", bid))
        name = ARTISTS.get(bid, bid)
        rel = [{"kind": "artist", "browse_id": r, "title": ARTISTS[r], "id": "ytm:artist:" + r}
               for r in ("UCrel1", "UCrel2") if r != bid]
        return {"title": name, "artwork": None, "songs": [song(i, bid, name) for i in range(6)],
                "related": rel if bid in ("UCaaa", "UCbbb") else [],
                "singles": [{"kind": "album", "id": "ytm:album:S" + bid, "browse_id": "S" + bid,
                             "title": "%s new single" % name, "year": str(YEAR), "type": "Single"}],
                "albums": [{"kind": "album", "id": "ytm:album:O" + bid, "browse_id": "O" + bid,
                            "title": "Old album", "year": "2001", "type": "Album"}]}

    def radio(self, vid):
        self.asked.append(("radio", vid))
        return [song(100 + i, "UCrel1" if i % 2 else "UCccc", "Related One" if i % 2 else "Artist C")
                for i in range(12)]

    def search(self, q):
        self.asked.append(("search", q))
        return {"songs": [song(200 + i, "UCrel2", "Related Two") for i in range(5)]}


t = taste_core.Taste(fetch=FakeFetch())
check(t.needs_onboarding(), "a new taste didn't ask for the first-run picks")
t.set_seeds(["hi", "pa"], ["Romantic", "Party", "Sad"],
            [{"id": "UCaaa", "name": "Artist A"}, {"id": "UCbbb", "name": "Artist B"}])
check(not t.needs_onboarding(), "the picks weren't kept")
prof = t.profile()
check(t.top_artists(prof)[:2] == ["UCaaa", "UCbbb"] or set(t.top_artists(prof)[:2]) == {"UCaaa", "UCbbb"},
      "the picked artists aren't the taste's top: %r" % t.top_artists(prof))
check(prof["languages"]["hi"] > prof["languages"]["pa"], "the first-ranked language doesn't count most")
print("the first-run picks seed the taste: the artists on top, languages in their order")

# ---- the feed from the picks alone -------------------------------------------------
started = time.perf_counter()
feed = t.build_feed()
print("  feed built in %.0f ms (pages made up)" % ((time.perf_counter() - started) * 1000))
check(feed["mixes"] and all(tr.get("reason") for m in feed["mixes"] for tr in m["tracks"]),
      "no Daily Mix, or a song in it without its reason")
mix = feed["mixes"][0]["tracks"]
keys = [taste_core.artist_key(x) for x in mix]
check(all(a != b for a, b in zip(keys, keys[1:])), "the same artist twice in a row: %r" % keys)
check(max(keys.count(k) for k in set(keys)) <= C.MAX_PER_ARTIST, "one artist too often in a mix")
check(any("Fans of" in x["reason"] for x in mix), "a mix has none of the artists near yours")
check(feed["discover"] and all(taste_core.artist_key(x) in ("UCrel1", "UCrel2") for x in feed["discover"]),
      "Discover isn't the artists your artists' fans play: %r" % [x["artist"] for x in feed["discover"]])
check([a["title"] for a in feed["new"]][:1] and all(int(a["year"]) >= YEAR - 1 for a in feed["new"]),
      "New from your artists has old releases: %r" % feed["new"])
check(feed["picked"] and any(q for k, q in t.fetch.asked if k == "search" and "Hindi Romantic" in q),
      "nothing was picked from the languages and genres: %r" % t.fetch.asked)
print("from the picks alone: Daily Mixes, Discover, New from your artists, Picked for you -- each with a reason")

# ---- listening moves the taste -------------------------------------------------------
c_song = song(1, "UCccc", "Artist C")
for _ in range(4):
    t.log(c_song, "play", save=False)
    t.listen_ended(c_song, 8, 200)                       # skipped after 8 seconds
prof = t.profile()
check(prof["artists"]["UCccc"] < 0, "early skips didn't push the artist away")
a_song = song(1, "UCaaa", "Artist A")
t.log(a_song, "play")
t.listen_ended(a_song, 200, 200, natural=True)
t.log(a_song, "like")
check(t.profile()["artists"]["UCaaa"] > prof["artists"]["UCaaa"], "a played-through, starred song didn't count")
rad = [dict(x, reason="r") for x in FakeFetch().radio("x")]
ranked = t.rank(rad, t.profile(), 10, explore=0.0)
check(not ranked or taste_core.artist_key(ranked[0]) != "UCccc", "a skipped artist still came first")
t.dislike(rad[1])
t.block_artist(rad[0])
ranked = t.rank(rad, t.profile(), 12)
check(rad[1]["id"] not in [x["id"] for x in ranked], "a song marked Not for me came back")
check(all(taste_core.artist_key(x) != taste_core.artist_key(rad[0]) for x in ranked), "a blocked artist came back")
print("listens, early skips, stars, Not for me and blocked artists all move the suggestions")

# the picks fade as real listening comes in
fresh = taste_core.Taste(path=_support.STATE_DIR + "/fade.json", fetch=FakeFetch())
fresh.set_seeds(["en"], ["Pop", "Rock", "Indie"], [{"id": "UCbbb", "name": "Artist B"}])
before = fresh.profile()["artists"]["UCbbb"]
x = song(9, "UCxyz", "Someone")
for _ in range(C.SEED_FADE_PLAYS):
    fresh.log(x, "play", save=False)
after = fresh.profile()["artists"]["UCbbb"]
check(abs(after / before - 0.368) < 0.02, "a pick didn't fade to about a third after %d plays: %.2f"
      % (C.SEED_FADE_PLAYS, after / before))
print("a first-run pick fades as real listening takes over")

check(taste_core.PATH in secure_store.covered_paths(), "the taste file isn't encrypted with the rest")
check(taste_core.script_language("मेरा दिल") == "hi" and taste_core.script_language("ਪੰਜਾਬੀ") == "pa"
      and taste_core.script_language("Love song") is None, "titles' scripts read wrong")
print("the taste file is encrypted with the rest; a title's script tells its language")

# ---- in the Music tab ------------------------------------------------------------------
win, tabs = build_window(tabs=("music", "download"), size=(1280, 820))
mt = tabs["music"]
mt.audio.setMuted(True)
mt.taste = t
t.build_feed()
started = time.perf_counter()
mt.go("home")
took = (time.perf_counter() - started) * 1000
settle(300)
home = mt._pages["home"]
titles = [w.title for w in home.sections.findChildren(type(home.sections)) if hasattr(w, "title")]
from ui_qt.music_widgets import SectionHeader  # noqa: E402
labels = [h.title for h in home.sections.findChildren(SectionHeader)]
check("Your Daily Mixes" in labels and "Discover for you" in labels and "New from your artists" in labels,
      "the home page doesn't show the feed: %r" % labels)
check(home.hero.title == "Daily Mix 1", "the home header isn't the first Daily Mix: %r" % home.hero.title)
check(took < 150, "the home page took %.0f ms to show the feed" % took)
print("the home page shows the feed in %.0f ms (under 150)" % took)

# Autoplay: the queue runs out, songs like the last one follow
mt.taste.state["prefs"]["autoplay"] = True
last = song(1, "UCaaa", "Artist A")
mt.queue, mt.index = [last], 0
got = []
mt.taste.autoplay = lambda track, exclude=(), now=None, n=15, chosen=False: [
    dict(song(9, "UCzzz", "Artist Z"), reason="Like %s" % track["title"])]
mt.player.play = lambda: None              # nothing really plays in this test
mt._play_current = lambda: got.append(mt.current())
mt.next(auto=True)
for _ in range(60):
    if got:
        break
    settle(50)
check(got and got[0]["artist"] == "Artist Z" and mt.queue[-1].get("_auto"),
      "Autoplay didn't carry on with songs like the last one: %r" % [t.get("title") for t in mt.queue])
del mt._play_current, mt.player.play
print("Autoplay carries on with songs like the last one when the queue runs out")

# ---- the taste setup screen ---------------------------------------------------------------
from app.core import ytmusic  # noqa: E402
ytmusic.search = lambda q: {"artists": [{"kind": "artist", "id": "ytm:artist:UCp%d" % i, "browse_id": "UCp%d" % i,
                                         "title": "Picked %d %s" % (i, q[:3]), "artwork": None} for i in range(8)]}
mt.taste = taste_core.Taste(path=_support.STATE_DIR + "/ui.json", fetch=FakeFetch())
mt.open_taste()
ob = mt.onboarding
check(ob.isVisible() and ob.step == 0, "the taste setup didn't open")
chips = {c.key: c for c in ob.lang_flow.chips}
chips["pa"].click()
chips["hi"].click()
check(ob.lang_order == ["pa", "hi"] and chips["hi"].rank == 2, "languages aren't ranked by tap order")
ob.next_btn.click()
check(ob.step == 1 and not ob.next_btn.isEnabled(), "Next works with fewer than three genres")
for c in ob.genre_flow.chips[:3]:
    c.click()
check(ob.next_btn.isEnabled(), "three genres don't let you go on")
ob.next_btn.click()
for _ in range(60):
    if ob.grid.tiles:
        break
    settle(50)
check(len(ob.grid.tiles) >= 5, "no artists were offered: %d" % len(ob.grid.tiles))
for tile in ob.grid.tiles[:5]:
    ob._toggle(tile)
check(ob.next_btn.isEnabled() and ob.next_btn.text() == "Done", "five artists don't finish the setup")
ob.next_btn.click()
seeds = mt.taste.state["seeds"]
check(seeds["languages"] == ["pa", "hi"] and len(seeds["genres"]) == 3 and len(seeds["artists"]) == 5
      and seeds["artists"][0]["id"].startswith("UCp"), "the picks weren't kept: %r" % seeds)
check(not ob.isVisible(), "the setup didn't close when done")
print("the taste setup: languages ranked, three genres, five artists -- kept as the seeds")
win.close()
settle(300)
print("\nTASTE OK")
