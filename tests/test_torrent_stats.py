"""A torrent's "took" and "finished" describe the download, not the launch.

Reported twice: a film downloaded days earlier showed "Finished: today, took
4 s" -- the app re-adds every torrent at launch, libtorrent fetches a
magnet's metadata again and re-checks the files on disk, and the torrent
turns complete seconds later. That must not count as finishing. The stats
code is driven here with libtorrent-shaped status objects, no network."""
import time
import types

import _support  # noqa: F401
from _support import check, qapp

app = qapp()
from ui_qt.torrent_tab import TorrentTab  # noqa: E402
from ui_qt.widgets.stats_strip import StatsStrip  # noqa: E402

owner = types.SimpleNamespace(_estimate_eta=lambda status: "--",
                              _files_finish_time=lambda handle, save_path: None)
GB = 1024 ** 3


def status(**kw):
    base = dict(paused=False, has_metadata=True, total_wanted=int(1.5 * GB), total_wanted_done=0,
                total_payload_download=0, total_upload=0, download_rate=0, upload_rate=0,
                num_peers=3, num_seeds=1)
    base.update(kw)
    return types.SimpleNamespace(**base)


def row(**kw):
    info = {"stats": StatsStrip(), "active_s": 0.0, "took_s": None, "finished_at": None,
            "uploaded_base": 0, "downloaded_base": 0, "last_poll": time.monotonic() - 1}
    info.update(kw)
    return info


def poll(info, st, complete, checking=False):
    info["last_poll"] = time.monotonic() - 1.0          # one second per poll
    TorrentTab._update_stats(owner, info, st, 1.0 if complete else 0.0, complete, checking)


# ---- already on disk: metadata again, a re-check, complete -- never "finished now" ----
info = row()
poll(info, status(has_metadata=False, total_wanted=0), complete=False)          # fetching metadata
poll(info, status(), complete=False, checking=True)                              # checking files
poll(info, status(total_wanted_done=int(1.5 * GB), total_payload_download=40_000), complete=True)
poll(info, status(total_wanted_done=int(1.5 * GB), total_payload_download=40_000), complete=True)
print("re-checked torrent: took=%s finished=%s" % (info["took_s"], info["finished_at"]))
check(info["took_s"] is None and info["finished_at"] is None,
      "a torrent already complete on disk was stamped as just finished")

# ---- really downloaded here: timed, and stamped once ----------------------------
info = row()
for i in range(5):
    poll(info, status(total_wanted_done=int(i * 0.3 * GB), total_payload_download=int(i * 0.3 * GB),
                      download_rate=300 * 1024 ** 2), complete=False)
poll(info, status(total_wanted_done=int(1.5 * GB), total_payload_download=int(1.5 * GB)), complete=True)
print("downloaded torrent: took=%s" % info["took_s"])
check(info["took_s"] and 4 <= info["took_s"] <= 6 and info["finished_at"], "a real download wasn't timed")
first = info["finished_at"]
poll(info, status(total_wanted_done=int(1.5 * GB), total_payload_download=int(1.5 * GB)), complete=True)
check(info["finished_at"] == first, "the finish time moved on a later poll")

# ---- downloaded across two launches: the earlier launch's bytes count too -------
info = row(downloaded_base=int(1.0 * GB), active_s=600.0)
poll(info, status(total_wanted_done=int(1.4 * GB), total_payload_download=int(0.4 * GB),
                  download_rate=100 * 1024 ** 2), complete=False)
poll(info, status(total_wanted_done=int(1.5 * GB), total_payload_download=int(0.5 * GB)), complete=True)
check(info["took_s"] and info["took_s"] >= 600, "a download split across launches lost its earlier time")

print("\nTORRENT STATS OK")
