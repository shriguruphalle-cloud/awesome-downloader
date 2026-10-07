"""Browser tab: a real browser inside the app, running on Microsoft Edge
WebView2 (see webview2.py for why it moved off Qt WebEngine: H.264).

What it does beyond showing pages:
  * the toolbar's ember Download button, and the floating one a page with a
    video gets, send the page to the Video tab's downloader;
  * files a page downloads are the engine's own downloads (so they carry the
    page's session), shown as cards in the Download tab with pause, resume
    and cancel; a .torrent offers itself to the Torrent tab;
  * magnet links go straight to the Torrent tab;
  * AdGuard runs inside it (browser_engine.py), with its blocked count on
    the shield and a panel for "allow this site" / "pause everywhere";
  * whatever plays in any tab shows in a Now Playing control;
  * tabs left in the background for ten minutes go to sleep, the tabs that
    were open come back next launch, and private tabs keep nothing.

Engine start is lazy: nothing heavier than this widget exists until the
Browser tab is first shown (or a download asks for its sign-in cookies).
"""
import ctypes
import os
import re
import time
import urllib.parse
import json
import html
import webbrowser
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QCompleter, QHBoxLayout, QLabel, QMenu, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget, QWidgetAction,
)

from app import config
from app.logging_setup import get_logger
from app.core import safe_browsing
from app.utils import browser_data, download_history, formatting, settings as settings_store

from . import browser_engine, browser_scripts, motion, palettes, theme, webview2
from .browser_chrome import (
    ActionButton, AddressBar, AdGuardPanel, BookmarkPopup, BookmarksBar, BookmarksPanel, ChromeButton,
    EngineMissing, LoadBar, NowPlaying, TabPill, TabStrip, favicons, icon, style_menu, UpdateBar,
)
from .browser_home import HomeView
from .widgets.toast import show_toast

logger = get_logger("browser_tab")

WEBVIEW2_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"
_SCHEME_RE = re.compile(r"^(https?|file|about|chrome-extension|edge|view-source):", re.I)
_HOST_RE = re.compile(r"^[^\s/]+\.[^\s/.]{2,}(:\d+)?(/\S*)?$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/\S*)?$")

VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
VK_TAB, VK_ESCAPE, VK_PRIOR, VK_NEXT, VK_HOME = 0x09, 0x1B, 0x21, 0x22, 0x24
VK_F4, VK_F6, VK_F11 = 0x73, 0x75, 0x7A


def normalize_address(text):
    """Address-bar text -> a URL: anything with a scheme as it is, a bare
    host (has a dot, no spaces) as https, localhost/IPs as http, anything
    else as a search with the chosen engine."""
    text = (text or "").strip()
    if not text:
        return None
    if _SCHEME_RE.match(text):
        return text
    if text.lower().startswith("localhost") or _IP_RE.match(text):
        return "http://" + text
    if _HOST_RE.match(text):
        return "https://" + text
    return browser_data.search_url(text)


def bookmark_address(text):
    """What's typed in a bookmark's URL box, as an address -- "example.com"
    becomes https://example.com -- or None if it isn't one (no searches:
    a bookmark points at a page)."""
    url = normalize_address(text)
    if url is None or url == browser_data.search_url((text or "").strip()):
        return None
    return url


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _title_for(url):
    host = _host(url)
    return host[4:] if host.startswith("www.") else (host or url)


def _key_down(vk):
    try:
        return bool(ctypes.windll.user32.GetKeyState(vk) & 0x8000)
    except Exception:   # noqa: BLE001
        return False


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


_blocklist = None


def private_blocklist():
    """Ad and tracker domains for private tabs, where AdGuard can't run
    (extensions are off in private mode): the list the Browser tab used
    before AdGuard."""
    global _blocklist
    if _blocklist is None:
        domains = set()
        paths = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_assets", "adblock_domains.txt")]
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    domains.update(line.strip().lower() for line in f if line.strip())
            except OSError:
                pass
        _blocklist = domains
    return _blocklist


def blocked_host(host, domains):
    """True if `host` or any parent domain of it is listed."""
    labels = (host or "").lower().split(".")
    return any(".".join(labels[i:]) in domains for i in range(len(labels) - 1))


def _short_count(n):
    return f"{n / 1000:.0f}k" if n >= 10000 else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))



# What a stopped page shows instead. Built with plain markup (it's our own
# string, shown with NavigateToString), and it talks back with the same
# messages as the page scripts.
_BLOCKED_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Blocked: %(host)s</title>
<style>
  html, body { margin: 0; height: 100%%; background: #0b0f1c; color: #eaf2ff;
    font: 15px/1.6 "Segoe UI Variable", "Segoe UI", system-ui, sans-serif; }
  body { display: grid; place-items: center; }
  .card { width: min(560px, calc(100vw - 48px)); padding: 32px 34px; border-radius: 22px;
    background: rgba(255, 107, 107, .07); border: 1px solid rgba(255, 107, 107, .35); }
  .k { font: 600 12px/1 "Segoe UI", sans-serif; letter-spacing: .14em; text-transform: uppercase; color: #ff8b8b; }
  h1 { font-size: 26px; line-height: 1.2; margin: 12px 0 10px; }
  p { color: rgba(234, 242, 255, .78); margin: 0 0 14px; }
  .host { font-family: Consolas, monospace; color: #fff; }
  .row { display: flex; gap: 12px; align-items: center; margin-top: 22px; flex-wrap: wrap; }
  button { font: 600 14px "Segoe UI", sans-serif; border-radius: 999px; padding: 11px 22px; cursor: pointer; }
  .back { background: #38bdf8; color: #04121f; border: 0; }
  .risk { background: none; border: 0; color: rgba(234, 242, 255, .55); text-decoration: underline; padding: 11px 4px; }
  small { display: block; margin-top: 18px; color: rgba(234, 242, 255, .45); font-size: 12px; }
</style></head><body><div class="card">
  <div class="k">Dangerous site stopped</div>
  <h1>This site may harm you</h1>
  <p><span class="host">%(host)s</span> is on an open list of known dangerous sites: it's listed as %(what)s.</p>
  <p>Nothing from it has loaded. If you got here from an email or a message, close it.</p>
  <div class="row">
    <button class="back" id="back" autofocus>Go back to safety</button>
    <button class="risk" id="risk">I understand the risk -- continue anyway</button>
  </div>
  <small>Listed by %(list)s. Lists only know what's been reported, so a site that isn't
  stopped isn't necessarily safe.</small>
</div>
<script>
  function post(m) { try { chrome.webview.postMessage(m); } catch (e) {} }
  document.getElementById('back').onclick = function () { post({type: 'sb-back'}); };
  document.getElementById('risk').onclick = function () { post({type: 'sb-continue', url: %(url)s}); };
</script></body></html>"""

class _Tab:
    """One browser tab. Its page (a WebView2Widget) is created the first
    time the tab is actually shown -- a tab opened in the background, or
    restored from last session, costs nothing until then."""

    def __init__(self, pill, private):
        self.pill = pill
        self.private = private
        self.view = None
        self.on_home = True
        self.page_ahead = False      # Back from a tab's first page went home
        self.url = ""
        self.title = "New Tab"
        self.icon = None
        self.loading = False
        self.progress = 0
        self.audible = False
        self.muted = False
        self.pending_url = None
        self.zoom = 1.0
        self.media = None
        self.media_at = 0.0
        self.has_video = False
        self.pip = False
        self.adguard = None
        self.opener = None
        self.hidden_since = time.monotonic()


# ----------------------------------------------------- fullscreen exit ----
class _FullscreenExit(QWidget):
    """Chrome's exit control for a fullscreen video: touch the top edge of
    the screen and a round button drops into view; click it to leave full
    screen. A top-level window, because the page below is a native one."""

    clicked = Signal()
    SIZE = 52
    MARGIN = 10

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Exit full screen (Esc)")
        self.setFixedSize(self.SIZE + 2 * self.MARGIN, self.SIZE + 2 * self.MARGIN)
        self._hover = False
        self._anim = None
        self._shown = False

    def drop(self, screen_rect):
        """Slides in from above the top edge of `screen_rect`."""
        if self._shown:
            return
        self._shown = True
        x = screen_rect.center().x() - self.width() // 2
        top = screen_rect.top() + 6
        self.move(x, top - self.height())
        self.show()
        self.raise_()
        self._anim = motion.tween(self, top - self.height(), top, motion.MEDIUM,
                                  lambda v: self.move(x, round(v)))

    def lift(self):
        if not self._shown:
            return
        self._shown = False
        y0, x = self.y(), self.x()
        self._anim = motion.tween(self, y0, y0 - self.height() - 8, motion.MEDIUM,
                                  lambda v: self.move(x, round(v)), self.hide)

    def is_down(self):
        return self._shown

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.MARGIN
        r = QRectF(m, m, self.SIZE, self.SIZE)
        for i in range(m, 0, -2):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 6 * (m - i + 2) // 2))
            p.drawEllipse(r.adjusted(-i + 2, -i + 4, i - 2, i))
        p.setBrush(QColor(28, 34, 48, 235) if not self._hover else QColor(48, 58, 80, 245))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawEllipse(r)
        pen = QPen(QColor(240, 244, 252), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c, d = r.center(), self.SIZE * 0.16
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        p.end()


# ------------------------------------------------------------ downloads ----
_INTERRUPT_REASONS = {
    "FileNoSpace": "not enough disk space",
    "FileAccessDenied": "the folder couldn't be written to",
    "FileTooLarge": "the file is too large for this drive",
    "FileNameTooLong": "the file name is too long",
    "FileBlocked": "Windows blocked the file",
    "FileFailed": "the file couldn't be written",
    "NetworkFailed": "the network connection failed",
    "NetworkTimeout": "the connection timed out",
    "NetworkDisconnected": "the network disconnected",
    "NetworkServerDown": "the server is down",
    "ServerFailed": "the server failed",
    "ServerBadContent": "the server sent a bad file",
    "ServerUnauthorized": "the site needs you to sign in",
    "ServerForbidden": "the site refused the download",
    "ServerNoRange": "the server can't resume it",
    "ServerCertificateProblem": "the site's certificate is invalid",
    "DownloadProcessCrashed": "the download stopped unexpectedly",
    "UserShutdown": "the app closed during the download",
}


class _NativeDownload(QObject):
    """A file a page downloaded. The engine does the downloading (so the
    page's cookies and session come along); this mirrors it onto a card in
    the Download tab and drives pause, resume and cancel from there."""

    def __init__(self, browser, op, path, host):
        super().__init__(browser)
        self.browser = browser
        self.op = op
        self.path = path
        self.name = os.path.basename(path)
        self.paused = False
        self.cancelled = False
        self.finished = False
        self._samples = []
        self._pct = 0.0
        dt = browser.download_tab
        self.job_id = dt.start_job(
            self.name, f"From {host}" if host else "Browser download", None,
            make_on_cancel=lambda _jid: self.cancel,
            make_on_pause_toggle=lambda _jid: self.set_paused,
            make_on_play=lambda _jid: self.play,
            make_on_retry=lambda _jid: self.retry,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def cancel(self):
        self.cancelled = True
        try:
            self.op.Cancel()
        except Exception:   # noqa: BLE001
            pass
        self._poll()

    def set_paused(self, paused):
        self.paused = bool(paused)
        try:
            if paused:
                self.op.Pause()
            elif self.op.CanResume:
                self.op.Resume()
        except Exception:   # noqa: BLE001
            logger.exception("Pause/resume failed")

    def retry(self):
        try:
            if self.op.CanResume:
                self.finished = False
                self.browser.download_tab.reset_for_retry(self.job_id)
                self.op.Resume()
                self._timer.start()
        except Exception:   # noqa: BLE001
            logger.exception("Resume failed")

    def play(self):
        if self.path and os.path.exists(self.path):
            os.startfile(self.path)

    def _end(self):
        self.finished = True
        self._timer.stop()

    def _poll(self):
        dt = self.browser.download_tab
        try:
            state = str(self.op.State)
            received = int(self.op.BytesReceived)
            total = self.op.TotalBytesToReceive
            total = int(total) if total is not None else 0
        except Exception:   # noqa: BLE001 -- the operation is gone
            self._end()
            return
        now = time.monotonic()
        self._samples.append((now, received))
        self._samples = [s for s in self._samples if now - s[0] <= 4.0]
        t0, b0 = self._samples[0]
        speed = (received - b0) / (now - t0) if now - t0 > 0.6 else 0.0
        size = (f"{formatting.humanize_size(received)} / {formatting.humanize_size(total)}"
                if total else formatting.humanize_size(received))
        if total:
            self._pct = received / total * 100
        if state == "InProgress":
            parts = [size]
            if self.paused:
                parts.insert(0, "Paused")
            elif speed > 0:
                parts.append(f"{formatting.humanize_size(speed)}/s")
                if total:
                    parts.append("ETA " + formatting.format_eta((total - received) / speed))
            dt.update_progress(self.job_id, self._pct, "  •  ".join(parts))
        elif state == "Interrupted":
            reason = str(self.op.InterruptReason)
            if self.cancelled or reason == "UserCanceled":
                dt.mark_cancelled(self.job_id)
                self._end()
            elif self.paused or reason == "UserPaused":
                dt.update_progress(self.job_id, self._pct, "Paused  •  " + size)
            else:
                pretty = _INTERRUPT_REASONS.get(reason) or re.sub(r"(?<!^)([A-Z])", r" \1", reason).lower()
                dt.mark_failed(self.job_id, f"Failed: {pretty}")
                self._end()
        elif state == "Completed":
            try:
                self.path = str(self.op.ResultFilePath) or self.path
            except Exception:   # noqa: BLE001
                pass
            dt.set_playable(self.job_id, True)
            try:
                size_bytes = os.path.getsize(self.path)
            except OSError:
                size_bytes = total or received
            download_history.add_entry("file", self.name, self.path, os.path.dirname(self.path), size_bytes)
            dt.mark_done(self.job_id, "✓ Completed")
            self._end()
            self.browser._download_finished(self)


# -------------------------------------------------------------- the tab ----
class BrowserTab(QWidget):
    # Page URL for the Video tab's own fetch and download.
    open_in_video_tab = Signal(str)
    # A page's player went fullscreen: the window hides its own chrome.
    fullscreen_requested = Signal(bool)
    # A magnet link was opened; a downloaded .torrent was offered.
    magnet_requested = Signal(str)
    torrent_file_requested = Signal(str)
    # "Show" on a download toast.
    show_downloads_requested = Signal()

    SLEEP_AFTER_S = 10 * 60
    TOOLBAR_H = 48

    # Web pages are native windows: MainWindow's page cross-fade skips this tab.
    hosts_native_views = True

    def __init__(self, settings, download_tab, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.download_tab = download_tab
        self.download_dir = settings_store.get_save_dir(settings, "video", config.DEFAULT_DOWNLOAD_DIR)
        self._tabs = []
        # Tabs in the order they were last used: closing one goes back to
        # the one used before it, as in Chrome and Edge.
        self._recent = []
        self._cur = None
        self._closed = []
        self._page_fullscreen = False
        self._fs_exit = None
        self._fs_away = 0
        self._fs_timer = QTimer(self)
        self._fs_timer.setInterval(100)
        self._fs_timer.timeout.connect(self._fs_poll)
        self._downloads = []
        self._status_busy = False
        self._panel = None
        self._watched_window = None
        self._dark = self._dark_mode()
        self._t = theme.tokens(dark_mode=self._dark)
        self.services = browser_engine.services()
        self.services.failed.connect(self._on_engine_failed)
        self.services.adguard_ready.connect(lambda ok: self._sync_shield())

        self._build_ui()
        self._setup_shortcuts()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1500)
        self._status_timer.timeout.connect(self._poll_adguard)
        # The shield's count only changes while a page loads or just after:
        # asked often then, rarely after.
        self._status_fast_until = 0.0
        self._browser_hidden_at = None
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setInterval(60 * 1000)
        self._sleep_timer.timeout.connect(self._sleep_idle_tabs)
        self._sleep_timer.start()
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self.save_state)
        self._completer_timer = QTimer(self)
        self._completer_timer.setSingleShot(True)
        self._completer_timer.setInterval(800)
        self._completer_timer.timeout.connect(self._refresh_completer)

        self.apply_theme()
        self._restore_session()

    # ------------------------------------------------------------- UI ----
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.chrome = QWidget()
        chrome = QVBoxLayout(self.chrome)
        chrome.setContentsMargins(0, 0, 0, 0)
        chrome.setSpacing(0)
        self.strip = TabStrip()
        self.strip.new_tab_clicked.connect(lambda: self._create_tab(activate=True))
        self.strip.close_all_clicked.connect(self.close_all_tabs)
        self.strip.reordered.connect(self._on_reordered)
        self.strip.new_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.strip.new_btn.customContextMenuRequested.connect(self._new_tab_menu)
        chrome.addWidget(self.strip)

        self.toolbar = QWidget()
        self.toolbar.setFixedHeight(self.TOOLBAR_H)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 6, 10, 8)
        bar.setSpacing(4)
        self.back_btn = ChromeButton("back", "Back (Alt+Left)")
        self.fwd_btn = ChromeButton("forward", "Forward (Alt+Right)")
        self.reload_btn = ChromeButton("reload", "Reload (Ctrl+R)")
        self.home_btn = ChromeButton("home", "Home (Alt+Home)")
        for b in (self.back_btn, self.fwd_btn, self.reload_btn, self.home_btn):
            bar.addWidget(b)
        bar.addSpacing(4)
        self.address = AddressBar()
        bar.addWidget(self.address, 1)
        bar.addSpacing(6)
        # Download right after the address (it's about this page); the music
        # player after it (it's about whichever tab is playing).
        self.download_btn = ActionButton("Download")
        self.download_btn.setToolTip("Download this page's video with Awesome Downloader")
        bar.addWidget(self.download_btn)
        bar.addSpacing(4)
        self.now_playing = NowPlaying()
        bar.addWidget(self.now_playing)
        bar.addSpacing(2)
        # Picture in picture for the page's video (Alt+P), the way Google's
        # PiP extension does it -- built in, like AdGuard.
        self.pip_btn = ChromeButton("pip", "Picture in picture (Alt+P)")
        bar.addWidget(self.pip_btn)
        # Bookmarks this page (and puts it on the bookmarks bar); right-click
        # -- or a click on a New Tab -- lists every bookmark.
        # Adding a bookmark is the address bar's star (Chrome's place for it);
        # this button is the way to every bookmark.
        self.bookmarks_btn = ChromeButton("bookmark", "All bookmarks (Ctrl+Shift+O)")
        self.bookmarks_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmarks_btn.customContextMenuRequested.connect(
            lambda _pos: self._show_bookmarks_panel(self.bookmarks_btn))
        bar.addWidget(self.bookmarks_btn)
        self.shield_btn = ChromeButton("shield_check", "AdGuard ad blocker")
        self.menu_btn = ChromeButton("menu", "Menu")
        bar.addWidget(self.shield_btn)
        bar.addWidget(self.menu_btn)
        chrome.addWidget(self.toolbar)
        self.load_bar = LoadBar(self.toolbar)

        # "An update is available": each start while one is waiting.
        self.update_bar = UpdateBar()
        self.update_bar.update_clicked.connect(self._update_from_bar)
        self.update_bar.never_clicked.connect(self._never_remind_update)
        self.update_bar.closed.connect(self._close_update_bar)
        self._update_bar_closed = False
        chrome.addWidget(self.update_bar)

        self.bookmarks_bar = BookmarksBar()
        self.bookmarks_bar.open_url.connect(self._open_bookmark)
        self.bookmarks_bar.remove_requested.connect(self._remove_bookmark)
        self.bookmarks_bar.edit_requested.connect(self.edit_bookmark)
        self.bookmarks_bar.all_clicked.connect(lambda: self._show_bookmarks_panel(self.bookmarks_bar.all_btn))
        # On by default: a bookmark you can't see anywhere reads as one that
        # didn't save (reported as "the bookmark button doesn't work").
        self.bookmarks_bar.setVisible(bool(browser_data.get_pref("bookmarks_bar", True)))
        if self.bookmarks_bar.isVisible():
            self.bookmarks_bar.set_bookmarks(browser_data.load_bookmarks())
        chrome.addWidget(self.bookmarks_bar)
        root.addWidget(self.chrome)

        self.pages = QStackedWidget()
        self.home = HomeView(self)
        self.home.go.connect(self._home_go)
        self.home.surfaced.connect(self._home_surfaced)
        self.home.strip_ready.connect(self._apply_home_backdrop)
        self.pages.addWidget(self.home.placeholder)
        self.missing = EngineMissing()
        self.missing.get_clicked.connect(lambda: QDesktopServices.openUrl(QUrl(WEBVIEW2_PAGE)))
        self.pages.addWidget(self.missing)
        root.addWidget(self.pages, 1)

        self._completer_model = QStringListModel(self)
        self._completer = QCompleter(self._completer_model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setMaxVisibleItems(8)
        self.address.edit.setCompleter(self._completer)
        self._refresh_completer()

        self.back_btn.clicked.connect(self.go_back)
        self.fwd_btn.clicked.connect(self.go_forward)
        self.reload_btn.clicked.connect(self.reload_or_stop)
        self.home_btn.clicked.connect(self.go_home)
        self.address.submitted.connect(lambda text: self.navigate(text))
        self.address.escaped.connect(self._focus_page)
        self.address.zoom_reset.connect(lambda: self._set_zoom(1.0))
        self.download_btn.clicked.connect(self._send_current_to_video_tab)
        self.bookmarks_btn.clicked.connect(lambda: self._show_bookmarks_panel(self.bookmarks_btn))
        self.pip_btn.clicked.connect(lambda: self.toggle_pip())
        self.address.star_clicked.connect(self._on_bookmark_button)
        self.shield_btn.clicked.connect(self._show_adguard_panel)
        self.menu_btn.clicked.connect(self._show_menu)
        self.now_playing.activated.connect(self._goto_media_tab)
        self.now_playing.toggle_clicked.connect(lambda: self._media_command("toggle"))
        self.now_playing.next_clicked.connect(lambda: self._media_command("next"))
        self.now_playing.prev_clicked.connect(lambda: self._media_command("prev"))
        self.now_playing.mute_clicked.connect(lambda: self._with_media_tab(self._toggle_mute))
        self.now_playing.seek_requested.connect(
            lambda s: self._with_media_tab(lambda t: t.view is not None and t.view.run_js(
                "window.__awdMedia && window.__awdMedia.seek(%d)" % int(s))))
        self.now_playing.download_clicked.connect(
            lambda: self._with_media_tab(lambda t: self.open_in_video_tab.emit(t.url)))
        self.now_playing.close_clicked.connect(lambda: self._with_media_tab(self._close))
        self.now_playing.pip_clicked.connect(lambda: self._with_media_tab(self.toggle_pip))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.load_bar.setGeometry(0, self.toolbar.height() - self.load_bar.height() + 1,
                                  self.toolbar.width(), self.load_bar.height())
        QTimer.singleShot(0, self._apply_home_backdrop)

    def paintEvent(self, event):
        # A hairline between the browser's chrome and the page.
        if not self.chrome.isVisible():
            return
        p = QPainter(self)
        y = self.chrome.geometry().bottom()
        p.fillRect(0, y, self.width(), 1, theme.qcolor(self._t["divider"]))
        p.end()

    def _setup_shortcuts(self):
        def add(seq, slot):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            return sc

        self._shortcuts = [
            add("Ctrl+T", lambda: self._create_tab(activate=True)),
            add("Ctrl+Shift+T", self.reopen_closed_tab),
            add("Ctrl+W", self.close_current_tab),
            add("Ctrl+Shift+W", self.close_all_tabs),
            add("Ctrl+Shift+O", lambda: self._show_bookmarks_panel(self.bookmarks_btn)),
            add("Alt+P", lambda: self.toggle_pip()),
            add("Ctrl+F4", self.close_current_tab),
            add("Ctrl+Tab", lambda: self._cycle(1)),
            add("Ctrl+Shift+Tab", lambda: self._cycle(-1)),
            add("Ctrl+PgDown", lambda: self._cycle(1)),
            add("Ctrl+PgUp", lambda: self._cycle(-1)),
            add("Ctrl+L", self.address.focus_and_select),
            add("Alt+D", self.address.focus_and_select),
            add("F6", self.address.focus_and_select),
            add("Ctrl+D", self.bookmark_current),
            add("Ctrl+F", self._find),
            add("Ctrl+R", self.reload),
            add("F5", self.reload),
            add("Alt+Left", self.go_back),
            add("Alt+Right", self.go_forward),
            add("Alt+Home", self.go_home),
            add("Ctrl+Shift+N", lambda: self._create_tab(activate=True, private=True)),
            add("Ctrl+Shift+B", self._toggle_bookmarks_bar),
            add("Escape", self._escape_fallback),
        ]
        for n in range(1, 10):
            self._shortcuts.append(add(f"Ctrl+{n}", lambda n=n: self._select_number(n)))

    # ---------------------------------------------------------- theme ----
    def _dark_mode(self):
        return (self.settings or {}).get("theme", "dark") != "light"

    def _page_bg(self):
        return QColor(*palettes.page_background(self._dark))

    def apply_theme(self):
        self._dark = self._dark_mode()
        t = self._t = theme.tokens(dark_mode=self._dark)
        for b in (self.back_btn, self.fwd_btn, self.reload_btn, self.home_btn, self.shield_btn, self.menu_btn,
                  self.bookmarks_btn, self.pip_btn):
            b.apply_theme(t)
        self.strip.apply_theme(t, self._dark)
        self.address.apply_theme(t, self._dark)
        self.download_btn.apply_theme(t)
        self.now_playing.apply_theme(t)
        self.load_bar.apply_theme(t)
        self.bookmarks_bar.apply_theme(t, self._dark)
        self.update_bar.apply_theme(t, self._dark)
        self.home.apply_theme()
        popup = self._completer.popup()
        popup.setStyleSheet(
            f"QListView {{ background: {t['card_bg_solid']}; color: {t['text']}; border: 1px solid {t['card_border']};"
            f" border-radius: 10px; padding: 4px; outline: none; font-size: 12px; }}"
            f"QListView::item {{ padding: 6px 10px; border-radius: 7px; }}"
            f"QListView::item:selected {{ background: {t['hover_overlay']}; color: {t['text']}; }}")
        for tab in self._tabs:
            tab.pill.apply_theme(t, self._dark)
            if tab.view is not None:
                tab.view.set_background(self._page_bg())
                tab.view.set_color_scheme(self._dark)
        self._sync_toolbar()
        self.update()

    def settings_changed(self):
        self.download_dir = settings_store.get_save_dir(self.settings, "video", config.DEFAULT_DOWNLOAD_DIR)

    # ----------------------------------------------------------- tabs ----

    def _current(self):
        return self._cur if self._cur in self._tabs else None

    def current_view(self):
        tab = self._current()
        return tab.view if tab is not None else None

    def _create_tab(self, url=None, activate=True, private=False, index=None, opener=None):
        pill = TabPill()
        pill.set_private(private)
        pill.apply_theme(self._t, self._dark)
        tab = _Tab(pill, private)
        tab.opener = opener
        pill.clicked.connect(lambda t=tab: self._switch_to(t))
        pill.close_clicked.connect(lambda t=tab: self._close(t))
        pill.mute_clicked.connect(lambda t=tab: self._toggle_mute(t))
        pill.context_requested.connect(lambda pos, t=tab: self._tab_menu(t, pos))
        if index is None or index > len(self._tabs):
            index = len(self._tabs)
        self._tabs.insert(index, tab)
        self.strip.add_pill(pill, index, animate=self.isVisible())
        if url:
            tab.on_home = False
            tab.url = url
            tab.title = _title_for(url)
            tab.pending_url = url
            pill.set_url(url, home=False)
            pill.set_title(tab.title)
            icon_pm = favicons().get(url)
            if icon_pm is not None:
                pill.set_icon(icon_pm)
            else:
                favicons().ready.connect(lambda host, pm, t=tab: self._early_icon(t, host, pm))
        else:
            pill.set_url("", home=True)
        if activate:
            self._switch_to(tab)
        self._schedule_save()
        return tab

    def _early_icon(self, tab, host, pixmap):
        if tab.icon is None and _host(tab.url) == host:
            tab.pill.set_icon(pixmap)

    def _ensure_view(self, tab):
        if tab.view is not None:
            return tab.view
        self.services.start()
        if self.services.error is not None or self.services.engine is None:
            self._on_engine_failed(self.services.error or "WebView2 didn't start")
            return None
        view = webview2.WebView2Widget(self.services.engine, private=tab.private, parent=self.pages,
                                       background=self._page_bg())
        for js in browser_scripts.all_scripts():
            view.add_startup_script(js)
        view.created.connect(lambda v=view: v.set_color_scheme(self._dark))
        view.urlChanged.connect(lambda u, t=tab: self._on_url(t, u))
        view.nav_guard = self._dangerous
        view.navigationBlocked.connect(lambda u, hit, t=tab: self._show_blocked(t, u, hit))
        view.titleChanged.connect(lambda s, t=tab: self._on_title(t, s))
        view.loadStarted.connect(lambda t=tab: self._on_load_started(t))
        view.loadProgress.connect(lambda v, t=tab: self._on_progress(t, v))
        view.loadFinished.connect(lambda ok, t=tab: self._on_load_finished(t, ok))
        view.iconChanged.connect(lambda pm, t=tab: self._on_icon(t, pm))
        view.historyChanged.connect(lambda t=tab: self._sync_toolbar() if t is self._cur else None)
        view.audibleChanged.connect(lambda on, t=tab: self._on_audio(t, audible=on))
        view.mutedChanged.connect(lambda on, t=tab: self._on_audio(t, muted=on))
        view.zoomChanged.connect(lambda z, t=tab: self._on_zoom(t, z))
        view.fullScreenChanged.connect(lambda on, t=tab: self._on_fullscreen(t, on))
        view.webMessage.connect(lambda msg, t=tab: self._on_message(t, msg))
        view.newWindowRequested.connect(lambda args, t=tab: self._on_new_window(t, args))
        view.downloadStarting.connect(lambda args, t=tab: self._on_download_starting(t, args))
        view.contextMenuRequested.connect(lambda args, t=tab: self._on_context_menu(t, args))
        view.acceleratorKey.connect(lambda args, t=tab: self._on_accelerator(t, args))
        view.externalUri.connect(lambda args, t=tab: self._on_external_uri(t, args))
        view.windowCloseRequested.connect(lambda t=tab: QTimer.singleShot(0, lambda: self._close(t)))
        view.processFailed.connect(lambda kind, t=tab: self._on_process_failed(t, kind))
        if tab.private:
            view.created.connect(lambda v=view, t=tab: self._block_in_private(v, t))
        self.pages.addWidget(view)
        tab.view = view
        return view

    def _block_in_private(self, view, tab):
        """Network blocking for a private tab, from the built-in list: every
        request the page makes is checked, and one to a listed ad or tracker
        domain is answered with a 403 instead of going out. The page's own
        top-level address is never blocked."""
        domains = private_blocklist()
        if not domains or view.core is None:
            return
        wv = webview2.WV
        env = self.services.engine.env

        def on_request(sender, args):
            try:
                uri = str(args.Request.Uri)
                if uri == view.nav_uri or not uri.startswith(("http://", "https://")):
                    return
                if blocked_host(_host(uri), domains):
                    args.Response = env.CreateWebResourceResponse(None, 403, "Blocked", "")
                    tab.private_blocked = getattr(tab, "private_blocked", 0) + 1
            except Exception:   # noqa: BLE001 -- never break the page over a check
                pass
        view.core.AddWebResourceRequestedFilter("*", wv.CoreWebView2WebResourceContext.All)
        view.core.WebResourceRequested += on_request
        view._private_handler = on_request

    def _load_in(self, tab, url):
        view = self._ensure_view(tab)
        if view is None:
            return
        tab.on_home = False
        tab.page_ahead = False
        tab.pending_url = None
        tab.url = url
        view.load(url)
        tab.pill.set_url(url, home=False)
        if tab is self._cur:
            self._show_surface(tab)
            self._sync_toolbar()

    def _show_surface(self, tab):
        if self.services.error is not None and not tab.on_home:
            self.pages.setCurrentWidget(self.missing)
        elif tab.on_home or tab.view is None:
            if self.isVisible():
                self.home.ensure()
            self.home.show_for(tab)
            self.pages.setCurrentWidget(self.home.surface())
            self._apply_home_backdrop()
            return
        else:
            self.pages.setCurrentWidget(tab.view)
            # Its place straight away: the stack only lays out the page it
            # shows on its next layout pass, and until then a page shown for
            # the first time (or after the window changed size) sits wherever
            # it last was -- its native window with it.
            tab.view.setGeometry(self.pages.contentsRect())
        self._apply_home_backdrop()

    def _chrome_top(self):
        win = self.window()
        if win is None or win is self:
            return 0
        return max(0, self.pages.mapTo(win, QPoint(0, 0)).y())

    def _apply_home_backdrop(self):
        """On the home page, its wallpaper runs on up behind the browser's
        bars and the title bar, frosted there (the window paints the strip
        the page hands over); anywhere else, the window's own backdrop."""
        surface = getattr(self.window(), "backdrop_surface", None)
        if surface is None or not hasattr(surface, "set_top_image"):
            return
        top = self._chrome_top()
        self.home.set_chrome_top(top)
        cur = self._current()
        showing = (self.isVisible() and cur is not None and (cur.on_home or cur.view is None)
                   and self.home.view is not None and self.pages.currentWidget() is self.home.view
                   and self.home.strip is not None)
        surface.set_top_image(self.home.strip if showing else None, top)

    def _switch_to(self, tab):
        if tab not in self._tabs:
            return
        self._recent = [t for t in self._recent if t is not tab and t in self._tabs] + [tab]
        prev = self._current()
        if prev is not None and prev is not tab:
            prev.pill.set_active(False)
            prev.hidden_since = time.monotonic()
            if prev.view is not None:
                prev.view.set_active(False)
        self._cur = tab
        self._status_fast()
        tab.pill.set_active(True)
        tab.pill.set_sleeping(False)
        if tab.pending_url and self.isVisible():
            self._load_in(tab, tab.pending_url)
        self._show_surface(tab)
        if tab.view is not None:
            tab.view.set_active(True)
            if not tab.on_home:
                QTimer.singleShot(0, lambda: self._focus_page())
        self._sync_toolbar()
        self._update_now_playing()
        self._schedule_save()
        self._sync_private_look()

    def _sync_private_look(self):
        """A private tab in view puts the whole window in black and white --
        its wallpaper, its glass, its colours; site icons and pictures keep
        theirs -- so there's no mistaking which kind of tab this is. Any
        other tab, or another part of the app, has the chosen palette back."""
        win = self.window()
        if win is None or win is self or not hasattr(win, "set_palette_override"):
            return
        cur = self._current()
        private = bool(self.isVisible() and cur is not None and cur.private)
        win.set_palette_override(palettes.MONO if private else None)

    def _home_go(self, text, where):
        """A link or a search from the home page: here, in a new tab, or behind."""
        cur = self._current()
        if where == "current" or cur is None:
            self.navigate(text, tab=cur)
            return
        url = normalize_address(text)
        if url.lower().startswith("magnet:"):
            self._handle_magnet(url)
            return
        self._open_link(cur, url, background=(where == "background"))

    def _home_surfaced(self):
        cur = self._current()
        if cur is not None and (cur.on_home or cur.view is None) and self.isVisible():
            self.pages.setCurrentWidget(self.home.surface())
            self._apply_home_backdrop()

    def _close(self, tab, record=True):
        if tab not in self._tabs:
            return
        if len(self._tabs) == 1:
            # A Browser always keeps a tab; closing the last one leaves a
            # fresh home tab rather than refusing (which read as a bug).
            self._create_tab(activate=False)
        was_current = tab is self._cur
        idx = self._tabs.index(tab)
        if record and tab.url and not tab.on_home and tab.url.startswith(("http://", "https://")):
            self._closed.append({"url": tab.url, "private": tab.private})
            self._closed = self._closed[-browser_data.CLOSED_LIMIT:]
        self._tabs.remove(tab)
        self.strip.remove_pill(tab.pill, animate=self.isVisible())
        if tab.view is not None:
            view, tab.view = tab.view, None
            view.close_page()
            self.pages.removeWidget(view)
            view.deleteLater()
        self._recent = [t for t in self._recent if t is not tab]
        if was_current:
            # back to the tab used before this one; failing that, the one
            # that opened it, then its neighbour
            target = next((t for t in reversed(self._recent) if t in self._tabs), None)
            if target is None:
                target = tab.opener if tab.opener in self._tabs else self._tabs[min(idx, len(self._tabs) - 1)]
            self._cur = None
            self._switch_to(target)
        self._update_now_playing()
        self._schedule_save()

    def close_current_tab(self):
        if self._cur is not None:
            self._close(self._cur)

    def close_all_tabs(self):
        """The strip's close-all button: every tab goes, a fresh New Tab is
        left, and Ctrl+Shift+T -- or Undo on the toast -- brings them all
        back at once."""
        doomed = list(self._tabs)
        if len(doomed) == 1 and doomed[0].on_home and doomed[0].view is None and not doomed[0].pending_url:
            return
        urls = [t.url for t in doomed if not t.private and not t.on_home
                and (t.url or "").startswith(("http://", "https://"))]
        # The fresh tab first, made current, so closing the rest never
        # switches through them (which would load each one on its way out).
        self._create_tab(activate=True)
        for tab in doomed:
            self._close(tab, record=False)
        if urls:
            self._closed.append({"session": urls})
            self._closed = self._closed[-browser_data.CLOSED_LIMIT:]
        n = len(doomed)
        show_toast(self.pages, "Closed %d tab%s" % (n, "" if n == 1 else "s"),
                   action="Undo" if urls else None, on_action=self.reopen_closed_tab if urls else None,
                   kind="info", ms=6000)
        self._schedule_save()

    def reopen_closed_tab(self):
        """Ctrl+Shift+T: the last closed tab comes back -- or, right after a
        restart that didn't reopen them, every tab the last run had open. The
        list survives restarts (URLs only, a few KB); restored tabs stay
        unloaded until they're looked at, so they cost next to no memory."""
        if not self._closed:
            return
        entry = self._closed.pop()
        if "session" in entry:
            first = None
            for url in entry["session"]:
                tab = self._create_tab(url=url, activate=False)
                first = first or tab
            if first is not None:
                self._switch_to(first)
        else:
            self._create_tab(url=entry["url"], activate=True, private=entry.get("private", False))
        self._schedule_save()

    def _cycle(self, step):
        if self._cur in self._tabs and len(self._tabs) > 1:
            i = (self._tabs.index(self._cur) + step) % len(self._tabs)
            self._switch_to(self._tabs[i])

    def _select_number(self, n):
        if not self._tabs:
            return
        self._switch_to(self._tabs[-1] if n == 9 else self._tabs[min(n, len(self._tabs)) - 1])

    def _on_reordered(self, old, new):
        tab = self._tabs.pop(old)
        self._tabs.insert(new, tab)
        self._schedule_save()

    def _toggle_mute(self, tab):
        if tab.view is not None:
            tab.view.set_muted(not tab.muted)

    # ------------------------------------------------------ navigation ----
    def navigate(self, text, tab=None):
        text = (text or "").strip()
        if not text:
            return
        if text.lower().startswith("magnet:"):
            self._handle_magnet(text)
            return
        url = normalize_address(text)
        tab = tab or self._current() or self._create_tab(activate=True)
        if not self.isVisible():
            # Loaded when the Browser is next shown (showEvent), the way a
            # restored tab is -- not spun up in the background.
            tab.on_home = False
            tab.page_ahead = False
            tab.url = url
            tab.title = _title_for(url)
            tab.pending_url = url
            tab.pill.set_url(url, home=False)
            tab.pill.set_title(tab.title)
            if tab is self._cur:
                self._show_surface(tab)
                self._sync_toolbar()
            self._schedule_save()
            return
        self._load_in(tab, url)
        if tab is self._cur:
            QTimer.singleShot(0, self._focus_page)

    def open_for_sign_in(self, url):
        """Opens the link a download needed a sign-in for, in a new tab: the
        site shows its own login there, and once signed in the downloader
        uses this browser's session for it."""
        if url and url.startswith(("http://", "https://")):
            self._create_tab(url=url, activate=True)
            show_toast(self.pages, "Sign in on this page, then fetch the link again", kind="info", ms=6000)

    def _open_link(self, opener, url, background):
        index = self._tabs.index(opener) + 1 if opener in self._tabs else None
        return self._create_tab(url=url, activate=not background, private=opener.private if opener else False,
                                index=index, opener=opener)

    def go_back(self):
        tab = self._current()
        if tab is None or tab.on_home:
            return
        if tab.view is not None and tab.view.can_go_back():
            tab.view.back()
            return
        # The tab's first page: one more step back is its home page.
        tab.on_home = True
        tab.page_ahead = True
        tab.pill.set_url("", home=True)
        tab.pill.set_title("New Tab")
        self._show_surface(tab)
        self._sync_toolbar()

    def go_forward(self):
        tab = self._current()
        if tab is None:
            return
        if tab.on_home and tab.page_ahead and (tab.view is not None or tab.pending_url):
            tab.on_home = False
            tab.page_ahead = False
            tab.pill.set_url(tab.url, home=False)
            tab.pill.set_title(tab.title)
            if tab.pending_url:
                self._load_in(tab, tab.pending_url)
            self._show_surface(tab)
            self._sync_toolbar()
        elif not tab.on_home and tab.view is not None and tab.view.can_go_forward():
            tab.view.forward()

    def go_home(self):
        tab = self._current()
        if tab is None or tab.on_home:
            return
        tab.on_home = True
        tab.page_ahead = tab.view is not None
        tab.pill.set_url("", home=True)
        tab.pill.set_title("New Tab")
        self._show_surface(tab)
        self._sync_toolbar()

    def reload(self):
        tab = self._current()
        if tab is not None and not tab.on_home and tab.view is not None:
            tab.view.reload()

    def reload_or_stop(self):
        tab = self._current()
        if tab is None or tab.on_home or tab.view is None:
            return
        if tab.loading:
            tab.view.stop()
        else:
            tab.view.reload()

    def _focus_page(self):
        tab = self._current()
        if tab is not None and not tab.on_home and tab.view is not None:
            tab.view.setFocus(Qt.FocusReason.OtherFocusReason)
            tab.view.focus_page()

    def _find(self):
        view = self.current_view()
        if view is not None and not self._cur.on_home:
            view.find_in_page()

    def _set_zoom(self, factor):
        view = self.current_view()
        if view is not None:
            view.set_zoom(max(0.25, min(5.0, factor)))

    # ------------------------------------------------------ page events ----
    def _on_url(self, tab, url):
        if not url or url == "about:blank" and tab.url:
            return
        tab.url = url
        if not tab.on_home:
            tab.pill.set_url(url, home=False)
        if tab is self._cur:
            self._sync_toolbar()
        self._schedule_save()

    def _on_title(self, tab, title):
        tab.title = title or _title_for(tab.url)
        if not tab.on_home:
            tab.pill.set_title(tab.title)

    def _on_load_started(self, tab):
        if tab is self._cur:
            self._status_fast()
        tab.loading = True
        tab.has_video = False
        tab.pip = False
        tab.adguard = None
        tab.private_blocked = 0
        tab.pill.set_loading(True)
        if tab is self._cur:
            self.load_bar.begin()
            self._sync_toolbar()

    def _on_progress(self, tab, value):
        tab.progress = value
        if tab is self._cur and value < 100:
            self.load_bar.stage(value)

    def _on_load_finished(self, tab, ok):
        tab.loading = False
        tab.pill.set_loading(False)
        if tab is self._cur and tab.private:
            QTimer.singleShot(1500, self._sync_shield)
        if tab is self._cur:
            self.load_bar.finish()
            self._sync_toolbar()
            QTimer.singleShot(900, self._poll_adguard)
        if ok and not tab.private and tab.url.startswith(("http://", "https://")):
            browser_data.add_history_entry(tab.url, tab.title)
            self._completer_timer.start()

    def _on_icon(self, tab, pixmap):
        tab.icon = pixmap
        if not tab.on_home:
            tab.pill.set_icon(pixmap)

    def _on_audio(self, tab, audible=None, muted=None):
        if audible is not None:
            tab.audible = audible
        if muted is not None:
            tab.muted = muted
            if tab is getattr(self, "_media_source", None):
                self.now_playing.set_muted(muted)
        tab.pill.set_audio(tab.audible, tab.muted)

    def _on_zoom(self, tab, factor):
        tab.zoom = factor
        if tab is self._cur:
            self.address.set_zoom(factor)

    def _on_fullscreen(self, tab, on):
        if tab is not self._cur and on:
            return
        self._set_page_fullscreen(on)

    def _set_page_fullscreen(self, on):
        if on == self._page_fullscreen:
            return
        self._page_fullscreen = on
        self.chrome.setVisible(not on)
        self.fullscreen_requested.emit(on)
        self.update()
        # While fullscreen, watch for the pointer reaching the top edge (the
        # page is a native window: it gets the mouse moves, not Qt).
        if on:
            if self._fs_exit is None:
                owner = self.window()
                self._fs_exit = _FullscreenExit(owner if isinstance(owner, QWidget) else None)
                self._fs_exit.clicked.connect(self._escape_fallback)
            self._fs_timer.start()
        else:
            self._fs_timer.stop()
            if self._fs_exit is not None:
                self._fs_exit.lift()

    def _fs_poll(self, pos=None):
        """Drops the exit button when the pointer touches the top of the
        screen, lifts it once the pointer has moved well away."""
        if not self._page_fullscreen or self._fs_exit is None:
            return
        pos = QCursor.pos() if pos is None else pos
        screen = self.window().screen()
        area = screen.geometry() if screen is not None else self.window().frameGeometry()
        if not area.contains(pos):
            return
        if pos.y() <= area.top() + 2:
            self._fs_exit.drop(area)
            self._fs_away = 0
        elif self._fs_exit.is_down() and not self._fs_exit.geometry().adjusted(-40, -10, 40, 90).contains(pos):
            self._fs_away += 1
            if self._fs_away >= 8:          # ~0.8 s away from it
                self._fs_exit.lift()
        else:
            self._fs_away = 0

    def _escape_fallback(self):
        """Escape (or the exit button that drops from the top of the screen)
        always gets the window out of a page's fullscreen, even if the page and
        the window have fallen out of step."""
        if self._fs_exit is not None:
            self._fs_exit.lift()
        win = self.window()
        if getattr(win, "_app_fullscreen", False) and not self._page_fullscreen:
            win.leave_fullscreen()          # the app's own full screen (F11)
            return
        stuck = self._page_fullscreen or (hasattr(win, "isFullScreen") and win.isFullScreen())
        if not stuck:
            return
        view = self.current_view()
        if view is not None:
            view.run_js("document.fullscreenElement && document.exitFullscreen()")
        self._page_fullscreen = False
        self._fs_timer.stop()
        self.chrome.setVisible(True)
        if hasattr(win, "set_video_fullscreen") and win.isFullScreen():
            win.set_video_fullscreen(False)

    def _on_process_failed(self, tab, kind):
        logger.warning("WebView2 process failure (%s) in tab %s", kind, tab.url)
        if "BrowserProcessExited" in kind:
            show_toast(self.pages, "The browser engine stopped. Reopen the app to restart it.", kind="error")

    def _on_message(self, tab, msg):
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "download":
            url = msg.get("url") or tab.url
            if url.startswith(("http://", "https://")):
                self.open_in_video_tab.emit(url)
        elif kind == "media-present":
            tab.has_video = bool(msg.get("value"))
            if tab is self._cur:
                self.download_btn.set_lit(tab.has_video)
                self._sync_pip_button(tab)
        elif kind == "pip":
            tab.pip = bool(msg.get("on"))
            if tab is self._cur:
                self._sync_pip_button(tab)
        elif kind == "media":
            tab.media = msg if msg.get("present") else None
            tab.media_at = time.monotonic()
            self._update_now_playing()
        elif kind == "sb-back":
            if tab.view is not None and tab.view.can_go_back():
                tab.view.back()
            else:
                self.go_home()
        elif kind == "sb-continue":
            url = str(msg.get("url") or "")
            if url.startswith(("http://", "https://")):
                safe_browsing.allow(url)
                logger.warning("Opening a listed dangerous site at the person's request: %s", url)
                tab.view.load(url)
        elif kind == "open-tab":
            url = str(msg.get("url") or "")
            if url.startswith(("http://", "https://")):
                self._open_link(tab, url, background=bool(msg.get("background", True)))

    def _on_new_window(self, opener, args):
        """window.open(), target=_blank, and the engine's own "open in new
        window". The new page is handed the request itself (NewWindow), so
        the opener relationship survives -- sign-in popups depend on it."""
        uri = str(args.Uri or "")
        ag = self.services.adguard_id
        if ag and uri.startswith("chrome-extension://%s/" % ag) and ("post-install" in uri or "thankyou" in uri):
            self.services.engine.adopt_extension_window(args)
            return
        if not args.IsUserInitiated and uri.startswith(("http://", "https://")):
            # A page opening a window on its own -- the classic pop-up ad.
            args.Handled = True
            show_toast(self.pages, "Blocked a pop-up from %s" % _title_for(opener.url), action="Open",
                       on_action=lambda: self._open_link(opener, uri, background=False), kind="warning")
            return
        deferral = args.GetDeferral()
        index = self._tabs.index(opener) + 1 if opener in self._tabs else None
        tab = self._create_tab(activate=True, private=opener.private, index=index, opener=opener)
        view = self._ensure_view(tab)
        if view is None:
            deferral.Complete()
            return
        tab.on_home = False
        tab.url = uri
        tab.pill.set_url(uri, home=False)
        tab.pill.set_title(_title_for(uri))
        self._show_surface(tab)
        self._sync_toolbar()

        def attach():
            try:
                args.NewWindow = view.core
                args.Handled = True
            except Exception:   # noqa: BLE001
                logger.exception("Couldn't hand the new window its page")
            finally:
                deferral.Complete()
        view.when_scripts_ready(attach)

    def _on_external_uri(self, tab, args):
        uri = str(args.Uri or "")
        if uri.lower().startswith("magnet:"):
            args.Cancel = True
            QTimer.singleShot(0, lambda: self._handle_magnet(uri))

    def _handle_magnet(self, uri):
        self.magnet_requested.emit(uri)

    def _on_accelerator(self, tab, args):
        kind = str(args.KeyEventKind)
        if kind not in ("KeyDown", "SystemKeyDown"):
            return
        vk = int(args.VirtualKey)
        ctrl, shift, alt = _key_down(VK_CONTROL), _key_down(VK_SHIFT), _key_down(VK_MENU)
        action = None
        if ctrl and not alt:
            letter = chr(vk) if 0x41 <= vk <= 0x5A else None
            if letter == "T":
                action = self.reopen_closed_tab if shift else (lambda: self._create_tab(activate=True))
            elif letter == "N":
                action = (lambda: self._create_tab(activate=True, private=True)) if shift else (
                    lambda: self._create_tab(activate=True))
            elif letter == "W" or vk == VK_F4:
                action = self.close_current_tab
            elif letter == "L":
                action = self.address.focus_and_select
            elif letter == "D" and not shift:
                action = self.bookmark_current
            elif letter == "B" and shift:
                action = self._toggle_bookmarks_bar
            elif vk == VK_TAB:
                action = (lambda: self._cycle(-1)) if shift else (lambda: self._cycle(1))
            elif vk in (VK_PRIOR, VK_NEXT):
                action = (lambda: self._cycle(-1)) if vk == VK_PRIOR else (lambda: self._cycle(1))
            elif 0x31 <= vk <= 0x39 and not shift:
                action = lambda n=vk - 0x30: self._select_number(n)
        elif alt and not ctrl:
            if vk == 0x44:          # Alt+D
                action = self.address.focus_and_select
            elif vk == VK_HOME:
                action = self.go_home
        elif vk == VK_F6 and not (ctrl or alt):
            action = self.address.focus_and_select
        elif vk == VK_F11 and not (ctrl or alt):
            win = self.window()
            if hasattr(win, "toggle_app_fullscreen") and not self._page_fullscreen:
                action = win.toggle_app_fullscreen
        if alt and not ctrl and vk == 0x50:     # Alt+P
            action = self.toggle_pip
        elif vk == VK_ESCAPE:
            QTimer.singleShot(0, self._escape_fallback)
            return
        if action is not None:
            args.Handled = True
            QTimer.singleShot(0, action)

    def _on_context_menu(self, tab, args):
        """Adds this app's items above the engine's own menu."""
        try:
            env = self.services.engine.env
            kinds = webview2.WV.CoreWebView2ContextMenuItemKind
            target = args.ContextMenuTarget
            items = args.MenuItems
            ours = []
            target_kind = str(target.Kind)
            page_url = str(target.PageUri or tab.url)
            if target_kind in ("Video", "Audio"):
                ours.append(("Download this video with Awesome Downloader",
                             lambda: self.open_in_video_tab.emit(page_url)))
            if target.HasLinkUri:
                link = str(target.LinkUri)
                low = link.lower()
                if low.startswith(("http://", "https://")):
                    ours.append(("Open link in new tab", lambda: self._open_link(tab, link, background=False)))
                    ours.append(("Open link in background tab", lambda: self._open_link(tab, link, background=True)))
                    if not tab.private:
                        ours.append(("Open link in private tab",
                                     lambda: self._create_tab(url=link, activate=True, private=True)))
                    ours.append(("Download link with Awesome Downloader", lambda: self.open_in_video_tab.emit(link)))
                elif low.startswith("magnet:"):
                    ours.append(("Add to the Torrent tab", lambda: self._handle_magnet(link)))
                # Tabs are what this browser has; the engine's "new window" would just be one more.
                for i in range(items.Count - 1, -1, -1):
                    if str(items[i].Name) in ("openLinkInNewWindow", "openLinkInNewWindowInPrivate"):
                        items.RemoveAt(i)
            if not ours:
                return
            made = []
            for label, fn in ours:
                item = env.CreateContextMenuItem(label, None, kinds.Command)
                item.CustomItemSelected += (lambda s, a, f=fn: QTimer.singleShot(0, f))
                made.append(item)
            made.append(env.CreateContextMenuItem("", None, kinds.Separator))
            for i, item in enumerate(made):
                items.Insert(i, item)
        except Exception:   # noqa: BLE001 -- the default menu still shows
            logger.exception("Couldn't extend the page's context menu")

    # ------------------------------------------------------ downloads ----
    def _on_download_starting(self, tab, args):
        try:
            suggested = os.path.basename(str(args.ResultFilePath or "")) or "download"
            os.makedirs(self.download_dir, exist_ok=True)
            path = _unique_path(os.path.join(self.download_dir, suggested))
            args.ResultFilePath = path
            args.Handled = True     # no engine download flyout: the Download tab shows it
            dl = _NativeDownload(self, args.DownloadOperation, path, _title_for(tab.url))
            self._downloads.append(dl)
            show_toast(self.pages, "Downloading %s" % os.path.basename(path), action="Show",
                       on_action=self.show_downloads_requested.emit)
        except Exception:   # noqa: BLE001 -- the engine's own download UI takes over
            logger.exception("Couldn't take over a download")

    def _download_finished(self, dl):
        if dl in self._downloads:
            self._downloads.remove(dl)
        if dl.path.lower().endswith(".torrent"):
            show_toast(self.pages, "Downloaded %s" % os.path.basename(dl.path), action="Add to Torrent tab",
                       on_action=lambda p=dl.path: self.torrent_file_requested.emit(p), kind="success", ms=8000)

    def _send_current_to_video_tab(self):
        tab = self._current()
        if tab is not None and not tab.on_home and tab.url.startswith(("http://", "https://")):
            self.open_in_video_tab.emit(tab.url)

    # ---------------------------------------------------- toolbar state ----
    def _sync_toolbar(self):
        tab = self._current()
        if tab is None:
            return
        page = not tab.on_home
        self.address.set_url(tab.url if page else "")
        self.address.set_zoom(tab.zoom if page else 1.0)
        can_mark = page and (tab.url or "").startswith(("http://", "https://", "file:"))
        marked = can_mark and browser_data.is_bookmarked(browser_data.load_bookmarks(), tab.url)
        self.address.set_bookmarked(can_mark, marked)
        can_back = page
        can_fwd = (tab.on_home and tab.page_ahead) or (page and tab.view is not None and tab.view.can_go_forward())
        self.back_btn.setEnabled(can_back)
        self.fwd_btn.setEnabled(bool(can_fwd))
        self.reload_btn.setEnabled(page)
        self.reload_btn.set_kind("stop" if page and tab.loading else "reload")
        self.reload_btn.set_tip("Stop loading (Esc)" if page and tab.loading else "Reload (Ctrl+R)")
        self.download_btn.setEnabled(page and tab.url.startswith(("http://", "https://")))
        self.download_btn.set_lit(page and tab.has_video)
        self._sync_pip_button(tab)
        if not (page and tab.loading):
            self.load_bar.reset()
        self._sync_shield()

    def _sync_shield(self):
        tab = self._current()
        if tab is not None and tab.private:
            count = getattr(tab, "private_blocked", 0) if not tab.on_home else 0
            self.shield_btn.set_kind("shield_check")
            self.shield_btn.set_badge(_short_count(count) if count else "")
            return
        ok = self.services.adguard_ok
        status = tab.adguard if tab is not None and not tab.on_home else None
        off = not ok or bool(status and (status.get("paused") or status.get("allowlisted")))
        self.shield_btn.set_kind("shield_off" if off and self.services.engine is not None else "shield_check")
        blocked = int(status.get("blocked") or 0) if status else 0
        self.shield_btn.set_badge(_short_count(blocked) if blocked else "")

    def _status_fast(self, seconds=20):
        self._status_fast_until = time.monotonic() + seconds
        if self._status_timer.interval() != 1500:
            self._status_timer.setInterval(1500)

    def _poll_adguard(self):
        if time.monotonic() > self._status_fast_until and self._status_timer.interval() != 6000:
            self._status_timer.setInterval(6000)
        tab = self._current()
        if tab is not None and tab.private:
            self._sync_shield()
            return
        if (not self.isVisible() or tab is None or tab.on_home or tab.private or self._status_busy
                or not self.services.adguard_ok or not tab.url.startswith(("http://", "https://"))):
            return
        self._status_busy = True
        url = tab.url

        def done(status):
            self._status_busy = False
            if tab is not self._current() or tab.url != url:
                return
            tab.adguard = status
            self._sync_shield()
            if self._panel is not None:
                try:
                    self._panel.set_status(status)
                except RuntimeError:
                    self._panel = None
        self.services.page_status(url, done)

    # ------------------------------------------------------ now playing ----
    def _media_tab(self):
        playing = [t for t in self._tabs if t.media and t.media.get("playing")]
        pool = playing or [t for t in self._tabs if t.media]
        return max(pool, key=lambda t: t.media_at) if pool else None

    def _update_now_playing(self):
        tab = self._media_tab()
        self._media_source = tab
        self.now_playing.set_media(tab.media if tab is not None else None)
        self.now_playing.set_muted(bool(tab is not None and tab.muted))
        self.home.set_now_playing(tab.media if tab is not None else None)

    # ---- dangerous sites ----
    def _dangerous(self, url):
        if not browser_data.get_pref("safe_browsing", True):
            return None
        return safe_browsing.check(url)

    def _set_safe_browsing(self, on):
        browser_data.set_pref("safe_browsing", bool(on))
        if on:
            safe_browsing.start()

    def _show_blocked(self, tab, url, hit):
        """In place of a listed page: what it is, why it was stopped, and the
        way back -- with "continue anyway" for a list that's wrong."""
        kind, list_name = hit
        host = urllib.parse.urlparse(url).hostname or url
        what = {"phishing": "a phishing site -- a page made to look like another one, to take passwords "
                            "or card details",
                "malware": "a site that spreads malware -- software that harms your PC or steals from it",
                "scam": "a scam site"}.get(kind, "a dangerous site")
        tab.url = url
        tab.title = "Blocked: %s" % host
        tab.pill.set_title(tab.title)
        if tab is self._cur:
            self._sync_toolbar()
        tab.view.core.NavigateToString(_BLOCKED_PAGE % {
            "host": html.escape(host), "what": html.escape(what), "list": html.escape(list_name),
            "url": json.dumps(url)})
        show_toast(self.pages, "Stopped %s: it's on a list of %s sites." % (host, kind), kind="warning")

    # ---- picture in picture ----
    def _sync_pip_button(self, tab):
        page = tab is not None and not tab.on_home
        self.pip_btn.setEnabled(bool(page and (tab.has_video or tab.media or tab.pip)))
        self.pip_btn.tint = self._t["brand"] if page and tab.pip else None
        self.pip_btn.set_tip("Close picture in picture (Alt+P)" if page and tab.pip
                             else "Picture in picture (Alt+P)")
        self.pip_btn.update()

    def toggle_pip(self, tab=None):
        """Puts the page's video in a picture-in-picture window, or brings it
        back. Run as a click in the page: Chromium allows PiP only from one."""
        tab = tab or self._current()
        if tab is None or tab.on_home or tab.view is None:
            return

        def done(result):
            if result == "none":
                show_toast(self.pages, "There's no video on this page to pop out. "
                           "For a video inside a frame, use the button on the video itself.", kind="info")
            elif isinstance(result, str) and result.startswith("refused"):
                logger.info("Picture in picture refused: %s", result)
                show_toast(self.pages, "This video can't play in picture in picture.", kind="warning")
        tab.view.evaluate("window.__awdPip ? window.__awdPip.toggle() : 'none'", done, gesture=True)

    def _with_media_tab(self, fn):
        tab = getattr(self, "_media_source", None)
        if tab in self._tabs:
            fn(tab)

    def _goto_media_tab(self):
        tab = getattr(self, "_media_source", None)
        if tab in self._tabs:
            self._switch_to(tab)

    def _media_command(self, command):
        tab = getattr(self, "_media_source", None)
        if tab in self._tabs and tab.view is not None:
            tab.view.run_js("window.__awdMedia && window.__awdMedia.%s()" % command)

    # -------------------------------------------------------- sleeping ----
    # With the Browser tab itself out of sight this long, every page sleeps
    # -- the one that was showing and the home page too -- unless it is
    # playing sound or still loading; they wake when looked at again.
    BROWSER_AWAY_S = 3 * 60

    def _sleep_idle_tabs(self):
        now = time.monotonic()
        away = self._browser_hidden_at is not None and now - self._browser_hidden_at >= self.BROWSER_AWAY_S
        for tab in self._tabs:
            if tab.view is None or tab.audible or tab.loading:
                continue
            if not away and (tab is self._cur or now - tab.hidden_since < self.SLEEP_AFTER_S):
                continue
            if not tab.view.is_suspended():
                tab.view.suspend()
                if tab is not self._cur:
                    tab.pill.set_sleeping(True)
        if away and self.home.view is not None and not self.home.view.isVisible():
            self.home.view.suspend()

    # ------------------------------------------------------ bookmarks ----
    def _on_bookmark_button(self):
        tab = self._current()
        if tab is None or tab.on_home or not (tab.url or "").startswith(("http://", "https://", "file:")):
            # Nothing here to bookmark: show what's saved instead.
            self._show_bookmarks_panel(self.bookmarks_btn)
            return
        self.bookmark_current()

    def bookmark_current(self):
        tab = self._current()
        if tab is None or tab.on_home or not tab.url:
            return
        url = tab.url
        bookmarks = browser_data.load_bookmarks()
        existing = next((b for b in bookmarks if b.get("url") == url), None)
        added = existing is None
        if added:
            browser_data.add_bookmark(url, tab.title or url)
            existing = {"url": url, "title": tab.title or url}
            if not self.bookmarks_bar.isVisible():
                browser_data.set_pref("bookmarks_bar", True)
                self.bookmarks_bar.setVisible(True)
            self._bookmarks_changed()
            self.bookmarks_bar.flash(url)
        popup = BookmarkPopup(self, self._t, self._dark, existing.get("title") or url, added)

        def save(title):
            browser_data.remove_bookmark(url)
            browser_data.add_bookmark(url, title or url)
            self._bookmarks_changed()

        def remove():
            browser_data.remove_bookmark(url)
            self._bookmarks_changed()
        popup.saved.connect(save)
        popup.removed.connect(remove)
        popup.show_all.connect(lambda: QTimer.singleShot(0, lambda: self._show_bookmarks_panel(self.bookmarks_btn)))
        star = self.address.star_btn
        popup.open_under(star if star.isVisible() else self.bookmarks_btn)

    def _remove_bookmark(self, url):
        browser_data.remove_bookmark(url)
        self._bookmarks_changed()

    def edit_bookmark(self, url, dialog=None):
        """Chrome's Edit...: the bookmark's name and address in a small
        dialog, saved in place. `dialog` is for tests (anything with exec()
        and values()). Returns whether it changed."""
        item = next((b for b in browser_data.load_bookmarks() if b.get("url") == url), None)
        if item is None:
            return False
        if dialog is None:
            from .dialogs.bookmark_dialog import EditBookmarkDialog
            dialog = EditBookmarkDialog(self.window(), item.get("title") or url, url,
                                        dark_mode=self._dark, normalize=bookmark_address)
        if not dialog.exec():
            return False
        title, new_url = dialog.values()
        if not new_url or (title == item.get("title") and new_url == url):
            return False
        browser_data.update_bookmark(url, new_url, title)
        self._bookmarks_changed()
        return True

    def _bookmarks_changed(self):
        self._sync_toolbar()
        self._refresh_completer()
        self.home.refresh()
        if self.bookmarks_bar.isVisible():
            self.bookmarks_bar.set_bookmarks(browser_data.load_bookmarks())

    # ---- the update notice ----
    def show_update_notice(self, release):
        """From the main window's startup check: a newer version exists."""
        from app.utils import updater
        version = (release or {}).get("version")
        if not version or self._update_bar_closed or updater.is_dismissed(version):
            return
        self.update_bar.show_for(version)
        QTimer.singleShot(400, self._apply_home_backdrop)

    def _update_from_bar(self):
        win = self.window()
        if hasattr(win, "open_app_update"):
            win.open_app_update()

    def _never_remind_update(self):
        from app.utils import updater
        if self.update_bar.version:
            updater.dismiss(self.update_bar.version)
        self.update_bar.dismiss()
        QTimer.singleShot(400, self._apply_home_backdrop)

    def _close_update_bar(self):
        self._update_bar_closed = True
        self.update_bar.dismiss()
        QTimer.singleShot(400, self._apply_home_backdrop)

    def _toggle_bookmarks_bar(self):
        on = not self.bookmarks_bar.isVisible()
        browser_data.set_pref("bookmarks_bar", on)
        if on:
            self.bookmarks_bar.set_bookmarks(browser_data.load_bookmarks())
        self.bookmarks_bar.setVisible(on)
        self.update()
        QTimer.singleShot(0, self._apply_home_backdrop)

    def _open_bookmark(self, url, background):
        """A bookmark opens in a new tab, so the page you're on stays put.
        Two exceptions, both so tabs don't pile up: a bookmark that's
        already open is switched to, and a blank New Tab is used rather than
        left behind."""
        if not url:
            return
        cur = self._current()
        if not background:
            for tab in self._tabs:
                if tab.url == url and not tab.on_home and not tab.private:
                    self._switch_to(tab)
                    return
            if (cur is not None and cur.on_home and cur.view is None and not cur.pending_url
                    and not cur.page_ahead):
                self.navigate(url, tab=cur)
                return
        self._open_link(cur, url, background=background)

    def _show_bookmarks_panel(self, anchor=None):
        anchor = anchor or self.bookmarks_btn
        tab = self._current()
        page = tab is not None and not tab.on_home and (tab.url or "").startswith(("http://", "https://"))
        bookmarks = browser_data.load_bookmarks()
        added = page and browser_data.is_bookmarked(bookmarks, tab.url)
        panel = BookmarksPanel(self, self._t, self._dark, bookmarks,
                               bar_visible=self.bookmarks_bar.isVisible(), can_add=page, added=added)
        panel.open_url.connect(self._open_bookmark)
        panel.remove_requested.connect(self._remove_bookmark)
        panel.edit_requested.connect(self.edit_bookmark)
        panel.add_current.connect(self.bookmark_current)
        panel.bar_toggled.connect(lambda on: self._toggle_bookmarks_bar() if on != self.bookmarks_bar.isVisible()
                                  else None)
        panel.open_under(anchor)
        return panel

    def _refresh_completer(self):
        urls = {e.get("url") for e in browser_data.load_history() if e.get("url")}
        urls |= {b.get("url") for b in browser_data.load_bookmarks() if b.get("url")}
        self._completer_model.setStringList(sorted(urls))

    # ---------------------------------------------------------- menus ----
    def _new_tab_menu(self, pos):
        menu = style_menu(QMenu(self), self._t)
        menu.addAction(icon("tab", self._t["text_muted"]), "New tab\tCtrl+T").triggered.connect(
            lambda: self._create_tab(activate=True))
        menu.addAction(icon("private", self._t["text_muted"]), "New private tab\tCtrl+Shift+N").triggered.connect(
            lambda: self._create_tab(activate=True, private=True))
        menu.exec(self.strip.new_btn.mapToGlobal(pos))

    def _tab_menu(self, tab, pos):
        t = self._t
        menu = style_menu(QMenu(self), t)
        page = not tab.on_home
        a = menu.addAction(icon("reload", t["text_muted"]), "Reload")
        a.setEnabled(page and tab.view is not None)
        a.triggered.connect(lambda: tab.view.reload() if tab.view else None)
        a = menu.addAction(icon("tab", t["text_muted"]), "Duplicate")
        a.setEnabled(page)
        a.triggered.connect(lambda: self._open_link(tab, tab.url, background=False))
        a = menu.addAction(icon("speaker" if tab.muted else "mute", t["text_muted"]),
                           "Unmute tab" if tab.muted else "Mute tab")
        a.setEnabled(tab.view is not None)
        a.triggered.connect(lambda: self._toggle_mute(tab))
        menu.addSeparator()
        menu.addAction(icon("close", t["text_muted"]), "Close tab\tCtrl+W").triggered.connect(
            lambda: self._close(tab))
        others = [x for x in self._tabs if x is not tab]
        a = menu.addAction("Close other tabs")
        a.setEnabled(bool(others))
        a.triggered.connect(lambda: [self._close(x) for x in list(others)])
        right = self._tabs[self._tabs.index(tab) + 1:] if tab in self._tabs else []
        a = menu.addAction("Close tabs to the right")
        a.setEnabled(bool(right))
        a.triggered.connect(lambda: [self._close(x) for x in list(right)])
        menu.addSeparator()
        a = menu.addAction("Reopen closed tab\tCtrl+Shift+T")
        a.setEnabled(bool(self._closed))
        a.triggered.connect(self.reopen_closed_tab)
        menu.exec(pos)

    def _zoom_row(self, menu):
        t = self._t
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 4, 8, 4)
        lay.setSpacing(6)
        label = QLabel("Zoom")
        label.setStyleSheet(f"color: {t['text']}; background: transparent;")
        lay.addWidget(label, 1)
        tab = self._current()
        zoom = tab.zoom if tab is not None else 1.0
        pct = QLabel(f"{round(zoom * 100)}%")
        pct.setStyleSheet(f"color: {t['text_muted']}; background: transparent; min-width: 40px;")
        pct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        minus = ChromeButton("zoom_out", "Zoom out (Ctrl+-)", size=28, icon_size=15)
        plus = ChromeButton("zoom_in", "Zoom in (Ctrl++)", size=28, icon_size=15)
        for b in (minus, plus):
            b.apply_theme(t)
        steps = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0]

        def bump(direction):
            cur = self._current()
            if cur is None or cur.view is None or cur.on_home:
                return
            z = cur.zoom
            if direction > 0:
                nxt = next((s for s in steps if s > z + 0.001), steps[-1])
            else:
                nxt = next((s for s in reversed(steps) if s < z - 0.001), steps[0])
            self._set_zoom(nxt)
            pct.setText(f"{round(nxt * 100)}%")
        minus.clicked.connect(lambda: bump(-1))
        plus.clicked.connect(lambda: bump(1))
        lay.addWidget(minus)
        lay.addWidget(pct)
        lay.addWidget(plus)
        action = QWidgetAction(menu)
        action.setDefaultWidget(row)
        return action

    def _show_menu(self):
        t = self._t
        muted = t["text_muted"]
        tab = self._current()
        page = tab is not None and not tab.on_home and tab.view is not None
        menu = style_menu(QMenu(self), t)
        menu.addAction(icon("tab", muted), "New tab\tCtrl+T").triggered.connect(
            lambda: self._create_tab(activate=True))
        menu.addAction(icon("private", muted), "New private tab\tCtrl+Shift+N").triggered.connect(
            lambda: self._create_tab(activate=True, private=True))
        a = menu.addAction(icon("reload", muted), "Reopen closed tab\tCtrl+Shift+T")
        a.setEnabled(bool(self._closed))
        a.triggered.connect(self.reopen_closed_tab)
        menu.addSeparator()

        bm = style_menu(menu.addMenu(icon("star", muted), "Bookmarks"), t)
        a = bm.addAction("Show bookmarks bar\tCtrl+Shift+B")
        a.setCheckable(True)
        a.setChecked(self.bookmarks_bar.isVisible())
        a.triggered.connect(self._toggle_bookmarks_bar)
        bm.addSeparator()
        bookmarks = browser_data.load_bookmarks()
        if not bookmarks:
            bm.addAction("No bookmarks yet").setEnabled(False)
        for b in bookmarks[:40]:
            act = bm.addAction((b.get("title") or b.get("url", ""))[:60])
            act.setToolTip(b.get("url", ""))
            act.triggered.connect(lambda _c=False, u=b.get("url"): self._open_bookmark(u, False))

        hist = style_menu(menu.addMenu(icon("clock", muted), "History"), t)
        entries = browser_data.load_history()
        if not entries:
            hist.addAction("No history yet").setEnabled(False)
        for e in entries[:25]:
            act = hist.addAction((e.get("title") or e.get("url", ""))[:60])
            act.setToolTip(e.get("url", ""))
            act.triggered.connect(lambda _c=False, u=e.get("url"): self.navigate(u))
        if entries:
            hist.addSeparator()
            hist.addAction("Clear history").triggered.connect(
                lambda: (browser_data.clear_history(), self._refresh_completer()))
        menu.addSeparator()

        menu.addAction(self._zoom_row(menu))
        for label, kind, fn in (("Find on page\tCtrl+F", "search", self._find),
                                ("Print\tCtrl+P", "print", lambda: self.current_view().print_page()),
                                ("Save page as\tCtrl+S", "save", lambda: self.current_view().save_page_as())):
            a = menu.addAction(icon(kind, muted), label)
            a.setEnabled(page)
            a.triggered.connect(fn)
        menu.addSeparator()

        engines = style_menu(menu.addMenu(icon("search", muted), "Search engine"), t)
        current_engine = browser_data.get_search_engine()
        for key, (label, _url) in browser_data.SEARCH_ENGINES.items():
            act = engines.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == current_engine)
            act.triggered.connect(lambda _c=False, k=key: browser_data.set_search_engine(k))
        restore = menu.addAction("Reopen tabs on start")
        restore.setCheckable(True)
        restore.setChecked(bool(browser_data.get_pref("restore_tabs", True)))
        restore.triggered.connect(lambda on: browser_data.set_pref("restore_tabs", bool(on)))
        sites, updated = safe_browsing.status()
        guard = menu.addAction("Block dangerous sites")
        guard.setCheckable(True)
        guard.setChecked(bool(browser_data.get_pref("safe_browsing", True)))
        guard.setToolTip("Phishing, malware and scam sites from open lists (URLhaus, The Block List "
                         "Project): %s sites, updated %s" % (
                             "{:,}".format(sites) if sites else "no",
                             time.strftime("%d %b %H:%M", time.localtime(updated)) if updated else "not yet"))
        guard.triggered.connect(self._set_safe_browsing)
        menu.addAction(icon("trash", muted), "Clear browsing data...").triggered.connect(self._clear_data_dialog)
        menu.addSeparator()

        a = menu.addAction(icon("external", muted), "Open in your default browser")
        a.setEnabled(page)
        a.triggered.connect(lambda: webbrowser.open(self._cur.url))
        a = menu.addAction(icon("code", muted), "Developer tools\tF12")
        a.setEnabled(page)
        a.triggered.connect(lambda: self.current_view().open_devtools())
        a = menu.addAction(icon("gauge", muted), "Browser task manager")
        a.setEnabled(page)
        a.triggered.connect(lambda: self.current_view().open_task_manager())
        a = menu.addAction(icon("shield", muted), "AdGuard filters and settings")
        a.setEnabled(bool(self.services.adguard_id))
        a.triggered.connect(self._open_adguard_settings)
        menu.exec(self.menu_btn.mapToGlobal(QPoint(self.menu_btn.width() - menu.sizeHint().width(),
                                                   self.menu_btn.height() + 4)))

    def _clear_data_dialog(self):
        from .dialogs.base import CinematicDialog, button_row, header
        dlg = CinematicDialog(self, "Clear browsing data", self._dark)
        dlg.setFixedWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        lay.addLayout(header("Clear browsing data", "From the Browser tab only. Downloads on disk aren't touched."))
        boxes = {
            "history": QCheckBox("Browsing history"),
            "cookies": QCheckBox("Cookies -- signs you out of sites"),
            "cache": QCheckBox("Cached images and files"),
        }
        boxes["history"].setChecked(True)
        boxes["cache"].setChecked(True)
        for box in boxes.values():
            lay.addWidget(box)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("quiet")
        clear = QPushButton("Clear")
        clear.setObjectName("danger")
        cancel.clicked.connect(dlg.reject)
        clear.clicked.connect(dlg.accept)
        lay.addSpacing(6)
        lay.addLayout(button_row(cancel, clear))
        if dlg.exec() != CinematicDialog.DialogCode.Accepted:
            return
        kinds = [k for k, box in boxes.items() if box.isChecked()]
        if "history" in kinds:
            browser_data.clear_history()
            self._refresh_completer()
        self.services.clear_browsing_data(
            kinds, lambda ok: show_toast(self.pages, "Browsing data cleared" if ok else
                                         "Couldn't clear everything -- try again with the page closed",
                                         kind="success" if ok else "warning"))

    # ------------------------------------------------------- AdGuard ----
    def _show_adguard_panel(self):
        tab = self._current()
        host = _title_for(tab.url) if tab is not None and not tab.on_home else ""
        available = self.services.adguard_ok and not (tab is not None and tab.private)
        message = ""
        if tab is not None and tab.private:
            count = getattr(tab, "private_blocked", 0)
            message = ("AdGuard doesn't run in private tabs. The built-in block list does here: "
                       "%d ad and tracker request%s stopped in this tab." % (count, "" if count == 1 else "s"))
        elif not available:
            if self.services.error:
                message = "The browser engine isn't running, so neither is AdGuard."
            elif not browser_engine.adguard_bundled():
                message = "This build doesn't include AdGuard."
            elif getattr(self.services, "adguard_error", None):
                # Not "still starting": it won't, this session. (It said so
                # forever when loading had failed -- reported.)
                message = ("AdGuard couldn't start: %s. Restart the app to try again; pages still "
                           "load, without ad blocking." % self.services.adguard_error.rstrip("."))
            else:
                message = "AdGuard is still starting -- it takes a few seconds the first time."
        panel = AdGuardPanel(self, self._t, self._dark, host,
                             tab.adguard if tab is not None else None, available, message)
        panel.site_toggled.connect(lambda protect: self._set_site_protection(protect))
        panel.pause_toggled.connect(lambda paused: self.services.set_paused_everywhere(
            paused, lambda _r: self._after_adguard_change()))
        panel.open_settings.connect(self._open_adguard_settings)
        panel.open_log.connect(lambda: self._open_extension_page("pages/filtering-log.html"))
        panel.closed.connect(lambda: setattr(self, "_panel", None))
        self._panel = panel
        panel.open_under(self.shield_btn)
        self._poll_adguard()

    def _set_site_protection(self, protect):
        tab = self._current()
        if tab is None or tab.on_home:
            return
        status = tab.adguard or {}
        self.services.set_site_paused(tab.url, status.get("tab"), not protect,
                                      lambda _r: self._after_adguard_change())

    def _after_adguard_change(self):
        view = self.current_view()
        if view is not None and not self._cur.on_home:
            view.reload()
        QTimer.singleShot(1500, self._poll_adguard)

    def _open_adguard_settings(self):
        self._open_extension_page("pages/options.html")

    def _open_extension_page(self, path):
        if self.services.adguard_id:
            self._create_tab(url="chrome-extension://%s/%s" % (self.services.adguard_id, path), activate=True)

    # --------------------------------------------------------- engine ----
    def _on_engine_failed(self, message):
        self.missing.set_error("Details: %s" % message if message else "")
        tab = self._current()
        if tab is not None and not tab.on_home:
            self.pages.setCurrentWidget(self.missing)

    # ------------------------------------------------ session and life ----
    def _restore_session(self):
        urls, current = browser_data.load_session()
        self._closed = browser_data.load_closed()
        if urls and not browser_data.get_pref("restore_tabs", True):
            # Not reopened at start: they're one Ctrl+Shift+T away instead.
            self._closed.append({"session": urls})
            urls = []
        if not urls:
            self._create_tab(activate=True)
            return
        for url in urls:
            self._create_tab(url=url, activate=False)
        self._switch_to(self._tabs[min(current, len(self._tabs) - 1)])

    def _schedule_save(self):
        self._session_timer.start()

    def save_state(self):
        """The open (non-private) tabs, for next launch."""
        keep = [t for t in self._tabs if not t.private and not t.on_home
                and (t.url or "").startswith(("http://", "https://"))]
        current = keep.index(self._cur) if self._cur in keep else 0
        browser_data.save_session([t.url for t in keep], current)
        browser_data.save_closed([{k: v for k, v in e.items() if k != "private"}
                                  for e in self._closed if not e.get("private")])

    def showEvent(self, event):
        super().showEvent(event)
        self._browser_hidden_at = None
        self._status_fast()
        QTimer.singleShot(0, self._sync_private_look)
        cur = self._current()
        if cur is not None and cur.view is not None:
            cur.view.set_active(True)
        self.services.start()
        if browser_data.get_pref("safe_browsing", True):
            safe_browsing.start()
        tab = self._current()
        if tab is not None and tab.pending_url:
            self._load_in(tab, tab.pending_url)
        elif tab is not None and tab.on_home:
            self._show_surface(tab)
        self._status_timer.start()
        win = self.window()
        if win is not self._watched_window:
            self._watched_window = win
            win.installEventFilter(self)
        self.strip.relayout(False)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._status_timer.stop()
        QTimer.singleShot(0, self._sync_private_look)
        # Out of sight: every page down to the least memory the engine will
        # keep it in, and the clock started for putting them to sleep.
        self._browser_hidden_at = time.monotonic()
        for tab in self._tabs:
            if tab.view is not None:
                tab.view.set_active(False)
        surface = getattr(self.window(), "backdrop_surface", None)
        if surface is not None and hasattr(surface, "set_top_image"):
            surface.set_top_image(None, 0)

    def eventFilter(self, obj, event):
        # After the window comes back to the front, the page doesn't have
        # keyboard focus until clicked, and that first click is spent on
        # focus alone ("first click swallowed" -- found and fixed the same
        # way in the user's own WebView2 browser project).
        if obj is self._watched_window:
            kind = event.type()
            if kind == QEvent.Type.WindowActivate and self.isVisible():
                QTimer.singleShot(0, self._refocus_after_activate)
            if kind in (QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate):
                # The home page's moving wallpaper moves only while the window
                # is the one in use.
                self.home.set_window_active(kind == QEvent.Type.WindowActivate)
            elif kind == QEvent.Type.WindowStateChange:
                self._on_window_state(obj)
        return False

    def _on_window_state(self, win):
        """Minimized, the pages stop drawing: Qt keeps its widgets "visible"
        in a minimized window, so the engine has to be told."""
        minimized = bool(win.windowState() & Qt.WindowState.WindowMinimized)
        for view in (self.current_view(), self.home.view):
            if view is None or view.controller is None:
                continue
            try:
                view.controller.IsVisible = (not minimized) and view.isVisible()
            except Exception:   # noqa: BLE001
                pass

    def _refocus_after_activate(self):
        if not self._should_refocus_page(QCursor.pos()):
            return
        self.current_view().focus_page()

    def _should_refocus_page(self, global_pos):
        """Whether the window coming back to the front should hand the
        keyboard to the page. Not when what brought it back was a click on
        the browser's own bars, or a panel is open: handing the page focus
        makes the app lose it, and an open panel (All bookmarks, the
        bookmark you just added) closes on that -- reported as All
        bookmarks not opening, when it opened and closed in a blink."""
        view = self.current_view()
        if view is None or self._cur is None or self._cur.on_home:
            return False
        if QApplication.activePopupWidget() is not None:
            return False
        if self.chrome.isVisible() and self.chrome.rect().contains(self.chrome.mapFromGlobal(global_pos)):
            return False
        focus = QApplication.focusWidget()
        return focus is None or focus is view
