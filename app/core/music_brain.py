"""The music logic the phone app and the PC share: the mixes, the DJ's sets, the charts, an artist's whole
catalogue, importing liked songs. It came from the Android app's bridge.py (the same decisions on both), so
every function still answers in JSON text: {"ok": value} or {"error": "what went wrong, in words"}. Call
value(fn, ...) to get the value itself, or MusicError with the words. Every call blocks: never on the UI thread.

Call use_taste(the tab's model) first, so there is one taste model and not two overwriting each other's file.
"""
import json
import os
import re
import sys
import threading
import time
import traceback

# what it keeps (chart matches, covers it looked up...) goes in the app's own folder
from app import config  # noqa: E402

os.environ.setdefault("AWESOME_MUSIC_HOME", os.path.join(config.APPDATA_DIR, "music-brain"))
os.makedirs(os.environ["AWESOME_MUSIC_HOME"], exist_ok=True)

_ready = False
_lock = threading.Lock()
_taste = None
_moods = None


def _ok(value):
    return json.dumps({"ok": value}, ensure_ascii=False)


def _err(e):
    from app.core import music_sources
    return json.dumps({"error": music_sources.friendly(e) or "Something went wrong."}, ensure_ascii=False)


def _call(fn, *args):
    try:
        return _ok(fn(*args))
    except Exception as e:   # noqa: BLE001 -- said in words on the other side
        try:
            from app.logging_setup import get_logger
            get_logger("bridge").info("%s failed: %s", getattr(fn, "__name__", fn), traceback.format_exc()[-1500:])
        except Exception:   # noqa: BLE001
            pass
        return _err(e)


def _j(text):
    return json.loads(text) if text else None


class MusicError(Exception):
    """Something the engine couldn't do, in words to show."""


def value(fn, *args):
    """fn's answer as the value itself. Arguments that are lists or dicts are passed as the JSON it expects."""
    args = [json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x for x in args]
    got = json.loads(fn(*args))
    if "error" in got:
        raise MusicError(got["error"])
    return got.get("ok")


def use_taste(model):
    """The tab's taste model is the one used here."""
    global _taste
    with _lock:
        _taste = model


# ------------------------------------------------------------------ start ---
def init(home, downloads):
    """Where the app keeps its things -- before anything else is imported."""
    global _ready
    with _lock:
        if _ready:
            return _ok(True)
        os.environ["AWESOME_MUSIC_HOME"] = home
        os.environ["AWESOME_MUSIC_DOWNLOADS"] = downloads
        os.makedirs(home, exist_ok=True)
        try:
            import certifi
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        except Exception:   # noqa: BLE001 -- the system's own then
            pass
        _phone_wording()
        _ready = True

    def warm():     # heavy, and only playing needs it: not on the way to the app opening, but ready for the first song
        time.sleep(4)
        _yt_dlp_version()
    threading.Thread(target=warm, daemon=True, name="warm-yt-dlp").start()
    return _ok(True)


def versions():
    """For Settings."""
    return _ok({"python": sys.version.split()[0], "yt_dlp": _yt_dlp_version()})


# What went wrong, said for the phone: the desktop's words point to its Browser tab's
# sign-in, which the phone app doesn't have.
_PHONE_WORDS = (
    ("not a bot", "YouTube is asking this connection to prove it isn't a bot. Wait a few minutes, or switch "
                  "between Wi-Fi and mobile data, then try again."),
    ("sign-in to go on", "YouTube is asking this connection to prove it isn't a bot. Wait a few minutes, or switch "
                         "between Wi-Fi and mobile data, then try again."),
    ("age-restricted", "This song is age-restricted — YouTube plays it only to a signed-in account, and this app "
                       "doesn't sign in."),
)


def _phone_wording():
    """music_sources.friendly(), with the phone's words where the desktop's mention its Browser tab."""
    from app.core import music_sources as ms
    if getattr(ms.friendly, "_phone", False):
        return
    desktop = ms.friendly

    def friendly(exc):
        text = desktop(exc)
        low = (str(exc or "") + " " + text).lower()
        for key, words in _PHONE_WORDS:
            if key in low:
                return words
        return text
    friendly._phone = True
    ms.friendly = friendly


def _yt_dlp_version():
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:   # noqa: BLE001
        return ""


def taste():
    global _taste
    with _lock:
        if _taste is None:
            from app.core import taste as taste_core
            _taste = taste_core.Taste()
    return _taste


# ------------------------------------------------------------------ search --
def search_part(part, query):
    """One part of the universal search (music_sources.search_everywhere asks
    all three at once; Kotlin does the same, one call each)."""
    from app.core import music_sources as ms
    jobs = {"music": ms.search_music, "free": ms.search_openverse, "live": ms.search_archive}
    try:
        got = jobs[part]((query or "").strip())
        return _ok(tidy_search((query or "").strip(), got) if part == "music" else got)
    except Exception as e:   # noqa: BLE001
        return json.dumps({"error": ms._short(e)})


# An upload rather than a release: "Video | Song...", "Full Video", "#hashtags", "(Official Video)", a film or
# studio's channel standing in for the singer. Charts of most-watched videos are full of these.
from app.core.ytmusic import UPLOAD_TITLE as _UPLOAD_TITLE   # one list of what an upload looks like
_UPLOAD_CHANNEL = re.compile(r"\b(films?|studios?|entertainments?|digital|creations?|productions?|pictures|"
                             r"bhakti|videos?|tv|channel|official)\b", re.I)


# a fan's party edit, not the song: kept, but after the real ones
_PARTY_EDIT = re.compile(r"\b(dj|full\s+bass|hard\s+bass|ringtone|dholki|jhankar|edm\s+mix|competition)\b", re.I)


def clean_title(title):
    """A video's title as the song's name: "Song (Official Music Video)", "Song | Singer | Film" -> "Song"."""
    t = re.split(r"\s+\|\s+", title or "")[0]
    t = re.sub(r"\s*[\(\[][^\)\]]*\b(video|audio|lyric(al)?|visuali[sz]er|4k|hd)\b[^\)\]]*[\)\]]", "", t, flags=re.I)
    return t.strip() or (title or "").strip()


def looks_like_upload(t, channel=True):
    """`t` is a video upload, not a song's release (see _UPLOAD_TITLE). `channel`: a channel's name standing in for
    the singer counts too -- for videos only (a real act can be called "TV Girl")."""
    return bool(_UPLOAD_TITLE.search(t.get("title") or "") or (channel and _UPLOAD_CHANNEL.search(t.get("artist") or "")))


def tidy_search(query, found):
    """YouTube Music's answer, made credible: songs with no singer and uploads dropped, a remix / lo-fi / slowed
    take only when one was asked for (else after the real ones), each song once; the albums the songs come from
    first, then real albums and soundtracks, then EPs, then singles -- so a film's name brings up its album, not
    a stranger's single named after it."""
    from app.core import upnext
    if not isinstance(found, dict):
        return found
    q = (query or "").lower()
    asked_variant = set(m.lower() for m in _VARIANT.findall(q))
    songs, later, seen = [], [], set()
    for s in found.get("songs") or []:
        if not (s.get("artist") or "").strip() or looks_like_upload(s, channel=False):
            continue
        k = upnext.song_key(s)
        if not k or k in seen:
            continue
        seen.add(k)
        variant = set(m.lower() for m in _VARIANT.findall((s.get("title") or "") + " " + (s.get("album") or "")))
        party = _PARTY_EDIT.search(s.get("title") or "") and not _PARTY_EDIT.search(q)
        (later if variant - asked_variant or party else songs).append(s)
    from_songs = set((s.get("album") or "").strip().lower() for s in songs[:12] if s.get("album"))
    rank = {"album": 1, "soundtrack": 1, "ep": 2, "single": 3}

    def album_rank(i_a):
        i, a = i_a
        own = (a.get("title") or "").strip().lower() in from_songs
        return (0 if own else rank.get((a.get("type") or "album").strip().lower(), 2), i)
    albums = [a for _, a in sorted(enumerate(found.get("albums") or []), key=album_rank)][:14]
    out = dict(found, songs=songs + later, albums=albums)
    top = found.get("top")
    if isinstance(top, dict) and top.get("kind") == "track" and (looks_like_upload(top, channel=False) or not (top.get("artist") or "").strip()):
        out["top"] = songs[0] if songs else None
    return out


def suggestions(text):
    from app.core import ytmusic
    return _call(ytmusic.suggestions, text)


# ------------------------------------------------------------------ pages ---
def artist(browse_id):
    from app.core import ytmusic
    return _call(ytmusic.artist, browse_id)


def album(browse_id):
    from app.core import ytmusic
    return _call(ytmusic.album, browse_id)


# ------------------------------------------------------------------ an artist's whole catalogue -
# Every song on every album and single an artist's page lists, newest first, each with its year, its release and
# what can honestly be told about it: its language, and its kind (a film's soundtrack, an album, a single, a score).
# Compilations ("Best of", "Hits", "Most played...") are left out -- their songs are on the records already.
_CATALOG = {}
_COMPILATION = re.compile(r"\b(best\s+of|greatest|hits|collection|essentials|most\s+played|jukebox|top\s+\d+|"
                          r"melodies|evergreen|forever|anthology|gold(en)?|playlist|mashup|non[-\s]?stop)\b", re.I)
_FILM = re.compile(r"\b(soundtrack|motion\s+picture|ost|from\s+the\s+film|movie)\b", re.I)
_SCORE = re.compile(r"\b(background\s+score|bgm|score|theme|instrumental)\b", re.I)
_DEVOTIONAL = re.compile(r"\b(bhajan|devotional|aarti|mantra|bhakti|qawwali|sufi|naat|hymn|gospel|kirtan)\b", re.I)


def _year_of(rel):
    """A release's year, or "" when it hasn't one -- or gives one from the future (YouTube has a few)."""
    y = str(rel.get("year") or "")
    return y if y.isdigit() and 1900 <= int(y) <= int(time.strftime("%Y")) + 1 else ""


def _release_kind(rel, track=None):
    title = (rel.get("title") or "") + " " + ((track or {}).get("title") or "")
    if _SCORE.search(title):
        return "Score & themes"
    if _DEVOTIONAL.search(title):
        return "Devotional & Sufi"
    if _FILM.search(rel.get("title") or "") or "(from" in ((track or {}).get("title") or "").lower():
        return "Film soundtracks"
    return "Singles & EPs" if rel.get("_single") else "Albums"


def artist_catalog_period(browse_id, period):
    """The songs of one stretch of time -- a year ("2013") or a decade ("1990s") -- straight away, for when it's
    picked before the catalogue has got that far. Same shape as artist_catalog's songs."""
    def work():
        from concurrent.futures import ThreadPoolExecutor
        from app.core import ytmusic, upnext
        cat = _CATALOG.get(browse_id)
        if not cat:
            json.loads(artist_catalog(browse_id, 0, 8))          # makes the catalogue's list of releases
            cat = _CATALOG[browse_id]
        cat["older"].wait(25)
        p = str(period or "")

        def inside(rel):
            y = _year_of(rel)
            if not y:
                return False
            return (str(int(y) // 10 * 10) + "s") == p if p.endswith("s") else y == p
        rels = [r for r in cat["releases"] if inside(r)]
        cached = cat.setdefault("period_pages", {})
        if p in cached:
            return cached[p]

        def tracks_of(rel):
            try:
                return rel, ytmusic.album(rel["browse_id"]).get("tracks") or []
            except Exception:   # noqa: BLE001
                return rel, []
        with ThreadPoolExecutor(max_workers=4) as pool:
            got = list(pool.map(tracks_of, rels))
        out, seen = [], set()
        for rel, tracks in got:
            rel_lang = _release_language(rel, tracks)
            for t in tracks:
                lang = upnext.features(t, None).get("lang") or rel_lang
                k = upnext.song_key(t)
                if not k or (k, lang) in seen:
                    continue
                seen.add((k, lang))
                out.append(dict(t, year=_year_of(rel), album=rel.get("title") or t.get("album") or "",
                                album_browse=rel.get("browse_id"), artwork=t.get("artwork") or rel.get("artwork"),
                                lang=lang or "", lang_name=_LANG_WORDS.get(lang or "", "Other"), kind_of=_release_kind(rel, t)))
        cached[p] = out
        return out
    return _call(work)


_ENGLISH = frozenset("""the you i me my your we our love it is in on of to and a be don't dont can't never all
    what when how night time heart baby girl boy world life day way one feel like know just get got go back home
    out up down no yes this that she he her his they them with for from so oh not are was were will""".split())

_LANG_TAG = {"hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml", "kannada": "kn", "marathi": "mr",
             "punjabi": "pa", "bengali": "bn", "gujarati": "gu", "english": "en", "urdu": "hi"}


def _by_artist(album, key, browse_id):
    """A release found by searching is this artist's own: its artist link is theirs; with no link, one of its credited
    names is theirs exactly ("Lisa" is not "Lisa Stansfield", and "LISA" isn't in "Mona Lisa")."""
    link = album.get("artist_browse") or ""
    if link:
        return link == browse_id
    names = [re.sub(r"[^a-z0-9]", "", n.lower()) for n in re.split(r",|&| x | feat\.? | ft\.? ", album.get("artist") or "")]
    return bool(key) and key in names


_LANG_IN_TITLE = re.compile(r"\b(hindi|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi)\b", re.I)


def _film_name(title):
    """A release made in several languages, by its film's own name: 'Ramayana' from '"Namit Malhotra's Ramayana"
    (Malayalam)' or 'Rama Theme (From "Ramayana")'. Empty for anything else."""
    m = re.search(r'\(from\s+["\u201c]?([^")\u201d]+)', title, re.I)
    if m:
        return m.group(1).strip()
    if not _LANG_IN_TITLE.search(title):
        return ""
    base = re.sub(r"\s*(\(|\[|-\s).*$", "", title).strip(" \"'\u201c\u201d")
    return re.sub(r"^.*['\u2019]s\s+", "", base).strip()        # "Namit Malhotra's Ramayana" -> "Ramayana"


def _release_language(rel, tracks):
    """A release's language: named in its title ("Peddi (Telugu)"), else the one most of its songs are in."""
    from app.core import upnext
    words = re.findall(r"[a-z]+", ((rel.get("title") or "") + " " + " ".join(t.get("title") or "" for t in tracks)).lower())
    for w in words:
        if w in _LANG_TAG:
            return _LANG_TAG[w]
    votes = {}
    for t in tracks:
        lang = upnext.features(t, None).get("lang")
        if lang:
            votes[lang] = votes.get(lang, 0) + 1
    if votes:
        return max(votes, key=votes.get)
    # English: most of its song titles carry everyday English words (not the film's name, not one stray word)
    plain = [set(re.findall(r"[a-z']+", (t.get("title") or "").lower())) for t in tracks]
    english = sum(1 for ws in plain if ws & _ENGLISH)
    return "en" if tracks and english >= max(2, (len(tracks) + 2) // 3) else None


def artist_catalog(browse_id, start=0, count=8):
    """Songs from releases [start, start+count) of the artist's catalogue (newest first): {"tracks", "next" (the
    next start, or null when done), "total" (releases), "first_year", "last_year"}. Each song once, wherever it
    first appeared. Kept six hours."""
    def work():
        from concurrent.futures import ThreadPoolExecutor
        from app.core import ytmusic, upnext
        cat = _CATALOG.get(browse_id)
        if not cat or time.time() - cat["at"] > 6 * 3600:
            r = ytmusic.artist_releases(browse_id)
            rels = [dict(x, _single=False) for x in r["albums"]] + [dict(x, _single=True) for x in r["singles"]]
            rels = [x for x in rels if not _COMPILATION.search(x.get("title") or "")]
            # newest first; a release with no year after the dated ones
            rels.sort(key=lambda x: -int(x.get("year") or 0) if str(x.get("year") or "").isdigit() else 1)
            cat = _CATALOG[browse_id] = {"at": time.time(), "releases": rels, "seen": set(), "pages": {},
                                         "older": threading.Event()}
            name = (r.get("name") or "").strip()

            def older():
                # the page lists only the latest hundred of each: the search for the artist's albums goes back further
                try:
                    have = set(x.get("browse_id") for x in rels)
                    key = re.sub(r"[^a-z0-9]", "", name.lower())
                    found = []
                    # several narrower searches side by side (one long one reads its pages one after another)
                    def look(q):
                        try:
                            return ytmusic.search_albums(q, pages=4)
                        except Exception:   # noqa: BLE001
                            return []
                    qs = [name, name + " album", name + " soundtrack", name + " 90s", name + " 2000s",
                          name + " hindi", name + " tamil", name + " telugu"] if key else []
                    from concurrent.futures import ThreadPoolExecutor as _P
                    with _P(max_workers=4) as pool:
                        everything = [a for got in pool.map(look, qs) for a in got]
                    for a in everything:
                        if a.get("browse_id") in have or not _by_artist(a, key, browse_id) or _COMPILATION.search(a.get("title") or ""):
                            continue
                        have.add(a.get("browse_id"))
                        found.append(dict(a, _single=(a.get("type") or "").lower() in ("single", "ep")))
                    # a film out in several languages: each language's release, searched for by the film's name
                    # (the page and the search above often carry only one or two of them)
                    bases = []
                    for rel in rels[:60] + found:
                        base = _film_name(rel.get("title") or "")
                        if base and base.lower() not in [x.lower() for x in bases]:
                            bases.append(base)
                    def sib(base):
                        try:
                            return ytmusic.search_albums(base + " " + name, pages=2)
                        except Exception:   # noqa: BLE001
                            return []
                    from concurrent.futures import ThreadPoolExecutor as _Pool
                    with _Pool(max_workers=4) as pool:
                        sibs = list(pool.map(sib, bases[:12]))
                    for got in sibs:
                        for a in got:
                            if a.get("browse_id") in have or not _by_artist(a, key, browse_id) or _COMPILATION.search(a.get("title") or ""):
                                continue
                            have.add(a.get("browse_id"))
                            found.append(dict(a, _single=(a.get("type") or "").lower() in ("single", "ep")))
                    found.sort(key=lambda x: -int(x.get("year") or 0) if str(x.get("year") or "").isdigit() else 1)
                    cat["releases"].extend(found)
                finally:
                    cat["older"].set()
            cat["older_start"] = older
        i0 = int(start or 0)
        if i0 + int(count or 8) >= len(cat["releases"]):
            cat["older"].wait(25)                       # the older releases still being found: they come next
        if i0 in cat["pages"]:
            page = cat["pages"][i0]
        else:
            if i0 == 0:
                cat["seen"] = set()
            chunk = cat["releases"][i0:i0 + int(count or 8)]

            def tracks_of(rel):
                try:
                    return rel, ytmusic.album(rel["browse_id"]).get("tracks") or []
                except Exception:   # noqa: BLE001 -- one release missing leaves the rest
                    return rel, []
            with ThreadPoolExecutor(max_workers=4) as pool:
                got = list(pool.map(tracks_of, chunk))
            page = []
            for rel, tracks in got:
                rel_lang = _release_language(rel, tracks)
                for t in tracks:
                    lang = upnext.features(t, None).get("lang") or rel_lang
                    # once per language: a film's Tamil and Telugu songs share their titles, and both belong here
                    k = upnext.song_key(t)
                    if not k or (k, lang) in cat["seen"] or t.get("id") in cat["seen"]:
                        continue
                    cat["seen"].add((k, lang))
                    cat["seen"].add(t.get("id"))
                    page.append(dict(t, year=_year_of(rel) or _year_of(t), album=rel.get("title") or t.get("album") or "",
                                     album_browse=rel.get("browse_id"), artwork=t.get("artwork") or rel.get("artwork"),
                                     lang=lang or "", lang_name=_LANG_WORDS.get(lang or "", "Other"),
                                     kind_of=_release_kind(rel, t)))
            cat["pages"][i0] = page
            go = cat.pop("older_start", None)
            if go:
                threading.Thread(target=go, daemon=True, name="catalog-older").start()
        years = [int(_year_of(x)) for x in cat["releases"] if _year_of(x)]
        nxt = i0 + int(count or 8)
        return {"tracks": page, "next": nxt if nxt < len(cat["releases"]) else None, "total": len(cat["releases"]),
                "first_year": min(years) if years else None, "last_year": max(years) if years else None,
                "years": sorted(set(years), reverse=True), "complete": cat["older"].is_set()}
    return _call(work)


def album_tracks(item):
    from app.core import music_sources
    return _call(music_sources.album_tracks, _j(item))


def playlist(browse_id):
    from app.core import ytmusic
    return _call(ytmusic.playlist, browse_id)


def radio(video_id):
    from app.core import ytmusic
    return _call(ytmusic.radio, video_id)


EXPLORE_CHIPS = ("All", "Bollywood", "Indian pop", "Indian indie", "Punjabi", "Desi hip-hop", "Romance", "Sad",
                 "Party", "Chill", "Feel good", "Workout", "Focus", "Devotional", "Ghazal/sufi",
                 "Hindustani classical", "Tamil", "Telugu", "Marathi", "Bengali", "Pop", "Hip-hop", "K-Pop",
                 "Indie & alternative", "Dance & electronic", "Rock", "R&B & soul", "Jazz", "Latin")
CHIP_CATEGORY = {"Bollywood": "Hindi"}

_TOP = {}
TOP_KEEP_S = 3600


def _short_count(n):
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return ("%.1f" % (n / div)).rstrip("0").rstrip(".") + unit
    return str(int(n))


def top_songs(country="IN"):
    """The country's top songs this week, from YouTube Charts (charts.youtube.com) -- the chart YouTube
    itself publishes every week, by this week's views. Each song is the record's own audio upload (its
    album cover, not a video's frame), with its place, last week's, weeks on the chart and the week's
    views. {"tracks", "week_end": "2026-10-08", "title"}; kept an hour."""
    import urllib.request
    from app.core import ytmusic
    cc = (country or "IN").lower()
    if cc in ("zz", "global"):
        cc = "global"                       # the world's chart
    hit = _TOP.get(cc)
    if hit and time.time() - hit[0] < TOP_KEEP_S:
        return hit[1]
    body = {"context": {"client": {"clientName": "WEB_MUSIC_ANALYTICS", "clientVersion": "2.0", "hl": "en",
                                   "gl": "US" if cc == "global" else cc.upper()}},
            "browseId": "FEmusic_analytics_charts_home",
            "query": "perspective=CHART_DETAILS&chart_params_country_code=%s&chart_params_chart_type=TRACKS"
                     "&chart_params_period_type=WEEKLY" % cc}
    req = urllib.request.Request("https://charts.youtube.com/youtubei/v1/browse?alt=json", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "https://charts.youtube.com",
                                          "Referer": "https://charts.youtube.com/", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    views = next(iter(ytmusic._walk(data, "trackViews")), []) or []
    ends = ytmusic._walk(data, "endDate")
    tracks = []
    for v in views:
        vid = v.get("atvExternalVideoId") or v.get("encryptedVideoId")
        if not vid or not v.get("name"):
            continue
        meta = v.get("chartEntryMetadata") or {}
        rank, prev, weeks = meta.get("currentPosition"), meta.get("previousPosition"), meta.get("periodsOnChart") or 0
        week = int(v.get("viewCount") or 0)
        move = ("NEW" if not prev or weeks <= 1 else "▲%d" % (prev - rank) if prev > rank
                else "▼%d" % (rank - prev) if prev < rank else "=")
        line = " · ".join(x for x in ("%s views this week" % _short_count(week) if week else "", move,
                                      "%d weeks" % weeks if weeks > 1 else "") if x)
        thumbs = (v.get("thumbnail") or {}).get("thumbnails") or []
        art = thumbs[-1]["url"] if thumbs else None
        rd = v.get("releaseDate") or {}
        tracks.append({"kind": "track", "id": "yt:" + vid, "title": v["name"].strip(),
                       "artist": ", ".join(a.get("name", "") for a in v.get("artists") or [] if a.get("name")),
                       "artwork": ytmusic.sized(art, 544) if art else None, "source": "youtube",
                       "page_url": "https://music.youtube.com/watch?v=%s" % vid, "ext": "m4a",
                       "year": str(rd["year"]) if rd.get("year") else "", "chart_rank": rank,
                       "chart_line": line, "week_views": week})
    out = {"tracks": tracks, "week_end": ends[0] if ends else "", "title": "Top songs this week"}
    if tracks:
        _TOP[cc] = (time.time(), out)
    return out


def top_today(country="IN"):
    """The country's most-watched music videos yesterday (YouTube Charts' daily chart -- the weekly songs
    chart runs about a week behind; this one is a day behind). Each wears its record's cover where one
    is found. {"tracks", "day", "title"}; kept half an hour."""
    import urllib.request
    from app.core import ytmusic
    cc = (country or "IN").lower()
    if cc in ("zz", "global"):
        cc = "global"
    key = "day:" + cc
    hit = _TOP.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    body = {"context": {"client": {"clientName": "WEB_MUSIC_ANALYTICS", "clientVersion": "2.0", "hl": "en",
                                   "gl": "US" if cc == "global" else cc.upper()}},
            "browseId": "FEmusic_analytics_charts_home",
            "query": "perspective=CHART_DETAILS&chart_params_country_code=%s&chart_params_chart_type=VIDEOS"
                     "&chart_params_period_type=DAILY" % cc}
    req = urllib.request.Request("https://charts.youtube.com/youtubei/v1/browse?alt=json", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "https://charts.youtube.com",
                                          "Referer": "https://charts.youtube.com/", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    views = next(iter(ytmusic._walk(data, "videoViews")), []) or []
    ends = ytmusic._walk(data, "endDate")
    tracks = []
    for v in views:
        vid = v.get("id")
        if not vid or not v.get("title") or v.get("isAvailable") is False:
            continue
        meta = v.get("chartEntryMetadata") or {}
        rank, prev, days = meta.get("currentPosition"), meta.get("previousPosition"), meta.get("periodsOnChart") or 0
        n = int(v.get("viewCount") or 0)
        move = ("NEW" if not prev or days <= 1 else "▲%d" % (prev - rank) if prev > rank
                else "▼%d" % (rank - prev) if prev < rank else "=")
        line = " · ".join(x for x in ("%s views in a day" % _short_count(n) if n else "", move,
                                            "%d days" % days if days > 1 else "") if x)
        artists = [a.get("name", "") for a in v.get("artists") or [] if a.get("name")]
        rd = v.get("releaseDate") or {}
        tracks.append({"kind": "track", "id": "yt:" + vid, "title": v["title"].strip(),
                       "artist": ", ".join(artists) or v.get("channelName") or "",
                       "artwork": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid, "source": "youtube",
                       "duration": int(v.get("videoDuration") or 0),
                       "page_url": "https://music.youtube.com/watch?v=%s" % vid, "ext": "m4a",
                       "year": str(rd["year"]) if rd.get("year") else "", "chart_rank": rank,
                       "chart_line": line})
    # the records' own covers for the first ones (the rest keep the video's picture until they're found)
    tracks = with_covers(tracks[:40], budget=5.0) + tracks[40:]
    # a video upload (a film's channel, "Video | ..."), unless it's matched to the song's own record: left out --
    # a chart of songs, not of clips
    from app.core import music_sources as _ms
    tracks = [dict(t, title=clean_title(t["title"])) for t in tracks if _ms.is_cover(t.get("artwork")) or not looks_like_upload(t)]
    out = {"tracks": tracks, "day": ends[0] if ends else "", "title": "Top today"}
    if tracks:
        _TOP[key] = (time.time(), out)
    return out


_MATCHES = None


def _matches():
    global _MATCHES
    if _MATCHES is None:
        try:
            with open(os.path.join(os.environ.get("AWESOME_MUSIC_HOME") or ".", "chart_matches-2.json"), encoding="utf-8") as f:
                _MATCHES = json.load(f)
        except Exception:
            _MATCHES = {}
    return _MATCHES


def _keep_matches():
    m = _matches()
    if len(m) > 3000:
        for k in list(m)[:1000]:
            m.pop(k, None)
    try:
        path = os.path.join(os.environ.get("AWESOME_MUSIC_HOME") or ".", "chart_matches-2.json")
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(m, f)
        os.replace(path + ".part", path)
    except Exception:
        pass


def _match_song(title, artist, art=None, extra=None):
    key = (title + "|" + artist).lower()
    known = _matches().get(key)
    # "not on YouTube Music" is believed for three days, then asked again (a new song gets there late)
    if known and not known.get("none"):
        out = dict(known)
        if art:
            out["artwork"] = art
        out.update(extra or {})
        return out
    if known and time.time() - known["none"] < 3 * 86400:
        return None
    got = _match_song_now(title, artist)
    if got == "later":
        return None                     # the search itself failed (no network, a refusal): asked again next time
    _matches()[key] = got or {"none": time.time()}
    if got is None:
        return None
    out = dict(got)
    if art:
        out["artwork"] = art
    out.update(extra or {})
    return out


def _match_song_now(title, artist):
    """A chart's song as one the player can play: YouTube Music's own recording of it (searched by name and
    singer, kept only when the name matches), wearing the chart's cover. None when there's no telling."""
    from app.core import music_sources as ms, ytmusic
    want = ms._plain_title(title)
    first = re.split(r",|&| x | feat", artist or "", flags=re.I)[0].strip()
    try:
        songs = ytmusic.search(("%s %s" % (re.sub(r"[(\[].*?[)\]]", "", title), first)).strip())["songs"][:6]
    except Exception:   # noqa: BLE001 -- not "no such song": remembered as nothing only when the search answered
        return "later"
    for s in songs:
        have = ms._plain_title(s.get("title"))
        if want and have and (want == have or want in have or have in want) and _same_take(title, s):
            return dict(s)
    return None


def _chart_from(rows, key):
    """[(title, artist, art, extra)] matched to playable songs, side by side; kept an hour under `key`."""
    from concurrent.futures import ThreadPoolExecutor
    hit = _TOP.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    # four at a time: more, and YouTube starts asking this phone whether it's a bot
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = list(pool.map(lambda r: _match_song(*r), rows))
    _keep_matches()
    seen, tracks = set(), []
    for t in got:
        if t and t["id"] not in seen:
            seen.add(t["id"])
            tracks.append(t)
    if tracks:
        _TOP[key] = (time.time(), tracks)
    return tracks


def top_apple(country="IN", n=30):
    """Apple Music's Top Songs for a country -- its own feed, remade every day. Global has none: the US list."""
    import urllib.request
    cc = (country or "IN").lower()
    if cc in ("zz", "global"):
        cc = "us"
    url = "https://rss.marketingtools.apple.com/api/v2/%s/music/most-played/%d/songs.json" % (cc, n)
    for attempt in range(3):            # Apple's feed answers 502 now and then; a moment later it's there
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                feed = json.load(r)["feed"]
            break
        except Exception:   # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    rows = []
    for i, x in enumerate(feed.get("results") or []):
        art = (x.get("artworkUrl100") or "").replace("100x100bb", "600x600bb") or None
        rows.append((x.get("name", ""), x.get("artistName", ""), art,
                     {"chart_rank": i + 1, "chart_line": "#%d on Apple Music today" % (i + 1)}))
    return {"tracks": _chart_from(rows, "apple:%s:%d" % (cc, n)), "day": (feed.get("updated") or "")[:16]}


# where Spotify's own chart comes from first: the app's server (CheckLeaked's Spotify API, its key kept there);
# set by home_charts from the app. Empty, or the server without a key: kworb.net instead.
_API_BASE = ""


def _spotify_api(page, n):
    """Spotify's own daily chart through the app's server: (rows, day) or None."""
    import urllib.request
    if not _API_BASE:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(_API_BASE.rstrip("/") + "/v1/charts/spotify?country=" + page,
                                                           headers={"User-Agent": "AwesomeMusic"}), timeout=8) as r:
            data = json.load(r)
    except Exception:   # noqa: BLE001 -- no key there, or down: kworb then
        return None
    rows = []
    for e in (data.get("tracks") or [])[:n]:
        rank, prev, days, streams = e.get("rank") or 0, e.get("prev") or 0, e.get("days") or 0, e.get("streams") or 0
        move = ("NEW" if e.get("status") == "NEW_ENTRY" or not prev else "▲%d" % (prev - rank) if prev > rank
                else "▼%d" % (rank - prev) if prev < rank else "")
        line = " · ".join(x for x in ("#%d on Spotify" % rank, "%s streams" % _short_count(streams) if streams else "",
                                            move, "%d days" % days if days > 1 else "") if x)
        art = e.get("art")
        if art:
            art = art.replace("ab67616d00001e02", "ab67616d0000b273")        # the 640 px cover, not the 300
        rows.append((e.get("title", ""), ", ".join(e.get("artists") or []), art, {"chart_rank": rank, "chart_line": line}))
    return (rows, data.get("chart_date") or "") if rows else None


def top_spotify(country="IN", n=30):
    """Spotify's daily top songs for a country (or the world): Spotify's own chart through the app's server where
    it can, else as kworb.net republishes it each day -- name, singer, days on the chart, the day's streams."""
    import html as _html
    import urllib.request
    cc = (country or "IN").lower()
    page = "global" if cc in ("zz", "global") else cc
    hit = _TOP.get("spotify-rows:%s:%d" % (page, n))
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    got = _spotify_api(page, n)
    if got:
        out = {"tracks": _chart_from(got[0], "spotify:%s:%d" % (page, n)), "day": got[1], "via": "spotify"}
        if out["tracks"]:
            _TOP["spotify-rows:%s:%d" % (page, n)] = (time.time(), out)
        return out
    with urllib.request.urlopen(urllib.request.Request("https://kworb.net/spotify/country/%s_daily.html" % page,
                                                       headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
        s = r.read().decode("utf-8", "replace")
    day = re.search(r"(\d{4}/\d{2}/\d{2})", s)
    rows = []
    for m in re.finditer(r'<tr><td class="np">(\d+)</td>\s*<td class="np">([^<]*)</td>\s*<td class="text mp"><div>(.*?)</div></td>\s*<td>(\d+)</td>(.*?)</tr>', s, re.S):
        rank, move, cell, days, rest = m.groups()
        text = _html.unescape(re.sub(r"<[^>]+>", "", cell))
        if " - " not in text:
            continue
        artist, title = text.split(" - ", 1)
        title = re.sub(r"\s*\(w/.*?\)\s*$", "", title).strip()
        nums = [int(x.replace(",", "")) for x in re.findall(r"<td>([\d,]+)</td>", rest)]
        streams = nums[-1] if nums else 0
        line = " · ".join(x for x in ("#%s on Spotify today" % rank,
                                            "%s streams in all" % _short_count(streams) if streams else "",
                                            "NEW" if move.strip().upper() == "NEW" else "",
                                            "%s days" % days if int(days) > 1 else "") if x)
        rows.append((title, artist, None, {"chart_rank": int(rank), "chart_line": line}))
        if len(rows) >= n:
            break
    out = {"tracks": _chart_from(rows, "spotify:%s:%d" % (page, n)), "day": day.group(1).replace("/", "-") if day else "", "via": "kworb"}
    if out["tracks"]:
        _TOP["spotify-rows:%s:%d" % (page, n)] = (time.time(), out)
    return out


def fresh_charts(region):
    """Apple Music's top songs today and Spotify's daily top songs -- the newest charts there are, matched to
    playable songs. Asked for apart from the rest of Explore (the first time can take a little while)."""
    def work():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as both:
            fa, fs = both.submit(top_apple, region or "IN"), both.submit(top_spotify, region or "IN")
            out = {}
            try:
                a = fa.result()
                if a.get("tracks"):
                    out["apple"], out["apple_title"] = a["tracks"], "Top songs today · Apple Music"
            except Exception:   # noqa: BLE001
                pass
            try:
                sp = fs.result()
                if sp.get("tracks"):
                    d = _day_words(sp.get("day"))
                    out["spotify"], out["spotify_title"] = sp["tracks"], "Spotify Top 30" + (" · " + d if d else " today")
            except Exception:   # noqa: BLE001
                pass
            return out
    return _call(work)


_REGION_NAMES = {"in": "India", "us": "USA", "gb": "UK", "ca": "Canada", "au": "Australia", "pk": "Pakistan",
                 "bd": "Bangladesh", "np": "Nepal", "lk": "Sri Lanka", "ae": "UAE", "sg": "Singapore", "my": "Malaysia",
                 "id": "Indonesia", "ph": "Philippines", "jp": "Japan", "kr": "Korea", "de": "Germany", "fr": "France",
                 "es": "Spain", "it": "Italy", "br": "Brazil", "mx": "Mexico", "global": "Global", "zz": "Global"}
_LANG_WORDS = {"hi": "Hindi", "en": "English", "pa": "Punjabi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
               "mr": "Marathi", "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati", "ko": "Korean", "es": "Spanish",
               "ja": "Japanese", "ar": "Arabic", "fr": "French"}
_home_lock = threading.Lock()


def _home_path():
    return os.path.join(os.environ.get("AWESOME_MUSIC_HOME") or ".", "home_charts.json")


def home_charts(region, langs, api_base="", fresh=False):
    """Home's charts, each as a card that opens its songs: Spotify's daily Top 50 for the country, a chart for each
    language picked (today's charts, only the songs in it), Apple Music's top songs today, what's trending on
    YouTube, and the world's Top 50 -- plus new releases. Kept on the phone: asked for without `fresh`, the last
    ones come back at once (with their "age" in seconds) and the app asks again, fresh, in the background."""
    global _API_BASE
    if api_base:
        _API_BASE = api_base
        os.environ["AWESOME_MUSIC_API"] = api_base
    langs = [x for x in (_j(langs) if isinstance(langs, str) else langs or []) if x][:3]
    cc = (region or "IN").lower()
    key = cc + "|" + ",".join(langs)

    def work():
        if not fresh:
            try:
                with open(_home_path(), encoding="utf-8") as f:
                    kept = json.load(f)
                if kept.get("key") == key and kept.get("charts"):
                    return dict(kept, age=max(0, time.time() - kept.get("at", 0)))
            except Exception:   # noqa: BLE001 -- none yet: made now
                pass
        with _home_lock:
            out = _make_home_charts(cc, langs)
            out.update(key=key, at=time.time())
            if out["charts"]:
                try:
                    with open(_home_path() + ".part", "w", encoding="utf-8") as f:
                        json.dump(out, f, ensure_ascii=False)
                    os.replace(_home_path() + ".part", _home_path())
                except Exception:   # noqa: BLE001
                    pass
            return dict(out, age=0)
    return _call(work)


# a language with a country of its own: that country's Spotify chart is the language's chart
_LANG_COUNTRY = {"es": ("es", "Spain"), "ko": ("kr", "Korea"), "ja": ("jp", "Japan"), "fr": ("fr", "France"),
                 "ar": ("eg", "Egypt")}
_INDIAN = ("hi", "pa", "ta", "te", "bn", "mr", "kn", "ml", "gu")
_LANG_HUE = {"hi": 0.02, "pa": 0.10, "ta": 0.58, "te": 0.46, "bn": 0.30, "mr": 0.86, "kn": 0.16, "ml": 0.40,
             "gu": 0.92, "es": 0.05, "ko": 0.75, "ja": 0.97, "fr": 0.62, "ar": 0.12}


def _lang_chart(lang, cc, pool):
    """The chart for one language: for a language of India, today's Indian charts kept to its songs (YouTube's
    weekly chart for it, or its newest songs, when the daily charts have too few); for Spanish, Korean, Japanese,
    French or Arabic, the Spotify chart of the country it's spoken in. English: none (the world's chart is it)."""
    from app.core import ytmusic
    word = _LANG_WORDS.get(lang, lang)
    hue = _LANG_HUE.get(lang, 0.70)
    if lang == "en":
        return None
    home = _LANG_COUNTRY.get(lang)
    if home:
        if home[0] == cc:
            return None                        # the country's own Top 50 is it already
        got = top_spotify(home[0], 50)
        if len(got.get("tracks") or []) >= 6:
            d = _day_words(got.get("day"))
            return _chart_card("lang:" + lang, "Top " + word, "Chart",
                               "Spotify %s" % home[1] + (" \u00b7 " + d if d else " today"), got["tracks"], hue)
        return None
    if lang not in _INDIAN:
        return None
    src = pool
    if cc != "in":
        # not in India: India's own charts, for its languages
        src = []
        for fn in (lambda: top_spotify("in", 50), lambda: top_apple("in", 50), lambda: top_today("in")):
            try:
                src += fn().get("tracks") or []
            except Exception:   # noqa: BLE001
                pass
    hot = _hot_in(lang, "IN", src)
    if len(hot) >= 8:
        return _chart_card("lang:" + lang, "Top " + word, "Chart", "The %s songs on today's charts" % word, hot, hue)
    # too few on the daily charts: YouTube's weekly chart for the language, where it has one
    try:
        for sh in ytmusic.charts("IN"):
            for x in sh["items"]:
                if x.get("kind") == "ytplaylist" and (x.get("title") or "").lower().endswith(" " + word.lower()):
                    tracks = with_covers((ytmusic.playlist(x["browse_id"]).get("tracks") or [])[:40], budget=4.0)
                    if len(tracks) >= 6:
                        return _chart_card("lang:" + lang, "Top " + word, "Chart", "YouTube's %s chart this week" % word,
                                           tracks, hue)
    except Exception:   # noqa: BLE001
        pass
    # and else the newest songs in it
    try:
        new = ytmusic.search("new %s songs %s" % (word, time.strftime("%Y")))["songs"][:25]
    except Exception:   # noqa: BLE001
        new = []
    if len(new) >= 6:
        return _chart_card("lang:" + lang, "New in " + word, "Chart", "The newest %s songs" % word, new, hue)
    return None


def _chart_card(cid, title, over, sub, tracks, hue):
    lead = tracks[0] if tracks else {}
    return {"kind": "mix", "id": "chart:" + cid, "title": title, "over": over, "subtitle": sub, "chart": True,
            "hue": hue, "artwork": lead.get("artwork"), "lead": "%s · %s" % (lead.get("title", ""), (lead.get("artist") or "").split(",")[0]),
            "tracks": tracks}


def _make_home_charts(cc, langs):
    from concurrent.futures import ThreadPoolExecutor
    from app.core import ytmusic
    place = _REGION_NAMES.get(cc, cc.upper())
    world = cc in ("zz", "global")

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception:   # noqa: BLE001 -- that chart is left out
            return {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_sp = pool.submit(safe, top_spotify, cc, 50)
        f_glob = pool.submit(safe, top_spotify, "global", 50) if not world else None
        f_apple = pool.submit(safe, top_apple, cc, 50)
        f_yt = pool.submit(safe, top_today, cc)
        f_new = pool.submit(safe, ytmusic.new_releases)
        sp, apple, yt = f_sp.result(), f_apple.result(), f_yt.result()
        glob = f_glob.result() if f_glob else {}
        new = f_new.result() or []
    charts = []
    # a chart for every language picked, first, in the order picked
    pool = (sp.get("tracks") or []) + (apple.get("tracks") or []) + (yt.get("tracks") or [])
    with ThreadPoolExecutor(max_workers=3) as ex:
        made = list(ex.map(lambda lg: safe(_lang_chart, lg, cc, pool) or None, langs))
    charts += [c for c in made if c]
    if sp.get("tracks"):
        d = _day_words(sp.get("day"))
        charts.append(_chart_card(
            "spotify:" + cc, "Top 50 · " + place, "Chart", "Spotify's most-streamed" + (" · " + d if d else " today"),
            sp["tracks"], 0.38))
    if apple.get("tracks"):
        charts.append(_chart_card("apple:" + cc, "Today's Top Songs", "Chart", "Apple Music · " + place, apple["tracks"], 0.95))
    if yt.get("tracks"):
        d = _day_words(yt.get("day"))
        charts.append(_chart_card("youtube:" + cc, "Trending on YouTube", "Chart",
                                  "Most watched yesterday" + (" · " + d if d else ""), yt["tracks"], 0.0))
    if glob.get("tracks"):
        charts.append(_chart_card("spotify:global", "Top 50 · Global", "Chart", "The world's most-streamed today",
                                  glob["tracks"], 0.62))
    return {"charts": charts, "new": new[:20], "place": place,
            "spotify_via": sp.get("via", "")}


def _day_words(day):
    try:
        return time.strftime("%d %b", time.strptime(day, "%Y-%m-%d")).lstrip("0")
    except (ValueError, TypeError):
        return ""


def explore(chip, region):
    """The explore page's data, as the desktop tab builds it (_build_explore)."""
    def work():
        global _moods
        from app.core import music_sources, ytmusic
        data = {}
        if chip in ("", "All"):
            shelves = ytmusic.charts("ZZ" if (region or "").upper() in ("ZZ", "GLOBAL") else (region or "IN"))
            lists = [x for sh in shelves for x in sh["items"] if x.get("kind") == "ytplaylist"]
            # the newest chart first (yesterday's), then the week's songs (YouTube publishes those a week late)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as both:
                fd, fw = both.submit(top_today, region or "IN"), both.submit(top_songs, region or "IN")

                def got(f):
                    try:
                        return f.result()
                    except Exception:   # noqa: BLE001 -- that chart is left out
                        return {}
                day, week = got(fd), got(fw)
            if day.get("tracks"):
                data["trending"] = day["tracks"]
                d = _day_words(day.get("day"))
                data["trending_title"] = "Top today" + (" · " + d if d else "")
            if week.get("tracks"):
                data["week"] = week["tracks"]
                d = _day_words(week.get("week_end"))
                data["week_title"] = "Top songs this week" + (" · to " + d if d else "")
                if not data.get("trending"):
                    data["trending"], data["trending_title"] = data.pop("week"), data.pop("week_title")
            if not data.get("trending") and lists:
                top = ytmusic.playlist(lists[0]["browse_id"])
                # play counts and the records' own covers, asked for side by side
                from concurrent.futures import ThreadPoolExecutor
                head = top["tracks"][:30]
                with ThreadPoolExecutor(max_workers=2) as both:
                    counted, covered = both.submit(with_plays, head), both.submit(with_covers, head)
                    counted, covered = counted.result(), covered.result()
                head = [dict(c, artwork=v.get("artwork")) for c, v in zip(counted, covered)]
                data["trending"] = head + top["tracks"][30:]
                data["trending_title"] = top.get("title") or lists[0]["title"]
            data["shelves"] = shelves
            try:
                data["new"] = ytmusic.new_releases()[:24]
            except Exception:   # noqa: BLE001 -- the charts alone will do
                data["new"] = []
        else:
            if _moods is None:
                _moods = ytmusic.moods()
            ep = _moods.get(CHIP_CATEGORY.get(chip, chip))
            if not ep:
                raise music_sources.MusicError("YouTube Music has no %s page here." % chip)
            data["shelves"] = ytmusic.category(ep)
            # a language's chip: what's hot in it right now, from today's charts, first
            lang = CHIP_LANG.get(chip)
            if lang:
                try:
                    hot = _hot_in(lang, region or "IN")
                    if hot:
                        data["trending"], data["trending_title"] = hot, "Hot right now · " + chip
                except Exception:   # noqa: BLE001 -- the chip's own lists still show
                    pass
        return data
    return _call(work)


CHIP_LANG = {"Bollywood": "hi", "Indian pop": "hi", "Punjabi": "pa", "Tamil": "ta", "Telugu": "te",
             "Marathi": "mr", "Bengali": "bn", "Desi hip-hop": "hi"}


def _hot_in(lang, region, pool=None):
    """Today's charts (Apple Music, Spotify, YouTube's daily), only the songs in `lang`, best placed first. Given
    the charts already fetched (`pool`), only those -- never an older list to make up the numbers."""
    from app.core import upnext
    given = pool is not None
    pool = list(pool or [])
    if not given:
        for fn in (lambda r: top_apple(r, 50), lambda r: top_spotify(r, 50), top_today):
            try:
                pool += fn(region).get("tracks") or []
            except Exception:   # noqa: BLE001
                pass
    mem = taste().memory
    out, seen = [], set()
    for t in sorted(pool, key=lambda t: t.get("chart_rank") or 99):
        k = upnext.song_key(t)
        if k in seen:
            continue
        if upnext.features(t, mem)["lang"] == lang:
            seen.add(k)
            out.append(t)
    if len(out) < 8 and not given:
        # titles in English letters rarely say their language: YouTube's own weekly chart for it instead
        from app.core import ytmusic
        name = {"pa": "Punjabi", "ta": "Tamil", "te": "Telugu", "hi": "Hindi", "mr": "Marathi", "bn": "Bengali"}.get(lang)
        for sh in ytmusic.charts(region or "IN"):
            for x in sh["items"]:
                if name and x.get("kind") == "ytplaylist" and name.lower() in (x.get("title") or "").lower():
                    tracks = ytmusic.playlist(x["browse_id"]).get("tracks") or []
                    return with_covers(tracks[:30], budget=5.0)
    return out[:30]


_plays = {}


def plays_of(video_id):
    """How many times a song has been played on YouTube (its view count), or None. Kept for the session."""
    if video_id in _plays:
        return _plays[video_id]
    from app.core import ytmusic
    try:
        got = ytmusic._post("player", {"videoId": video_id}, timeout=8, background=True)
        n = int((got.get("videoDetails") or {}).get("viewCount") or 0) or None
    except Exception:   # noqa: BLE001 -- the list shows without it
        n = None
    _plays[video_id] = n
    return n


def with_plays(tracks):
    """`tracks`, each with "plays" (its play count) where YouTube gives it -- asked for all at once."""
    from concurrent.futures import ThreadPoolExecutor
    ids = [t["id"].split(":", 1)[1] for t in tracks if str(t.get("id", "")).startswith("yt:")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = dict(zip(ids, pool.map(plays_of, ids)))
    out = []
    for t in tracks:
        n = counts.get(str(t.get("id", "")).split(":", 1)[-1])
        out.append(dict(t, plays=n) if n else t)
    return out


# ------------------------------------------------------------ real covers -
_COVER_POOL = None


def with_covers(tracks, budget=4.0):
    """`tracks` with the music videos among them wearing their records' own covers (a video comes with its
    frame for a picture) -- looked up all at once, the way the player looks up the one playing. Waits
    `budget` seconds at most: what's found by then is used, the rest is kept for next time."""
    global _COVER_POOL
    from concurrent.futures import ThreadPoolExecutor, wait
    from app.core import music_sources as ms
    videos = [t for t in tracks if str(t.get("id", "")).startswith("yt:") and not ms.is_cover(t.get("artwork"))]
    if not videos:
        return tracks
    known = _known_covers()
    ask = [t for t in videos if t["id"] not in known]
    if ask:
        if _COVER_POOL is None:
            _COVER_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="music-cover")
        wait([_COVER_POOL.submit(record_cover, t) for t in ask], timeout=budget)
        known = _known_covers()
    return [dict(t, artwork=known[t["id"]]) if known.get(t.get("id")) else t for t in tracks]


# ------------------------------------------------------------------ playing -
# The sound's quality, as the app's settings say for the network the phone is on (Apple Music's way: Wi-Fi and
# mobile data each have their own). "high": the best YouTube has (Opus ~160 kbps); "normal": about 128 kbps;
# "low": about 50-70 kbps, for a weak signal or a small data plan. A song keeps the quality it started at.
QUALITY_FORMATS = {
    "normal": "bestaudio[ext=m4a][abr<=135]/bestaudio[abr<=135][abr>=96]/bestaudio[abr<=135]/bestaudio",
    "low": "bestaudio[abr<=72][acodec=opus]/worstaudio[acodec=opus]/worstaudio[ext=m4a]/bestaudio",
}
_q_streams = {}                 # (video id, quality) -> (url, good until)
_q_lock = threading.Lock()


def _stream_at(t, quality):
    """A stream link for `t` at `quality` (the engine's own for "high")."""
    from app.core import music_sources as ms
    fmt = QUALITY_FORMATS.get(quality)
    if not fmt or t.get("source") != "youtube":
        return ms.resolve_stream(t)
    key = (ms._video_id(t), quality)
    with _q_lock:
        got = _q_streams.get(key)
    if got and got[1] > time.time():
        return got[0]
    from app.core import downloader
    # a song kept from before (the last session, a chart) may come without its link: made from its id
    page = t.get("page_url") or "https://music.youtube.com/watch?v=" + ms._video_id(t)
    opts = ms._anonymous(downloader, page)
    opts.update({"skip_download": True, "format": fmt})
    info = downloader._run(opts, page, lambda ydl: ydl.extract_info(page, download=False))
    url = info.get("url") or next((f["url"] for f in reversed(info.get("requested_formats") or [])), None)
    if not url:
        raise ms.MusicError("YouTube didn't give an audio stream for this song.")
    with _q_lock:
        _q_streams[key] = (url, ms._good_until(url))
        if len(_q_streams) > 300:
            for k in list(_q_streams)[:100]:
                del _q_streams[k]
    return url


def _linked(t):
    """A YouTube song with its watch link (songs kept from before -- recently played, a chart -- may lack it)."""
    if t and t.get("source") == "youtube" and not t.get("page_url") and str(t.get("id", "")).startswith("yt:"):
        t = dict(t, page_url="https://music.youtube.com/watch?v=" + t["id"][3:])
    return t


def resolve(track, quality="high"):
    """Where to play `track` from: {"path"} (fetched whole earlier), or
    {"url"} (a stream, at `quality`). Blocking for YouTube (about a second and a half)."""
    from app.core import music_sources as ms
    t = _linked(_j(track))

    def work():
        if t.get("source") == "local":
            return {"path": t["stream"]}
        if t.get("source") == "youtube":
            got = ms.cached(t)
            if got:
                return {"path": got}
        got = _stream_at(t, quality or "high")
        return {"path": got} if os.path.exists(got) else {"url": got}
    return _call(work)


def forget_stream(track):
    """A stream link that stopped working (expired, refused): asked for afresh next time."""
    from app.core import music_sources as ms
    t = _j(track)
    with ms._streams_lock:
        ms._streams.pop(ms._video_id(t), None)
    with _q_lock:
        for q in QUALITY_FORMATS:
            _q_streams.pop((ms._video_id(t), q), None)
    return _ok(True)


def cached(track):
    from app.core import music_sources as ms
    return _ok(ms.cached(_j(track)) or "")


def warm(tracks, quality="high"):
    """The stream links of `tracks` asked for ahead, in the background, so they play the moment they're tapped."""
    from app.core import music_sources as ms
    got = [_linked(t) for t in (_j(tracks) or [])]
    if not QUALITY_FORMATS.get(quality):
        ms.warm(got)
        return _ok(True)
    for t in got:
        if not t or t.get("source") != "youtube" or t.get("kind", "track") != "track" or ms.cached(t):
            continue
        ms._warm_pool.submit(lambda t=t: _call(_stream_at, t, quality))
    return _ok(True)


def fetch_song(track):
    """The song's audio, fetched whole into the cache (a stream that won't
    open, or broke off): its path."""
    from app.core import music_sources as ms
    return _call(ms.fetch_song, _linked(_j(track)))


def original_art(track):
    return _call(record_cover, _j(track))


_TAGGED = {}


def _video_words(video_id):
    """A video's tags and channel, lower case, as one text -- where an official video names its singers
    and its film when its own "artist" is only the lyricist or the label."""
    from app.core import ytmusic
    if video_id not in _TAGGED:
        try:
            p = ytmusic._post("player", {"videoId": video_id})
            mf = (p.get("microformat") or {}).get("microformatDataRenderer") or {}
            vd = p.get("videoDetails") or {}
            _TAGGED[video_id] = " | ".join((mf.get("tags") or []) + [vd.get("author") or "", mf.get("description") or ""]).lower()
        except Exception:
            return ""
    return _TAGGED[video_id]


_COVERS_LOCK = threading.Lock()
_COVERS = None


def _covers_path():
    # covers-2: found again with the stricter match (a remix's or a fan's picture is never a record's cover); the
    # old list is dropped
    home = os.environ.get("AWESOME_MUSIC_HOME") or "."
    try:
        os.remove(os.path.join(home, "covers.json"))
    except OSError:
        pass
    return os.path.join(home, "covers-2.json")


def _known_covers():
    """Covers found for music videos before: {song id: url, or "" for none to be had}, kept on disk."""
    global _COVERS
    with _COVERS_LOCK:
        if _COVERS is None:
            try:
                with open(_covers_path(), encoding="utf-8") as f:
                    _COVERS = json.load(f)
            except Exception:
                _COVERS = {}
        return dict(_COVERS)


def _keep_cover(song_id, url):
    _known_covers()
    with _COVERS_LOCK:
        _COVERS[song_id] = url or ""
        if len(_COVERS) > 4000:
            for k in list(_COVERS)[:1000]:
                _COVERS.pop(k, None)
        try:
            tmp = _covers_path() + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_COVERS, f)
            os.replace(tmp, _covers_path())
        except Exception:
            pass


# what a video's title wears around the song's name
_VIDEO_TAGS = re.compile(r"#\w+|\b(?:official|full|lyrical|lyric|lyrics|music|video|song|audio|hd|4k|new|latest)\b", re.I)


def _song_name(title, artists):
    """A music video's title down to the song's name: "#Video | Maai ( माई ) - Power Star ... | ..." -> "Maai",
    "295 (MUSIC VIDEO) SIDHU MOOSE WALA" -> "295"."""
    t = re.sub(r"[(\[].*?[)\]]", " ", title or "")
    parts = [p for p in re.split(r"\s*[|｜]\s*|\s+-\s+|\s+–\s+", t) if _VIDEO_TAGS.sub("", p).strip(" -:,.")]
    t = _VIDEO_TAGS.sub(" ", parts[0] if parts else t)
    for a in sorted((a for a in re.split(r",|&| ft\.? | feat\.? ", artists or "") if len(a.strip()) >= 3), key=len, reverse=True):
        t = re.sub(re.escape(a.strip()), " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -:,.")


def record_cover(track):
    """The record's own cover for `track` (the engine's own look-up first); failing that, for a music
    video, the song of the very same name whose singer -- or whose film, its "(From ...)" -- the video's
    tags name. Blocking; None when there's no telling. Kept on disk either way."""
    vid = str(track.get("id", ""))
    known = _known_covers()
    if vid in known:
        return known[vid] or None
    url = _record_cover(track)
    _keep_cover(vid, url)
    return url


# a fan's or a label's other take on a song -- its picture is not the record's cover, and its sound not the song
_VARIANT = re.compile(r"\b(remix|synthwave|lo-?fi|slowed|reverb|sped[ -]?up|nightcore|8d|cover|covered|instrumental|"
                      r"karaoke|mashup|unplugged|reprise|acoustic|phonk|bass ?boost(?:ed)?|flip|edit|live|version|"
                      r"female|male|duet|chill|lofi|refix|bootleg|rework|retro|80s)\b", re.I)


def _same_take(want_title, cand):
    """`cand` is the same take of the song as `want_title`: no remix, lofi, cover... unless the song is one too."""
    have = set(m.lower() for m in _VARIANT.findall((cand.get("title") or "") + " " + (cand.get("album") or "")))
    asked = set(m.lower() for m in _VARIANT.findall(want_title or ""))
    return have <= asked


def _record_cover(track):
    from app.core import music_sources as ms, ytmusic
    url = ms.original_art(track)
    vid = str(track.get("id", ""))
    if url or not vid.startswith("yt:"):
        return url
    words = _video_words(vid[3:])
    if not words:
        return None
    name = _song_name(track.get("title"), track.get("artist"))
    want = ms._plain_title(name)
    if not want:
        return None
    try:
        songs = ytmusic.search(name)["songs"][:8]
    except Exception:
        return None
    for s in songs:
        if ms._plain_title(s.get("title")) != want or not ms.is_cover(s.get("artwork")) or not _same_take(track.get("title"), s):
            continue
        film = re.search(r"from\s+[\"'“]?([^\"'”)]+)", s.get("title") or "", re.I)
        singers = [a for a in ms._artist_set(s.get("artist")) if len(a) >= 4]
        if any(a in words for a in singers) or (film and len(film.group(1).strip()) >= 4 and film.group(1).strip().lower() in words):
            return s["artwork"]
    return None


def loudness_of(track):
    from app.core import music_sources as ms
    return _ok(ms.loudness_of(_j(track)))


def note_loudness(track, lufs):
    from app.core import music_sources as ms
    ms.note_loudness(_j(track), float(lufs))
    return _ok(True)


def normal_gain(lufs):
    from app.core import eq
    return _ok(eq.normal_gain(lufs))


# ------------------------------------------------------------------ keeping -
_keeps = {}            # track id -> {"got", "total", "state", "path", "error"}
_cancel = set()


def download(track, folder):
    """Keeps `track` (music_sources.download) in `folder`, in the background;
    keep_status() tells how it's going."""
    from app.core import music_sources as ms
    t = _j(track)
    tid = t["id"]
    _cancel.discard(tid)
    _keeps[tid] = {"got": 0, "total": 0, "state": "busy", "path": "", "error": ""}

    def progress(got, total):
        _keeps[tid].update(got=got, total=total)

    def work():
        try:
            path = ms.download(t, folder, progress, cancelled=lambda: tid in _cancel)
            _tag(path, t)
            _keeps[tid].update(state="done", path=path)
        except ms.Cancelled:
            _keeps[tid].update(state="cancelled")
        except Exception as e:   # noqa: BLE001
            _keeps[tid].update(state="failed", error=ms.friendly(e))
        try:
            taste().log(t, "download")
        except Exception:   # noqa: BLE001
            pass
    threading.Thread(target=work, daemon=True, name="music-keep").start()
    return _ok(True)


def _tag(path, t):
    """A kept M4A tagged as the desktop tags its MP3s (with FFmpeg): title, artist,
    album, year and the cover -- with mutagen, there being no FFmpeg here."""
    if not path or not path.lower().endswith((".m4a", ".mp4")):
        return
    try:
        import urllib.request
        from mutagen.mp4 import MP4, MP4Cover
        from app.core import music_sources as ms, ytmusic
        a = MP4(path)
        for key, value in (("\xa9nam", t.get("title")), ("\xa9ART", t.get("artist")), ("\xa9alb", t.get("album")),
                           ("\xa9day", t.get("year"))):
            if value:
                a[key] = [str(value)]
        art = t.get("artwork") or ""
        if art.startswith("http"):
            req = urllib.request.Request(ytmusic.sized(art, 544), headers=ms.UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(4 * 1024 * 1024)
            if data[:3] == ms.JPEG_MAGIC:
                a["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                a["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_PNG)]
        a.save()
    except Exception:   # noqa: BLE001 -- a song without its tags is still a song
        from app.logging_setup import get_logger
        get_logger("bridge").info("Couldn't tag %s", path, exc_info=True)


def tag_mp3(path, track):
    """A kept MP3 tagged as the desktop tags its own (ID3 v2.3): title, artist, album, year, cover."""
    t = _j(track)
    try:
        import urllib.request
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1
        from app.core import music_sources as ms, ytmusic
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        for frame, value in ((TIT2, t.get("title")), (TPE1, t.get("artist")), (TALB, t.get("album")), (TDRC, t.get("year"))):
            if value:
                tags.add(frame(encoding=3, text=str(value)))
        art = t.get("artwork") or ""
        if art.startswith("http"):
            req = urllib.request.Request(ytmusic.sized(art, 544), headers=ms.UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(4 * 1024 * 1024)
            if data[:3] == ms.JPEG_MAGIC or data[:8] == b"\x89PNG\r\n\x1a\n":
                tags.add(APIC(encoding=3, mime="image/jpeg" if data[:3] == ms.JPEG_MAGIC else "image/png", type=3,
                              desc="Album cover", data=data))
        tags.save(path, v2_version=3)
        return _ok(True)
    except Exception as e:   # noqa: BLE001 -- a song without its tags is still a song
        return _err(e)


def keep_status(tid):
    return _ok(_keeps.get(tid))


def clear_cache():
    from app.core import music_sources as ms
    import glob
    n = 0
    for p in glob.glob(os.path.join(glob.escape(ms.CACHE_DIR), "*")):
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return _ok(n)


# ------------------------------------------------------------------ lyrics --
def lyrics_find(track, duration_s):
    """Every version of the song's words (lyrics.find_versions), for the
    song at its playing length."""
    from app.core import upnext
    t = _j(track)

    def work():
        hint = upnext.features(t, taste().memory)["lang"]
        return _latin_first(_find_words(t, int(duration_s or t.get("duration") or 0), hint))
    return _call(work)


def _untimed(found):
    """The words without their timing: they were timed for another cut of the song."""
    for v in (found.get("versions") or {}).values():
        if v.get("synced"):
            v["plain"] = v.get("plain") or "\n".join(x for _ms, x in v.get("lines") or [])
            v["lines"], v["synced"] = [], False
    return found


def _find_words(t, secs, hint):
    """The song's words, tried harder than one look-up: LRCLIB matches a song by its name and its length,
    and much of what plays is a music video -- a title full of tags, a length the record doesn't have.
      1. as it plays;
      2. once more after a moment -- a busy LRCLIB or a dropped connection isn't "no lyrics"
         (the engine keeps an empty answer for the session; it's let go here);
      3. a music video: under its song's own name, and the record's name, singer and length
         (timed only when the lengths agree);
      4. at any length -- the words, untimed.
    """
    import copy
    from app.core import lyrics, music_sources as ms, ytmusic

    def look(track, dur):
        key = ("versions", track.get("id"), int(dur or 0))
        got = copy.deepcopy(lyrics.find_versions(dict(track, duration=int(dur or 0)), hint))
        if not got.get("versions"):
            lyrics._cache.pop(key, None)
        return got

    found = look(t, secs)
    if found.get("versions"):
        return found
    time.sleep(1.2)
    found = look(t, secs)
    if found.get("versions"):
        return found
    name = _song_name(t.get("title"), t.get("artist"))
    try:
        songs = ytmusic.search(name)["songs"][:6] if name else []
    except Exception:
        songs = []
    want = ms._plain_title(name)
    rec = next((s for s in songs if ms._plain_title(s.get("title")) == want), None)
    if rec:
        rd = int(rec.get("duration") or 0)
        found = look(dict(rec, id=str(t.get("id")) + "#record"), rd)
        if found.get("versions"):
            return found if (secs and rd and abs(rd - secs) <= lyrics.LENGTH_SLACK_S) else _untimed(found)
    if name and name != t.get("title"):
        found = look(dict(t, id=str(t.get("id")) + "#name", title=name), secs)
        if found.get("versions"):
            return found
    found = look(dict(t, id=str(t.get("id")) + "#any", title=name or t.get("title")), 0)
    return _untimed(found) if found.get("versions") else found


def _latin_first(found):
    """The phone shows the words in English letters, on their own: when LRCLIB has no Roman-letter
    version, one is made from the native script (how it's said, line by line, keeping the timing);
    the native script and translations stay in the lyrics menu, not under each line."""
    from app.core import lyrics
    versions = (found or {}).get("versions") or {}
    if not versions:
        return found
    if "Latn" not in versions:
        native = next((c for c in found.get("order") or list(versions) if c != "Latn"), None)
        res = versions.get(native) if native else None
        if res:
            lines = res.get("lines") or []
            texts = [x for _ms, x in lines] if lines else (res.get("plain") or "").splitlines()
            said = lyrics.pronounce(texts, native)
            if said and sum(1 for x in said if x.strip()) >= max(1, len([x for x in texts if x.strip()]) // 2):
                versions["Latn"] = {"lines": [[ms, x] for (ms, _o), x in zip(lines, said)] if lines else [],
                                    "plain": "" if lines else "\n".join(said), "synced": bool(lines),
                                    "spelt_out": True}
                found["order"] = ["Latn"] + [c for c in found.get("order") or [] if c != "Latn"]
    if "Latn" in versions:
        found["main"] = "Latn"
    found["sub"] = None
    return found


def lyrics_choice(found, main_pref, sub_pref):
    """(the version shown, what's under each line) -- the desktop tab's
    _lyrics_choice: "" nothing, "v:Deva" another version, "tr:en" a
    translation, "pron" how it's said."""
    found = _j(found) or {}
    versions = found.get("versions") or {}
    main = main_pref if main_pref in versions else found.get("main")
    under = sub_pref or "auto"
    if under == "auto":
        under = ""                       # the words on their own, unless another line is asked for
    if under == "auto" or (under.startswith("v:") and under[2:] not in versions
                           and under[2:] != found.get("spell")):
        dflt = found.get("sub")
        under = ("v:" + dflt) if dflt and dflt != main else ""
    if main in versions and not versions[main].get("synced"):
        timed = [c for c in (found.get("order") or list(versions)) if versions[c].get("synced")]
        if timed:
            main = timed[0]
    if under == "v:" + str(main):
        under = ""
    return _ok({"main": main, "under": under, "label": _lyrics_label(main, under)})


def _lyrics_label(main, under):
    from app.core import lyrics
    name = lyrics.SHORT.get(main, main)
    if not under:
        return name
    kind, _, arg = under.partition(":")
    if kind == "v":
        other = lyrics.SHORT.get(arg, arg)
    elif kind == "tr":
        other = dict(lyrics.TRANSLATE_TO).get(arg, arg)
    else:
        other = "Pronunciation"
    return "%s  ·  %s" % (name, other)


def lyrics_menu(found, main, under):
    """The choices the desktop's lyrics-language menu offers."""
    from app.core import lyrics
    found = _j(found) or {}
    versions = found.get("versions") or {}
    shows = [{"value": c, "text": lyrics.SCRIPT_NAMES.get(c, c) + (" (spelt out)" if versions[c].get("spelt_out")
                                                                   else ""), "on": c == main}
             for c in found.get("order") or list(versions)]
    options = [("", "Nothing")]
    for code in found.get("order") or []:
        if code != main:
            options.append(("v:" + code, lyrics.SCRIPT_NAMES.get(code, code)))
    spelt = found.get("spell")
    if spelt and main == "Latn" and spelt not in (found.get("order") or []):
        options.append(("v:" + spelt, lyrics.SCRIPT_NAMES.get(spelt, spelt) + " — spelt out"))
    if main != "Latn" and "Latn" not in versions:
        options.append(("pron", "How it's said (in English letters)"))
    unders = [{"value": v, "text": t, "on": v == under} for v, t in options]
    tr = [{"value": "tr:" + c, "text": n, "on": under == "tr:" + c}
          for c, n in lyrics.TRANSLATE_TO if c != found.get("lang")]
    return _ok({"shows": shows, "unders": unders, "translations": tr})


def lyrics_sub(found, main, under):
    """What goes under each line of version `main`: [text], and its script --
    the desktop tab's _apply_lyrics, minus the drawing."""
    from app.core import lyrics as lyrics_db
    found = _j(found) or {}

    def work():
        versions = found.get("versions") or {}
        res = versions[main]
        lines = res.get("lines") or []
        texts = [t for _ms, t in lines] if lines else (res.get("plain") or "").splitlines()
        kind, _, arg = under.partition(":")
        if kind == "v" and arg in versions:
            other = versions[arg]
            subs = lyrics_db.align(lines, other["lines"]) if lines else (other.get("plain") or "").splitlines()
            return {"subs": subs, "script": arg}
        lang = found.get("lang")
        subs, script = [], "Latn"
        if kind == "v":
            subs, script = lyrics_db.spell(texts, lang), arg
        elif kind == "tr":
            native = next((c for c in found.get("order") or [] if c != "Latn"), None)
            src = texts
            if native and native != main and lines:
                src = lyrics_db.align(lines, versions[native]["lines"])
            elif main == "Latn" and found.get("spell"):
                src = lyrics_db.spell(texts, lang) or texts
            subs = lyrics_db.translate(src, arg, lang or "auto")
            script = lyrics_db.script_code(" ".join(subs[:20]))
        elif kind == "pron":
            subs = lyrics_db.pronounce(texts, main)
        return {"subs": subs, "script": script}
    return _call(work)


# ------------------------------------------------------------------ taste ---
def taste_state():
    t = taste()
    return _ok({"onboarded": t.state.get("onboarded"), "seeds": t.state.get("seeds"),
                "prefs": t.state.get("prefs"), "feed_age": t.feed_age()})


def taste_options():
    from app.core import taste as taste_core
    return _ok({"languages": [list(x) for x in taste_core.LANGUAGES], "genres": list(taste_core.GENRES)})


def set_seeds(languages, genres, artists):
    """Your taste; and the artists you picked join your favourite artists (Your music > Artists), as if each had been
    liked -- the ones there already stay as they are."""
    from app.utils import music_library
    picked = _j(artists) or []
    taste().set_seeds(_j(languages), _j(genres), picked)
    for a in picked:
        bid = a.get("browse_id") or str(a.get("id") or "").replace("ytm:artist:", "")
        if not bid:
            continue
        item = {"kind": "artist", "id": "ytm:artist:" + bid, "browse_id": bid, "title": a.get("title") or a.get("name") or "",
                "artwork": a.get("artwork"), "source": "youtube"}
        try:
            if not music_library.is_saved(item):
                music_library.toggle(item)
        except Exception:   # noqa: BLE001 -- one that won't save leaves the rest
            pass
    return _ok(True)


def skip_onboarding():
    taste().skip_onboarding()
    return _ok(True)


def taste_reset():
    taste().reset()
    return _ok(True)


def set_taste_pref(key, value):
    t = taste()
    t.state["prefs"][key] = _j(value)
    t.save()
    return _ok(True)


def _followers(text):
    """1.2M / 340K / 2,345 subscribers (or monthly audience) in a subtitle, as a number; None if it doesn't say."""
    import re
    m = re.search(r"([\d.,]+)\s*([KMB])?\s*(subscribers|monthly audience|monthly listeners|followers)", text or "", re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1))


def onboarding_artists(queries, language, region="IN", searched=False):
    """Artists for the taste picks. `searched`: what was typed in its search box -- those artists, best match
    first, only leaving out tiny channels (the "71 subscribers" namesakes). Otherwise: the artists the given
    searches (a language, a genre) find, with a real following (50K+), biggest first; no `queries`: the
    country's top artists chart. One search per call, artists only (one request), so the picker asks for each
    on its own and shows them as they come."""
    from concurrent.futures import ThreadPoolExecutor
    from app.core import ytmusic

    def one(q):
        try:
            return ytmusic.search_artists(q)
        except Exception:   # noqa: BLE001 -- the rest still come
            return []

    def work():
        qs = _j(queries) if isinstance(queries, str) else (queries or [])
        out, seen = [], set()

        def add(a, lang, least):
            bid = a.get("browse_id") or a.get("id")
            n = _followers(a.get("subtitle"))
            if bid in seen or (n is not None and n < least) or (n is None and least > 1000):
                return
            seen.add(bid)
            out.append(dict(a, language=lang or None, followers=n))
        if searched:
            for q in qs[:1]:
                for a in one(q):
                    add(a, language, 1000)
            return out                                  # as YouTube Music ranks them: the best match first
        if qs:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for found in pool.map(one, qs):
                    for a in found:
                        add(a, language, 50000)
        else:
            try:
                for sh in ytmusic.charts(region or "IN"):
                    if "artist" in (sh.get("title") or "").lower():
                        for a in sh["items"]:
                            if a.get("kind") == "artist":
                                add(a, None, 0)
            except Exception:   # noqa: BLE001
                pass
        out.sort(key=lambda a: -(a.get("followers") or 0))
        return out
    return _call(work)


def log(track, kind):
    taste().log(_j(track), kind)
    return _ok(True)


def started(track, chosen):
    taste().started(_j(track), chosen=bool(chosen))
    return _ok(True)


def listen_ended(track, pos_s, duration_s, natural):
    taste().listen_ended(_j(track), float(pos_s), float(duration_s), natural=bool(natural))
    return _ok(True)


def dislike(track):
    taste().dislike(_j(track))
    return _ok(True)


def block_artist(track):
    taste().block_artist(_j(track))
    return _ok(True)


def build_feed():
    return _call(taste().build_feed)


def feed():
    return _ok(taste().feed())


def autoplay(track, exclude, n, chosen, played=None):
    """What follows `track`: the engine's up next for it, and the anchor's radio (the song you chose,
    kept until you choose another), weighed against the anchor by station.rank -- its language only,
    close in genre, mood and era, nothing heard this session again, no artist twice in a row."""
    def work():
        from app.core import station
        seed = _j(track)
        heard = _j(played) or []
        t = taste()
        st = _station()
        anchor = st.update(seed, bool(chosen), t.memory, heard)
        engine = t.autoplay(seed, exclude=(_j(exclude) or []) + [x.get("id") for x in heard], n=60,
                            chosen=bool(chosen))
        anchor_radio, era_radio = [], []
        try:
            got = t.fetch.radio_full(anchor["id"].split(":", 1)[1]) or {}
            anchor_radio = got.get("tracks") or []
            if anchor_radio and anchor_radio[0].get("id") == anchor["id"] and not anchor.get("year"):
                anchor = dict(anchor, year=anchor_radio[0].get("year"))      # the radio's first is the song itself
                st.anchor = anchor
            chip = station.decade_chip(got.get("chips") or {}, station.year_of(anchor))
            if chip:
                era_radio = (t.fetch.radio_full(anchor["id"].split(":", 1)[1], (got.get("chips") or {})[chip]) or {}).get("tracks") or []
        except Exception:
            pass                                     # the engine's own picks still play
        return station.rank(anchor, st.anchor_f, seed, engine, anchor_radio, era_radio, heard, int(n), t.memory,
                            liked=st.liked)
    return _call(work)


_STATION = None


def _station():
    """The autoplay station (station.Station): its anchor lasts for the app's run."""
    global _STATION
    if _STATION is None:
        from app.core import station
        _STATION = station.Station()
    return _STATION


def language_of(track):
    from app.core import upnext
    return _ok(upnext.features(_j(track), taste().memory)["lang"])


# ------------------------------------------------------------------ library -
def saved(kind):
    from app.utils import music_library
    return _ok(music_library.saved(kind))


def is_saved(item):
    from app.utils import music_library
    return _ok(music_library.is_saved(_j(item)))


def saved_ids():
    from app.utils import music_library
    return _ok([x["id"] for k in music_library.KINDS for x in music_library.saved(k)])


def toggle_saved(item):
    from app.utils import music_library
    it = _j(item)
    on = music_library.toggle(it)
    if on and it.get("kind", "track") == "track":
        try:
            taste().log(it, "like")
        except Exception:   # noqa: BLE001
            pass
    return _ok(on)


def add_recent(track):
    from app.utils import music_library
    music_library.add_recent(_j(track))
    return _ok(True)


def recent():
    from app.utils import music_library
    return _ok(music_library.recent())


def playlists():
    from app.utils import music_library
    return _ok(music_library.playlists())


def create_playlist(title, tracks):
    from app.utils import music_library
    return _ok(music_library.create_playlist(title, _j(tracks) or []))


def add_to_playlist(pid, track):
    from app.utils import music_library
    return _ok(music_library.add_to_playlist(pid, _j(track)))


def remove_from_playlist(pid, track_id):
    from app.utils import music_library
    music_library.remove_from_playlist(pid, track_id)
    return _ok(True)


def rename_playlist(pid, title):
    from app.utils import music_library
    music_library.rename_playlist(pid, title)
    return _ok(True)


def delete_playlist(pid):
    from app.utils import music_library
    music_library.delete_playlist(pid)
    return _ok(True)


_import = {}


def import_playlist(text, name="", api_base=""):
    """Imports a playlist from a link or a list of songs (the desktop's
    ImportSheet), in the background; import_status() tells how it's going."""
    from app.core import playlist_import
    from app.utils import music_library
    if api_base:
        os.environ["AWESOME_MUSIC_API"] = api_base
    _import.clear()
    _import.update(state="busy", done=0, total=0, note="Reading the playlist...")

    def work():
        try:
            found = playlist_import.read(text)
            tracks = list(found.get("tracks") or [])
            if tracks:
                _import.update(total=len(tracks), note="Finding the songs...")
                tracks = playlist_import.prefer_songs(tracks, found.get("songs"), lambda done, total: _import.update(
                    done=done, total=total, note="Finding the songs... %d of %d" % (done, total)))
            found["tracks"] = tracks
            if found.get("items"):
                total = len(found["items"])
                _import.update(total=total, note="Finding %d songs..." % total)

                def progress(done, total):
                    _import.update(done=done, total=total, note="Finding songs... %d of %d" % (done, total))
                tracks += [m for m in playlist_import.match(found["items"], progress) if m]
            if not tracks:
                raise playlist_import.ImportError_("None of its songs could be found.")
            missing = len(found.get("items") or []) - (len(tracks) - len(found.get("tracks") or []))
            pl = music_library.create_playlist((name or "").strip() or found["title"], tracks,
                                               source=found["source"], artwork=tracks[0].get("artwork"))
            _import.update(state="done", playlist=pl, note="%d songs imported%s%s." % (
                len(tracks), (", %d couldn't be found" % missing) if missing > 0 else "",
                " -- Spotify showed only its first 100 songs" if found.get("short") else ""))
        except Exception as e:   # noqa: BLE001
            from app.core import music_sources as ms
            _import.update(state="failed", note=ms.friendly(e))
    threading.Thread(target=work, daemon=True, name="music-import").start()
    return _ok(True)


def import_liked(items, source, link=""):
    """Liked songs from another app into yours, in the background (import_status() tells how it's going): `items`, the
    songs read from it ([{"title", "artist", "album", "duration"}], as the sign-in page read them), or `link` -- a
    playlist they were copied into. Each is matched to YouTube Music's song, liked here (the oldest first, so your
    newest likes come out on top, as in the app they came from), and kept as a playlist too ("Imported likes")."""
    from app.core import playlist_import
    from app.utils import music_library
    _import.clear()
    _import.update(state="busy", done=0, total=0, note="Reading your liked songs...")

    def work():
        try:
            given = _j(items) if isinstance(items, str) else (items or [])
            tracks = []
            songs = ()
            if link:
                found = playlist_import.read(link)
                tracks = list(found.get("tracks") or [])
                songs = found.get("songs")
                given = list(found.get("items") or [])
            videos = [x for x in given if x.get("video_id")]
            if videos:
                tracks, songs = playlist_import.from_videos(videos)
                given = [x for x in given if not x.get("video_id")]
            want = [{"title": str(x.get("title") or "").strip(), "artist": str(x.get("artist") or "").strip(),
                     "duration": int(x.get("duration") or 0)} for x in given if str(x.get("title") or "").strip()]
            total = len(want) + len(tracks)
            if not total:
                raise playlist_import.ImportError_("No liked songs were found there.")
            _import.update(total=total, note="Finding your %d songs..." % total)
            if tracks:
                count = len(tracks)
                tracks = playlist_import.prefer_songs(tracks, songs, lambda done, _t: _import.update(
                    done=done, note="Finding your songs... %d of %d" % (done, total)), workers=8)
                total -= count - len(tracks)    # two videos of one song are one song

            def progress(done, _total):
                _import.update(done=done + len(tracks), note="Finding your songs... %d of %d" % (done + len(tracks), total))
            found_tracks = tracks + [m for m in playlist_import.match(want, progress, workers=8) if m]
            if not found_tracks:
                raise playlist_import.ImportError_("None of those songs could be found.")
            # liked here, oldest first: the newest like ends up at the top of Liked songs
            added = music_library.save_many(reversed(found_tracks))
            pl = music_library.merge_playlist("Imported likes", found_tracks, source=source or "import",
                                              artwork=found_tracks[0].get("artwork"))
            missing = total - len(found_tracks)
            _import.update(state="done", playlist=pl, added=added, note="%d songs added to your Liked songs%s." % (
                added, (" (%d couldn't be found)" % missing) if missing > 0 else ""))
        except Exception as e:   # noqa: BLE001
            from app.core import music_sources as ms
            _import.update(state="failed", note=ms.friendly(e))
    threading.Thread(target=work, daemon=True, name="music-import-liked").start()
    return _ok(True)


def _read_link(link):
    """A linked playlist's songs as its app has them now, matched to YouTube Music's songs."""
    from app.core import playlist_import
    found = playlist_import.read(link)
    tracks = list(found.get("tracks") or [])
    if tracks:
        tracks = playlist_import.prefer_songs(tracks, found.get("songs"), None)
    if found.get("items"):
        tracks += [m for m in playlist_import.match(found["items"], None) if m]
    return tracks


_syncing = threading.Lock()


def sync_playlist(pid):
    """One linked playlist brought up to date with where it came from: {"added", "title"}."""
    from app.utils import music_library
    pl = music_library.playlist(pid)
    if not pl or not pl.get("link"):
        return _err(Exception("This playlist isn't linked to another app."))
    try:
        with _syncing:
            added = music_library.synced_add(pid, _read_link(pl["link"]), time.time())
        return _ok({"added": added, "title": pl.get("title") or ""})
    except Exception as e:   # noqa: BLE001
        return _err(e)


def sync_due(max_age_s=6 * 3600, done=None):
    """Every linked playlist not synced for a while, brought up to date in the background; how many are being looked
    at. done(added) afterwards, off this thread."""
    from app.utils import music_library
    due = [p for p in music_library.playlists() if p.get("link") and time.time() - (p.get("synced") or 0) > float(max_age_s)]

    def work():
        added = 0
        for p in due:
            try:
                with _syncing:
                    added += music_library.synced_add(p["id"], _read_link(p["link"]), time.time())
            except Exception:   # noqa: BLE001 -- one that can't be read is tried again next time
                from app.logging_setup import get_logger
                get_logger("bridge").info("Couldn't sync a linked playlist", exc_info=True)
        if done is not None:
            done(added)
    if due:
        threading.Thread(target=work, daemon=True, name="playlist-sync").start()
    return _ok(len(due))


def import_status():
    return _ok(dict(_import))


def mine(q):
    """Songs of your own (liked, recent, in your playlists) close to `q` --
    forgiving of a letter or two out of place (the desktop tab's _mine)."""
    import difflib
    from app.utils import music_library
    ql = (q or "").lower().strip()
    pool, seen = [], set()
    for t in music_library.saved("songs") + music_library.recent() + [
            x for p in music_library.playlists() for x in p["tracks"]]:
        if t.get("id") not in seen and t.get("kind", "track") == "track":
            seen.add(t["id"])
            pool.append(t)
    scored = []
    words = ql.split()
    for t in pool:
        hay = ("%s %s" % (t.get("title", ""), t.get("artist", ""))).lower()
        hits = sum(1 for w in words if any(difflib.SequenceMatcher(None, w, h).ratio() > 0.75 for h in hay.split()))
        score = hits / max(1, len(words))
        if score >= 0.6:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return _ok([t for _s, t in scored[:4]])


def artist_names(text):
    """The artists of a song's "A, B & C feat. D" line, each once."""
    import re
    names = []
    for n in re.split(r"\s*(?:,|&|\band\b|\bfeat\.?|\bft\.|\bwith\b)\s*", text or "", flags=re.I):
        if n.strip() and n.strip().lower() not in [m.lower() for m in names]:
            names.append(n.strip())
    return _ok(names)


def find(kind, q):
    """The first artist or album YouTube Music has for `q` (opening an
    artist or album a song doesn't link to), or None."""
    from app.core import ytmusic

    def work():
        found = ytmusic.search(q).get("artists" if kind == "artist" else "albums") or []
        return found[0] if found else None
    return _call(work)


def ping():
    return _ok(time.time())


# ------------------------------------------------------------------ sync ---
def sync_prepare(app_docs):
    """What changed here since the last sync (library_sync.prepare); "first" when this phone has never
    synced -- then the app pulls the server's library in before sending its own on top."""
    from app.core import library_sync

    return _call(lambda: library_sync.prepare(taste(), _j(app_docs) or {}))


def sync_apply(docs, rev):
    from app.core import library_sync
    return _call(lambda: library_sync.apply(taste(), _j(docs) or [], rev))


def sync_forget():
    from app.core import library_sync
    library_sync.forget()
    return _ok(True)


def set_playlist_public(pid, on):
    """Shows a playlist on your profile (or keeps it to yourself); it reaches the server with the next sync."""
    from app.utils import music_library
    for p in music_library._load().setdefault("playlists", []):
        if p["id"] == pid:
            p["public"] = bool(on)
            music_library._save()
            return _ok(True)
    return _ok(False)


# ------------------------------------------------------------------ mood mixes -
# Each card: its name, whether it's a genre (a genre's mix is only that genre: its top and newest songs, none of the
# rest of your library) or a mood, YouTube Music's own pages for it (by language where there's one), the words a
# search for it takes, how lively it is (energy, -1 calm .. 1 lively), the genre words that say a song belongs, and
# words in a song's own name that say so (a phonk track nearly always says "phonk").
MOOD_MIXES = {
    "chill": {"title": "Chill", "cats": ["Chill"], "words": "chill", "energy": (-1.0, 0.15)},
    "focus": {"title": "Focus", "cats": ["Focus"], "words": "focus instrumental", "energy": (-1.0, 0.0),
              "genres": ("lo-fi", "classical", "ambient", "instrumental")},
    "workout": {"title": "Workout", "cats": ["Workout", "Energize"], "words": "workout gym", "energy": (0.35, 1.0)},
    "party": {"title": "Party", "cats": ["Party"], "words": "party", "energy": (0.35, 1.0)},
    "romance": {"title": "Romance", "cats": ["Romance"], "words": "romantic", "energy": (-0.6, 0.5), "genres": ("romantic",)},
    "sad": {"title": "Sad", "cats": ["Sad"], "words": "sad", "energy": (-1.0, 0.0), "genres": ("sad",)},
    "lofi": {"title": "Lo-fi", "genre": True, "words": "lofi", "genres": ("lo-fi", "lofi"), "tags": ("lofi", "lo-fi", "lo fi")},
    "indie": {"title": "Indie", "genre": True, "cats": ["Indie & alternative"], "cats_hi": ["Indian indie"], "words": "indie",
              "genres": ("indie", "alternative")},
    "jazz": {"title": "Jazz", "genre": True, "cats": ["Jazz"], "words": "jazz", "genres": ("jazz",), "tags": ("jazz",)},
    "classical": {"title": "Classical", "genre": True, "cats": ["Classical"], "words": "classical", "genres": ("classical",),
                  "tags": ("symphony", "sonata", "concerto", "nocturne", "prelude", "requiem", "mozart", "beethoven",
                           "chopin", "bach", "debussy", "vivaldi")},
    "hiphop": {"title": "Hip-hop", "genre": True, "cats": ["Hip-hop"], "cats_hi": ["Desi hip-hop"], "words": "hip hop rap",
               "genres": ("hip-hop", "hip hop", "rap", "trap")},
    "phonk": {"title": "Phonk", "genre": True, "words": "phonk", "genres": ("phonk",), "tags": ("phonk", "drift", "funk")},
}
_mood_cache = {}


def _mood_fits(m, tr, f, strict=False):
    """Whether a song belongs on a card. A genre: its genre (or its own name says so) -- never just "lively enough".
    A mood: the right energy (and, strictly, the mood's genre where it names one)."""
    name = " ".join(str(tr.get(k) or "") for k in ("title", "artist", "album")).lower()
    g = " ".join(f["genres"] or ()).lower()
    says = any(w in name for w in m.get("tags", ())) or any(w in g for w in m.get("genres", ()))
    if m.get("genre"):
        return says
    e = m.get("energy")
    if e is not None:
        if f["energy"] is None:
            return says
        if not (e[0] <= f["energy"] <= e[1]):
            return False
        return says or not strict or not m.get("genres")
    return says


def _mood_covers_path():
    return os.path.join(os.environ.get("AWESOME_MUSIC_HOME") or ".", "mood_covers_made.json")


def _mood_covers_made():
    try:
        with open(_mood_covers_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:   # noqa: BLE001
        return {}


def mood_mix(key):
    """A mood's or genre's mix. A genre (Phonk, Jazz, Hip-hop...) is that genre only: its top and newest songs, from
    YouTube Music's own lists for it and searches for its hits, and songs of yours only when they are that genre.
    A mood leans on its own lists in your languages, with songs of yours that fit it. Kept two hours."""
    def work():
        hit = _mood_cache.get(key)
        if hit and time.time() - hit[0] < 7200:
            return hit[1]
        out = _make_mood_mix(key)
        if out["tracks"]:
            _mood_cache[key] = (time.time(), out)
            # its first song with a record's own cover is the card's cover when none of yours fits
            art = next((x.get("artwork") for x in out["tracks"] if "googleusercontent.com" in (x.get("artwork") or "")
                        or "ggpht.com" in (x.get("artwork") or "")), None)
            if art:
                made = _mood_covers_made()
                made[key] = art
                try:
                    with open(_mood_covers_path(), "w", encoding="utf-8") as f:
                        json.dump(made, f)
                except OSError:
                    pass
        return out
    return _call(work)


def _make_mood_mix(key):
    import random
    from concurrent.futures import ThreadPoolExecutor
    from app.core import ytmusic, upnext
    from app.utils import music_library
    m = MOOD_MIXES.get(key) or {"title": key.title(), "cats": [key.title()], "words": key}
    genre = bool(m.get("genre"))
    t = taste()
    mem = t.memory
    langs = [x for x in ((t.state.get("seeds") or {}).get("languages") or []) if x][:2] or ["hi"]
    lang_words = [_LANG_WORDS.get(x, "") for x in langs]
    year = time.strftime("%Y")

    def fits(tr, strict=False):
        f = upnext.features(tr, mem)
        if not _mood_fits(m, tr, f, strict):
            return False
        # a genre is the genre in any language; a mood keeps to yours
        return genre or f["lang"] in langs or f["lang"] is None or "en" in langs

    # yours: liked first, then played -- only the ones that truly belong
    mine, seen = [], set()
    for tr in (music_library.saved("songs") or []) + (music_library.recent() or []):
        k = upnext.song_key(tr)
        if not k or k in seen or not str(tr.get("id", "")).startswith("yt:"):
            continue
        seen.add(k)
        if fits(tr, strict=True):
            mine.append(tr)
    random.shuffle(mine)
    mine = mine[:8 if genre else 12]

    def searched(q):
        """A search's songs: its top ones are what was searched for (a search for phonk's hits is phonk, named so
        or not); past them, only the ones that fit."""
        try:
            got = ytmusic.search(q)["songs"][:20]
        except Exception:   # noqa: BLE001
            return []
        trust = 12
        return [x for i, x in enumerate(got) if i < trust or fits(x)]

    def radio_of(tr):
        try:
            return [x for x in ytmusic.radio(tr["id"][3:])[1:30] if fits(x)][:12]
        except Exception:   # noqa: BLE001
            return []

    def lists():
        """YouTube Music's own lists for it, the ones in your languages first: [[track], ...] a list each."""
        global _moods
        cats = list(m.get("cats") or [])
        if "hi" in langs or "mr" in langs or "pa" in langs:
            cats = list(m.get("cats_hi") or []) + cats
        if not cats:
            return []
        try:
            if _moods is None:
                _moods = ytmusic.moods()
            pls = []
            for c in cats:
                ep = _moods.get(c)
                if ep:
                    pls += [x for sh in ytmusic.category(ep) for x in sh["items"] if x.get("kind") == "ytplaylist"]
            ours = [x for x in pls if any(w and w.lower() in (x.get("title") or "").lower()
                                          for w in lang_words + (["Bollywood", "Desi"] if "hi" in langs else []))]
            if not genre and "en" not in langs:
                pls = ours or pls
            else:
                pls = ours + [x for x in pls if x not in ours]
            out = []
            for pl in pls[:3]:
                try:
                    out.append((ytmusic.playlist(pl["browse_id"]).get("tracks") or [])[:25])
                except Exception:   # noqa: BLE001
                    pass
            return out
        except Exception:   # noqa: BLE001
            return []

    w = m.get("words") or key
    if genre:
        queries = ["top %s songs" % w, "new %s songs %s" % (w, year)]
        if m.get("cats_hi") and ("hi" in langs or "pa" in langs):
            queries.append("%s %s songs" % (lang_words[0], w))
    else:
        queries = ["%s %s songs" % (lw, w) for lw in lang_words if lw]
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_lists = pool.submit(lists)
        f_search = [pool.submit(searched, q) for q in queries]
        f_radio = [pool.submit(radio_of, tr) for tr in mine[:2]]
        sources = f_lists.result() + [f.result() for f in f_search]
        # a mood's lists are the mood already: kept to your languages
        if not genre:
            sources = [[x for x in src if "en" in langs or upnext.features(x, mem)["lang"] in langs + [None]] for src in sources]
        radio = [x for f in f_radio for x in f.result()]
    # no list for it and the searches thin: the radio of its top song (a phonk song's radio is phonk)
    flat = [x for src in sources for x in src]
    if genre and len(flat) < 25 and flat:
        radio += radio_of(flat[0])
    # top first: a song from each source in turn, so the best of each comes early
    backbone = []
    for row in range(max([len(x) for x in sources + [radio]] or [0])):
        for src in sources + [radio]:
            if row < len(src):
                x = src[row]
                k = upnext.song_key(x)
                if k and k not in seen and str(x.get("id", "")).startswith("yt:"):
                    seen.add(k)
                    backbone.append(x)
    if not genre:
        head, rest = backbone[:10], backbone[10:]
        random.shuffle(rest)
        backbone = head + rest
    # one of yours every few songs
    every = 4 if genre else 3
    tracks, i = [], 0
    for n, x in enumerate(backbone):
        if n % every == every - 1 and i < len(mine):
            tracks.append(mine[i])
            i += 1
        tracks.append(x)
        if len(tracks) >= 50:
            break
    while i < len(mine) and len(tracks) < 50:
        tracks.append(mine[i])
        i += 1
    title = m["title"]
    if genre:
        sub = "Top and new %s" % title + (" · %d of yours" % i if i else "")
        over = "Genre · top and latest"
    else:
        sub = "%d of yours and %d more for the mood" % (i, len(tracks) - i)
        over = "Mood mix · made for you"
    return {"kind": "mix", "id": "moodmix:" + key, "title": title, "over": over, "subtitle": sub, "tracks": tracks[:50]}


# ------------------------------------------------------------------ the DJ (beta) -
# Spotify's DJ, the phone's way: a run of short sets, each with a theme -- what you've had on repeat, songs you
# liked a while back, new ones picked for you, today's charts in your language, something like your favourite
# artist -- and a few words before each set, spoken by the phone's own voice over the song's opening (the
# words made here, from what's in the set; nothing is sent anywhere to write them).
def dj_session(exclude=None, options=None):
    """The DJ's sets: [{"theme", "tracks": [...]}], four songs each, every song once and none just heard.

    options: {"vibe": "familiar" | "balanced" | "adventurous" -- how much of it is yours vs new to you,
              "energy": "chill" | "steady" | "hype" -- kept to songs that calm, or that lift,
              "request": what was asked for, in any words ("90s Hindi", "i'm running very fast", "driving, in Punjabi"):
                         read for its situation, energy and language (dj_brain), which then hold for the whole session}."""
    def work():
        import random
        from concurrent.futures import ThreadPoolExecutor
        from app.core import dj_brain, upnext, ytmusic
        from app.utils import music_library
        o = _j(options) if isinstance(options, str) else (options or {})
        vibe = o.get("vibe") or "balanced"
        energy = o.get("energy") or "steady"
        request = (o.get("request") or "").strip()[:120]
        t = taste()
        mem = t.memory
        feed = t.feed() or {}
        want = dj_brain.read(request, (t.state.get("seeds") or {}).get("languages") or ())
        # what was asked for sets the energy, over the page's setting
        energy = want["energy"] or energy
        skip = set(_j(exclude) or []) if isinstance(exclude, str) else set(exclude or [])
        used = set()
        # the song playing: the first pick follows on from it
        seed = o.get("seed") if isinstance(o.get("seed"), dict) else None
        last = [upnext.features(seed, mem) if seed else None]

        def gap(f, g):
            """How far apart two songs feel: language, genres, intensity (0 = the same, ~2+ = a jolt)."""
            if f is None or g is None:
                return 0.5
            d = 0.0
            if f["lang"] and g["lang"] and f["lang"] != g["lang"]:
                d += 0.7
            gf, gg = set(f["genres"] or ()), set(g["genres"] or ())
            if gf and gg:
                d += 0.6 * (1 - len(gf & gg) / len(gf | gg))
            ef, eg = f["energy"], g["energy"]
            if ef is not None and eg is not None:
                d += 1.4 * abs(ef - eg)
                if abs(ef - eg) > 0.7:
                    d += 1.0                       # a jump from flat out to very calm (or back): never next to each other
            return d

        def chain(lst, n):
            """n songs from `lst`, each the closest still left to the one before it (the first, to how the last set
            ended): a flow, not a shuffle. Songs too far from everything are left out."""
            pool = []
            for x in lst or []:
                k = upnext.song_key(x)
                if not k or k in used or x.get("id") in skip or not str(x.get("id", "")).startswith("yt:") or not lively(x):
                    continue
                pool.append((x, upnext.features(x, mem), k))
            out = []
            while pool and len(out) < n:
                ranked = sorted(pool, key=lambda p: gap(last[0], p[1]))
                x, f, k = ranked[0]
                if out and gap(last[0], f) > 1.6:
                    break                          # nothing left that follows on well: a shorter set
                pool.remove(ranked[0])
                used.add(k)
                out.append(x)
                last[0] = f
            return out

        def on_request(x):
            """Not plainly wrong for what was asked: the language, and what its name says."""
            if not dj_brain.fits(x, want) or looks_like_upload(x, channel=False):
                return False
            lang = upnext.features(x, mem)["lang"]
            return not (want["language"] and lang and lang != want["language"])

        def lively(x):
            if request and not on_request(x):
                return False
            e = upnext.features(x, mem)["energy"]
            # a situation asked for: your own songs join only when they're known to fit it
            if request and want["energy"] and e is None:
                return False
            if energy == "chill":
                return e is None or e <= 0.15
            if energy == "hype":
                return e is None or e >= 0.2
            return True

        def fresh(lst, n, strict=True):
            out = []
            for x in lst or []:
                k = upnext.song_key(x)
                if not k or k in used or x.get("id") in skip or not str(x.get("id", "")).startswith("yt:"):
                    continue
                if strict and not lively(x):
                    continue
                used.add(k)
                out.append(x)
                if len(out) >= n:
                    break
            return out

        def shuffled(lst):
            lst = list(lst or [])
            random.shuffle(lst)
            return lst
        sets = []

        def add(theme, tracks):
            if len(tracks) >= 2:
                sets.append({"theme": theme, "tracks": tracks})
        # what was asked for, first: songs found for it and songs like the best of them
        if request:
            def look(q):
                try:
                    return ytmusic.search_songs(q)[:12]
                except Exception:   # noqa: BLE001
                    return []
            with ThreadPoolExecutor(max_workers=4) as pool:
                answers = list(pool.map(look, want["queries"]))
            # each search's best first, turn by turn, so one reading of the request doesn't fill the set
            found = [x for row in zip(*[a + [None] * (12 - len(a)) for a in answers]) for x in row if x] if answers else []
            good = [x for x in found if on_request(x)]
            like = []
            # songs like these: not when a language was asked for, where a radio soon wanders out of it
            for seed_song in ([] if want["language"] else good[:2]):
                try:
                    like += [x for x in ytmusic.radio(seed_song["id"][3:])[1:15] if on_request(x)]
                except Exception:   # noqa: BLE001
                    pass
            pick = good or found            # filtered down to nothing: what the search said, rather than silence
            title = want["label"] or (request[:1].upper() + request[1:])
            add(title, fresh(pick[:6] + shuffled(like), 6, strict=False))
            add(title + " \u00b7 more", fresh(shuffled(pick[6:] + like), 6, strict=False))
        recent = [x for x in music_library.recent() if x.get("kind", "track") == "track"]
        liked = [x for x in music_library.saved("songs") if x.get("kind", "track") == "track"]
        try:
            with open(_home_path(), encoding="utf-8") as f:
                charts = json.load(f).get("charts") or []
        except Exception:   # noqa: BLE001
            charts = []
        # yours: what you've had on repeat, what you liked a while back, more like a favourite
        mine = [("On repeat", shuffled(recent[:30])), ("Throwback", shuffled(liked[len(liked) // 3:] or liked))]
        for b in (feed.get("because") or [])[:2]:
            # the song's own name, without "(From the film)" or "[Remix]" after it
            name = re.sub(r"\s*[(\[].*$", "", b.get("title") or "").strip() or "that"
            mine.append(("Because you played " + name, shuffled(b.get("tracks"))))
        # new to you: picks from your taste, artists your artists' fans play, today's charts
        new = [("New for you", shuffled(feed.get("discover"))), ("Your kind of sound", shuffled(feed.get("picked")))]
        for c in charts[:3]:
            new.append(((c.get("title") or "The charts").replace(" \u00b7 ", " in "), shuffled((c.get("tracks") or [])[:25])))
        random.shuffle(mine)
        random.shuffle(new)
        # the mix of the two, by the vibe: familiar is mostly yours, adventurous mostly new
        share_new = {"familiar": 0.25, "balanced": 0.5, "adventurous": 0.8}.get(vibe, 0.5)
        mi = ni = 0
        while (mi < len(mine) or ni < len(new)) and len(sets) < 7:
            want_new = (ni + 1) / (mi + ni + 1) <= share_new + 0.01
            pick = (new[ni] if ni < len(new) else None) if want_new else (mine[mi] if mi < len(mine) else None)
            if pick is None:
                pick = new[ni] if ni < len(new) else mine[mi]
                want_new = ni < len(new)
            if want_new:
                ni += 1
            else:
                mi += 1
            add(pick[0], chain(pick[1], 4))
        # too little to go on yet: the charts carry it
        if len(sets) < 2:
            more = shuffled([x for c in charts[:4] for x in (c.get("tracks") or [])[:20]])
            add("Today's charts", fresh(more, 6, strict=False))
        return sets
    return _call(work)

def mood_covers():
    """A cover for every mood card, from what's on the phone already -- your liked and played songs, today's
    charts kept for Home -- a song that fits each mood (its energy, its genre) where there is one, else any; each
    cover used once. No network. {mood key: artwork url}."""
    def work():
        from app.core import upnext
        from app.utils import music_library
        mem = taste().memory
        pool, seen = [], set()
        try:
            with open(_home_path(), encoding="utf-8") as f:
                charted = [t for c in (json.load(f).get("charts") or []) for t in (c.get("tracks") or [])[:25]]
        except Exception:   # noqa: BLE001
            charted = []
        for t in (music_library.saved("songs") or []) + (music_library.recent() or []) + charted:
            art = t.get("artwork") or ""
            if t.get("kind", "track") != "track" or not art or art in seen:
                continue
            # a record's own cover (Google's or Apple's picture server), not a video's frame
            if "googleusercontent.com" not in art and "ggpht.com" not in art and "mzstatic.com" not in art and "scdn.co" not in art:
                continue
            seen.add(art)
            pool.append((t, upnext.features(t, mem)))
        made = _mood_covers_made()
        out, used = {}, set()
        for key, m in MOOD_MIXES.items():
            pick = next((t for t, f in pool if t["artwork"] not in used and _mood_fits(m, t, f, strict=True)), None)
            if pick is None and made.get(key):
                out[key] = made[key]            # none of yours fits: the cover of its own mix's top song
                continue
            # a mood with nothing yet: any song of yours, so long as it isn't plainly one genre's (a phonk
            # track's cover on Romance would say the wrong thing)
            if pick is None and not m.get("genre"):
                tagged = [w for g in MOOD_MIXES.values() if g.get("genre") for w in g.get("tags", ())]
                pick = next((t for t, f in pool if t["artwork"] not in used and not any(
                    w in (str(t.get("title")) + " " + str(t.get("artist"))).lower() for w in tagged)), None)
            if pick:
                used.add(pick["artwork"])
                out[key] = pick["artwork"]
        return out
    return _call(work)
