"""The Music tab's suggestions: what you like, learned from what you play.

  * First run, you pick languages, genres and some artists (the "seeds").
    They start strong and fade as real listening takes over
    (taste_config.SEED_*).
  * Every listen is logged -- started, played through, skipped (and when),
    repeated, starred, "Not for me", searched-then-played, downloaded -- with
    the hour. Each kind is an implicit score (taste_config.EVENT_SCORES),
    time-decayed: the long-term taste. What was played in the last couple of
    hours leans the next picks (the session); what you play at this time of
    day leans them a little too.
  * Candidates come from YouTube Music: your top artists' pages, the artists
    their fans play, the radio of songs you finished or starred, new
    releases, and about one in eight from outside what you know.
  * They're ranked by that taste, then re-ordered so no artist comes twice in
    a row or more than three times in a list, and every one keeps the
    reason it was picked ("Because you like ...").
  * What plays next, after the song you chose (autoplay), follows the
    listening session instead -- its language, mood and artists, moved by
    every song played through or skipped (upnext.py).

The feed (Daily Mixes, Discover, Because you played X, New from your
artists) is built in the background and kept, so the home page shows it at
once. Everything stays on this PC, in one small file -- encrypted with the
rest of your history when that's on (secure_store).
"""
import json
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config
from ..logging_setup import get_logger
from ..utils import secure_store
from . import taste_config as C
from . import upnext

logger = get_logger("taste")

PATH = os.path.join(config.APPDATA_DIR, "music_taste.json")
META_DIR = os.path.join(config.APPDATA_DIR, "music-meta")      # public pages, kept a few days

LANGUAGES = (("hi", "Hindi"), ("en", "English"), ("pa", "Punjabi"), ("ta", "Tamil"), ("te", "Telugu"),
             ("bn", "Bengali"), ("mr", "Marathi"), ("kn", "Kannada"), ("ml", "Malayalam"), ("gu", "Gujarati"),
             ("ko", "Korean"), ("es", "Spanish"), ("ja", "Japanese"), ("ar", "Arabic"), ("fr", "French"))
GENRES = ("Pop", "Hip-hop", "Rock", "Indie", "EDM", "Lo-fi", "Romantic", "Sad", "Party", "Bollywood", "Sufi",
          "Ghazal", "Devotional", "Classical", "Jazz", "K-pop", "Workout", "Chill", "Focus", "Folk")
LANGUAGE_NAMES = dict(LANGUAGES)
# Unicode blocks that name a language by their script
_SCRIPTS = (("hi", 0x0900, 0x097F), ("bn", 0x0980, 0x09FF), ("pa", 0x0A00, 0x0A7F), ("gu", 0x0A80, 0x0AFF),
            ("ta", 0x0B80, 0x0BFF), ("te", 0x0C00, 0x0C7F), ("kn", 0x0C80, 0x0CFF), ("ml", 0x0D00, 0x0D7F),
            ("ko", 0xAC00, 0xD7AF), ("ja", 0x3040, 0x30FF), ("ar", 0x0600, 0x06FF))


def script_language(text):
    """A language from the script a title is written in; None for Latin."""
    counts = {}
    for ch in text or "":
        o = ord(ch)
        for code, lo, hi in _SCRIPTS:
            if lo <= o <= hi:
                counts[code] = counts.get(code, 0) + 1
    return max(counts, key=counts.get) if counts else None


def artist_key(track):
    return track.get("artist_browse") or ("name:" + (track.get("artist") or "").split(",")[0].strip().lower())


def _slim(t):
    keep = ("id", "title", "artist", "artist_browse", "album", "album_browse", "year", "duration", "artwork",
            "source", "page_url", "ext", "stream", "license")
    out = {k: t[k] for k in keep if t.get(k)}
    if t.get("source") == "youtube":
        out.pop("stream", None)
    return out


# ------------------------------------------------------------ public pages ---
class Fetch:
    """YouTube Music's pages, kept on disk for a few days (they're public and
    change slowly), so building the feed again costs little."""

    def __init__(self, folder=META_DIR):
        self.folder = folder

    def _cached(self, kind, key, make):
        path = os.path.join(self.folder, "%s-%s.json" % (kind, "".join(c for c in key if c.isalnum())[:80]))
        try:
            if time.time() - os.path.getmtime(path) < C.CACHE_DAYS * 86400:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError):
            pass
        try:
            value = make()
        except Exception as e:   # noqa: BLE001 -- one page missing leaves the rest
            logger.info("Couldn't fetch %s %s: %s", kind, key, e)
            return None
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(value, f)
        except OSError:
            pass
        return value

    def artist(self, browse_id):
        from . import ytmusic
        return self._cached("artist", browse_id, lambda: ytmusic.artist(browse_id))

    def radio(self, video_id):
        got = self.radio_full(video_id)
        return got.get("tracks") if got else None

    def radio_full(self, video_id, tune=None):
        """A song's radio and its chips (ytmusic.radio_full); `tune`, a chip's tuned radio."""
        from . import ytmusic
        key = video_id + ("-" + "".join(c for c in (tune or {}).get("playlistId", "") if c.isalnum())[:40]
                          if tune else "")
        return self._cached("radio2", key, lambda: ytmusic.radio_full(video_id, tune))

    def search(self, query):
        from . import ytmusic
        return self._cached("search", query, lambda: ytmusic.search(query))


# ------------------------------------------------------------------ taste ----
class Taste:
    def __init__(self, path=PATH, fetch=None):
        self.path = path
        self.fetch = fetch or Fetch()
        self._lock = threading.RLock()
        self.state = self._load()
        self.session = upnext.Session()          # what's being listened to right now
        self.memory = upnext.Memory(self.state)  # what songs and artists are like

    # ---- the file ----
    def _load(self):
        try:
            s = secure_store.read_json(self.path)
        except FileNotFoundError:
            s = {}
        except (ValueError, OSError):
            logger.warning("Couldn't read the music taste file; starting afresh", exc_info=True)
            s = {}
        base = {"onboarded": False, "seeds": {"languages": [], "genres": [], "artists": []}, "events": [],
                "tracks": {}, "totals": {}, "blocked": {"artists": {}, "tracks": []}, "feed": {},
                "prefs": {"autoplay": True, "discover": 0.3}}
        for k, v in base.items():
            s.setdefault(k, v)
        return s

    def save(self):
        with self._lock:
            try:
                secure_store.write_json(self.path, self.state)
            except OSError:
                logger.exception("Couldn't save the music taste file")

    # ---- the first-run picks ----
    def needs_onboarding(self):
        return not self.state.get("onboarded")

    def set_seeds(self, languages, genres, artists):
        """languages: codes in the order picked; artists: [{"id","name","artwork"}]."""
        with self._lock:
            lang0 = languages[0] if languages else None
            self.state["seeds"] = {"languages": list(languages), "genres": list(genres),
                                   "artists": [dict(a, language=a.get("language") or lang0) for a in artists]}
            self.state["onboarded"] = True
            self.state["feed"] = {}
        self.save()

    def skip_onboarding(self):
        self.state["onboarded"] = True
        self.save()

    def reset(self):
        """Forgets the picks and everything learned from listening."""
        with self._lock:
            prefs = self.state.get("prefs", {})
            self.state = {"onboarded": False, "seeds": {"languages": [], "genres": [], "artists": []},
                          "events": [], "tracks": {}, "totals": {}, "blocked": {"artists": {}, "tracks": []},
                          "feed": {}, "prefs": prefs or {"autoplay": True, "discover": 0.3},
                          "meta": self.state.get("meta") or {}}       # what songs are like stays known
            self.session = upnext.Session()
            self.memory = upnext.Memory(self.state)
        self.save()

    # ---- listening ----
    def log(self, track, kind, pos_s=0.0, completion=0.0, when=None, save=True):
        if not track or not track.get("id") or track.get("kind", "track") != "track":
            return
        when = when or time.time()
        with self._lock:
            self.state["tracks"][track["id"]] = _slim(track)
            self.state["events"].append([int(when), track["id"], kind, round(float(pos_s), 1),
                                         round(float(completion), 3), time.localtime(when).tm_hour])
            self._trim()
        if save:
            self.save()

    def started(self, track, chosen=False):
        """A song began: the session won't play it (or a version of it) again;
        one you chose yourself leads it from here."""
        if track and track.get("id"):
            with self._lock:
                self.session.started(track, upnext.features(track, self.memory), chosen)

    def listen_ended(self, track, pos_s, duration_s, natural=False, when=None):
        """A listen's end: played through, or skipped -- and how far in. The
        session moves with it at once (what plays next follows)."""
        if not track or not duration_s:
            return
        with self._lock:
            self.session.heard(track, upnext.features(track, self.memory), duration_s if natural else pos_s,
                               duration_s)
        completion = min(1.0, max(0.0, pos_s / float(duration_s)))
        if natural or completion >= C.COMPLETE_AT:
            self.log(track, "complete", pos_s, completion, when)
        else:
            self.log(track, "skip", pos_s, completion, when)

    def dislike(self, track):
        self.log(track, "dislike")
        with self._lock:
            if track["id"] not in self.state["blocked"]["tracks"]:
                self.state["blocked"]["tracks"].append(track["id"])
        self.save()

    def block_artist(self, track):
        with self._lock:
            self.state["blocked"]["artists"][artist_key(track)] = track.get("artist") or ""
        self.save()

    def _trim(self):
        """The oldest listens beyond EVENTS_KEPT are summed into per-artist totals."""
        ev = self.state["events"]
        if len(ev) <= C.EVENTS_KEPT:
            return
        old, self.state["events"] = ev[:-C.EVENTS_KEPT], ev[-C.EVENTS_KEPT:]
        now = time.time()
        for e in old:
            t = self.state["tracks"].get(e[1]) or {}
            key = artist_key(t)
            self.state["totals"][key] = self.state["totals"].get(key, 0.0) + self._score(e) * self._decay(e[0], now)
        live = {e[1] for e in self.state["events"]}
        self.state["tracks"] = {k: v for k, v in self.state["tracks"].items() if k in live}

    @staticmethod
    def _score(e):
        kind, pos = e[2], e[3]
        if kind == "skip":
            return C.SKIP_EARLY if pos < C.SKIP_EARLY_S else C.SKIP_LATE
        return C.EVENT_SCORES.get(kind, 0.0)

    @staticmethod
    def _decay(ts, now):
        return 0.5 ** (max(0.0, now - ts) / 86400.0 / C.HALF_LIFE_DAYS)

    # ---- what you like, right now ----
    def profile(self, now=None):
        now = now or time.time()
        with self._lock:
            events = list(self.state["events"])
            tracks = dict(self.state["tracks"])
            seeds = self.state["seeds"]
            totals = dict(self.state["totals"])
        artists = dict(totals)
        names = {}
        track_scores, recent = {}, {}
        session_score, hour = {}, {}
        bucket = time.localtime(now).tm_hour // 6
        plays = 0
        for e in events:
            t = tracks.get(e[1]) or {}
            key = artist_key(t)
            names[key] = t.get("artist", "")
            s = self._score(e) * self._decay(e[0], now)
            # "Not for me" is about the song; it leans on its artist only half as hard
            artists[key] = artists.get(key, 0.0) + (s * C.DISLIKE_ARTIST_SHARE if e[2] == "dislike" else s)
            track_scores[e[1]] = track_scores.get(e[1], 0.0) + s
            if e[2] in ("play", "search_play"):
                plays += 1
                recent[e[1]] = max(recent.get(e[1], 0), e[0])
                if e[5] // 6 == bucket:
                    hour[key] = hour.get(key, 0) + 1
            if now - e[0] < C.SESSION_HOURS * 3600:
                session_score[key] = session_score.get(key, 0.0) + s
        # what's being enjoyed right now: artists whose listens this session add up to more than skips
        session = {k for k, v in session_score.items() if v > 0.3}
        fade = math.exp(-plays / float(C.SEED_FADE_PLAYS))
        for a in seeds.get("artists", []):
            artists[a["id"]] = artists.get(a["id"], 0.0) + C.SEED_ARTIST * fade
            names.setdefault(a["id"], a.get("name", ""))
        languages = {}
        for i, code in enumerate(seeds.get("languages", [])):
            languages[code] = C.SEED_LANGUAGE * (C.SEED_LANGUAGE_STEP ** i) * max(0.25, fade)
        # ... and the languages actually listened to, learned as you play
        heard = {}
        for e in events:
            t = tracks.get(e[1]) or {}
            lang = upnext.features(t, self.memory)["lang"] if t else None
            if lang:
                heard[lang] = heard.get(lang, 0.0) + self._score(e) * self._decay(e[0], now)
        top_heard = max([v for v in heard.values() if v > 0] or [0])
        for code, v in heard.items():
            if top_heard > 0:
                languages[code] = languages.get(code, 0.0) + 1.4 * v / top_heard
        artist_language = {a["id"]: a.get("language") for a in seeds.get("artists", []) if a.get("language")}
        top_hour = max(hour.values()) if hour else 1
        return {"artists": artists, "names": names, "tracks": track_scores, "recent": recent,
                "session": session, "hour": {k: v / top_hour for k, v in hour.items()}, "plays": plays,
                "languages": languages, "artist_language": artist_language, "seed_fade": fade,
                "blocked_artists": set(self.state["blocked"]["artists"]),
                "blocked_tracks": set(self.state["blocked"]["tracks"])}

    def top_artists(self, prof, n=C.TOP_ARTISTS):
        good = [(s, a) for a, s in prof["artists"].items() if s > 0 and not a.startswith("name:")
                and a not in prof["blocked_artists"]]
        return [a for _s, a in sorted(good, reverse=True)[:n]]

    # ---- ranking ----
    def language_of(self, track, prof):
        return (upnext.features(track, self.memory)["lang"] or prof["artist_language"].get(artist_key(track)))

    def rank(self, cands, prof, limit, now=None, explore=C.EXPLORE_SHARE, rng=None, exclude=()):
        """`cands` (each with a "reason") ranked by taste, then re-ordered for
        variety: no artist twice in a row, at most MAX_PER_ARTIST, and about
        `explore` of the list from artists you don't know yet."""
        now = now or time.time()
        rng = rng or random.Random(int(now // 86400))       # the same order all day
        discover = float(self.state["prefs"].get("discover", 0.3))
        top = max([v for v in prof["artists"].values() if v > 0] or [1.0])
        langs = prof["languages"]
        top_lang = max(langs.values()) if langs else 1.0
        seen, scored = set(exclude), []
        for t in cands:
            if not t or t.get("id") in seen or t["id"] in prof["blocked_tracks"]:
                continue
            key = artist_key(t)
            if key in prof["blocked_artists"] or prof["artists"].get(key, 0) < C.ARTIST_DROPPED_BELOW:
                continue
            seen.add(t["id"])
            a = prof["artists"].get(key, 0.0) / top
            s = 1.5 * max(-1.0, min(1.0, a)) + t.get("_prior", 0.0)
            lang = self.language_of(t, prof)
            if lang and langs:
                s += 0.6 * langs.get(lang, -0.3) / top_lang
            if key in prof["session"]:
                s += C.SESSION_BOOST
            s += C.HOUR_BOOST * prof["hour"].get(key, 0.0)
            s += 0.5 * max(-1.0, min(1.0, prof["tracks"].get(t["id"], 0.0) / 3.0))
            last = prof["recent"].get(t["id"])
            if last and now - last < C.RECENT_PENALTY_DAYS * 86400:
                s -= 0.9
            known = prof["artists"].get(key, 0.0) > 0
            if not known:
                s += 0.5 * discover
            scored.append([s, t, known])
        scored.sort(key=lambda x: -x[0])
        # a share kept for songs from outside what you know
        n_explore = int(round(limit * explore))
        unknown = [x for x in scored if not x[2]]
        rng.shuffle(unknown)
        picks = unknown[:n_explore]
        for x in picks:
            x[1] = dict(x[1], reason=x[1].get("reason") or "Something new for you")
        ordered = [x for x in scored if x not in picks]
        # every few, one of the new ones
        merged = []
        step = max(2, int(1 / explore)) if explore > 0 else 10 ** 6
        for i, x in enumerate(ordered):
            if picks and i and i % step == 0:
                merged.append(picks.pop(0))
            merged.append(x)
        merged += picks
        return self._spread(merged, limit)

    @staticmethod
    def _spread(scored, limit):
        out, counts, pending = [], {}, list(scored)
        while pending and len(out) < limit:
            last = artist_key(out[-1]) if out else None
            pick = next((x for x in pending if artist_key(x[1]) != last
                         and counts.get(artist_key(x[1]), 0) < C.MAX_PER_ARTIST), None)
            if pick is None:
                break
            pending.remove(pick)
            counts[artist_key(pick[1])] = counts.get(artist_key(pick[1]), 0) + 1
            out.append(pick[1])
        return out

    # ---- the feed ----
    def build_feed(self, now=None):
        """Daily Mixes, Discover, Because you played X, New from your artists
        and (from the first-run picks alone) Picked for you. Blocking: a dozen
        or so page fetches the first time, few after. Kept, and returned."""
        now = now or time.time()
        prof = self.profile(now)
        top = self.top_artists(prof)
        with ThreadPoolExecutor(max_workers=6) as pool:
            pages = dict(zip(top, pool.map(self.fetch.artist, top)))
        pages = {k: v for k, v in pages.items() if v}
        feed = {"built": int(now), "mixes": [], "discover": [], "because": [], "new": [], "picked": []}

        def tag(tracks, reason, prior=0.0):
            return [dict(t, reason=reason, _prior=prior - i * 0.004) for i, t in enumerate(tracks or [])]

        # artists their fans play
        related = {}
        for aid in top[:4]:
            for r in (pages.get(aid) or {}).get("related", [])[:3]:
                if r["browse_id"] not in pages and r["browse_id"] not in related:
                    related[r["browse_id"]] = aid
        with ThreadPoolExecutor(max_workers=6) as pool:
            rel_pages = dict(zip(related, pool.map(self.fetch.artist, list(related))))

        # Daily Mixes: one per top artist, with the artists near them
        for i, aid in enumerate(top[:3]):
            page = pages.get(aid)
            if not page:
                continue
            name = page.get("title") or prof["names"].get(aid, "")
            pool_ = tag(page.get("songs"), "Because you like %s" % name, 0.3)
            near = [r for r, of in related.items() if of == aid]
            for r in near:
                rp = rel_pages.get(r) or {}
                pool_ += tag(rp.get("songs"), "Fans of %s play %s" % (name, rp.get("title", "this")))
            if page.get("songs"):
                seed_song = page["songs"][0]
                pool_ += tag((self.fetch.radio(seed_song["id"].split(":", 1)[1]) or [])[1:],
                             "Like %s" % seed_song["title"], -0.1)
            tracks = self.rank(pool_, prof, C.MIX_LENGTH, now)
            if tracks:
                others = [rel_pages[r]["title"] for r in near if rel_pages.get(r)][:2]
                feed["mixes"].append({"kind": "mix", "id": "mix:%d" % (i + 1), "title": "Daily Mix %d" % (i + 1),
                                      "artist": ", ".join([name] + others) + " and more",
                                      "subtitle": ", ".join([name] + others), "artwork": tracks[0].get("artwork"),
                                      "tracks": tracks,
                                      "reason": "Made from %s and the artists near them" % name})

        # Discover: artists your artists' fans play, that you haven't
        disc = []
        for r, of in related.items():
            rp = rel_pages.get(r) or {}
            if prof["artists"].get(r, 0) <= 0:
                disc += tag(rp.get("songs"), "Fans of %s also play %s" % (
                    (pages.get(of) or {}).get("title", "your artists"), rp.get("title", "this")))
        week = random.Random(int(now // (7 * 86400)))          # the same Discover all week
        feed["discover"] = self.rank(disc, prof, 20, now, explore=0.0, rng=week)

        # Because you played X: the radio of the latest song you finished or starred
        with self._lock:
            liked = [e[1] for e in reversed(self.state["events"]) if e[2] in ("complete", "like", "search_play")]
            tracks_meta = dict(self.state["tracks"])
        for tid in list(dict.fromkeys(liked))[:2]:
            t = tracks_meta.get(tid) or {}
            if not tid.startswith("yt:"):
                continue
            rad = (self.fetch.radio(tid.split(":", 1)[1]) or [])[1:]
            ranked = self.rank(tag(rad, "Because you played %s" % t.get("title", "")), prof, 12, now)
            if ranked:
                feed["because"].append({"title": t.get("title", ""), "artist": t.get("artist", ""), "tracks": ranked})

        # New from your artists: their singles and albums from this year and last
        year = time.localtime(now).tm_year
        new = []
        for aid, page in pages.items():
            for al in (page.get("singles") or []) + (page.get("albums") or []):
                if str(al.get("year") or "").isdigit() and year - 1 <= int(al["year"]) <= year:
                    new.append(dict(al, reason="New from %s" % page.get("title", "")))
        new.sort(key=lambda a: -int(a["year"]))
        feed["new"] = new[:12]

        # from the picks alone: before there's listening to go on
        seeds = self.state["seeds"]
        if seeds.get("languages") or seeds.get("genres"):
            qs = []
            langs = [LANGUAGE_NAMES.get(c, c) for c in seeds.get("languages", [])[:2]] or [""]
            genres = seeds.get("genres", [])[:3] or [""]
            for g in genres:
                qs.append(("%s %s songs" % (langs[0], g)).strip())
            if len(langs) > 1:
                qs.append(("%s %s songs" % (langs[1], genres[0])).strip())
            with ThreadPoolExecutor(max_workers=4) as pool:
                found = list(pool.map(self.fetch.search, qs))
            picked = []
            for q, f in zip(qs, found):
                picked += tag((f or {}).get("songs"), "For your taste: %s" % q.replace(" songs", ""))
            feed["picked"] = self.rank(picked, prof, 20, now)
        with self._lock:
            self.state["feed"] = feed
        self.save()
        return feed

    def feed(self):
        return self.state.get("feed") or {}

    def feed_age(self, now=None):
        built = (self.feed() or {}).get("built")
        return (now or time.time()) - built if built else None

    def autoplay(self, track, exclude=(), now=None, n=15, chosen=False):
        """Songs to follow `track`: its radio, put in order by the listening
        session (upnext.choose) -- same language and mood, nothing played
        again, nothing marked "Not for me"."""
        if not track or not str(track.get("id", "")).startswith("yt:"):
            return []
        prof = self.profile(now)
        with self._lock:
            out = upnext.choose(track, self.session, self.memory, self.fetch.radio_full, prof=prof,
                                exclude=exclude, n=n, now=now, chosen=chosen)
        self.save()
        return out
