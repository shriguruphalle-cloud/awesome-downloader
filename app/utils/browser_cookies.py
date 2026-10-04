"""Hands yt-dlp the cookies from *this app's own* Browser tab.

Sites that require a login -- Instagram posts, age-gated or members-only
videos, private galleries -- return an empty response to an anonymous
request, and yt-dlp's advice for that is to pass cookies. The usual route is
`--cookies-from-browser chrome`, which reaches into the user's real Chrome
profile and reads their whole cookie store. This app does not need to do
that: it ships a browser of its own, that browser keeps a persistent profile,
and anything the user logs into there is a session they created inside this
app and can clear from inside it.

Since 2.5 the Browser tab runs on WebView2, whose cookie store is encrypted
like any Chromium browser's -- and locked while it runs. Its cookies are
asked for through WebView2's own cookie API instead, which the Browser tab
registers here as a provider (set_provider). The provider answers from the
GUI thread even when asked from a download thread. The old path, reading
QtWebEngine's plaintext `Cookies` database, stays as the fallback for a
2.4 profile when no provider is registered.

The export is a Netscape cookies.txt, which is what yt-dlp's `cookiefile`
option wants.

Scoped and short-lived. The first version wrote *every* site's session --
168 cookies across 33 sites on the machine it was built on -- into one
shared file in AppData on every single fetch, and left it there. Two things
were wrong with that: a download from one site does not need the sessions of
thirty-two others sitting in a plaintext file, and concurrent downloads all
read, and yt-dlp writes back to, the same path at once. Each call now gets
its own temporary file holding only the cookies for the site being fetched,
and the caller deletes it when yt-dlp is done with it (see discard()).
"""
import os
import shutil
import sqlite3
import tempfile
import urllib.parse

from .. import config
from ..logging_setup import get_logger

logger = get_logger("browser_cookies")

COOKIES_DB = os.path.join(config.APPDATA_DIR, "browser_profile", "Cookies")
# The shared file the first version wrote. Nothing writes it any more; it is
# removed on the next call so an old install doesn't keep a stale copy of
# every session lying around.
LEGACY_EXPORT_PATH = os.path.join(config.APPDATA_DIR, "browser_cookies.txt")
_TEMP_PREFIX = "awd-cookies-"

# Chromium counts microseconds from 1601-01-01; Unix counts seconds from
# 1970-01-01. This is the gap, in seconds.
_EPOCH_GAP = 11644473600

# Sites whose media is served from, or whose sign-in lives on, a different
# domain than the page itself. Without these a YouTube download would carry
# only youtube.com cookies and miss the google.com session some sign-in
# checks look for.
_DOMAIN_FAMILIES = {
    "youtube.com": ("youtube.com", "google.com", "youtu.be"),
    "youtu.be": ("youtube.com", "google.com", "youtu.be"),
    "instagram.com": ("instagram.com", "facebook.com", "cdninstagram.com"),
    "facebook.com": ("facebook.com", "fb.watch", "fbcdn.net"),
    "fb.watch": ("facebook.com", "fb.watch", "fbcdn.net"),
    "twitter.com": ("twitter.com", "x.com", "twimg.com"),
    "x.com": ("twitter.com", "x.com", "twimg.com"),
    "reddit.com": ("reddit.com", "redd.it", "redditmedia.com"),
    "tiktok.com": ("tiktok.com", "tiktokcdn.com"),
    "vimeo.com": ("vimeo.com", "vimeocdn.com"),
}

# Two-label public suffixes common enough to matter for "which site is
# this". A full public-suffix list is a dependency this doesn't need: the
# worst case for an unknown one is sending the cookies of the one host.
_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "net.in", "org.in",
    "com.au", "net.au", "org.au", "co.jp", "ne.jp", "com.br", "com.mx",
    "co.kr", "com.tr", "com.cn", "co.nz", "co.za", "com.sg", "com.hk",
}


# Registered by the Browser tab: provider(sites) -> rows, where `sites` is a
# set of registrable domains (or None for everything) and each row is
# (host, name, value, path, expires_unix, secure). Returns None when it
# can't answer, and the legacy database is read instead.
_provider = None


def set_provider(fn):
    global _provider
    _provider = fn


def _to_unix(chromium_us):
    if not chromium_us:
        return 0
    return int(chromium_us / 1_000_000 - _EPOCH_GAP)


def site_of(host):
    """"www.m.youtube.com" -> "youtube.com"; "news.bbc.co.uk" -> "bbc.co.uk"."""
    host = (host or "").lower().strip(".").split(":")[0]
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _wanted_sites(url):
    """Every registrable domain whose cookies a request to `url` needs, or
    None meaning "no URL given, so no filter"."""
    if not url:
        return None
    host = urllib.parse.urlparse(url if "://" in url else "https://" + url).hostname
    if not host:
        return None
    site = site_of(host)
    return set(_DOMAIN_FAMILIES.get(site, (site,)))


def _read_rows():
    """Reads the cookie table from a copy.

    The live database belongs to a running QtWebEngine profile and is locked
    while the app has the Browser tab open, which is exactly when this is
    wanted -- so it is copied first and the copy is read.
    """
    if not os.path.exists(COOKIES_DB):
        return []
    tmp_dir = tempfile.mkdtemp(prefix="awd-cookiedb-")
    tmp = os.path.join(tmp_dir, "Cookies")
    try:
        shutil.copy2(COOKIES_DB, tmp)
        con = sqlite3.connect(tmp)
        try:
            cur = con.execute(
                "SELECT host_key, name, value, path, expires_utc, is_secure "
                "FROM cookies WHERE value != ''")
            return [(h, n, v, p, _to_unix(e), s) for h, n, v, p, e, s in cur.fetchall()]
        finally:
            con.close()
    except Exception:
        logger.exception("Could not read the browser cookie store")
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _netscape_lines(rows):
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
            str(int(expires or 0)), name, value,
        ]))
    return lines


def scoped_rows(url):
    """The cookie rows a request to `url` should carry."""
    wanted = _wanted_sites(url)
    rows = None
    if _provider is not None:
        try:
            rows = _provider(wanted)
        except Exception:
            logger.exception("Browser cookie provider failed")
            rows = None
    if rows is None:
        rows = _read_rows()
    if wanted is None:
        return rows
    return [r for r in rows if site_of(r[0]) in wanted]


def scoped_cookie_file(url=None):
    """Writes the cookies for `url`'s site to a fresh temporary file and
    returns its path, or None if there are none to write.

    Must never raise and must never be the reason a download fails: with no
    browser profile, an unreadable one, or no cookies for this site, it
    returns None and yt-dlp runs anonymously exactly as it would have.
    """
    _remove_legacy_export()
    try:
        rows = scoped_rows(url)
        if not rows:
            return None
        fd, path = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(_netscape_lines(rows)) + "\n")
        return path
    except Exception:
        logger.exception("Cookie export failed")
        return None


def discard(path):
    """Deletes a file scoped_cookie_file() made. Ignores anything else, so a
    caller can hand it whatever ended up in yt-dlp's options."""
    if not path:
        return
    try:
        if os.path.basename(path).startswith(_TEMP_PREFIX) and os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.debug("Could not remove temporary cookie file %s", path, exc_info=True)


def _remove_legacy_export():
    try:
        if os.path.exists(LEGACY_EXPORT_PATH):
            os.remove(LEGACY_EXPORT_PATH)
    except OSError:
        pass


# ---- kept for callers written against the first version -----------------
