import json
import os

from .. import config
from ..logging_setup import get_logger

logger = get_logger("settings")

DEFAULTS = {
    "theme": "dark",
    # Which browser's cookie store yt-dlp should borrow a signed-in session
    # from. None means "none of them": the app falls back to whatever the
    # user is signed in to in its OWN Browser tab, which is read from that
    # tab's profile and never touches an installed browser. Set to one of
    # "chrome"/"edge"/"firefox"/"brave"/"opera"/"vivaldi" by the sign-in
    # dialog, when the user explicitly picks that instead.
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
    # How many video/audio downloads run at once; the rest wait their turn.
    # "Start all" on thirty links used to launch thirty downloads together,
    # each fetching eight fragments in parallel -- a few hundred connections,
    # slower overall than a short queue, and the fastest way to get
    # rate-limited by YouTube.
    "max_concurrent": 3,
    # Height newly stacked links default to, or "best". Applied to the
    # nearest height a link actually offers at or below it.
    "preferred_quality": "best",
    # Container the Video tab's format picker starts on.
    "default_format": "mp4",
    # "cinematic" paints the app's own backdrop behind the glass panels;
    # "desktop" lets the real Windows acrylic blur of the desktop show
    # through instead, which is what every version before 2.5 did.
    "backdrop": "cinematic",
    # The backdrop's colour: sapphire, ruby, gold, emerald, obsidian,
    # amethyst or rose (ui_qt/palettes.py).
    "palette": "sapphire",
    # Set once the Torrent tab has made this app the magnet-link handler on
    # its first run, so unticking that box afterwards is never undone.
    "magnet_handler_offered": False,
    # Turns off the few animations the interface has (the sliding tab
    # indicator, hover fades) for anyone who finds motion distracting.
    "reduce_motion": False,
    # Asks GitHub once at startup whether a newer release exists.
    "check_app_updates": True,
}

MAX_CONCURRENT_RANGE = (1, 6)


def max_concurrent(settings):
    """The concurrency setting, clamped to a sane range whatever the file
    says -- a hand-edited 0 would otherwise stop every download starting."""
    lo, hi = MAX_CONCURRENT_RANGE
    try:
        value = int((settings or {}).get("max_concurrent", DEFAULTS["max_concurrent"]))
    except (TypeError, ValueError):
        value = DEFAULTS["max_concurrent"]
    return max(lo, min(hi, value))


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
        merged = _fresh_defaults()
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except FileNotFoundError:
        return _fresh_defaults()
    except Exception:
        logger.exception("Failed to load settings from %s, using defaults", config.SETTINGS_PATH)
        return _fresh_defaults()


def _fresh_defaults():
    """A deep-enough copy of DEFAULTS. dict(DEFAULTS) shared the nested
    save_dirs dict with the module constant, so set_save_dir() on one
    settings object quietly edited the defaults every later load starts
    from."""
    merged = dict(DEFAULTS)
    merged["save_dirs"] = dict(DEFAULTS["save_dirs"])
    return merged


def save_settings(settings):
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        logger.exception("Failed to save settings to %s", config.SETTINGS_PATH)
