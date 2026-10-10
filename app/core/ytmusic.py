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
import threading
import time
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


_gate = threading.BoundedSemaphore(4)
_cool = {"until": 0.0}
COOL_S = 20                  # after a 403 / 429, what can wait does


def _post(endpoint, body, timeout=15, background=False):
    """One InnerTube request. At most four at once; after YouTube Music
    answers "too many" (403 / 429), `background` requests -- covers, tuned
    radios, things looked up ahead -- wait it out instead of adding to it."""
    if background and time.time() < _cool["until"]:
        raise YTMusicError("YouTube Music asked for a pause")
    with _gate:
        try:
            return _post_now(endpoint, body, timeout)
        except YTMusicError as e:
            if "403" in str(e) or "429" in str(e):
                _cool["until"] = time.time() + COOL_S
            raise


def _post_now(endpoint, body, timeout):
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
    if url and "mzstatic.com" in url:
        # Apple's covers: the size is in the name ("600x600bb.jpg")
        return re.sub(r"/\d+x\d+(bb|cc|sr)?\.(jpg|png|webp)$", "/%dx%dbb.jpg" % (px, height or px), url)
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


def _queue_tracks(data):
    out = []
    for r in _walk(data, "playlistPanelVideoRenderer"):
        vid = r.get("videoId")
        title = _text(r.get("title"))
        if not vid or not title:
            continue
        m = _meta(_runs(r.get("longBylineText")))
        length = _text(r.get("lengthText"))
        out.append(_track(
            id="yt:" + vid, title=title, artist=", ".join(m["artists"]), artist_browse=m["artist_id"],
            album=m["album"], album_browse=m["album_id"], year=m["year"],
            duration=_seconds(length) if _DURATION.match(length or "") else 0,
            artwork=sized(_thumb(r.get("thumbnail")), 544), source="youtube",
            page_url="https://www.youtube.com/watch?v=%s" % vid, ext="m4a"))
    return out


# the radio's own chips that aren't about the song
_PLAIN_CHIPS = {"all", "save", "popular", "discover", "deep cuts", "familiar"}


def radio_full(video_id, tune=None):
    """YouTube Music's radio for a song -- about fifty songs like it, the
    first the song itself -- and the chips the site tunes it with: the
    song's own moods, languages and eras ("Romance", "Chill", "Hindi",
    "2010s", "Upbeat"...), each with what asks for that tuned radio.
    `tune`: one of those, to get that tuned radio instead."""
    body = {"videoId": video_id, "playlistId": "RDAMVM" + video_id, "isAudioOnly": True}
    if tune:
        body["playlistId"] = tune["playlistId"]
        if tune.get("params"):
            body["params"] = tune["params"]
    data = _post("next", body, background=bool(tune))
    chips = {}
    for c in _walk(data, "chipCloudChipRenderer"):
        label = _text(c.get("text"))
        we = _first(c.get("navigationEndpoint") or {}, "watchEndpoint")
        if label and we and we.get("playlistId") and label.lower() not in _PLAIN_CHIPS:
            chips[label] = {"playlistId": we["playlistId"], "params": we.get("params")}
    return {"tracks": _queue_tracks(data), "chips": chips}


def radio(video_id):
    """YouTube Music's radio for a song: about fifty songs like it, the way
    the site's "Start radio" plays them. The first is the song itself."""
    return radio_full(video_id)["tracks"]


# ------------------------------------------------------------- explore -----
def playlist_item(browse_id, title, subtitle="", artwork=None):
    return {"kind": "ytplaylist", "id": "ytm:pl:" + browse_id, "browse_id": browse_id, "title": title,
            "subtitle": subtitle, "artist": subtitle, "artwork": artwork, "source": "youtube"}


def _tile(r):
    """A carousel's tile: an album, an artist -- or a playlist (a chart)."""
    found = _two_row(r)
    if found:
        return found
    nav = r.get("navigationEndpoint") or {}
    title_runs = _runs(r.get("title"))
    bid = _browse_id(nav) or (_browse_id(title_runs[0].get("navigationEndpoint")) if title_runs else "")
    if bid.startswith(("VL", "RDCLAK", "PL")):
        return playlist_item(bid if bid.startswith("VL") else "VL" + bid, _text(r.get("title")),
                             _text(r.get("subtitle")), sized(_thumb(r.get("thumbnailRenderer")), 544))
    return None


def _shelves(data):
    """Every carousel and grid on a browse page: [{"title", "items"}]."""
    out = []
    for sh in _walk(data, "musicCarouselShelfRenderer"):
        head = _text(_first(sh.get("header") or {}, "title"))
        items = []
        for it in sh.get("contents") or []:
            if it.get("musicTwoRowItemRenderer"):
                x = _tile(it["musicTwoRowItemRenderer"])
            elif it.get("musicResponsiveListItemRenderer"):
                row = it["musicResponsiveListItemRenderer"]
                x = _artist_from_row(row) or _song(row)
            else:
                x = None
            if x:
                items.append(x)
        if items:
            out.append({"title": head, "items": items})
    for grid in _walk(data, "gridRenderer"):
        head = _text(_first(grid.get("header") or {}, "title"))
        items = [_tile(it["musicTwoRowItemRenderer"]) for it in grid.get("items") or []
                 if it.get("musicTwoRowItemRenderer")]
        items = [x for x in items if x]
        if items:
            out.append({"title": head, "items": items})
    return out


def charts(country="IN"):
    """YouTube Music's charts for a country: trending and top songs (as
    playlists), the charts by language, and the top artists."""
    return _shelves(_post("browse", {"browseId": "FEmusic_charts", "formData": {"selectedValues": [country]}}))


def moods():
    """{name: browse endpoint} for every mood and genre the site lists --
    "Romance", "Sad", "Party", "Hindi", "Indian indie", "Punjabi"..."""
    data = _post("browse", {"browseId": "FEmusic_moods_and_genres"})
    out = {}
    for b in _walk(data, "musicNavigationButtonRenderer"):
        name = _text(b.get("buttonText"))
        ep = _first(b.get("clickCommand") or {}, "browseEndpoint")
        if name and ep and ep.get("params"):
            out[name] = {"browseId": ep.get("browseId"), "params": ep["params"]}
    return out


def category(endpoint):
    """A mood's or genre's page: its shelves of playlists."""
    return _shelves(_post("browse", {"browseId": endpoint["browseId"], "params": endpoint["params"]}))


def new_releases():
    """New albums and singles, as the site's "New releases" lists them."""
    shelves = _shelves(_post("browse", {"browseId": "FEmusic_new_releases_albums"}))
    return [x for sh in shelves for x in sh["items"] if x.get("kind") == "album"]


def playlist(browse_id):
    """A public playlist's name and songs (a chart, a mood's mix)."""
    if not browse_id.startswith("VL"):
        browse_id = "VL" + browse_id
    data = _post("browse", {"browseId": browse_id})
    h = _first(data, "musicResponsiveHeaderRenderer") or _first(data, "musicDetailHeaderRenderer") or {}
    tracks = []
    for key in ("musicPlaylistShelfRenderer", "musicShelfRenderer"):
        for shelf in _walk(data, key):
            for c in shelf.get("contents") or []:
                item = c.get("musicResponsiveListItemRenderer")
                t = _song(item) if item else None
                if t and not any(x["id"] == t["id"] for x in tracks):
                    tracks.append(t)
        if tracks:
            break
    return {"title": _text(h.get("title")), "subtitle": _text(h.get("subtitle")),
            "artwork": sized(_thumb(h.get("thumbnail")), 900), "tracks": tracks}
