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
from ui_qt.music_more import AllSongsCard, LibraryCard
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
check(len(mt._pages["artist"].findChildren(AllSongsCard)) == 1, "no All-songs card")
check(not mt._pages["artist"].hero.trio, "Popular shown twice")
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

# what a search found first: a card with its picture; clicked, it opens
artist_top = {"kind": "artist", "id": "ytm:artist:UCx", "browse_id": "UCx", "title": "Nobody", "subtitle": "5M monthly audience",
              "artwork": None}
mt._artist_cache["UCx"] = dict(artist, kind="artist", id="ytm:artist:UCx")
music_sources.search_everywhere = lambda q, cb: cb("music", {"top": artist_top, "songs": [song], "albums": [], "artists": []}, "")
mt.run_search("nobody")
settle(200)
card = mt._top_card
check(card.isVisible() and card.circle and card.title == "Nobody", "no top-result card")
check(not mt._pages["search"].hero.isVisible(), "the header sits above the card")
check("5M monthly audience" in card.over, card.over)
QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
settle(200)
check(mt._here[0] == "artist", "the card didn't open the artist: %s" % (mt._here,))
check("5M monthly audience" in mt._pages["artist"].hero.overline, mt._pages["artist"].hero.overline)
album_top = {"kind": "album", "id": "al:1", "browse_id": "b1", "title": "Made Up", "artist": "Nobody", "type": "Album", "year": "2020"}
music_sources.search_everywhere = lambda q, cb: cb("music", {"top": album_top, "songs": [song], "albums": [], "artists": []}, "")
mt.run_search("made up")
settle(200)
check(mt._top_card.isVisible() and not mt._top_card.circle and "Nobody" in mt._top_card.over, "the album's card")
rail.items["search"].click()
settle(100)
check(mt._pages["search"].hero.isVisible(), "the header didn't come back for the moods")
print("the top result's card")

# the full-screen player's views, in the phone's wide layouts
f = mt.full
win.resize(1400, 860)
settle(200)
mt.queue, mt.index = [song], 0
f._idle.setInterval(10 ** 7)        # (no fading under the checks; that's checked below)
mt.open_full()
settle(300)
check(f.view == "card" and f.card.mode == "side", "a wide window: the card on its side: %s" % f.card.mode)
check(f.card.art.x() + f.card.art.width() < f.title.x(), "the cover isn't left of the song")
check(f.card.width() > f.card.height(), "the card isn't lying on its side")
check(f.card.geometry().center().x() in range(f.width() // 2 - 2, f.width() // 2 + 3), "the card isn't in the middle")
for w in (f.lyrics_pill, f.queue_tile, f.eq_tile, f.repeat_btn, f.shuffle_btn, f.card.vol, f.prev_btn, f.next_btn, f.play_btn):
    check(w.isVisible() and f.card.rect().contains(w.geometry()), "%s isn't in the card" % w)
check(f.close_btn.x() < f.width() // 2 < f.moon_btn.x() < f.view_btn.x() < f.theme_btn.x() < f.share_btn.x()
      < f.info_btn.x() < f.dots_btn.x(), "the top bar's order")
print("the card on its side; the top bar in the phone's order")

# a card opened beside it doesn't sit on it
for size in ((1400, 860), (1000, 680)):
    win.resize(*size)
    settle(200)
    for panel in (f.queue_card, f.fx_card, f.info_card):
        f.show_card(panel, True)
        settle(600)
        check(panel.isVisible() and not panel.geometry().intersects(f.card.geometry()), "%s over the player at %s" % (panel, size))
        check(panel.x() >= 0 and panel.geometry().right() <= f.width(), "%s off the screen at %s" % (panel, size))
        f.show_card(panel, False)
        settle(400)
win.resize(1400, 860)
settle(200)
print("the cards round the player leave it clear")

# the old upright player, by choice, in any window
f.cycle_view()
settle(200)
check(f.view == "upright" and mt.settings.get("music_player_view") == "upright", "the upright player isn't kept")
check(f.card.mode == "portrait" and f.card.height() > f.card.width(), "not upright: %s" % f.card.mode)
check(f.card.art.y() + f.card.art.height() < f.title.y(), "the cover isn't above the song")
check(f.shuffle_btn.isVisible() and f.card.div_a.isVisible() and f.lyrics_pill.isVisible(), "its controls")
check(abs(f.card.geometry().center().x() - f.width() // 2) <= 2, "it isn't in the middle")
for size in ((1400, 860), (1000, 680)):
    win.resize(*size)
    settle(300)
    check(f.card.mode == "portrait", "the upright player at %s" % (size,))
    check(f.card.geometry().bottom() <= f.height() and f.card.width() <= f.width(), "it doesn't fit at %s" % (size,))
    f.show_card(f.queue_card, True)
    f.show_card(f.fx_card, True)
    settle(600)
    check(f.queue_card.isVisible() and f.fx_card.isVisible(), "both cards, either side, at %s" % (size,))
    check(f.queue_card.geometry().right() < f.card.x() and f.fx_card.x() > f.card.geometry().right(), "they sit on it")
    f.show_card(f.queue_card, False)
    f.show_card(f.fx_card, False)
    settle(400)
win.resize(1400, 860)
settle(200)
f.lyrics_pill.click()
settle(200)
check(f._words and f.card.mode == "portrait" and f.lyrics.x() > f.card.geometry().right(), "the words beside the upright card")
f.lyrics_pill.click()
settle(200)
check(not f._words and f.view == "upright", "the words didn't go")
mt.close_full()
settle(100)
mt.open_full()
settle(200)
check(f.view == "upright" and f.card.mode == "portrait", "the upright player didn't come back")
print("the upright player, in any window")

f.cycle_view()
settle(200)
check(f.view == "focus" and mt.settings.get("music_player_view") == "focus", "focus isn't kept")
check(f.focus_art.isVisible() and f.card.mode == "strip", "focus: the cover and the strip")
c = f.focus_art.geometry().center()
check(abs(c.x() - f.width() // 2) <= 2 and abs(c.y() - f.height() // 2) <= 2, "the cover isn't in the very middle")
check(f.card.width() == f.width() and f.card.geometry().bottom() < f.height(), "the strip isn't along the bottom")
for w in (f.prev_btn, f.back10_btn, f.play_btn, f.fwd10_btn, f.next_btn, f.queue_tile, f.heart, f.more_btn, f.card.vol, f.seek):
    check(w.isVisible() and f.card.rect().contains(w.geometry()), "%s isn't in the strip" % w)
check(not f.shuffle_btn.isVisible() and not f.eq_tile.isVisible(), "the card's other buttons in the strip")
check(abs(f.play_btn.geometry().center().x() - f.width() // 2) <= 2, "play isn't in the middle of the strip")
xs = [f.prev_btn.x(), f.back10_btn.x(), f.play_btn.x(), f.fwd10_btn.x(), f.next_btn.x(), f.queue_tile.x(), f.heart.x(),
      f.more_btn.x(), f.card.vol.x()]
check(xs == sorted(xs), "the strip's order: %s" % xs)
f.show_card(f.queue_card, True)
settle(600)
check(not f.focus_art.isVisible() and f.queue_card.isVisible(), "the queue over focus")
check(not f.queue_card.geometry().intersects(f.card.geometry()), "the queue sits on the strip")
f.show_card(f.queue_card, False)
settle(400)
check(f.focus_art.isVisible(), "focus's cover didn't come back")
print("focus: the cover in the middle, the strip along the bottom")

f.cycle_view()
settle(200)
check(f.view == "lyrics" and f._words and f.lyrics.isVisible() and f.card.mode == "strip", "the lyrics view")
check(f.lyrics.width() > f.width() * 0.8 and not f.lyrics.geometry().intersects(f.card.geometry()), "the words' room")
f.cycle_view()
settle(200)
check(f.view == "card" and not f._words and f.card.mode == "side" and f.shuffle_btn.isVisible(), "back to the card")
f.lyrics_pill.click()
settle(200)
check(f._words and f.view == "card" and f.card.mode == "strip", "the words over the card")
f.cycle_view()
settle(200)
check(not f._words and f.view == "card", "the view button didn't leave the words over the card")
print("the lyrics view; the words over the card go back to the card")

# restored on opening
f.set_view("focus")
mt.close_full()
settle(100)
mt.open_full()
settle(200)
check(f.view == "focus" and f.focus_art.isVisible(), "focus didn't come back")
f.set_view("lyrics")
mt.close_full()
settle(100)
mt.open_full()
settle(200)
check(f._words and f.lyrics.isVisible(), "the lyrics view didn't come back")

# the buttons fade, and come back
f.set_view("focus")
settle(100)
f._set_chrome(False)
settle(500)
check(not f.moon_btn.isVisible() and not f.card.isVisible(), "focus: the buttons and the strip didn't fade")
check(f.cursor().shape() == Qt.CursorShape.BlankCursor, "the cursor stays over focus")
f._wake()
settle(400)
check(f.moon_btn.isVisible() and f.card.isVisible() and f.card.graphicsEffect() is None, "they didn't come back")
f.set_view("card")
settle(100)
f._set_chrome(False)
settle(500)
check(not f.moon_btn.isVisible() and f.card.isVisible(), "the card's controls go with the top bar")
f._wake()
settle(400)
print("the buttons fade and come back")

# the top bar's menus
music_tab.QMenu = music_more.QMenu = SeenMenu
f.dots_btn.click()
for want in ("Like", "Lyrics", "Lyrics language", "Queue", "Repeat", "Shuffle", "Equalizer & effects", "Share", "More for this song"):
    check(want in shown[-1], "the player's dots have no %r: %s" % (want, shown[-1]))
f.moon_btn.click()
check("End of this song" in shown[-1], "the moon's menu: %s" % shown[-1])
f.share_btn.click()
check("Share as a video…" in shown[-1], "the share menu: %s" % shown[-1])
mt.set_sleep(15)
check(f.moon_btn.tint, "the moon isn't lit")
mt.set_sleep(None)
check(not f.moon_btn.tint, "the moon stayed lit")
music_tab.QMenu = music_more.QMenu = QMenu

# a tall or narrow window keeps the upright card
win.resize(1000, 860)
settle(300)
check(f.card.mode == "portrait" and f.shuffle_btn.isVisible() and f.card.div_a.isVisible(), "the upright card: %s" % f.card.mode)
win.resize(1000, 640)
settle(300)
check(f.card.mode == "side" and f.card.height() <= f.height() and f.card.width() <= f.width(), "a small wide window")
f.set_view("focus")
settle(200)
check(f.focus_art.geometry().bottom() - 30 < f.card.y(), "focus's cover under the strip in a small window")
f.set_view("card")
mt.close_full()
mt.queue, mt.index = [], -1
print("a small or tall window")
print("PHONE PARITY OK")
