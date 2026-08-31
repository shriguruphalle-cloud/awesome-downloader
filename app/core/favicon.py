"""Fetches and caches website favicons for the Browser tab's shortcut
tiles. Framework-agnostic (no Qt) -- callers convert the returned PIL
Image to whatever GUI-toolkit image type they need, on whichever thread
that conversion is safe on (Qt image types generally aren't safe to build
off the GUI thread).
"""
import io
import os
import urllib.parse
import urllib.request

from .. import config
from ..logging_setup import get_logger

logger = get_logger("favicon")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

CACHE_DIR = os.path.join(config.APPDATA_DIR, "browser_favicons")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _cache_path(host):
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in host)
    return os.path.join(CACHE_DIR, f"{safe}.png")


def get_favicon(url, size=48):
    """Returns a PIL Image (RGBA, pre-resized to size x size) or None on
    any failure -- not every site serves a favicon at the standard
    /favicon.ico path, so callers are expected to fall back to a
    placeholder rather than treat None as an error.

    Checks a local on-disk cache first so repeat app launches don't
    re-fetch every shortcut's icon over the network every time."""
    if not PIL_AVAILABLE:
        return None
    host = urllib.parse.urlparse(url).netloc
    if not host:
        return None

    cache_file = _cache_path(host)
    if os.path.exists(cache_file):
        try:
            return Image.open(cache_file).convert("RGBA")
        except Exception:
            pass  # corrupt cache entry -- fall through and refetch

    favicon_url = f"https://{host}/favicon.ico"
    try:
        req = urllib.request.Request(favicon_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        # .ico files bundle several pre-rendered sizes -- Pillow's ICO
        # plugin picks the largest available frame by default, giving a
        # clean source to downscale from.
        img = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
        os.makedirs(CACHE_DIR, exist_ok=True)
        img.save(cache_file, "PNG")
        return img
    except Exception:
        logger.debug("No favicon found for %s", host)
        return None
