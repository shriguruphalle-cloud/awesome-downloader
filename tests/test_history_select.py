"""History: select rows (box, click, Shift+click, select all), then remove
them from the list or delete their files in one go.

The Recycle Bin is stubbed with a plain delete: a test may not put files in
the machine's real Recycle Bin."""
import os

import _support
from _support import settle, build_window, check, pump, qapp

app = qapp()
from PySide6.QtWidgets import QMessageBox  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402

from app import config  # noqa: E402
from app.utils import download_history, recycle  # noqa: E402

recycled_calls = []


def fake_recycle(paths):
    done = []
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
            done.append(p)
    recycled_calls.append(list(paths))
    return done, []


recycle.to_recycle_bin = fake_recycle
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

folder = os.path.join(config.APPDATA_DIR, "dl")
os.makedirs(folder, exist_ok=True)
files = []
for i in range(6):
    path = os.path.join(folder, "clip%d.mp4" % i)
    with open(path, "wb") as f:
        f.write(b"x" * 1000)
    files.append(path)
    download_history.add_entry("video" if i % 2 else "image", "Clip %d" % i, path, folder, 1000)

win, tabs = build_window(tabs=("video", "history"))
ht = tabs["history"]
win.tabs.setCurrentWidget(ht)
pump(3)
check(len(ht.rows) == 6, "rows: %d" % len(ht.rows))

# ---- a click selects, Shift+click selects the run between -----------------------
ht._on_row_toggled(ht.rows[1], True, False)
ht._on_row_toggled(ht.rows[3], True, True)
check(len(ht.selected) == 3 and ht._bar_shown, "Shift+click didn't select the run: %d" % len(ht.selected))
settle(500)
# What to do with them opens in a row of its own under the header: over
# nothing -- not the filters, not the search, not the first row (each was
# reported in turn).
def top(w):
    return w.mapTo(ht, QPoint(0, 0)).y()


header_bottom = max(top(w) + w.height() for w in (ht.count_label, ht.search, *ht.chips.values()) if w.isVisible())
bar_top, bar_bottom = top(ht.bar), top(ht.bar) + ht.bar.height()
check(ht.bar.isVisible() and bar_top >= header_bottom,
      "the selection bar overlaps the header (bar at y=%d, header ends at y=%d)" % (bar_top, header_bottom))
check(bar_bottom <= top(ht.rows[0]) + 1, "the selection bar sits over the first row")
check(all(c.isVisible() for k, c in ht.chips.items() if k == "all") and ht.search.isVisible(),
      "the filters or the search went away while selecting")
check(ht.bar.remove_btn.isVisible() and ht.bar.delete_btn.isVisible(), "the bar lost its actions")
check(ht.all_check.partial, "header box doesn't show a partial selection")
ht._select_all(True)
check(len(ht.selected) == 6 and ht.all_check.isChecked(), "select all missed rows")
ht._select_all(False)
check(not ht.selected and not ht._bar_shown, "deselect all left a selection or the bar")
settle(500)
check(not ht.bar_row.isVisible(), "the selection row stayed open with nothing selected")
print("selection: click, Shift+click range, select all, deselect all")

# ---- the filter narrows "select all" to what it shows ------------------------------
ht._set_filter("video")
check(len(ht.rows) == 3, "video filter shows %d" % len(ht.rows))
ht._select_all(True)
check(len(ht.selected) == 3, "select all under a filter took hidden rows too")
ht._select_all(False)
ht._set_filter("all")

# ---- remove from History keeps the files ----------------------------------------------
ht._on_row_toggled(ht.rows[0], True, False)
ht._on_row_toggled(ht.rows[1], True, False)
ht._remove_selected()
check(len(download_history.load()) == 4 and all(os.path.exists(f) for f in files),
      "Remove from History touched files or removed the wrong count")
print("remove from History: 2 entries gone, every file kept")

# ---- delete files: recycled, and gone from History ---------------------------------------
ht._select_all(True)
before = [r.entry["file_path"] for r in ht.rows]
ht._delete_selected()
pump(2)
check(recycled_calls and sorted(recycled_calls[-1]) == sorted(before), "not every selected file was recycled")
check(not download_history.load() and not ht.selected, "deleted files are still listed")
check(sum(os.path.exists(f) for f in files) == 2, "the wrong files were deleted")
print("delete files: %d recycled and removed from History" % len(before))

print("\nHISTORY SELECT OK")
