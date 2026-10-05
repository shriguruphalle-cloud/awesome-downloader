"""AdGuard loads from a writable copy in the app's data folder, not from the
app's own folder. Installed, the app lives in Program Files, which WebView2
can't write to -- loading AdGuard from there failed with "Access is denied"
and every installed copy ran without an ad blocker (reported: "AdGuard not
started for too long"). The shield also said "still starting" for ever after
such a failure; it now says what went wrong."""
import os
import shutil
import tempfile

import _support
from _support import check

from app import config
from ui_qt import browser_engine as be

src = tempfile.mkdtemp(prefix="awd-adguard-src-")
with open(os.path.join(src, "manifest.json"), "w", encoding="utf-8") as f:
    f.write('{"name": "AdGuard", "version": "9.9.1"}')
os.makedirs(os.path.join(src, "pages"))
open(os.path.join(src, "pages", "options.html"), "w").close()
be.bundled_adguard_dir = lambda: src
be._adguard_dir = None

d = be.adguard_dir()
check(d.startswith(config.APPDATA_DIR), "AdGuard loads from %s, not the data folder" % d)
check(os.path.isfile(os.path.join(d, "manifest.json")) and os.path.isfile(os.path.join(d, "pages", "options.html")),
      "the copy is incomplete")
check(d.endswith("9.9.1"), "the copy isn't per version: %s" % d)
check(be.adguard_bundled(), "adguard_bundled() should look at what ships")
print("AdGuard copied to the data folder:", d)

# Stable: the same path next launch (WebView2 ties the extension to it), no re-copy.
mtime = os.path.getmtime(os.path.join(d, ".copied"))
be._adguard_dir = None
check(be.adguard_dir() == d and os.path.getmtime(os.path.join(d, ".copied")) == mtime, "re-copied on the next launch")

# A new AdGuard version replaces the old copy.
with open(os.path.join(src, "manifest.json"), "w", encoding="utf-8") as f:
    f.write('{"name": "AdGuard", "version": "9.9.2"}')
be._adguard_dir = None
d2 = be.adguard_dir()
check(d2.endswith("9.9.2") and not os.path.exists(d), "the old version's copy wasn't replaced")
print("same path every launch; a new version replaces the copy")

# Nowhere to copy to: falls back to the bundled folder rather than failing.
be._adguard_dir = None
real = shutil.copytree
shutil.copytree = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
with open(os.path.join(src, "manifest.json"), "w", encoding="utf-8") as f:
    f.write('{"name": "AdGuard", "version": "9.9.3"}')
check(be.adguard_dir() == src, "no fallback when the copy can't be made")
shutil.copytree = real
print("falls back to the app folder if the copy can't be made")

# The shield says why, instead of "still starting" for ever.
tab_src = open(os.path.join(config.BASE_DIR, "ui_qt", "browser_tab.py"), encoding="utf-8").read()
check("AdGuard couldn't start: %s" in tab_src and "adguard_error" in tab_src, "the shield doesn't report the failure")
print("\nADGUARD LOCATION OK")
