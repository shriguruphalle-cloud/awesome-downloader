"""The Music tab has the phone app's shape: four tabs, each with its own places under it; Home in the phone's order
with no moods (they're on Search); Your music's cards; the phone's placeholder and wordmark. Made-up data; no network."""
import json

import _support
from _support import build_window, check, settle
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.core import music_brain as brain
from app.utils import music_library
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
rail.items["home"].click()
settle(100)
check(rail.items["explore"].isVisibleTo(rail), "nothing leads to Explore")
rail.search.setFocus()
rail.items["library"].click()
settle(100)
check(not rail.search.hasFocus(), "moving to a page left the keyboard in the search box")
rail.items["home"].click()
settle(100)

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

# Explore reads the phone's lists, and the newest charts arrive on their own
brain.explore = lambda chip, region: json.dumps({"ok": {"trending": [song], "trending_title": "Top today", "week": [
    dict(song, id="yt:00000000002")], "week_title": "Top songs this week", "shelves": [], "new": []}})
brain.fresh_charts = lambda region: json.dumps({"ok": {"apple": [dict(song, id="yt:00000000003")], "apple_title": "Top Apple"}})
mt.go("explore")
for _ in range(100):
    settle(50)
    labels = [h.title for h in mt._pages["explore"].findChildren(SectionHeader)]
    if "Top Apple" in labels and "Top songs this week" in labels:
        break
check("Top Apple" in labels and "Top today" in labels and "Top songs this week" in labels, "Explore: %s" % labels)
print("Explore: today's charts, the week's, trending")

# Artist: the All-songs card and a radio
artist = {"title": "Nobody", "browse_id": "UCx", "songs": [song], "albums": [], "singles": [], "related": [], "about": "Made up."}
mt.go("artist", {"browse_id": "", "title": "Nobody"})
settle(100)
mt._pages["artist"].clear()
mt._fill_artist(artist, None)
settle(100)
check([c.title for c in mt._pages["artist"].findChildren(LibraryCard)] == ["All songs"], "no All-songs card")
print("Artist: All songs, radio")

# the phone's orders
real = music_library.saved
a, b = dict(song, id="yt:a", title="Alpha", duration=10), dict(song, id="yt:b", title="Beta", duration=99)
music_library.saved = lambda kind: [b, a] if kind == "songs" else []
mt.set_pref("music_liked_sort", "title")
check([t["title"] for t in mt._liked_in_order([b, a])] == ["Alpha", "Beta"], "by title")
mt.set_pref("music_liked_sort", "longest")
check([t["title"] for t in mt._liked_in_order([a, b])] == ["Beta", "Alpha"], "longest")
mt.set_pref("music_liked_sort", "recent")
mt._build_saved("songs")
check(mt._pages["songs"].hero.title == "Liked songs", "title: %r" % mt._pages["songs"].hero.title)
music_library.saved = real
print("Liked: the order chosen")

# playlists: pinned ones first
p1 = music_library.create_playlist("Older", [song])
p2 = music_library.create_playlist("Newer", [song])
mt.set_pref("music_playlist_sort", "az")
check([p["title"] for p in mt._playlists_in_order()][:2] == ["Newer", "Older"], "A-Z")
mt._toggle_pin(music_library.playlist(p1["id"] if isinstance(p1, dict) else p1))
settle(100)
check(mt._playlists_in_order()[0]["title"] == "Older", "the pinned one isn't first")
mt._build_library()
mt.go("playlist", p2["id"] if isinstance(p2, dict) else p2)
settle(150)
print("Playlists: the order chosen, pinned first")

# searches are remembered and can be forgotten
mt._remember_search("synthetic one")
mt._remember_search("synthetic two")
mt._remember_search("Synthetic One")
check(mt.settings["music_recent_searches"][:2] == ["Synthetic One", "synthetic two"], str(mt.settings["music_recent_searches"]))
mt._build_search_empty()
settle(100)
check(any(h.title == "Recently searched" for h in mt._pages["search"].findChildren(SectionHeader)), "not shown")
mt._forget_search()
settle(100)
check(not mt.settings["music_recent_searches"], "not cleared")
print("Recent searches")

# the song menu has what the phone's has; the sleep timer
from PySide6.QtWidgets import QMenu  # noqa: E402
from ui_qt import music_more, music_tab  # noqa: E402
shown = []


class SeenMenu(QMenu):
    def exec(self, *_a):
        shown.append([x.text() for x in self.actions()])


music_tab.QMenu = music_more.QMenu = SeenMenu
mt._row_menu(dict(song, album="Made Up", artist_browse="UCx"), QPoint(0, 0))
for want in ("Play next", "Like", "Add to a playlist…", "Start a radio from this song", "Go to artist", "Go to album",
             "Share to a chat…", "Not for me", "Download", "Copy link"):
    check(want in shown[-1], "the song menu has no %r: %s" % (want, shown[-1]))
mt.queue, mt.index = [song], 0
mt._song_menu()
check("Sleep timer…" in shown[-1], "no sleep timer: %s" % shown[-1])
mt.set_sleep(15)
check(mt._sleep_timer.isActive() and mt._sleep_words().endswith("min left"), mt._sleep_words())
mt.set_sleep(-1)
check(not mt._sleep_timer.isActive() and mt._sleep_at_song_end() and not mt._sleep_at_song_end(), "end of song")
mt.set_sleep(None)
check(mt._sleep_words() == "Sleep timer…", mt._sleep_words())
pl = music_library.playlist(p2["id"] if isinstance(p2, dict) else p2)
mt._playlist_more(pl)
for want in ("Add to the queue", "Download all", "Make private", "Rename", "Delete"):
    check(want in shown[-1], "the playlist menu has no %r: %s" % (want, shown[-1]))
mt._confirm_delete(pl)
check("Delete it" in shown[-1] and music_library.playlist(pl["id"]), "deleting asks first")
music_tab.QMenu = music_more.QMenu = QMenu
mt.queue, mt.index = [], -1
print("the song and playlist menus, the sleep timer")

# the Search tab comes back to the moods; Back skips a deleted playlist
from app.core import music_sources  # noqa: E402
music_sources.search_everywhere = lambda q, cb: None
mt.run_search("synthetic")
settle(100)
check(not any("Moods" in h.title for h in mt._pages["search"].findChildren(SectionHeader)), "moods under results")
rail.items["search"].click()
settle(100)
check(any("Moods" in h.title for h in mt._pages["search"].findChildren(SectionHeader)) and not rail.search.text(),
      "the Search tab didn't go back to the moods")
mt.run_search("synthetic")
settle(100)
rail.search.clear()
settle(100)
check(any("Moods" in h.title for h in mt._pages["search"].findChildren(SectionHeader)), "emptying the box left the results")
gone = music_library.create_playlist("Gone", [song])
gid = gone["id"] if isinstance(gone, dict) else gone
mt.go("library")
mt.go("playlist", gid)
music_library.delete_playlist(gid)
mt.go("recent")
mt.back()
settle(100)
check(mt._here[0] == "library", "Back opened a deleted playlist: %s" % (mt._here,))
print("Search goes back to the moods; Back skips what's gone")
print("PHONE PARITY OK")
