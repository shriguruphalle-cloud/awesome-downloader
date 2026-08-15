"""Entry point for Awesome Downloader.

Four tabs -- Video, Torrent, Images, History -- built on PySide6 (ui_qt/)
over the framework-agnostic app/core/ and app/utils/ modules.
"""
import sys

from PySide6.QtWidgets import QApplication

from . import config
from .logging_setup import get_logger
from .utils import settings as settings_store, single_instance

logger = get_logger("app_qt")


def main():
    # Magnet arg is read before anything else so it can be handed to an
    # already-running instance instead of starting a second one.
    magnet_arg = next((a for a in sys.argv[1:] if a.lower().startswith("magnet:")), None)

    # This guard was missing here while app/main.py has always had it, so
    # launching this build twice built a whole second window (its listener
    # thread quietly gave up on the taken port, but nothing stopped the UI
    # from coming up) -- exactly the "pile of windows" single_instance.py
    # exists to prevent, plus two libtorrent sessions fighting over the same
    # listen port. Now a second launch forwards its magnet link (or a bare
    # focus request) to the running window and exits, same as the real app.
    if single_instance.forward_to_existing(magnet_arg):
        logger.info("Another instance is already running -- forwarded and exiting.")
        return

    settings = settings_store.load_settings()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)

    # ui_qt/ is a top-level package alongside app/, not a subpackage of it
    # -- absolute import, not relative (bit us once already porting
    # video_tab.py). Imported inside main() so the QApplication above
    # exists before any widget module is touched.
    from ui_qt.history_tab import HistoryTab
    from ui_qt.images_tab import ImagesTab
    from ui_qt.main_window import MainWindow
    from ui_qt.torrent_tab import TorrentTab
    from ui_qt.video_tab import VideoTab

    win = MainWindow(dark_mode=settings.get("theme", "dark") != "light", settings=settings)
    win.add_tab(VideoTab(settings=settings), "Video")
    torrent_tab = TorrentTab(settings=settings)
    win.add_tab(torrent_tab, "Torrent")
    win.add_tab(ImagesTab(settings=settings), "Images")
    history_tab = HistoryTab(settings=settings)
    win.add_tab(history_tab, "History")
    history_tab_index = win.tabs.indexOf(history_tab)
    # HistoryTab only ever loaded download_history.json once, at __init__ --
    # anything downloaded *after* that (including every image save, per a
    # direct bug report: "image download history is not available") never
    # showed up without restarting the app. Refreshing on every switch into
    # this tab matches the real CustomTkinter app's own behavior
    # (app/main.py's _on_tab_change calls history_tab.refresh() the same way).
    win.tabs.currentChanged.connect(
        lambda i: history_tab.refresh() if i == history_tab_index else None
    )
    win.show()

    # Flushes the torrent queue's current state to torrents.json before the
    # process actually exits -- mirrors app/main.py's _on_close() for the
    # real, shipped app, which does the exact same thing before destroying
    # its root window. Nothing here called this on shutdown at all before
    # (only on add/remove), so torrents.json could end up stale by the time
    # the app was later reopened -- reported directly: "after restarting
    # the app torrent progress gets removed". aboutToQuit fires on every
    # quit path (window close, sys.exit(), Alt+F4), unlike overriding
    # closeEvent, which only catches the window-close button specifically.
    if hasattr(torrent_tab, "save_state"):
        app.aboutToQuit.connect(torrent_tab.save_state)

    # Magnet link from the command line (e.g. this build launched directly
    # with a magnet: URI as an argument) -- mirrors app/main.py's own
    # magnet_arg handling for the real, shipped app. Read at the top of
    # main(), before the single-instance check that may forward it.
    if magnet_arg:
        torrent_tab.magnet_forwarded.emit(magnet_arg)

    # A magnet link forwarded from a second launch (or from the registered
    # magnet: protocol handler) reaches the Torrent tab by emitting a
    # signal: this callback fires on a background socket thread, which
    # can't touch widgets directly, so Qt marshals it to the GUI thread.
    single_instance.start_listener(
        on_magnet=lambda uri: torrent_tab.magnet_forwarded.emit(uri),
        on_focus_request=win.focus_window,
    )

    logger.info("Starting %s", config.APP_NAME)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
