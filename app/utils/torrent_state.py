"""Persists which torrents are active so they can be re-added (and resume
downloading) the next time the app starts.

Deliberately simple: we only remember *what* to re-add (magnet URI or
.torrent file path, save location, which files were selected) -- not
byte-level progress. Re-adding a torrent with the same save path makes
libtorrent re-hash-check whatever's already on disk and resume from there
automatically; that's slower than a real .fastresume file would be, but is a
lot less code for a one-time-per-restart cost.
"""
import json
import os

from .. import config
from . import secure_store
from ..logging_setup import get_logger

logger = get_logger("torrent_state")

STATE_PATH = os.path.join(config.APPDATA_DIR, "torrents.json")


def load():
    try:
        data = secure_store.read_json(STATE_PATH)
        if isinstance(data, list):
            return data
        return []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load torrent state from %s", STATE_PATH)
        return []


def save(entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        secure_store.write_json(STATE_PATH, entries)
    except Exception:
        logger.exception("Failed to save torrent state to %s", STATE_PATH)
