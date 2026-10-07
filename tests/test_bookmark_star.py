"""Chrome's star in the address bar: shown on a page (not the home page),
bookmarks the tab when clicked, then fills and offers to edit. The toolbar's
bookmark button lists every bookmark instead of adding a second way in."""
import os
import sys

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.utils import browser_data  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

win, tabs = build_window(tabs=("video", "browser", "download"), size=(1280, 800))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
win.show()
settle(4000)
star = bt.address.star_btn

check(not star.isVisible(), "the star shows on the home page, where there's nothing to bookmark")

bt._create_tab(url="https://example.invalid/page", activate=True)
settle(2500)
bt._current().title = "Example page"
bt._sync_toolbar()
settle(200)
check(star.isVisible(), "no star in the address bar on an open page")
check(star.kind == "star", "the star starts filled on a page that isn't bookmarked")

star.click()
settle(600)
check(browser_data.is_bookmarked(browser_data.load_bookmarks(), "https://example.invalid/page"),
      "clicking the star didn't bookmark the tab")
check(star.kind == "star_filled", "the star didn't fill once the tab was bookmarked")
popup = QApplication.activePopupWidget() or next(
    (w for w in QApplication.topLevelWidgets() if type(w).__name__ == "BookmarkPopup" and w.isVisible()), None)
check(popup is not None, "no 'Bookmark added' popup after clicking the star")
if len(sys.argv) > 1:          # a picture for a person to look at, when asked for
    win.grab().save(os.path.join(sys.argv[1], "star.png"))
popup.close()
settle(300)
print("the star bookmarks the tab and fills")

bt.bookmarks_btn.click()
settle(600)
panel = next((w for w in QApplication.topLevelWidgets()
              if type(w).__name__ == "BookmarksPanel" and w.isVisible()), None)
check(panel is not None, "the toolbar's bookmark button didn't list the bookmarks")
panel.close()
print("the toolbar button lists every bookmark")

win.close()
settle(1200)
print("\nBOOKMARK STAR OK")
