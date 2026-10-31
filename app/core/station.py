"""The phone's autoplay station: what plays after the songs you chose, held to them.

The desktop engine (app.core.upnext) orders one song's radio by the listening session. Chained, it
drifts: each autoplayed song becomes the next seed, so five songs on, the music follows a song nobody
chose. Here the song you chose is the station's anchor until you choose another, and every refill is
weighed against it:

  * Candidates: the engine's order for the song playing now, the anchor's own radio, and the anchor's
    radio tuned to its decade ("2010s") -- a song more than one of them suggests comes first.
  * Held to: the anchor's language (another language never plays; one that can't be told only fills in,
    from an artist already in the station), and nothing heard this session again -- no other version or
    upload of it either.
  * Weighed by: genre overlap, mood (energy) closeness, and era -- years apart cost, and a new song keeps
    to new songs (what's trending now), an old one to its own years.
  * Flow: no artist twice in a row, no more than two in ten; a remix or lo-fi only if that's what's on.

Pure functions over plain dicts (tracks as the engine makes them), so they test without the network.
"""
import time
from collections import deque

from app.core import upnext

# ---- weights ------------------------------------------------------------------------------------
W_ENGINE = 1.0            # its place in the engine's order for the song playing now
W_ANCHOR = 1.3            # its place in the anchor's own radio
W_ERA_RADIO = 0.7         # its place in the anchor's radio tuned to its decade
W_AGREE = 0.6             # each further list that has it
W_GENRE = 0.45            # per genre shared with the anchor (and the station's played-through songs)
W_GENRE_CLASH = -1.2
W_ENERGY = 1.0            # per unit of mood difference (-1 calm .. +1 energetic)
W_YEARS = 0.09            # per year apart, past the first three ...
YEARS_CAP = 1.4           # ... at most this much
W_FRESH = 0.45            # a song from the last two years when the anchor is one too
W_ANCHOR_ARTIST = 0.25
W_VARIANT = -1.6
W_LANG_UNKNOWN = -0.6


class Station:
    """The anchor (the song chosen), and what's been heard since."""

    def __init__(self):
        self.anchor = None
        self.anchor_f = None
        self.liked = deque(maxlen=12)      # songs played through since the anchor
        self.since = 0.0

    def update(self, seed, chosen, memory, heard=()):
        """`seed` started: chosen by you, it's the new anchor; otherwise the anchor stays."""
        if chosen or self.anchor is None or not str(self.anchor.get("id", "")).startswith("yt:"):
            self.anchor = dict(seed)
            self.anchor_f = upnext.features(seed, memory)
            self.liked.clear()
            self.since = time.time()
        elif self.anchor_f.get("lang") is None:
            self.anchor_f = upnext.features(self.anchor, memory)
        for h in heard or ():
            if h.get("_complete") and h.get("id") not in {x.get("id") for x in self.liked}:
                self.liked.append(h)
        return self.anchor


def singer(track):
    """Who sings it, by name: one singer has several YouTube ids (their channel, their "Topic"), so
    turns and caps go by the first name credited."""
    name = (track.get("artist") or "").split(",")[0].split("&")[0]
    return " ".join(name.lower().split()) or upnext.artist_key(track)


def year_of(track):
    y = str(track.get("year") or "")[:4]
    return int(y) if y.isdigit() and 1900 < int(y) < 2100 else None


def decade_chip(chips, year):
    if not year:
        return None
    want = "%d0s" % (year // 10)
    return next((c for c in chips if c.lower() == want), None)


def _rank_map(tracks):
    n = max(1, len(tracks))
    return {t["id"]: 1.0 - i / float(n) for i, t in enumerate(tracks)}


def rank(anchor, anchor_f, seed, engine, anchor_radio, era_radio, heard, n, memory,
         now_year=None, liked=()):
    """Up to `n` songs to follow `seed`, held to `anchor`. `engine`: the engine's ordered picks for the
    seed; `anchor_radio` / `era_radio`: the anchor's radio, plain and tuned to its decade; `heard`: the
    session's songs so far."""
    now_year = now_year or time.localtime().tm_year
    if not anchor_f.get("lang"):
        anchor_f = upnext.features(anchor, memory)          # the engine has just read the anchor's radio
    lang = anchor_f.get("lang") or upnext.features(seed, memory)["lang"]
    # an English (or unknown-language) station takes Latin-script songs whose language can't be told:
    # that's how English titles look. Elsewhere -- Hindi, Punjabi, Tamil written in English letters
    # look just the same as each other -- an unknown song needs a known singer, or two lists agreeing.
    latin_ok = lang in (None, "en")
    a_year = year_of(anchor)
    a_energy = anchor_f.get("energy")
    a_genres = set(anchor_f.get("genres") or ())
    for t in liked:
        a_genres |= set(upnext.features(t, memory)["genres"] or ())
    a_variant = anchor_f.get("variant") or upnext.variant_of(seed)
    keys = {upnext.song_key(x) for x in list(heard) + [seed, anchor]}
    ids = {x.get("id") for x in list(heard) + [seed, anchor]}
    around = list(heard) + [seed, anchor]
    station_artists = {upnext.artist_key(x) for x in around} | {singer(x) for x in around}
    lists = {"engine": _rank_map(engine), "anchor": _rank_map(anchor_radio), "era": _rank_map(era_radio)}
    weights = {"engine": W_ENGINE, "anchor": W_ANCHOR, "era": W_ERA_RADIO}
    pool = {}
    for src in (engine, anchor_radio, era_radio):
        for t in src:
            pool.setdefault(t["id"], t)
    scored, seen = [], set()
    for tid, t in pool.items():
        k = upnext.song_key(t)
        if not k or tid in ids or k in keys or k in seen:
            continue
        f = upnext.features(t, memory)
        known = bool(f["lang"])
        if lang and known and f["lang"] != lang:
            continue                                         # another language: never on this station
        hits = [s for s in lists if tid in lists[s]]
        unsure = lang and not known and not latin_ok
        if unsure and len(hits) < 2 and upnext.artist_key(t) not in station_artists \
                and singer(t) not in station_artists:
            continue                                         # can't tell, and nothing vouches for it: not worth a guess
        seen.add(k)
        s = sum(weights[h] * lists[h][tid] for h in hits) + W_AGREE * (len(hits) - 1)
        if unsure:
            s += W_LANG_UNKNOWN
        g = set(f["genres"] or ())
        if g and a_genres:
            s += W_GENRE * len(g & a_genres)
            if upnext._clash(g, a_genres):
                s += W_GENRE_CLASH
        if a_energy is not None and f["energy"] is not None:
            s -= W_ENERGY * abs(a_energy - f["energy"])
        y = year_of(t)
        if a_year and y:
            s -= min(YEARS_CAP, W_YEARS * max(0, abs(a_year - y) - 3))
            if a_year >= now_year - 1 and y >= now_year - 2:
                s += W_FRESH
        if f["variant"] and f["variant"] != a_variant:
            s += W_VARIANT
        if singer(t) == singer(anchor):
            s += W_ANCHOR_ARTIST
        scored.append((s, t))
    scored.sort(key=lambda x: -x[0])
    # flow: no singer twice in a row, at most two in any ten -- counting the last songs heard
    recent = [singer(x) for x in (list(heard) + [seed])[-9:]]
    out, pending = [], [t for _s, t in scored]
    while pending and len(out) < n:
        window = (recent + [singer(t) for t in out])[-9:]
        last = window[-1] if window else None
        pick = next((t for t in pending if singer(t) != last and window.count(singer(t)) < 2), None)
        if pick is None:
            pick = next((t for t in pending if singer(t) != last), None)
        if pick is None:
            break                                            # only the one singer left: better a shorter list
        pending.remove(pick)
        out.append(pick)
    return out
