"""yt-dlp wrapper: info fetching, download, and the HTTP-403 client-fallback retry.

Framework-agnostic on purpose -- no Tk imports here. UI code is responsible for
running these (blocking) calls on a background thread and marshaling results
back via root.after(...).
"""
import glob
import io
import os
import urllib.parse
import urllib.request

import yt_dlp

from ..logging_setup import get_logger
from ..utils import browser_cookies

logger = get_logger("downloader")

# Re-exported so UI code can catch/raise it without importing yt_dlp directly
# -- raising this from a progress_hook is yt-dlp's own documented way to
# cleanly abort an in-progress download from the outside.
DownloadCancelled = yt_dlp.utils.DownloadCancelled

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Sites behind Cloudflare's Bot Management (PornHub confirmed directly --
# fetches that fail outright over plain urllib succeed immediately once
# impersonated) fingerprint the TLS handshake itself, which a custom
# User-Agent header can't touch -- the byte-level ClientHello from
# Python's ssl/urllib simply doesn't look like a real browser's. curl_cffi
# is yt-dlp's supported fix: it replays a real Chrome TLS fingerprint.
# Checked once at import time via yt-dlp's own compatibility loader
# (rather than hand-rolling a curl_cffi version check here) so this stays
# correct as yt-dlp's supported curl_cffi version range changes -- and
# degrades to plain, unimpersonated requests (today's exact behaviour)
# on any machine where it isn't bundled or the installed version isn't
# one yt-dlp accepts, instead of hard-failing every single download.
try:
    from yt_dlp.networking._curlcffi import CurlCFFIRH as _  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget
    IMPERSONATE_TARGET = ImpersonateTarget.from_str("chrome")
except ImportError:
    IMPERSONATE_TARGET = None

try:
    from yt_dlp.cookies import CookieLoadError
except ImportError:  # older yt-dlp
    CookieLoadError = None

# Browsers whose cookie store couldn't be read this session. Chrome and Edge
# now lock their cookies with app-bound encryption that nothing outside the
# browser can open ("Failed to decrypt with DPAPI", yt-dlp issue #10927), and
# yt-dlp reads the store while it *starts up* -- before it has even looked at
# the link -- so one unreadable browser made every fetch fail, including
# public videos that need no sign-in at all (reported with a PornHub link).
# Once a browser is known to be unreadable it is skipped for the rest of the
# session instead of failing the same way on every request.
_UNREADABLE_BROWSERS = set()

# Called as listener(browser, message) -- from a worker thread -- the first
# time a browser's cookies turn out to be unreadable, so the UI can say why a
# sign-in wasn't used. The UI marshals it to its own thread.
cookie_fallback_listeners = []

_COOKIE_FAILURE_MARKERS = (
    "failed to decrypt with dpapi",
    "failed to load cookies",
    "could not copy chrome cookie database",
    "could not find chrome cookies database",
    "could not find firefox cookies database",
    "cookie database",
    "unsupported browser",
)


def _is_cookie_failure(exc):
    """True if `exc` (or anything it wraps) is yt-dlp failing to read a
    browser's cookie store, rather than failing on the link itself."""
    seen = set()
    e = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if CookieLoadError is not None and isinstance(e, CookieLoadError):
            return True
        if any(m in str(e).lower() for m in _COOKIE_FAILURE_MARKERS):
            return True
        wrapped = getattr(e, "exc_info", None)
        e = e.__cause__ or e.__context__ or (wrapped[1] if wrapped else None)
    return False


def _drop_browser_cookies(opts, url, exc):
    """Carries on without the browser's cookies: the app's own Browser-tab
    session is used instead if there is one, else the request goes out
    anonymously -- which is all a public video ever needed."""
    browser = opts.pop("cookiesfrombrowser", (None,))[0]
    first_time = browser not in _UNREADABLE_BROWSERS
    _UNREADABLE_BROWSERS.add(browser)
    if not opts.get("cookiefile"):
        cookie_file = browser_cookies.scoped_cookie_file(url)
        if cookie_file:
            opts["cookiefile"] = cookie_file
    logger.warning("Couldn't read %s's cookies (%s) -- continuing without them",
                   browser, str(exc).splitlines()[0][:160])
    if first_time:
        for listener in list(cookie_fallback_listeners):
            try:
                listener(browser, str(exc).splitlines()[0][:300])
            except Exception:
                logger.exception("cookie fallback listener failed")


def _run(opts, url, work):
    """work(ydl) with a YoutubeDL built from `opts`. If the chosen browser's
    cookie store can't be read -- when YoutubeDL starts, or on its first
    request -- the browser's cookies are dropped (see _drop_browser_cookies)
    and the work runs once more. `opts` is updated in place, so a caller that
    goes on to build more instances from it doesn't hit the same wall."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return work(ydl)
    except Exception as e:
        if not opts.get("cookiesfrombrowser") or not _is_cookie_failure(e):
            raise
        _drop_browser_cookies(opts, url, e)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return work(ydl)


# Among streams of the same resolution and frame rate, H.264 and AAC first:
# they play in every player and on every GPU, where AV1 and HEVC depend on
# the decoder installed (a missing or faulty one shows garbage blocks too).
# Resolution still comes first, so a 4K video that only exists in VP9/AV1 is
# still downloaded in 4K. The fetch uses the same order, so the size shown
# for each resolution is the size of what will actually be downloaded.
VIDEO_FORMAT_SORT = ["res", "fps", "vcodec:h264", "acodec:aac"]


def base_ydl_opts(cookies_from_browser=None, url=None):
    """Common options, incl. a browser-like User-Agent which avoids some 403s.

    socket_timeout is a hard safety net: confirmed directly that a
    problematic URL (a Reddit gallery post, which yt-dlp's Reddit extractor
    doesn't understand and tries to recurse into itself) can hang a fetch
    thread forever with no timeout at all, leaving the UI stuck on
    "Fetching..." permanently with no error and no way to recover short of
    restarting the app. This bounds every network call yt-dlp makes so a
    fetch always eventually fails loudly instead of hanging silently.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "http_headers": {"User-Agent": USER_AGENT},
        # yt-dlp's own multi-connection equivalent for the Video tab: most
        # high-res/YouTube sources are DASH/HLS (separate video+audio
        # streams split into many small fragments), and yt-dlp can fetch N
        # of those fragments in parallel instead of one at a time -- same
        # "download faster via more simultaneous connections" idea as
        # a chunked-range engine, just using
        # yt-dlp's own built-in support instead of routing video downloads
        # through that separate engine (which only speaks plain ranged HTTP,
        # not yt-dlp's format selection/extraction/merge pipeline). A no-op
        # for sources that aren't fragmented (a single direct progressive
        # MP4 URL, say) -- nothing to parallelize there, but harmless to set
        # unconditionally.
        #
        # 4, not 8: many video CDNs throttle a client that opens that many
        # connections at once, and a throttled fragment is a failed one.
        "concurrent_fragment_downloads": 4,
        # A fragment that still fails after its retries ends the download
        # with an error. yt-dlp's default is to skip it and carry on, which
        # finishes "successfully" with a hole in the stream: every frame
        # until the next keyframe then decodes against missing data, and the
        # picture smears into blocks (reported with a screenshot as "looks
        # pixelated, like the encoding went wrong"). A failed download can
        # be retried, and resumes; a silently corrupt one can't be noticed.
        "skip_unavailable_fragments": False,
        "fragment_retries": 15,
        "retries": 10,
        "file_access_retries": 5,
        # Back off between retries instead of hammering a CDN that has
        # started refusing: 1, 2, 4, 8 ... seconds, at most 20.
        "retry_sleep_functions": {
            "fragment": lambda n: min(2 ** n, 20),
            "http": lambda n: min(2 ** n, 20),
        },
    }
    if IMPERSONATE_TARGET is not None:
        opts["impersonate"] = IMPERSONATE_TARGET
    if cookies_from_browser and cookies_from_browser not in _UNREADABLE_BROWSERS:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    else:
        # Whatever the user is signed in to in this app's own Browser tab.
        # Sites that gate content behind a login (an Instagram post, an
        # age-restricted video) answer an anonymous request with an empty
        # media response, and yt-dlp's remedy is cookies. Taking them from
        # the built-in browser means the user opts in by signing in here,
        # rather than the app reaching into their real Chrome profile.
        #
        # Scoped to `url`'s site and written to a file of its own: callers
        # hand the options back to release_opts() once yt-dlp is finished,
        # which deletes it. None when there is nothing to send, in which case
        # the request goes out anonymously exactly as before.
        cookie_file = browser_cookies.scoped_cookie_file(url)
        if cookie_file:
            opts["cookiefile"] = cookie_file
    return opts


def release_opts(opts):
    """Deletes the temporary cookie file base_ydl_opts() made for these
    options. Safe to call on any options dict, and more than once."""
    if opts:
        browser_cookies.discard(opts.get("cookiefile"))


def _selected_size(ydl, formats, fmt_spec):
    """Ask yt-dlp's own format selector which format(s) `fmt_spec` would pick
    out of `formats`, and sum their filesizes. This mirrors the real download
    exactly (same selector string used in download_video/download_audio),
    instead of guessing via a manual heuristic -- e.g. picking the largest
    file at a given height can grab an outlier codec variant that yt-dlp's
    default selection would never actually choose.
    """
    try:
        selector = ydl.build_format_selector(fmt_spec)
        selected = list(selector({"formats": formats, "incomplete_formats": False}))
    except Exception:
        logger.exception("Format selector failed for spec %r", fmt_spec)
        return 0
    total = 0
    for s in selected:
        for rf in s.get("requested_formats") or [s]:
            total += rf.get("filesize") or rf.get("filesize_approx") or 0
    return total


def fetch_info_with_sizes(url, cookies_from_browser=None):
    """Blocking. Returns (info_dict, height_sizes) where height_sizes[h] is the
    *combined* video+audio byte total yt-dlp's own selector would pick for
    'bestvideo[height<=h]+bestaudio/best[height<=h]' -- i.e. what a real
    download at that resolution will actually produce. Raises on failure.

    ignore_no_formats_error=True matters for image-only posts (Instagram,
    Facebook, ...): those extractors successfully fetch the post's metadata
    (title, thumbnails) but then deliberately raise rather than return it,
    since they're video extractors first. This flag is yt-dlp's own supported
    way to get that already-fetched info_dict back anyway -- formats just
    comes back empty, which the caller uses to detect "this is an image
    post" and fall back to saving info['thumbnail'] directly, instead of us
    re-implementing HTML scraping that would just hit the same login walls
    yt-dlp's extractor-specific handling already knows how to work around.
    """
    opts = {**base_ydl_opts(cookies_from_browser, url), "skip_download": True,
            "ignore_no_formats_error": True,
            "format_sort": VIDEO_FORMAT_SORT,
            # A playlist or channel comes back as a list of links rather than
            # having every one of its videos fully extracted -- without this,
            # pasting a 200-video playlist sat on "reading..." for minutes
            # while yt-dlp resolved formats for videos nobody had chosen yet.
            # It changes nothing for a single video, which is still extracted
            # in full. `noplaylist` stays on, so a watch?v=...&list=... link
            # is still that one video.
            "extract_flat": "in_playlist",
            "playlist_items": f"1-{PLAYLIST_LIMIT}"}
    def work(ydl):
        info = ydl.extract_info(url, download=False)
        if is_playlist(info):
            return info, {}
        formats = info.get("formats", [])
        heights = sorted({
            f["height"] for f in formats
            if f.get("vcodec") not in (None, "none") and f.get("height")
        }, reverse=True)
        height_sizes = {
            h: _selected_size(ydl, formats, f"bestvideo[height<={h}]+bestaudio/best[height<={h}]")
            for h in heights
        }
        return info, height_sizes

    try:
        return _run(opts, url, work)
    finally:
        release_opts(opts)


# How many entries of a playlist or channel get stacked at once. A channel
# can list tens of thousands; the first few hundred is what anyone means by
# "download this playlist", and it keeps the queue usable.
PLAYLIST_LIMIT = 300

# The heights offered for a playlist entry, whose real formats are not known
# until it is downloaded (the whole point of the flat listing above). The
# format selector falls back to the nearest available one, so picking 1440p
# for a video that tops out at 1080p still gets the 1080p.
STANDARD_LADDER = [2160, 1440, 1080, 720, 480, 360]


def is_playlist(info):
    return (bool(info) and info.get("_type") in ("playlist", "multi_video")
            and info.get("entries") is not None)


def _entry_url(entry):
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url.startswith(("http://", "https://")):
        return url
    ie = (entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    vid = entry.get("id") or url
    if ie.startswith("youtube") and vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url or None


def _is_tab_listing(entries):
    """A channel's root URL lists its *tabs* (Videos, Shorts, Live) rather
    than videos, each one a playlist of its own."""
    if not entries:
        return False
    tabs = [e for e in entries if isinstance(e, dict) and (
        (e.get("ie_key") or "").lower().endswith("tab") or e.get("_type") == "playlist")]
    return len(tabs) == len(entries)


def playlist_entries(info, cookies_from_browser=None):
    """Flattens a playlist result into [{"url", "title", "duration",
    "uploader", "thumbnail_url"}], following a channel root into its Videos
    tab first. Entries with no usable link (deleted or private videos show up
    in playlists as placeholders) are dropped."""
    entries = list(info.get("entries") or [])
    if _is_tab_listing(entries):
        tab = next((e for e in entries if "/videos" in (_entry_url(e) or "")), entries[0])
        tab_url = _entry_url(tab)
        if tab_url:
            sub_info, _sizes = fetch_info_with_sizes(tab_url, cookies_from_browser)
            if is_playlist(sub_info):
                entries = list(sub_info.get("entries") or [])

    out = []
    for e in entries[:PLAYLIST_LIMIT]:
        if not isinstance(e, dict):
            continue
        url = _entry_url(e)
        title = e.get("title") or ""
        if not url or title in ("[Deleted video]", "[Private video]"):
            continue
        out.append({
            "url": url,
            "title": title or url,
            "duration": int(e.get("duration") or 0),
            "uploader": e.get("uploader") or e.get("channel") or info.get("uploader") or "",
            "thumbnail_url": best_thumbnail_url(e),
        })
    return out


_REDDIT_COOKIE_JAR = None


def _reddit_opener():
    """Reddit blocks a plain anonymous request to its own public .json
    endpoint (confirmed directly: a bare urllib request gets HTTP 403), but
    accepts it once a session cookie (`loid`) has been set by visiting
    old.reddit.com first -- the same session-priming yt-dlp's own Reddit
    extractor does internally. Reused across calls via a module-level cookie
    jar so we're not re-priming a session on every fetch.
    """
    global _REDDIT_COOKIE_JAR
    if _REDDIT_COOKIE_JAR is None:
        import http.cookiejar
        _REDDIT_COOKIE_JAR = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_REDDIT_COOKIE_JAR))
        try:
            opener.open(
                urllib.request.Request("https://old.reddit.com/", headers={"User-Agent": USER_AGENT}),
                timeout=10,
            )
        except Exception:
            logger.exception("Failed to prime Reddit session")
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_REDDIT_COOKIE_JAR))


def fetch_reddit_gallery(url):
    """Reddit's multi-image "gallery" post type has no support at all in the
    installed yt-dlp's Reddit extractor (confirmed by reading its source --
    there's no gallery/media_metadata handling for images, only for a
    'RedditVideo' media type). Feeding it a gallery URL doesn't just fail
    cleanly, it hangs indefinitely (confirmed directly: extract_info() on a
    real gallery post never returned, no exception, no timeout). Reddit's
    public `<permalink>.json` endpoint (no auth required, well-documented)
    has everything directly: `is_gallery` + `gallery_data.items` (display
    order) + `media_metadata[id].s.url` (full-resolution source per image).

    Returns (title, items) for a gallery post, or (None, []) if this URL
    isn't a gallery post at all (caller falls back to the normal path, which
    already works fine for single-image and video Reddit posts).
    """
    import html
    import json as json_module

    clean_url = url.split("?", 1)[0].rstrip("/")
    json_url = f"{clean_url}/.json"
    req = urllib.request.Request(json_url, headers={"User-Agent": USER_AGENT})
    with _reddit_opener().open(req, timeout=15) as resp:
        data = json_module.load(resp)

    post = data[0]["data"]["children"][0]["data"]
    if not post.get("is_gallery"):
        return None, []

    title = html.unescape(post.get("title") or "Reddit gallery")
    order = [item["media_id"] for item in (post.get("gallery_data") or {}).get("items", [])]
    media_metadata = post.get("media_metadata") or {}

    items = []
    for media_id in order:
        meta = media_metadata.get(media_id) or {}
        if meta.get("e") != "Image":
            continue
        img_url = (meta.get("s") or {}).get("u")
        if not img_url:
            continue
        items.append({
            "title": f"{title} ({len(items) + 1})",
            "url": html.unescape(img_url),
            "is_video": False,
        })
    return title, items


def fetch_image_gallery(url, cookies_from_browser=None):
    """Blocking. Returns (post_title, items) where items is a list of
    {"title", "url", "is_video"} dicts -- one per image (or video, marked as
    such) in the post. Handles a single-media post, a carousel (Instagram
    calls these "sidecar" posts: yt-dlp represents one as a '_type':
    'playlist' result with an 'entries' list, one entry per slide, same
    shape its own Instagram extractor builds for carousel_media), and a
    Reddit gallery (handled separately above, since yt-dlp doesn't support
    those at all).
    """
    if "reddit.com" in url.lower():
        title, items = fetch_reddit_gallery(url)
        if items:
            return title, items
        # Not a gallery post (or gallery parsing found nothing) -- fall
        # through to the normal yt-dlp path below, which handles single-image
        # and video Reddit posts fine on its own.

    opts = {**base_ydl_opts(cookies_from_browser, url), "skip_download": True,
            "ignore_no_formats_error": True}
    try:
        info = _run(opts, url, lambda ydl: ydl.extract_info(url, download=False))
    finally:
        release_opts(opts)

    post_title = info.get("title") or "Untitled post"
    entries = info.get("entries")
    slides = list(entries) if entries is not None else [info]

    items = []
    for idx, entry in enumerate(slides, start=1):
        formats = entry.get("formats") or []
        has_video = any(f.get("vcodec") not in (None, "none") for f in formats)
        slide_url = best_thumbnail_url(entry)
        if not slide_url:
            continue
        items.append({
            "title": entry.get("title") or f"{post_title} ({idx})",
            "url": slide_url,
            "is_video": has_video,
        })
    return post_title, items


def best_thumbnail_url(info):
    """Highest-resolution image URL available for this post/entry.

    The singular 'thumbnail' field is yt-dlp's *own* pick, and it is not
    reliably the largest one -- for Instagram in particular the extractor
    often sets it to a downscaled display variant while the full-size
    original sits further down the 'thumbnails' list. Since this is what
    actually gets saved to disk (see save_thumbnail), always rank the whole
    list by pixel area and take the winner; 'thumbnail' is only the fallback
    for the case where the list is empty or carries no usable dimensions at
    all (many extractors leave width/height unset, which would otherwise
    make every candidate rank as area 0 and pick arbitrarily).
    """
    thumbs = info.get("thumbnails") or []
    sized = [t for t in thumbs if t.get("url") and (t.get("width") or t.get("height"))]
    if sized:
        best = max(sized, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        return best.get("url")
    url = info.get("thumbnail")
    if url:
        return url
    for t in thumbs:
        if t.get("url"):
            return t["url"]
    return None


def fetch_thumbnail_image(url, size=(160, 90)):
    """Best-effort thumbnail download; returns a PIL Image or None."""
    if not url or not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(io.BytesIO(_fetch_thumbnail_bytes(url))).convert("RGB")
        return img.resize(size, Image.LANCZOS)
    except Exception:
        logger.exception("Failed to fetch thumbnail from %s", url)
        return None


def _fetch_thumbnail_bytes(url, with_content_type=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
        if with_content_type:
            return data, (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return data


# Content-Type -> extension. Deliberately small: only the formats an image
# host actually serves. Anything unrecognized falls back to the extension in
# the URL itself, then to .jpg.
_IMAGE_EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
    "image/heic": ".heic", "image/bmp": ".bmp", "image/tiff": ".tiff",
}


def _image_extension(url, content_type):
    ext = _IMAGE_EXT_BY_MIME.get(content_type)
    if ext:
        return ext
    url_ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if url_ext in set(_IMAGE_EXT_BY_MIME.values()) | {".jpeg"}:
        return ".jpg" if url_ext == ".jpeg" else url_ext
    return ".jpg"


def save_thumbnail(url, dest_path_no_ext):
    """Save the image at maximum quality, returning the real path written.

    Writes the fetched bytes to disk verbatim rather than round-tripping
    them through PIL. The previous version did `Image.open(...).convert(
    "RGB").save(dest)`, which re-encoded every image as a *fresh* JPEG at
    Pillow's default quality=75 -- a second lossy generation stacked on top
    of the host's own compression, plus a flattened alpha channel on PNGs
    and a forced .jpg extension on WebP/PNG sources. Copying the original
    bytes is both lossless and faster, and needs no PIL at all.

    Takes a path *without* an extension and appends the real one (derived
    from the response's Content-Type, falling back to the URL) so a PNG
    stays a .png instead of being mislabeled .jpg.
    """
    data, content_type = _fetch_thumbnail_bytes(url, with_content_type=True)
    dest_path = dest_path_no_ext + _image_extension(url, content_type)
    with open(dest_path, "wb") as f:
        f.write(data)
    return dest_path


def range_ydl_opts(time_range):
    """Build the yt-dlp options that trim a download to a specific time window."""
    if not time_range:
        return {}
    start, end = time_range
    return {
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
    }


def run_with_client_fallback(opts, url):
    """Try the download; on HTTP 403 / Forbidden, first retry the *same*
    client a few times before ever switching clients; only fall back to
    alternate YouTube clients if that still fails.

    That ordering matters and was gotten wrong before: reproduced directly
    against a real 4320p/~870MB download that hit a 403 partway through (at
    21%, several minutes in) -- YouTube's signed CDN URLs have a limited
    validity window, and a large, slow download on a big format is exactly
    the case that can outlast it. That is nothing to do with the *client*
    being blocked; re-extracting gets a fresh signed URL for the *same*
    format, and yt-dlp resumes the partial download via Range requests
    against the .part file already on disk instead of restarting -- no
    quality loss, no wasted bytes. The old code jumped straight to
    alternate clients (android/ios/etc.) on the very first 403, which do
    carry a much lower resolution ceiling than the default client -- that
    is what silently turned this exact download into a 360p/22MB file
    instead of the requested 4320p one, with nothing distinguishing it from
    a normal successful download.

    Alternate clients are still tried as a last resort, for the case where
    the block is real rather than just an expired URL (confirmed necessary
    separately -- YouTube does sometimes block the default client outright
    while another internal client still works).

    Returns (info, used_fallback_client_or_None). The second value matters:
    callers use it to tell the user their requested quality may not have
    been honored when a fallback client genuinely was needed, instead of
    silently reporting success as if it had been.
    """
    last_error = None
    for attempt in range(3):
        try:
            return _run(opts, url, lambda ydl: ydl.extract_info(url, download=True)), None
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            msg = str(e)
            if "403" not in msg and "Forbidden" not in msg:
                raise
            logger.info("403 on default client (attempt %d/3) for %s -- retrying "
                        "(likely an expired signed URL on a large/slow download, "
                        "not a real block)", attempt + 1, url)

    for client in ("android", "ios", "web_creator", "tv"):
        try:
            retry_opts = dict(opts)
            retry_opts["extractor_args"] = {"youtube": {"player_client": [client]}}
            return _run(retry_opts, url, lambda ydl: ydl.extract_info(url, download=True)), client
        except Exception:
            logger.info("403 client-fallback '%s' also failed for %s", client, url)
            continue
    raise last_error


def download_audio(url, save_dir, bitrate, progress_hook, time_range=None, cookies_from_browser=None):
    """Returns (info, mp3_path_or_None, used_fallback_client_or_None)."""
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")
    opts = {
        **base_ydl_opts(cookies_from_browser, url),
        **range_ydl_opts(time_range),
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate}
        ],
    }
    try:
        info, used_fallback = run_with_client_fallback(opts, url)
    finally:
        release_opts(opts)
    return info, _final_path(info, save_dir, ".mp3"), used_fallback


def download_video(url, save_dir, height, progress_hook, time_range=None, cookies_from_browser=None):
    """Returns (info, merged_mkv_path_or_None, used_fallback_client_or_None)."""
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")
    # The trailing "/bestvideo+bestaudio/best" is a last resort for a height
    # the video simply doesn't go down to: without it, asking for 360p on a
    # video whose smallest stream is 480p failed outright with "Requested
    # format is not available". That matters more now that playlist entries
    # offer a standard ladder rather than the heights each video really has.
    fmt = (f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/bestvideo+bestaudio/best"
           if height else "bestvideo+bestaudio/best")
    opts = {
        **base_ydl_opts(cookies_from_browser, url),
        **range_ydl_opts(time_range),
        "format": fmt,
        "format_sort": VIDEO_FORMAT_SORT,
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "merge_output_format": "mkv",  # always merge into mkv first: universally compatible
    }
    try:
        info, used_fallback = run_with_client_fallback(opts, url)
    finally:
        release_opts(opts)
    return info, _final_path(info, save_dir, ".mkv"), used_fallback


def _final_path(info, save_dir, ext):
    """Where this download actually ended up.

    yt-dlp records the post-processed path on the info it returns, so that is
    the answer. The old fallback -- "the newest file with this extension in
    the folder" -- is only used when it is missing, because it is wrong the
    moment two downloads share a folder: whichever finished last would be
    reported as the result of both, so one card's Play button opened the
    other one's file.
    """
    for d in (info or {}).get("requested_downloads") or []:
        path = d.get("filepath") or d.get("filename")
        if path and os.path.exists(path):
            return path
    path = (info or {}).get("filepath")
    if path and os.path.exists(path):
        return path
    candidates = sorted(glob.glob(os.path.join(save_dir, "*" + ext)), key=os.path.getmtime)
    return candidates[-1] if candidates else None
