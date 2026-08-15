"""Opt-in 'launch at Windows startup' toggle, backed by the per-user Run key.

This is the app changing its own launch behavior in response to an explicit
user action (a checkbox), the same category as any other setting -- not
something done to the user's system from outside the app.
"""
import sys

from .. import config
from ..logging_setup import get_logger

logger = get_logger("startup")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AwesomeDownloader"


def _launch_command():
    if config.IS_FROZEN:
        return f'"{sys.executable}"'
    # Running from source: launch the no-console entry point with the same
    # interpreter that's running right now.
    import os
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    # main_qt.py, not the old main.pyw -- that pointed at the retired
    # CustomTkinter entry point, so "launch at startup" would have started
    # the previous UI (or, once it was removed, nothing at all).
    entry = os.path.join(config.BASE_DIR, "main_qt.py")
    return f'"{pythonw}" "{entry}"'


def is_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
    except Exception:
        logger.exception("Failed to read startup registry value")
        return False


def set_enabled(enabled):
    """Returns True on success. Never raises -- caller decides how to surface
    a failure (e.g. leave the checkbox unchanged)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        logger.exception("Failed to %s startup registration", "enable" if enabled else "disable")
        return False
