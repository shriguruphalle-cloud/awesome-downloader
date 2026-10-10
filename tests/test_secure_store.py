"""History, bookmarks and queues are encrypted on disk with Windows' data
protection: nothing readable in the files, read back as before, older plain
files encrypted at start-up, Settings can turn it off, and a file that can't
be decrypted is kept aside rather than overwritten. Test profile only."""
import glob
import json
import os

import _support
from _support import check

from app.utils import browser_data, download_history, secure_store  # noqa: E402

secure_store.ENCRYPT = True

# 1. bookmarks and download history: encrypted on disk, read back intact
browser_data.add_bookmark("https://example.test/secret-page", "My secret page")
download_history.add_entry("video", "Private holiday video", "C:/x/holiday.mp4", "C:/x", 1234)
for path in (browser_data.BOOKMARKS_PATH, download_history.HISTORY_PATH):
    raw = open(path, "rb").read()
    check(raw.startswith(secure_store.MAGIC), "%s isn't encrypted" % os.path.basename(path))
    check(b"secret" not in raw and b"holiday" not in raw, "%s has readable text in it" % os.path.basename(path))
check(any(b.get("title") == "My secret page" for b in browser_data.load_bookmarks()), "bookmarks didn't read back")
check(download_history.load()[0]["title"] == "Private holiday video", "history didn't read back")
print("bookmarks and history are encrypted on disk, and read back")

# 2. a plain file from before is still read, and encrypted at start-up
plain = [{"url": "https://old.test/", "title": "From before"}]
with open(browser_data.HISTORY_PATH, "w", encoding="utf-8") as f:
    json.dump(plain, f)
check(browser_data.load_history()[0]["title"] == "From before", "an old plain file wasn't read")
check(secure_store.migrate() >= 1 and secure_store.is_encrypted(browser_data.HISTORY_PATH),
      "start-up didn't encrypt an old plain file")
check(browser_data.load_history()[0]["title"] == "From before", "migrating changed the data")
print("older plain files are encrypted at start-up")

# 3. turned off in Settings: files become readable again
secure_store.ENCRYPT = False
secure_store.rewrite_all(secure_store.covered_paths())
check(not secure_store.is_encrypted(browser_data.BOOKMARKS_PATH), "turning it off left the files encrypted")
check(b"My secret page" in open(browser_data.BOOKMARKS_PATH, "rb").read(), "the plain file isn't plain")
secure_store.ENCRYPT = True
secure_store.rewrite_all(secure_store.covered_paths())
check(secure_store.is_encrypted(browser_data.BOOKMARKS_PATH), "turning it back on didn't encrypt")
print("Settings can turn it off and on again")

# 4. a file that can't be decrypted (another account's, say) is kept aside
with open(download_history.HISTORY_PATH, "wb") as f:
    f.write(secure_store.MAGIC + b"\x01\x02\x03 not a real blob")
check(download_history.load() == [], "an undecryptable history didn't fall back to empty")
kept = glob.glob(download_history.HISTORY_PATH + ".unreadable-*")
check(kept, "the undecryptable file wasn't kept aside")
print("an undecryptable file is kept aside, not lost")
# 5. going back (Settings > Updates) to a version from before encryption:
#    the files are left plain, as that version reads them
from app.utils import updater  # noqa: E402
secure_store.ENCRYPT = True
browser_data.add_bookmark("https://example.test/kept", "Kept across going back")
check(secure_store.is_encrypted(browser_data.BOOKMARKS_PATH), "precondition: bookmarks encrypted")
updater.install("C:/nowhere/setup.exe", "3.0.0", "2.5.0", rollback=True, launch=lambda p, a: True)
check(not secure_store.is_encrypted(browser_data.BOOKMARKS_PATH),
      "going back to 2.5.0 left the bookmarks encrypted, which 2.5.0 can't read")
check(b"Kept across going back" in open(browser_data.BOOKMARKS_PATH, "rb").read(), "the bookmarks were lost")
print("going back to a version from before encryption leaves the files readable to it")

print("\nSECURE STORE OK")
