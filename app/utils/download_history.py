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
