"""The app's saved data on disk -- download history, browser history and
bookmarks, the queues -- encrypted with Windows' own data protection (DPAPI).

DPAPI ties the data to the Windows account that wrote it: the same person
on the same PC reads it back without a password, while another account on
the machine, or someone with the disk out of the computer, gets ciphertext.
It's the same protection the browser engine uses for its own saved sign-ins.

A file starts with MAGIC when encrypted; a plain JSON file (from before this
existed, or with encryption turned off in Settings) is still read, and is
encrypted the next time it is saved. Settings themselves stay plain: they
hold nothing private, and being readable is what makes them fixable.
"""
import ctypes
import json
import os
import shutil
import sys
import time
from ctypes import wintypes

from .. import config
from ..logging_setup import get_logger

logger = get_logger("secure_store")

MAGIC = b"AWDPROT1\n"
# Ties the blobs to this app: another program running as the same person
# can't simply hand one to DPAPI and read it.
_ENTROPY = b"Awesome Downloader saved data v1"

# Set from Settings at start-up (main_qt) and when the switch is flipped.
ENCRYPT = True

# Every file this covers, for "encrypt now" / "decrypt now" from Settings.
_FILES = set()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data, protect):
    if sys.platform != "win32":
        raise OSError("Windows data protection isn't available here")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    flags = 0x1     # CRYPTPROTECT_UI_FORBIDDEN: never put up a prompt
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "Awesome Downloader", ctypes.byref(ent),
                                      None, None, flags, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent),
                                        None, None, flags, ctypes.byref(out))
    if not ok:
        raise OSError("Windows data protection failed (error %d)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data):
    return MAGIC + _dpapi(data, True)


def unprotect(raw):
    return _dpapi(raw[len(MAGIC):], False)


def is_encrypted(path):
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_json(path):
    """The saved value at `path`. Raises FileNotFoundError when there's none
    (callers fall back to their defaults), ValueError when it can't be read --
    a file encrypted under another Windows account, say. That file is kept
    aside, renamed, rather than overwritten by the next save."""
    _FILES.add(path)
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(MAGIC):
        try:
            raw = unprotect(raw)
        except OSError as exc:
            keep = "%s.unreadable-%d" % (path, int(time.time()))
            try:
                shutil.copy2(path, keep)
            except OSError:
                pass
            logger.error("Couldn't decrypt %s (%s); kept a copy as %s", path, exc, keep)
            raise ValueError("%s is encrypted for another Windows account" % os.path.basename(path))
    return json.loads(raw.decode("utf-8"))


def write_json(path, value):
    """Saves `value` at `path` -- encrypted unless turned off -- through a
    temporary file, so a crash mid-write never leaves half a file."""
    _FILES.add(path)
    data = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    if ENCRYPT:
        try:
            data = protect(data)
        except OSError:
            logger.exception("Couldn't encrypt %s; saving it plain this time", path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def covered_paths():
    """Every file this protects."""
    from . import (browser_data, download_history, download_queue_state, music_library, torrent_state,
                   video_queue_state)
    return [download_history.HISTORY_PATH, browser_data.BOOKMARKS_PATH, browser_data.HISTORY_PATH,
            browser_data.SHORTCUTS_PATH, browser_data.PREFS_PATH, video_queue_state.STATE_PATH,
            download_queue_state.STATE_PATH, torrent_state.STATE_PATH, music_library.PATH,
            os.path.join(config.APPDATA_DIR, "music_taste.json")]


def migrate():
    """At start-up: anything saved before encryption was on (or while it was
    off) is encrypted now, rather than waiting for its next save."""
    if not ENCRYPT:
        return 0
    plain = [p for p in covered_paths() if os.path.exists(p) and not is_encrypted(p)]
    return rewrite_all(plain) if plain else 0


def rewrite_all(paths=None):
    """Re-saves every covered file in the current mode: after the Settings
    switch, so turning encryption on protects what's already there, and
    turning it off leaves readable files. Returns how many were rewritten."""
    done = 0
    for path in sorted(paths or _FILES):
        try:
            value = read_json(path)
        except FileNotFoundError:
            continue
        except Exception:   # noqa: BLE001 -- left as it is
            logger.exception("Couldn't re-save %s", path)
            continue
        write_json(path, value)
        done += 1
    return done
