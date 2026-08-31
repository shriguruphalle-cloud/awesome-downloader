"""Persists the Browser tab's own bookmarks and visited-page history --
separate from download_history.py, which tracks completed *downloads*, not
pages browsed. Same load/save shape as that module on purpose.
"""
import json
import os
import time

from .. import config
from ..logging_setup import get_logger

logger = get_logger("browser_data")

BOOKMARKS_PATH = os.path.join(config.APPDATA_DIR, "browser_bookmarks.json")
HISTORY_PATH = os.path.join(config.APPDATA_DIR, "browser_history.json")
SHORTCUTS_PATH = os.path.join(config.APPDATA_DIR, "browser_shortcuts.json")
ADBLOCK_DISABLED_PATH = os.path.join(config.APPDATA_DIR, "browser_adblock_disabled.json")
PREFS_PATH = os.path.join(config.APPDATA_DIR, "browser_prefs.json")
MAX_HISTORY_ENTRIES = 300

# Seeded on first run only (the file not existing yet). Just the one tile:
# this is a downloader, and YouTube is what it is opened for -- a Google
# shortcut inside a browser whose address bar already searches Google was
# a tile that did nothing the toolbar didn't. Anything else on the new-tab
# page is there because the user added it.
_DEFAULT_SHORTCUTS = [
    {"url": "https://www.youtube.com", "title": "YouTube"},
]


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load %s", path)
        return []


def _save(path, entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        logger.exception("Failed to save %s", path)


def load_bookmarks():
    return _load(BOOKMARKS_PATH)


def is_bookmarked(bookmarks, url):
    return any(b.get("url") == url for b in bookmarks)


def add_bookmark(url, title):
    bookmarks = load_bookmarks()
    if is_bookmarked(bookmarks, url):
        return bookmarks
    bookmarks.append({"url": url, "title": title or url})
    _save(BOOKMARKS_PATH, bookmarks)
    return bookmarks


def remove_bookmark(url):
    bookmarks = [b for b in load_bookmarks() if b.get("url") != url]
    _save(BOOKMARKS_PATH, bookmarks)
    return bookmarks


def load_history():
    return _load(HISTORY_PATH)


def add_history_entry(url, title):
    """Newest-first, capped list. A page reloaded/revisited moves back to
    the top rather than growing a second entry -- the point of a browser
    history list is 'what did I look at recently', not a full navigation
    log."""
    entries = [e for e in load_history() if e.get("url") != url]
    entries.insert(0, {"url": url, "title": title or url, "visited_at": time.time()})
    _save(HISTORY_PATH, entries[:MAX_HISTORY_ENTRIES])


def clear_history():
    _save(HISTORY_PATH, [])


def load_shortcuts():
    """Distinguishes "file genuinely has zero shortcuts" (the user removed
    every default) from "first run, nothing saved yet" via FileNotFoundError
    -- only the second case falls back to the seeded defaults."""
    try:
        with open(SHORTCUTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return list(_DEFAULT_SHORTCUTS)
    except Exception:
        logger.exception("Failed to load %s", SHORTCUTS_PATH)
        return list(_DEFAULT_SHORTCUTS)


def add_shortcut(url, title):
    shortcuts = load_shortcuts()
    if any(s.get("url") == url for s in shortcuts):
        return shortcuts
    shortcuts.append({"url": url, "title": title or url})
    _save(SHORTCUTS_PATH, shortcuts)
    return shortcuts


def remove_shortcut(url):
    shortcuts = [s for s in load_shortcuts() if s.get("url") != url]
    _save(SHORTCUTS_PATH, shortcuts)
    return shortcuts


def load_adblock_disabled_hosts():
    try:
        with open(ADBLOCK_DISABLED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except FileNotFoundError:
        return set()
    except Exception:
        logger.exception("Failed to load %s", ADBLOCK_DISABLED_PATH)
        return set()


def set_adblock_disabled(host, disabled):
    """Per-site override for the always-on ad blocker -- some sites break
    under it, and a global on/off would mean giving up blocking everywhere
    just to fix one site (matches uBlock's own per-site toggle)."""
    hosts = load_adblock_disabled_hosts()
    if disabled:
        hosts.add(host)
    else:
        hosts.discard(host)
    _save(ADBLOCK_DISABLED_PATH, sorted(hosts))
    return hosts


def _load_prefs():
    """A plain dict of small scalar Browser-tab preferences (accent
    choice, home background image path) -- separate from the list-shaped
    files above, which _load()/_save() assume."""
    try:
        with open(PREFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed to load %s", PREFS_PATH)
        return {}


def _save_prefs(prefs):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        logger.exception("Failed to save %s", PREFS_PATH)


def get_browser_accent():
    """"warm" (the default, off-black/terracotta) or "classic" (the same
    blue/near-black palette every other tab uses)."""
    return _load_prefs().get("accent", "warm")


def set_browser_accent(name):
    prefs = _load_prefs()
    prefs["accent"] = name
    _save_prefs(prefs)


def get_home_background_image():
    """Absolute path to a user-picked home-page background image, or None
    for the plain theme background -- same idea as Chrome's new-tab-page
    customize-background."""
    path = _load_prefs().get("home_background_image")
    return path if path and os.path.exists(path) else None


def set_home_background_image(path):
    prefs = _load_prefs()
    prefs["home_background_image"] = path
    _save_prefs(prefs)


def clear_home_background_image():
    prefs = _load_prefs()
    prefs.pop("home_background_image", None)
    _save_prefs(prefs)
