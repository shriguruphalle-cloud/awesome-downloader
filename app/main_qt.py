"""Entry point for Awesome Downloader.

Six tabs -- Video, Torrent, Images, Browser, Download, History -- built on
PySide6 (ui_qt/) over the framework-agnostic app/core/ and app/utils/
modules.
"""
import os
import sys

from PySide6.QtWidgets import QApplication

from . import config
from .logging_setup import get_logger
from .utils import download_history, settings as settings_store, single_instance

logger = get_logger("app_qt")


def main():
    # A build check, not a way to start the app (see app/self_test.py).
    if "--self-test" in sys.argv[1:]:
        from . import self_test
        args = sys.argv[1:]
        i = args.index("--self-test")
        out = args[i + 1] if i + 1 < len(args) else os.path.join(config.APPDATA_DIR, "self-test.json")
        sys.exit(self_test.run(out))

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
    single_instance.hold_running_mutex()

    settings = settings_store.load_settings()

    # History, bookmarks and queues are encrypted on disk with Windows' own
    # data protection (Settings > Your data); older plain files are encrypted
    # now rather than at their next save.
    from app.utils import secure_store
    secure_store.ENCRYPT = bool(settings.get("encrypt_data", True))
    try:
        secure_store.migrate()
    except Exception:   # noqa: BLE001 -- never a reason not to start
        logger.exception("Couldn't encrypt saved data")

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)

    # ui_qt/ is a top-level package alongside app/, not a subpackage of it
    # -- absolute import, not relative (bit us once already porting
    # video_tab.py). Imported inside main() so the QApplication above
    # exists before any widget module is touched.
    from ui_qt.browser_tab import BrowserTab
    from ui_qt.dialogs import message
    from ui_qt.download_tab import DownloadTab
    from ui_qt.history_tab import HistoryTab
    from ui_qt.images_tab import ImagesTab
    from ui_qt.main_window import MainWindow
    from ui_qt.music_tab import MusicTab
    from ui_qt.torrent_tab import TorrentTab
    from ui_qt.video_tab import VideoTab

    # Every QMessageBox.warning/critical/... in the app becomes the glass
    # dialog instead of Windows' own box.
    message.install()

    win = MainWindow(dark_mode=settings.get("theme", "dark") != "light", settings=settings)
    win.quit_on_close = True

    # Constructed before Video/Browser -- both need this reference (every
    # download either one starts gets its progress card here, not in
    # either tab's own space; direct feedback settled on this after both
    # "downloads live under Video" and "downloads live under Browser" read
    # as wrong once actually seen).
    download_tab = DownloadTab(settings=settings)

    video_tab = VideoTab(settings=settings, download_tab=download_tab)
    win.add_tab(video_tab, "Video")
    torrent_tab = TorrentTab(settings=settings)
    win.add_tab(torrent_tab, "Torrent")
    images_tab = ImagesTab(settings=settings)
    win.add_tab(images_tab, "Images")
    # An open music player: free music from open libraries, or YouTube
    # Music, with no account (ui_qt/music_tab.py).
    music_tab = MusicTab(settings=settings)
    win.add_tab(music_tab, "Music")
    # its pages are painted edge to edge in their album's colours
    win.set_full_bleed(music_tab)

    browser_tab = BrowserTab(settings=settings, download_tab=download_tab)
    win.add_tab(browser_tab, "Browser")
    # "Sign in inside this app" from either tab's sign-in dialog.
    def _sign_in_at(url):
        win.tabs.setCurrentIndex(win.tabs.indexOf(browser_tab))
        browser_tab.open_for_sign_in(url)

    images_tab.open_browser_requested.connect(_sign_in_at)
    # signed in there for a link the Images tab needed it for: back here, and it fetches again
    images_tab.signed_in_again.connect(lambda: win.tabs.setCurrentWidget(images_tab))
    video_tab.open_browser_requested.connect(_sign_in_at)
    # The browser renders a whole web page; app margins around it are just a
    # frame of dead chrome, so this tab runs edge to edge.
    win.set_full_bleed(browser_tab)
    # Added as a tab here (between Browser and History) even though it was
    # constructed earlier -- both Video and Browser needed the instance
    # itself before this point, but its position in the tab strip is
    # independent of when it was built.
    win.add_tab(download_tab, "Download")
    download_tab_index = win.tabs.indexOf(download_tab)
    # A dot on the Download tab's pill whenever a job starts while looking
    # at some other tab -- cleared automatically the moment that tab is
    # actually switched into (win._sync_island_selection already does that
    # clear on every tab change, add_tab's own click wiring runs through it).
    download_tab.job_started.connect(
        lambda: win.set_tab_badge(download_tab_index, win.tabs.currentIndex() != download_tab_index)
    )
    # A job *finishing* also needs to (re-)badge -- visiting Download while a
    # job is still running clears the start badge, and without this a later
    # completion while looking at some other tab would be silent.
    download_tab.job_finished.connect(
        lambda: win.set_tab_badge(download_tab_index, win.tabs.currentIndex() != download_tab_index)
    )
    video_tab_index = win.tabs.indexOf(video_tab)

    def _open_in_video_tab(url):
        # Adds the page to the Video tab's queue rather than driving a
        # single shared form. The Video tab now stacks fetched links, so
        # hitting the browser's download button several times lines them
        # all up instead of each one replacing the last.
        win.tabs.setCurrentIndex(video_tab_index)
        video_tab.queue_url(url)

    # The overlay button (inside the embedded page) hands its page URL to
    # the Video tab's own fetch flow. Direct file downloads (the browser's
    # own download interception) get their progress card from download_tab
    # directly -- no wiring needed here for those.
    browser_tab.open_in_video_tab.connect(_open_in_video_tab)
    browser_tab.media_found.connect(
        lambda title, entries: (win.tabs.setCurrentIndex(video_tab_index), video_tab.stack_found(title, entries)))
    browser_tab.fullscreen_requested.connect(win.set_video_fullscreen)

    # A magnet link opened in the browser, or a .torrent it downloaded, goes
    # to the Torrent tab; "Show" on a download toast goes to Download.
    torrent_tab_index = win.tabs.indexOf(torrent_tab)

    def _to_torrent_tab(add):
        win.tabs.setCurrentIndex(torrent_tab_index)
        add()

    browser_tab.magnet_requested.connect(
        lambda uri: _to_torrent_tab(lambda: torrent_tab.magnet_forwarded.emit(uri)))
    browser_tab.torrent_file_requested.connect(
        lambda path: _to_torrent_tab(lambda: torrent_tab.add_torrent_path(path)))
    browser_tab.show_downloads_requested.connect(
        lambda: win.tabs.setCurrentIndex(win.tabs.indexOf(download_tab)))

    # The paste boxes hand each other whatever isn't theirs: an image link
    # pasted into Video opens in Images, a reel pasted into Images opens in
    # Video, a magnet goes to Torrent (ui_qt/link_routing.py).
    from ui_qt import link_routing
    link_routing.wire(win, video_tab, images_tab, torrent_tab)

    history_tab = HistoryTab(settings=settings)
    win.add_tab(history_tab, "History")
    history_tab_index = win.tabs.indexOf(history_tab)
    # download_history.add_entry() has five separate call sites (video/audio
    # download, image save, torrent completion, browser direct download),
    # none of which otherwise have a way to reach the tab strip -- routing
    # all of them through this one listener badges History for every one of
    # them, not just the paths that happened to already have a signal.
    # entry_added.emit is itself thread-safe to call from a background
    # thread (images_tab.py's own download thread calls add_entry()
    # directly) -- same cross-thread pattern this codebase already relies on
    # for every progress_hook -> _progress_sig.emit() call.
    download_history.register_listener(history_tab.entry_added.emit)
    history_tab.entry_added.connect(
        lambda: win.set_tab_badge(history_tab_index, win.tabs.currentIndex() != history_tab_index)
    )
    # HistoryTab only ever loaded download_history.json once, at __init__ --
    # anything downloaded *after* that (including every image save, per a
    # direct bug report: "image download history is not available") never
    # showed up without restarting the app. Refreshing on every switch into
    # this tab matches the real CustomTkinter app's own behavior
    # (app/main.py's _on_tab_change calls history_tab.refresh() the same way).
    win.tabs.currentChanged.connect(
        lambda i: history_tab.refresh() if i == history_tab_index else None
    )
    # The app opens on the tab it was closed on.
    from app.utils import ui_state
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    if ui_state.get("last_tab") in labels:
        win.tabs.setCurrentIndex(labels.index(ui_state.get("last_tab")))
    win.tabs.currentChanged.connect(lambda i: ui_state.put("last_tab", win.tabs.tabText(i)))
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
    if hasattr(torrent_tab, "shutdown"):
        app.aboutToQuit.connect(torrent_tab.shutdown)
    elif hasattr(torrent_tab, "save_state"):
        app.aboutToQuit.connect(torrent_tab.save_state)
    # Same idea for in-progress video/audio downloads: flush whatever's
    # still running to pending_downloads.json so it resumes (via yt-dlp's
    # own .part-file Range-request resume) instead of restarting from
    # scratch the next time the app opens -- reported directly ("if a
    # download is happening and app closes or relaunches it must continue
    # the download").
    app.aboutToQuit.connect(video_tab.save_state)
    # The Browser's open tabs come back next launch.
    app.aboutToQuit.connect(browser_tab.save_state)
    # Installers downloaded for earlier updates (about 100 MB each) aren't
    # needed once the app has started -- the update they were for is done.
    # Cleared at start-up, before any new update could be downloading; one
    # still held by a just-finished installer goes next time.
    import threading
    from app.utils import updater
    threading.Thread(target=updater.cleanup, name="update-cleanup", daemon=True).start()

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
