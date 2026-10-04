"""A fresh install opens with one browser shortcut (YouTube), no bookmarks and
no history -- and a user who clears the shortcuts doesn't get them back."""
import _support
from _support import check

from app.utils import browser_data

shortcuts = browser_data.load_shortcuts()
print("shortcuts:", [(s["title"], s["url"]) for s in shortcuts])
check(len(shortcuts) == 1 and shortcuts[0]["url"] == "https://www.youtube.com", shortcuts)
check(browser_data.load_bookmarks() == [], "a fresh install ships bookmarks")
check(browser_data.load_history() == [], "a fresh install ships history")

browser_data.remove_shortcut("https://www.youtube.com")
check(browser_data.load_shortcuts() == [], "defaults came back after being cleared")

print("\nFRESH INSTALL OK")
