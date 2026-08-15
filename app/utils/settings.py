import json
import os

from .. import config
from ..logging_setup import get_logger

logger = get_logger("settings")

DEFAULTS = {
    "theme": "dark",
    # None = no cookies passed to yt-dlp; otherwise one of "chrome"/"edge"/"firefox".
    # Wired up in Phase B (universal link support) -- present now so the settings
    # file format doesn't need to change shape later.
    "cookies_from_browser": None,
    # Per-tab "Save to" folder. None = fall back to the matching
    # config.DEFAULT_*_DIR, same None-means-default convention as above.
    # Without this the folder picked in each tab was reset to the default on
    # every launch, so a customer who downloads somewhere other than the
    # default had to re-pick it every single session.
    "save_dirs": {
        "video": None,
        "torrent": None,
        "images": None,
    },
}


def get_save_dir(settings, tab, default):
    """Remembered 'Save to' folder for a tab, or `default` if none is stored
    (or the stored one has since been deleted/unmounted)."""
    if not settings:
        return default
    stored = (settings.get("save_dirs") or {}).get(tab)
    if stored and os.path.isdir(stored):
        return stored
    return default


def set_save_dir(settings, tab, path):
    """Remembers `path` as the 'Save to' folder for a tab and writes it to
    disk. No-op without a settings dict (e.g. a tab constructed standalone
    in a test)."""
    if settings is None or not path:
        return
    dirs = settings.setdefault("save_dirs", {})
    if dirs.get(tab) == path:
        return  # unchanged -- don't rewrite the file on every keystroke
    dirs[tab] = path
    save_settings(settings)


def load_settings():
    try:
        with open(config.SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except FileNotFoundError:
        return dict(DEFAULTS)
    except Exception:
        logger.exception("Failed to load settings from %s, using defaults", config.SETTINGS_PATH)
        return dict(DEFAULTS)


def save_settings(settings):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        logger.exception("Failed to save settings to %s", config.SETTINGS_PATH)
