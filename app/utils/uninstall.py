"""The installed app's own way out: Settings > Your data > Uninstall.

The installer (installer.iss) puts its uninstaller, unins000.exe, beside
the app. Run from source or from a build folder there is none, and the
button doesn't show.

The uninstaller asks for administrator rights (the app lives in Program
Files), so it is started through the shell -- os.startfile, which can raise
the UAC prompt -- not CreateProcess, which refuses an elevated program
outright. The app then quits, so nothing it has open is left behind: the
uninstaller also waits on the "running" mutex (single_instance.py) until it
has gone.
"""
import glob
import os
import sys

from .. import config
from ..logging_setup import get_logger

logger = get_logger("uninstall")


def uninstaller_path(base_dir=None, frozen=None):
    """unins000.exe beside the installed app, or None."""
    frozen = config.IS_FROZEN if frozen is None else frozen
    if not frozen or sys.platform != "win32":
        return None
    base = base_dir or config.BASE_DIR
    found = sorted(glob.glob(os.path.join(base, "unins[0-9][0-9][0-9].exe")))
    return found[-1] if found else None


def launch_uninstaller(path=None, start=None):
    """Starts the uninstaller; True if it started. `start` is for tests."""
    path = path or uninstaller_path()
    if not path:
        return False
    try:
        (start or os.startfile)(path)
        logger.info("Started the uninstaller: %s", path)
        return True
    except OSError:
        logger.exception("Couldn't start the uninstaller")
        return False
