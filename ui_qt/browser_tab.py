"""Browser tab: a real embedded Chromium (multiple QWebEngineView tabs) for
downloading videos/files directly off the web without leaving the app --
paste a link, watch a video, or hit the floating logo button to send the
current page to the Video tab's own fetch/download pipeline. Kept in its
own module (separate from video_tab.py/main_window.py) since it owns a
large, self-contained subsystem: the persistent browser profile, ad-block
request interception, and the JS-injected overlay button + its QWebChannel
bridge back into Python.

No VPN here -- dropped from the original ask (real server infra, ongoing
cost, no-logs/jurisdiction concerns, out of scope for a downloader app).
"""
import base64
import math
import os
import re
import threading
import webbrowser

from PySide6.QtCore import (
    QFile, QIODevice, QObject, QPoint, QPointF, QRectF, QSize, QStringListModel, QTimer, QUrl,
    Qt, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QCompleter, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEngineContextMenuRequest, QWebEngineDownloadRequest, QWebEngineNewWindowRequest,
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from app import config
from app.core import adblock_updater, downloader, favicon
from app.logging_setup import get_logger
from app.utils import browser_data, download_history, formatting, settings as settings_store

from . import theme
from .browser_home import BrowserHomePage, _pil_to_pixmap_rgba

logger = get_logger("browser_tab")

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_assets")
_ADBLOCK_LIST_PATH = os.path.join(_ASSETS_DIR, "adblock_domains.txt")
_OVERLAY_LOGO_PATH = os.path.join(_ASSETS_DIR, "overlay_logo.png")

# Loaded once per process (44k+ lines) rather than once per BrowserTab --
# there's only ever one instance in practice, but this also means a second
# instance (there isn't one) wouldn't re-parse the file.
_adblock_domains = None


def _load_adblock_domains():
    """Unions the bundled snapshot with whatever adblock_updater.py's
    "Refresh ad-block list" has cached in APPDATA_DIR (if anything -- a
    fresh install has nothing there yet and just gets the bundled list, no
    different from before). Never reloaded mid-process: a refresh only
    takes effect on the next restart, matching how the yt-dlp update
    already works, and for the same reason -- keeping "what's currently
    blocking requests" a single stable snapshot for the life of the
    process, not something that can change out from under an open tab."""
    global _adblock_domains
    if _adblock_domains is not None:
        return _adblock_domains
    domains = set()
    try:
        with open(_ADBLOCK_LIST_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    domains.add(line)
    except OSError:
        logger.exception("Could not load ad-block domain list from %s", _ADBLOCK_LIST_PATH)
    try:
        with open(adblock_updater.EXTRA_DOMAINS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    domains.add(line)
    except OSError:
        pass  # nothing refreshed yet, or refresh_domain_list() has never run -- fine, bundled list stands alone
    _adblock_domains = domains
    return domains


class _AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    """Blocks sub-resource requests (scripts, images, iframes, XHR -- not
    the page navigation itself) whose host matches an EasyList domain rule
    or any subdomain of one. `||domain^` blocks the domain and everything
    under it, so a plain suffix check against each label of the host
    reproduces that rule without needing a trie for a set this size.

    Shared across every tab (one interceptor on the one shared profile) --
    per-site disabling is why this re-reads the disabled-hosts set on
    every request rather than caching it at construction: a toggle in one
    tab needs to take effect immediately for requests already in flight
    from any other tab on the same site."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._domains = _load_adblock_domains()
        self.enabled = True

    def interceptRequest(self, info):
        if not self.enabled:
            return
        if info.resourceType() == info.ResourceType.ResourceTypeMainFrame:
            return  # never block an actual page the user navigated to
        page_host = info.firstPartyUrl().host().lower()
        if page_host and page_host in browser_data.load_adblock_disabled_hosts():
            return
        host = info.requestUrl().host().lower()
        if not host:
            return
        labels = host.split(".")
        for i in range(len(labels) - 1):
            if ".".join(labels[i:]) in self._domains:
                info.block(True)
                return


class _Bridge(QObject):
    """JS-to-Python callback target for the injected overlay button and the
    video-playback-fallback banner. Registered on every tab's QWebChannel
    as `bridge`. One shared instance -- it carries no per-tab state, just
    relays whichever URL called it."""
    urlReceived = Signal(str)
    openSystemBrowserRequested = Signal(str)

    @Slot(str)
    def sendToVideoTab(self, url):
        self.urlReceived.emit(url)

    @Slot(str)
    def openInSystemBrowser(self, url):
        self.openSystemBrowserRequested.emit(url)


_qwebchannel_js_cache = None


def _qwebchannel_js():
    """Qt ships qwebchannel.js as a compiled-in Qt resource (available the
    moment QtWebChannel is imported, no loose file involved). Read directly
    via QFile rather than a page-side <script src qrc:///...> fetch -- the
    fetch approach requires setting .src on a <script> element, which sites
    with a strict Trusted Types CSP (YouTube included) refuse as an
    untrusted sink."""
    global _qwebchannel_js_cache
    if _qwebchannel_js_cache is not None:
        return _qwebchannel_js_cache
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        _qwebchannel_js_cache = bytes(f.readAll()).decode("utf-8")
        f.close()
    else:
        logger.error("Could not open the bundled qwebchannel.js Qt resource")
        _qwebchannel_js_cache = ""
    return _qwebchannel_js_cache


def _overlay_button_script(logo_data_uri):
    # Fixed bottom-right position per spec -- not per-video DOM detection.
    # Re-injected on every DocumentReady, so it survives full navigations;
    # `if (window.__adlOverlayInstalled) return;` guards against a second
    # copy appearing on SPA soft-navigations that re-fire DocumentReady.
    return f"""
(function() {{
    if (window.__adlOverlayInstalled) return;
    window.__adlOverlayInstalled = true;

    function install() {{
        var btn = document.createElement('button');
        btn.id = '__adl_overlay_btn';
        btn.title = 'Download with Awesome Downloader';
        // Built via DOM methods, not innerHTML -- sites with a Trusted
        // Types CSP (YouTube included) throw on any innerHTML string
        // assignment ("This document requires 'TrustedHTML' assignment"),
        // which silently killed the whole script before this fix.
        var img = document.createElement('img');
        img.src = '{logo_data_uri}';
        img.width = 48;
        img.height = 48;
        img.style.display = 'block';
        img.style.pointerEvents = 'none';
        // The artwork itself is already a self-contained circular badge
        // (dark disc + neon ring) -- a solid-color circle drawn *behind* it
        // just doubled up as an odd-looking ring-in-a-ring. This drop-shadow
        // is what keeps it legible on light-background pages instead.
        img.style.filter = 'drop-shadow(0 2px 6px rgba(0,0,0,0.45))';
        btn.appendChild(img);
        // Up and to the left of the bottom-right corner, and ~20% bigger
        // than the original 56px -- reported directly as overlapping the
        // page's own video-settings/fullscreen controls, which cluster
        // tight in that exact corner.
        btn.style.cssText = [
            'position:fixed', 'right:64px', 'bottom:64px', 'z-index:2147483647',
            'width:68px', 'height:68px', 'border-radius:50%', 'border:none',
            'background:transparent',
            'display:flex', 'align-items:center', 'justify-content:center',
            'cursor:pointer', 'padding:0', 'opacity:0.92',
            'transition:opacity 0.15s, transform 0.15s',
        ].join(';');
        // The extra padding around the icon is a real click target, not
        // decoration -- reported directly as too easy to miss-click right
        // next to a video's own on-page controls in the same corner.
        btn.onmouseenter = function() {{ btn.style.opacity = '1'; btn.style.transform = 'scale(1.06)'; }};
        btn.onmouseleave = function() {{ btn.style.opacity = '0.92'; btn.style.transform = 'scale(1)'; }};
        btn.onclick = function(e) {{
            e.preventDefault();
            e.stopPropagation();
            if (window.__adlBridge) {{
                window.__adlBridge.sendToVideoTab(window.location.href);
            }}
        }};
        (document.body || document.documentElement).appendChild(btn);
    }}

    // QWebChannel itself is injected as a separate DocumentCreation-time
    // script (see _qwebchannel_lib_script()) rather than pulled in here via
    // a dynamic <script src>: on Trusted-Types sites (YouTube included)
    // setting .src on a script element is itself a blocked sink, same as
    // the innerHTML problem above. By DocumentReady time (when this script
    // runs) the QWebChannel class is already a real global.
    new QWebChannel(qt.webChannelTransport, function(channel) {{
        window.__adlBridge = channel.objects.bridge;
    }});

    if (document.body) install();
    else document.addEventListener('DOMContentLoaded', install);
}})();
"""


# Cosmetic hiding on top of the domain-level network blocking above: the
# interceptor stops third-party ad *networks*, but plenty of ads (YouTube's
# in-player overlay/companion ads especially) are served from the same
# googlevideo.com/youtube.com domains as real video, so a domain block would
# have to choose between blocking ads and blocking playback. These selectors
# instead just hide the known ad-container elements after the fact -- a
# curated, conservative list (well-known class/id patterns), not an attempt
# at full EasyList cosmetic-rule parsing.
_ADBLOCK_CSS = """
.ad, .ads, .advert, .advertisement, .adsbygoogle, ins.adsbygoogle,
[id^="google_ads"], [id*="google_ads_iframe"], [class*="-ad-container"],
[class*="ad-banner"], [id*="ad-banner"], .ytp-ad-module, .ytp-ad-overlay-container,
.video-ads, .ytp-ad-player-overlay, #player-ads, ytd-ad-slot-renderer,
ytd-display-ad-renderer, ytd-promoted-sparkles-web-renderer,
ytd-promoted-video-renderer
{ display: none !important; }
"""


def _adblock_css_script():
    return f"""
(function() {{
    if (window.__adlCssInstalled) return;
    window.__adlCssInstalled = true;
    function install() {{
        var style = document.createElement('style');
        style.id = '__adl_adblock_css';
        // textContent, not innerHTML -- a plain text sink, unaffected by
        // Trusted-Types-CSP sites the way the button injection was.
        style.textContent = {_ADBLOCK_CSS!r};
        (document.head || document.documentElement).appendChild(style);
    }}
    if (document.head || document.body) install();
    else document.addEventListener('DOMContentLoaded', install);
}})();
"""


def _video_fallback_script():
    """QtWebEngine's bundled Chromium has no H.264/AAC decoder (a licensing
    exclusion, confirmed directly against real playback failures) -- there's
    no fixing that from inside this embedded browser. This offers a one-click
    way out instead: hand the exact same URL to the user's actual default
    browser, which has full codec support already.

    Two separate detection paths, not just one -- checked directly against a
    real previously-failing PornHub URL: that site's own player never even
    assigns the <video> a src once it decides (client-side) the codec isn't
    supported, so readyState sits at 0 and currentSrc stays empty forever --
    no 'error' event ever fires, only a permanently blank player. The error
    listener alone would have caught nothing there. The stall watcher (a
    <video> that exists but never advances past readyState 0 for several
    seconds) is what actually catches that real-world case; the error
    listener stays as the fast path for sites that *do* attempt and fail
    outright."""
    return """
(function() {
    if (window.__adlVideoFallbackInstalled) return;
    window.__adlVideoFallbackInstalled = true;
    var banner = null;

    function hideBanner() {
        if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
        banner = null;
    }

    function showBanner() {
        if (banner) return;
        banner = document.createElement('div');
        banner.id = '__adl_video_fallback_banner';
        banner.style.cssText = [
            'position:fixed', 'top:14px', 'left:50%', 'transform:translateX(-50%)',
            'z-index:2147483647', 'background:#1c1c1e', 'color:#fff',
            'padding:10px 12px 10px 16px', 'border-radius:10px',
            'font:13px -apple-system,Segoe UI,Arial,sans-serif',
            'display:flex', 'align-items:center', 'gap:10px',
            'box-shadow:0 4px 18px rgba(0,0,0,0.45)',
        ].join(';');

        var text = document.createElement('span');
        text.textContent = "This video didn't load here.";
        banner.appendChild(text);

        var openBtn = document.createElement('button');
        openBtn.textContent = 'Open in default browser';
        openBtn.style.cssText = [
            'background:#0A84FF', 'color:#fff', 'border:none', 'border-radius:6px',
            'padding:6px 10px', 'cursor:pointer', 'font:inherit', 'white-space:nowrap',
        ].join(';');
        openBtn.onclick = function() {
            if (window.__adlBridge) window.__adlBridge.openInSystemBrowser(window.location.href);
            hideBanner();
        };
        banner.appendChild(openBtn);

        var closeBtn = document.createElement('button');
        closeBtn.textContent = '\\u00d7';
        closeBtn.title = 'Dismiss';
        closeBtn.style.cssText = [
            'background:transparent', 'color:#999', 'border:none', 'cursor:pointer',
            'font-size:18px', 'line-height:1', 'padding:0 2px',
        ].join(';');
        closeBtn.onclick = hideBanner;
        banner.appendChild(closeBtn);

        (document.body || document.documentElement).appendChild(banner);
    }

    // 'error' on a <video>/<audio> element doesn't bubble -- capture phase
    // is the only way to catch it via a single document-level listener
    // instead of having to individually instrument every <video> tag a
    // page might add dynamically.
    document.addEventListener('error', function(e) {
        var el = e.target;
        if (el && el.tagName === 'VIDEO') {
            showBanner();
        }
    }, true);

    // Stall watcher: catches the sites where playback never even attempts
    // to start (see the docstring on this function for the real case that
    // motivated it) -- a WeakSet so re-scanning the page never double-
    // instruments the same element.
    var watched = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

    function watchVideo(v) {
        if (!watched || watched.has(v)) return;
        watched.add(v);
        var stalledTicks = 0;
        var totalTicks = 0;
        var interval = setInterval(function() {
            totalTicks++;
            if (!document.body || !document.body.contains(v) || banner) {
                clearInterval(interval);
                return;
            }
            if (v.readyState < 2 && v.currentTime === 0) {
                stalledTicks++;
            } else {
                // Real progress -- this video is fine, stop watching it.
                clearInterval(interval);
                return;
            }
            if (stalledTicks >= 5) {  // ~5s with zero progress
                showBanner();
                clearInterval(interval);
            } else if (totalTicks >= 15) {  // give up watching after ~15s either way
                clearInterval(interval);
            }
        }, 1000);
    }

    function scanForVideos() {
        var videos = document.querySelectorAll('video');
        for (var i = 0; i < videos.length; i++) watchVideo(videos[i]);
    }

    function install() {
        scanForVideos();
        // Real players (PornHub/xHamster included) mount their <video>
        // element via JS after the page's own initial load, not before --
        // a one-time scan at DocumentReady would miss it entirely.
        if (typeof MutationObserver !== 'undefined') {
            new MutationObserver(scanForVideos).observe(
                document.body || document.documentElement, {childList: true, subtree: true});
        }
    }
    if (document.body) install();
    else document.addEventListener('DOMContentLoaded', install);
})();
"""


def _logo_data_uri():
    try:
        with open(_OVERLAY_LOGO_PATH, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except OSError:
        logger.exception("Could not load overlay button logo from %s", _OVERLAY_LOGO_PATH)
        return ""


_home_favicon_cache = {}


def _home_favicon_pixmap(size):
    """The app's own logo, desaturated to grayscale, used as a tab's
    favicon placeholder while it's showing the home page -- a real
    favicon never gets fetched for a page that isn't a real site, and a
    blank favicon slot there read as broken/unfinished. Cached per size
    since every "New Tab" pill asks for the same pixmap."""
    if size in _home_favicon_cache:
        return _home_favicon_cache[size]
    src = QPixmap(_OVERLAY_LOGO_PATH)
    if src.isNull():
        _home_favicon_cache[size] = None
        return None
    scaled = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img = scaled.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 0:
                continue
            gray = int(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
            c.setRed(gray)
            c.setGreen(gray)
            c.setBlue(gray)
            img.setPixelColor(x, y, c)
    result = QPixmap.fromImage(img)
    _home_favicon_cache[size] = result
    return result


_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_LOOKS_LIKE_HOST_RE = re.compile(r"^[^\s/]+\.[^\s/]{2,}(/.*)?$")


def _new_icon_painter(size):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _nav_icon(kind, color, size=18):
    """Hand-drawn vector icons for the toolbar's nav/menu buttons -- not
    Unicode glyphs (was "←"/"→"/"⟳"/"⌂"/"⋮" text).
    Confirmed directly (rendered each glyph off-screen and counted actual
    drawn pixels) that those glyphs paint only a handful of very faint
    pixels in this font stack -- reported as "what are these buttons?",
    four indistinguishable blank squares. A stroked vector shape always
    covers a real, visible area regardless of what fonts are installed or
    how a QSS font-size rule interacts with the app's font-fallback chain."""
    pixmap, painter = _new_icon_painter(size)
    c = QColor(color)
    cx, cy = size / 2, size / 2
    pen = QPen(c)
    pen.setWidthF(max(1.6, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    if kind in ("back", "forward"):
        dx = size * 0.18
        dy = size * 0.24
        sign = -1 if kind == "back" else 1
        path = QPainterPath()
        path.moveTo(cx - sign * dx, cy - dy)
        path.lineTo(cx + sign * dx, cy)
        path.lineTo(cx - sign * dx, cy + dy)
        painter.drawPath(path)
    elif kind == "reload":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = size * 0.28
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        painter.drawArc(rect, 35 * 16, 280 * 16)
        end_angle = math.radians(35 + 280)
        ax = cx + r * math.cos(end_angle)
        ay = cy - r * math.sin(end_angle)
        head = size * 0.14
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        arrow = QPainterPath()
        arrow.moveTo(ax - head, ay - head * 0.3)
        arrow.lineTo(ax + head * 0.7, ay + head * 0.5)
        arrow.lineTo(ax - head * 0.5, ay + head)
        arrow.closeSubpath()
        painter.drawPath(arrow)
    elif kind == "home":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        w, h = size * 0.5, size * 0.42
        roof = QPainterPath()
        roof.moveTo(cx, cy - h * 0.95)
        roof.lineTo(cx - w * 0.62, cy - h * 0.15)
        roof.lineTo(cx - w * 0.4, cy - h * 0.15)
        roof.lineTo(cx - w * 0.4, cy + h * 0.55)
        roof.lineTo(cx + w * 0.4, cy + h * 0.55)
        roof.lineTo(cx + w * 0.4, cy - h * 0.15)
        roof.lineTo(cx + w * 0.62, cy - h * 0.15)
        roof.closeSubpath()
        painter.drawPath(roof)
    elif kind == "menu":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        r = max(1.3, size * 0.075)
        for dy in (-size * 0.22, 0, size * 0.22):
            painter.drawEllipse(QPointF(cx, cy + dy), r, r)
    elif kind == "stop":
        d = size * 0.22
        painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))
    elif kind == "zoom":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = size * 0.24
        lens_cx, lens_cy = cx - size * 0.06, cy - size * 0.06
        painter.drawEllipse(QPointF(lens_cx, lens_cy), r, r)
        handle_start = QPointF(lens_cx + r * 0.72, lens_cy + r * 0.72)
        handle_end = QPointF(cx + size * 0.28, cy + size * 0.28)
        painter.drawLine(handle_start, handle_end)
    elif kind == "plus":
        d = size * 0.26
        painter.drawLine(QPointF(cx - d, cy), QPointF(cx + d, cy))
        painter.drawLine(QPointF(cx, cy - d), QPointF(cx, cy + d))
    elif kind in ("speaker", "mute"):
        # Speaker cone (a small rect + triangle, filled) is the same shape
        # for both -- only what's drawn to its right changes (sound-wave
        # arcs vs. a crossed-out X), matching the standard mute/unmuted
        # glyph pairing every browser uses.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        w, h = size * 0.16, size * 0.28
        cone = QPainterPath()
        cone.moveTo(cx - size * 0.32, cy - h / 2)
        cone.lineTo(cx - size * 0.32 + w, cy - h / 2)
        cone.lineTo(cx - size * 0.06, cy - size * 0.34)
        cone.lineTo(cx - size * 0.06, cy + size * 0.34)
        cone.lineTo(cx - size * 0.32 + w, cy + h / 2)
        cone.lineTo(cx - size * 0.32, cy + h / 2)
        cone.closeSubpath()
        painter.drawPath(cone)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(c)
        pen.setWidthF(max(1.4, size * 0.09))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        if kind == "speaker":
            for r in (size * 0.16, size * 0.27):
                rect = QRectF(cx + size * 0.04 - r, cy - r, r * 2, r * 2)
                painter.drawArc(rect, -45 * 16, 90 * 16)
        else:
            d = size * 0.16
            x0, y0 = cx + size * 0.12, cy - size * 0.18
            painter.drawLine(QPointF(x0 - d, y0 - d), QPointF(x0 + d, y0 + d))
            painter.drawLine(QPointF(x0 + d, y0 - d), QPointF(x0 - d, y0 + d))

    painter.end()
    return QIcon(pixmap)


def _star_icon(filled, color, size=20):
    """5-pointed star, drawn as a real polygon -- replaces the "★"/
    "☆" text glyphs used for the address-bar bookmark action, which
    have the identical faint-glyph risk as the nav icons above."""
    pixmap, painter = _new_icon_painter(size)
    c = QColor(color)
    cx, cy = size / 2, size / 2
    outer = size * 0.46
    inner = outer * 0.42
    path = QPainterPath()
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = outer if i % 2 == 0 else inner
        x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    if filled:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
    else:
        pen = QPen(c)
        pen.setWidthF(max(1.3, size * 0.07))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def _normalize_address(text):
    """Address bar text -> a real URL. Anything that already has a scheme
    is used as-is; anything shaped like a bare host (has a dot, no spaces)
    gets https:// prepended; everything else is treated as a search query,
    same as typing into any real browser's combined address/search bar."""
    text = text.strip()
    if not text:
        return None
    if _URL_RE.match(text):
        return text
    if _LOOKS_LIKE_HOST_RE.match(text) or text.startswith("localhost"):
        return "https://" + text
    return "https://duckduckgo.com/?q=" + QUrl.toPercentEncoding(text).data().decode("ascii")


_TAB_FAVICON_SIZE = 16


class _TabPill(QWidget):
    """One entry in the tab strip -- favicon + title + close button, click
    to switch. Real Chrome's own shape: top corners rounded, flat bottom
    flush against the toolbar directly below it, no gap and no border --
    the active tab's fill simply continues straight into the toolbar so
    the two read as one connected surface, while inactive tabs stay flat
    and nearly transparent until hovered. A stretch factor on each pill
    (set where it's inserted into the strip's layout) lets tabs grow to
    fill the available row width the way Chrome's own tabs do, rather
    than sitting at a small fixed width with dead space beside them.
    The close button only appears on hover or while active -- permanently
    visible on every background tab reads as clutter once more than two
    or three are open.
    A plain QWidget (not a QPushButton) since it needs a child button of
    its own (close) that a QPushButton can't cleanly host."""
    clicked = Signal()
    close_requested = Signal()
    mute_toggled = Signal()
    _favicon_ready = Signal(object)  # background-thread payload: a PIL Image

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setMinimumWidth(90)
        self.setMaximumWidth(168)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._hovered = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(6)

        self.favicon_label = QLabel()
        self.favicon_label.setFixedSize(_TAB_FAVICON_SIZE, _TAB_FAVICON_SIZE)
        # Explicit no-op style -- otherwise it inherits the pill's own
        # unscoped `QWidget { border; border-radius }` rule (a plain QLabel
        # is a QWidget too), showing a stray boxed outline around an empty
        # favicon slot on the active tab.
        self.favicon_label.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(self.favicon_label)

        self.title_label = QLabel("New Tab")
        layout.addWidget(self.title_label, 1)

        # Hidden until the page actually produces sound (recentlyAudible),
        # same as Chrome's own tab strip -- a permanently-visible speaker
        # icon on every tab would just be noise.
        self.mute_btn = QPushButton()
        self.mute_btn.setFixedSize(22, 22)
        self.mute_btn.setIconSize(QSize(13, 13))
        self.mute_btn.setCursor(Qt.PointingHandCursor)
        self.mute_btn.setVisible(False)
        self.mute_btn.clicked.connect(self.mute_toggled.emit)
        layout.addWidget(self.mute_btn)

        # Icon, not a "×" glyph -- the same faint/invisible-glyph problem
        # already hit (and fixed) on the toolbar buttons applies here too.
        # Sized up from the original 16px -- reported as too small to
        # comfortably click.
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("Close tab")
        self.close_btn.setAccessibleName("Close tab")
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close_btn)

        self._checked = False
        self._incognito = False
        self._full_title = "New Tab"
        self._current_favicon_url = None
        self._favicon_ready.connect(self._apply_favicon)

    def set_title(self, text):
        self._full_title = text or "New Tab"
        elided = self.title_label.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, 90)
        self.title_label.setText(elided)
        self.setToolTip(("Private -- " + self._full_title) if self._incognito else self._full_title)

    def set_incognito(self, incognito):
        """Visual marker so a private tab is never mistaken for a normal
        one -- a real browser puts incognito in its own differently-themed
        window; this app only has tabs, so a distinct dot (in the favicon
        slot, where a real favicon never gets fetched for a private tab)
        plus a permanent background tint is the closest equivalent."""
        self._incognito = incognito
        if incognito:
            pixmap = QPixmap(_TAB_FAVICON_SIZE, _TAB_FAVICON_SIZE)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#9B7BFF"))
            painter.drawEllipse(0, 0, _TAB_FAVICON_SIZE, _TAB_FAVICON_SIZE)
            painter.end()
            self.favicon_label.setPixmap(pixmap)
        self.setToolTip(("Private -- " + self._full_title) if incognito else self._full_title)
        self._restyle()

    def set_url(self, url):
        """Kicks off a background favicon fetch for this tab's current
        page -- skipped entirely for a URL already fetched (every
        urlChanged during a single page's own navigation would otherwise
        refetch the same icon), and entirely for an incognito tab (its
        favicon slot always shows the private-tab marker instead, set once
        in set_incognito())."""
        if self._incognito or not url or url == self._current_favicon_url:
            return
        self._current_favicon_url = url
        self.favicon_label.clear()
        threading.Thread(target=self._fetch_favicon_thread, args=(url,), daemon=True).start()

    def show_home_state(self):
        """Resets this pill to a fresh "New Tab" look -- title, favicon
        (the app's own grayscale logo, not a blank slot), everything. Used
        both when a tab is first created and whenever Back/Forward or the
        Home button lands it back on the home page: without this, a tab
        that had navigated away and come back via Back kept showing its
        old page's title/favicon even though the home page was what was
        actually on screen."""
        self.set_title("New Tab")
        self._current_favicon_url = None
        if self._incognito:
            return
        pixmap = _home_favicon_pixmap(_TAB_FAVICON_SIZE)
        if pixmap is not None:
            self.favicon_label.setPixmap(pixmap)
        else:
            self.favicon_label.clear()

    def _fetch_favicon_thread(self, url):
        img = favicon.get_favicon(url, size=_TAB_FAVICON_SIZE)
        if img is not None:
            self._favicon_ready.emit(img)

    def set_audible(self, audible):
        """Shows/hides the mute button -- only while the page is actually
        producing sound, same as Chrome's own tab strip. Muted-but-silent
        tabs (paused video, muted before playback started) don't need the
        control visible; audible ones do."""
        self._audible = audible
        self.mute_btn.setVisible(audible or getattr(self, "_muted", False))
        self._restyle_mute_icon()

    def set_muted(self, muted):
        self._muted = muted
        self.mute_btn.setToolTip("Unmute tab" if muted else "Mute tab")
        self.mute_btn.setAccessibleName("Unmute tab" if muted else "Mute tab")
        self.mute_btn.setVisible(muted or getattr(self, "_audible", False))
        self._restyle_mute_icon()

    def _restyle_mute_icon(self):
        t = getattr(self, "_t", None)
        if not t:
            return
        color = t["text"] if self._checked else t["text_muted"]
        self.mute_btn.setIcon(_nav_icon("mute" if getattr(self, "_muted", False) else "speaker", color, size=16))

    def _apply_favicon(self, pil_image):
        pixmap = _pil_to_pixmap_rgba(pil_image)
        if pixmap is not None and not pixmap.isNull():
            self.favicon_label.setPixmap(pixmap.scaled(
                _TAB_FAVICON_SIZE, _TAB_FAVICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def set_checked(self, checked):
        self._checked = checked
        self._restyle()

    def apply_theme(self, t):
        self._t = t
        self._restyle()

    def _update_close_visibility(self):
        self.close_btn.setVisible(self._checked or self._hovered)

    def _restyle(self):
        t = getattr(self, "_t", None)
        if not t:
            return
        # Fully rounded floating pill with a real gap on every side (the
        # warm/Safari-esque direction) rather than Chrome's flat-bottomed
        # flush shape -- the active pill sits raised off the strip with a
        # soft shadow, inactive pills stay flat and nearly transparent
        # until hovered. A private tab keeps a faint purple tint even when
        # inactive -- the whole point of the marker is that it stays
        # visible without needing to click into the tab first.
        if self._checked:
            bg = t["card_bg_solid"]
        elif self._incognito:
            bg = "rgba(155, 123, 255, 40)"
        elif self._hovered:
            bg = t["hover_overlay"]
        else:
            bg = "transparent"
        fg = t["text"] if self._checked else t["text_muted"]
        # Bold on every tab, not just the active one -- the site name is
        # the one thing a tab exists to communicate, and the lighter
        # inactive weight was reported as hard to read at this size.
        weight = 700 if self._checked else 600
        # A plain CSS border stands in for the "raised" shadow on the
        # active pill -- a real QGraphicsDropShadowEffect forces Qt to
        # composite this widget through an offscreen buffer on every
        # tab-state change, which this project has already hit as a real
        # DWM-Acrylic-desync trigger when it happens near a live
        # QWebEngineView (see mica.py's own note on addWindowAnimation for
        # the same underlying fragility). Reported directly as ghosting
        # caption buttons and a window that goes fully invisible until the
        # theme is toggled a few times -- removing the graphics effect
        # here is the fix, not a cosmetic downgrade.
        border = "rgba(255, 255, 255, 40)" if self._checked else "transparent"
        self.setStyleSheet(f"""
            QWidget {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 13px;
            }}
        """)
        self.title_label.setStyleSheet(
            f"color: {fg}; font-size: 11px; font-weight: {weight}; background: transparent; border: none;")
        self.close_btn.setIcon(_nav_icon("stop", fg, size=12))
        self.close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; border-radius: 11px; }}
            QPushButton:hover {{ background: rgba(128,128,128,60); }}
        """)
        self.mute_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; border-radius: 11px; }}
            QPushButton:hover {{ background: rgba(128,128,128,60); }}
        """)
        self._restyle_mute_icon()
        self._update_close_visibility()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._restyle()
        super().leaveEvent(event)


class BrowserTab(QWidget):
    # Overlay button click, carrying the page URL to hand off to the Video
    # tab's existing fetch/download flow.
    open_in_video_tab = Signal(str)
    # A page's own video asked to go fullscreen (its Fullscreen API, e.g.
    # YouTube's fullscreen button) -- MainWindow hides its own titlebar/tab
    # island and resizes to fill the screen while this is True.
    fullscreen_requested = Signal(bool)
    _progress_sig = Signal(int, float, str)
    _download_done_sig = Signal(int, str)
    _download_error_sig = Signal(int, str)
    # progress_hook runs on the download thread -- can't touch download_tab's
    # widgets directly, same reason _progress_sig exists.
    _playable_sig = Signal(int)
    # adblock_updater.refresh_domain_list() runs on a background thread (real
    # network call) -- same cross-thread-to-GUI marshaling reason as above.
    _adblock_refresh_sig = Signal(bool, str)

    def __init__(self, settings, download_tab, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.download_tab = download_tab
        self._loading = False
        self.download_dir = settings_store.get_save_dir(settings, "video", config.DEFAULT_DOWNLOAD_DIR)
        os.makedirs(self.download_dir, exist_ok=True)
        # job_id -> {"cancel", "pause_event", "path", "title"} -- the card
        # itself lives in download_tab, keyed by this same id. Kept around
        # indefinitely rather than popped on completion (see video_tab.py's
        # _forget_job for why) -- the Play button stays live on a completed
        # card for several seconds and still needs "path" to resolve.
        self._jobs = {}
        self._progress_sig.connect(self._update_progress)
        self._download_done_sig.connect(self._on_download_done)
        self._download_error_sig.connect(self._on_download_error)
        self._playable_sig.connect(lambda jid: self.download_tab.set_playable(jid, True))
        self._adblock_refresh_sig.connect(self._on_adblock_refresh_done)
        self._adblock_refreshing = False

        # Multi-tab state. Each entry: {"view", "page", "home_page",
        # "stack" (home vs. real page), "pill"}. One shared profile (so
        # cookies/logins carry across tabs, same as any real browser).
        self._tabs = []
        self._current_index = -1
        self._closed_urls = []  # recently-closed tab URLs, for reopen (Ctrl+Shift+T)
        self._zoom_factor = 1.0

        self._setup_shared_profile()
        self._build_ui()
        self._create_tab(activate=True)
        self._setup_shortcuts()

    # --------------------------------------------------------- Profile ----
    def _setup_shared_profile(self):
        profile_dir = os.path.join(config.APPDATA_DIR, "browser_profile")
        os.makedirs(profile_dir, exist_ok=True)
        # A named, non-off-the-record profile persists cookies/logins across
        # app restarts (the same as any real browser) -- kept even though
        # the headline use case is YouTube "without login", since any other
        # site the user browses to still needs normal session handling.
        self.profile = QWebEngineProfile("awesome-downloader-browser", self)
        self.profile.setPersistentStoragePath(profile_dir)
        self.profile.setCachePath(os.path.join(profile_dir, "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

        # Kept as real attributes (not locals) -- PySide6 does not keep a C++-
        # side owning reference to Python QObjects handed to setters like
        # this one, so an unreferenced interceptor gets garbage-collected out
        # from under the profile and crashes the process on the next request.
        self._interceptor = _AdBlockInterceptor(self)
        self.profile.setUrlRequestInterceptor(self._interceptor)
        self.profile.downloadRequested.connect(self._on_download_requested)

        self._bridge = _Bridge(self)
        self._bridge.urlReceived.connect(self.open_in_video_tab.emit)
        self._bridge.openSystemBrowserRequested.connect(self._open_in_system_browser)

        self._attach_shared_scripts(self.profile)

        # Incognito: a genuinely separate, off-the-record profile -- no
        # storage name passed in, which is what tells QtWebEngine to keep
        # everything (cookies, cache, history) in memory only, gone the
        # moment the tab closes. Same ad-block/download-button/find-in-page
        # behavior as a normal tab (own interceptor + own copies of the
        # same scripts, not shared QWebEngineScript instances with the
        # persistent profile -- keeps the two profiles fully independent
        # rather than relying on Qt's implicit-sharing semantics holding up
        # under removal/teardown of either one).
        self._incognito_profile = QWebEngineProfile(self)
        self._incognito_interceptor = _AdBlockInterceptor(self)
        self._incognito_profile.setUrlRequestInterceptor(self._incognito_interceptor)
        self._incognito_profile.downloadRequested.connect(self._on_download_requested)
        self._attach_shared_scripts(self._incognito_profile)

    def _attach_shared_scripts(self, profile):
        """Builds fresh QWebEngineScript objects and attaches them to
        `profile` -- called once for the normal profile and once for the
        incognito one, so every tab (private or not) gets the overlay
        download button, ad-block cosmetic CSS, the QWebChannel bridge, and
        the H.264-fallback banner, regardless of which profile it's using."""
        qwebchannel_lib_script = QWebEngineScript()
        qwebchannel_lib_script.setName("adl_qwebchannel_lib")
        qwebchannel_lib_script.setSourceCode(_qwebchannel_js())
        qwebchannel_lib_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        qwebchannel_lib_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        qwebchannel_lib_script.setRunsOnSubFrames(False)
        profile.scripts().insert(qwebchannel_lib_script)

        logo_uri = _logo_data_uri()
        overlay_script = QWebEngineScript()
        overlay_script.setName("adl_overlay_button")
        overlay_script.setSourceCode(_overlay_button_script(logo_uri))
        overlay_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        overlay_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        overlay_script.setRunsOnSubFrames(False)
        profile.scripts().insert(overlay_script)

        adblock_css_script = QWebEngineScript()
        adblock_css_script.setName("adl_adblock_css")
        adblock_css_script.setSourceCode(_adblock_css_script())
        adblock_css_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        adblock_css_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        adblock_css_script.setRunsOnSubFrames(True)
        profile.scripts().insert(adblock_css_script)

        video_fallback_script_obj = QWebEngineScript()
        video_fallback_script_obj.setName("adl_video_fallback")
        video_fallback_script_obj.setSourceCode(_video_fallback_script())
        video_fallback_script_obj.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        video_fallback_script_obj.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        video_fallback_script_obj.setRunsOnSubFrames(False)
        profile.scripts().insert(video_fallback_script_obj)
        # Kept alive on the instance (not just local variables) -- PySide6
        # does not keep a C++-side owning reference to Python QObjects handed
        # to a profile's script collection, so unreferenced scripts would be
        # garbage-collected out from under it. Namespaced by profile identity
        # so the normal and incognito profiles' copies don't overwrite each
        # other's references.
        self._script_refs = getattr(self, "_script_refs", [])
        self._script_refs.extend([
            qwebchannel_lib_script, overlay_script, adblock_css_script, video_fallback_script_obj,
        ])

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Tab strip -- above the toolbar, real Chrome's own layout: tabs
        # grow to fill the row (stretch factor set where each is inserted),
        # sit nearly flush against each other, and the active one's fill
        # continues straight down into the toolbar with no visible seam.
        self.tabstrip_frame = QFrame()
        self.tabstrip_frame.setObjectName("browserTabstrip")
        tabstrip = QHBoxLayout(self.tabstrip_frame)
        # Chrome's own tab strip and toolbar are about 34px and 40px; ours
        # were 43 each. The difference was all padding, and on this window it
        # was padding taken directly off the page.
        tabstrip.setContentsMargins(8, 2, 8, 2)
        tabstrip.setSpacing(6)
        self._tabstrip_layout = tabstrip
        self.new_tab_btn = QPushButton()
        self.new_tab_btn.setToolTip("New tab (Ctrl+T) -- right-click for a private tab")
        self.new_tab_btn.setAccessibleName("New tab")
        self.new_tab_btn.setFixedSize(30, 30)
        self.new_tab_btn.setIconSize(QSize(17, 17))
        self.new_tab_btn.setCursor(Qt.PointingHandCursor)
        self.new_tab_btn.clicked.connect(lambda: self._create_tab(activate=True))
        self.new_tab_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.new_tab_btn.customContextMenuRequested.connect(self._show_new_tab_menu)
        tabstrip.addWidget(self.new_tab_btn)
        tabstrip.addStretch(1)
        root.addWidget(self.tabstrip_frame)

        # Wrapped in one card-style frame (not a bare row of buttons floating
        # on the transparent background) to actually read as a browser
        # toolbar rather than loose controls. Layout/ordering follows
        # Chrome's own: nav cluster hugging the left edge, address bar
        # (with the bookmark star embedded in it, not beside it) claiming
        # all the remaining space, overflow menu at the far right.
        self.toolbar_frame = QFrame()
        self.toolbar_frame.setObjectName("browserToolbar")
        toolbar = QHBoxLayout(self.toolbar_frame)
        toolbar.setContentsMargins(6, 3, 8, 3)
        toolbar.setSpacing(4)

        self.back_btn = QPushButton()
        self.fwd_btn = QPushButton()
        self.reload_btn = QPushButton()
        self.home_btn = QPushButton()
        self.back_btn.setToolTip("Back (Alt+Left)")
        self.fwd_btn.setToolTip("Forward (Alt+Right)")
        self.reload_btn.setToolTip("Reload (Ctrl+R)")
        self.home_btn.setToolTip("Home")
        # Every icon-only button in this toolbar gets a real accessible
        # name, not just a tooltip -- a screen reader announces
        # accessibleName(), and Qt doesn't reliably fall back to tooltip
        # text for that on its own. Otherwise every one of these buttons
        # reads as silent/unlabeled to anyone using one.
        self.back_btn.setAccessibleName("Back")
        self.fwd_btn.setAccessibleName("Forward")
        self.reload_btn.setAccessibleName("Reload")
        self.home_btn.setAccessibleName("Home")
        self.back_btn.setEnabled(False)
        self.fwd_btn.setEnabled(False)
        for btn in (self.back_btn, self.fwd_btn, self.reload_btn, self.home_btn):
            btn.setFixedSize(30, 30)
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.PointingHandCursor)
            toolbar.addWidget(btn)

        # Claims every pixel of empty space once the wide text-label
        # buttons that used to sit beside it are gone -- both the bookmarks
        # and history lists moved into the "⋮" overflow menu instead.
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Search or enter a web address")
        self.address_bar.setFixedHeight(30)
        # Chrome-style: the bookmark star lives inside the address bar's own
        # trailing edge, not as a separate button next to it.
        self._bookmark_action = QAction(self)
        self._bookmark_action.triggered.connect(self._show_bookmark_dialog)
        self.address_bar.addAction(self._bookmark_action, QLineEdit.ActionPosition.TrailingPosition)
        toolbar.addWidget(self.address_bar, 1)

        # Address-bar autocomplete over history + bookmark URLs, Chrome-style
        # (contains-match, case-insensitive). The model is refreshed lazily
        # whenever history/bookmarks change rather than kept live, since
        # QCompleter has no notion of "data changed under me".
        self._address_completer_model = QStringListModel(self)
        self._address_completer = QCompleter(self)
        self._address_completer.setModel(self._address_completer_model)
        self._address_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._address_completer.setFilterMode(Qt.MatchContains)
        self._address_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.address_bar.setCompleter(self._address_completer)
        self._refresh_address_completer()

        # No Go button. Enter in the address bar already navigates, and every
        # mainstream browser dropped this control years ago -- it was the only
        # filled accent block in the toolbar, so removing it is what lets the
        # bar read as one quiet strip rather than a form with a submit.

        # Zoom control -- sits with the other secondary controls at the
        # right edge, next to the overflow menu.
        self.zoom_btn = QPushButton()
        self.zoom_btn.setToolTip("Page zoom")
        self.zoom_btn.setAccessibleName("Page zoom")
        self.zoom_btn.setFixedSize(32, 32)
        self.zoom_btn.setIconSize(QSize(18, 18))
        self.zoom_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.zoom_btn)

        self.menu_btn = QPushButton()
        self.menu_btn.setToolTip("Bookmarks and history")
        self.menu_btn.setAccessibleName("Browser menu")
        self.menu_btn.setFixedSize(32, 32)
        self.menu_btn.setIconSize(QSize(18, 18))
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.menu_btn)
        root.addWidget(self.toolbar_frame)

        # Find-in-page -- a small floating tool window, not a row built
        # into this widget's own layout: QWebEngineView renders through a
        # native compositor surface, and an ordinary child widget isn't
        # guaranteed to actually draw on top of that through normal Qt
        # z-ordering. A real top-level window sidesteps that entirely --
        # Qt.Tool rather than Qt.Popup so clicking the page underneath
        # (e.g. to follow a found link) doesn't immediately close it the
        # way a popup would.
        self.find_frame = QFrame(self, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.find_frame.setObjectName("browserFindBar")
        self.find_frame.setFixedSize(230, 38)
        find_layout = QHBoxLayout(self.find_frame)
        find_layout.setContentsMargins(10, 4, 6, 4)
        find_layout.setSpacing(4)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in page")
        self.find_input.setFixedHeight(28)
        self.find_input.textChanged.connect(lambda text: self._find_in_page(text))
        self.find_input.returnPressed.connect(lambda: self._find_in_page(self.find_input.text()))
        find_layout.addWidget(self.find_input, 1)
        self.find_prev_btn = QPushButton()
        self.find_next_btn = QPushButton()
        self.find_close_btn = QPushButton()
        self.find_prev_btn.setToolTip("Previous match")
        self.find_prev_btn.setAccessibleName("Previous match")
        self.find_next_btn.setToolTip("Next match")
        self.find_next_btn.setAccessibleName("Next match")
        self.find_close_btn.setToolTip("Close find bar (Esc)")
        self.find_close_btn.setAccessibleName("Close find bar")
        for b in (self.find_prev_btn, self.find_next_btn, self.find_close_btn):
            b.setFixedSize(22, 22)
            b.setIconSize(QSize(11, 11))
            b.setCursor(Qt.PointingHandCursor)
            find_layout.addWidget(b)
        self.find_prev_btn.clicked.connect(lambda: self._find_in_page(self.find_input.text(), backward=True))
        self.find_next_btn.clicked.connect(lambda: self._find_in_page(self.find_input.text()))
        self.find_close_btn.clicked.connect(self._hide_find_bar)
        find_escape = QShortcut(QKeySequence("Escape"), self.find_frame)
        find_escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_escape.activated.connect(self._hide_find_bar)

        # One entry per open tab, only the active one visible.
        self._tabs_stack = QStackedWidget()
        root.addWidget(self._tabs_stack, 1)

        self.back_btn.clicked.connect(self._on_back_clicked)
        self.fwd_btn.clicked.connect(self._on_forward_clicked)
        self.reload_btn.clicked.connect(self._on_reload_clicked)
        self.home_btn.clicked.connect(self._show_home_page)
        self.address_bar.returnPressed.connect(self._navigate_from_address_bar)
        self.menu_btn.clicked.connect(self._show_overflow_menu)
        self.zoom_btn.clicked.connect(self._show_zoom_menu)

        self.apply_theme()

    def _setup_shortcuts(self):
        def add(seq, slot):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            return sc

        self._shortcuts = [
            add("Ctrl+L", lambda: (self.address_bar.setFocus(), self.address_bar.selectAll())),
            add("Ctrl+D", self._show_bookmark_dialog),
            add("Ctrl+F", self._show_find_bar),
            add("Escape", self._on_escape_pressed),
            add("Ctrl+T", lambda: self._create_tab(activate=True)),
            add("Ctrl+W", lambda: self._close_tab(self._current_index)),
            # Ctrl+Shift+T only -- a bare Shift+T loses to normal typing
            # any time a text field has focus (address bar, find bar),
            # since Shift+T there just types the letter T. This is the
            # actual industry-standard binding for reopen-closed-tab
            # anyway (every major browser uses it).
            add("Ctrl+Shift+T", self._reopen_closed_tab),
            add("Ctrl+R", self._on_reload_clicked),
            add(QKeySequence.StandardKey.Refresh, self._on_reload_clicked),
            add("Alt+Left", self._on_back_clicked),
            add("Alt+Right", self._on_forward_clicked),
            # Chrome's own binding for a new private/incognito window --
            # this app only has tabs, not separate windows, so it opens a
            # private tab instead; same keyboard muscle memory either way.
            add("Ctrl+Shift+N", lambda: self._create_tab(activate=True, incognito=True)),
        ]

    # ------------------------------------------------------------ Tabs ----
    def _current_tab(self):
        if 0 <= self._current_index < len(self._tabs):
            return self._tabs[self._current_index]
        return None

    def _active_view(self):
        tab = self._current_tab()
        return tab["view"] if tab else None

    @property
    def view(self):
        return self._active_view()

    @property
    def page(self):
        tab = self._current_tab()
        return tab["page"] if tab else None

    @property
    def home_page(self):
        tab = self._current_tab()
        return tab["home_page"] if tab else None

    def _tab_index_of(self, key, obj):
        """Resolves a tab's CURRENT position by identity (its pill/view/
        page/home_page, whichever never gets recreated) rather than trusting
        a numeric index captured once at tab-creation time. Every per-tab
        signal below used to close over that fixed "index" via a default
        lambda arg -- looked safe (default args do capture a snapshot, not
        a live reference), but _close_tab() does self._tabs.pop(index),
        which shifts every later tab's real position in the list. Their
        callbacks kept firing with the stale pre-close index, closing or
        switching to the wrong tab (or silently no-op'ing when the stale
        index no longer existed) -- reported as tabs "not closing or adding
        properly". Returns -1 if the tab's already gone (e.g. a signal from
        a view mid-teardown after its own close), which every caller below
        already treats as a safe no-op via its own bounds check."""
        for i, tab in enumerate(self._tabs):
            if tab.get(key) is obj:
                return i
        return -1

    def _create_tab(self, url=None, activate=True, incognito=False):
        index = len(self._tabs)

        profile = self._incognito_profile if incognito else self.profile
        view = QWebEngineView(self)
        page = QWebEnginePage(profile, view)
        view.setPage(page)

        channel = QWebChannel(page)
        channel.registerObject("bridge", self._bridge)
        page.setWebChannel(channel)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        # A tab opened in the background must not start playing. Chromium keeps
        # background tabs fully alive -- it does not pause them for us -- so a
        # link middle-clicked into a new tab would sit there playing audio out
        # of a tab nobody had looked at yet. Requiring a user gesture before
        # playback is Chromium's own mechanism for exactly this; the gate is
        # lifted the first time the tab is actually opened (_switch_to_tab), so
        # once you are looking at it the tab behaves like any other.
        if not activate:
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        page.fullScreenRequested.connect(self._on_fullscreen_requested)
        # Middle-click (and Ctrl+click) on a link, or a page's own
        # target="_blank"/window.open() -- Chromium's renderer already
        # recognizes all of these as "open elsewhere", QtWebEngine just
        # surfaces them here instead of silently doing nothing, which is
        # what happens without a handler connected at all.
        page.newWindowRequested.connect(self._on_new_window_requested)

        # Right-click "Download this video/image" -- the floating overlay
        # button covers the common case (download the page's own video),
        # but it can end up hidden behind a site's own on-page controls in
        # that same corner; a right-click option is the standard second way
        # a real browser offers this.
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda pos, v=view: self._show_web_context_menu(pos, v))

        bg = QColor(24, 24, 27) if self._dark_mode() else QColor(255, 255, 255)
        page.setBackgroundColor(bg)

        view.urlChanged.connect(
            lambda qurl, v=view: self._on_url_changed(self._tab_index_of("view", v), qurl))
        view.loadStarted.connect(
            lambda v=view: self._on_load_started(self._tab_index_of("view", v)))
        view.loadFinished.connect(
            lambda ok, v=view: self._on_load_finished(self._tab_index_of("view", v), ok))
        page.titleChanged.connect(
            lambda title, p=page: self._on_title_changed(self._tab_index_of("page", p), title))
        # Mute tab -- Chrome-parity. recentlyAudibleChanged shows/hides the
        # tab's speaker icon (only while sound is actually playing);
        # audioMutedChanged keeps the icon correct if muted state changes
        # any other way than this button (there isn't one yet, but a page
        # calling document.mute() territory isn't a thing -- this is just
        # defensive symmetry with how the maximize button etc. stay in
        # sync with state changes from outside this app's own controls).
        page.recentlyAudibleChanged.connect(
            lambda audible, p=page: self._on_audible_changed(self._tab_index_of("page", p), audible))
        page.audioMutedChanged.connect(
            lambda muted, p=page: self._on_muted_changed(self._tab_index_of("page", p), muted))

        home_page = BrowserHomePage(self.settings, self)
        home_page.navigate_requested.connect(
            lambda text, hp=home_page: self._navigate_to(text, self._tab_index_of("home_page", hp)))
        # The accent toggle changes a single Browser-tab-wide setting, not
        # just this one tab's home page -- re-run the whole apply_theme(),
        # which already cascades to every open tab's toolbar/pills/home page.
        home_page.accent_changed.connect(self.apply_theme)

        inner_stack = QStackedWidget()
        inner_stack.addWidget(home_page)
        inner_stack.addWidget(view)
        self._tabs_stack.addWidget(inner_stack)

        pill = _TabPill()
        pill.clicked.connect(lambda pl=pill: self._switch_to_tab(self._tab_index_of("pill", pl)))
        pill.close_requested.connect(lambda pl=pill: self._close_tab(self._tab_index_of("pill", pl)))
        pill.mute_toggled.connect(lambda pl=pill: self._toggle_tab_mute(self._tab_index_of("pill", pl)))
        # Inserted right before new_tab_btn's own current position (not a
        # fixed index) -- each new pill lands after every existing one but
        # still ahead of the "+" button, so tabs grow left-to-right with
        # "+" immediately following the last one, Chrome's own placement
        # (it used to sit to the left of every tab instead).
        self._tabstrip_layout.insertWidget(
            self._tabstrip_layout.indexOf(self.new_tab_btn), pill, 1)
        if hasattr(self, "_theme_tokens"):
            pill.apply_theme(self._theme_tokens)

        self._tabs.append({
            "view": view, "page": page, "home_page": home_page,
            "stack": inner_stack, "pill": pill, "incognito": incognito,
            # Back/Forward's own history -- separate from the QWebEngineView's
            # built-in one, which has no concept of "the home page" at all
            # (switching to it is a plain widget-stack swap, not a real
            # navigation the view ever sees), so relying on view.history()
            # alone meant Back stopped working the moment a tab's history
            # was just one real page deep with no way back to Home. "home"
            # or a URL string, walked by nav_index; nav_stepping suppresses
            # a duplicate push while a Back/Forward click is itself driving
            # a URL load (its own urlChanged would otherwise re-push it).
            "nav_stack": ["home"], "nav_index": 0, "nav_stepping": False,
            # True while this tab has never been looked at and its media is
            # therefore held behind the user-gesture requirement above.
            "playback_gated": not activate,
        })
        if incognito:
            pill.set_incognito(True)

        if url:
            inner_stack.setCurrentWidget(view)
            view.load(QUrl(url))
        else:
            inner_stack.setCurrentWidget(home_page)
            pill.show_home_state()

        if activate:
            self._switch_to_tab(index)
        return index

    def _switch_to_tab(self, index):
        if not (0 <= index < len(self._tabs)):
            return
        self._current_index = index
        self._tabs_stack.setCurrentWidget(self._tabs[index]["stack"])
        for i, tab in enumerate(self._tabs):
            tab["pill"].set_checked(i == index)
        tab = self._tabs[index]
        if tab["stack"].currentWidget() is tab["home_page"]:
            self.address_bar.clear()
        else:
            view = self._active_view()
            if view is not None:
                self.address_bar.setText(view.url().toString())
        if tab.get("playback_gated"):
            # First time this tab has actually been opened: hand it back the
            # normal autoplay behaviour it would have had if it had been
            # opened in the foreground to begin with.
            tab["playback_gated"] = False
            tab["page"].settings().setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self._update_back_forward_buttons(index)
        self._update_bookmark_button()

    def _close_tab(self, index):
        if not (0 <= index < len(self._tabs)):
            return
        if len(self._tabs) == 1:
            # This app always keeps at least one Browser tab open (there's
            # no "no tab" state alongside the Video/Torrent/etc tabs), but
            # silently refusing to do anything when the user closes their
            # only tab read as a bug ("tab doesn't close"). Open a genuinely
            # fresh tab in its place -- the exact same path "New Tab" itself
            # uses, so it's correctly initialized -- then fall through and
            # remove the old one below exactly like any other close.
            self._create_tab(activate=True)
            index = 0
        tab = self._tabs.pop(index)
        url = tab["view"].url().toString()
        if url and url != "about:blank":
            self._closed_urls.append(url)
        self._tabs_stack.removeWidget(tab["stack"])
        self._tabstrip_layout.removeWidget(tab["pill"])
        tab["stack"].deleteLater()
        tab["pill"].deleteLater()

        if self._current_index >= len(self._tabs):
            self._current_index = len(self._tabs) - 1
        elif self._current_index > index:
            self._current_index -= 1
        self._switch_to_tab(self._current_index)

    def _reopen_closed_tab(self):
        if self._closed_urls:
            self._create_tab(url=self._closed_urls.pop(), activate=True)

    # ---------------------------------------------------------- Actions ----
    def _show_home_page(self):
        tab = self._current_tab()
        if tab:
            tab["stack"].setCurrentWidget(tab["home_page"])
            tab["pill"].show_home_state()
            self.address_bar.clear()
            self._push_nav_entry(self._current_index, "home")

    def _navigate_to(self, raw_text, tab_index=None):
        """Single entry point for 'go somewhere in the real browser' --
        used by the address bar, the home page's search bar, and its
        shortcut tiles alike, so normalization and the home-page-to-view
        switch only need to happen in one place."""
        target = _normalize_address(raw_text)
        if not target:
            return
        index = tab_index if tab_index is not None else self._current_index
        if not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        tab["stack"].setCurrentWidget(tab["view"])
        tab["view"].load(QUrl(target))
        # Swapping the home page out for the real QWebEngineView is exactly
        # the moment Chromium's own compositor surface (a real native child
        # HWND, not something Qt's raster backing store draws) goes from
        # dormant to live -- reported directly as the window's frosted
        # background vanishing to a flat/washed-out fill right as a
        # shortcut or search result loads, fixable only by toggling the
        # theme twice. Same underlying Acrylic-blur-behind desync this
        # project has hit before at other trigger points (DPI change,
        # every window-state change); this is one more of those points,
        # not a new problem. A short delay so it runs after the view has
        # actually started painting, not synchronously with this call.
        win = self.window()
        if hasattr(win, "_on_dpi_or_scale_changed"):
            QTimer.singleShot(150, win._on_dpi_or_scale_changed)

    def _navigate_from_address_bar(self):
        self._navigate_to(self.address_bar.text())

    def _show_find_bar(self):
        # Positioned relative to this widget's own top-right corner (just
        # under the toolbar), recomputed on every open since the window
        # may have moved or resized since the last time.
        margin = 14
        top_right = self.mapToGlobal(QPoint(self.width(), self.toolbar_frame.geometry().bottom()))
        self.find_frame.move(top_right.x() - self.find_frame.width() - margin, top_right.y() + margin)
        self.find_frame.show()
        self.find_input.setFocus()
        self.find_input.selectAll()

    def _on_escape_pressed(self):
        """Hides the find bar (the shortcut's original job) and, as a
        guaranteed fallback, also tells the page to exit fullscreen if it's
        in it. Chromium normally handles Escape-to-exit-fullscreen
        entirely on its own, but reported directly: stuck in fullscreen
        video with no way out except Task Manager -- something about the
        embedded QtWebEngine setup isn't reliably delivering that key to
        Chromium's own fullscreen controller. This shortcut is bound at
        the window level (Qt's default WindowShortcut context, active
        whenever this tab's top-level window is the active one), so it
        fires regardless of which specific child widget currently holds
        keyboard focus -- it doesn't depend on the fix it's a fallback for."""
        self._hide_find_bar()
        view = self._active_view()
        if view is not None:
            view.page().runJavaScript(
                "if (document.fullscreenElement) { document.exitFullscreen(); }")
        # Second, independent layer: force the window's own chrome back
        # directly, in case the page's fullscreen state already flipped
        # (so the JS call above is a no-op) while the window itself never
        # actually got restored -- covers the window staying stuck even
        # when the page/signal side is already consistent.
        win = self.window()
        if hasattr(win, "isFullScreen") and win.isFullScreen() and hasattr(win, "set_video_fullscreen"):
            self.toolbar_frame.setVisible(True)
            self.tabstrip_frame.setVisible(True)
            win.set_video_fullscreen(False)

    def _hide_find_bar(self):
        # Unconditional -- hiding an already-hidden widget and clearing an
        # empty search are both harmless, and an isVisible() guard here is
        # unreliable besides: it reflects whether the whole window is
        # actually on-screen, not just this widget's own shown/hidden state.
        self.find_frame.setVisible(False)
        page = self.page
        if page:
            page.findText("")  # clears the highlight

    def _find_in_page(self, text, backward=False):
        page = self.page
        if not page:
            return
        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
        page.findText(text, flags)

    def _show_zoom_menu(self):
        """A small floating slider, not a menu of preset steps -- click
        the zoom button, drag to any zoom level continuously. Qt.Popup
        gives it the same "closes on an outside click" behaviour a QMenu
        has, without actually being one."""
        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        popup = QWidget(self, Qt.WindowType.Popup)
        popup.setStyleSheet(f"""
            QWidget {{ background: {t['card_bg_solid']}; border: 1px solid {t['card_border']}; border-radius: 10px; }}
        """)
        layout = QHBoxLayout(popup)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        minus_btn = QPushButton("−")
        plus_btn = QPushButton("+")
        for b in (minus_btn, plus_btn):
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: {t['hover_overlay']}; color: {t['text']};
                    border: none; border-radius: 12px; font-weight: 700; }}
                QPushButton:hover {{ background: {t['pressed_overlay']}; }}
            """)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(25)
        slider.setMaximum(300)
        slider.setValue(round(self._zoom_factor * 100))
        slider.setFixedWidth(150)
        slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 4px; background: {t['hover_overlay']}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
            QSlider::handle:horizontal {{
                background: {t['accent']}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
        """)

        pct_label = QLabel(f"{slider.value()}%")
        pct_label.setFixedWidth(38)
        pct_label.setStyleSheet(f"color: {t['text']};")

        def on_change(value):
            self._set_zoom(value / 100)
            pct_label.setText(f"{value}%")

        slider.valueChanged.connect(on_change)
        minus_btn.clicked.connect(lambda: slider.setValue(max(25, slider.value() - 10)))
        plus_btn.clicked.connect(lambda: slider.setValue(min(300, slider.value() + 10)))

        layout.addWidget(minus_btn)
        layout.addWidget(slider)
        layout.addWidget(plus_btn)
        layout.addWidget(pct_label)

        popup.adjustSize()
        anchor = self.zoom_btn.mapToGlobal(self.zoom_btn.rect().bottomRight())
        popup.move(anchor.x() - popup.sizeHint().width(), anchor.y() + 4)
        popup.show()

    def _set_zoom(self, factor):
        self._zoom_factor = max(0.25, min(3.0, round(factor, 2)))
        page = self.page
        if page:
            page.setZoomFactor(self._zoom_factor)

    def _update_reload_icon(self):
        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        self.reload_btn.setIcon(_nav_icon("stop" if self._loading else "reload", t["text"]))

    def _on_reload_clicked(self):
        view = self._active_view()
        if not view:
            return
        if self._loading:
            view.stop()
        else:
            view.reload()

    def _on_url_changed(self, tab_index, qurl):
        if 0 <= tab_index < len(self._tabs):
            self._tabs[tab_index]["pill"].set_url(qurl.toString())
        if tab_index == self._current_index:
            self.address_bar.setText(qurl.toString())
            self._update_bookmark_button()
        url = qurl.toString()
        if url and url != "about:blank":
            self._push_nav_entry(tab_index, url)

    def _push_nav_entry(self, index, entry):
        """Records a real navigation ("home" or a URL) into this tab's own
        Back/Forward list. A no-op while nav_stepping is set -- that means
        this call is the *result* of a Back/Forward click re-loading a URL
        (see _apply_nav_entry), not a fresh navigation to record again."""
        if not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        if tab.get("nav_stepping"):
            tab["nav_stepping"] = False
            self._update_back_forward_buttons(index)
            return
        stack = tab["nav_stack"]
        ni = tab["nav_index"]
        if stack and 0 <= ni < len(stack) and stack[ni] == entry:
            self._update_back_forward_buttons(index)
            return
        del stack[ni + 1:]
        stack.append(entry)
        tab["nav_index"] = len(stack) - 1
        self._update_back_forward_buttons(index)

    def _update_back_forward_buttons(self, index):
        if index != self._current_index or not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        self.back_btn.setEnabled(tab["nav_index"] > 0)
        self.fwd_btn.setEnabled(tab["nav_index"] < len(tab["nav_stack"]) - 1)

    def _apply_nav_entry(self, index):
        tab = self._tabs[index]
        entry = tab["nav_stack"][tab["nav_index"]]
        if entry == "home":
            tab["stack"].setCurrentWidget(tab["home_page"])
            tab["pill"].show_home_state()
            if index == self._current_index:
                self.address_bar.clear()
        else:
            tab["nav_stepping"] = True
            tab["stack"].setCurrentWidget(tab["view"])
            tab["view"].load(QUrl(entry))
        self._update_back_forward_buttons(index)

    def _on_back_clicked(self):
        index = self._current_index
        if not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        if tab["nav_index"] <= 0:
            return
        tab["nav_index"] -= 1
        self._apply_nav_entry(index)

    def _on_forward_clicked(self):
        index = self._current_index
        if not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        if tab["nav_index"] >= len(tab["nav_stack"]) - 1:
            return
        tab["nav_index"] += 1
        self._apply_nav_entry(index)

    def _on_title_changed(self, tab_index, title):
        if 0 <= tab_index < len(self._tabs):
            self._tabs[tab_index]["pill"].set_title(title)

    def _on_audible_changed(self, tab_index, audible):
        if 0 <= tab_index < len(self._tabs):
            self._tabs[tab_index]["pill"].set_audible(audible)

    def _on_muted_changed(self, tab_index, muted):
        if 0 <= tab_index < len(self._tabs):
            self._tabs[tab_index]["pill"].set_muted(muted)

    def _toggle_tab_mute(self, tab_index):
        if 0 <= tab_index < len(self._tabs):
            page = self._tabs[tab_index]["page"]
            page.setAudioMuted(not page.isAudioMuted())

    def _on_load_started(self, tab_index):
        if tab_index == self._current_index:
            self._loading = True
            self._update_reload_icon()

    def _on_load_finished(self, tab_index, ok):
        if not (0 <= tab_index < len(self._tabs)):
            return
        tab = self._tabs[tab_index]
        if tab_index == self._current_index:
            self._loading = False
            self._update_reload_icon()
        url = tab["view"].url().toString()
        if ok and url.startswith("http") and not tab.get("incognito"):
            browser_data.add_history_entry(url, tab["page"].title())
            self._refresh_address_completer()

    def _refresh_address_completer(self):
        urls = set()
        for entry in browser_data.load_history():
            u = entry.get("url")
            if u:
                urls.add(u)
        for entry in browser_data.load_bookmarks():
            u = entry.get("url")
            if u:
                urls.add(u)
        self._address_completer_model.setStringList(sorted(urls))

    def _on_download_requested(self, download: QWebEngineDownloadRequest):
        # Cancel Chromium's own download UI/manager entirely -- the file is
        # instead handed to this tab's own start_direct_download() below,
        # so it shows up as a real progress card in the shared Download
        # tab (no extension, no separate download manager window), and
        # still lands in the shared History tab once it finishes.
        url = download.url().toString()
        suggested_name = download.downloadFileName() or None
        download.cancel()
        self.start_direct_download(url, suggested_name or None)

    def _open_in_system_browser(self, url):
        """Fallback for sites this embedded Chromium can't play video on --
        QtWebEngine ships without H.264/AAC decoding (a licensing exclusion,
        confirmed directly against real PornHub/xHamster playback failures),
        and there's no fixing that from inside this browser. The user's own
        installed default browser has full codec support already, so handing
        off the exact same URL there is a real fix, not a dead end. Reached
        either from the overflow menu (manual, any page) or the injected
        video-error banner (automatic, only when a <video> actually fails)."""
        if url:
            webbrowser.open(url)

    # ------------------------------------------------------ Downloads -----
    def start_direct_download(self, url, suggested_filename=None):
        title = suggested_filename or url
        job_id = self.download_tab.start_job(
            title, "Direct download", None,
            make_on_cancel=lambda jid: (lambda: self._cancel_job(jid)),
            make_on_pause_toggle=lambda jid: (lambda paused: self._toggle_pause(jid, paused)),
            make_on_play=lambda jid: (lambda: self._play_job(jid)),
        )
        pause_event = threading.Event()
        pause_event.set()  # set == not paused
        self._jobs[job_id] = {"cancel": False, "pause_event": pause_event, "path": None, "title": title}
        threading.Thread(
            target=self._direct_download_thread,
            args=(job_id, url, suggested_filename),
            daemon=True,
        ).start()

    def _direct_download_thread(self, job_id, url, suggested_filename):
        hook = lambda d: self._progress_hook(job_id, d)
        try:
            final_path = downloader.download_direct_file(url, self.download_dir, hook, suggested_filename)
            self._download_done_sig.emit(job_id, final_path)
        except Exception as e:
            logger.exception("Direct download failed for %s", url)
            self._download_error_sig.emit(job_id, str(e))

    def _progress_hook(self, job_id, d):
        job = self._jobs.get(job_id)
        if job is None or job["cancel"]:
            raise downloader.DownloadCancelled("Cancelled by user")
        # Segmented direct downloads call this from several worker threads
        # at once (see download_direct_file) -- Event.wait()/the dict
        # lookups above are all thread-safe, so pausing here genuinely
        # stalls every one of them together, not just the reporting thread.
        job["pause_event"].wait()
        if job["cancel"]:
            raise downloader.DownloadCancelled("Cancelled by user")

        path = d.get("filename")
        if path and path != job.get("path") and os.path.exists(path):
            job["path"] = path
            self._playable_sig.emit(job_id)

        if d.get("status") == "downloading":
            total = d.get("total_bytes")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else 0
            parts = []
            if total:
                parts.append(f"{formatting.humanize_size(downloaded)} / {formatting.humanize_size(total)}")
            if speed:
                parts.append(f"{formatting.humanize_size(speed)}/s")
            eta_str = formatting.format_eta(eta) if eta is not None else None
            if eta_str:
                parts.append(f"ETA {eta_str}")
            self._progress_sig.emit(job_id, pct, "  •  ".join(parts) if parts else "Downloading...")

    def _update_progress(self, job_id, pct, label):
        if job_id in self._jobs:
            self.download_tab.update_progress(job_id, pct, label)

    def _on_download_done(self, job_id, final_path):
        job = self._jobs.get(job_id)
        if job is None:
            return
        # Set path/playable *before* mark_done()/job_finished, not after --
        # anything reacting to "this job is done" should already see the
        # correct final path, not a stale one (real bug, caught via a direct
        # end-to-end test: video_tab.py's audio path updated job["path"]
        # after mark_done() and a job_finished listener observed the old,
        # already-deleted intermediate file instead of the real one).
        if final_path and os.path.exists(final_path):
            job["path"] = final_path
            self.download_tab.set_playable(job_id, True)
            try:
                size_bytes = os.path.getsize(final_path)
            except OSError:
                size_bytes = 0
            download_history.add_entry(
                "file", job["title"] or os.path.basename(final_path),
                final_path, self.download_dir, size_bytes,
            )
        self.download_tab.mark_done(job_id, "✓ Completed")
        # Not popped -- see the _jobs comment in __init__: the card's Play
        # button stays live for a few seconds after completion and still
        # needs "path" to resolve.

    def _on_download_error(self, job_id, err):
        job = self._jobs.pop(job_id, None)
        if job is None:
            return
        if job["cancel"]:
            self.download_tab.mark_cancelled(job_id)
            return
        self.download_tab.mark_failed(
            job_id, f"Failed: {err.splitlines()[0][:120]}" if err else "Failed.")
        logger.error("Direct download job %s failed: %s", job_id, err)

    def _cancel_job(self, job_id):
        job = self._jobs.get(job_id)
        if job:
            job["cancel"] = True
            job["pause_event"].set()  # wake a paused thread so it reaches the cancel check

    def _toggle_pause(self, job_id, paused):
        job = self._jobs.get(job_id)
        if not job:
            return
        if paused:
            job["pause_event"].clear()
        else:
            job["pause_event"].set()

    def _play_job(self, job_id):
        job = self._jobs.get(job_id)
        path = job.get("path") if job else None
        if path and os.path.exists(path):
            os.startfile(path)

    def _on_fullscreen_requested(self, request):
        request.accept()
        is_fullscreen = request.toggleOn()
        self.toolbar_frame.setVisible(not is_fullscreen)
        self.tabstrip_frame.setVisible(not is_fullscreen)
        self.fullscreen_requested.emit(is_fullscreen)

    def _on_new_window_requested(self, request):
        # Middle-click/Ctrl+click background-opens without stealing focus
        # from the page you were reading -- exactly Chrome's own behaviour.
        # Everything else (target="_blank", window.open(), a genuine
        # new-window request) activates the new tab immediately, since
        # those represent explicit intent to go look at it now.
        background = request.destination() == QWebEngineNewWindowRequest.DestinationType.InNewBackgroundTab
        new_index = self._create_tab(activate=not background)
        tab = self._tabs[new_index]
        # No url= passed to _create_tab above -- openIn() below is what
        # actually starts the navigation (it hands the in-flight request,
        # referrer/POST-data and all, straight to the target page). Passing
        # a URL there too would have fired a second, separate load.
        tab["stack"].setCurrentWidget(tab["view"])
        request.openIn(tab["page"])

    # -------------------------------------------------- Bookmarks/History --
    def _current_url(self):
        view = self._active_view()
        return view.url().toString() if view else ""

    def _update_bookmark_button(self):
        bookmarked = browser_data.is_bookmarked(browser_data.load_bookmarks(), self._current_url())
        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        color = t["accent"] if bookmarked else t["text_muted"]
        self._bookmark_action.setIcon(_star_icon(bookmarked, color))
        self._bookmark_action.setToolTip("Edit bookmark" if bookmarked else "Bookmark this page")

    def _show_bookmark_dialog(self):
        """Chrome's own flow: clicking the star adds the bookmark
        immediately, then opens a small popup to rename or remove it --
        not a plain on/off toggle with no feedback."""
        url = self._current_url()
        if not url or url == "about:blank":
            return
        page = self.page
        bookmarks = browser_data.load_bookmarks()
        existing = next((b for b in bookmarks if b.get("url") == url), None)
        if existing is None:
            browser_data.add_bookmark(url, (page.title() if page else "") or url)
            self._update_bookmark_button()
            self._refresh_address_completer()
            existing = {"url": url, "title": (page.title() if page else "") or url}

        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        dlg = QDialog(self)
        dlg.setWindowTitle("Bookmark added")
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"QDialog {{ background: {t['card_bg_solid']}; color: {t['text']}; }}")
        layout = QVBoxLayout(dlg)
        name_label = QLabel("Name")
        name_label.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")
        layout.addWidget(name_label)
        name_edit = QLineEdit(existing.get("title", url))
        name_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {'#1c1c1e' if self._dark_mode() else '#ffffff'};
                color: {t['text']}; border: 1px solid {t['card_border']};
                border-radius: 6px; padding: 6px 8px;
            }}
        """)
        layout.addWidget(name_edit)

        btn_row = QHBoxLayout()
        remove_btn = QPushButton("Remove")
        done_btn = QPushButton("Done")
        done_btn.setObjectName("accent")
        for b in (remove_btn, done_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ border-radius: 6px; padding: 6px 14px;
                    background: {t['hover_overlay']}; color: {t['text']}; border: none; }}
                QPushButton:hover {{ background: {t['pressed_overlay']}; }}
            """)
        done_btn.setStyleSheet(f"""
            QPushButton {{ border-radius: 6px; padding: 6px 14px;
                background: {t['accent']}; color: {t['accent_text']}; border: none; font-weight: 600; }}
            QPushButton:hover {{ background: {t['accent_hover']}; }}
        """)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(done_btn)
        layout.addLayout(btn_row)

        def _save_and_close():
            browser_data.remove_bookmark(url)
            browser_data.add_bookmark(url, name_edit.text().strip() or url)
            self._update_bookmark_button()
            self._refresh_address_completer()
            dlg.accept()

        def _remove_and_close():
            browser_data.remove_bookmark(url)
            self._update_bookmark_button()
            self._refresh_address_completer()
            dlg.accept()

        done_btn.clicked.connect(_save_and_close)
        remove_btn.clicked.connect(_remove_and_close)
        dlg.exec()

    def _show_web_context_menu(self, pos, view):
        """Right-click on the page itself. Video specifically routes through
        the *page* URL (open_in_video_tab -> the Video tab's own yt-dlp
        fetch), not the raw mediaUrl() -- confirmed necessary: sites like
        YouTube stream video via MediaSource Extensions, so mediaUrl() for
        a right-clicked <video> is often a blob: URL that isn't independently
        downloadable, while the existing overlay-button pipeline (page URL
        in, yt-dlp handles real extraction) already works reliably. Images
        do have a real, direct URL, so those go straight to a direct
        download instead."""
        req = view.lastContextMenuRequest()
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        media_type = req.mediaType()
        added_download_action = False
        if media_type == QWebEngineContextMenuRequest.MediaType.MediaTypeVideo:
            action = menu.addAction("Download this video with Awesome Downloader")
            action.triggered.connect(lambda: self.open_in_video_tab.emit(view.url().toString()))
            added_download_action = True
        elif media_type == QWebEngineContextMenuRequest.MediaType.MediaTypeImage and not req.mediaUrl().isEmpty():
            action = menu.addAction("Download this image")
            action.triggered.connect(
                lambda u=req.mediaUrl().toString(): self.start_direct_download(u))
            added_download_action = True

        if added_download_action:
            menu.addSeparator()

        # The rest of Chromium's own default menu (Back/Forward/Reload,
        # Copy, Inspect, ...) still underneath -- this adds a download
        # option on top of the normal menu, not instead of it.
        standard = view.createStandardContextMenu()
        for standard_action in standard.actions():
            menu.addAction(standard_action)

        menu.exec(view.mapToGlobal(pos))

    def _show_new_tab_menu(self, pos):
        """Right-click on the "+" button -- the explicit, discoverable way
        to open a private tab, for anyone who doesn't already know the
        Ctrl+Shift+N shortcut."""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        new_tab_action = menu.addAction("New Tab")
        new_tab_action.triggered.connect(lambda: self._create_tab(activate=True))
        private_action = menu.addAction("New Private Tab")
        private_action.triggered.connect(lambda: self._create_tab(activate=True, incognito=True))
        menu.exec(self.new_tab_btn.mapToGlobal(pos))

    def _show_overflow_menu(self):
        """The "⋮" menu -- Bookmarks and History as cascading submenus,
        plus a per-site ad-block toggle, Chrome/uBlock-style."""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # Manual escape hatch for the sites this embedded Chromium can't play
        # video on (no H.264/AAC decoder, a licensing exclusion) -- always
        # available here rather than only appearing after a video visibly
        # fails, since not every failure trips the auto-detect banner
        # (silent black-screen players that never fire a real 'error' event).
        open_system_action = menu.addAction("Open this page in your default browser")
        open_system_action.triggered.connect(
            lambda: self._open_in_system_browser(self._current_url()))
        menu.addSeparator()

        host = QUrl(self._current_url()).host()
        if host:
            disabled_hosts = browser_data.load_adblock_disabled_hosts()
            is_disabled = host in disabled_hosts
            adblock_action = menu.addAction(
                f"{'Enable' if is_disabled else 'Disable'} ad-block on {host}")
            adblock_action.triggered.connect(lambda: self._toggle_adblock_for_current_site(host, is_disabled))

        refresh_label = "Refreshing ad-block list..." if self._adblock_refreshing else "Refresh ad-block list"
        refresh_action = menu.addAction(refresh_label)
        refresh_action.setEnabled(not self._adblock_refreshing)
        refresh_action.triggered.connect(self._refresh_adblock_list)
        menu.addSeparator()

        bookmarks_menu = menu.addMenu("★  Bookmarks")
        bookmarks_menu.setStyleSheet(self._menu_stylesheet())
        bookmarks = browser_data.load_bookmarks()
        if not bookmarks:
            empty = bookmarks_menu.addAction("No bookmarks yet")
            empty.setEnabled(False)
        else:
            for b in bookmarks:
                title = b.get("title") or b.get("url", "")
                action = bookmarks_menu.addAction(title[:60])
                action.setToolTip(b.get("url", ""))
                action.triggered.connect(lambda _c=False, u=b.get("url"): self._navigate_to(u))

        history_menu = menu.addMenu("🕘  History")
        history_menu.setStyleSheet(self._menu_stylesheet())
        entries = browser_data.load_history()
        if not entries:
            empty = history_menu.addAction("No history yet")
            empty.setEnabled(False)
        else:
            for e in entries[:25]:
                title = e.get("title") or e.get("url", "")
                action = history_menu.addAction(title[:60])
                action.setToolTip(e.get("url", ""))
                action.triggered.connect(lambda _c=False, u=e.get("url"): self._navigate_to(u))
            history_menu.addSeparator()
            clear_action = history_menu.addAction("Clear history")
            clear_action.triggered.connect(browser_data.clear_history)
            clear_action.triggered.connect(self._refresh_address_completer)

        # bottomRight, not bottomLeft: the button sits at the toolbar's right
        # edge now, so a left-anchored popup would try to open mostly off
        # the window -- anchoring to its right edge keeps the menu on-screen
        # and matches where a real browser opens this same menu from.
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomRight() - QPoint(menu.sizeHint().width(), 0)))

    def _toggle_adblock_for_current_site(self, host, currently_disabled):
        browser_data.set_adblock_disabled(host, not currently_disabled)
        view = self._active_view()
        if view:
            view.reload()  # the new rule only affects requests made after this point

    def _refresh_adblock_list(self):
        """Pulls a fresh, actively maintained ad/tracker domain list (see
        adblock_updater.py) to supplement the bundled snapshot, which was
        confirmed to have real gaps (doubleclick.net, googlesyndication.com,
        facebook.net and others were missing entirely). Lives here in the
        Browser tab's own menu, not the app-wide Updates dialog -- this is
        specifically a Browser-tab concern, same reasoning as the per-site
        ad-block toggle right above it."""
        if self._adblock_refreshing:
            return
        self._adblock_refreshing = True
        threading.Thread(target=self._adblock_refresh_thread, daemon=True).start()

    def _adblock_refresh_thread(self):
        try:
            count = adblock_updater.refresh_domain_list()
            self._adblock_refresh_sig.emit(True, str(count))
        except Exception as e:
            logger.exception("Ad-block list refresh failed")
            self._adblock_refresh_sig.emit(False, str(e))

    def _on_adblock_refresh_done(self, ok, result):
        self._adblock_refreshing = False
        if ok:
            QMessageBox.information(
                self, config.APP_NAME,
                f"Ad-block list refreshed ({int(result):,} domains). "
                f"Restart the app for it to take effect."
            )
        else:
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't refresh the ad-block list:\n{result}")

    # ----------------------------------------------------------- Theme -----
    def _dark_mode(self):
        return (self.settings or {}).get("theme", "dark") != "light"

    def _browser_accent(self):
        return browser_data.get_browser_accent()

    def apply_theme(self):
        """Called at startup and again by MainWindow after a live theme
        toggle -- matches the app's own dark/light window theme, per spec."""
        t = theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        self._theme_tokens = t
        # One continuous rounded card spanning both rows -- the tab strip's
        # top corners and the toolbar's bottom corners share the same
        # radius and border, meeting with no gap between them, the way the
        # address bar's own pill reads as one seamless shape rather than
        # two separate floating cards stacked with a visible seam.
        self.toolbar_frame.setStyleSheet(f"""
            QFrame#browserToolbar {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {t['card_border']};
            }}
        """)
        # Still a distinct, slightly darker fill than the toolbar/active-tab
        # so inactive tabs (transparent) visibly recede against it while
        # the active tab's toolbar-matched background pops forward -- only
        # the outer shape (rounded top corners, matching border, no gap)
        # merges with the toolbar below, not the fill color.
        self.tabstrip_frame.setStyleSheet(f"""
            QFrame#browserTabstrip {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {t['divider']};
            }}
        """)
        self.find_frame.setStyleSheet(f"""
            QFrame#browserFindBar {{
                background: {t['card_bg_solid']};
                border: 1px solid {t['card_border']};
                border-radius: 8px;
            }}
        """)
        button_style = f"""
            QPushButton {{
                background: transparent;
                color: {t['text']};
                border: 1px solid {t['card_border']};
                border-radius: 12px;
                font-size: 14px;
            }}
            QPushButton:hover {{ background: {t['hover_overlay']}; }}
            QPushButton:pressed {{ background: {t['pressed_overlay']}; }}
            QPushButton:disabled {{ color: {t['text_muted']}; border-color: transparent; }}
        """
        for btn in (self.zoom_btn, self.menu_btn, self.back_btn, self.fwd_btn, self.reload_btn, self.home_btn):
            btn.setStyleSheet(button_style)
        icon_color = t["text"]
        self.zoom_btn.setIcon(_nav_icon("zoom", icon_color))
        self.menu_btn.setIcon(_nav_icon("menu", icon_color))
        self.back_btn.setIcon(_nav_icon("back", icon_color))
        self.fwd_btn.setIcon(_nav_icon("forward", icon_color))
        self.home_btn.setIcon(_nav_icon("home", icon_color))
        self.new_tab_btn.setIcon(_nav_icon("plus", icon_color))
        self.new_tab_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {t['text']}; border: none; border-radius: 15px; }}
            QPushButton:hover {{ background: {t['hover_overlay']}; }}
        """)
        self._update_reload_icon()
        for btn in (self.find_prev_btn, self.find_next_btn, self.find_close_btn):
            btn.setStyleSheet(button_style)
        self.find_prev_btn.setIcon(_nav_icon("back", icon_color, size=11))
        self.find_next_btn.setIcon(_nav_icon("forward", icon_color, size=11))
        self.find_close_btn.setIcon(_nav_icon("stop", icon_color, size=11))
        self.find_input.setStyleSheet(f"""
            QLineEdit {{ background: {'#1c1c1e' if self._dark_mode() else '#ffffff'};
                color: {t['text']}; border: 1px solid {t['card_border']}; border-radius: 6px; padding: 0 8px; }}
        """)
        for tab in self._tabs:
            tab["pill"].apply_theme(t)
            bg = QColor(24, 24, 27) if self._dark_mode() else QColor(255, 255, 255)
            tab["page"].setBackgroundColor(bg)
            tab["home_page"].apply_theme()
        self.address_bar.setStyleSheet(f"""
            QLineEdit {{
                background: {'#1c1c1e' if self._dark_mode() else '#ffffff'};
                color: {t['text']};
                border: 1px solid {t['card_border']};
                border-radius: 16px;
                padding: 0 12px;
            }}
        """)
        if hasattr(self, "_bookmark_action"):
            self._update_bookmark_button()

    def _menu_stylesheet(self):
        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(accent=self._browser_accent(), dark_mode=self._dark_mode())
        return f"""
            QMenu {{
                background: {t['card_bg_solid']};
                color: {t['text']};
                border: 1px solid {t['card_border']};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {t['hover_overlay']}; }}
            QMenu::item:disabled {{ color: {t['text_muted']}; }}
            QMenu::separator {{ height: 1px; background: {t['divider']}; margin: 4px 8px; }}
        """
