"""A browser whose cookies can't be read must not break fetching.

Chrome and Edge now lock their cookie stores (app-bound encryption), and
yt-dlp reads the store while it starts -- before it looks at the link -- so a
sign-in borrowed from Chrome made every fetch fail with "Failed to decrypt
with DPAPI", public videos included (reported with a PornHub link). The
downloader now drops the unreadable browser's cookies, retries once, remembers
the browser for the session, and tells the UI once.

No network and no real browser: yt_dlp.YoutubeDL is replaced by a stand-in
that fails exactly the way the real one does when handed Chrome cookies."""
import _support  # noqa: F401  (isolates app state)
from _support import check

import yt_dlp

from app.core import downloader, errors

DPAPI = "ERROR: Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info"
built = []


class FakeYDL:
    def __init__(self, params):
        built.append(dict(params))
        if params.get("cookiesfrombrowser"):
            raise yt_dlp.utils.DownloadError(DPAPI)
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return {"title": "Public video", "formats": [
            {"format_id": "hls-720", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
        ]}

    def build_format_selector(self, spec):
        return lambda ctx: iter(ctx["formats"])


downloader.yt_dlp.YoutubeDL = FakeYDL
heard = []
downloader.cookie_fallback_listeners.append(lambda browser, msg: heard.append((browser, msg)))

URL = "https://www.pornhub.org/view_video.php?viewkey=example"

# ---- the fetch succeeds instead of failing on the browser's cookies ------------
info, sizes = downloader.fetch_info_with_sizes(URL, cookies_from_browser="chrome")
print("fetched:", info["title"], sorted(sizes))
check(info["title"] == "Public video" and 720 in sizes, "fetch failed")
check(built[0].get("cookiesfrombrowser") == ("chrome",), "Chrome cookies weren't tried first")
check("cookiesfrombrowser" not in built[-1], "the retry still used Chrome's cookies")
check(heard and heard[0][0] == "chrome" and "DPAPI" in heard[0][1], "the UI wasn't told: %s" % heard)

# ---- remembered: the next request doesn't hit the same wall ----------------------
built.clear()
downloader.fetch_info_with_sizes(URL, cookies_from_browser="chrome")
check(len(built) == 1 and "cookiesfrombrowser" not in built[0], "an unreadable browser was retried")
check(len(heard) == 1, "the user was told more than once")

# ---- downloads take the same route ---------------------------------------------------
built.clear()
opts = downloader.base_ydl_opts("edge", URL)
opts["cookiesfrombrowser"] = ("edge",)      # as if Edge hadn't failed yet
info2, client = downloader.run_with_client_fallback(opts, URL)
check(info2["title"] == "Public video" and client is None, "download path didn't fall back")
check(heard[-1][0] == "edge", heard)

# ---- a genuine link error is not mistaken for a cookie problem ------------------
class Broken(FakeYDL):
    def extract_info(self, url, download=False):
        raise yt_dlp.utils.DownloadError("ERROR: [PornHub] example: Video unavailable")


downloader.yt_dlp.YoutubeDL = Broken
try:
    downloader.fetch_info_with_sizes(URL)
    check(False, "a real error was swallowed")
except yt_dlp.utils.DownloadError as e:
    check("unavailable" in str(e).lower(), e)

# ---- and if it does reach the user, it reads as advice, not a crash ------------
headline, advice = errors.friendly(DPAPI)
print("friendly:", headline, "/", advice)
check("sign-in" in headline.lower() and "Browser tab" in advice, headline)

# ---- the Sign-in setting moves off a browser that can never be read -------------
from ui_qt.dialogs import signin_dialog  # noqa: E402
st = {"cookies_from_browser": "chrome"}
line = signin_dialog.handle_cookie_fallback(st, "chrome", DPAPI)
print("notice:", line)
check(st["cookies_from_browser"] is None, "setting not moved to the app's Browser tab")
# A locked Chrome database used to count as temporary ("close Chrome and try
# again"), but closing Chrome only moves the failure on to DPAPI.
st = {"cookies_from_browser": "chrome"}
signin_dialog.handle_cookie_fallback(st, "chrome", "Could not copy Chrome cookie database")
check(st["cookies_from_browser"] is None, "a locked Chrome database left Sign-in on Chrome")
# Firefox can be read while it runs; a one-off failure there only explains.
st = {"cookies_from_browser": "firefox"}
line = signin_dialog.handle_cookie_fallback(st, "firefox", "failed to load cookies")
check(st["cookies_from_browser"] == "firefox" and "try again" in line, line)
# Not installed is as permanent as locked.
st = {"cookies_from_browser": "firefox"}
line = signin_dialog.handle_cookie_fallback(st, "firefox", 'could not find firefox cookies database in "x"')
check(st["cookies_from_browser"] is None and "isn't installed" in line, line)

print("\nCOOKIE FALLBACK OK")
