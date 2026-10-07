"""Shared setup for the test scripts in this folder.

Import this FIRST in every test, before anything from app/ or ui_qt/:

    import _support
    from _support import qapp, build_window

It isolates every file the app persists. app/config.py derives APPDATA_DIR
from LOCALAPPDATA and the default download folders from the user profile, so
pointing both at a throwaway directory *before config is imported* means no
test can read the real user's settings, queue, history or browser profile,
and none can write into them. Earlier versions of these tests redirected one
state file at a time and missed one: a test left fake URLs in the real
pending-downloads file, and the app then tried to resume them on launch.

Each test is its own process (see run_all.py), so the isolation is fresh for
every file.
"""
import atexit
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

STATE_DIR = tempfile.mkdtemp(prefix="awd-test-state-")
os.environ["LOCALAPPDATA"] = STATE_DIR
os.environ["USERPROFILE"] = STATE_DIR
# no blocklist downloads from the tests (app/core/safe_browsing.py)
os.environ["AWD_OFFLINE_LISTS"] = "1"
atexit.register(shutil.rmtree, STATE_DIR, True)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

if "app.config" in sys.modules:  # pragma: no cover -- a misordered import
    raise RuntimeError("tests/_support.py must be imported before app.config")

from app import config  # noqa: E402

assert config.APPDATA_DIR.startswith(STATE_DIR), config.APPDATA_DIR

_app = None


class _Clipboard:
    """Stands in for the system clipboard in every test. A test once put a
    fake link ("https://a.com/form") on the real Windows clipboard, and the
    user then pasted it into the real app. Nothing here may read or write
    the machine's clipboard."""

    def __init__(self):
        self._text = ""

    def text(self, *args):
        return self._text

    def setText(self, text, *args):  # noqa: N802 -- Qt's name
        self._text = str(text)

    def clear(self, *args):
        self._text = ""


_CLIPBOARD = _Clipboard()


def _isolate_clipboard():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    QGuiApplication.clipboard = staticmethod(lambda: _CLIPBOARD)
    QApplication.clipboard = staticmethod(lambda: _CLIPBOARD)


_isolate_clipboard()


# Which screen test windows open on: the second one when there is one, so
# a person watching the first isn't covered by them (AWD_TEST_SCREEN=0 puts
# them back on the main screen).
TEST_SCREEN = int(os.environ.get("AWD_TEST_SCREEN", "1"))
_screen_filter = None


def _to_test_screen(w):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QGuiApplication
    screens = QGuiApplication.screens()
    if TEST_SCREEN >= len(screens):
        return
    target = screens[TEST_SCREEN]
    current = w.screen()
    if current is target:
        return
    dst = target.availableGeometry()
    src = current.availableGeometry() if current is not None else dst
    state = w.windowState()
    if state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):
        w.setWindowState(Qt.WindowState.WindowNoState)
    if w.windowHandle() is not None:
        w.windowHandle().setScreen(target)
    at = w.frameGeometry().topLeft() - src.topLeft() + dst.topLeft()
    x = max(dst.left(), min(at.x(), dst.right() - w.frameGeometry().width()))
    y = max(dst.top(), min(at.y(), dst.bottom() - w.frameGeometry().height()))
    w.move(QPoint(x, y))
    if state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):
        w.setWindowState(state)


def qapp():
    """The one QApplication for this process, with the app's stylesheet --
    and every window it shows sent to the test screen (TEST_SCREEN)."""
    global _app, _screen_filter
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QApplication, QWidget
    _app = QApplication.instance() or QApplication(sys.argv)
    if _screen_filter is None:
        class _ToTestScreen(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                    kind = obj.windowFlags() & Qt.WindowType.WindowType_Mask
                    if kind not in (Qt.WindowType.Popup, Qt.WindowType.ToolTip):
                        try:
                            _to_test_screen(obj)
                        except Exception:   # noqa: BLE001 -- a test runs wherever, rather than not at all
                            pass
                return False
        _screen_filter = _ToTestScreen(_app)
        _app.installEventFilter(_screen_filter)
    return _app


def pump(times=2):
    """Lets queued signals and layout passes run."""
    app = qapp()
    for _ in range(times):
        app.processEvents()


def settle(ms=450):
    """Lets animations (list rows opening, page cross-fades) run to their
    end in real time before a test measures anything."""
    import time
    app = qapp()
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def settings(**overrides):
    from app.utils import settings as settings_store
    s = settings_store.load_settings()
    # A test never asks GitHub for releases unless it sets this itself.
    s["check_app_updates"] = False
    # Nor registers this app as the machine's magnet-link handler: that is
    # the real registry (HKCU), which no test may touch.
    s["magnet_handler_offered"] = True
    s.update(overrides)
    return s


def stub_network(video_tab):
    """Replaces every VideoTab method that would touch the network or start a
    real download with a recorder. Returns the recorder dict."""
    calls = {"lookups": [], "downloads": [], "images": [], "thumbs": [], "fetches": []}
    video_tab._strip_info_thread = lambda strip, url: calls["lookups"].append((strip, url))
    video_tab._download_thread = lambda *a, **k: calls["downloads"].append(a)
    video_tab._image_download_thread = lambda *a, **k: calls["images"].append(a)
    video_tab._thumb_only_thread = lambda card, url: calls["thumbs"].append((card, url))
    video_tab._fetch_thread = lambda url: calls["fetches"].append(url)
    return calls


def build_window(tabs=("video", "torrent", "images", "browser", "download", "history"),
                 size=(1006, 706), dark=True, **setting_overrides):
    """A MainWindow assembled the way app/main_qt.py assembles it, with only
    the requested tabs. Returns (window, {name: tab})."""
    qapp()
    from ui_qt.main_window import MainWindow
    from ui_qt.download_tab import DownloadTab

    st = settings(theme="dark" if dark else "light", **setting_overrides)
    win = MainWindow(dark_mode=dark, settings=st)
    made = {}
    dl = DownloadTab(settings=st)
    made["download_obj"] = dl
    for name in tabs:
        if name == "video":
            from ui_qt.video_tab import VideoTab
            made[name] = VideoTab(settings=st, download_tab=dl)
            win.add_tab(made[name], "Video")
        elif name == "torrent":
            from ui_qt.torrent_tab import TorrentTab
            made[name] = TorrentTab(settings=st)
            win.add_tab(made[name], "Torrent")
        elif name == "images":
            from ui_qt.images_tab import ImagesTab
            made[name] = ImagesTab(settings=st)
            win.add_tab(made[name], "Images")
        elif name == "music":
            from ui_qt.music_tab import MusicTab
            made[name] = MusicTab(settings=st)
            win.add_tab(made[name], "Music")
            win.set_full_bleed(made[name])
        elif name == "browser":
            from ui_qt.browser_tab import BrowserTab
            made[name] = BrowserTab(settings=st, download_tab=dl)
            win.add_tab(made[name], "Browser")
            win.set_full_bleed(made[name])
        elif name == "download":
            made[name] = dl
            win.add_tab(dl, "Download")
        elif name == "history":
            from ui_qt.history_tab import HistoryTab
            made[name] = HistoryTab(settings=st)
            win.add_tab(made[name], "History")
    win.resize(*size)
    win.show()
    pump(3)
    # the window centres itself on the main screen once shown; the test
    # screen wins (TEST_SCREEN)
    _to_test_screen(win)
    pump(2)
    return win, made


def keep_on_top(*windows):
    """Puts test windows above every other window, in the order given (the
    last ends up in front). Tests that hit-test or read what's on screen
    share it with whatever else is open: a window left over that spot made a
    real click "miss" a button and a corner read as cut too deep."""
    import ctypes
    from ctypes import wintypes
    # Typed, and HWND_TOPMOST as a real HWND(-1): passed as a plain int it
    # reached Windows as 0xFFFFFFFF -- not a window, not "topmost" -- and the
    # call quietly did nothing.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    set_pos = user32.SetWindowPos
    set_pos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        wintypes.UINT]
    set_pos.restype = wintypes.BOOL
    for w in windows:
        if not set_pos(wintypes.HWND(int(w.winId())), wintypes.HWND(-1), 0, 0, 0, 0,
                       0x0001 | 0x0002 | 0x0010):   # no move, no size, no activate
            raise OSError(ctypes.get_last_error(), "couldn't keep a test window on top")
    pump(3)


def no_modal_dialogs():
    """Makes QMessageBox's static helpers record instead of blocking. A real
    modal in a test waits for a click that never comes and hangs the run."""
    from PySide6.QtWidgets import QMessageBox
    shown = []

    def record(kind):
        def _show(*args, **kwargs):
            shown.append((kind, args[2] if len(args) > 2 else ""))
            return QMessageBox.StandardButton.Ok
        return staticmethod(_show)

    for kind in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, kind, record(kind))
    return shown


def check(condition, message):
    """assert that also survives python -O."""
    if not condition:
        raise AssertionError(message)
