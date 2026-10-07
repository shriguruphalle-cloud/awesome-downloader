"""The Music tab's own library: songs, albums and artists starred to keep
coming back to, and what was played recently. Kept on this PC only, in one
small file (encrypted with the rest when that's on -- secure_store).

Saved items are slimmed to what's needed to show and play them again; the
song's stream link is never kept for YouTube (it expires within hours)."""
import os

from .. import config
from ..logging_setup import get_logger
from . import secure_store

logger = get_logger("music_library")

PATH = os.path.join(config.APPDATA_DIR, "music_library.json")
KINDS = ("songs", "albums", "artists")
RECENT_MAX = 40
_KEEP = ("kind", "id", "title", "artist", "duration", "artwork", "source", "page_url", "ext", "license",
         "license_url", "attribution", "album", "album_browse", "artist_browse", "year", "album_id",
         "browse_id", "type", "subtitle")

_state = None


def _load():
    global _state
    if _state is None:
        try:
            data = secure_store.read_json(PATH)
        except FileNotFoundError:
            data = {}
        except (ValueError, OSError):
            logger.warning("Couldn't read the music library; starting a new one", exc_info=True)
            data = {}
        _state = {k: list(data.get(k) or []) for k in KINDS + ("recent",)}
    return _state


def _save():
    try:
        secure_store.write_json(PATH, _load())
    except OSError:
        logger.exception("Couldn't save the music library")


def slim(item):
    out = {k: item[k] for k in _KEEP if k in item and item[k] not in (None, "")}
    if item.get("source") in ("openverse", "archive", "local") and item.get("stream"):
        out["stream"] = item["stream"]      # an open library's file address doesn't expire
    out.setdefault("kind", "track")
    return out


def _bucket(item):
    return {"track": "songs", "album": "albums", "artist": "artists"}.get(item.get("kind") or "track", "songs")


def saved(kind):
    """Saved songs / albums / artists, newest first."""
    return [dict(x) for x in _load()[kind]]


def is_saved(item):
    return any(x["id"] == item["id"] for x in _load()[_bucket(item)])


def toggle(item):
    """Saves `item`, or un-saves it if it was saved. Returns whether it's saved now."""
    items = _load()[_bucket(item)]
    for i, x in enumerate(items):
        if x["id"] == item["id"]:
            del items[i]
            _save()
            return False
    items.insert(0, slim(item))
    _save()
    return True


def add_recent(track):
    items = _load()["recent"]
    items[:] = [x for x in items if x["id"] != track["id"]]
    items.insert(0, slim(track))
    del items[RECENT_MAX:]
    _save()


def recent():
    return [dict(x) for x in _load()["recent"]]


def forget_recent():
    _load()["recent"] = []
    _save()


def _reset_for_tests():
    global _state
    _state = None
