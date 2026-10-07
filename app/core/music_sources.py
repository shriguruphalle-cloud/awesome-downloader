"""Where the Music tab's songs come from.

One search asks every library at once (search_everywhere), and each answers
into its own part of the results as soon as it can:

  "music" -- YouTube Music, read the way music.youtube.com reads it
    (ytmusic.py): songs with their album covers, albums and artists, and a
    top result. If that can't be read, yt-dlp's plain YouTube search stands in.
  "free"  -- Openverse, the WordPress Foundation's open-source search of openly
    licensed media: Creative Commons songs from Jamendo, ccMixter, Freesound
    and Wikimedia, each with its licence and credit -- free to keep.
  "live"  -- the Internet Archive's netlabel releases (Creative Commons) and
    the Live Music Archive (bands that allow trading their live shows).

A track is a plain dict:
  kind ("track"), id, title, artist, duration (s), artwork (url), source,
  page_url, stream (a playable url or local path; None until resolved), ext,
  license, license_url, attribution, album, album_browse, artist_browse, year,
  album_id (an Internet Archive item, opened into its tracks when played)
Albums and artists (ytmusic.album_item / artist_item) carry kind "album" /
"artist" and are opened, not played.
"""
import glob
import html
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from ..logging_setup import get_logger

logger = get_logger("music")

UA = {"User-Agent": "AwesomeDownloader/2.5 (+https://awesome-downloader.pages.dev)"}
OPENVERSE = "https://api.openverse.org/v1/audio/"
ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_COLLECTIONS = "collection:(netlabels OR etree OR audio_music)"
JPEG_MAGIC = bytes((0xFF, 0xD8, 0xFF))
AUDIO_EXT = (".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac", ".wma", ".webm")

# The parts of one search, in the order the results page shows them.
PARTS = ("music", "free", "live")

# Songs fetched to play when a stream won't open directly (cache_audio), kept
# to this many before the oldest go.
CACHE_DIR = os.path.join(config.APPDATA_DIR, "music-cache")
CACHE_KEEP = 40


class MusicError(Exception):
    pass


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise MusicError("Openverse's free searches are used up for now (200 a day without an "
                             "account) -- the Internet Archive and YouTube Music still answer.")
        raise


def _track(**kw):
    t = {"kind": "track", "id": "", "title": "", "artist": "", "duration": 0, "artwork": None, "source": "",
         "page_url": "", "stream": None, "ext": "mp3", "license": "", "license_url": "", "attribution": "",
         "album": "", "album_browse": "", "artist_browse": "", "year": "", "album_id": None}
    t.update(kw)
    return t


def _short(exc):
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:200]


# ------------------------------------------------------------------ search
def search_everywhere(query, deliver):
    """Asks every library at once. deliver(part, value, error) is called from
    a worker thread as each answers -- "music" with YouTube Music's
    {"top", "songs", "albums", "artists"}, "free" and "live" with lists --
    and error is "" or what went wrong. Returns the threads."""
    query = (query or "").strip()
    jobs = {"music": lambda: search_music(query), "free": lambda: search_openverse(query),
            "live": lambda: search_archive(query)}
    threads = []
    for part in PARTS:
        def run(part=part):
            try:
                deliver(part, jobs[part](), "")
            except Exception as e:   # noqa: BLE001 -- one library down leaves the others
                logger.info("Music search (%s) failed: %s", part, _short(e))
                deliver(part, None, _short(e))
        t = threading.Thread(target=run, daemon=True, name="music-search-" + part)
        t.start()
        threads.append(t)
    return threads


def search_music(query):
    """YouTube Music's answer; yt-dlp's YouTube search when that can't be read."""
    from . import ytmusic
    try:
        found = ytmusic.search(query)
        if found["songs"] or found["albums"] or found["artists"]:
            return found
    except Exception as e:   # noqa: BLE001
        logger.warning("YouTube Music search failed (%s); using YouTube's", _short(e))
    return {"top": None, "songs": search_youtube(query), "albums": [], "artists": []}


def search(query, source, page=1):
    """One library's songs (the universal search uses search_everywhere)."""
    query = (query or "").strip()
    if not query:
        return []
    if source == "openverse":
        return search_openverse(query, page)
    if source == "archive":
        return search_archive(query, page)
    if source == "youtube":
        return search_music(query)["songs"]
    raise ValueError(source)


def _license_name(code, version):
    code = (code or "").lower()
    if code in ("cc0", "pdm"):
        return "Public domain" if code == "pdm" else "CC0"
    return ("CC %s %s" % (code.upper(), version or "")).strip()


def search_openverse(query, page=1):
    q = urllib.parse.urlencode({"q": query, "page_size": 20, "page": page, "category": "music"})  # 20: the most a signed-out search may ask for
    data = _get_json(OPENVERSE + "?" + q)
    out = []
    for r in data.get("results") or []:
        if not r.get("url") or r.get("mature"):
            continue
        ext = (r.get("filetype") or "mp3").lower()
        ext = "mp3" if ext.startswith("mp3") else ext
        out.append(_track(
            id="ov:" + r["id"], title=html.unescape(r.get("title") or "Untitled"),
            artist=html.unescape(r.get("creator") or ""),
            duration=int((r.get("duration") or 0) / 1000), artwork=r.get("thumbnail"), source="openverse",
            page_url=r.get("foreign_landing_url") or "", stream=r["url"], ext=ext,
            license=_license_name(r.get("license"), r.get("license_version")),
            license_url=r.get("license_url") or "", attribution=html.unescape(r.get("attribution") or "")))
    return out


def search_archive(query, page=1):
    """Albums and live shows: each result is an item, its tracks read when
    it's opened or played (expand_archive)."""
    q = urllib.parse.urlencode({
        "q": "(%s) AND mediatype:(audio) AND %s" % (query, ARCHIVE_COLLECTIONS),
        "fl[]": ["identifier", "title", "creator", "licenseurl", "year"],
        "rows": 30, "page": page, "sort[]": "downloads desc", "output": "json"}, doseq=True)
    data = _get_json(ARCHIVE_SEARCH + "?" + q)
    out = []
    for d in (data.get("response") or {}).get("docs") or []:
        ident = d.get("identifier")
        if not ident:
            continue
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = ", ".join(creator[:2])
        lic = d.get("licenseurl") or ""
        out.append(_track(
            kind="album", id="ia:" + ident, title=d.get("title") or ident, artist=creator or "",
            artwork="https://archive.org/services/img/%s" % ident, source="archive",
            page_url="https://archive.org/details/%s" % ident, album_id=ident, year=str(d.get("year") or ""),
            license=_archive_license(lic), license_url=lic))
    return out


def _archive_license(url):
    u = (url or "").lower()
    if "publicdomain" in u:
        return "Public domain"
    if "creativecommons.org/licenses/" in u:
        parts = u.split("/licenses/")[1].strip("/").split("/")
        return ("CC %s %s" % (parts[0].upper(), parts[1] if len(parts) > 1 else "")).strip()
    return ""


def expand_archive(track):
    """An Internet Archive item -> its audio files, in order, one track each."""
    ident = track["album_id"]
    meta = _get_json("https://archive.org/metadata/%s" % urllib.parse.quote(ident))
    files = meta.get("files") or []
    # one encoding of each recording: MP3 first (it plays everywhere), else the originals
    chosen = [f for f in files if f.get("name", "").lower().endswith(".mp3")]
    if not chosen:
        chosen = [f for f in files if f.get("name", "").lower().endswith(AUDIO_EXT)]
    album_artist = (meta.get("metadata") or {}).get("creator") or track["artist"]
    if isinstance(album_artist, list):
        album_artist = ", ".join(album_artist[:2])
    out = []
    for f in sorted(chosen, key=lambda f: (str(f.get("track") or "").zfill(4), f.get("name", ""))):
        name = f["name"]
        dur = _length_s(f.get("length"))
        out.append(_track(
            id="ia:%s/%s" % (ident, name), title=f.get("title") or os.path.splitext(os.path.basename(name))[0],
            artist=f.get("artist") or f.get("creator") or album_artist, duration=dur, artwork=track["artwork"],
            source="archive", page_url=track["page_url"], ext=os.path.splitext(name)[1].lstrip(".").lower(),
            stream="https://archive.org/download/%s/%s" % (ident, urllib.parse.quote(name)),
            license=track["license"], license_url=track["license_url"], album=track["title"],
            year=track.get("year") or "",
            attribution="%s -- %s (archive.org/details/%s)" % (f.get("title") or name, album_artist, ident)))
    return out


def album_tracks(item):
    """An album's tracks, whichever library it's from."""
    if item.get("source") == "archive":
        return expand_archive(item)
    from . import ytmusic
    return ytmusic.album(item["browse_id"])["tracks"]


def _length_s(value):
    """The Archive writes lengths as seconds ("214.5") or as m:ss / h:mm:ss."""
    text = str(value or "").strip()
    try:
        if ":" in text:
            return int(sum(float(x) * 60 ** i for i, x in enumerate(reversed(text.split(":")))))
        return int(float(text or 0))
    except ValueError:
        return 0


def search_youtube(query):
    """yt-dlp's plain YouTube search, for when YouTube Music can't be read."""
    from . import downloader
    url = "ytsearch25:%s" % query
    opts = _anonymous(downloader, "https://www.youtube.com/")
    opts.update({"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False})
    info = downloader._run(opts, url, lambda ydl: ydl.extract_info(url, download=False))
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        dur = int(e.get("duration") or 0)
        if not dur or dur > 20 * 60 or e.get("live_status") in ("is_live", "is_upcoming"):
            continue                     # live radios, premieres and hour-long mixes aren't songs
        artist = (e.get("channel") or e.get("uploader") or "").removesuffix(" - Topic")
        out.append(_track(
            id="yt:" + e["id"], title=e.get("title") or "", artist=artist,
            # the plain JPEG: YouTube's listed thumbnails come as AVIF, which Qt can't read
            duration=dur, artwork="https://i.ytimg.com/vi/%s/hqdefault.jpg" % e["id"],
            source="youtube", page_url="https://www.youtube.com/watch?v=%s" % e["id"], ext="m4a"))
    return out


# ------------------------------------------------------------------ playing
def _anonymous(downloader, url):
    """yt-dlp's options without anyone's cookies. Signed in, YouTube hands out
    stream links bound to the browser that asked -- the player can't open
    those ("Could not open file", seen with a signed-in Browser tab)."""
    opts = downloader.base_ydl_opts(None, url)
    downloader.release_opts(opts)
    opts.pop("cookiefile", None)
    opts.pop("cookiesfrombrowser", None)
    return opts


def resolve_stream(track):
    """A playable address for `track` (blocking for YouTube, whose stream
    links are made on request and last a few hours)."""
    if track.get("stream") and track["source"] != "youtube":
        return track["stream"]
    if track["source"] == "youtube":
        from . import downloader
        cached = _cached(track)
        if cached:
            return cached
        opts = _anonymous(downloader, track["page_url"])
        opts.update({"skip_download": True, "format": "bestaudio[ext=m4a]/bestaudio/best"})
        info = downloader._run(opts, track["page_url"],
                               lambda ydl: ydl.extract_info(track["page_url"], download=False))
        url = info.get("url") or next((f["url"] for f in reversed(info.get("requested_formats") or [])), None)
        if not url:
            raise MusicError("YouTube didn't give an audio stream for this song.")
        return url
    raise MusicError("This track has nothing to play.")


def _video_id(track):
    return track["id"].split(":", 1)[1] if ":" in track["id"] else track["id"]


def _cached(track):
    vid = _video_id(track)
    for path in glob.glob(os.path.join(glob.escape(CACHE_DIR), glob.escape(vid) + ".*")):
        if not path.endswith((".part", ".ytdl", ".tmp")) and os.path.getsize(path) > 0:
            return path
    return None


cached = _cached

# one fetch of a song at a time: playing it, fetching it ahead and keeping it
# all wait for the same download instead of starting their own
_fetch_locks = {}
_fetch_guard = threading.Lock()


def _lock_for(vid):
    with _fetch_guard:
        return _fetch_locks.setdefault(vid, threading.Lock())


def cache_audio(track, progress=None, cookies=False):
    """Fetches a YouTube song's audio to the app's cache and returns the
    file. Anonymous unless `cookies` (the Browser tab's sign-in, for a song
    that asks for one)."""
    from . import downloader
    with _lock_for(_video_id(track)):
        found = _cached(track)
        if found:
            return found
        os.makedirs(CACHE_DIR, exist_ok=True)
        url = track["page_url"]
        opts = downloader.base_ydl_opts(None, url) if cookies else _anonymous(downloader, url)

        def hook(d):
            if progress and d.get("status") == "downloading":
                progress(d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
        opts.update({"format": "bestaudio[ext=m4a]/bestaudio/best", "noplaylist": True, "noprogress": True,
                     "outtmpl": os.path.join(CACHE_DIR, "%(id)s.%(ext)s"), "progress_hooks": [hook],
                     "overwrites": True, "continuedl": False})
        try:
            downloader._run(opts, url, lambda ydl: ydl.extract_info(url, download=True))
        finally:
            downloader.release_opts(opts)
        found = _cached(track)
        if not found:
            raise MusicError("Couldn't fetch this song.")
    _prune_cache()
    return found


def fetch_song(track, progress=None):
    """The song's audio in the cache -- fetched without anyone's sign-in, and
    with the Browser tab's only if YouTube won't give it otherwise."""
    try:
        return cache_audio(track, progress)
    except Exception as first:   # noqa: BLE001
        logger.info("Fetching %s signed out failed (%s); trying the sign-in", track.get("id"), _short(first))
        try:
            return cache_audio(track, progress, cookies=True)
        except Exception as second:   # noqa: BLE001
            raise MusicError(friendly(second if "sign in" in str(second).lower() else first)) from second


_FRIENDLY = (
    ("sign in to confirm your age", "This song is age-restricted. Sign in to YouTube in the Browser tab to play it."),
    ("not a bot", "YouTube wants a sign-in to go on. Sign in to YouTube in the Browser tab, then try again."),
    ("page needs to be reloaded", "YouTube asked for a fresh start — try again."),
    ("private video", "This song is private."),
    ("video unavailable", "This song isn't available any more."),
    ("not available in your country", "This song isn't available in your country."),
    ("http error 403", "YouTube refused this one for now — try again."),
    ("http error 429", "YouTube is limiting requests for a moment — try again shortly."),
    ("timed out", "The connection timed out — check your internet and try again."),
    ("getaddrinfo", "No internet connection — check it and try again."),
    ("unable to connect", "No internet connection — check it and try again."),
)


def friendly(exc):
    """What went wrong, in words -- not yt-dlp's "ERROR: [youtube] id: ..."."""
    text = str(exc or "").strip()
    low = text.lower()
    for key, words in _FRIENDLY:
        if key in low:
            return words
    line = text.splitlines()[0] if text else "Something went wrong."
    if line.startswith("ERROR: "):
        line = line[7:]
    if line.startswith("[") and "] " in line:
        line = line.split("] ", 1)[1]
        if ": " in line and len(line.split(": ", 1)[0]) <= 16:
            line = line.split(": ", 1)[1]      # the video id
    return line[:200]


def _prune_cache():
    try:
        files = [p for p in glob.glob(os.path.join(glob.escape(CACHE_DIR), "*")) if os.path.isfile(p)]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[CACHE_KEEP:]:
            os.remove(old)
    except OSError:
        logger.debug("Couldn't tidy the music cache", exc_info=True)


# ------------------------------------------------------------------ keeping
def _safe_name(text):
    bad = '<>:/\\|?*'
    text = (text or "").replace('"', "'")          # quotes are common in titles; Windows won't have them
    clean = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in text).strip(" .")
    return clean[:120] or "track"


def download(track, folder, progress=None):
    """Saves `track` into `folder` and returns the file's path. YouTube goes
    through the same audio download as the Video tab (MP3, 320 kbps); the
    open libraries' files are saved as they are."""
    from ..utils import download_history
    os.makedirs(folder, exist_ok=True)
    name = _safe_name("%s - %s" % (track["artist"], track["title"]) if track["artist"] else track["title"])
    if track["source"] == "youtube":
        # the same fetch that plays it (often already in the cache), signed out
        # first: a signed-in fetch is what gets "The page needs to be reloaded"
        src = fetch_song(track, progress)
        path = _save_audio(src, folder, name, track)
    elif track["source"] == "local":
        return track["stream"]
    else:
        url = track.get("stream") or resolve_stream(track)
        ext = track.get("ext") or "mp3"
        path = os.path.join(folder, "%s.%s" % (name, ext))
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, "%s (%d).%s" % (name, n, ext))
            n += 1
        req = urllib.request.Request(url, headers=UA)
        tmp = path + ".part"
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
        os.replace(tmp, path)
        if track.get("attribution"):
            # the licence asks for credit: kept beside the song
            with open(os.path.splitext(path)[0] + " (credit).txt", "w", encoding="utf-8") as f:
                f.write("%s\n%s\n%s\n" % (track["attribution"], track.get("license_url") or "",
                                          track.get("page_url") or ""))
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    download_history.add_entry("audio", track["title"], path, folder, size)
    return path


def _unique(folder, name, ext):
    path = os.path.join(folder, "%s.%s" % (name, ext))
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s (%d).%s" % (name, n, ext))
        n += 1
    return path


def _ffmpeg():
    from . import ffmpeg_utils
    exe = ffmpeg_utils.ffmpeg_path()
    if exe == "ffmpeg" and not shutil.which("ffmpeg"):
        dev = os.path.join(config.BASE_DIR, "vendor", "ffmpeg.exe")     # running from source
        if os.path.exists(dev):
            return dev
    return exe


def _save_audio(src, folder, name, track):
    """The fetched song saved into `folder` as "Artist - Title.mp3" (320 kbps,
    like the Video tab's audio), tagged, with its cover. If FFmpeg can't do
    it, the fetched file itself is kept as it is."""
    out = _unique(folder, name, "mp3")
    part = out[:-4] + ".part.mp3"
    cover = None
    try:
        if (track.get("artwork") or "").startswith("http"):
            from . import ytmusic
            req = urllib.request.Request(ytmusic.sized(track["artwork"], 544), headers=UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read(4 * 1024 * 1024)
            if data[:3] == JPEG_MAGIC:                 # a JPEG: what an MP3's cover holds
                cover = part + ".jpg"
                with open(cover, "wb") as f:
                    f.write(data)
    except Exception:   # noqa: BLE001 -- a song without its cover is still a song
        logger.debug("No cover for %s", track.get("title"), exc_info=True)
    cmd = [_ffmpeg(), "-v", "error", "-y", "-i", src]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:0", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-map", "0:a"]
    for key, value in (("title", track.get("title")), ("artist", track.get("artist")),
                       ("album", track.get("album")), ("date", track.get("year"))):
        if value:
            cmd += ["-metadata", "%s=%s" % (key, value)]
    cmd += ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3", part]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        os.replace(part, out)
        return out
    except Exception:   # noqa: BLE001
        logger.warning("Couldn't convert %s to MP3; keeping it as fetched", src, exc_info=True)
        if os.path.exists(part):
            os.remove(part)
        out = _unique(folder, name, os.path.splitext(src)[1].lstrip(".") or "m4a")
        shutil.copyfile(src, out)
        return out
    finally:
        if cover and os.path.exists(cover):
            os.remove(cover)


def library(folder):
    """Songs already on this PC, in the music folder (newest first)."""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    files = []
    for root, _dirs, names in os.walk(folder):
        for n in names:
            if n.lower().endswith(AUDIO_EXT) and not n.lower().endswith(".part"):
                files.append(os.path.join(root, n))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in files[:2000]:
        base = os.path.splitext(os.path.basename(path))[0]
        artist, _, title = base.partition(" - ")
        if not title:
            artist, title = "", base
        out.append(_track(id="local:" + path, title=title, artist=artist, source="local", stream=path,
                          ext=os.path.splitext(path)[1].lstrip(".").lower(), page_url=path))
    return out
