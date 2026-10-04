"""The way out: the "running" mutex the installer and uninstaller wait on,
and Settings > Your data > Uninstall, shown only in an installed copy.

Nothing is uninstalled: the uninstaller is a made-up empty file in a temp
folder, and starting it is recorded, not done."""
import ctypes
import os
import tempfile

import _support
from _support import check, qapp, settle

qapp()
from app import config
from app.utils import single_instance, uninstall

# ---- the mutex Setup and the uninstaller look for ------------------------------------------
SYNCHRONIZE = 0x00100000
kernel32 = ctypes.windll.kernel32
kernel32.OpenMutexW.restype = ctypes.c_void_p
# (Not "absent before": an open copy of the app holds it too, as it should.)
check(single_instance.hold_running_mutex(), "couldn't create the running mutex")
for name in (single_instance.RUNNING_MUTEX, "Global\\" + single_instance.RUNNING_MUTEX):
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    check(handle, "the installer couldn't see %s" % name)
    kernel32.CloseHandle(ctypes.c_void_p(handle))
iss = open(os.path.join(config.BASE_DIR, "installer.iss"), encoding="utf-8-sig").read()
check('#define MyAppMutex "%s"' % single_instance.RUNNING_MUTEX in iss,
      "installer.iss waits on a different mutex than the app holds")
print("running mutex: held, and the one installer.iss names")

# ---- finding the uninstaller ------------------------------------------------------------------
folder = tempfile.mkdtemp(prefix="awd-uninstall-")
check(uninstall.uninstaller_path(folder, frozen=True) is None, "found an uninstaller in an empty folder")
open(os.path.join(folder, "unins000.exe"), "wb").close()
found = uninstall.uninstaller_path(folder, frozen=True)
check(found == os.path.join(folder, "unins000.exe"), "didn't find unins000.exe: %s" % found)
check(uninstall.uninstaller_path(folder, frozen=False) is None, "offered to uninstall a copy run from source")
started = []
check(uninstall.launch_uninstaller(found, start=started.append) and started == [found],
      "the uninstaller wasn't started: %s" % started)
print("uninstaller: found beside an installed app only")

# ---- Settings shows the button only when installed ----------------------------------------------
from _support import build_window  # noqa: E402

from ui_qt.dialogs import settings_dialog  # noqa: E402

win, _tabs = build_window(tabs=("video", "download"))
orig = uninstall.uninstaller_path
try:
    uninstall.uninstaller_path = lambda *a, **k: None
    d = settings_dialog.SettingsDialog(win)
    check(d.uninstall_btn is None, "Settings offers Uninstall in a copy that isn't installed")
    d.deleteLater()
    uninstall.uninstaller_path = lambda *a, **k: found
    d = settings_dialog.SettingsDialog(win)
    check(d.uninstall_btn is not None and not d.uninstall_btn.isHidden(),
          "Settings has no Uninstall in an installed copy")
    d.deleteLater()
finally:
    uninstall.uninstaller_path = orig
print("settings: Uninstall shows only when installed")
win.close()
settle(100)
print("\nUNINSTALL OK")
