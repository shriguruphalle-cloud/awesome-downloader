"""Persists the Browser tab's own bookmarks and visited-page history --
separate from download_history.py, which tracks completed *downloads*, not
pages browsed. Same load/save shape as that module on purpose.
"""
import json
import os
import time

from .. import config
from . import secure_store
from ..logging_setup import get_logger

logger = get_logger("browser_data")

BOOKMARKS_PATH = os.path.join(config.APPDATA_DIR, "browser_bookmarks.json")
HISTORY_PATH = os.path.join(config.APPDATA_DIR, "browser_history.json")
SHORTCUTS_PATH = os.path.join(config.APPDATA_DIR, "browser_shortcuts.json")
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
        data = secure_store.read_json(path)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.exception("Failed to load %s", path)
        return []


def _save(path, entries):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        secure_store.write_json(path, entries)
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


def update_bookmark(old_url, url, title):
    """A bookmark renamed and/or pointed somewhere else, in place: it keeps
    its spot in the list. If the new address is already another bookmark,
    that one goes -- one bookmark per address."""
    url = (url or "").strip()
    bookmarks = load_bookmarks()
    if not url or not is_bookmarked(bookmarks, old_url):
        return bookmarks
    out = []
    for b in bookmarks:
        if b.get("url") == old_url:
            out.append(dict(b, url=url, title=(title or "").strip() or url))
        elif b.get("url") != url:
            out.append(b)
    _save(BOOKMARKS_PATH, out)
    return out


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
    entries = load_history()
    previous = next((e for e in entries if e.get("url") == url), None)
    visits = int((previous or {}).get("visits") or 0) + 1
    entries = [e for e in entries if e.get("url") != url]
    entries.insert(0, {"url": url, "title": title or url, "visited_at": time.time(), "visits": visits})
    _save(HISTORY_PATH, entries[:MAX_HISTORY_ENTRIES])


def _host(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def site_name(host, titles=()):
    """A site's own name for a tile: the part its pages' titles share
    ("... - YouTube", "... | Reddit"), else the host made presentable."""
    counts = {}
    for title in titles:
        for sep in (" - ", " | ", " — ", " · ", " – "):
            if sep in (title or ""):
                tail = title.rsplit(sep, 1)[1].strip()
                if 1 < len(tail) <= 24:
                    counts[tail] = counts.get(tail, 0) + 1
                break
    if counts:
        name, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= 2 or len(titles) == 1:
            return name
    bare = host[4:] if host.startswith("www.") else host
    stem = bare.split(".")[0] if bare.count(".") >= 1 else bare
    return stem[:1].upper() + stem[1:] if stem else host


def top_sites(limit=8, exclude_hosts=()):
    """The sites visited most, weighted toward recent visits (a visit two
    weeks ago counts half), one entry per site: [{url, title, host}]."""
    now = time.time()
    exclude = {h.lower().removeprefix("www.") for h in exclude_hosts if h}
    sites = {}
    for e in load_history():
        url = e.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        host = _host(url)
        bare = host.removeprefix("www.")
        if not host or bare in exclude:
            continue
        age_days = max(0.0, (now - float(e.get("visited_at") or now)) / 86400.0)
        weight = int(e.get("visits") or 1) * (0.5 ** (age_days / 14.0))
        site = sites.setdefault(bare, {"score": 0.0, "host": host, "titles": [], "scheme": url.split(":", 1)[0]})
        site["score"] += weight
        site["titles"].append(e.get("title") or "")
    ranked = sorted(sites.values(), key=lambda s: s["score"], reverse=True)[:limit]
    return [{"url": "%s://%s/" % (s["scheme"], s["host"]), "host": s["host"],
             "title": site_name(s["host"], s["titles"])} for s in ranked]


def clear_history():
    _save(HISTORY_PATH, [])


def load_shortcuts():
    """Distinguishes "file genuinely has zero shortcuts" (the user removed
    every default) from "first run, nothing saved yet" via FileNotFoundError
    -- only the second case falls back to the seeded defaults."""
    try:
        data = secure_store.read_json(SHORTCUTS_PATH)
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


def _load_prefs():
    """A plain dict of small scalar Browser-tab preferences (the home page's
    wallpaper and options, the bookmarks bar) -- separate from the
    list-shaped files above, which _load()/_save() assume."""
    try:
        data = secure_store.read_json(PREFS_PATH)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed to load %s", PREFS_PATH)
        return {}


def _save_prefs(prefs):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        secure_store.write_json(PREFS_PATH, prefs)
    except Exception:
        logger.exception("Failed to save %s", PREFS_PATH)


def get_home_background_image():
    """Absolute path to a user-picked home-page background image, or None
    for the plain theme background -- same idea as Chrome's new-tab-page
    customize-background."""
    path = _load_prefs().get("home_background_image")
    return path if path and os.path.exists(path) else None


def clear_home_background_image():
    prefs = _load_prefs()
    prefs.pop("home_background_image", None)
    _save_prefs(prefs)


# ---- search engine ---------------------------------------------------------
# name -> (label, query URL prefix). The query is percent-encoded and appended.
SEARCH_ENGINES = {
    "google": ("Google", "https://www.google.com/search?q="),
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q="),
    "bing": ("Bing", "https://www.bing.com/search?q="),
    "brave": ("Brave Search", "https://search.brave.com/search?q="),
    "yandex": ("Yandex", "https://yandex.com/search/?text="),
}
DEFAULT_SEARCH_ENGINE = "duckduckgo"


def get_search_engine():
    name = _load_prefs().get("search_engine", DEFAULT_SEARCH_ENGINE)
    return name if name in SEARCH_ENGINES else DEFAULT_SEARCH_ENGINE


def set_search_engine(name):
    if name not in SEARCH_ENGINES:
        return
    prefs = _load_prefs()
    prefs["search_engine"] = name
    _save_prefs(prefs)


def search_url(query):
    import urllib.parse
    return SEARCH_ENGINES[get_search_engine()][1] + urllib.parse.quote_plus(query)


# ---- small switches ----------------------------------------------------------
def get_pref(name, default=None):
    return _load_prefs().get(name, default)


def set_pref(name, value):
    prefs = _load_prefs()
    prefs[name] = value
    _save_prefs(prefs)


# ---- session (the tabs that were open) ----------------------------------------
def load_session():
    """(urls, current_index) of the tabs open when the app last closed --
    whether or not they're reopened at start (that's the caller's choice;
    with it off, Ctrl+Shift+T brings them back)."""
    data = _load_prefs().get("session") or {}
    urls = [u for u in data.get("tabs", []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
    current = data.get("current", 0)
    if not isinstance(current, int) or not 0 <= current < max(1, len(urls)):
        current = 0
    return urls[:30], current


def save_session(urls, current):
    prefs = _load_prefs()
    prefs["session"] = {"tabs": list(urls)[:30], "current": int(current)}
    _save_prefs(prefs)


# ---- recently closed (Ctrl+Shift+T), kept across restarts ------------------------
# Newest last. Each entry is {"url": ...} for a tab, or {"session": [urls]} for
# the whole set of tabs a previous run left open. Private tabs never get here.
CLOSED_LIMIT = 25


def load_closed():
    out = []
    for e in _load_prefs().get("closed") or []:
        if isinstance(e, dict) and isinstance(e.get("url"), str) and e["url"].startswith(("http://", "https://")):
            out.append({"url": e["url"]})
        elif isinstance(e, dict) and isinstance(e.get("session"), list):
            urls = [u for u in e["session"] if isinstance(u, str) and u.startswith(("http://", "https://"))]
            if urls:
                out.append({"session": urls[:30]})
    return out[-CLOSED_LIMIT:]


def save_closed(entries):
    prefs = _load_prefs()
    prefs["closed"] = list(entries)[-CLOSED_LIMIT:]
    _save_prefs(prefs)
