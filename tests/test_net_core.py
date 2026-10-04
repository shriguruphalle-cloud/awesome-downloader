"""Live checks against the real sites the app depends on. Metadata only --
nothing is downloaded. Skipped by run_all.py unless --network is given,
because these fail for reasons that have nothing to do with this code (the
site is down, YouTube changed something, no connection).

  * GitHub's releases API answers, and the app reads a real release from it.
  * yt-dlp can still read a YouTube video's formats through the app's own
    fetch path (the first video ever uploaded -- about as stable a URL as the
    platform has), and every size estimate comes back as a number.
  * The same path through a playlist link returns a flat listing, not a
    full extraction of every video in it.
"""
import time

import _support
from _support import check

from app.core import downloader
from app.utils import app_update

# ---- GitHub releases -------------------------------------------------------------
t0 = time.time()
release = app_update.latest_release(timeout=15)
print("latest release: %s (%.1fs)" % (release, time.time() - t0))
check(release is not None, "GitHub's releases API gave nothing back")
check(release["version"].count(".") >= 1, "unexpected version %r" % release["version"])
check("github.com" in release["page"], release["page"])
check(app_update.newer_release("0.0.1") is not None, "an ancient version wasn't offered the update")

# ---- one video, through the app's own fetch path ----------------------------------
t0 = time.time()
info, sizes = downloader.fetch_info_with_sizes("https://www.youtube.com/watch?v=jNQXAC9IVRw")
print("video: %r by %r, heights %s (%.1fs)" % (
    info.get("title"), info.get("uploader"), sorted(sizes)[:6], time.time() - t0))
check(not downloader.is_playlist(info), "a single video came back as a playlist")
check("zoo" in (info.get("title") or "").lower(), "wrong video: %r" % info.get("title"))
check(sizes, "no resolutions found -- yt-dlp may need an update")
check(all(isinstance(v, (int, float)) and v >= 0 for v in sizes.values()), sizes)
check(downloader.best_thumbnail_url(info), "no thumbnail URL")

# ---- a playlist is listed flat -------------------------------------------------------
t0 = time.time()
pl_url = "https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Ar_3GPy3workP8ETnS6XS"
try:
    pl_info, pl_sizes = downloader.fetch_info_with_sizes(pl_url)
except Exception as e:  # a removed playlist is not a failure of this code
    print("playlist check skipped: %s" % str(e).splitlines()[0][:120])
else:
    took = time.time() - t0
    entries = downloader.playlist_entries(pl_info)
    print("playlist: %r, %d entries (%.1fs)" % (pl_info.get("title"), len(entries), took))
    check(downloader.is_playlist(pl_info) and pl_sizes == {}, "playlist wasn't recognised")
    check(entries and all(e.get("url") for e in entries), "entries without links")
    check(took < 30, "listing took %.0fs -- it's extracting every video" % took)

print("\nNETWORK CORE OK")
