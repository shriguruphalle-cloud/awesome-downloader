"""Adding a torrent offered no way to choose where it goes: the add dialog
showed the Torrent tab's folder, read-only. It now has Change..., for that
torrent only, and the torrent is moved there (taking along anything it
already wrote, never replacing a file that's already in the folder).

Also: unticking "Start downloading when added" paused with handle.pause(),
which libtorrent's auto-manager undoes at once -- it must use the manager's
sticky pause. No libtorrent session is started: fake handles and a fake
manager stand in, and no real folder is touched."""
import os
import tempfile
import types

import _support
from _support import check, qapp

app = qapp()
from ui_qt import torrent_tab  # noqa: E402
from ui_qt.dialogs import add_torrent_dialog  # noqa: E402
from app.core import torrent_manager  # noqa: E402

base = tempfile.mkdtemp(prefix="awd-torrent-folder-")
default_dir = os.path.join(base, "Torrents")
chosen_dir = os.path.join(base, "Films", "New")


class Handle:
    def __init__(self):
        self.calls = []

    def is_valid(self):
        return False

    def status(self):
        return types.SimpleNamespace(has_metadata=False, name="")

    def move_storage(self, path, flags):
        self.calls.append(("move_storage", path, flags))

    def prioritize_files(self, p):
        self.calls.append(("prioritize", p))

    def pause(self):
        self.calls.append(("plain pause",))


# 1. The dialog's Change... sets this torrent's folder.
handle = Handle()
dlg = add_torrent_dialog.AddTorrentDialog(types.SimpleNamespace(remove=lambda h: None), handle,
                                          {"theme": "dark"}, default_dir, "Some film")
check(dlg.change_btn.isVisibleTo(dlg), "the add dialog has no way to choose a folder")
add_torrent_dialog.QFileDialog = types.SimpleNamespace(getExistingDirectory=lambda *a, **k: chosen_dir)
dlg._choose_folder()
check(dlg.save_entry.text() == os.path.normpath(chosen_dir), "the chosen folder isn't shown: %r" % dlg.save_entry.text())
dlg._confirm()
check(dlg.result and dlg.result["save_path"] == os.path.normpath(chosen_dir), "the chosen folder isn't returned")
print("the add dialog's Change... picks this torrent's folder")

# 2. The manager moves without replacing anything already there.
mgr = torrent_manager.TorrentManager.__new__(torrent_manager.TorrentManager)
h2 = Handle()
check(mgr.move(h2, chosen_dir), "move reported a failure")
lt = torrent_manager.lt
check(h2.calls == [("move_storage", chosen_dir, lt.move_flags_t.dont_replace)],
      "move didn't use move_storage with dont_replace: %r" % h2.calls)
print("moving keeps files already in the folder")


# 3. The Torrent tab uses the chosen folder, and a sticky pause.
class FakeDialog:
    def __init__(self, manager, handle, settings, save_path, name_hint, parent=None):
        self.result = {"selected": None, "auto_start": False, "save_path": os.path.normpath(chosen_dir)}
        self.file_list = None
        self.name_label = types.SimpleNamespace(text=lambda: "Some film")

    def exec(self):
        return 1


torrent_tab.AddTorrentDialog = FakeDialog
moved, paused, rows = [], [], []
fake_tab = types.SimpleNamespace(
    settings={}, save_state=lambda: None,
    manager=types.SimpleNamespace(move=lambda h, p: moved.append(p) or True,
                                  pause=lambda h: paused.append(h) or True),
    _add_row=lambda name, kind, uri, save_path, h, selected: rows.append(save_path))
h3 = Handle()
torrent_tab.TorrentTab._finish_add(fake_tab, h3, default_dir, kind="magnet", uri_or_path="magnet:?xt=urn:btih:00")
check(moved == [os.path.normpath(chosen_dir)], "the torrent wasn't moved to the chosen folder: %r" % moved)
check(os.path.isdir(chosen_dir), "the chosen folder wasn't created")
check(rows == [os.path.normpath(chosen_dir)], "the torrent is listed with the wrong folder: %r" % rows)
check(paused == [h3] and ("plain pause",) not in h3.calls, "not starting used a pause the auto-manager undoes")
print("the torrent goes to the chosen folder, and 'don't start' stays paused")

print("\nTORRENT FOLDER OK")
