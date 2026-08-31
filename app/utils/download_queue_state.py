"""Persists which video/audio downloads were still in progress so they can
be resumed (not restarted from scratch) the next time the app starts.

Deliberately simple, the same approach as torrent_state.py: we only
remember *what* to re-download (url, save dir, mode, quality/format,
title) -- not byte-level progress. Re-invoking yt-dlp with the same
url+save_dir resumes the existing .part file via HTTP Range requests
automatically (yt-dlp's own default continuedl behavior), so nothing else
needs to be tracked.
"""
import json
import os

from .. import config
from ..logging_setup import get_logger

logger = get_logger("download_queue_state")

STATE_PATH = os.path.join(config.APPDATA_DIR, "pending_downloads.json")


def load():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load pending-download state from %s", STATE_PATH)
        return []


def save(entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        logger.exception("Failed to save pending-download state to %s", STATE_PATH)
