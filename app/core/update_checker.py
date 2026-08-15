"""Checks installed vs. latest versions of the app's moving parts.

yt-dlp is the one that matters most here, and by a wide margin: sites change
their pages constantly, yt-dlp ships near-daily releases to keep up, and an
installed copy that's fallen behind doesn't error out cleanly -- downloads
just start failing with confusing extractor errors, or silently return the
wrong/lowest-quality format, with nothing in the UI to say "your yt-dlp is
stale, that's why." This module is what gives the app the ability to notice
and say so, and to fix it in one click, rather than a user assuming the app
itself is broken and reporting a UI bug for what's actually an outdated
dependency.

ffmpeg and libtorrent are checked too since they're the other two components
downloads actually depend on, but neither churns anywhere near as fast --
they're reported for completeness, not because they're expected to go stale
often.

Framework-agnostic on purpose (no Qt imports), same as the rest of app/core/:
UI code runs these (blocking, network-touching) calls on a background thread
and marshals results back, exactly like fetch_info_with_sizes() etc. already do.
"""
import json
import re
import subprocess
import sys
import urllib.request

from .. import config
from ..logging_setup import get_logger
from . import ffmpeg_utils

logger = get_logger("update_checker")

PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
_REQUEST_TIMEOUT = 10


def _version_tuple(v):
    """'2026.7.4' -> (2026, 7, 4); non-numeric trailing bits (rare, e.g. a
    '.post1' suffix) are dropped rather than raising, so a comparison always
    produces *some* answer instead of crashing the check for one odd
    version string."""
    parts = []
    for p in re.split(r"[.\-]", v):
        m = re.match(r"\d+", p)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def is_outdated(installed, latest):
    """True only if `latest` is a real, comparable, greater version --
    never true from a parse failure or a network hiccup, so a broken
    version string can't falsely tell a user they're behind."""
    if not installed or not latest:
        return False
    it, lt = _version_tuple(installed), _version_tuple(latest)
    return bool(it) and bool(lt) and lt > it


def _latest_pypi_version(package):
    """Raises on any failure (network, bad JSON, ...) -- the caller decides
    how to present that, matching how fetch_info_with_sizes() etc. raise
    rather than returning a sentinel."""
    url = PYPI_JSON_URL.format(package=package)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["info"]["version"]


# ------------------------------------------------------------- yt-dlp -----
def installed_yt_dlp_version():
    import yt_dlp
    return yt_dlp.version.__version__


def latest_yt_dlp_version():
    return _latest_pypi_version("yt-dlp")


def upgrade_yt_dlp():
    """Blocking. Raises RuntimeError with a clear message on failure (no
    pip available, network down mid-install, frozen .exe with no
    interpreter to pip into, ...) -- the caller shows that message rather
    than a raw traceback, same pattern as TorrentTab's libtorrent installer.
    """
    if config.IS_FROZEN:
        raise RuntimeError(
            "Auto-update isn't available in the packaged .exe.\n\n"
            "Run this in a terminal (Python 3.11-3.13), then restart the app:\n"
            "pip install --upgrade yt-dlp"
        )
    kwargs = {"capture_output": True, "text": True, "timeout": 300}
    if config.CREATE_NO_WINDOW:
        kwargs["creationflags"] = config.CREATE_NO_WINDOW
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], **kwargs
    )
    if result.returncode != 0:
        logger.error("yt-dlp upgrade failed: %s", (result.stderr or "")[-800:])
        raise RuntimeError((result.stderr or "Unknown error")[-500:])


# ------------------------------------------------------------- ffmpeg -----
def ffmpeg_status():
    """(installed_version_or_None, available_bool) -- ffmpeg has no simple
    single-package PyPI/version feed to check against (it's a bundled
    binary, not a Python dependency), so this reports presence/version only,
    not an outdated/current comparison."""
    return ffmpeg_utils.installed_ffmpeg_version(), ffmpeg_utils.ffmpeg_available()


# ---------------------------------------------------------- libtorrent ----
def libtorrent_status():
    """(installed_version_or_None, available_bool)."""
    try:
        import libtorrent as lt
        return getattr(lt, "version", None), True
    except ImportError:
        return None, False
