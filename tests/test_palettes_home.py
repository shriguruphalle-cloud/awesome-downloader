"""Colour palettes, the title bar's caption dots, the Browser's tab strip,
bookmarks and home page -- everything that doesn't need a live web page.

  * every palette changes the backdrop and the tokens, and switching one in
    Settings re-renders the window without a restart;
  * the caption dots are big enough to find, and keep their colour;
  * tabs are 20% narrower than before, there's a close-all button left of
    them, and closing all can be undone in one go;
  * a bookmark opens in a new tab (not over the page you're on), switches to
    it if it's already open, and reuses a blank New Tab;
  * the bookmarks panel lists every bookmark and opens one in a new tab;
  * the home page suggests the sites you visit, hides one you dismiss, and
    its wallpaper layers shift with the pointer only when parallax is on."""
import time

import _support
from _support import build_window, check, pump, qapp, settle

app = qapp()
from PySide6.QtCore import QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from app.utils import browser_data  # noqa: E402
from ui_qt import cinema, palettes, theme  # noqa: E402

# ---- palettes: distinct tokens and a distinct backdrop --------------------------
seen_accents, seen_bases = set(), set()
for name in palettes.ORDER:
    palettes.set_current(name)
    for dark in (True, False):
        t = theme.tokens(dark)
        for key in ("text", "accent", "brand", "card_bg_solid", "field_bg"):
            check(isinstance(t.get(key), str) and t[key], "%s/%s: no %s" % (name, dark, key))
        check(theme.qcolor(t["card_bg_solid"]).alpha() > 200, "%s: menus would be see-through" % name)
        qss = theme.build_stylesheet(dark)
        check(t["accent"] in qss, "%s: the stylesheet doesn't use the palette's accent" % name)
        sharp, frost = cinema.render_backdrop(200, 120, dark, 1.0, band=40)
        px = QColor(sharp.toImage().pixel(100, 110))
        seen_bases.add((px.red() // 8, px.green() // 8, px.blue() // 8))
        seen_accents.add(t["accent"])
palettes.set_current(palettes.DEFAULT)
check(theme.tokens(True)["accent"] == theme.DARK["accent"], "Sapphire isn't the original look")
check(len(seen_bases) >= 12, "palettes don't change the backdrop enough: %d distinct" % len(seen_bases))
print("%d palettes x 2 themes: tokens, stylesheet and backdrop all differ" % len(palettes.ORDER))

# ---- switching the palette live --------------------------------------------------
win, tabs = build_window(tabs=("video", "browser"), size=(1180, 760))
bt = tabs["browser"]
surface = win.backdrop_surface
before = QColor(surface._sharp.toImage().pixel(surface.width() // 2, surface.height() - 20))
win.settings["palette"] = "emerald"
win.apply_settings({"palette"})
pump(3)
surface.update()
pump(3)
check(palettes.current() == "emerald", "Settings didn't switch the palette")
after = QColor(surface._sharp.toImage().pixel(surface.width() // 2, surface.height() - 20))
check(after.green() > after.blue() and before.blue() > before.green(),
      "the backdrop didn't turn emerald: %s -> %s" % (before.name(), after.name()))
check(win.styleSheet().count(theme.tokens(True)["accent"]) > 0, "the window kept the old stylesheet")
win.settings["palette"] = "sapphire"
win.apply_settings({"palette"})
pump(2)
print("palette switches live: %s -> %s" % (before.name(), after.name()))

# ...and the Settings panel offers all of them.
from ui_qt.dialogs.settings_dialog import SettingsDialog  # noqa: E402
dlg = SettingsDialog(win)
check(len(dlg.palette.swatches) == len(palettes.ORDER), "the colour row is missing palettes")
dlg.palette.swatches[3].click()
pump(2)
check(win.settings.get("palette") == palettes.ORDER[3] and palettes.current() == palettes.ORDER[3],
      "clicking a swatch didn't apply it")
dlg.palette.swatches[0].click()
pump(2)
dlg.close()
print("Settings > Colour: %d swatches, applies on click" % len(dlg.palette.swatches))

# ---- caption dots: big, coloured, inactive or not --------------------------------
from ui_qt import main_window as mw  # noqa: E402
check(mw._CAPTION_DOT >= 16, "caption dots are only %dpx" % mw._CAPTION_DOT)
icon = win.titleBar._buttons[2].icon().pixmap(mw._CAPTION_BTN_W, mw._NAV_H).toImage()
centre = QColor(icon.pixel(icon.width() // 2, icon.height() // 2 + 3))
check(centre.red() > 200 and centre.green() < 140, "the close dot isn't red: %s" % centre.name())
print("caption dots: %dpx, close dot %s" % (mw._CAPTION_DOT, centre.name()))

# ---- tab strip --------------------------------------------------------------------
from ui_qt.browser_chrome import BookmarksPanel, TabStrip  # noqa: E402
check(TabStrip.MAX_W <= 180, "tabs are still %dpx wide" % TabStrip.MAX_W)
win.tabs.setCurrentWidget(bt)
pump(2)
first_tab = bt.strip.pills[0]
lead = bt.strip.close_all_btn.mapTo(bt.strip, QPoint(bt.strip.close_all_btn.width(), 0)).x()
check(bt.strip.close_all_btn.isVisible() and lead <= first_tab.x(),
      "the close-all button isn't left of the tabs")
win.tabs.setCurrentIndex(0)      # back to Video: nothing below loads a page
pump(2)

# ---- bookmarks open in a new tab -------------------------------------------------
for i in range(3):
    browser_data.add_bookmark("https://site%d.example/page" % i, "Site %d" % i)
start = len(bt._tabs)
check(bt._current().on_home, "the browser didn't start on a home tab")
bt._open_bookmark("https://site0.example/page", False)
check(len(bt._tabs) == start and bt._current().pending_url == "https://site0.example/page",
      "a bookmark from a blank New Tab didn't use that tab")
bt._open_bookmark("https://site1.example/page", False)
check(len(bt._tabs) == start + 1 and bt._current().url == "https://site1.example/page",
      "a bookmark replaced the page you were on instead of opening a new tab")
bt._open_bookmark("https://site0.example/page", False)
check(len(bt._tabs) == start + 1 and bt._current().url == "https://site0.example/page",
      "an open bookmark was opened a second time instead of switched to")
bt._open_bookmark("https://site2.example/page", True)
check(len(bt._tabs) == start + 2 and bt._current().url == "https://site0.example/page",
      "middle-click took you away from the page you were on")
print("bookmarks: new tab, switch-to-open, blank tab reused, background")

# The toolbar's bookmarks button: every bookmark, a click opens a new tab.
# (A popup closes itself as soon as this unfocused test window gets events,
# so each one is checked and used before the event loop runs again.)
import shiboken6  # noqa: E402
panel = bt._show_bookmarks_panel()
check(isinstance(panel, BookmarksPanel) and len(panel.rows) == 3, "the bookmarks panel lists %d"
      % (len(panel.rows) if isinstance(panel, BookmarksPanel) else -1))
panel.close()
pump(1)
n = len(bt._tabs)
browser_data.add_bookmark("https://site3.example/x", "Site 3")
panel = bt._show_bookmarks_panel()
row = next(r for r in panel.rows if r.url == "https://site3.example/x")
row.click()
check(len(bt._tabs) == n + 1 and bt._current().url == "https://site3.example/x",
      "clicking a bookmark in the panel didn't open it in a new tab")
check(not shiboken6.isValid(panel) or not panel.isVisible(), "the panel stayed open after opening a bookmark")
pump(1)
print("bookmarks panel: lists all, opens in a new tab")

# ---- close all, and undo ------------------------------------------------------------
open_urls = sorted(t.url for t in bt._tabs if not t.on_home)
bt.close_all_tabs()
pump(2)
check(len(bt._tabs) == 1 and bt._current().on_home, "close-all didn't leave one fresh tab")
bt.reopen_closed_tab()
pump(2)
check(sorted(t.url for t in bt._tabs if not t.on_home) == open_urls,
      "Ctrl+Shift+T didn't bring all the closed tabs back")
print("close all: %d tabs closed, one Ctrl+Shift+T brought them all back" % len(open_urls))

# ---- the home page (an HTML page; this checks what the app sends it) ------------
import os  # noqa: E402
from ui_qt import browser_home  # noqa: E402

for name in ("index.html", "home.css", "home.js", "fonts/instrument-serif-normal-400-latin.woff2",
             "fonts/instrument-serif-italic-400-latin.woff2"):
    check(os.path.exists(os.path.join(browser_home.ASSETS, name)), "home page file missing: %s" % name)

home = bt.home

# ---- a private tab: the window goes black and white, and comes back ------------------
chosen = palettes.current()
win.tabs.setCurrentWidget(bt)
pump(3)
private_tab = bt._create_tab(activate=True, private=True)
pump(3)
check(palettes.current() == palettes.MONO, "a private tab didn't turn the window black and white")
t = theme.tokens(True)
brand = theme.qcolor(t["brand"])
check(max(brand.red(), brand.green(), brand.blue()) - min(brand.red(), brand.green(), brand.blue()) <= 6,
      "the private look still has colour in it: brand %s" % brand.name())
check(win.settings.get("palette") == chosen, "the private look was saved as the palette")
check(browser_home._css_vars(True)["--brand"].lower() == t["brand"].lower(),
      "the home page didn't follow the private look")
bt._switch_to(bt._tabs[0])
pump(3)
check(palettes.current() == chosen, "a normal tab didn't bring the palette back: %s" % palettes.current())
bt._switch_to(private_tab)
pump(3)
win.tabs.setCurrentIndex(0)
pump(3)
check(palettes.current() == chosen, "leaving the Browser tab kept the private look")
win.tabs.setCurrentWidget(bt)
pump(3)
bt._close(private_tab)
pump(3)
check(palettes.current() == chosen, "closing the private tab kept the private look")
print("private tab: black and white while it shows; %s back after" % chosen)
home.show_for(None)       # the shared home page, back to a normal tab's (as showing one would)

# ---- the search engines: Yandex among them ------------------------------------------
from app.utils import browser_data as _bd  # noqa: E402
check("yandex" in _bd.SEARCH_ENGINES and "yandex" in browser_home.ENGINE_SITES, "Yandex isn't offered")
check(any(e[0] == "yandex" for e in home.state()["engines"]), "Yandex isn't on the home page's list")
_bd.set_search_engine("yandex")
check(_bd.search_url("a b").startswith("https://yandex.com/search/?text="), _bd.search_url("a b"))
_bd.set_search_engine(_bd.DEFAULT_SEARCH_ENGINE)

# ---- bookmarks: the bar's end says what it is ---------------------------------------
check(bt.bookmarks_bar.all_btn.text() == "All bookmarks", "the bookmarks bar's end button has no label")
from ui_qt.browser_chrome import BookmarkPopup  # noqa: E402
check(hasattr(BookmarkPopup, "show_all"), "the bookmark popup can't reach every bookmark")

# ---- Silk: the wallpaper from this version on, once, for an existing install too -----
_bd.set_pref("home_wallpaper", "glow")
_bd.set_pref("home_wallpaper_default", None)
browser_home.HomeView._adopt_new_default()
check(_bd.get_pref("home_wallpaper") == "silk", "an existing install didn't get Silk")
_bd.set_pref("home_wallpaper", "peaks")
browser_home.HomeView._adopt_new_default()
check(_bd.get_pref("home_wallpaper") == "peaks", "a wallpaper chosen afterwards didn't stick")
_bd.set_pref("home_wallpaper", "pic:mine.jpg")
_bd.set_pref("home_wallpaper_default", None)
browser_home.HomeView._adopt_new_default()
check(_bd.get_pref("home_wallpaper") == "pic:mine.jpg", "Silk replaced a picture of the person's own")
_bd.set_pref("home_wallpaper", None)
print("Yandex, the bookmarks bar's label, and Silk as the default")

for url, title, visits in (("https://often.example/a", "Thing - Often", 6),
                           ("https://often.example/b", "Other - Often", 4),
                           ("https://rarely.example/a", "Page - Rarely", 1)):
    for _ in range(visits):
        browser_data.add_history_entry(url, title)
sites = browser_data.top_sites(5)
check(sites and sites[0]["host"] == "often.example" and sites[0]["title"] == "Often",
      "top sites: %r" % sites[:2])
state = home.state()
suggested = [t for t in state["tiles"] if t["kind"] == "suggested"]
check(any(t["url"].startswith("https://often.example") for t in suggested), "no suggestion from history")
check(state["recent"] and all(not r["url"].rstrip("/").endswith(r["host"]) for r in state["recent"]),
      "continue browsing lists front pages: %r" % state["recent"])
check(state["vars"]["--brand"] == theme.tokens(state["dark"])["brand"], "the page isn't in the palette's colours")
home._on_message({"type": "hide", "url": "https://often.example/"})
check(not any("often.example" in t["url"] for t in home.state()["tiles"] if t["kind"] == "suggested"),
      "a dismissed site is still suggested")
print("home: suggests from history (%s), forgets a dismissed one" % sites[0]["title"])

# Private: no suggestions, no history on show.
home._private = True
private = home.state()
check(private["private"] and not private["recent"] and not private["suggest"]
      and all(t["kind"] == "shortcut" for t in private["tiles"]), "a private tab's home shows history")
home._private = False

# What the page asks for: a middle-click opens behind, settings stick.
before = len(bt._tabs)
home._on_message({"type": "go", "text": "https://site9.example/", "where": "background"})
check(len(bt._tabs) == before + 1 and bt._current().url != "https://site9.example/",
      "a middle-click on the home page didn't open a background tab")
home._on_message({"type": "set", "key": "home_wallpaper", "value": "peaks"})
check(home.state()["wallpaper"] == "peaks", "the wallpaper choice didn't stick")
home._on_message({"type": "set", "key": "home_parallax", "value": False})
check(home.state()["options"]["parallax"] is False, "the parallax switch didn't stick")
print("home messages: background tab, wallpaper, parallax")

# A picture of one's own recolours the page from its own palette.
from PIL import Image, ImageDraw  # noqa: E402
pic = os.path.join(os.environ["LOCALAPPDATA"], "sunset.png")
img = Image.new("RGB", (240, 160), (24, 10, 30))
ImageDraw.Draw(img).rectangle([0, 0, 240, 70], fill=(250, 140, 60))
img.save(pic)
kept = home._adopt_picture(pic)
check(kept, "couldn't take the picture")
pid = os.path.basename(kept)
home._on_message({"type": "set", "key": "home_wallpaper", "value": "pic:" + pid})
st = home.state()
check(st["wallpaper"] == "pic:" + pid and st["custom"].startswith("https://%s/" % browser_home.WALL_HOST),
      "the picture isn't the wallpaper: %r" % ((st["wallpaper"], st["custom"]),))
check([p["id"] for p in st["pictures"]] == [pid], "the picture isn't listed: %r" % st["pictures"])
brand = theme.qcolor(st["sceneVars"]["--brand"])
check(brand.red() > brand.blue() + 40, "the page didn't take the picture's warm colours: %s" % brand.name())
print("own picture: page recoloured to %s" % brand.name())

# Several pictures: each kept, each removable; removing the one shown falls
# back to the default scene.
second = home._adopt_picture(pic)
check(second and os.path.basename(second) != pid, "a second picture wasn't kept beside the first")
check(len(home.state()["pictures"]) == 2, "two pictures aren't both listed")
home._on_message({"type": "remove-picture", "id": pid})
st = home.state()
check([p["id"] for p in st["pictures"]] == [os.path.basename(second)], "removing one removed the wrong one")
check(st["wallpaper"] == browser_home.DEFAULT_WALLPAPER, "removing the picture shown left %r" % st["wallpaper"])
check(not os.path.exists(os.path.join(browser_home.WALL_DIR, pid)), "a removed picture's file is still there")
# A picture from before there could be several joins the list.
from app.utils import browser_data as bd  # noqa: E402
bd.set_home_background_image(pic)
bd.set_pref("home_wallpaper", "custom")
st = home.state()
check(len(st["pictures"]) == 2 and st["wallpaper"].startswith("pic:"), "the old single picture wasn't carried over")
print("pictures: several kept, one removed, the old one carried over")

win.close()
print("\nPALETTES AND HOME OK")
