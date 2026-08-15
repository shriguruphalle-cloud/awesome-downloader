"""Generates/caches a small preview thumbnail for a Download History entry.

Real thumbnail when it's cheap to get one (the file itself, for images; a
single frame grab via ffmpeg, for video/torrent files with a video
extension); returns None otherwise so the caller falls back to a clean
placeholder icon -- never raises, a missing/corrupt file just means no
preview, not a crash.
"""
import hashlib
import os

from .. import config
from ..logging_setup import get_logger
from . import ffmpeg_utils

logger = get_logger("thumbnails")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

CACHE_DIR = os.path.join(config.APPDATA_DIR, "thumb_cache")
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".ts"}


def _cache_path(file_path):
    digest = hashlib.sha1(file_path.encode("utf-8", errors="ignore")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.jpg")


def get_preview_image(file_path, kind, size=(64, 64)):
    """Returns a resized PIL Image, or None if no real thumbnail could be
    produced (caller should show a placeholder icon instead)."""
    if not file_path or not PIL_AVAILABLE or not os.path.exists(file_path):
        return None

    if kind == "image":
        try:
            return Image.open(file_path).convert("RGB").resize(size, Image.LANCZOS)
        except Exception:
            logger.exception("Failed to load image thumbnail for %s", file_path)
            return None

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _VIDEO_EXTS or not ffmpeg_utils.ffmpeg_available():
        return None

    cache_path = _cache_path(file_path)
    if not os.path.exists(cache_path):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            cmd = ["ffmpeg", "-y", "-ss", "00:00:01", "-i", file_path,
                   "-frames:v", "1", "-vf", "scale=320:-1", cache_path]
            result = ffmpeg_utils.ffmpeg_run(cmd)
            if result.returncode != 0 or not os.path.exists(cache_path):
                return None
        except Exception:
            logger.exception("Failed to extract video thumbnail for %s", file_path)
            return None

    try:
        return Image.open(cache_path).convert("RGB").resize(size, Image.LANCZOS)
    except Exception:
        logger.exception("Failed to load cached thumbnail for %s", file_path)
        return None
