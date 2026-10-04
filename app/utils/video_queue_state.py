"""Persists the Video tab's stacked-link queue across restarts.

Kept separate from download_queue_state.py on purpose, because the two answer
different questions. That file remembers downloads that were *already running*
so yt-dlp can resume their .part files; this one remembers links that were
lined up and never started, which have no bytes on disk and nothing to resume
-- they just need to still be sitting there next time the app opens.

Only plain JSON-able fields are kept. A queue card also carries a decoded
thumbnail QPixmap, which is neither serialisable nor worth storing: the
thumbnail URL is saved instead and the image is fetched again on restore, the
same way it was fetched the first time.
"""
import json
import os

from .. import config
from ..logging_setup import get_logger

logger = get_logger("video_queue_state")

STATE_PATH = os.path.join(config.APPDATA_DIR, "queued_links.json")

# Everything a card needs to come back looking and behaving as it did.
FIELDS = ("url", "mode", "height", "container", "bitrate", "time_range",
          "title", "meta", "duration", "is_image", "thumbnail_url", "heights",
          # A card that came from a playlist listing offers "Best" plus the
          # standard ladder rather than heights it has actually seen.
          "ladder")


def to_entry(payload, heights=None):
    """Reduces a card's payload to the storable subset."""
    entry = {k: payload.get(k) for k in FIELDS if k != "heights"}
    entry["heights"] = list(heights or [])
    # JSON has no tuples; a clip range round-trips as a list and is put back
    # together on load, so the payload keeps the shape the download code wants.
    if entry.get("time_range") is not None:
        entry["time_range"] = list(entry["time_range"])
    return entry


def from_entry(entry):
    payload = {k: entry.get(k) for k in FIELDS if k != "heights"}
    payload.setdefault("bitrate", "192")
    payload["mode"] = payload.get("mode") or "video"
    payload["thumb_pixmap"] = None
    if isinstance(payload.get("time_range"), list) and len(payload["time_range"]) == 2:
        payload["time_range"] = tuple(payload["time_range"])
    return payload, list(entry.get("heights") or [])


def load():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict) and e.get("url")]
        return []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load queued links from %s", STATE_PATH)
        return []


def save(entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        logger.exception("Failed to save queued links to %s", STATE_PATH)
