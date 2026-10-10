"""Up next: what plays after the song you chose -- a radio built on it that
follows what you're listening to.

Autoplay as Spotify describes its own: the song's neighbours (collaborative
filtering -- songs played together by people who play this one), put in
order by what you're listening to right now. Here:

  * Neighbours: YouTube Music's radio for the song (its "Start radio",
    the same collaborative filtering), and the radio of the last song you
    played through -- a song both suggest comes first.
  * The song's character: YouTube Music tunes each radio with chips for
    the moods, genres, languages and eras around the song ("Romance",
    "Chill", "Party", "Pump-up", "Hindi", "Desi hip-hop", "Indian
    devotional"...). Which chips a song has says what it is -- a party
    song's radio has "Party" and no "Chill" -- and the radio tuned to the
    session's language and mood marks every song in it as that language
    and mood. Song and artist characters are remembered, so the picture
    gets sharper the more you play.
  * The session: every song moves it. Played through, its language, energy,
    genres and artist count for the next picks; skipped in its first
    half-minute, against them -- so a skipped hip-hop song steers away from
    hip-hop, and a run of sad songs keeps it sad.
  * Rules: never the same song again in a session (nor another version of
    it -- a remix, lo-fi, slowed or cover -- unless that's what's playing),
    no artist twice in a row, nothing marked "Not for me".

Everything is a plain score per candidate (score()); the weights are below.
"""
import random
import re
import time
import unicodedata
from collections import deque

from ..logging_setup import get_logger

logger = get_logger("upnext")

# ---- weights ----------------------------------------------------------------------------
W_SOURCE = {"radio": 1.0, "mood": 0.9, "lang": 0.8, "prev": 0.6}   # a neighbour's place in each radio
W_AGREE = 0.35            # each further radio that suggests the same song
W_LANG_MATCH, W_LANG_CLASH, W_LANG_UNKNOWN = 0.8, -2.2, -0.35
W_ENERGY = 1.1            # per unit of energy difference (-1 calm .. +1 energetic)
W_GENRE = 0.35
W_GENRE_CLASH = -1.0      # hip-hop into a ghazal session, devotional into a party
W_VARIANT = -1.4          # a remix / lo-fi / slowed version when the session isn't
W_ARTIST_SESSION = 0.5
W_ARTIST_SKIPPED = -1.8
W_ARTIST_TASTE = 0.35
W_RECENT_DAYS = -0.9      # played in the last two days
NOISE = 0.07              # a little chance, so two sessions aren't identical
SKIP_EARLY_S = 30

ENERGETIC = {"party", "pump-up", "workout", "energy boosters", "dance", "dance & electronic", "edm", "club",
             "house music", "electronic", "energize"}
CALM = {"chill", "romance", "sleep", "focus", "relax", "sad", "downbeat", "commute", "feel good", "ghazal",
        "sufi", "acoustic", "lo-fi", "lofi"}
LANG_CHIPS = {"hindi": "hi", "punjabi": "pa", "tamil": "ta", "telugu": "te", "bengali": "bn", "marathi": "mr",
              "kannada": "kn", "malayalam": "ml", "gujarati": "gu", "english": "en", "korean": "ko", "k-pop": "ko",
              "spanish": "es", "latin": "es", "japanese": "ja", "j-pop": "ja", "arabic": "ar", "french": "fr",
              "urdu": "ur", "bhojpuri": "bho", "haryanvi": "hry", "indian other languages": "in-other"}
# genres that don't sit next to each other in one listening session
CLASHES = ({"desi hip-hop", "hip-hop", "rap"}, {"indian devotional", "devotional", "spiritual"},
           {"ghazal", "sufi", "indian classical"}, {"metal", "rock"}, {"edm", "house music", "electronic"})
_ERA = re.compile(r"^(19|20)\d0s$")

_SAD = {"dard", "judaai", "judai", "bewafa", "tanha", "tanhai", "alvida", "rula", "rulaya", "aansu", "aansoo",
        "toota", "tuta", "toot", "gham", "ghum", "bichad", "bichhad", "bichde", "sad", "heartbreak", "broken",
        "tears", "cry", "crying", "lonely", "alone", "missing", "goodbye", "akela", "akele", "dooriyan",
        "doori", "intezaar", "wafa", "zakhm", "sazaa", "saza", "hurt"}
_PARTY = {"party", "dance", "nach", "nachle", "naach", "disco", "dj", "club", "bhangra", "thumka", "jhoom",
          "dhol", "remix", "mashup", "banger", "lit", "groove", "boom", "beat", "beats", "workout", "gym"}
_HINDI = {"tum", "tera", "teri", "tere", "mera", "meri", "mere", "dil", "pyaar", "pyar", "ishq", "mohabbat", "hai",
          "hain", "main", "mein", "hum", "humko", "tumko", "nahi", "kya", "kyun", "jaana", "jaane", "yaar",
          "yaara", "saathiya", "sanam", "zindagi", "raat", "baat", "aankhon", "aankhein", "kabhi", "koi",
          "sajna", "sajni", "dhadkan", "dard", "tanha", "tujh", "tujhe", "mujhe", "mujh", "sang", "saath",
          "kaise", "kahin", "chal", "chalo", "aaja", "bin", "sapne", "sapna", "khwab", "baarish", "barish",
          "rang", "jiya", "pehli", "kuch", "bhi", "phir", "dobara", "yaad", "yaadein", "bewafa", "jaan", "raha",
          "rahi", "gaya", "gayi", "diya", "karo", "dekha", "nazar", "duniya", "kahani", "safar", "musafir",
          "kesariya", "channa", "mereya", "hoon", "hoga", "hogi", "milke", "mile", "tujhse", "mujhse", "teri",
          "aashiqui", "mehboob", "chand", "chaand", "sitare", "humsafar", "dilbar", "deewana", "deewani",
          "pagal", "jaanam", "khuda", "rabba", "rab", "maula", "piya", "saiyaan", "saiyan", "balam"}
_PUNJABI = {"tainu", "mainu", "sanu", "kithe", "jatt", "kudi", "munda", "mundeya", "balle", "gal", "soniye",
            "sohneya", "tera", "ve", "vey", "nai", "jaandi", "laung", "laachi", "gabru", "yaaran", "naal",
            "kade", "haaye", "pind", "sardar", "punjabi"}
_VARIANTS = (("edit", re.compile(r"\b(remix|re-?mix|lo-?fi|lofi|slowed|reverb|sped[ -]?up|8d|nightcore|"
                                  r"bass ?boosted|mashup|dj|flip|phonk)\b", re.I)),
             ("cover", re.compile(r"\b(cover|karaoke|instrumental|tribute)\b", re.I)),
             ("alt", re.compile(r"\b(unplugged|acoustic|reprise|live|female version|male version)\b", re.I)))
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FROM = re.compile(r"\s+(from|feat\.?|ft\.?|featuring|prod\.?)\s+.*$", re.I)


# ---------------------------------------------------------------- reading a song ---
def artist_key(track):
    return track.get("artist_browse") or ("name:" + (track.get("artist") or "").split(",")[0].strip().lower())


def song_key(track):
    """The song itself, whatever version or upload: "Tum Hi Ho (From
    "Aashiqui 2") [Lo-fi]" and "tum hi ho - lofi" are the same."""
    t = unicodedata.normalize("NFKC", track.get("title") or "").lower()
    if " - " in t and len(t.split(" - ", 1)[1]) > 2:
        left, right = t.split(" - ", 1)
        # "Artist - Song": the song half
        t = right if (track.get("artist") or "").lower().split(",")[0].strip() in left else t
    t = _BRACKETS.sub(" ", t)
    t = _FROM.sub("", t)
    for _k, rx in _VARIANTS:
        t = rx.sub(" ", t)
    return re.sub(r"[^\w]+", "", t)


def variant_of(track):
    title = track.get("title") or ""
    for kind, rx in _VARIANTS:
        if rx.search(title):
            return kind
    return None


def _words(text):
    return set(re.findall(r"[a-z]+", unicodedata.normalize("NFKC", text or "").lower()))


def lexicon_language(track):
    words = _words(track.get("title"))
    if not words:
        return None
    hi, pa = len(words & _HINDI), len(words & _PUNJABI)
    if pa >= 2 and pa > hi:
        return "pa"
    if hi >= 1 and (hi >= 2 or len(words) <= 3):
        return "hi"
    return None


def lexicon_energy(track):
    words = _words(track.get("title")) | _words(track.get("album"))
    if words & _PARTY:
        return 0.8
    if words & _SAD:
        return -0.8
    return None


def chip_character(chips):
    """What a song's radio chips say about it: {"energy", "genres", "lang"}."""
    low = {c.lower() for c in chips}
    energetic, calm = low & ENERGETIC, low & CALM
    if energetic and not calm:
        energy = 0.8
    elif calm and not energetic:
        energy = -0.6
    elif energetic and calm:
        energy = 0.15
    else:
        energy = None
    langs = [LANG_CHIPS[c] for c in low if c in LANG_CHIPS and LANG_CHIPS[c] != "in-other"]
    genres = sorted(c for c in low if c not in ENERGETIC and c not in CALM and c not in LANG_CHIPS
                    and not _ERA.match(c) and c not in ("upbeat",))
    if "upbeat" in low and energy is None:
        energy = 0.4
    lang = langs[0] if len(langs) == 1 else None
    if lang is None and not langs and genres and not any("indian" in c or "desi" in c or "bollywood" in c
                                                        or "punjabi" in c for c in low):
        lang = "en"           # a radio of Western genres only, with no language of its own
    return {"energy": energy, "genres": genres, "lang": lang, "other_indian": "indian other languages" in low}


def _clash(a, b):
    """Two genre sets that don't belong in one session."""
    for group in CLASHES:
        if (a & group) and b and not (b & group) and (b - {"pop", "indian film music", "adult pop"}):
            return True
    return False


# ---------------------------------------------------------------- memory ---
class Memory:
    """What's known about songs and artists from their radios: kept in the
    taste file (state["meta"]), bounded."""
    MAX_SONGS = 4000

    def __init__(self, state):
        self.m = state.setdefault("meta", {})
        self.m.setdefault("songs", {})
        self.m.setdefault("artists", {})

    def song(self, tid):
        return self.m["songs"].get(tid) or {}

    def remember(self, track, character, weight=1.0):
        """What a song is like -- from its own radio's chips (weight 1), or
        from being in a tuned radio (less) -- and, through it, its artist."""
        if not track or not track.get("id"):
            return
        songs = self.m["songs"]
        old = songs.get(track["id"]) or {}
        new = {}
        if character.get("energy") is not None and (weight >= 1 or "e" not in old):
            new["e"] = round(character["energy"], 2)
        if character.get("genres") and weight >= 1:
            new["g"] = list(character["genres"])[:6]
        if character.get("lang") and (weight >= 1 or "l" not in old):
            new["l"] = character["lang"]
        if not new or all(old.get(k) == v for k, v in new.items()):
            return
        s = dict(old)
        s.update(new)
        songs.pop(track["id"], None)
        songs[track["id"]] = s
        if len(songs) > self.MAX_SONGS:
            for k in list(songs)[:len(songs) - self.MAX_SONGS]:
                del songs[k]
        a = self.m["artists"].setdefault(artist_key(track), {"l": {}, "e": [0.0, 0], "g": {}})
        if new.get("l") and new["l"] != old.get("l"):
            a["l"][new["l"]] = a["l"].get(new["l"], 0) + weight
        if "e" in new and "e" not in old:
            a["e"] = [a["e"][0] + new["e"] * weight, a["e"][1] + weight]
        for g in new.get("g", []):
            a["g"][g] = a["g"].get(g, 0) + 1

    def artist_language(self, track):
        a = self.m["artists"].get(artist_key(track)) or {}
        langs = a.get("l") or {}
        total = sum(langs.values())
        if total >= 1:
            best = max(langs, key=langs.get)
            if langs[best] / total >= 0.7:
                return best
        return None

    def artist_energy(self, track):
        a = self.m["artists"].get(artist_key(track)) or {}
        e = a.get("e") or [0, 0]
        return e[0] / e[1] if e[1] >= 2 else None

    def artist_genres(self, track):
        a = self.m["artists"].get(artist_key(track)) or {}
        g = a.get("g") or {}
        n = max(g.values()) if g else 0
        return {k for k, v in g.items() if v >= max(1, n * 0.5)}


def features(track, memory, tags=None):
    """A song's language, energy (-1 calm .. 1 energetic), genres and version."""
    from .taste import script_language
    tags = tags or {}
    known = memory.song(track.get("id")) if memory else {}
    lang = (script_language((track.get("title") or "") + " " + (track.get("artist") or ""))
            or known.get("l") or tags.get("lang") or (memory.artist_language(track) if memory else None)
            or lexicon_language(track))
    energy = known.get("e")
    if energy is None:
        energy = tags.get("energy")
    if energy is None:
        energy = lexicon_energy(track)
    if energy is None and memory:
        energy = memory.artist_energy(track)
    genres = set(known.get("g") or []) | set(tags.get("genres") or [])
    if not genres and memory:
        genres = memory.artist_genres(track)
    return {"lang": lang, "energy": energy, "genres": genres, "variant": variant_of(track)}


# ---------------------------------------------------------------- the session ---
class Session:
    """What's being listened to right now. Every song moves it: weights fade
    by a sixth each time, so the last few songs count most."""
    FADE = 0.84

    def __init__(self):
        self.lang, self.genre, self.artist, self.variant = {}, {}, {}, {}
        self.energy = [0.0, 0.0]
        self.played = deque(maxlen=80)          # (id, song key)
        self.last_complete = None
        self.skips_in_row = 0

    def _fade(self):
        for d in (self.lang, self.genre, self.artist, self.variant):
            for k in list(d):
                d[k] *= self.FADE
                if abs(d[k]) < 0.02:
                    del d[k]
        self.energy = [self.energy[0] * self.FADE, self.energy[1] * self.FADE]

    def started(self, track, feats=None, chosen=False):
        """A song began. One you chose yourself says what you want now: its
        language and mood lead the session from here."""
        self.played.append((track.get("id"), song_key(track)))
        if chosen and feats:
            self._fade()
            if feats.get("lang"):
                for k in list(self.lang):
                    if k != feats["lang"] and self.lang[k] > 0:
                        self.lang[k] *= 0.4
                self.lang[feats["lang"]] = self.lang.get(feats["lang"], 0.0) + 1.0
            if feats.get("energy") is not None:
                self.energy = [self.energy[0] * 0.4 + feats["energy"] * 1.2, self.energy[1] * 0.4 + 1.2]

    def heard(self, track, feats, pos_s, duration_s):
        """A song's end: played through (it counts for its kind), skipped early
        (against), or in between (a little for)."""
        frac = pos_s / duration_s if duration_s else 0.0
        if frac >= 0.8:
            w = 1.0
        elif pos_s < SKIP_EARLY_S:
            w = -1.0
        else:
            w = 0.3 + 0.5 * frac
        # a skip is held against what made the song different from the session,
        # not what it shared: a skipped Hindi party song in a Hindi session
        # says "not party", not "not Hindi"
        lang_now, energy_now = self.language(), self.target_energy()
        self._fade()
        if feats.get("lang") and not (w < 0 and feats["lang"] == lang_now):
            self.lang[feats["lang"]] = self.lang.get(feats["lang"], 0.0) + w
        for g in feats.get("genres") or ():
            if not (w < 0 and self.genre.get(g, 0.0) > 0.5):
                self.genre[g] = self.genre.get(g, 0.0) + w * 0.7
        if w < 0 and feats.get("energy") is not None and energy_now is not None and \
                abs(feats["energy"] - energy_now) > 0.5:
            # the mood was wrong: lean the session the other way a little
            self.energy = [self.energy[0] + energy_now * 0.6, self.energy[1] + 0.6]
        if feats.get("energy") is not None and w > 0:
            self.energy = [self.energy[0] + feats["energy"] * w, self.energy[1] + w]
        key = artist_key(track)
        self.artist[key] = self.artist.get(key, 0.0) + (w * 1.4 if w < 0 else w * 0.6)
        v = feats.get("variant") or "original"
        self.variant[v] = self.variant.get(v, 0.0) + w
        if w > 0.6:
            self.last_complete = track
            self.skips_in_row = 0
        elif w < 0:
            self.skips_in_row += 1

    def language(self):
        good = {k: v for k, v in self.lang.items() if v > 0}
        if not good:
            return None
        best = max(good, key=good.get)
        return best if good[best] >= 0.5 and good[best] >= 0.6 * sum(good.values()) else None

    def target_energy(self):
        return self.energy[0] / self.energy[1] if self.energy[1] >= 0.4 else None

    def played_keys(self):
        return {k for _i, k in self.played}

    def played_ids(self):
        return {i for i, _k in self.played}


# ---------------------------------------------------------------- choosing ---
def _mood_chip(chips, energy):
    """The radio chip that keeps the session's mood: calm, or energetic."""
    names = {c.lower(): c for c in chips}
    if energy is None:
        return None
    order = (("party", "pump-up", "workout", "upbeat") if energy >= 0.25 else
             ("chill", "romance", "sad", "downbeat", "sleep") if energy <= -0.15 else ())
    for n in order:
        if n in names:
            return names[n]
    return None


def _lang_chip(chips, lang):
    for c in chips:
        if LANG_CHIPS.get(c.lower()) == lang:
            return c
    return None


def gather(seed, session, memory, fetch_radio, prev=None, chosen=False):
    """Candidates for after `seed`, each with where it came from and what the
    tuned radios say about it. fetch_radio(video_id, tune=None) ->
    {"tracks", "chips"} (cached by the caller)."""
    vid = seed["id"].split(":", 1)[1]
    main = fetch_radio(vid) or {"tracks": [], "chips": {}}
    chips = main.get("chips") or {}
    character = chip_character(chips)
    memory.remember(seed, character)
    seed_f = features(seed, memory)
    lists = {"radio": [t for t in main.get("tracks") or [] if t["id"] != seed["id"]]}
    tags = {}
    # the song decides the language (one you picked in another language is what you want now);
    # its mood leads, the session's follows
    lang = seed_f["lang"] or session.language()
    energy = session.target_energy()
    lead = 0.8 if chosen else 0.6
    if seed_f["energy"] is not None:
        energy = seed_f["energy"] if energy is None else lead * seed_f["energy"] + (1 - lead) * energy
    lc = _lang_chip(chips, lang) if lang else None
    if lc:
        tuned = fetch_radio(vid, chips[lc]) or {}
        lists["lang"] = [t for t in tuned.get("tracks") or [] if t["id"] != seed["id"]]
        for t in lists["lang"]:
            tags.setdefault(t["id"], {})["lang"] = lang
            memory.remember(t, {"lang": lang}, weight=0.5)
    mc = _mood_chip(chips, energy)
    if mc:
        tuned = fetch_radio(vid, chips[mc]) or {}
        lists["mood"] = [t for t in tuned.get("tracks") or [] if t["id"] != seed["id"]]
        e = 0.8 if mc.lower() in ENERGETIC or mc.lower() == "upbeat" else -0.6
        for t in lists["mood"]:
            tags.setdefault(t["id"], {})["energy"] = e
            memory.remember(t, {"energy": e}, weight=0.5)
    if prev and prev.get("id") != seed["id"] and str(prev.get("id", "")).startswith("yt:") and \
            features(prev, memory)["lang"] == lang:
        pr = fetch_radio(prev["id"].split(":", 1)[1]) or {}
        lists["prev"] = [t for t in pr.get("tracks") or [] if t["id"] not in (prev["id"], seed["id"])]
    cands = {}
    for name, tracks in lists.items():
        n = max(1, len(tracks))
        for rank, t in enumerate(tracks):
            c = cands.setdefault(t["id"], {"track": t, "sources": {}})
            c["sources"][name] = 1.0 - rank / float(n)
    return {"cands": list(cands.values()), "tags": tags, "seed": seed_f, "lang": lang, "energy": energy,
            "chips": list(chips)}


def score(c, ctx, session, memory, prof=None, now=None, rng=None):
    """One candidate's score; (score, features, why) -- why in words."""
    t = c["track"]
    f = features(t, memory, ctx["tags"].get(t["id"]))
    s = sum(W_SOURCE[k] * v for k, v in c["sources"].items()) + W_AGREE * (len(c["sources"]) - 1)
    why = []
    lang = ctx.get("lang")
    if lang:
        if f["lang"] == lang:
            s += W_LANG_MATCH
        elif f["lang"] and f["lang"] != lang:
            s += W_LANG_CLASH
        else:
            s += W_LANG_UNKNOWN
    energy = ctx.get("energy")
    if energy is not None and f["energy"] is not None:
        s -= W_ENERGY * abs(energy - f["energy"])
    elif energy is not None:
        s -= 0.25
    sg = {g for g, w in session.genre.items() if w > 0.3} or set(ctx["seed"].get("genres") or ())
    if f["genres"]:
        s += W_GENRE * len(f["genres"] & sg)
        if sg and _clash(f["genres"], sg):
            s += W_GENRE_CLASH
    for g, w in session.genre.items():
        if w < -0.3 and g in f["genres"]:
            s += 0.6 * w
    seed_v = ctx["seed"].get("variant")
    if f["variant"] and f["variant"] != seed_v and session.variant.get(f["variant"], 0.0) <= 0.2:
        s += W_VARIANT
    a = artist_key(t)
    sw = session.artist.get(a, 0.0)
    if sw < -0.5:
        s += W_ARTIST_SKIPPED
    else:
        s += W_ARTIST_SESSION * min(1.0, sw)
    if prof:
        top = max([v for v in prof["artists"].values() if v > 0] or [1.0])
        s += W_ARTIST_TASTE * max(-1.0, min(1.0, prof["artists"].get(a, 0.0) / top))
        last = prof["recent"].get(t["id"])
        if last and (now or time.time()) - last < 2 * 86400:
            s += W_RECENT_DAYS
    s += (rng or random).gauss(0, NOISE)
    if len(c["sources"]) > 1:
        why.append("in %d radios" % len(c["sources"]))
    return s, f, why


def choose(seed, session, memory, fetch_radio, prof=None, exclude=(), n=20, now=None, rng=None, chosen=False):
    """Up to `n` songs to follow `seed`, in order, each with a "reason".
    `chosen`: the seed is a song you picked (not one autoplay did)."""
    rng = rng or random.Random()
    ctx = gather(seed, session, memory, fetch_radio, prev=session.last_complete, chosen=chosen)
    played_keys = session.played_keys() | {song_key(seed)}
    exclude = set(exclude) | session.played_ids() | {seed["id"]}
    blocked_t = prof["blocked_tracks"] if prof else set()
    blocked_a = prof["blocked_artists"] if prof else set()
    scored, seen_keys = [], set()
    for c in ctx["cands"]:
        t = c["track"]
        k = song_key(t)
        if t["id"] in exclude or t["id"] in blocked_t or k in played_keys or not k:
            continue
        if artist_key(t) in blocked_a or (prof and prof["artists"].get(artist_key(t), 0) < -6):
            continue
        s, f, why = score(c, ctx, session, memory, prof, now, rng)
        scored.append([s, t, f, k])
    scored.sort(key=lambda x: -x[0])
    # in order, with flow: no artist twice in a row, at most two in ten, one version of a song
    out, recent_artists = [], deque(maxlen=2)
    counts = {}
    pending = scored
    while pending and len(out) < n:
        pick = None
        for x in pending:
            a = artist_key(x[1])
            if a in recent_artists or counts.get(a, 0) >= 2 + len(out) // 10 or x[3] in seen_keys:
                continue
            pick = x
            break
        if pick is None:
            pick = next((x for x in pending if x[3] not in seen_keys), None)
            if pick is None:
                break
        pending.remove(pick)
        a = artist_key(pick[1])
        recent_artists.append(a)
        counts[a] = counts.get(a, 0) + 1
        seen_keys.add(pick[3])
        out.append(dict(pick[1], reason=_reason(seed, pick[2], ctx)))
    return out


_LANG_NAMES = {"hi": "Hindi", "pa": "Punjabi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "mr": "Marathi",
               "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati", "en": "English", "ko": "Korean",
               "es": "Spanish", "ja": "Japanese", "ar": "Arabic", "fr": "French", "ur": "Urdu"}


def _reason(seed, f, ctx):
    bits = []
    if f["lang"] and f["lang"] == ctx.get("lang"):
        bits.append(_LANG_NAMES.get(f["lang"], f["lang"]))
    e = f.get("energy")
    if e is not None:
        bits.append("upbeat" if e >= 0.35 else "mellow" if e <= -0.2 else "")
    bits = [b for b in bits if b]
    lead = ", ".join(bits)
    return ("%s — like %s" % (lead[:1].upper() + lead[1:], seed.get("title", ""))) if lead else \
        "Like %s" % seed.get("title", "")


__all__ = ["Session", "Memory", "choose", "features", "song_key", "variant_of", "chip_character"]
