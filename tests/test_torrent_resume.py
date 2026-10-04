"""A finished torrent comes back finished: fast-resume data, not a re-check.

Before this, every launch re-added torrents from their magnet link or
.torrent file, libtorrent re-hashed everything already downloaded, and a
finished torrent passing 100% again was recorded in History again -- dated
that day ("finished today, took 4 s" on a film from days before).

  * the manager writes resume data and a new session adds the torrent back
    from it already complete, without a checking pass;
  * a torrent History already knows isn't recorded a second time, and its
    finish time is the first record's;
  * the duplicate entries earlier builds left are collapsed to the oldest.
"""
import os
import time

import _support
from _support import check

try:
    import libtorrent as lt
except ImportError:
    print("libtorrent not installed -- skipped")
    raise SystemExit(0)

from app import config
from app.core.torrent_manager import TorrentManager
from app.utils import download_history

# ---- a tiny torrent of our own, complete on disk -----------------------------
data_dir = os.path.join(config.APPDATA_DIR, "seed")
os.makedirs(data_dir, exist_ok=True)
payload = os.path.join(data_dir, "film.bin")
with open(payload, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024))
fs = lt.file_storage()
lt.add_files(fs, payload)
ct = lt.create_torrent(fs, 256 * 1024)
lt.set_piece_hashes(ct, data_dir)
torrent_path = os.path.join(config.APPDATA_DIR, "film.torrent")
with open(torrent_path, "wb") as f:
    f.write(lt.bencode(ct.generate()))


def wait(pred, seconds, manager):
    end = time.time() + seconds
    while time.time() < end:
        manager.process_alerts()
        if pred():
            return True
        time.sleep(0.05)
    return pred()


# ---- first run: added from the .torrent, checked once -------------------------
m1 = TorrentManager()
h1 = m1.add_torrent_file(torrent_path, data_dir)
check(wait(lambda: h1.status().progress >= 1.0, 20, m1), "the seed never finished checking")
key = TorrentManager.key(h1)
check(key and len(key) == 40, "no info-hash key: %r" % key)
m1.save_all_resume([h1])
check(os.path.exists(TorrentManager.resume_path(key)), "no resume data written")
print("resume data written for %s" % key[:12])
del h1, m1

# ---- next launch: back from the resume data, complete, no checking pass -----------
m2 = TorrentManager()
h2 = m2.add_from_resume(key, data_dir)
check(h2 is not None and h2.is_valid(), "couldn't add the torrent back from its resume data")
states = set()
end = time.time() + 3
while time.time() < end:
    m2.process_alerts()
    st = h2.status()
    states.add(str(st.state))
    if st.progress >= 1.0 and "checking" not in str(st.state):
        break
    time.sleep(0.05)
check(h2.status().progress >= 1.0, "restored torrent isn't complete: %.2f" % h2.status().progress)
check(not any("checking_files" in s for s in states), "it re-checked the files: %s" % states)
check(h2.status().has_metadata, "the metadata didn't come back with it")
print("restored complete, states seen: %s" % sorted(states))

# ...and removing it forgets the resume data.
m2.remove(h2)
check(not os.path.exists(TorrentManager.resume_path(key)), "resume data left behind after removal")

# ---- History: one entry per torrent, the first one's date -------------------------
download_history.clear_all()
download_history.add_entry("torrent", "Film", payload, data_dir, 3 << 20)
first = download_history.load()[0]["completed_at"]
time.sleep(0.02)
download_history.add_entry("torrent", "Film", payload, data_dir, 3 << 20)   # the old re-check bug
download_history.add_entry("video", "Clip", payload, data_dir, 1)
check(download_history.first_completion("torrent", "Film") == first, "first completion isn't the oldest")
dropped = download_history.collapse_duplicates("torrent")
kinds = [(e["kind"], e["title"]) for e in download_history.load()]
check(dropped == 1 and kinds.count(("torrent", "Film")) == 1 and ("video", "Clip") in kinds,
      "duplicates not collapsed: %r" % kinds)
check(download_history.load()[[k for k in kinds].index(("torrent", "Film"))]["completed_at"] == first,
      "the oldest record wasn't the one kept")
print("History: duplicates collapsed to the first record")

print("\nTORRENT RESUME OK")
