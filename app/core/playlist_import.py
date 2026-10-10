"""Playlists brought over from other apps.

A link to a public playlist (or album) on Spotify, Apple Music, YouTube or
YouTube Music -- or a pasted list, one song per line ("Artist - Song"), or
a CSV exported from Spotify (Exportify, TuneMyMusic and the like) -- is read
into song titles and artists, without signing in to anything. YouTube's
songs play as they are; every other song is matched to YouTube Music's best
answer for its title and artist (and dropped if nothing close comes back).
"""
import csv
import difflib
import html
import io
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..logging_setup import get_logger

logger = get_logger("playlist_import")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
SOURCES = ("Spotify", "Apple Music", "YouTube Music", "YouTube", "A list or CSV")


class ImportError_(Exception):
    pass


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def kind_of(text):
    t = (text or "").strip()
    low = t.lower()
    if "open.spotify.com/" in low:
        return "spotify"
    if "music.apple.com/" in low:
        return "apple"
    if ("youtube.com/" in low or "youtu.be/" in low) and "list=" in low:
        return "youtube"
    return "text"


def read(text):
    """{"title", "source", "items": [{"title", "artist"}], "tracks": [ready tracks]}"""
    kind = kind_of(text)
    if kind == "spotify":
        return _spotify(text.strip())
    if kind == "apple":
        return _apple(text.strip())
    if kind == "youtube":
        return _youtube(text.strip())
    items = parse_text(text)
    if not items:
        raise ImportError_("Paste a playlist link, or songs one per line as \"Artist - Song\".")
    return {"title": "Imported playlist", "source": "list", "items": items, "tracks": []}


def _spotify(url):
    m = re.search(r"open\.spotify\.com/(?:intl-\w+/)?(playlist|album)/([A-Za-z0-9]+)", url)
    if not m:
        raise ImportError_("That Spotify link isn't a playlist or an album.")
    page = _get("https://open.spotify.com/embed/%s/%s" % m.groups())
    data = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not data:
        raise ImportError_("Spotify didn't show that playlist -- is it public?")
    ent = (((json.loads(data.group(1)).get("props") or {}).get("pageProps") or {}).get("state") or {}).get(
        "data", {}).get("entity") or {}
    items = [{"title": t.get("title", ""), "artist": t.get("subtitle", "")} for t in ent.get("trackList") or []
             if t.get("title")]
    if not items:
        raise ImportError_("That Spotify playlist is empty, or private.")
    return {"title": ent.get("name") or ent.get("title") or "Spotify playlist", "source": "Spotify",
            "items": items, "tracks": []}


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _apple(url):
    """Apple Music's page carries its own data (the "serialized-server-data"
    script): a header with the playlist's (or album's) name, and a row --
    a "track-lockup" -- per song, with its title, artist, album and length.
    Shared personal playlists (pl.u-...) read the same way as editorial ones."""
    page = _get(url)
    items, title, seen = [], "", set()
    m = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        for o in _walk(data):
            oid = str(o.get("id") or "")
            if not title and oid.startswith(("playlist-detail-header", "album-detail-header", "container-detail-header")):
                title = o.get("title") or ""
            if oid.startswith("track-lockup") and o.get("title") and oid not in seen:
                seen.add(oid)
                artist = o.get("artistName") or ""
                if not artist:
                    links = o.get("subtitleLinks") or []
                    artist = ", ".join(x.get("title", "") for x in links if isinstance(x, dict))
                album = ""
                for x in o.get("tertiaryLinks") or []:
                    if isinstance(x, dict) and x.get("title"):
                        album = x["title"]
                        break
                dur = o.get("duration")
                items.append({"title": html.unescape(o["title"]), "artist": html.unescape(artist),
                              "album": html.unescape(album),
                              "duration": int(dur / 1000) if isinstance(dur, (int, float)) and dur > 1000 else 0})
    if not items:
        # older pages: schema.org's description of the playlist
        for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            title = title or data.get("name", "")
            for t in data.get("track") or []:
                if isinstance(t, dict) and t.get("name"):
                    by = t.get("byArtist")
                    artist = (by[0] if isinstance(by, list) and by else by or {}).get("name", "") \
                        if isinstance(by, (dict, list)) else ""
                    items.append({"title": html.unescape(t["name"]), "artist": html.unescape(artist)})
    if not items:
        raise ImportError_("Apple Music didn't show that playlist's songs. If it's your own, open it in Apple "
                           "Music, choose Share → Copy Link (that makes it public), and paste that link.")
    title = re.sub(r"^[^\w(]+", "", html.unescape(title)).strip()       # an emoji in front of the name
    return {"title": title or "Apple Music playlist", "source": "Apple Music", "items": items, "tracks": []}


def _youtube(url):
    from . import downloader
    from .music_sources import _anonymous, _track
    opts = _anonymous(downloader, url)
    opts.update({"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False, "playlistend": 500})
    info = downloader._run(opts, url, lambda ydl: ydl.extract_info(url, download=False))
    tracks = []
    for e in info.get("entries") or []:
        if e and e.get("id"):
            tracks.append(_track(id="yt:" + e["id"], title=e.get("title") or "", source="youtube",
                                 artist=(e.get("channel") or e.get("uploader") or "").removesuffix(" - Topic"),
                                 duration=int(e.get("duration") or 0),
                                 artwork="https://i.ytimg.com/vi/%s/hqdefault.jpg" % e["id"],
                                 page_url="https://www.youtube.com/watch?v=%s" % e["id"], ext="m4a"))
    if not tracks:
        raise ImportError_("That YouTube playlist is empty, or private.")
    return {"title": info.get("title") or "YouTube playlist", "source": "YouTube", "items": [], "tracks": tracks}


def parse_text(text):
    """Songs from a pasted list ("Artist - Song", "Song by Artist", a bare
    title) or a CSV with title / artist columns."""
    text = (text or "").strip()
    if not text:
        return []
    first = text.splitlines()[0].lower()
    if "," in first and any(k in first for k in ("track", "title", "song", "name")):
        rows = list(csv.DictReader(io.StringIO(text)))
        out = []
        for r in rows:
            low = {k.lower().strip(): (v or "") for k, v in r.items() if k}
            title = next((low[k] for k in low if k in ("track name", "title", "song", "name", "track")), "")
            artist = next((low[k] for k in low if "artist" in k), "")
            if title:
                out.append({"title": title.strip(), "artist": artist.split(";")[0].split(",")[0].strip()})
        return out
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(\d+[.)]\s*)?", "", line).strip()
        if not line or line.startswith("#"):
            continue
        for dash in (" - ", " – ", " — "):
            if dash in line:
                artist, title = line.split(dash, 1)
                out.append({"title": title.strip(), "artist": artist.strip()})
                break
        else:
            m = re.match(r"(.+?)\s+by\s+(.+)$", line, re.I)
            out.append({"title": m.group(1).strip(), "artist": m.group(2).strip()} if m
                       else {"title": line, "artist": ""})
    return out


def _close(a, b):
    a, b = re.sub(r"\(.*?\)|\[.*?\]", "", a or "").lower().strip(), re.sub(r"\(.*?\)|\[.*?\]", "", b or "").lower()
    return difflib.SequenceMatcher(None, a, b.strip()).ratio()


def match(items, progress=None, workers=6):
    """Each {"title", "artist"} -> YouTube Music's best song for it (or None),
    in order. `progress(done, total)` as they come."""
    from . import ytmusic
    done = [0]

    def one(it):
        q = ("%s %s" % (it["title"], it.get("artist", ""))).strip()
        best = None
        try:
            songs = ytmusic.search(q)["songs"][:6]

            def fit(s):
                v = _close(it["title"], s["title"]) * 2 + (_close(it.get("artist"), s["artist"])
                                                           if it.get("artist") else 0.5)
                if it.get("duration") and s.get("duration"):
                    # the same recording runs the same length: a remix or a live take doesn't
                    v -= min(1.0, abs(it["duration"] - s["duration"]) / 40.0)
                return v
            scored = sorted(songs, key=lambda s: -fit(s))
            if scored and _close(it["title"], scored[0]["title"]) >= 0.45:
                best = scored[0]
        except Exception:   # noqa: BLE001 -- one song not found leaves the rest
            logger.info("No match for %r", q, exc_info=True)
        done[0] += 1
        if progress:
            progress(done[0], len(items))
        return best

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, items))
