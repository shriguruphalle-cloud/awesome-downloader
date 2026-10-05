"""Opt-in 'open magnet: links with this app' toggle, backed by the per-user
protocol handler key.

Same category as startup.py: the app registering itself to handle a link type
in response to an explicit user action (a checkbox), not something done to the
user's machine behind their back. Everything here is under HKCU -- nothing
system-wide, no admin rights, and unregister() puts it back exactly the way it
was by removing only the key this module created.

Why HKCU\\Software\\Classes and not HKCR: that subtree is merged into
HKEY_CLASSES_ROOT and takes precedence over the machine-wide registration for
the current user, so this wins over an already-installed client (uTorrent,
qBittorrent, ...) without touching that client's own registration at all --
untick the box and theirs is simply in front again.

Caveat worth knowing: if Windows has recorded an explicit UserChoice for the
magnet protocol (set via Settings > Default apps), that is hash-protected and
takes precedence over this. is_default() reports False in that case even
straight after register(), which is the honest answer -- the fix is the
Windows Settings UI, not a registry write, and deliberately so.
"""
import os
import sys

from .. import config
from ..logging_setup import get_logger

logger = get_logger("protocol_handler")

_PROG_ID = "AwesomeDownloader.Magnet"
_CLASSES = r"Software\Classes"
# Windows' Settings > Default apps only lists an app for a link type if the app
# declares it: a Capabilities key, named in RegisteredApplications. Without
# them the app wasn't in the list to pick when Windows had magnet: locked to
# another client.
REGISTERED_NAME = "Awesome Downloader"
_CAPABILITIES = r"Software\AwesomeDownloader\Capabilities"
_REGISTERED_APPS = r"Software\RegisteredApplications"
_USERCHOICE = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"
               r"\UrlAssociations\magnet\UserChoice")


def _launch_command():
    """Command line Windows runs for a clicked magnet link. '%1' is the URI."""
    if config.IS_FROZEN:
        return f'"{sys.executable}" "%1"'
    # From source: pythonw (no console flash) running the current entry point.
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    entry = os.path.join(config.BASE_DIR, "main_qt.py")
    return f'"{pythonw}" "{entry}" "%1"'


def _icon_path():
    if config.IS_FROZEN:
        return sys.executable
    return os.path.join(config.BASE_DIR, "app_icon.ico")


def userchoice_owner():
    """ProgId Windows has locked the magnet protocol to via Settings > Default
    apps, or None if there's no such lock. When this returns something other
    than our own ProgId, registering has no visible effect."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USERCHOICE) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to read magnet UserChoice")
        return None


def is_default():
    """True only if a clicked magnet link would actually reach this app."""
    if sys.platform != "win32":
        return False
    owner = userchoice_owner()
    if owner is not None and owner != _PROG_ID:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             rf"{_CLASSES}\magnet\shell\open\command") as key:
            return winreg.QueryValueEx(key, "")[0] == _launch_command()
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("Failed to read magnet handler registration")
        return False


def register():
    """Point magnet: links at this app for the current user. Returns True on
    success. Rewrites the command every time so it stays correct after the
    app is moved, reinstalled, or switched between source and frozen."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        # The ProgId describing this app as a magnet handler...
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES}\{_PROG_ID}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{config.APP_NAME} magnet link")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES}\{_PROG_ID}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _icon_path())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES}\{_PROG_ID}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _launch_command())

        # ...and the magnet protocol itself pointing at that command. The
        # "URL Protocol" value (empty string, presence is what matters) is
        # what marks a key as a URL scheme handler rather than a file type.
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{_CLASSES}\magnet") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Magnet Link")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES}\magnet\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _icon_path())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               rf"{_CLASSES}\magnet\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _launch_command())

        # ...and the declaration that puts the app in Default apps.
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CAPABILITIES) as key:
            winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, REGISTERED_NAME)
            winreg.SetValueEx(key, "ApplicationDescription", 0, winreg.REG_SZ,
                              "Video, audio, images and torrents in one window")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CAPABILITIES + r"\URLAssociations") as key:
            winreg.SetValueEx(key, "magnet", 0, winreg.REG_SZ, _PROG_ID)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTERED_APPS) as key:
            winreg.SetValueEx(key, REGISTERED_NAME, 0, winreg.REG_SZ, _CAPABILITIES)
        logger.info("Registered as magnet handler: %s", _launch_command())
        return True
    except Exception:
        logger.exception("Failed to register as magnet handler")
        return False


def unregister():
    """Removes only the keys register() created, restoring whatever handler
    was in front before (the machine-wide one, typically another client)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        for path in (rf"{_CLASSES}\magnet\shell\open\command",
                      rf"{_CLASSES}\magnet\shell\open",
                      rf"{_CLASSES}\magnet\shell",
                      rf"{_CLASSES}\magnet\DefaultIcon",
                      rf"{_CLASSES}\magnet",
                      rf"{_CLASSES}\{_PROG_ID}\shell\open\command",
                      rf"{_CLASSES}\{_PROG_ID}\shell\open",
                      rf"{_CLASSES}\{_PROG_ID}\shell",
                      rf"{_CLASSES}\{_PROG_ID}\DefaultIcon",
                      rf"{_CLASSES}\{_PROG_ID}",
                      _CAPABILITIES + r"\URLAssociations",
                      _CAPABILITIES,
                      r"Software\AwesomeDownloader"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except (FileNotFoundError, OSError):
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTERED_APPS, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, REGISTERED_NAME)
        except (FileNotFoundError, OSError):
            pass
        logger.info("Unregistered as magnet handler")
        return True
    except Exception:
        logger.exception("Failed to unregister as magnet handler")
        return False


def open_default_apps():
    """Windows' Default apps page for this app, where a locked magnet:
    choice can be changed (Windows allows that only from its own UI)."""
    from urllib.parse import quote
    try:
        os.startfile("ms-settings:defaultapps?registeredAppUser=" + quote(REGISTERED_NAME))
        return True
    except OSError:
        try:
            os.startfile("ms-settings:defaultapps")
            return True
        except OSError:
            logger.exception("Couldn't open Default apps")
            return False


def set_enabled(enabled):
    return register() if enabled else unregister()
