"""YouTube Music's catalogue, read the way music.youtube.com reads it.

The site's own web client (InnerTube, client "WEB_REMIX") answers a search
with songs, albums and artists -- each song with its square album cover --
and opens an artist's or an album's page, all without an account. These are
the same requests the site makes; the songs themselves still play through
yt-dlp (music_sources.resolve_stream).

How the answers are read follows the open-source ytmusicapi project (MIT),
but loosely: renderers are found by name wherever they sit, rather than by
fixed paths, so a wrapper YouTube moves doesn't break the search.

Everything here is blocking; the Music tab calls it from worker threads.
"""
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..logging_setup import get_logger

logger = get_logger("ytmusic")

API = "https://music.youtube.com/youtubei/v1/"
CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20250929.01.00", "hl": "en", "gl": "US"}
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://music.youtube.com",
    "Referer": "https://music.youtube.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/140.0.0.0 Safari/537.36",
}
# The search filters the site's own chips send (Songs, Albums, Artists).
FILTERS = {
    "songs": "EgWKAQIIAWoMEA4QChADEAQQCRAF",
    "albums": "EgWKAQIYAWoMEA4QChADEAQQCRAF",
    "artists": "EgWKAQIgAWoMEA4QChADEAQQCRAF",
}
ARTIST, ALBUM = "MUSIC_PAGE_TYPE_ARTIST", "MUSIC_PAGE_TYPE_ALBUM"
_DURATION = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_SIZE = re.compile(r"=(w\d+-h\d+|s\d+)(?=[-a-z0-9]*$)")


class YTMusicError(Exception):
    pass


def _post(endpoint, body, timeout=15):
    data = json.dumps({"context": {"client": CLIENT}, **body}).encode("utf-8")
    req = urllib.request.Request(API + endpoint + "?prettyPrint=false", data=data, headers=HEADERS,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise YTMusicError("YouTube Music answered %s" % e.code) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise YTMusicError("Couldn't reach YouTube Music (%s)" % e) from e


# ------------------------------------------------------------- reading ----
def _walk(o, key):
    """Every value stored under `key`, anywhere in `o` -- list items in their
    order, a dict's own match before what's nested deeper inside it."""
    out = []
    stack = [o]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if key in cur:
                out.append(cur[key])
            stack.extend(v for v in reversed(list(cur.values())) if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(v for v in reversed(cur) if isinstance(v, (dict, list)))
    return out


def _first(o, key, default=None):
    found = _walk(o, key)
    return found[0] if found else default


def _runs(o):
    return list((o or {}).get("runs") or [])


def _text(o):
    return "".join(r.get("text", "") for r in _runs(o)).strip()


def _page_type(run):
    return _first(run.get("navigationEndpoint") or {}, "pageType") or ""


def _browse_id(o):
    return _first(o or {}, "browseId") or ""


def _thumb(o):
    """The largest picture in a renderer's thumbnail."""
    for thumbs in _walk(o or {}, "thumbnails"):
        if thumbs:
            best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
            return best.get("url") or None
    return None


def sized(url, px, height=None):
    """The same Google-hosted picture at `px` wide (and `height`, square by
    default) -- covers come listed at 60-120 px."""
    if not url or "googleusercontent.com" not in url and "ggpht.com" not in url:
        return url
    size = "w%d-h%d" % (px, height or px)
    if _SIZE.search(url):
        return _SIZE.sub("=" + size, url, count=1)
    return url + "=" + size + "-l90-rj"


def _seconds(text):
    parts = [int(x) for x in text.split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def _flex(item):
    return [(c.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}
            for c in item.get("flexColumns") or []]


def _fixed(item):
    return [(c.get("musicResponsiveListItemFixedColumnRenderer") or {}).get("text") or {}
            for c in item.get("fixedColumns") or []]


def _meta(runs):
    """Artists, album, year and duration out of a "Coldplay • Parachutes •
    4:36" line, by what each run links to."""
    artists, artist_id, album, album_id, year, duration, kind = [], "", "", "", "", 0, ""
    for i, r in enumerate(runs):
        text = (r.get("text") or "").strip()
        if not text or text in ("•", "&", ","):
            continue
        pt = _page_type(r)
        bid = _browse_id(r.get("navigationEndpoint"))
        if pt == ARTIST or bid.startswith("UC"):
            artists.append(text)
            artist_id = artist_id or bid
        elif pt == ALBUM or bid.startswith("MPRE"):
            album, album_id = text, bid
        elif _DURATION.match(text):
            duration = _seconds(text)
        elif _YEAR.match(text):
            year = text
        elif i == 0 and text in ("Song", "Video", "Album", "Single", "EP", "Artist", "Playlist", "Episode"):
            kind = text
        elif not r.get("navigationEndpoint") and not artists and not re.search(r"\d", text):
            artists.append(text)        # an artist without a page
    return {"artists": artists, "artist_id": artist_id, "album": album, "album_id": album_id,
            "year": year, "duration": duration, "kind": kind}


def _track(**kw):
    from .music_sources import _track as make
    return make(**kw)


def _song(item, artist="", artist_id="", album="", album_id="", artwork=None, year=""):
    """A song row (search, an artist's top songs, an album's tracks) as a track."""
    cols = _flex(item)
    if not cols:
        return None
    title_runs = _runs(cols[0])
    title = _text(cols[0])
    vid = ((item.get("playlistItemData") or {}).get("videoId")
           or _first(title_runs[0].get("navigationEndpoint") if title_runs else {}, "videoId")
           or _first(item.get("overlay") or {}, "videoId"))
    if not vid or not title:
        return None
    m = {"artists": [], "artist_id": "", "album": "", "album_id": "", "year": "", "duration": 0}
    for col in cols[1:]:
        got = _meta(_runs(col))
        for k in ("artist_id", "album", "album_id", "year", "duration"):
            m[k] = m[k] or got[k]
        m["artists"] = m["artists"] or got["artists"]
    for col in _fixed(item):
        t = _text(col)
        if _DURATION.match(t):
            m["duration"] = m["duration"] or _seconds(t)
    return _track(
        id="yt:" + vid, title=title, artist=", ".join(m["artists"]) or artist, duration=m["duration"],
        artwork=sized(_thumb(item.get("thumbnail")), 544) or artwork, source="youtube",
        page_url="https://www.youtube.com/watch?v=%s" % vid, ext="m4a",
        album=m["album"] or album, album_browse=m["album_id"] or album_id,
        artist_browse=m["artist_id"] or artist_id, year=m["year"] or year)


def _album_from_row(item):
    cols = _flex(item)
    bid = _browse_id(item.get("navigationEndpoint"))
    if not cols or not bid.startswith("MPRE"):
        return None
    m = _meta(_runs(cols[1]) if len(cols) > 1 else [])
    return album_item(bid, _text(cols[0]), ", ".join(m["artists"]), m["artist_id"], m["year"],
                      m["kind"] or "Album", sized(_thumb(item.get("thumbnail")), 544))


def _artist_from_row(item):
    cols = _flex(item)
    bid = _browse_id(item.get("navigationEndpoint"))
    if not cols or not bid.startswith("UC"):
        return None
    sub = _text(cols[1]) if len(cols) > 1 else ""
    return artist_item(bid, _text(cols[0]), sized(_thumb(item.get("thumbnail")), 544),
                       sub.replace("Artist • ", "").replace("Artist", "").strip(" •"))


def album_item(browse_id, title, artist="", artist_id="", year="", kind="Album", artwork=None):
    return {"kind": "album", "id": "ytm:album:" + browse_id, "browse_id": browse_id, "title": title,
            "artist": artist, "artist_browse": artist_id, "year": year, "type": kind, "artwork": artwork,
            "source": "youtube"}


def artist_item(browse_id, name, artwork=None, subtitle=""):
    return {"kind": "artist", "id": "ytm:artist:" + browse_id, "browse_id": browse_id, "title": name,
            "artist": name, "artwork": artwork, "subtitle": subtitle, "source": "youtube"}


def _two_row(r):
    """A carousel tile: an album, a single, an artist (or a video / playlist,
    which the Music tab leaves out)."""
    title_runs = _runs(r.get("title"))
    nav = r.get("navigationEndpoint") or {}
    bid = _browse_id(nav) or (_browse_id(title_runs[0].get("navigationEndpoint")) if title_runs else "")
    art = _thumb(r.get("thumbnailRenderer"))
    title = _text(r.get("title"))
    if bid.startswith("MPRE"):
        m = _meta(_runs(r.get("subtitle")))
        return album_item(bid, title, ", ".join(m["artists"]), m["artist_id"], m["year"], m["kind"] or "Album",
                          sized(art, 544))
    if bid.startswith("UC"):
        return artist_item(bid, title, sized(art, 544), _text(r.get("subtitle")))
    return None


# ------------------------------------------------------------- search -----
def _songs_from(data):
    out = []
    for shelf in _walk(data, "musicShelfRenderer"):
        for c in shelf.get("contents") or []:
            item = c.get("musicResponsiveListItemRenderer")
            t = _song(item) if item else None
            if t:
                out.append(t)
    return out


def _rows_from(data, make):
    out = []
    for shelf in _walk(data, "musicShelfRenderer"):
        for c in shelf.get("contents") or []:
            item = c.get("musicResponsiveListItemRenderer")
            x = make(item) if item else None
            if x:
                out.append(x)
    return out


def _top_from(data):
    """The search's "top result" card: an artist, an album or a song."""
    card = _first(data, "musicCardShelfRenderer")
    if not card:
        return None
    title_runs = _runs(card.get("title"))
    if not title_runs:
        return None
    nav = title_runs[0].get("navigationEndpoint") or {}
    name = _text(card.get("title"))
    art = _thumb(card.get("thumbnail"))
    bid = _browse_id(nav)
    pt = _first(nav, "pageType") or ""
    if pt == ARTIST or bid.startswith("UC"):
        top = artist_item(bid, name, sized(art, 900), _text(card.get("subtitle")).replace("Artist • ", ""))
    elif pt == ALBUM or bid.startswith("MPRE"):
        m = _meta(_runs(card.get("subtitle")))
        top = album_item(bid, name, ", ".join(m["artists"]), m["artist_id"], m["year"], m["kind"] or "Album",
                         sized(art, 900))
    else:
        vid = _first(nav, "videoId")
        if not vid:
            return None
        m = _meta(_runs(card.get("subtitle")))
        top = _track(id="yt:" + vid, title=name, artist=", ".join(m["artists"]), duration=m["duration"],
                     artwork=sized(art, 900), source="youtube", page_url="https://www.youtube.com/watch?v=%s" % vid,
                     ext="m4a", album=m["album"], album_browse=m["album_id"], artist_browse=m["artist_id"])
    # the card's own few songs (an artist's best known), when it lists them
    songs = []
    for c in card.get("contents") or []:
        item = c.get("musicResponsiveListItemRenderer")
        t = _song(item) if item else None
        if t:
            songs.append(t)
    top["songs"] = songs
    return top


def search(query):
    """{"top": item or None, "songs": [track], "albums": [album], "artists": [artist]}
    -- the four requests the site makes, at once."""
    query = (query or "").strip()
    if not query:
        return {"top": None, "songs": [], "albums": [], "artists": []}
    asks = {"top": {"query": query}}
    for key, params in FILTERS.items():
        asks[key] = {"query": query, "params": params}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ytm-search") as pool:
        futures = {k: pool.submit(_post, "search", body) for k, body in asks.items()}
    answers, errors = {}, []
    for k, f in futures.items():
        try:
            answers[k] = f.result()
        except Exception as e:   # noqa: BLE001 -- one part missing still leaves the rest
            errors.append(e)
            logger.info("YouTube Music %s search failed: %s", k, e)
    if not answers:
        raise errors[0] if errors else YTMusicError("YouTube Music didn't answer")
    out = {
        "top": _top_from(answers["top"]) if "top" in answers else None,
        "songs": _songs_from(answers["songs"]) if "songs" in answers else [],
        "albums": _rows_from(answers["albums"], _album_from_row) if "albums" in answers else [],
        "artists": _rows_from(answers["artists"], _artist_from_row) if "artists" in answers else [],
    }
    if not out["songs"] and "top" in answers:
        out["songs"] = [t for t in _songs_from(answers["top"]) if t]
    return out


def suggestions(text):
    """What YouTube Music suggests while `text` is being typed:
    {"queries": [search text], "items": [songs, artists, albums]} -- the
    site's own as-you-type list (about a tenth of a second)."""
    text = (text or "").strip()
    if not text:
        return {"queries": [], "items": []}
    data = _post("music/get_search_suggestions", {"input": text}, timeout=8)
    queries, items, seen = [], [], set()
    for r in _walk(data, "searchSuggestionRenderer"):
        q = _first(r.get("navigationEndpoint") or {}, "query") or _text(r.get("suggestion"))
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)
    for r in _walk(data, "musicResponsiveListItemRenderer"):
        bid = _browse_id(r.get("navigationEndpoint"))
        if bid.startswith("UC"):
            x = _artist_from_row(r)
        elif bid.startswith("MPRE"):
            x = _album_from_row(r)
        elif not bid and _first(r, "videoId"):
            x = _song(r)
        else:
            x = None                # playlists: not opened here
        if x:
            items.append(x)
    return {"queries": queries[:7], "items": items[:5]}


# ------------------------------------------------------------- pages ------
def artist(browse_id):
    """An artist's page: name, banner photo, about, top songs, albums,
    singles and related artists."""
    data = _post("browse", {"browseId": browse_id})
    header = data.get("header") or {}
    h = next(iter(header.values()), {}) if header else {}
    name = _text(h.get("title"))
    banner = _thumb(h.get("thumbnail")) or _thumb(h.get("foregroundThumbnail"))
    about = _text(h.get("description"))
    if len(about) > 600:
        about = about[:600].rsplit(" ", 1)[0] + "…"
    out = {"kind": "artist", "id": "ytm:artist:" + browse_id, "browse_id": browse_id, "title": name,
           "artist": name, "artwork": banner, "about": about, "songs": [], "albums": [], "singles": [],
           "related": [], "source": "youtube"}
    contents = data.get("contents") or {}
    shelf = _first(contents, "musicShelfRenderer")
    if shelf:
        for c in shelf.get("contents") or []:
            item = c.get("musicResponsiveListItemRenderer")
            t = _song(item, artist=name, artist_id=browse_id) if item else None
            if t:
                out["songs"].append(t)
    for car in _walk(contents, "musicCarouselShelfRenderer"):
        head = _text(_first(car.get("header") or {}, "title") or {}).lower()
        items = [_two_row(c.get("musicTwoRowItemRenderer") or {}) for c in car.get("contents") or []]
        items = [x for x in items if x]
        if head.startswith("albums"):
            out["albums"] = [x for x in items if x["kind"] == "album"]
        elif head.startswith("singles"):
            out["singles"] = [x for x in items if x["kind"] == "album"]
        elif "might also like" in head or head.startswith("similar") or head.startswith("related"):
            out["related"] = [x for x in items if x["kind"] == "artist"]
    for a in out["albums"] + out["singles"]:
        a["artist"] = a["artist"] or name
        a["artist_browse"] = a["artist_browse"] or browse_id
    # a song's year, from the album it's on
    years = {a["title"]: a["year"] for a in out["albums"] + out["singles"] if a.get("year")}
    for t in out["songs"]:
        t["year"] = t.get("year") or years.get(t.get("album"), "")
    return out


def album(browse_id):
    """An album's page: title, artist, year, cover and its tracks in order."""
    data = _post("browse", {"browseId": browse_id})
    h = _first(data, "musicResponsiveHeaderRenderer") or _first(data, "musicDetailHeaderRenderer") or {}
    title = _text(h.get("title"))
    strap = _runs(h.get("straplineTextOne"))
    artist_name = ", ".join(r.get("text", "") for r in strap if r.get("text", "").strip() not in ("", "•", "&", ","))
    artist_id = next((_browse_id(r.get("navigationEndpoint")) for r in strap if r.get("navigationEndpoint")), "")
    m = _meta(_runs(h.get("subtitle")))
    cover = sized(_thumb(h.get("thumbnail")), 900)
    out = album_item(browse_id, title, artist_name or ", ".join(m["artists"]), artist_id or m["artist_id"],
                     m["year"], m["kind"] or "Album", cover)
    out["tracks"] = []
    shelf = _first(data.get("contents") or {}, "musicShelfRenderer")
    for c in (shelf or {}).get("contents") or []:
        item = c.get("musicResponsiveListItemRenderer")
        t = _song(item, artist=out["artist"], artist_id=out["artist_browse"], album=title, album_id=browse_id,
                  artwork=cover, year=out["year"]) if item else None
        if t:
            t["artwork"] = cover      # an album's tracks are listed without covers of their own
            out["tracks"].append(t)
    out["length"] = sum(t["duration"] for t in out["tracks"])
    return out
