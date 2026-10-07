"""Which tab a pasted link belongs in.

Every paste box in the app sends its links here first, so a link always ends
up where it can be downloaded, whichever box it was pasted into: an image
link pasted into the Video tab opens in Images, a reel pasted into Images
opens in Video, a magnet link goes to Torrent, and an Instagram account or a
story -- which hold both pictures and videos -- is split between the two.

Decided from the link alone, without any network request. Where the link
can't say (an Instagram post can be a photo, a video or a carousel of both;
so can a post on X, Facebook or Reddit), the route is POST and the tab that
fetches it sorts the content once it knows.
"""
import re
import urllib.parse
from dataclasses import dataclass

VIDEO = "video"         # the Video tab
IMAGES = "images"       # the Images tab
TORRENT = "torrent"     # the Torrent tab
SPLIT = "split"         # videos to the Video tab, pictures to the Images tab
POST = "post"           # decided by what the post turns out to hold

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif", ".tif", ".tiff")
_VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".flv", ".m3u8", ".mpd", ".ts")

# The first path segment of an instagram.com link that isn't an account.
_IG_RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about", "legal", "developer",
    "developers", "web", "api", "s", "challenge", "emails", "session", "privacy", "terms", "graphql", "static",
    "ar", "lite", "nametag", "topics", "locations", "directory", "press", "blog", "help", "oauth", "create",
}
_YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")


@dataclass(frozen=True)
class Route:
    kind: str           # what the link is, e.g. "ig_profile", "yt_channel", "image"
    tab: str            # VIDEO, IMAGES, TORRENT, SPLIT or POST
    name: str = ""      # a person's name for it: the account, channel or file

    def describe(self):
        return {
            "torrent": "a torrent", "image": "an image", "gallery": "an image gallery",
            "video": "a video", "ig_reel": "an Instagram reel", "ig_post": "an Instagram post",
            "ig_profile": "an Instagram account", "ig_story": "Instagram stories",
            "yt_channel": "a YouTube channel", "yt_playlist": "a YouTube playlist", "post": "a post",
        }.get(self.kind, "a link")


def _host(parsed):
    return (parsed.hostname or "").lower()


def _ends(host, *domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def classify(link):
    """Route for one pasted link. Anything that isn't recognised goes to the
    Video tab, whose engine knows 1,750+ sites."""
    text = (link or "").strip()
    if text.lower().startswith("magnet:"):
        return Route("torrent", TORRENT)
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return Route("video", VIDEO)
    host = _host(parsed)
    path = parsed.path or "/"
    low_path = path.lower()
    segs = [s for s in path.split("/") if s]

    if low_path.endswith(".torrent"):
        return Route("torrent", TORRENT, segs[-1] if segs else "")

    # instagram.com pages first (its picture files, on cdninstagram, are
    # caught below as the pictures they are)
    if _ends(host, "instagram.com", "instagr.am"):
        return _instagram(segs)

    if low_path.endswith(_IMAGE_EXT) or _image_query(parsed):
        return Route("image", IMAGES, segs[-1] if segs else "")
    if low_path.endswith(_VIDEO_EXT):
        return Route("video", VIDEO, segs[-1] if segs else "")
    if host in ("i.redd.it", "i.imgur.com", "pbs.twimg.com", "i.pinimg.com") and not low_path.endswith(_VIDEO_EXT):
        return Route("image", IMAGES)

    if host in _YT_HOSTS or host == "youtu.be":
        return _youtube(host, segs, parsed)

    if _ends(host, "reddit.com") and segs[:1] == ["gallery"]:
        return Route("gallery", IMAGES)
    if _ends(host, "reddit.com") and "comments" in segs:
        return Route("post", POST)
    if _ends(host, "imgur.com") and segs[:1] in (["a"], ["gallery"]):
        return Route("gallery", IMAGES)
    if _ends(host, "x.com", "twitter.com") and "status" in segs:
        return Route("post", POST)
    if _ends(host, "facebook.com", "fb.watch"):
        if any(s in ("photo", "photo.php", "photos") for s in segs):
            return Route("image", IMAGES)
        if any(s in ("videos", "watch", "reel", "reels") for s in segs) or host == "fb.watch":
            return Route("video", VIDEO)
        return Route("post", POST)
    if _ends(host, "pinterest.com", "pin.it") or (".pinterest." in host):
        return Route("post", POST)
    if _ends(host, "threads.net", "tumblr.com", "bsky.app"):
        return Route("post", POST)
    if _ends(host, "flickr.com"):
        return Route("gallery", IMAGES)
    return Route("video", VIDEO)


def _image_query(parsed):
    """Image links whose type is in the query, e.g. pbs.twimg.com/media/X?format=jpg."""
    q = urllib.parse.parse_qs(parsed.query)
    fmt = (q.get("format") or q.get("fm") or [""])[0].lower()
    return fmt in ("jpg", "jpeg", "png", "webp", "gif", "avif")


def _instagram(segs):
    if not segs:
        return Route("video", VIDEO)
    first = segs[0].lower()
    if first in ("reel", "reels", "tv") and len(segs) >= 2:
        return Route("ig_reel", VIDEO)
    if first == "p" and len(segs) >= 2:
        return Route("ig_post", POST)
    if first == "stories" and len(segs) >= 2:
        return Route("ig_story", SPLIT, segs[1] if segs[1] != "highlights" else "highlights")
    if first not in _IG_RESERVED and re.fullmatch(r"[A-Za-z0-9._]{1,30}", segs[0]):
        # instagram.com/<name>/, /<name>/reels/, /<name>/p/<code>/ ...
        if len(segs) >= 3 and segs[1].lower() in ("p", "reel", "reels", "tv"):
            return Route("ig_reel" if segs[1].lower() != "p" else "ig_post",
                         VIDEO if segs[1].lower() != "p" else POST)
        return Route("ig_profile", SPLIT, segs[0])
    return Route("video", VIDEO)


def _youtube(host, segs, parsed):
    if host == "youtu.be":
        return Route("video", VIDEO)
    first = segs[0] if segs else ""
    q = urllib.parse.parse_qs(parsed.query)
    if first == "playlist" and q.get("list"):
        return Route("yt_playlist", VIDEO)
    if first in ("watch", "shorts", "live", "embed", "v"):
        return Route("video", VIDEO)
    if first.startswith("@") or first in ("channel", "c", "user"):
        name = first if first.startswith("@") else (segs[1] if len(segs) > 1 else "")
        return Route("yt_channel", VIDEO, name)
    return Route("video", VIDEO)
