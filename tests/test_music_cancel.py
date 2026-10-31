"""Music downloads can be stopped: each song's card in the Download tab has
Cancel, a song's own download button cancels it while it runs or waits,
"Cancel all" stops the lot (playlist downloads included), and a song
stopped part-way leaves nothing half-saved. The network is stubbed: songs
are made-up, and the one real download reads a local file."""
import os
import tempfile
import threading
import time

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import music_sources as ms  # noqa: E402

win, tabs = build_window(tabs=("music", "download"))
mt, dt = tabs["music"], tabs["download"]
mt.download_tab = dt

started = []


def slow_download(track, folder, progress=None, cancelled=None):
    """Takes ~2 s in small steps, stopping when asked -- like the real one."""
    started.append(track["id"])
    for i in range(40):
        if cancelled and cancelled():
            raise ms.Cancelled("Cancelled")
        if progress:
            progress(i + 1, 40)
        time.sleep(0.05)
    return os.path.join(folder, track["title"] + ".mp3")


ms.download = slow_download
songs = [ms._track(id="yt:song%d" % i, title="Made-up Song %d" % i, artist="Test Singer", source="youtube",
                   page_url="https://www.youtube.com/watch?v=song%d" % i) for i in range(4)]

# ---- a playlist's worth queued: one runs, the rest wait, all in the Download tab ----
for s in songs:
    mt.keep(s)
settle(300)
check(len(dt.active_jobs()) == 4 and (not dt.header.isHidden()), "the songs didn't show up in the Download tab")
check(dt.count_label.text() == "4 downloads", "the header count is wrong: %r" % dt.count_label.text())
check(mt._keeping == "yt:song0" and [t["id"] for t in mt._keep_queue] == ["yt:song1", "yt:song2", "yt:song3"],
      "the songs aren't downloading one at a time")
print("a playlist's songs download one at a time, each with its card in the Download tab")

# ---- a waiting song cancelled from its own download button ----
mt.cancel_keep("yt:song2")
settle(200)
check("yt:song2" not in [t["id"] for t in mt._keep_queue] and "yt:song2" not in mt._kept,
      "a waiting song wasn't taken out of the line")
check(len(dt.active_jobs()) == 3, "its card still counts as running")
print("a waiting song is cancelled on its own")

# ---- Cancel all: the running one stops part-way, the rest never start ----
settle(300)
dt.cancel_all()
deadline = time.monotonic() + 3
while mt._keeping is not None and time.monotonic() < deadline:
    settle(50)
settle(300)
check(mt._keeping is None and not mt._keep_queue, "Cancel all didn't stop the music downloads")
check(not dt.active_jobs() and not (not dt.header.isHidden()), "the Download tab still shows downloads running")
check(started == ["yt:song0"], "a song started after Cancel all: %r" % started)
check(not any(v in ("busy", "waiting") for v in mt._kept.values()), "a row still shows a download")
print("Cancel all in the Download tab stops the music downloads, running and waiting")

# ---- the Music tab's own Cancel all ----
started.clear()
for s in songs[:2]:
    mt.keep(s)
settle(200)
mt.cancel_all_keeps()
deadline = time.monotonic() + 3
while mt._keeping is not None and time.monotonic() < deadline:
    settle(50)
check(mt._keeping is None and not mt._keep_queue and started == ["yt:song0"], "the Music tab's Cancel all didn't")
print("the Music tab's own Cancel all stops them too")

# ---- the real download, stopped part-way: no half file left ----
import importlib  # noqa: E402
real = importlib.reload(ms)
work = tempfile.mkdtemp()
src = os.path.join(work, "big.bin")
with open(src, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024))
out = os.path.join(work, "out")
track = real._track(id="ia:big", title="Big Free Song", artist="", source="archive", ext="mp3",
                    stream="file:///" + src.replace("\\", "/"))
calls = []
stop = threading.Event()


def progress(got, total):
    calls.append(got)
    stop.set()                                  # asked to stop after the first chunk


try:
    real.download(track, out, progress, cancelled=stop.is_set)
    check(False, "the download didn't stop")
except real.Cancelled:
    pass
check(calls and not os.listdir(out), "a stopped download left a file behind: %r" % os.listdir(out))
print("a song stopped part-way leaves nothing half-saved")

win.close()
settle(300)
print("\nMUSIC CANCEL OK")
