"""The Browser tab's home page: what a new tab shows.

It is a web page of the app's own (ui_qt/browser_assets/home), in a WebView2
of its own, served from a virtual host -- not Qt widgets. The page is where
the design wants things only a browser engine does well: the website's fonts
(Instrument Serif for the name), real backdrop blur on its glass, and a
wallpaper whose layers move with the pointer on the GPU at the display's
frame rate. The Qt version this replaced repainted the whole page on every
frame of that motion, which was slow and looked it (reported as such).

One page serves every tab that is on its home page; only one can be showing.
The page asks for its state once it has loaded ({type: 'ready'}) and gets it
as one message; what the person does on it comes back as messages too and is
acted on here: 'go' (open a link or a search here, in a new tab, or behind),
'pin' / 'remove' / 'hide' / 'add' for the tiles, 'set' for its switches, and
'pick-picture' / 'remove-picture' for a wallpaper of one's own.

The tiles are the person's own shortcuts first, then the sites they visit
most (browser_data.top_sites: visit counts, weighted toward recent visits),
so the page fills itself in. "Continue browsing" is the last few pages
visited, a page per site. A private tab gets neither -- only its pinned
shortcuts and a note on what private means.
"""
import base64
import colorsys
import os
import shutil
import time

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog, QWidget

from app import config
from app.logging_setup import get_logger
from app.utils import browser_data

from . import motion, palettes, theme, webview2

logger = get_logger(__name__)

HOST = "awdhome.example"
WALL_HOST = "awdwall.example"
URL = "https://%s/index.html" % HOST
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_assets", "home")
WALL_DIR = os.path.join(config.APPDATA_DIR, "home")
# Painted scenes. The first three move (a GPU shader, see home.js); the
# rest are still, layered for the parallax. "pic:<file>" is a picture of
# one's own, any number of which can be kept.
SCENES = ("silk", "borealis", "liquid", "glow", "aurora", "peaks", "dunes", "ocean", "nebula")
WALLPAPERS = SCENES
DEFAULT_WALLPAPER = "silk"
PIC_PREFIX = "pic:"
TILE_LIMIT = 12
RECENT_LIMIT = 4
SUGGEST_LIMIT = 400
_OPTIONS = {"home_parallax": True, "home_suggestions": True, "home_recent": True, "home_animate": True}
# Where each search engine's logo comes from: its own site's icon, fetched
# once and kept on disk like every other site icon (app.core.favicon).
ENGINE_SITES = {"google": "https://www.google.com", "duckduckgo": "https://duckduckgo.com",
                "bing": "https://www.bing.com", "brave": "https://brave.com",
                "yandex": "https://yandex.com"}


def _host(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _rgb(token):
    c = theme.qcolor(token)
    return (c.red(), c.green(), c.blue())


def _data_uri(pixmap):
    """A small QPixmap as a PNG data: URI, for the page."""
    if pixmap is None or pixmap.isNull():
        return None
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    buf.close()
    return "data:image/png;base64," + base64.b64encode(bytes(data)).decode("ascii")


def _css_vars(dark):
    """The page's colours, from the app's own tokens for this palette."""
    t = theme.tokens(dark)
    spec = palettes.spec(dark)
    base = spec["base"]
    brand = _rgb(t["brand"])
    second = spec["glows"][1][0]
    if dark:
        brand_hi = _mix(brand, (255, 255, 255), 0.4)
        brand_2 = _mix(second, (255, 255, 255), 0.15)
        return {
            "--bg": _hex(base), "--text": t["text"], "--muted": t["text_muted"], "--faint": t["text_faint"],
            "--brand": t["brand"], "--brand-hi": _hex(brand_hi), "--brand-2": _hex(brand_2),
            "--accent": t["accent"], "--accent-top": t["accent_top"], "--accent-text": t["accent_text"],
            "--glass": "rgba(255, 255, 255, .05)", "--glass-hi": "rgba(255, 255, 255, .085)",
            "--brd": "rgba(255, 255, 255, .11)", "--brd-hi": "rgba(255, 255, 255, .2)",
            "--rim": "rgba(255, 255, 255, .2)", "--shade": "rgba(0, 0, 0, .45)",
            "--panel": "rgba(%d, %d, %d, .9)" % _mix(base, (255, 255, 255), 0.05),
            "--field": "rgba(0, 0, 0, .2)",
        }
    deep = spec["deep"]
    return {
        "--bg": _hex(base), "--text": t["text"], "--muted": t["text_muted"], "--faint": t["text_faint"],
        "--brand": t["brand"], "--brand-hi": _hex(_mix(brand, (255, 255, 255), 0.18)),
        "--brand-2": _hex(_mix(second, deep, 0.45)),
        "--accent": t["accent"], "--accent-top": t["accent_top"], "--accent-text": t["accent_text"],
        "--glass": "rgba(255, 255, 255, .42)", "--glass-hi": "rgba(255, 255, 255, .62)",
        "--brd": "rgba(%d, %d, %d, .12)" % deep, "--brd-hi": "rgba(%d, %d, %d, .22)" % deep,
        "--rim": "rgba(255, 255, 255, .85)", "--shade": "rgba(%d, %d, %d, .18)" % deep,
        "--panel": "rgba(%d, %d, %d, .94)" % _mix(base, (255, 255, 255), 0.7),
        "--field": "rgba(255, 255, 255, .55)",
    }


_image_palettes = {}


def _luma(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hls_rgb(h, l, s):
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s))))


def image_palette(path):
    """The colours a picture is made of, turned into the page's own: a
    dark field from its shadows, and its most vivid colours (kept readable)
    for the headline, the search button and the glows. None if it can't be
    read. Cached per file and version."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _image_palettes:
        return _image_palettes[key]
    try:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((96, 96))
            quant = img.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
            raw = quant.getpalette()[:30]
            counts = sorted(quant.getcolors(), reverse=True)
    except Exception:   # noqa: BLE001
        logger.exception("Couldn't read colours from %s", path)
        _image_palettes[key] = None
        return None
    total = float(sum(n for n, _ in counts)) or 1.0
    colors = []
    for n, idx in counts:
        rgb = tuple(raw[idx * 3: idx * 3 + 3])
        h, l, sat = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
        colors.append({"rgb": rgb, "share": n / total, "h": h, "l": l, "s": sat})

    # The field: the darkest colour that covers a fair part of the picture.
    dark = sorted((c for c in colors if c["share"] > 0.04), key=lambda c: c["l"]) or colors
    field = dark[0]
    base = _hls_rgb(field["h"], min(0.09, field["l"] * 0.6 + 0.03), min(0.55, field["s"]))

    # The lights: vivid, not too dark or too pale, and not a speck.
    def vivid(c):
        return c["s"] * (1.0 - abs(c["l"] - 0.55) * 1.4) * (c["share"] ** 0.35)
    ranked = sorted(colors, key=vivid, reverse=True)
    first = ranked[0]
    second = next((c for c in ranked[1:] if min(abs(c["h"] - first["h"]), 1 - abs(c["h"] - first["h"])) > 0.08),
                  ranked[1] if len(ranked) > 1 else first)
    if first["s"] < 0.12:
        # A near-monochrome picture: a quiet silver rather than inventing a hue.
        brand = (214, 220, 232)
        brand_2 = (176, 186, 204)
    else:
        brand = _hls_rgb(first["h"], 0.66, max(0.45, min(0.85, first["s"])))
        brand_2 = _hls_rgb(second["h"], 0.70, max(0.35, min(0.8, second["s"])))
    accent = _hls_rgb(first["h"], 0.62, max(0.35, min(0.75, first["s"]))) if first["s"] >= 0.12 else (226, 230, 238)
    accent_text = "#14110d" if _luma(accent) > 0.45 else "#ffffff"
    out = {"base": base, "brand": brand, "brand_2": brand_2, "accent": accent, "accent_text": accent_text}
    _image_palettes[key] = out
    return out


def _picture_vars(pal):
    """sceneVars, recoloured from a picture's own palette."""
    v = dict(_css_vars(True))
    v.update({
        "--bg": _hex(pal["base"]),
        "--brand": _hex(pal["brand"]),
        "--brand-hi": _hex(_mix(pal["brand"], (255, 255, 255), 0.35)),
        "--brand-2": _hex(pal["brand_2"]),
        "--accent": _hex(pal["accent"]),
        "--accent-top": _hex(_mix(pal["accent"], (255, 255, 255), 0.22)),
        "--accent-text": pal["accent_text"],
        "--panel": "rgba(%d, %d, %d, .9)" % _mix(pal["base"], (255, 255, 255), 0.05),
    })
    return v


def _rig(dark):
    """The palette's light rig, for the page's Glow wallpaper."""
    spec = palettes.spec(dark)
    grid_rgb, grid_alpha, step = spec["grid"]
    return {
        "dark": bool(dark),
        "base": list(spec["base"]),
        "glows": [[list(c), a, list(centre), list(radii), fade] for c, a, centre, radii, fade in spec["glows"]],
        "grid": [list(grid_rgb), grid_alpha * 1.4, step],
        "brand": list(_rgb(theme.tokens(dark)["brand"])),
    }


class HomeView(QObject):
    """The shared home page and everything it asks the app for."""

    go = Signal(str, str)          # text or URL, where: current | tab | background
    surfaced = Signal()            # the page exists now: show it instead of the placeholder
    # The wallpaper's top strip -- what lies behind the title bar and the
    # browser's bars -- arrived (or changed): the window paints it there.
    strip_ready = Signal()

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.view = None
        # What a home tab shows until the page exists (a fraction of a
        # second): the window's own backdrop.
        self.placeholder = QWidget()
        self._loaded = False
        self._private = False
        self._icons = {}
        self.chrome_top = 0           # window top to the page's top, in px
        self.strip = None             # QImage of the wallpaper above the page
        self.strip_top = 0
        self._wanted = set()
        from .browser_chrome import favicons
        favicons().ready.connect(self._icon_arrived)
        self._adopt_new_default()

    @staticmethod
    def _adopt_new_default():
        """Silk is the home page's wallpaper from this version on -- for a
        new install, and once for an existing one too, unless the wallpaper
        there is a picture of the person's own. Any choice made after that
        is kept."""
        if browser_data.get_pref("home_wallpaper_default") == DEFAULT_WALLPAPER:
            return
        name = browser_data.get_pref("home_wallpaper")
        own = isinstance(name, str) and (name.startswith(PIC_PREFIX) or name == "custom")
        if not own:
            browser_data.set_pref("home_wallpaper", DEFAULT_WALLPAPER)
        browser_data.set_pref("home_wallpaper_default", DEFAULT_WALLPAPER)

    # ---- the page ----
    def surface(self):
        """The widget a home tab shows right now."""
        return self.view if self.view is not None and self.view.core is not None else self.placeholder

    def is_surface(self, widget):
        return widget is not None and widget in (self.view, self.placeholder)

    def ensure(self):
        """Creates the page if the engine can run it. Returns the view or None."""
        if self.view is not None:
            return self.view
        services = self.browser.services
        services.start()
        if services.error is not None or services.engine is None:
            return None
        os.makedirs(WALL_DIR, exist_ok=True)
        view = webview2.WebView2Widget(services.engine, private=False, parent=self.browser.pages,
                                       background=self.browser._page_bg())
        self.browser.pages.addWidget(view)
        view.created.connect(self._on_created)
        view.webMessage.connect(self._on_message)
        view.newWindowRequested.connect(self._on_new_window)
        view.acceleratorKey.connect(lambda args: self.browser._on_accelerator(self.browser._current(), args))
        view.processFailed.connect(self._on_crash)
        self.view = view
        return view

    def _on_created(self):
        view = self.view
        view.configure(IsZoomControlEnabled=False, IsStatusBarEnabled=False, AreDevToolsEnabled=False,
                       IsSwipeNavigationEnabled=False, IsPasswordAutosaveEnabled=False,
                       IsGeneralAutofillEnabled=False)
        view.set_color_scheme(self.browser._dark)
        view.map_host(HOST, ASSETS)
        view.map_host(WALL_HOST, WALL_DIR)
        view.load(URL)
        self.surfaced.emit()

    def _on_crash(self, kind):
        logger.warning("Home page process ended (%s); reloading it", kind)
        self._loaded = False
        if self.view is not None and self.view.core is not None:
            self.view.load(URL)

    def _on_new_window(self, args):
        # A link the page didn't handle itself must not open a window of its own.
        try:
            uri = str(args.Uri or "")
            args.Handled = True
        except Exception:   # noqa: BLE001
            return
        if uri.startswith(("http://", "https://")):
            self.go.emit(uri, "tab")

    def show_for(self, tab):
        """A home tab is being shown: the page follows its privacy."""
        private = bool(tab is not None and tab.private)
        if private != self._private:
            self._private = private
            self.refresh()

    def set_chrome_top(self, px):
        """How far below the window's top edge the page starts: the title
        bar and the browser's own bars, which the wallpaper runs behind."""
        px = int(px)
        if px == self.chrome_top:
            return
        self.chrome_top = px
        if self.view is not None and self._loaded:
            self.view.post_json({"type": "chrome", "top": px})

    def set_window_active(self, active):
        """The moving wallpaper stops while the window isn't the one in use."""
        if self.view is not None and self._loaded:
            self.view.post_json({"type": "active", "on": bool(active)})

    def replay(self):
        """The rise-in animation once more (a new tab was opened)."""
        if self.view is not None and self._loaded:
            self.view.post_json({"type": "replay"})

    # ---- state ----
    def apply_theme(self):
        if self.view is not None:
            self.view.set_background(self.browser._page_bg())
            self.view.set_color_scheme(self.browser._dark)
        self.refresh()

    def refresh(self):
        """Sends the page everything it shows, if it's there to receive it."""
        if self.view is not None and self._loaded:
            self.view.post_json(self.state())

    # ---- pictures of one's own ----
    def pictures(self):
        """The pictures kept for the home page, oldest first: file names in
        the wallpaper folder (the one folder the page can read)."""
        names = list(browser_data.get_pref("home_pictures", []) or [])
        changed = False
        # Earlier versions kept one picture: as a path anywhere on disk
        # (2.4), then as a copy here named custom-*. Both join the list.
        legacy = browser_data.get_home_background_image()
        if legacy and os.path.exists(legacy):
            if os.path.dirname(os.path.abspath(legacy)) == os.path.abspath(WALL_DIR):
                fn = os.path.basename(legacy)
            else:
                fn = self._copy_in(legacy)
            if fn and fn not in names:
                names.append(fn)
            browser_data.clear_home_background_image()
            changed = True
        kept = [n for n in names if os.path.isfile(os.path.join(WALL_DIR, n))]
        if changed or kept != names:
            browser_data.set_pref("home_pictures", kept)
        return kept

    def wallpaper(self):
        name = browser_data.get_pref("home_wallpaper")
        pics = self.pictures()
        if isinstance(name, str) and name.startswith(PIC_PREFIX) and name[len(PIC_PREFIX):] in pics:
            return name
        if name == "custom" and pics:          # "my picture", from before there could be several
            return PIC_PREFIX + pics[-1]
        if name in SCENES:
            return name
        return DEFAULT_WALLPAPER

    def _picture_path(self):
        """The picture that is the wallpaper now, or None."""
        name = self.wallpaper()
        if name.startswith(PIC_PREFIX):
            return os.path.join(WALL_DIR, name[len(PIC_PREFIX):])
        return None

    @staticmethod
    def _pic_url(fn):
        path = os.path.join(WALL_DIR, fn)
        return "https://%s/%s?v=%d" % (WALL_HOST, fn, int(os.path.getmtime(path)))

    def _custom_url(self):
        path = self._picture_path()
        return self._pic_url(os.path.basename(path)) if path else None

    def state(self):
        dark = self.browser._dark
        private = self._private
        shortcuts = [s for s in browser_data.load_shortcuts() if s.get("url")][:TILE_LIMIT]
        tiles = [{"url": s["url"], "title": s.get("title") or browser_data.site_name(_host(s["url"])),
                  "host": _host(s["url"]), "kind": "shortcut"} for s in shortcuts]
        options = {key[5:]: bool(browser_data.get_pref(key, default)) for key, default in _OPTIONS.items()}
        options["reduceMotion"] = motion.reduced()
        recent = []
        suggest = []
        if not private:
            if options["suggestions"] and len(tiles) < TILE_LIMIT:
                hidden = set(browser_data.get_pref("home_hidden_sites", []) or [])
                taken = {t["host"] for t in tiles} | hidden
                for site in browser_data.top_sites(TILE_LIMIT - len(tiles), exclude_hosts=taken):
                    tiles.append({"url": site["url"], "title": site["title"], "host": site["host"],
                                  "kind": "suggested"})
            if options["recent"]:
                recent = self._recent_pages({t["url"] for t in tiles})
            suggest = self._suggestions()
        icons = {}
        for item in tiles + recent:
            uri = self._icon_for(item["url"])
            if uri:
                icons[item["host"]] = uri
        engines = []
        for key, (label, _url) in browser_data.SEARCH_ENGINES.items():
            site = ENGINE_SITES.get(key, "")
            host = _host(site)
            engines.append([key, label, host])
            uri = self._icon_for(site) if site else None
            if uri:
                icons[host] = uri
        return {
            "type": "state",
            "dark": bool(dark),
            "private": private,
            "engine": browser_data.SEARCH_ENGINES[browser_data.get_search_engine()][0],
            "engineKey": browser_data.get_search_engine(),
            "engines": engines,
            "chromeTop": self.chrome_top,
            "website": config.WEBSITE,
            "vars": _css_vars(dark),
            "sceneVars": self._scene_vars(),
            "rig": _rig(dark),
            "wallpaper": self.wallpaper(),
            "custom": self._custom_url(),
            "pictures": [{"id": fn, "url": self._pic_url(fn)} for fn in self.pictures()],
            "options": options,
            "tiles": tiles,
            "recent": recent,
            "suggest": suggest,
            "icons": icons,
        }

    def _scene_vars(self):
        """Over a picture of one's own, the page takes its colours from it
        (not in a private tab: that's black and white throughout)."""
        if self._private:
            return _css_vars(True)
        path = self._picture_path()
        pal = image_palette(path) if path else None
        if pal:
            return _picture_vars(pal)
        return _css_vars(True)

    @staticmethod
    def _recent_pages(skip_urls):
        seen, out = set(), []
        for e in browser_data.load_history():
            url = e.get("url") or ""
            host = _host(url)
            if not url.startswith(("http://", "https://")) or not host or host in seen or url in skip_urls:
                continue
            if url.split(host, 1)[-1] in ("", "/"):
                continue     # a site's front page is what the tiles are for
            seen.add(host)
            out.append({"url": url, "title": e.get("title") or url, "host": host.removeprefix("www.")})
            if len(out) == RECENT_LIMIT:
                break
        return out

    @staticmethod
    def _suggestions():
        seen, out = set(), []
        for b in browser_data.load_bookmarks():
            url = b.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": b.get("title") or url, "bookmark": True})
        for e in browser_data.load_history():
            url = e.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": e.get("title") or url})
            if len(out) >= SUGGEST_LIMIT:
                break
        return out

    def _icon_for(self, url):
        host = _host(url)
        if not host:
            return None
        if host in self._icons:
            return self._icons[host]
        from .browser_chrome import favicons
        pm = favicons().get(url)
        if pm is not None:
            self._icons[host] = _data_uri(pm)
            return self._icons[host]
        self._wanted.add(host)
        return None

    def _icon_arrived(self, host, pixmap):
        if host not in self._wanted:
            return
        self._wanted.discard(host)
        uri = _data_uri(pixmap)
        if not uri:
            return
        self._icons[host] = uri
        if self.view is not None and self._loaded:
            self.view.post_json({"type": "icons", "icons": {host: uri, host.removeprefix("www."): uri}})

    # ---- what the page asks for ----
    def _on_message(self, msg):
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "ready":
            self._loaded = True
            self.view.post_json(self.state())
        elif kind == "go":
            text = str(msg.get("text") or "").strip()
            where = msg.get("where") if msg.get("where") in ("current", "tab", "background") else "current"
            if text:
                self.go.emit(text, where)
        elif kind == "pin":
            url = str(msg.get("url") or "")
            if url.startswith(("http://", "https://")):
                browser_data.add_shortcut(url, str(msg.get("title") or "") or browser_data.site_name(_host(url)))
                self.refresh()
        elif kind == "remove":
            browser_data.remove_shortcut(str(msg.get("url") or ""))
            self.refresh()
        elif kind == "hide":
            host = _host(str(msg.get("url") or ""))
            hidden = list(browser_data.get_pref("home_hidden_sites", []) or [])
            if host and host not in hidden:
                hidden.append(host)
                browser_data.set_pref("home_hidden_sites", hidden)
            self.refresh()
        elif kind == "add":
            url = str(msg.get("url") or "").strip()
            if url:
                if "://" not in url:
                    url = "https://" + url
                title = str(msg.get("title") or "").strip() or browser_data.site_name(_host(url))
                browser_data.add_shortcut(url, title)
                self.refresh()
        elif kind == "set":
            key, value = msg.get("key"), msg.get("value")
            if key == "home_wallpaper" and (value in SCENES or (
                    isinstance(value, str) and value.startswith(PIC_PREFIX)
                    and value[len(PIC_PREFIX):] in self.pictures())):
                browser_data.set_pref(key, value)
            elif key in _OPTIONS:
                browser_data.set_pref(key, bool(value))
            self.refresh()
        elif kind == "set-engine":
            if msg.get("key") in browser_data.SEARCH_ENGINES:
                browser_data.set_search_engine(msg["key"])
            self.refresh()
        elif kind == "strip":
            self._take_strip(str(msg.get("url") or ""), int(msg.get("top") or 0))
        elif kind == "pick-picture":
            self._pick_picture()
        elif kind == "remove-picture":
            self.remove_picture(str(msg.get("id") or ""))
            self.refresh()

    def _take_strip(self, data_url, top):
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1])
        except (IndexError, ValueError):
            return
        img = QImage.fromData(raw)
        if img.isNull():
            return
        self.strip, self.strip_top = img, top
        self.strip_ready.emit()

    def _pick_picture(self):
        paths, _ = QFileDialog.getOpenFileNames(self.browser, "Choose pictures for the home page", "",
                                                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        added = [fn for fn in (self._adopt_picture(p) for p in paths) if fn]
        if added:
            # The last one picked is shown; the rest wait in Customize.
            browser_data.set_pref("home_wallpaper", PIC_PREFIX + os.path.basename(added[-1]))
            self.refresh()

    @staticmethod
    def _copy_in(path):
        """A copy of a picture in the wallpaper folder; its file name, or None."""
        try:
            os.makedirs(WALL_DIR, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".jpg"
            stamp = int(time.time() * 1000)
            while True:
                fn = "pic-%d%s" % (stamp, ext)
                if not os.path.exists(os.path.join(WALL_DIR, fn)):
                    break
                stamp += 1
            shutil.copyfile(path, os.path.join(WALL_DIR, fn))
            return fn
        except OSError:
            logger.exception("Couldn't copy %s for the home page", path)
            return None

    def _adopt_picture(self, path):
        """Keeps a picture for the home page (a copy, in the one folder the
        page can read) beside any already kept. Its path, or None."""
        fn = self._copy_in(path)
        if not fn:
            return None
        names = self.pictures()
        names.append(fn)
        browser_data.set_pref("home_pictures", names)
        return os.path.join(WALL_DIR, fn)

    def remove_picture(self, fn):
        names = self.pictures()
        if fn not in names:
            return
        try:
            os.remove(os.path.join(WALL_DIR, fn))
        except OSError:
            pass
        names.remove(fn)
        browser_data.set_pref("home_pictures", names)
        if browser_data.get_pref("home_wallpaper") == PIC_PREFIX + fn:
            browser_data.set_pref("home_wallpaper", DEFAULT_WALLPAPER)
