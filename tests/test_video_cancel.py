"""Cancelling a video download that's stuck -- the site's server has stopped
sending, so yt-dlp sits waiting and never calls back -- takes it off the
screen at once, frees its slot, and leaves none of its files behind
(reported: three cards sat on "Cancelling..." with their .part files kept).
"""
import os
import tempfile
import threading
import time

import _support
from _support import build_window, check, settle

from app.core import downloader

win, tabs = build_window(tabs=("video", "download"))
vt, dl = tabs["video"], tabs["download_obj"]
folder = tempfile.mkdtemp(prefix="ad-cancel-")
release = threading.Event()
ended = threading.Event()


def stuck_download(url, save_dir, height, hook, time_range=None, cookies_from_browser=None, name=None):
    """Writes what yt-dlp would for a streamed video, reports once, then
    hears nothing from the server until released."""
    part = os.path.join(save_dir, "Clip.f301.mp4.part")
    for f in (part, part + ".ytdl", part + "-Frag1", part + "-Frag2"):
        with open(f, "wb") as fh:
            fh.write(b"x" * 64)
    hook({"status": "downloading", "tmpfilename": part, "downloaded_bytes": 64, "total_bytes": 10_000})
    release.wait(30)
    try:
        hook({"status": "downloading", "tmpfilename": part, "downloaded_bytes": 128, "total_bytes": 10_000})
    finally:
        ended.set()
    return {}, None, None


downloader.download_video = stuck_download
# someone else's download in the same folder, with a similar name: never touched
keep = os.path.join(folder, "Clip extended.mp4")
with open(keep, "wb") as fh:
    fh.write(b"y")

job_id = vt._start_job("Clip", "test", folder)
vt._launch(job_id, vt._download_thread, (job_id, "https://example.test/v", folder, "video", 720, "mkv"))
for _ in range(40):
    if vt._jobs[job_id].get("path"):
        break
    settle(50)
check(vt._jobs[job_id].get("path"), "the stuck download never reported its file")
check(job_id in vt._running, "the download didn't take a slot")

dl._cards[job_id]._handle_cancel()
settle(1400)
check(job_id not in dl._cards, "the cancelled card is still on screen")
check(job_id not in vt._running, "the cancelled download still holds its slot")
left = sorted(os.listdir(folder))
check(left == ["Clip extended.mp4"], "files left after cancelling: %s" % left)
print("cancelled at once: card gone, slot free, its files deleted, the other file kept")

# the thread wakes at last: it stops, and stays gone from the screen
release.set()
check(ended.wait(10), "the stuck thread never ended")
settle(500)
check(job_id not in dl._cards and sorted(os.listdir(folder)) == ["Clip extended.mp4"],
      "the thread ending brought something back: %s" % sorted(os.listdir(folder)))

# silence on a running download is shown, not left as "Starting..."
release.clear()
ended.clear()
job2 = vt._start_job("Clip", "test", folder)
vt._launch(job2, vt._download_thread, (job2, "https://example.test/v2", folder, "video", 720, "mkv"))
settle(400)
vt._jobs[job2]["last_progress"] = time.monotonic() - 45
vt._watch_stalls()
detail = dl._cards[job2].detail_label.text()
check("stopped sending" in detail, "a silent download doesn't say so: %r" % detail)
vt._cancel_job(job2)
release.set()
ended.wait(10)
settle(300)
print("VIDEO CANCEL OK")
