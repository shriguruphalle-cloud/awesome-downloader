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


def base_ydl_opts(cookies_from_browser=None):
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
        "concurrent_fragment_downloads": 8,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


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
    opts = {**base_ydl_opts(cookies_from_browser), "skip_download": True, "ignore_no_formats_error": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
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

    opts = {**base_ydl_opts(cookies_from_browser), "skip_download": True, "ignore_no_formats_error": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

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
    """Try the download; on HTTP 403 / Forbidden retry with alternate YouTube clients.

    YouTube periodically tweaks its player/signature logic, which can make
    yt-dlp's default client get blocked with a 403 while another internal
    client still works. This mirrors what an updated yt-dlp would try.
    """
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "403" not in msg and "Forbidden" not in msg:
            raise
        for client in ("android", "ios", "web_creator", "tv"):
            try:
                retry_opts = dict(opts)
                retry_opts["extractor_args"] = {"youtube": {"player_client": [client]}}
                with yt_dlp.YoutubeDL(retry_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            except Exception:
                logger.info("403 client-fallback '%s' also failed for %s", client, url)
                continue
        raise


def download_audio(url, save_dir, bitrate, progress_hook, time_range=None, cookies_from_browser=None):
    """Returns (info, mp3_path_or_None)."""
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")
    opts = {
        **base_ydl_opts(cookies_from_browser),
        **range_ydl_opts(time_range),
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate}
        ],
    }
    info = run_with_client_fallback(opts, url)

    # The postprocessor changes the extension to .mp3 after download, so the
    # newest .mp3 in the save dir is the reliable way to find it (same
    # fallback pattern download_video uses for its merged .mkv).
    candidates = sorted(glob.glob(os.path.join(save_dir, "*.mp3")), key=os.path.getmtime)
    mp3_path = candidates[-1] if candidates else None
    return info, mp3_path


def download_video(url, save_dir, height, progress_hook, time_range=None, cookies_from_browser=None):
    """Returns (info, merged_mkv_path_or_None)."""
    outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")
    fmt = (f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
           if height else "bestvideo+bestaudio/best")
    opts = {
        **base_ydl_opts(cookies_from_browser),
        **range_ydl_opts(time_range),
        "format": fmt,
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "merge_output_format": "mkv",  # always merge into mkv first: universally compatible
    }
    info = run_with_client_fallback(opts, url)

    merged_path = None
    downloads = info.get("requested_downloads") or []
    if downloads and downloads[0].get("filepath"):
        merged_path = downloads[0]["filepath"]
    else:
        candidates = sorted(glob.glob(os.path.join(save_dir, "*.mkv")), key=os.path.getmtime)
        merged_path = candidates[-1] if candidates else None
    return info, merged_path
