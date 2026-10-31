"""The Music tab has the phone app's shape: four tabs, each with its own places under it; Home in the phone's order
with no moods (they're on Search); Your music's cards; the phone's placeholder and wordmark. Made-up data; no network."""
import json

import _support
from _support import build_window, check, settle
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.core import music_brain as brain
from ui_qt import music_look as look
from ui_qt.music_more import LibraryCard
from ui_qt.music_widgets import NavItem, SectionHeader, Tile

song = {"kind": "track", "id": "yt:00000000001", "title": "Synthetic", "artist": "Nobody", "source": "youtube",
        "page_url": "https://music.youtube.com/watch?v=00000000001", "duration": 180}
brain.home_charts = lambda region, langs, base, fresh: json.dumps({"ok": {"place": "India", "new": [], "charts": [
    {"kind": "mix", "id": "chart:t", "title": "Top Synthetic", "over": "Chart", "tracks": [song], "artwork": None}]}})

win, tabs = build_window(tabs=("music", "download"), size=(1400, 860))
win.show()
settle(300)
mt = tabs["music"]
rail = mt.rail

# four tabs and only those, plus the account row
top = [w.key for w in rail.findChildren(NavItem) if w.isVisibleTo(rail) and w.parent() is not None
       and not any(w.parent() is b for b in rail._subs.values())]
check(top == ["home", "search", "library", "social", "account"], "the rail's top: %s" % top)
print("four tabs: Home, Search, Your music, Social")


def shown_subs():
    return [k for k, b in rail._subs.items() if b.isVisibleTo(rail)]


for tab, want in (("library", "library"), ("social", "social"), ("home", "home")):
    rail.items[tab].click()
    settle(100)
    check(shown_subs() == [want], "%s shows %s" % (tab, shown_subs()))
check(mt._here[0] == "home", "Home didn't open")
rail.items["social"].click()
settle(100)
check(mt._here[0] == "chats", "Social opens on Chats, as on the phone: %s" % (mt._here,))
print("each tab shows its own sub-tabs; Social opens on Chats")

# Home: charts first, no moods
rail.items["home"].click()
for _ in range(100):
    settle(50)
    if mt._charts.get("charts"):
        break
check(mt._charts.get("charts"), "the charts never arrived")
home = mt._pages["home"]
labels = [h.title for h in home.sections.findChildren(SectionHeader)]
check(labels and labels[0].startswith("Top charts"), "Home doesn't open on charts: %s" % labels)
check(not any("Moods" in x for x in labels), "moods on Home: %s" % labels)
check(any(t.item.get("title") == "Top Synthetic" for t in home.sections.findChildren(Tile)), "the chart card isn't shown")
check(home.hero.title.startswith("Good "), "no greeting: %r" % home.hero.title)
print("Home: greeting, then charts; no moods")

# a chart card opens its songs, as on the phone; its button plays them
chart = [t for t in home.sections.findChildren(Tile) if t.item.get("title") == "Top Synthetic"][0]
QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(chart.width() // 2, 30))
settle(200)
check(mt._here[0] == "mix", "the chart card didn't open: %s" % (mt._here,))
check(mt._pages["mix"].hero.title == "Top Synthetic", "another page: %r" % mt._pages["mix"].hero.title)
check(not mt.queue, "the chart started playing instead of opening")
print("a chart card opens its songs")

# Moods on Search
rail.items["search"].click()
settle(200)
labels = [h.title for h in mt._pages["search"].sections.findChildren(SectionHeader)]
check(any("Moods" in x for x in labels), "no moods on Search: %s" % labels)
print("Search has the moods")

# Your music
rail.items["library"].click()
settle(200)
names = [c.title for c in mt._pages["library"].findChildren(LibraryCard)]
check(names == ["Liked", "Artists", "Albums", "On this PC", "Recent", "Queue"], "the cards: %s" % names)
print("Your music: the phone's six cards")

# the phone's pictures
art = look._placeholder_art()
check(not art.isNull(), "the placeholder image didn't load")
check(rail.brand.pixmap() is not None and not rail.brand.pixmap().isNull(), "the wordmark didn't load")
print("placeholder and wordmark are the phone's")
print("PHONE PARITY OK")
