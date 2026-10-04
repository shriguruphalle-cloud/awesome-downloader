"""Right-click a bookmark -> Edit...: its name and address, changed in place
(Chrome's "Edit bookmark"). Made-up bookmarks only; nothing is loaded."""
import _support
from _support import build_window, check, qapp, settle

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

qapp()
from app.utils import browser_data
from ui_qt import browser_chrome, theme
from ui_qt.browser_tab import bookmark_address
from ui_qt.dialogs.bookmark_dialog import EditBookmarkDialog


def urls():
    return [b["url"] for b in browser_data.load_bookmarks()]


# ---- storage: in place, one bookmark per address ------------------------------------------
for i in range(4):
    browser_data.add_bookmark("https://site%d.example/" % i, "Site %d" % i)
browser_data.update_bookmark("https://site1.example/", "https://renamed.example/", "  Renamed  ")
check(urls() == ["https://site0.example/", "https://renamed.example/", "https://site2.example/",
                 "https://site3.example/"], "the edited bookmark moved: %s" % urls())
check(browser_data.load_bookmarks()[1]["title"] == "Renamed", "the new name wasn't saved (trimmed)")
browser_data.update_bookmark("https://site3.example/", "https://site0.example/", "Dup")
check(urls().count("https://site0.example/") == 1, "two bookmarks now share an address: %s" % urls())
before = urls()
browser_data.update_bookmark("https://site2.example/", "   ", "Nothing")
check(urls() == before, "an empty address was saved")
browser_data.update_bookmark("https://site2.example/", "https://x.example/", "")
x = next(b for b in browser_data.load_bookmarks() if b["url"] == "https://x.example/")
check(x["title"] == "https://x.example/", "an empty name didn't fall back to the address")
print("storage: edited in place, no duplicates, no empty address")

# ---- what the URL box accepts --------------------------------------------------------------
check(bookmark_address("example.com") == "https://example.com", bookmark_address("example.com"))
check(bookmark_address("https://example.com/a?b=1") == "https://example.com/a?b=1", "a full URL was changed")
check(bookmark_address("two words") is None, "a search was accepted as a bookmark's address")
check(bookmark_address("") is None, "an empty address was accepted")
print("URL box: bare hosts get https, searches and blanks are refused")

# ---- the dialog ----------------------------------------------------------------------------
dlg = EditBookmarkDialog(None, "Site 0", "https://site0.example/", normalize=bookmark_address)
check(dlg.name.text() == "Site 0" and dlg.url.text() == "https://site0.example/", "the fields weren't filled in")
dlg.url.setText("not an address")
check(not dlg.save.isEnabled(), "Save is on for something that isn't an address")
dlg.url.setText("news.example")
dlg.name.setText("News")
check(dlg.save.isEnabled(), "Save is off for a valid address")
dlg.save.click()
check(dlg.values() == ("News", "https://news.example"), "the dialog returned %s" % (dlg.values(),))
dlg.deleteLater()
print("dialog: filled in, Save only for an address, returns (name, address)")

# ---- the menus offer Edit..., and it lands ---------------------------------------------------
menus = []


class RecordedMenu(QMenu):
    """Records the menu instead of opening it (a real one would wait on screen)."""

    def exec(self, *args, **kwargs):
        menus.append(self)


browser_chrome.QMenu = RecordedMenu
win, tabs = build_window(tabs=("video", "browser", "download"))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
settle(800)
bt.bookmarks_bar.set_bookmarks(browser_data.load_bookmarks())
settle(100)

bar = bt.bookmarks_bar
chip = bar._chips[0]
first = chip.url
bar._chip_menu(chip.url, QPoint(0, 0))
texts = [a.text() for a in menus[-1].actions() if not a.isSeparator()]
check("Edit…" in texts and texts.index("Edit…") < texts.index("Remove bookmark"),
      "the chip's menu has no Edit... before Remove: %s" % texts)


class Fake:
    def __init__(self, name, url):
        self.v = (name, url)

    def exec(self):
        return 1

    def values(self):
        return self.v


edited = []
bt.edit_bookmark = (lambda orig: (lambda url, dialog=None: edited.append(url)
                                  or orig(url, Fake("Docs", "https://docs.example/"))))(bt.edit_bookmark)
bar.edit_requested.disconnect()
bar.edit_requested.connect(bt.edit_bookmark)
next(a for a in menus[-1].actions() if a.text() == "Edit…").trigger()
settle(200)
check(edited == [first], "Edit... on the chip didn't open the editor: %s" % edited)
check(urls()[0] == "https://docs.example/", "the edit wasn't saved in place: %s" % urls())
check(any(c.url == "https://docs.example/" and c.title == "Docs" for c in bar._chips),
      "the bookmarks bar still shows the old bookmark")
print("bar: Edit... -> saved in place, the chip shows the new name")

panel = browser_chrome.BookmarksPanel(win, theme.tokens(True), True, browser_data.load_bookmarks())
panel._row_menu("https://docs.example/", QPoint(0, 0))
texts = [a.text() for a in menus[-1].actions() if not a.isSeparator()]
check("Edit…" in texts, "the All bookmarks panel's menu has no Edit...: %s" % texts)
asked = []
panel.edit_requested.connect(asked.append)
next(a for a in menus[-1].actions() if a.text() == "Edit…").trigger()
check(asked == ["https://docs.example/"], "Edit... in the panel asked for %s" % asked)
print("panel: Edit... in its menu too")

win.close()
print("\nBOOKMARK EDIT OK")
