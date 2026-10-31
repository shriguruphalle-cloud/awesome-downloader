"""A crash, noted as it happens and sent the next time the app starts, so the owner's dashboard can show what broke (the
phone app's Crashes.kt). What goes: the app's version, "Windows 11", the error's kind and the lines of code it passed
through. Deliberately not the error's own words, which can hold an address or a file name; and no account.
"""
import os
import platform
import sys
import threading
import traceback

from app import config
from app.core import social
from app.logging_setup import get_logger

logger = get_logger("crash_report")

PENDING = os.path.join(config.APPDATA_DIR, "crash_pending.txt")


def describe(kind, tb):
    """The error's kind and where it happened: file (from the app's folder on), line and function."""
    rows = [kind.__name__]
    for f in traceback.extract_tb(tb)[-12:]:
        rows.append("%s:%d %s" % (os.path.basename(f.filename), f.lineno or 0, f.name))
    return "\n".join(rows)[:1500]


def _note(kind, tb):
    try:
        if not os.path.exists(PENDING):          # the first of a run says most
            with open(PENDING, "w", encoding="utf-8") as f:
                f.write(describe(kind, tb))
    except OSError:
        pass


def send_pending():
    """What the last run left, to the server; kept for next time if that fails."""
    try:
        with open(PENDING, encoding="utf-8") as f:
            message = f.read()
    except OSError:
        return False
    try:
        social.call("POST", "/v1/crash", {"version": social.APP_VERSION_TAG,
                                          "device": ("%s %s" % (platform.system(), platform.release()))[:60],
                                          "message": message}, auth=False)
        os.remove(PENDING)
        return True
    except (social.SocialError, OSError):
        logger.debug("Couldn't send a crash note")
        return False


def watch():
    """From now on, crashes are noted; the last run's is sent, off this thread."""
    before = sys.excepthook

    def hook(kind, value, tb):
        _note(kind, tb)
        before(kind, value, tb)
    sys.excepthook = hook
    before_thread = threading.excepthook

    def thread_hook(args):
        _note(args.exc_type, args.exc_traceback)
        before_thread(args)
    threading.excepthook = thread_hook
    threading.Thread(target=send_pending, daemon=True, name="crash-send").start()
