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
import os
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
    short = False
    if m.group(1) == "playlist" and len(items) >= 100:
        # the embed page lists the first hundred only: the rest, a hundred at a time, through the app's server
        more, short = _spotify_rest(m.group(2), len(items))
        items += more
    return {"title": ent.get("name") or ent.get("title") or "Spotify playlist", "source": "Spotify",
            "items": items, "tracks": [], "short": short}


def _spotify_rest(pid, have):
    """The songs of a Spotify playlist after the first `have`, from the app's server (Spotify's API, its key kept
    there): ([{"title", "artist", "duration"}], short) -- short when there were more that couldn't be read."""
    base = os.environ.get("AWESOME_MUSIC_API", "").rstrip("/")
    if not base:
        return [], True
    out, offset = [], have
    while offset < 3000:
        try:
            data = json.loads(_get("%s/v1/spotify/playlist?id=%s&offset=%d" % (base, pid, offset), timeout=20))
        except Exception:   # noqa: BLE001 -- not set up there, or too many: what there is
            return out, True
        got = [t for t in data.get("tracks") or [] if t.get("title")]
        out += got
        total = data.get("total")
        if len(got) < 100 or (total and offset + len(got) >= total):
            return out, False
        offset += len(got)
    return out, False


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


def from_videos(videos):
    """YouTube Music's own list ([{"video_id", "title", "channel", "duration", "is_song"}]) as tracks: (tracks, the ids
    that are songs already, not music videos)."""
    from .music_sources import _track
    tracks, songs = [], []
    for v in videos:
        vid, channel = v["video_id"], v.get("channel") or ""
        if v.get("is_song") or channel.endswith(" - Topic"):
            songs.append("yt:" + vid)
        tracks.append(_track(id="yt:" + vid, title=v.get("title") or "", source="youtube", artist=channel.removesuffix(" - Topic"),
                             duration=int(v.get("duration") or 0), artwork="https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid,
                             page_url="https://www.youtube.com/watch?v=%s" % vid, ext="m4a"))
    return tracks, songs


def _youtube(url):
    from . import downloader
    from .music_sources import _anonymous, _track
    opts = _anonymous(downloader, url)
    opts.update({"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False, "playlistend": 5000})
    info = downloader._run(opts, url, lambda ydl: ydl.extract_info(url, download=False))
    tracks = []
    songs = []     # already the song itself: on an artist's "- Topic" channel
    for e in info.get("entries") or []:
        if e and e.get("id"):
            if (e.get("channel") or e.get("uploader") or "").endswith(" - Topic"):
                songs.append("yt:" + e["id"])
            tracks.append(_track(id="yt:" + e["id"], title=e.get("title") or "", source="youtube",
                                 artist=(e.get("channel") or e.get("uploader") or "").removesuffix(" - Topic"),
                                 duration=int(e.get("duration") or 0),
                                 artwork="https://i.ytimg.com/vi/%s/hqdefault.jpg" % e["id"],
                                 page_url="https://www.youtube.com/watch?v=%s" % e["id"], ext="m4a"))
    if not tracks:
        raise ImportError_("That YouTube playlist is empty, or private.")
    return {"title": info.get("title") or "YouTube playlist", "source": "YouTube", "items": [], "tracks": tracks,
            "songs": songs}


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


# words around a song's name in a video's title
_VIDEO_WORDS = {"official", "full", "hd", "4k", "video", "audio", "lyrical", "lyric", "lyrics", "song", "songs", "music",
                "new", "latest", "hindi", "punjabi", "tamil", "telugu", "bollywood"}
# a video that is its own thing, not a copy of the song: it stays as it is
_NOT_THE_SONG = re.compile(r"\b(remix|cover|live|unplugged|mashup|medley|jukebox|non[- ]?stop|nonstop|lo-?fi|slowed|reverb|"
                           r"reprise|instrumental|karaoke|teaser|trailer|reaction|dance|tutorial|8d)\b", re.I)


def video_song(title):
    """What a video's title says: (the names the song may go by, the singer if it's given, what else it mentions).
    "Arijit Singh: Pachtaoge | Vicky Kaushal" -> (["pachtaoge"], "Arijit Singh", "Vicky Kaushal")."""
    parts = [p for p in re.split(r"\s*(?:\|+|\uff5c)\s*", title or "") if p.strip()]
    head = parts[0] if parts else ""
    # "Song (Official Video): the cast" -- what follows such a tag isn't the song's name
    tag = re.search(r"[\(\[][^\)\]]*\b(song|video|audio|lyric\w*|visuali[sz]er)\b[^\)\]]*[\)\]]", head, re.I)
    if tag and tag.start() > 1:
        head = head[:tag.start()]
    head = re.sub(r"\(.*?\)|\[.*?\]", " ", head)
    head = re.sub(r"[\"\u201c\u201d]", " ", head).strip()
    singer = ""
    m = re.match(r"^([^:]{2,40}):\s*(.+)$", head)
    if m:
        before = re.sub(r"\bversion\b", " ", m.group(1), flags=re.I).strip()
        if not all(w in _VIDEO_WORDS for w in before.lower().split()):
            singer = before
        head = m.group(2)

    def bare(text):
        words = text.split()
        while len(words) > 1 and words[-1].lower() in _VIDEO_WORDS:
            words.pop()
        while len(words) > 1 and words[0].lower() in _VIDEO_WORDS:
            words.pop(0)
        return " ".join(words).strip()
    names = [bare(head).lower()]
    for dash in (" - ", " \u2013 ", " \u2014 "):
        if dash in head:     # "Singer - Song", or the other way round
            a, b = head.split(dash, 1)
            names = [bare(b).lower(), bare(a).lower()]
            break
    return [n for n in names if n], singer, parts[1].strip() if len(parts) > 1 else ""


def _song_for_video(t):
    """The song a video is a copy of, or None unless it's clearly that song: a video is better than the wrong song."""
    from . import ytmusic
    title = t.get("title") or ""
    if _NOT_THE_SONG.search(title):
        return None
    names, singer, more = video_song(title)
    if not names:
        return None
    channel = (t.get("artist") or "").lower()
    said = set(re.findall(r"\w+", title.lower()))
    duration = t.get("duration") or 0
    best, best_v = None, 0.0
    for s in ytmusic.search_songs(" ".join(x for x in (names[0], singer, more) if x))[:6]:
        name = re.sub(r"\(.*?\)|\[.*?\]", "", s.get("title") or "").lower().strip()
        artist = (s.get("artist") or "").lower()
        if not name or not artist or _NOT_THE_SONG.search(s.get("title") or ""):
            continue
        close = max(_close(n, name) for n in names)
        if len(name.split()) >= 2 and any(set(name.split()) <= set(n.split()) for n in names):
            close = max(close, 0.9)      # all of its name, with a film's name after it, say
        credited = [a.split()[0] for a in artist.split(",") if a.split()]
        known = any(len(w) > 2 and w in said for w in credited) or \
            (bool(singer) and any(w in artist for w in singer.lower().split() if len(w) > 2)) or \
            any(a.strip().replace(" ", "") in channel.replace(" ", "") for a in artist.split(",") if len(a.strip()) > 2) or \
            (bool(names[1:]) and any(n in artist or artist in n for n in names))
        # the film may come after the song's name, or before it ("Baaghi 2: Lo Safar"), where a singer also goes
        about = ("%s %s" % (s.get("title") or "", s.get("album") or "")).lower()
        film = any(len(x) > 2 and x.lower() in about for x in (more, singer))
        # or the title names its album: "Aashiqui 2", or the "Bharat" of 'Chashni (From "Bharat")'
        for x in [re.sub(r"\(.*?\)|\[.*?\]", "", s.get("album") or "")] + re.findall(r'"([^"]+)"', s.get("title") or ""):
            x = set(re.findall(r"\w+", x.lower()))
            if x and x != set(re.findall(r"\w+", name)) and x <= said:
                film = True
        # the title says who's in it and this isn't any of them: the same name, by someone else
        if more and not known and not film:
            continue
        gap = (s.get("duration") or 0) - duration if duration and s.get("duration") else 0
        # its name and who sang it (or its film): the song, however long the video runs. Otherwise the length has to
        # agree too -- a video runs longer than its song (a story around it), not shorter -- and with no word of who
        # sang it, only the very same name will do
        if close < (0.72 if known or film else 0.9):
            continue
        if not (close >= 0.9 and (known or film)) and (gap > 25 or gap < (-180 if known else -120)):
            continue
        v = close + (0.3 if known else 0) + (0.2 if film else 0)
        if v > best_v:
            best, best_v = s, v
    return best


def prefer_songs(tracks, songs=(), progress=None, workers=6):
    """A playlist's music videos as the songs they are, with the song's own name, singer and cover; the rest as they
    were, in order, each once. `songs`: the ids that are songs already."""
    songs = set(songs or ())
    done = [0]

    def one(t):
        found = None
        if t.get("id") not in songs:
            try:
                found = _song_for_video(t)
            except Exception:   # noqa: BLE001 -- one that can't be looked up stays a video
                logger.info("No song for %r", t.get("title"), exc_info=True)
        done[0] += 1
        if progress:
            progress(done[0], len(tracks))
        return found or t
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out, seen = [], set()
        for t in pool.map(one, tracks):
            if t.get("id") not in seen:
                seen.add(t.get("id"))
                out.append(t)
    return out


def match(items, progress=None, workers=6):
    """Each {"title", "artist"} -> YouTube Music's best song for it (or None),
    in order. `progress(done, total)` as they come."""
    from . import ytmusic
    done = [0]

    def one(it):
        q = ("%s %s" % (it["title"], it.get("artist", ""))).strip()
        best = None
        try:
            songs = ytmusic.search_songs(q)[:6]

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
            if best is None and it.get("artist"):
                # once more by the name alone, with only the first singer (a long credit list confuses the search)
                first = re.split(r",|&| x | feat", it["artist"], flags=re.I)[0].strip()
                more = ytmusic.search_songs(("%s %s" % (re.sub(r"\(.*?\)|\[.*?\]", "", it["title"]), first)).strip())[:6]
                scored = sorted(more, key=lambda s: -fit(s))
                if scored and _close(it["title"], scored[0]["title"]) >= 0.40:
                    best = scored[0]
        except Exception:   # noqa: BLE001 -- one song not found leaves the rest
            logger.info("No match for %r", q, exc_info=True)
        done[0] += 1
        if progress:
            progress(done[0], len(items))
        return best

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, items))
