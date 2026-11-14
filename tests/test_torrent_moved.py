"""A finished torrent whose files are moved or deleted doesn't download them again by itself: it stops and says so,
while the app runs and at the next launch. "Download again" starts it over; "Find the files…" points it at their
new folder and it carries on sharing them. A real libtorrent session on a made-up file; no peers, no internet."""
import os
import shutil
import tempfile
import time
import types

import _support
from _support import build_window, check, settle

try:
    import libtorrent as lt
except ImportError:
    print("libtorrent not installed -- skipped")
    raise SystemExit(0)

from app.core.torrent_manager import TorrentManager
from ui_qt import torrent_tab as tt

base = tempfile.mkdtemp(prefix="awd-moved-")
data_dir = os.path.join(base, "Torrents")
elsewhere = os.path.join(base, "Moved here")
os.makedirs(data_dir)
os.makedirs(elsewhere)
payload = os.path.join(data_dir, "film.bin")
with open(payload, "wb") as f:
    f.write(os.urandom(2 * 1024 * 1024))
fs = lt.file_storage()
lt.add_files(fs, payload)
ct = lt.create_torrent(fs, 256 * 1024)
lt.set_piece_hashes(ct, data_dir)
torrent_path = os.path.join(base, "film.torrent")
with open(torrent_path, "wb") as f:
    f.write(lt.bencode(ct.generate()))

win, tabs = build_window(tabs=("torrent", "download"))
tab = tabs["torrent"]
check(hasattr(tab, "manager"), "no torrent engine in this build")
handle = tab.manager.add_torrent_file(torrent_path, data_dir)
row = tab._add_row("film.bin", "file", torrent_path, data_dir, handle, None)
info = tab.rows[row]


def poll_until(cond, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        tab._poll()
        if cond():
            return True
        settle(100)
    return cond()


def held():
    flags = handle.flags()
    return bool(flags & lt.torrent_flags.paused) and not flags & lt.torrent_flags.auto_managed


check(poll_until(lambda: info["done"]), "the seed never finished")
check(not info["moved"], "held while its files are there")
print("finished: seeding")


def move_away(to):
    end = time.time() + 15
    while True:        # libtorrent may still hold the file open for a moment
        try:
            shutil.move(os.path.join(info["save_path"], "film.bin"), os.path.join(to, "film.bin"))
            return
        except PermissionError:
            if time.time() > end:
                raise
            settle(300)


move_away(elsewhere)
check(poll_until(lambda: info["moved"], 15), "moving its file didn't stop it")
check(held(), "it isn't held: it would download again")
check(info["status_label"].text() == "Moved or deleted", info["status_label"].text())
check(info["pause_btn"].text() == "Download again" and not info["play_btn"].isVisible(), "its buttons")
for _ in range(10):
    tab._poll()
    settle(100)
check(held() and not os.path.exists(payload), "it started downloading the file again")
print("files moved: held, 'Moved or deleted', nothing fetched again")

# next launch: back held, not downloading
tab.manager.save_all_resume([handle])
key = TorrentManager.key(handle)
m2 = TorrentManager()
h2 = m2.add_from_resume(key, data_dir, hold_if_missing=True)
check(h2 is not None and bool(h2.flags() & lt.torrent_flags.paused), "at the next launch it started again")
m2.remove(h2)
del m2
# (a torrent whose files are there comes back running)
print("the next launch: still held")

# Find the files: it uses them where they are now
tt.QFileDialog = types.SimpleNamespace(getExistingDirectory=lambda *a, **k: elsewhere)
tab._find_files(row)
check(not info["moved"] and os.path.normcase(info["save_path"]) == os.path.normcase(os.path.normpath(elsewhere)),
      "it wasn't pointed at the new folder: %s" % info["save_path"])
check(poll_until(lambda: handle.status().progress >= 1.0 and "checking" not in str(handle.status().state), 20),
      "it isn't complete in its new folder")
check(not held() and not info["moved"], "still held after finding its files")
check(os.path.exists(os.path.join(elsewhere, "film.bin")) and not os.path.exists(payload), "files were moved about")
print("Find the files: carries on from the new folder")

# deleted this time; Download again asks, then starts it over
move_away(data_dir)        # (out of the folder it now uses)
check(poll_until(lambda: info["moved"], 15), "deleting its file didn't stop it")
asked = []
tt.QMessageBox = types.SimpleNamespace(question=lambda *a, **k: (asked.append(a[2]), "no")[1],
                                       StandardButton=types.SimpleNamespace(Yes="yes"))
tab._download_again(row)
check(asked and info["moved"] and held(), "it started without a yes")
tt.QMessageBox = types.SimpleNamespace(question=lambda *a, **k: "yes",
                                       StandardButton=types.SimpleNamespace(Yes="yes"))
tab._toggle_pause(row)          # the row's own button says "Download again"
check(not info["moved"] and not held(), "Download again didn't start it")
for _ in range(10):
    tab._poll()
    settle(100)
check(not info["moved"], "it was held again right after Download again")
print("Download again: asks first, then starts it over")

tab.manager.remove(handle)
win.close()
settle(200)
shutil.rmtree(base, ignore_errors=True)
print("\nTORRENT MOVED OK")
