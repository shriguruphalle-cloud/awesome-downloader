"""Microsoft Edge WebView2, hosted inside Qt widgets, for the Browser tab.

Why not Qt WebEngine any more: the Qt WebEngine that ships in the PySide6
packages is built without H.264 or AAC (a licensing exclusion Qt can't lift),
so on a large part of the web -- anything serving MP4 video -- the player
simply stayed black. WebView2 is the Edge engine that ships with Windows 10
and 11; it plays H.264, AAC, HEVC, VP9 and AV1, supports Chromium extensions
(which is how AdGuard runs inside it), and leaves the installer ~200 MB
lighter because the engine is already on the machine.

How it is hosted: every page is a QWidget holding an embedded QWindow
(QWidget.createWindowContainer); WebView2 draws into that QWindow's HWND.
Not a QWidget with its own native handle: asking a widget for one makes Qt
turn every ancestor *and every sibling* of it native too -- seventeen native
windows across the main window once a page existed, inside a translucent
frameless window that resizes itself on maximize and snap. The container
keeps the rest of the UI ordinary widgets. The .NET WebView2 SDK is driven
through pythonnet on the GUI thread (WebView2 is an STA COM object and must
only ever be touched from the thread that created it). Its asynchronous calls
return .NET Tasks, which are polled from the Qt event loop (_TaskPump) rather
than awaited, so nothing here blocks the UI.

Findings worth keeping (each cost a debugging session):
  * The profile folder must be short. Extensions keep IndexedDB databases
    deep inside it, and past Windows' 260-character path limit LevelDB fails
    with "Internal error opening backing store" -- AdGuard then loads zero
    rules and blocks nothing, silently.
  * AdGuard opens its welcome page with chrome.tabs.create() on first
    install and continues its setup after that. The page is given a real but
    hidden window (see Engine.adopt_extension_window).
  * A native child HWND always draws above Qt-painted siblings, so nothing
    Qt draws can overlap a page; popups (menus, the zoom slider) are
    top-level windows.
"""
import json
import os
import platform
import sys
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap, QWindow
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app import config
from app.logging_setup import get_logger

logger = get_logger("webview2")

_loaded = False
_load_error = None
_runtime_version = None
WV = None      # the Microsoft.Web.WebView2.Core namespace, once loaded


def sdk_dir():
    """The WebView2 SDK: bundled beside the app when frozen, else the copy the
    pywebview package ships (pywebview itself is not used)."""
    if config.IS_FROZEN:
        return os.path.join(getattr(sys, "_MEIPASS", config.BASE_DIR), "webview2")
    # Located, not imported: an import would make PyInstaller bundle all of
    # pywebview for two DLLs the build copies on its own.
    import importlib.util
    spec = importlib.util.find_spec("webview")
    if spec is None or not spec.origin:
        raise RuntimeError("the WebView2 SDK (from the pywebview package) isn't installed")
    return os.path.join(os.path.dirname(os.path.abspath(spec.origin)), "lib")


def _native_arch():
    machine = platform.machine().upper()
    return {"AMD64": "win-x64", "X86_64": "win-x64", "ARM64": "win-arm64",
            "X86": "win-x86", "I386": "win-x86"}.get(machine, "win-x64")


def load():
    """Loads .NET and the WebView2 SDK once. Returns (ok, version_or_error)."""
    global _loaded, _load_error, _runtime_version, WV
    if _loaded:
        return True, _runtime_version
    if _load_error:
        return False, _load_error
    if sys.platform != "win32":
        _load_error = "WebView2 is Windows-only"
        return False, _load_error
    try:
        from pythonnet import load as load_runtime
        try:
            load_runtime("netfx")
        except RuntimeError:
            pass   # already loaded by an earlier call in this process
        import clr
        base = sdk_dir()
        clr.AddReference(os.path.join(base, "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference("System.Drawing")
        import Microsoft.Web.WebView2.Core as core_ns
        core_ns.CoreWebView2Environment.SetLoaderDllFolderPath(
            os.path.join(base, "runtimes", _native_arch(), "native"))
        _runtime_version = core_ns.CoreWebView2Environment.GetAvailableBrowserVersionString()
        WV = core_ns
        _loaded = True
        logger.info("WebView2 runtime %s", _runtime_version)
        return True, _runtime_version
    except Exception as e:  # noqa: BLE001 -- any failure means "no WebView2"
        _load_error = str(e).splitlines()[0][:300] if str(e) else e.__class__.__name__
        logger.exception("WebView2 unavailable")
        return False, _load_error


def available():
    return load()[0]


# ------------------------------------------------------------ task pump ----
class _TaskPump(QObject):
    """Completes .NET Tasks on the GUI thread.

    Each task tells us when it's done (Task.ContinueWith, from a .NET
    thread; a queued signal brings it to this one), and its callback runs
    then. It used to be polled every 8 ms until it finished -- and with one
    slow call always in flight (the ad blocker's page count, every 1.5 s)
    the app woke up 50 times a second for as long as the Browser tab was
    open, doing nothing. Polling is left only as a fallback, slowing down
    the longer a task takes."""

    _finished = Signal(int)

    def __init__(self):
        super().__init__()
        self._pending = {}           # key -> (task, ok, fail, started)
        self._next = 0
        self._finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)

    def then(self, task, ok=None, fail=None):
        key = self._next
        self._next += 1
        self._pending[key] = (task, ok, fail, time.monotonic())
        try:
            from System import Action
            from System.Threading.Tasks import Task
            task.ContinueWith(Action[Task](lambda _t, k=key: self._finished.emit(k)))
        except Exception:   # noqa: BLE001 -- no continuations: poll for it
            self._schedule()

    def _on_finished(self, key):
        entry = self._pending.pop(key, None)
        if entry is not None:
            self._complete(*entry[:3])

    def _schedule(self):
        if not self._pending or self._timer.isActive():
            return
        youngest = time.monotonic() - max(e[3] for e in self._pending.values())
        self._timer.start(8 if youngest < 0.5 else 50 if youngest < 3 else 250)

    def _poll(self):
        for key, (task, ok, fail, _started) in list(self._pending.items()):
            try:
                done = task.IsCompleted
            except Exception:   # noqa: BLE001 -- a disposed task
                done = True
            if done:
                self._pending.pop(key, None)
                self._complete(task, ok, fail)
        self._schedule()

    @staticmethod
    def _complete(task, ok, fail):
        try:
            if task.IsFaulted or task.IsCanceled:
                exc = task.Exception
                inner = getattr(exc, "InnerException", None) if exc else None
                msg = str(inner.Message if inner is not None else (exc or "cancelled"))
                if fail:
                    fail(msg)
                else:
                    logger.warning("WebView2 call failed: %s", msg)
            elif ok:
                try:
                    result = task.Result
                except AttributeError:   # a plain Task has no result
                    result = None
                ok(result)
        except Exception:
            logger.exception("WebView2 callback failed")


_pump = None


def then(task, ok=None, fail=None):
    global _pump
    if _pump is None:
        _pump = _TaskPump()
    _pump.then(task, ok, fail)


# ---------------------------------------------------------------- engine ----
class Engine(QObject):
    """One WebView2 environment (browser process + profile) for the app.

    Pages ask for a controller through create_controller(); calls made before
    the environment exists are queued and run once it does."""

    ready = Signal()
    failed = Signal(str)

    def __init__(self, user_data_dir, parent=None):
        super().__init__(parent)
        self.user_data_dir = user_data_dir
        self.env = None
        self.error = None
        self._queue = []
        self._hidden_windows = []
        self.extension_ids = {}     # name -> id, once known

    def start(self):
        ok, info = load()
        if not ok:
            self.error = info
            self.failed.emit(info)
            return
        os.makedirs(self.user_data_dir, exist_ok=True)
        opts = WV.CoreWebView2EnvironmentOptions()
        opts.AreBrowserExtensionsEnabled = True
        # A ceiling on the page cache, which otherwise grows with browsing.
        opts.AdditionalBrowserArguments = "--disk-cache-size=%d" % (200 * 1024 * 1024)
        then(WV.CoreWebView2Environment.CreateAsync(None, self.user_data_dir, opts),
             self._on_env, self._on_env_failed)

    def _on_env(self, env):
        self.env = env
        queued, self._queue = self._queue, []
        for fn in queued:
            fn()
        self.ready.emit()

    def _on_env_failed(self, msg):
        self.error = msg
        logger.error("WebView2 environment failed: %s", msg)
        self.failed.emit(msg)
        self._queue = []

    def when_ready(self, fn):
        if self.env is not None:
            fn()
        elif self.error is None:
            self._queue.append(fn)

    def create_controller(self, hwnd, private, ok, fail, background=None):
        """`background` (a QColor) is what the page shows before its first
        paint -- without it every new tab flashes white on a dark window."""
        def go():
            from System import IntPtr
            options = self.env.CreateCoreWebView2ControllerOptions()
            if private:
                options.IsInPrivateModeEnabled = True
            if background is not None:
                try:
                    from System.Drawing import Color
                    options.DefaultBackgroundColor = Color.FromArgb(
                        255, background.red(), background.green(), background.blue())
                except Exception:   # noqa: BLE001 -- older runtimes
                    pass
            then(self.env.CreateCoreWebView2ControllerAsync(IntPtr(hwnd), options), ok, fail)
        self.when_ready(go)

    # A window for an extension page nobody should see (AdGuard's welcome
    # page on first install). The extension's setup continues only once the
    # tab it asked for exists, so refusing the request is not an option.
    def adopt_extension_window(self, args, close_after_ms=6000):
        deferral = args.GetDeferral()
        host = QWidget()
        host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        host.resize(800, 600)
        entry = {"host": host}
        self._hidden_windows.append(entry)

        def created(ctl):
            entry["ctl"] = ctl
            ctl.IsVisible = False
            try:
                args.NewWindow = ctl.CoreWebView2
                args.Handled = True
            finally:
                deferral.Complete()
            QTimer.singleShot(close_after_ms, lambda: self._close_hidden(entry))

        def failed(msg):
            deferral.Complete()
            self._close_hidden(entry)

        self.create_controller(int(host.winId()), False, created, failed)

    def _close_hidden(self, entry):
        ctl = entry.pop("ctl", None)
        if ctl is not None:
            try:
                ctl.Close()
            except Exception:   # noqa: BLE001
                pass
        if entry in self._hidden_windows:
            self._hidden_windows.remove(entry)


# ------------------------------------------------------------------ page ----
class WebView2Widget(QWidget):
    """One browser page. Emits Qt signals shaped like QWebEngineView's so the
    Browser tab can treat it as a view."""

    created = Signal()
    urlChanged = Signal(str)
    titleChanged = Signal(str)
    loadStarted = Signal()
    loadFinished = Signal(bool)
    iconChanged = Signal(object)          # QPixmap
    historyChanged = Signal()
    audibleChanged = Signal(bool)
    mutedChanged = Signal(bool)
    fullScreenChanged = Signal(bool)
    zoomChanged = Signal(float)
    webMessage = Signal(object)           # parsed JSON from chrome.webview.postMessage
    newWindowRequested = Signal(object)   # the .NET event args (Uri, deferral...)
    downloadStarting = Signal(object)     # the .NET event args
    contextMenuRequested = Signal(object) # the .NET event args
    acceleratorKey = Signal(object)       # the .NET event args
    externalUri = Signal(object)          # LaunchingExternalUriScheme args (magnet: ...)
    windowCloseRequested = Signal()       # the page called window.close()
    loadProgress = Signal(int)            # 0-100, by navigation stage
    processFailed = Signal(str)
    navigationBlocked = Signal(str, object)   # a page stopped by nav_guard: (address, why)

    def __init__(self, engine, private=False, parent=None, background=None):
        super().__init__(parent)
        self.engine = engine
        self.private = private
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.host_window = QWindow()
        self.host_window.create()
        self._container = QWidget.createWindowContainer(self.host_window, self)
        self._container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._container)
        self.host_window.widthChanged.connect(lambda _w: self._sync_bounds())
        self.host_window.heightChanged.connect(lambda _h: self._sync_bounds())
        self.controller = None
        self.core = None
        self._pending = []           # calls made before the page exists
        self._startup_scripts = []
        self._script_tasks = []
        self._background = QColor(background) if background is not None else QColor(5, 10, 24)
        self._url = ""
        self._title = ""
        self._closed = False
        self._watch_window = None
        self.nav_uri = ""
        # Called with each address the page is about to load; anything it
        # returns (truthy) stops the load -- the Browser tab's dangerous-site
        # check. Frames are stopped silently; a page says why.
        self.nav_guard = None
        engine.create_controller(int(self.host_window.winId()), private, self._on_created, self._on_failed,
                                 background=self._background)

    # ---- lifecycle ----
    def _on_failed(self, msg):
        logger.error("WebView2 page creation failed: %s", msg)
        self.processFailed.emit(msg)

    def _on_created(self, ctl):
        if self._closed:
            ctl.Close()
            return
        self.controller = ctl
        self.core = core = ctl.CoreWebView2
        self._apply_background()
        self._sync_bounds()
        ctl.IsVisible = self.isVisible()
        settings = core.Settings
        settings.IsStatusBarEnabled = True
        settings.AreDefaultContextMenusEnabled = True
        settings.IsZoomControlEnabled = True
        settings.AreBrowserAcceleratorKeysEnabled = True
        settings.AreDevToolsEnabled = True
        settings.IsBuiltInErrorPageEnabled = True
        for name in ("IsPasswordAutosaveEnabled", "IsGeneralAutofillEnabled", "IsSwipeNavigationEnabled"):
            try:
                setattr(settings, name, True)
            except Exception:   # noqa: BLE001 -- older runtimes
                pass

        core.SourceChanged += lambda s, a: self._emit_url()
        core.DocumentTitleChanged += lambda s, a: self._emit_title()
        core.NavigationStarting += lambda s, a: self._navigation_starting(a)
        core.FrameNavigationStarting += lambda s, a: self._frame_navigation_starting(a)
        core.ContentLoading += lambda s, a: self._stage(40)
        core.DOMContentLoaded += lambda s, a: self._stage(72)
        core.NavigationCompleted += lambda s, a: self._completed(bool(a.IsSuccess))
        core.HistoryChanged += lambda s, a: self.historyChanged.emit()
        core.FaviconChanged += lambda s, a: self._fetch_icon()
        core.IsDocumentPlayingAudioChanged += lambda s, a: self.audibleChanged.emit(
            bool(core.IsDocumentPlayingAudio))
        core.IsMutedChanged += lambda s, a: self.mutedChanged.emit(bool(core.IsMuted))
        core.ContainsFullScreenElementChanged += lambda s, a: self.fullScreenChanged.emit(
            bool(core.ContainsFullScreenElement))
        core.WebMessageReceived += self._on_message
        core.NewWindowRequested += lambda s, a: self.newWindowRequested.emit(a)
        core.DownloadStarting += lambda s, a: self.downloadStarting.emit(a)
        core.ContextMenuRequested += lambda s, a: self.contextMenuRequested.emit(a)
        core.WindowCloseRequested += lambda s, a: self.windowCloseRequested.emit()
        core.ProcessFailed += lambda s, a: self.processFailed.emit(str(a.ProcessFailedKind))
        try:
            core.LaunchingExternalUriScheme += lambda s, a: self.externalUri.emit(a)
        except Exception:   # noqa: BLE001 -- older runtimes
            pass
        ctl.ZoomFactorChanged += lambda s, a: self.zoomChanged.emit(float(ctl.ZoomFactor))
        ctl.AcceleratorKeyPressed += lambda s, a: self.acceleratorKey.emit(a)
        ctl.MoveFocusRequested += lambda s, a: None

        self._script_tasks = [core.AddScriptToExecuteOnDocumentCreatedAsync(js)
                              for js in self._startup_scripts]
        self._watch_parent_window()
        queued, self._pending = self._pending, []
        for fn in queued:
            fn()
        self.created.emit()

    def when_scripts_ready(self, fn):
        """Runs `fn` once the page exists and every startup script is
        registered -- a page handed to window.open() must not start its first
        document before then, or that document runs without them."""
        def check():
            pending = [t for t in self._script_tasks if not t.IsCompleted]
            if not pending:
                fn()
            else:
                then(pending[0], lambda _r: check(), lambda _m: check())
        self._later(check)

    def _navigation_starting(self, args):
        try:
            self.nav_uri = str(args.Uri or "")
        except Exception:   # noqa: BLE001
            self.nav_uri = ""
        if self.nav_guard is not None and self.nav_uri:
            hit = self.nav_guard(self.nav_uri)
            if hit:
                args.Cancel = True
                self.navigationBlocked.emit(self.nav_uri, hit)
                return
        self._stage(12, started=True)

    def _frame_navigation_starting(self, args):
        if self.nav_guard is None:
            return
        try:
            uri = str(args.Uri or "")
        except Exception:   # noqa: BLE001
            return
        if uri and self.nav_guard(uri):
            args.Cancel = True

    def _stage(self, value, started=False):
        if started:
            self.loadStarted.emit()
        self.loadProgress.emit(value)

    def _completed(self, ok):
        self.loadProgress.emit(100)
        self.loadFinished.emit(ok)

    def _later(self, fn):
        if self._closed:
            return
        if self.core is not None:
            self._guarded(fn)
        else:
            self._pending.append(fn)

    def _call(self, fn):
        """Runs `fn(controller)` if the page is still alive. WebView2 can
        dispose a controller on its own -- when its host window goes away
        during shutdown -- without close_page() having run; touching it then
        raised "members cannot be accessed after the WebView2 control is
        disposed" (closing the window on a private tab recoloured the page
        after that). Such a page is marked closed, and nothing touches it
        again. Returns whether the call went through."""
        ctl = self.controller
        if ctl is None:
            return False
        return self._guarded(lambda: fn(ctl))

    def _guarded(self, fn):
        # The same for calls on the page itself: the home page was still sent
        # "window inactive" as the window closed, after its page was disposed.
        try:
            fn()
            return True
        except Exception as exc:   # noqa: BLE001
            if "disposed" in str(exc) or "InvalidOperation" in type(exc).__name__:
                logger.info("A page's controller was already disposed; dropping it")
                self._closed = True
                self.controller = self.core = None
                return False
            raise

    def close_page(self):
        self._closed = True
        if self.controller is not None:
            try:
                self.controller.Close()
            except Exception:   # noqa: BLE001
                pass
        self.controller = self.core = None

    # ---- geometry: WebView2 bounds are physical pixels in the host HWND ----
    def _device_size(self):
        win = self.host_window
        dpr = win.devicePixelRatio() or self.devicePixelRatioF()
        w = win.width() or self.width()
        h = win.height() or self.height()
        return max(1, round(w * dpr)), max(1, round(h * dpr))

    def _sync_bounds(self):
        if self.controller is None:
            return
        from System.Drawing import Rectangle
        w, h = self._device_size()
        self._call(lambda c: setattr(c, "Bounds", Rectangle(0, 0, w, h)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_bounds()
        self._place_soon()

    def showEvent(self, event):
        super().showEvent(event)
        if self._call(lambda c: setattr(c, "IsVisible", True)):
            self._sync_bounds()
        self._place_soon()

    # ---- keeping the page where its widget is ----
    # The page is a native window, positioned by Qt's window container from
    # this widget's place in the window. It was once seen drawn well off to
    # the side after switching tabs -- the native window left where the page
    # area had been on screen before the window was maximized (reported, with
    # the page offset by exactly the restored window's position). Whatever
    # left it there, the page now checks where its window really is whenever
    # it is shown, resized or its window moves, and puts it back.
    def _place_soon(self):
        QTimer.singleShot(0, self._place_native)
        QTimer.singleShot(180, self._place_native)

    def _place_native(self):
        if sys.platform != "win32" or self._closed or not self.isVisible():
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            top = self.window()
            hwnd = int(self.host_window.winId())
            top_hwnd = int(top.winId())
            if not hwnd or not top_hwnd or hwnd == top_hwnd:
                return
            dpr = self.devicePixelRatioF() or 1.0
            origin = wintypes.POINT(0, 0)
            user32.ClientToScreen(top_hwnd, ctypes.byref(origin))
            at = self._container.mapTo(top, QPoint(0, 0))
            want = (origin.x + round(at.x() * dpr), origin.y + round(at.y() * dpr),
                    max(1, round(self._container.width() * dpr)), max(1, round(self._container.height() * dpr)))
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            have = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            if all(abs(a - b) <= 1 for a, b in zip(have, want)):
                return
            # SetWindowPos takes the parent's client coordinates (or the
            # screen's, for a window with no parent).
            pt = wintypes.POINT(want[0], want[1])
            parent = user32.GetParent(hwnd)
            if parent:
                user32.ScreenToClient(parent, ctypes.byref(pt))
            else:
                logger.warning("A page's window has no parent window; placing it on screen")
            logger.warning("Page window was at %s, not %s: moved back", have, want)
            user32.SetWindowPos(hwnd, 0, pt.x, pt.y, want[2], want[3],
                                0x0004 | 0x0010)        # SWP_NOZORDER | SWP_NOACTIVATE
            self._sync_bounds()
            if self.controller is not None:
                self.controller.NotifyParentWindowPositionChanged()
        except Exception:   # noqa: BLE001 -- never let a check break the page
            logger.exception("Couldn't check the page window's position")

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.controller is not None:
            try:
                self.controller.IsVisible = False
            except Exception:   # noqa: BLE001
                pass

    def _watch_parent_window(self):
        """Dropdowns and other popups WebView2 draws are positioned from the
        top-level window's position, which it only learns when told."""
        win = self.window()
        if win is not None and win is not self._watch_window:
            self._watch_window = win
            win.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._watch_window and event.type() in (QEvent.Type.Move, QEvent.Type.Resize,
                                                          QEvent.Type.WindowStateChange):
            if self.controller is not None:
                try:
                    self.controller.NotifyParentWindowPositionChanged()
                except Exception:   # noqa: BLE001
                    pass
            if self.isVisible():
                self._place_soon()
        return False

    def focus_page(self):
        """Hand keyboard focus to the page. Without this the first click after
        switching tabs or windows only moves focus and never reaches the page
        (WebView2's "first click swallowed")."""
        if self.controller is not None:
            try:
                self.controller.MoveFocus(WV.CoreWebView2MoveFocusReason.Programmatic)
            except Exception:   # noqa: BLE001
                pass

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_page()

    # ---- appearance ----
    def set_background(self, color):
        self._background = QColor(color)
        self._apply_background()

    def _apply_background(self):
        if self.controller is None:
            return
        from System.Drawing import Color
        bg = self._background
        self._call(lambda c: setattr(c, "DefaultBackgroundColor", Color.FromArgb(255, bg.red(), bg.green(), bg.blue())))

    def set_color_scheme(self, dark):
        def go():
            scheme = WV.CoreWebView2PreferredColorScheme
            self.core.Profile.PreferredColorScheme = scheme.Dark if dark else scheme.Light
        self._later(go)

    def map_host(self, host, folder):
        """Serves `folder` at https://<host>/ in this page (WebView2's
        virtual host mapping) -- how the app's own pages load."""
        def go():
            kind = WV.CoreWebView2HostResourceAccessKind.Allow
            self.core.SetVirtualHostNameToFolderMapping(host, folder, kind)
        self._later(go)

    def configure(self, **flags):
        """Sets CoreWebView2Settings flags by name, e.g. IsZoomControlEnabled=False."""
        def go():
            settings = self.core.Settings
            for name, value in flags.items():
                try:
                    setattr(settings, name, value)
                except Exception:   # noqa: BLE001 -- not on this runtime
                    pass
        self._later(go)

    def post_json(self, obj):
        """Hands `obj` to the page (window.chrome.webview 'message' event)."""
        text = json.dumps(obj)
        self._later(lambda: self.core.PostWebMessageAsJson(text))

    def add_startup_script(self, js):
        """Runs `js` at document creation in every future page and frame."""
        self._startup_scripts.append(js)
        if self.core is not None:
            self._script_tasks.append(self.core.AddScriptToExecuteOnDocumentCreatedAsync(js))

    # ---- navigation ----
    def load(self, url):
        self._url = url
        self._later(lambda: self.core.Navigate(url))

    def url(self):
        return self._url

    def title(self):
        return self._title

    def _emit_url(self):
        self._url = str(self.core.Source or "")
        self.urlChanged.emit(self._url)

    def _emit_title(self):
        self._title = str(self.core.DocumentTitle or "")
        self.titleChanged.emit(self._title)

    def back(self):
        self._later(lambda: self.core.GoBack() if self.core.CanGoBack else None)

    def forward(self):
        self._later(lambda: self.core.GoForward() if self.core.CanGoForward else None)

    def can_go_back(self):
        return bool(self.core is not None and self.core.CanGoBack)

    def can_go_forward(self):
        return bool(self.core is not None and self.core.CanGoForward)

    def reload(self):
        self._later(lambda: self.core.Reload())

    def stop(self):
        self._later(lambda: self.core.Stop())

    # ---- page state ----
    def set_zoom(self, factor):
        self._later(lambda: setattr(self.controller, "ZoomFactor", float(factor)))

    def zoom(self):
        box = []
        self._call(lambda c: box.append(float(c.ZoomFactor)))
        return box[0] if box else 1.0

    def set_muted(self, muted):
        self._later(lambda: setattr(self.core, "IsMuted", bool(muted)))

    def run_js(self, js, callback=None):
        def go():
            task = self.core.ExecuteScriptAsync(js)
            if callback is not None:
                then(task, lambda result: callback(json.loads(result) if result else None))
        self._later(go)

    def evaluate(self, expression, callback, gesture=False):
        """Like run_js, but waits for a promise to settle: callback(value)
        with the JSON-able result, or None if it threw. gesture=True runs it
        as if the person had just clicked in the page -- what calls such as
        requestPictureInPicture() insist on."""
        def go():
            params = json.dumps({"expression": expression, "awaitPromise": True, "returnByValue": True,
                                 "userGesture": bool(gesture)})

            def ok(raw):
                try:
                    reply = json.loads(raw)
                except (TypeError, ValueError):
                    reply = {}
                callback(None if "exceptionDetails" in reply else reply.get("result", {}).get("value"))
            then(self.core.CallDevToolsProtocolMethodAsync("Runtime.evaluate", params), ok,
                 lambda _msg: callback(None))
        self._later(go)

    def open_devtools(self):
        self._later(lambda: self.core.OpenDevToolsWindow())

    def print_page(self):
        self._later(lambda: self.core.ShowPrintUI())

    def save_page_as(self):
        self._later(lambda: then(self.core.ShowSaveAsUIAsync(), None, lambda m: None))

    def open_task_manager(self):
        self._later(lambda: self.core.OpenTaskManagerWindow())

    def find_in_page(self):
        """The engine's own find bar, the one Ctrl+F opens inside a page."""
        def go():
            opts = self.engine.env.CreateFindOptions()
            opts.SuppressDefaultFindDialog = False
            opts.ShouldHighlightAllMatches = True
            then(self.core.Find.StartAsync(opts), None, lambda m: None)
        self._later(go)

    def set_active(self, active):
        """Hidden tabs ask the engine to keep less of themselves in memory."""
        if self.core is None:
            return
        try:
            level = WV.CoreWebView2MemoryUsageTargetLevel
            self.core.MemoryUsageTargetLevel = level.Normal if active else level.Low
        except Exception:   # noqa: BLE001
            pass

    def suspend(self):
        """Puts a hidden page to sleep (scripts and timers stop). It wakes by
        itself when shown or navigated."""
        if self.core is None or self.isVisible() or self.is_suspended():
            return
        try:
            then(self.core.TrySuspendAsync(), None, lambda m: None)
        except Exception:   # noqa: BLE001
            pass

    def is_suspended(self):
        try:
            return bool(self.core is not None and self.core.IsSuspended)
        except Exception:   # noqa: BLE001
            return False

    def _on_message(self, sender, args):
        try:
            payload = json.loads(args.WebMessageAsJson)
        except Exception:   # noqa: BLE001
            return
        self.webMessage.emit(payload)

    def _fetch_icon(self):
        core = self.core
        if core is None:
            return

        def got(stream):
            if stream is None:
                return
            try:
                from System.IO import MemoryStream
                buf = MemoryStream()
                stream.CopyTo(buf)
                data = bytes(buf.ToArray())
            except Exception:   # noqa: BLE001
                return
            img = QImage.fromData(data)
            if not img.isNull():
                self.iconChanged.emit(QPixmap.fromImage(img))

        then(core.GetFaviconAsync(WV.CoreWebView2FaviconImageFormat.Png), got,
             lambda msg: None)
