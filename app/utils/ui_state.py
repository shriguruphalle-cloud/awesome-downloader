"""Small bits of where-you-were state, each kept in a file of its own so a
problem with one can't lose the others: the tab that was open, and the song
the Music tab was playing (with its queue and position -- listening, so it's
kept encrypted with the rest of your history: secure_store)."""
import json
import os

from .. import config
from ..logging_setup import get_logger

logger = get_logger("ui_state")

UI_PATH = os.path.join(config.APPDATA_DIR, "ui_state.json")
MUSIC_PATH = os.path.join(config.APPDATA_DIR, "music_state.json")


def _read_plain(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:   # noqa: BLE001
        logger.warning("Couldn't read %s", path, exc_info=True)
        return {}


def get(key, default=None):
    return _read_plain(UI_PATH).get(key, default)


def put(key, value):
    data = _read_plain(UI_PATH)
    if data.get(key) == value:
        return
    data[key] = value
    try:
        os.makedirs(os.path.dirname(UI_PATH), exist_ok=True)
        tmp = UI_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, UI_PATH)
    except Exception:   # noqa: BLE001
        logger.warning("Couldn't save %s", UI_PATH, exc_info=True)


def load_music():
    from . import secure_store
    try:
        data = secure_store.read_json(MUSIC_PATH)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:   # noqa: BLE001
        logger.warning("Couldn't read the music state", exc_info=True)
        return {}


def save_music(state):
    from . import secure_store
    try:
        os.makedirs(os.path.dirname(MUSIC_PATH), exist_ok=True)
        secure_store.write_json(MUSIC_PATH, state)
        return True
    except Exception:   # noqa: BLE001
        logger.warning("Couldn't save the music state", exc_info=True)
        return False
