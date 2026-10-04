"""A finished download reports its own file, not whichever file in the
folder happens to be newest."""
import os
import time

import _support
from _support import check

from app.core import downloader

folder = os.path.join(_support.STATE_DIR, "dl")
os.makedirs(folder)
mine = os.path.join(folder, "My Song.mp3")
theirs = os.path.join(folder, "Their Song.mp3")
open(mine, "w").close()
time.sleep(0.05)
open(theirs, "w").close()   # newer -- the old lookup would have picked this one

info = {"requested_downloads": [{"filepath": mine}]}
got = downloader._final_path(info, folder, ".mp3")
print("reported:", os.path.basename(got))
check(got == mine, "reported %r instead of the job's own file" % got)

# requested_downloads missing -> top-level filepath
got = downloader._final_path({"filepath": mine}, folder, ".mp3")
check(got == mine, got)

# Nothing recorded at all -> the old newest-file fallback, as a last resort.
got = downloader._final_path({}, folder, ".mp3")
check(got == theirs, got)

# A recorded path that no longer exists is not trusted.
got = downloader._final_path({"requested_downloads": [{"filepath": mine + ".gone"}]}, folder, ".mp3")
check(got == theirs, got)

print("\nDOWNLOAD PATHS OK")
