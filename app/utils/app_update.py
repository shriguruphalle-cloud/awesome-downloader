"""Asks GitHub whether a newer release of the app exists.

The Updates panel has always checked yt-dlp; nothing checked the app itself,
so anyone who installed once stayed on that version until they happened to
visit the releases page. This makes one request to GitHub's public releases
API at startup (Settings > Updates can turn it off) and, when there is a
newer version, the top bar says so and links to the release.

It never downloads or runs anything. The installer is unsigned, and a
program that fetches and launches an unsigned executable in the background
is exactly what the user should not have to trust; the release page is one
click away and shows the checksum to compare against.

Framework-agnostic: a blocking call, run by the UI on a background thread.
"""
import json
import urllib.request

from .. import config
from ..core.update_checker import _version_tuple, is_outdated
from ..logging_setup import get_logger

logger = get_logger("app_update")

API_URL = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
_TIMEOUT_S = 6


def _normalize(tag):
    """"v2.5.0" -> "2.5.0". Tags are written with and without the v."""
    tag = (tag or "").strip()
    return tag[1:] if tag[:1] in ("v", "V") else tag


def parse_release(payload):
    """GitHub's release JSON -> {"version", "page", "installer", "notes"},
    or None if it doesn't describe a usable release."""
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    version = _normalize(payload.get("tag_name") or payload.get("name"))
    if not _version_tuple(version):
        return None
    installer = None
    for asset in payload.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe") and "setup" in name:
            installer = asset.get("browser_download_url")
            break
    return {
        "version": version,
        "page": payload.get("html_url") or config.RELEASES_PAGE,
        "installer": installer,
        "notes": (payload.get("body") or "").strip(),
    }


def latest_release(timeout=_TIMEOUT_S):
    """The latest published release, or None on any failure -- offline, rate
    limited, GitHub down. A failed check is silent; it is never worth an
    error message about something the user didn't ask for."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"AwesomeDownloader/{config.APP_VERSION}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_release(json.load(resp))
    except Exception as e:  # noqa: BLE001 -- see docstring
        logger.info("App update check skipped: %s", e)
        return None


def newer_release(current=config.APP_VERSION, timeout=_TIMEOUT_S):
    """The latest release if it is newer than `current`, else None."""
    release = latest_release(timeout)
    if release and is_outdated(current, release["version"]):
        return release
    return None
