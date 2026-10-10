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
        _state = {k: list(data.get(k) or []) for k in KINDS + ("recent", "playlists")}
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


# ---- playlists: made here, or imported from another app ----------------------------
def playlists():
    """[{"id", "title", "source", "tracks": [...], "created"}], newest first."""
    return [dict(p) for p in _load().setdefault("playlists", [])]


def playlist(pid):
    return next((dict(p) for p in _load().setdefault("playlists", []) if p["id"] == pid), None)


def create_playlist(title, tracks=(), source="", artwork=None):
    import time
    import uuid
    pl = {"id": "pl:" + uuid.uuid4().hex[:12], "title": (title or "My playlist").strip()[:80], "source": source,
          "artwork": artwork, "tracks": [slim(t) for t in tracks], "created": int(time.time())}
    _load().setdefault("playlists", []).insert(0, pl)
    _save()
    return dict(pl)


def add_to_playlist(pid, track):
    """Adds `track`; False if it was already there."""
    for p in _load().setdefault("playlists", []):
        if p["id"] == pid:
            if any(t["id"] == track["id"] for t in p["tracks"]):
                return False
            p["tracks"].append(slim(track))
            _save()
            return True
    return False


def remove_from_playlist(pid, track_id):
    for p in _load().setdefault("playlists", []):
        if p["id"] == pid:
            p["tracks"] = [t for t in p["tracks"] if t["id"] != track_id]
            _save()


def rename_playlist(pid, title):
    for p in _load().setdefault("playlists", []):
        if p["id"] == pid:
            p["title"] = title.strip()[:80] or p["title"]
            _save()


def delete_playlist(pid):
    st = _load()
    st["playlists"] = [p for p in st.setdefault("playlists", []) if p["id"] != pid]
    _save()
