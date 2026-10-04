"""The built-in browser's cookies reach yt-dlp scoped to the site being
fetched, in a file of their own that is deleted afterwards.

Builds a small Chromium-format Cookies database in the isolated profile
folder, so nothing real is read.
"""
import glob
import os
import sqlite3
import tempfile

import _support
from _support import check

from app.core import downloader
from app.utils import browser_cookies

os.makedirs(os.path.dirname(browser_cookies.COOKIES_DB), exist_ok=True)
con = sqlite3.connect(browser_cookies.COOKIES_DB)
con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, path TEXT, "
            "expires_utc INTEGER, is_secure INTEGER)")
rows = [
    (".youtube.com", "SID", "yt1", "/", 13447353773507301, 1),
    ("www.youtube.com", "PREF", "yt2", "/", 13447353773507301, 1),
    (".google.com", "NID", "g1", "/", 13447353773507301, 1),
    (".instagram.com", "sessionid", "ig1", "/", 13447353773507301, 1),
    (".bank.example.com", "session", "SECRET", "/", 13447353773507301, 1),
    (".news.bbc.co.uk", "ckns", "bbc", "/", 13447353773507301, 0),
    (".empty.com", "blank", "", "/", 0, 0),
]
con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?)", rows)
con.commit()
con.close()

# A stale copy of every session, as the first version left behind.
with open(browser_cookies.LEGACY_EXPORT_PATH, "w") as f:
    f.write("# old\n")

# ---- registrable-domain grouping -------------------------------------------
check(browser_cookies.site_of("www.m.youtube.com") == "youtube.com", "site_of youtube")
check(browser_cookies.site_of(".news.bbc.co.uk") == "bbc.co.uk", "site_of bbc.co.uk")

# ---- scoping --------------------------------------------------------------
yt = browser_cookies.scoped_rows("https://www.youtube.com/watch?v=x")
yt_names = sorted(r[1] for r in yt)
print("YouTube link carries:", yt_names)
check(yt_names == ["NID", "PREF", "SID"], yt_names)       # youtube + google, nothing else

ig = sorted(r[1] for r in browser_cookies.scoped_rows("https://www.instagram.com/p/abc/"))
print("Instagram link carries:", ig)
check(ig == ["sessionid"], ig)

nothing = browser_cookies.scoped_rows("https://unknown-site.org/video")
check(nothing == [], "an unrelated site got cookies: %r" % nothing)
check(all(r[1] != "session" for r in yt + nothing), "the bank cookie leaked")

# ---- a real options dict: temp file, scoped, and released -------------------
before = set(glob.glob(os.path.join(tempfile.gettempdir(), "awd-cookies-*")))
opts = downloader.base_ydl_opts(url="https://youtu.be/x")
path = opts.get("cookiefile")
check(path and os.path.exists(path), "no cookie file for a site with cookies")
body = open(path, encoding="utf-8").read()
check("SID" in body and "SECRET" not in body and "ig1" not in body, body)
check(not os.path.exists(browser_cookies.LEGACY_EXPORT_PATH),
      "the old shared all-sites file was not removed")
downloader.release_opts(opts)
check(not os.path.exists(path), "release_opts left the cookie file behind")
after = set(glob.glob(os.path.join(tempfile.gettempdir(), "awd-cookies-*")))
check(after <= before, "temporary cookie files leaked: %r" % (after - before))

# No cookies for the site -> no file at all, anonymous request.
opts = downloader.base_ydl_opts(url="https://unknown-site.org/v")
check("cookiefile" not in opts, "a cookie file was written for a site with no cookies")

# An explicit browser choice wins and nothing is exported.
opts = downloader.base_ydl_opts("chrome", url="https://youtu.be/x")
check(opts.get("cookiesfrombrowser") == ("chrome",) and "cookiefile" not in opts, opts)

# discard() only ever deletes files it made.
bystander = os.path.join(_support.STATE_DIR, "keep-me.txt")
open(bystander, "w").close()
browser_cookies.discard(bystander)
check(os.path.exists(bystander), "discard() deleted a file it did not create")

print("\nCOOKIE SCOPE OK")
