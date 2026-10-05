"""Shared services behind the Browser tab: the one WebView2 engine, AdGuard,
and the Browser tab's cookies for the downloader.

The engine starts the first time something needs it -- the Browser tab being
opened, or a download asking for the Browser tab's sign-in -- not at app
launch: the WebView2 browser process costs memory, and plenty of sessions
never open the Browser at all.

AdGuard is the official AdGuard AdBlocker (MV3 build, GPL-3.0), bundled and
loaded as a browser extension. The app talks to it the way AdGuard's own
popup does -- chrome.runtime messages sent from one of its extension pages --
through a hidden page kept open for the session ("the service page").
"""
import json
import os
import shutil
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QWidget

from app import config
from app.logging_setup import get_logger
from app.utils import browser_cookies

from . import webview2

logger = get_logger("browser_engine")

# Kept short on purpose: extension databases live deep inside it, and past
# Windows' 260-character path limit they fail to open (see webview2.py).
PROFILE_DIR = os.path.join(config.APPDATA_DIR, "wv2")
_STATE_PATH = os.path.join(config.APPDATA_DIR, "adguard.json")

# Switched on at first install, on top of AdGuard's own defaults (its Base
# filter and the one for the system language): Tracking Protection, Social
# media, URL tracking, Cookie notices, Popups, Mobile app banners, Other
# annoyances, Widgets. Measured: the defaults alone stopped 3 of 10 common
# ad/tracker scripts, this set 10 of 10.
EXTRA_FILTERS = (3, 4, 17, 18, 19, 20, 21, 22,
                 # IndianList (regional ads), and four safety lists: Online
                 # Malicious URLs, Phishing URLs, Scam Blocklist, uBlock Badware.
                 253, 208, 255, 256, 257)
# Raised whenever EXTRA_FILTERS changes, so installs configured with an older
# set pick the new lists up on their next start.
FILTERS_VERSION = 2
# "Search ads and self-promotion" -- an allowlist, it lets ads through.
DISABLED_FILTERS = (10,)


def bundled_adguard_dir():
    """The AdGuard extension as shipped (installed, it's under Program Files)."""
    if config.IS_FROZEN:
        return os.path.join(getattr(sys, "_MEIPASS", config.BASE_DIR), "adguard")
    return os.path.join(config.BASE_DIR, "vendor", "adguard-dl", "unpacked")


def adguard_bundled():
    return os.path.isfile(os.path.join(bundled_adguard_dir(), "manifest.json"))


_adguard_dir = None


def adguard_dir():
    """Where AdGuard is loaded from: a copy in the app's data folder.

    WebView2 needs to write into an extension's folder to load it, and an
    installed app's folder (Program Files) isn't writable -- every installed
    copy failed with "Access is denied" and ran without an ad blocker, while
    builds run from a writable folder worked. The copy is per AdGuard
    version, so it is made once and its path (which WebView2 ties the
    extension and its settings to) stays the same across launches, updates
    and copies of the app. Falls back to the bundled folder if it can't be
    made."""
    global _adguard_dir
    if _adguard_dir is not None:
        return _adguard_dir
    src = bundled_adguard_dir()
    try:
        with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
            version = str(json.load(f).get("version") or "0")
    except (OSError, ValueError):
        _adguard_dir = src
        return src
    root = os.path.join(config.APPDATA_DIR, "adguard")
    dst = os.path.join(root, version)
    marker = os.path.join(dst, ".copied")
    try:
        if not os.path.exists(marker):
            tmp = dst + ".tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            open(os.path.join(tmp, ".copied"), "w").close()
            shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
            logger.info("AdGuard %s copied to %s", version, dst)
        for old in os.listdir(root):
            if old != version and not old.endswith(".tmp"):
                shutil.rmtree(os.path.join(root, old), ignore_errors=True)
        _adguard_dir = dst
    except OSError:
        logger.exception("Couldn't copy AdGuard to the data folder; loading it from the app folder")
        _adguard_dir = src
    return _adguard_dir


def _load_state():
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        logger.exception("Could not save AdGuard state")


class BrowserServices(QObject):
    """One per app. Get it with services()."""

    ready = Signal()                 # engine up (pages can be created)
    failed = Signal(str)             # no WebView2 on this machine
    adguard_ready = Signal(bool)     # True once AdGuard is installed and answering
    _cookie_request = Signal(object, object)   # (sites, box) -- from any thread

    def __init__(self):
        super().__init__()
        self.engine = None
        self.error = None
        self.adguard_id = None
        self.adguard_ok = False
        self._service = None            # hidden WebView2Widget on an AdGuard page
        self._service_host = None
        self._state = _load_state()
        self.adguard_error = None    # why AdGuard didn't start, when it didn't
        self._cookie_waiters = []
        self._cookie_request.connect(self._answer_cookies)
        browser_cookies.set_provider(self._cookie_provider)

    # ---- engine ----
    def start(self):
        if self.engine is not None or self.error is not None:
            return
        self.engine = webview2.Engine(PROFILE_DIR, self)
        self.engine.ready.connect(self._on_ready)
        self.engine.failed.connect(self._on_failed)
        self.engine.start()

    def running(self):
        return self.engine is not None and self.engine.env is not None

    def _on_failed(self, msg):
        self.error = msg
        self._cookie_waiters = []
        self.failed.emit(msg)

    def _on_ready(self):
        # The service page: hidden, never shown, alive for the session.
        self._service_host = QWidget()
        self._service_host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._service = webview2.WebView2Widget(self.engine, parent=self._service_host)
        self._service.resize(800, 600)
        self._service.created.connect(self._on_service_created)
        self._service.newWindowRequested.connect(self._on_service_new_window)
        self.ready.emit()

    def _on_service_new_window(self, args):
        # AdGuard's first-run welcome page opens from its background worker;
        # it must get a real window for the install to finish.
        self.engine.adopt_extension_window(args)

    # ---- AdGuard ----
    def _on_service_created(self):
        self._flush_cookie_waiters()
        if not adguard_bundled():
            logger.info("AdGuard is not bundled with this build")
            self.adguard_ready.emit(False)
            return
        webview2.then(self._service.core.Profile.GetBrowserExtensionsAsync(),
                      self._on_extensions, self._adguard_failed)

    def _on_extensions(self, extensions):
        path = adguard_dir()
        found = [e for e in list(extensions) if "adguard" in str(e.Name or "").lower()]
        stale = [e for e in found if self._state.get("path") not in (None, path)]
        if found and not stale:
            self._use_adguard(found[0].Id, fresh=False)
            return
        for ext in stale:
            # Installed from another copy of the app (a dev checkout, an old
            # install folder) -- its ID is tied to that folder.
            try:
                ext.RemoveAsync()
            except Exception:   # noqa: BLE001
                pass
        webview2.then(self._service.core.Profile.AddBrowserExtensionAsync(path),
                      lambda ext: self._use_adguard(ext.Id, fresh=True), self._adguard_failed)

    def _adguard_failed(self, msg):
        logger.error("AdGuard could not be loaded: %s", msg)
        self.adguard_error = str(msg)
        self.adguard_ready.emit(False)

    def _use_adguard(self, ext_id, fresh):
        self.adguard_id = ext_id
        if fresh:
            self._state = {"path": adguard_dir(), "configured": False, "filters_v": 0}
            _save_state(self._state)
        self._service.loadFinished.connect(self._on_service_page)
        self._service.load("chrome-extension://%s/pages/options.html" % ext_id)

    def _on_service_page(self, ok):
        try:
            self._service.loadFinished.disconnect(self._on_service_page)
        except (RuntimeError, TypeError):
            pass
        self._wait_initialized(time.time())

    def _wait_initialized(self, since):
        """AdGuard's install sequence takes a few seconds on first run; it
        answers getIsAppInitialized once its engine is up."""
        def got(value):
            if value is True:
                self._after_initialized()
            elif time.time() - since < 45:
                QTimer.singleShot(700, lambda: self._wait_initialized(since))
            else:
                logger.warning("AdGuard did not finish starting")
                self.adguard_error = "it didn't finish starting within 45 seconds"
                self.adguard_ready.emit(False)
        self.send({"type": "getIsAppInitialized"}, got)

    def _after_initialized(self):
        # Protecting from here: AdGuard's own default filters are already
        # on. The extra set (first run only) is switched on behind it --
        # that takes AdGuard ~20 s, and the shield shouldn't wait for it.
        self.adguard_ok = True
        self.adguard_ready.emit(True)
        if not self._state.get("configured") or self._state.get("filters_v") != FILTERS_VERSION:
            self._configure_filters()

    def _configure_filters(self):
        """Switches the extra filters on, then watches AdGuard's own filter
        list until it shows them on. The replies can't be relied on: on a
        fresh install AdGuard reloads its pages once while setting up, which
        drops any call still waiting on the service page."""
        messages = [{"type": "addAndEnableFilter", "data": {"filterId": i}} for i in EXTRA_FILTERS]
        messages += [{"type": "disableFilter", "data": {"filterId": i}} for i in DISABLED_FILTERS]
        send = ("(()=>{%s.forEach(m => {m.handlerName='app'; chrome.runtime.sendMessage(m).catch(()=>{});});"
                " return true})()" % json.dumps(messages))
        self._eval(send)
        self._check_filters(time.time(), send, resent=False)

    def _check_filters(self, since, send, resent):
        def got(enabled):
            on = set(enabled or [])
            if set(EXTRA_FILTERS) <= on and not set(DISABLED_FILTERS) & on:
                self._state["configured"] = True
                self._state["filters_v"] = FILTERS_VERSION
                _save_state(self._state)
                logger.info("AdGuard configured with the extended filter set")
                return
            elapsed = time.time() - since
            if elapsed > 120:
                logger.warning("AdGuard's extra filters didn't all switch on; trying again next start")
                return
            again = not resent and elapsed > 30
            if again:
                self._eval(send)
            QTimer.singleShot(4000, lambda: self._check_filters(since, send, resent or again))

        self._eval("(async()=>{const d = await chrome.runtime.sendMessage({handlerName: 'app', type: 'getOptionsData'});"
                   " return ((d && d.filtersMetadata && d.filtersMetadata.filters) || [])"
                   ".filter(f => f.enabled).map(f => f.filterId)})()", got)

    def _eval(self, expression, callback=None):
        """Evaluates `expression` in the service page, awaiting a promise, and
        hands the JSON-able result to `callback`."""
        if self._service is None or self._service.core is None:
            if callback:
                callback(None)
            return
        params = json.dumps({"expression": expression, "awaitPromise": True, "returnByValue": True})

        def ok(raw):
            if callback is None:
                return
            try:
                callback(json.loads(raw).get("result", {}).get("value"))
            except (ValueError, AttributeError):
                callback(None)

        webview2.then(self._service.core.CallDevToolsProtocolMethodAsync("Runtime.evaluate", params),
                      ok, lambda msg: callback(None) if callback else None)

    def send(self, message, callback=None):
        message = dict(message, handlerName="app")
        self._eval("chrome.runtime.sendMessage(%s)" % json.dumps(message), callback)

    def page_status(self, url, callback):
        """{blocked, allowlisted, paused_everywhere, tab} for the tab showing
        `url`, or None."""
        if not self.adguard_ok or not url:
            callback(None)
            return
        js = """(async () => {
          const want = %s;
          const tabs = await chrome.tabs.query({});
          const strip = u => (u || '').split('#')[0];
          const t = tabs.find(t => t.url === want) || tabs.find(t => strip(t.url) === strip(want));
          if (!t) return null;
          const info = await chrome.runtime.sendMessage(
              {handlerName: 'app', type: 'getTabInfoForPopup', data: {tabId: t.id}});
          if (!info || !info.frameInfo) return null;
          const f = info.frameInfo;
          return {tab: t.id, blocked: f.totalBlockedTab || 0, total: f.totalBlocked || 0,
                  allowlisted: !!f.documentAllowlisted, paused: !!f.applicationFilteringDisabled,
                  can_toggle: !!f.canAddRemoveRule};
        })()""" % json.dumps(url)
        self._eval(js, callback)

    def set_site_paused(self, url, tab_id, paused, callback=None):
        if paused:
            self.send({"type": "addAllowlistDomainForUrl", "data": {"url": url}}, callback)
        else:
            self.send({"type": "removeAllowlistDomain", "data": {"tabId": tab_id, "tabRefresh": False}},
                      callback)

    def set_paused_everywhere(self, paused, callback=None):
        self.send({"type": "changeApplicationFilteringPaused", "data": {"state": bool(paused)}}, callback)

    # ---- cookies for the downloader ----
    def _cookie_provider(self, sites):
        """Called by browser_cookies, usually from a download thread. Asks the
        GUI thread and waits for the answer. Returns None (so the legacy
        store is read) when there is no WebView2 or no answer in time."""
        if self.error is not None:
            return None
        if threading.current_thread() is threading.main_thread():
            return None    # can't wait on ourselves; the caller falls back
        if not self.running() and not os.path.exists(
                os.path.join(PROFILE_DIR, "EBWebView", "Default", "Network", "Cookies")):
            # Nobody has signed in to anything in this browser yet: don't
            # start the whole engine in the background to find that out.
            return None
        box = {"event": threading.Event(), "rows": None}
        self._cookie_request.emit(sites, box)
        box["event"].wait(10)
        return box["rows"]

    def _answer_cookies(self, sites, box):
        def finish(rows):
            if not box["event"].is_set():
                box["rows"] = rows
                box["event"].set()

        if self._service is not None and self._service.core is not None:
            self._query_cookies(sites, finish)
            return
        # Nothing running yet: start the engine just for this, and answer
        # once the service page exists. Nine seconds, then the caller falls
        # back to the old profile's cookies (or none).
        self._cookie_waiters.append(lambda: self._query_cookies(sites, finish))
        self.start()
        if self.error is not None:
            self._cookie_waiters = []
            finish(None)
            return
        QTimer.singleShot(9000, lambda: finish(None))

    def _flush_cookie_waiters(self):
        waiters, self._cookie_waiters = self._cookie_waiters, []
        for fn in waiters:
            fn()

    def _query_cookies(self, sites, finish):
        manager = self._service.core.CookieManager
        uris = [""] if not sites else sorted(
            {u for s in sites for u in ("https://%s/" % s, "https://www.%s/" % s)})
        rows, seen = [], set()
        remaining = {"n": len(uris)}

        def collect(cookies):
            from System import DateTime, DateTimeKind
            epoch = DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)
            for c in list(cookies or []):
                key = (str(c.Domain), str(c.Name), str(c.Path))
                if key in seen:
                    continue
                seen.add(key)
                expires = 0
                if not c.IsSession:
                    try:
                        expires = int(c.Expires.ToUniversalTime().Subtract(epoch).TotalSeconds)
                    except Exception:   # noqa: BLE001
                        expires = 0
                rows.append((str(c.Domain), str(c.Name), str(c.Value), str(c.Path or "/"),
                             expires, bool(c.IsSecure)))
            step()

        def step(*_args):
            remaining["n"] -= 1
            if remaining["n"] <= 0:
                finish(rows)

        for uri in uris:
            webview2.then(manager.GetCookiesAsync(uri), collect, step)

    # CoreWebView2BrowsingDataKinds. Deliberately no "all site data" or DOM
    # storage: those take extensions' storage with them, and AdGuard would
    # lose its filter settings.
    _DATA_KINDS = {"cookies": 64, "cache": 256 | 16, "history": 4096, "downloads": 512}

    def clear_browsing_data(self, kinds, callback=None):
        """Clears the engine's own data of the given kinds ("cookies",
        "cache", "history", "downloads"). callback(ok)."""
        if self._service is None or self._service.core is None:
            if callback:
                callback(False)
            return
        value = 0
        for k in kinds:
            value |= self._DATA_KINDS.get(k, 0)
        if not value:
            if callback:
                callback(True)
            return
        import clr
        from System import Enum
        flags = Enum.ToObject(clr.GetClrType(webview2.WV.CoreWebView2BrowsingDataKinds), value)
        webview2.then(self._service.core.Profile.ClearBrowsingDataAsync(flags),
                      lambda _r: callback(True) if callback else None,
                      lambda _m: callback(False) if callback else None)


_services = None


def services():
    global _services
    if _services is None:
        _services = BrowserServices()
    return _services
