"""Links that need an account get a way to sign in, and whichever session
source is chosen actually reaches yt-dlp -- for the fetch *and* the
download."""
import _support
from _support import check, no_modal_dialogs, qapp, settings

qapp()
from app.core import downloader
from ui_qt.dialogs import signin_dialog
from ui_qt.download_tab import DownloadTab
from ui_qt.images_tab import ImagesTab
from ui_qt.video_tab import VideoTab

REAL = ("ERROR: [Instagram] DcoUemhio5E: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in. "
        "If it is not, then use --cookies-from-browser or --cookies for the authentication.")
check(signin_dialog.needs_sign_in(REAL), "the Instagram login wall isn't recognised")
for other in ("HTTP Error 404: Not Found", "Unable to download webpage: timed out"):
    check(not signin_dialog.needs_sign_in(other), "false positive on %r" % other)

# ---- default: the app's own browser, never an installed one ----------------
opts = downloader.base_ydl_opts()
check("cookiesfrombrowser" not in opts, "an installed browser is read by default")
opts = downloader.base_ydl_opts("chrome")
check(opts.get("cookiesfrombrowser") == ("chrome",) and "cookiefile" not in opts, opts)

# ---- both tabs read the setting live ---------------------------------------
st = settings(cookies_from_browser=None)
img = ImagesTab(settings=st)
vt = VideoTab(settings=st, download_tab=DownloadTab(settings=st))
st["cookies_from_browser"] = "edge"
check(img._cookies_from_browser() == "edge" and vt._cookies_from_browser() == "edge",
      "a tab ignores Settings > Sign-in")

# ---- a login wall offers the sign-in dialog, not yt-dlp's text --------------
shown = no_modal_dialogs()
offered = []
signin_dialog.show_sign_in_help = lambda *a, **k: (offered.append(k.get("current")), None)[1]
img._on_fetch_error(REAL)
check(len(offered) == 1 and not shown, "Images: sign-in not offered / raw error shown")
vt._last_fetch_url = "https://www.instagram.com/p/x/"
vt._on_fetch_error(REAL)
check(len(offered) == 2 and not shown, "Video: sign-in not offered / raw error shown")
print("sign-in dialog offered in both tabs, with the current choice:", offered)

# ...and an ordinary failure gets a plain-language message instead.
vt._on_fetch_error("HTTP Error 404: Not Found")
check(len(offered) == 2 and shown and "404" in shown[-1][1], shown)
check("--" not in shown[-1][1], "terminal advice in a user message")
print("ordinary failure message:", shown[-1][1].splitlines()[0])

# ---- the fetch passes the setting down ---------------------------------------
seen = {}


def spy(url, cookies_from_browser=None):
    seen["gallery"] = cookies_from_browser
    raise RuntimeError("stop")


downloader.fetch_image_gallery = spy
img._fetch_thread("https://example.test/p/abc")
check(seen.get("gallery") == "edge", "the Images fetch ignored the setting")

print("\nSIGN-IN OK")
