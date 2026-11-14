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
RECENT_MAX = 250                   # the last 250 songs played (about 175 KB, synced as one item)
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


_let_go = (None, 0)      # the last one un-saved, and where it was


def toggle(item):
    """Saves `item`, or un-saves it if it was saved. Returns whether it's saved now."""
    global _let_go
    items = _load()[_bucket(item)]
    for i, x in enumerate(items):
        if x["id"] == item["id"]:
            del items[i]
            _let_go = (item["id"], i)
            _save()
            return False
    # taken straight back (Undo): where it was, not at the top as a new one
    at = _let_go[1] if _let_go[0] == item["id"] else 0
    _let_go = (None, 0)
    items.insert(min(at, len(items)), slim(item))
    _save()
    return True


def save_many(items):
    """Saves each of `items` (the first ends up lowest, as if saved first) that isn't saved yet, writing the file once:
    a thousand songs one by one would rewrite it a thousand times. Returns how many were new."""
    state = _load()
    have = {}
    added = 0
    for item in items:
        kind = _bucket(item)
        known = have.setdefault(kind, {x["id"] for x in state[kind]})
        if item.get("id") and item["id"] not in known:
            known.add(item["id"])
            state[kind].insert(0, slim(item))
            added += 1
    if added:
        _save()
    return added


def merge_playlist(title, tracks, source="", artwork=None):
    """The playlist called `title` with `tracks` added (those it hasn't got), or a new one."""
    for p in _load().setdefault("playlists", []):
        if p.get("title") == title:
            known = {t.get("id") for t in p.get("tracks") or []}
            p.setdefault("tracks", []).extend(slim(t) for t in tracks if t.get("id") not in known)
            _save()
            return dict(p)
    return create_playlist(title, tracks, source=source, artwork=artwork)


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


def create_playlist(title, tracks=(), source="", artwork=None, link=""):
    import time
    import uuid
    pl = {"id": "pl:" + uuid.uuid4().hex[:12], "title": (title or "My playlist").strip()[:80], "source": source,
          "artwork": artwork, "tracks": [slim(t) for t in tracks], "created": int(time.time())}
    if link:
        # kept in sync with where it came from: new songs added there arrive here too
        pl.update(link=link, synced=int(time.time()), removed=[])
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
            gone = [t for t in p["tracks"] if t["id"] == track_id]
            if p.get("link") and gone:
                # taken out of a synced playlist: a sync never brings it back
                from app.core import upnext
                p.setdefault("removed", []).extend(k for k in (upnext.song_key(t) for t in gone) if k)
            p["tracks"] = [t for t in p["tracks"] if t["id"] != track_id]
            _save()


def synced_add(pid, tracks, when):
    """A sync's new songs, appended (none already there or taken out by hand); how many were added."""
    from app.core import upnext
    for p in _load().setdefault("playlists", []):
        if p["id"] == pid:
            have = {upnext.song_key(t) for t in p["tracks"]} | set(p.get("removed") or []) | {t["id"] for t in p["tracks"]}
            have.discard("")
            new = []
            for t in tracks:
                k = upnext.song_key(t)
                if t.get("id") in have or (k and k in have):
                    continue
                have.update(x for x in (t.get("id"), k) if x)
                new.append(slim(t))
            p["tracks"].extend(new)
            p["synced"] = int(when)
            _save()
            return len(new)
    return 0


def rename_playlist(pid, title):
    for p in _load().setdefault("playlists", []):
        if p["id"] == pid:
            p["title"] = title.strip()[:80] or p["title"]
            _save()


def delete_playlist(pid):
    st = _load()
    st["playlists"] = [p for p in st.setdefault("playlists", []) if p["id"] != pid]
    _save()
