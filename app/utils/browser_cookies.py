"""Hands yt-dlp the cookies from *this app's own* Browser tab.

Sites that require a login -- Instagram posts, age-gated or members-only
videos, private galleries -- return an empty response to an anonymous
request, and yt-dlp's advice for that is to pass cookies. The usual route is
`--cookies-from-browser chrome`, which reaches into the user's real Chrome
profile and reads their whole cookie store. This app does not need to do
that: it ships a browser of its own, that browser keeps a persistent profile,
and anything the user logs into there is a session they created inside this
app and can clear from inside it.

QtWebEngine stores that profile as an ordinary Chromium `Cookies` SQLite
database, but without the `Local State` file Chrome uses to hold its DPAPI
encryption key -- so the values land in the plaintext `value` column rather
than `encrypted_value`, and can be read directly with no decryption and no
platform-specific key handling.

The export is a Netscape cookies.txt, which is what yt-dlp's `cookiefile`
option wants.
"""
import os
import shutil
import sqlite3
import tempfile

from .. import config
from ..logging_setup import get_logger

logger = get_logger("browser_cookies")

COOKIES_DB = os.path.join(config.APPDATA_DIR, "browser_profile", "Cookies")
EXPORT_PATH = os.path.join(config.APPDATA_DIR, "browser_cookies.txt")

# Chromium counts microseconds from 1601-01-01; Unix counts seconds from
# 1970-01-01. This is the gap, in seconds.
_EPOCH_GAP = 11644473600


def _to_unix(chromium_us):
    if not chromium_us:
        return 0
    return int(chromium_us / 1_000_000 - _EPOCH_GAP)


def _read_rows():
    """Reads the cookie table from a copy.

    The live database belongs to a running QtWebEngine profile and is locked
    while the app has the Browser tab open, which is exactly when this is
    wanted -- so it is copied first and the copy is read.
    """
    if not os.path.exists(COOKIES_DB):
        return []
    tmp_dir = tempfile.mkdtemp(prefix="awd-cookies-")
    tmp = os.path.join(tmp_dir, "Cookies")
    try:
        shutil.copy2(COOKIES_DB, tmp)
        con = sqlite3.connect(tmp)
        try:
            cur = con.execute(
                "SELECT host_key, name, value, path, expires_utc, is_secure "
                "FROM cookies WHERE value != ''")
            return cur.fetchall()
        finally:
            con.close()
    except Exception:
        logger.exception("Could not read the browser cookie store")
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def export(path=EXPORT_PATH):
    """Writes a Netscape cookies.txt and returns its path, or None if there
    was nothing to write."""
    rows = _read_rows()
    if not rows:
        return None
    lines = ["# Netscape HTTP Cookie File",
             "# Exported from the Awesome Downloader browser tab.", ""]
    for host, name, value, cookie_path, expires, secure in rows:
        if not host or not name:
            continue
        # A leading dot is Netscape's way of saying "and its subdomains",
        # and it is also how Chromium stores a domain cookie, so the flag
        # can be read straight off the host key.
        include_sub = "TRUE" if host.startswith(".") else "FALSE"
        lines.append("\t".join([
            host, include_sub, cookie_path or "/",
            "TRUE" if secure else "FALSE",
            str(_to_unix(expires)), name, value,
        ]))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        logger.exception("Could not write the cookie file for yt-dlp")
        return None
    return path


def cookie_file_if_available():
    """Refreshes the export and returns its path, or None.

    Called on the way into every yt-dlp options dict, so it must never raise
    and must never be the reason a download fails: with no browser profile,
    or an unreadable one, this returns None and yt-dlp runs anonymously
    exactly as it did before.
    """
    try:
        return export()
    except Exception:
        logger.exception("Cookie export failed")
        return None


def hosts_with_cookies():
    """Which sites the built-in browser is currently signed in to -- used to
    tell the user whether logging in would actually help."""
    return sorted({row[0].lstrip(".") for row in _read_rows()})


def clear():
    """Removes the exported file. The cookies themselves live in the browser
    profile and are cleared from the Browser tab."""
    try:
        if os.path.exists(EXPORT_PATH):
            os.remove(EXPORT_PATH)
    except Exception:
        logger.exception("Could not remove the exported cookie file")
