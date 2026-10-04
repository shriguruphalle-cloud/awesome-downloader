"""Persists a log of completed downloads (video/audio from the Video
Downloader tab, and fully-downloaded torrents) for the Download History tab:
title, kind, the actual file (for Play) and its folder (for Open Folder).
"""
import json
import os
import time

from .. import config
from ..logging_setup import get_logger

logger = get_logger("download_history")

HISTORY_PATH = os.path.join(config.APPDATA_DIR, "history.json")
MAX_ENTRIES = 500

# Plain-callable observers, not a Qt Signal -- this module stays
# framework-agnostic like the rest of app/utils. add_entry() is called from
# five different tabs, some from background download threads (images_tab.py's
# _download_thread) and some from the GUI thread (video_tab.py/browser_tab.py's
# _on_download_done slots), so whatever gets registered here must itself be
# safe to call from either -- a Qt Signal's bound .emit method qualifies
# (that's the standard cross-thread-safe pattern already used throughout this
# codebase's own progress_hook -> _progress_sig.emit() calls), a direct widget
# touch would not.
_listeners = []


def register_listener(fn):
    _listeners.append(fn)


def load():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load download history from %s", HISTORY_PATH)
        return []


def add_entry(kind, title, file_path, folder_path, size_bytes=0):
    """kind: 'video' | 'audio' | 'torrent'. Newest-first list, capped at
    MAX_ENTRIES so this file can't grow without bound over months of use."""
    entries = load()
    entries.insert(0, {
        "kind": kind,
        "title": title,
        "file_path": file_path,
        "folder_path": folder_path,
        "size_bytes": size_bytes,
        "completed_at": time.time(),
    })
    _save(entries[:MAX_ENTRIES])
    for fn in _listeners:
        try:
            fn()
        except Exception:
            logger.exception("download_history listener failed")


def remove_entry(index):
    """Removes just this one entry from the history list -- never touches
    the actual downloaded file, only the history.json record of it."""
    entries = load()
    if 0 <= index < len(entries):
        entries.pop(index)
        _save(entries)


def remove_entries(indices):
    """Removes several entries at once (History's multi-select). Indices
    are positions in load()'s list. Files are not touched here."""
    drop = set(int(i) for i in indices)
    entries = [e for i, e in enumerate(load()) if i not in drop]
    _save(entries)


def first_completion(kind, title):
    """When an entry for this download was first recorded, or None."""
    times = [e.get("completed_at") for e in load()
             if e.get("kind") == kind and e.get("title") == title and e.get("completed_at")]
    return min(times) if times else None


def collapse_duplicates(kind="torrent"):
    """Keeps only the oldest entry per (title, file) of `kind`.

    Until 2.5, a finished torrent was re-checked from scratch at every
    launch, and reaching 100% again recorded it in History again -- one
    entry per launch, each dated "today". Returns how many were dropped."""
    entries = load()
    oldest = {}
    for i, e in enumerate(entries):
        if e.get("kind") != kind:
            continue
        key = (e.get("title"), e.get("file_path"))
        best = oldest.get(key)
        if best is None or (e.get("completed_at") or 0) < (entries[best].get("completed_at") or 0):
            oldest[key] = i
    keep = set(oldest.values())
    kept = [e for i, e in enumerate(entries) if e.get("kind") != kind or i in keep]
    dropped = len(entries) - len(kept)
    if dropped:
        _save(kept)
    return dropped


def correct_torrent_times():
    """Dates each torrent entry by its file, where the file is older than
    the entry -- earlier builds recorded a torrent again whenever a launch's
    re-check reached 100%, dated that launch. A file's modified time is when
    its last piece was written; re-checking never changes it."""
    entries = load()
    changed = False
    for e in entries:
        if e.get("kind") != "torrent":
            continue
        path = e.get("file_path")
        try:
            written = os.path.getmtime(path) if path and os.path.exists(path) else None
        except OSError:
            written = None
        if written and written < (e.get("completed_at") or 0) - 120:
            e["completed_at"] = written
            changed = True
    if changed:
        _save(entries)
    return changed


def clear_all():
    """Wipes every history entry -- same file-safety guarantee as
    remove_entry (metadata only, no files touched)."""
    _save([])


def _save(entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        logger.exception("Failed to save download history to %s", HISTORY_PATH)
