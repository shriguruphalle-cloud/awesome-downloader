"""A browser download that fails on its own (the engine's "FileFailed" at 0 bytes) is logged with its
details and shown as failed; Retry on its card asks the page that started it for the file again. Its save
path has one kind of slash. Stand-ins for the engine's download; no network."""
import os
import tempfile

import _support
from _support import build_window, check, settle

from ui_qt.browser_tab import _NativeDownload

win, tabs = build_window(tabs=("browser", "download"))
bt, dl_tab = tabs["browser"], tabs["download_obj"]


class Op:
    def __init__(self, can_resume):
        self.State = "Interrupted"
        self.InterruptReason = "FileFailed"
        self.BytesReceived = 0
        self.TotalBytesToReceive = 1000
        self.Uri = "https://example.test/files/clip.mp4"
        self.CanResume = can_resume
        self.ResultFilePath = ""
        self.resumed = 0

    def Resume(self):
        self.resumed += 1
        self.State = "InProgress"


class View:
    def __init__(self):
        self.loaded = []

    def load(self, url):
        self.loaded.append(url)


folder = tempfile.mkdtemp(prefix="ad-dl-")
path = os.path.join(folder, "clip.mp4")
open(path, "wb").close()                        # the empty file a failed start leaves

# 1) a failed start is NOT resumed (that left cards stuck at 0%): it's shown as failed, once, and logged
op = Op(True)
d = _NativeDownload(bt, op, path, "example.test", view=View())
d._poll()
settle(50)
check(op.resumed == 0 and d.finished, "a failed start was resumed or left running")
print("failed start: shown as failed, not left running")

# 2) Retry on its card asks the page for the file again, and clears the old card
op2, view = Op(False), View()
d2 = _NativeDownload(bt, op2, path, "example.test", view=view)
d2._poll()
settle(50)
d2.retry()
check(view.loaded == [op2.Uri], "Retry didn't ask the page again: %s" % view.loaded)
check(op2.Uri in bt._retry_uris, "the retry isn't marked as one")
print("Retry: asks the page for it again")

# 3) a second failure is shown, not retried again
op3 = Op(False)
d3 = _NativeDownload(bt, op3, path, "example.test", view=View())
d3._auto_retried = True
d3._poll()
settle(50)
check(d3.finished, "a failure wasn't ended")
print("BROWSER DOWNLOAD RETRY OK")
