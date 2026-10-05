"""Check for Updates dialog.

Reports installed-vs-latest for yt-dlp (the one that matters: sites change
constantly, yt-dlp ships near-daily fixes, and a stale copy doesn't error
out cleanly -- downloads just start failing or silently pick the wrong
format, with nothing in the UI to explain why) plus ffmpeg and libtorrent
status for completeness. The network check runs on a background thread so
it can't freeze the window; results come back via a signal, the same
pattern TorrentTab's libtorrent installer already uses.
"""
from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout

from app import config
from app.core import update_checker
from app.logging_setup import get_logger

from ..widgets import make_card
from .base import CinematicDialog, button_row, header
from ..widgets.button import Button

logger = get_logger("update_dialog")


class _CheckWorker(QThread):
    done = Signal(dict)

    def run(self):
        result = {}
        # The app itself first: a newer release on GitHub. latest_release()
        # is silent on every failure, so None here means "couldn't tell".
        try:
            from app.utils import app_update
            result["app_release"] = app_update.latest_release()
        except Exception:
            logger.exception("App release check failed")
            result["app_release"] = None
        try:
            result["yt_dlp_installed"] = update_checker.installed_yt_dlp_version()
        except Exception:
            logger.exception("Failed to read installed yt-dlp version")
            result["yt_dlp_installed"] = None
        try:
            result["yt_dlp_latest"] = update_checker.latest_yt_dlp_version()
            result["yt_dlp_error"] = None
        except Exception as e:
            result["yt_dlp_latest"] = None
            result["yt_dlp_error"] = str(e)
        result["ffmpeg_version"], result["ffmpeg_available"] = update_checker.ffmpeg_status()
        result["libtorrent_version"], result["libtorrent_available"] = update_checker.libtorrent_status()
        self.done.emit(result)


class _UpgradeWorker(QThread):
    done = Signal(bool, str)

    def run(self):
        try:
            update_checker.upgrade_yt_dlp()
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e))


# A worker outlives the dialog that started it, deliberately.
#
# Both workers used to be parented to the dialog. Closing the dialog while a
# check was still in flight therefore destroyed a QThread that was still
# running -- and Qt does not raise for that, it calls std::terminate and the
# whole application dies on the spot, with no traceback. Since the check is a
# network call to pypi.org, "still running when you close it" was the normal
# case, not an edge case: closing the Updates panel took the app with it.
#
# Parentless workers are not destroyed with the dialog, and this set holds the
# only remaining reference so Python cannot garbage-collect one mid-run
# either. Each drops itself when it finishes.
_LIVE_WORKERS = set()


def wait_for_workers(timeout_ms=3000):
    """Lets any in-flight check finish before the process goes away.

    Detaching the workers stops the *dialog* closing from destroying a
    running thread, but it does not help at shutdown: whatever is still
    running when the interpreter tears down gets destroyed anyway, and that
    is the same std::terminate. Waiting here is the other half of the fix --
    wired to aboutToQuit, so quitting mid-check exits cleanly instead of
    aborting."""
    for worker in list(_LIVE_WORKERS):
        try:
            worker.wait(timeout_ms)
        except RuntimeError:
            pass
    _LIVE_WORKERS.clear()


def _ensure_shutdown_hook():
    app = QApplication.instance()
    if app is None or getattr(app, "_update_worker_hook", False):
        return
    app.aboutToQuit.connect(wait_for_workers)
    app._update_worker_hook = True


def _run_detached(worker, on_done):
    """Starts a worker that is safe to abandon."""
    _ensure_shutdown_hook()
    _LIVE_WORKERS.add(worker)
    worker.done.connect(on_done)

    def _cleanup():
        # Only drop the reference. deleteLater() was tried here and made
        # shutdown flaky: it queues a deletion that needs a running event
        # loop, and at quit time there may not be one left to run it, which
        # leaves a half-deleted QThread for the interpreter to trip over.
        #
        # But not before the thread has really ended. finished() is emitted
        # from the worker thread just *before* it exits, so this can run
        # while it is still on its last few instructions -- and dropping the
        # last reference then destroys a running QThread, which is Qt's
        # abort() ("Aborted", then an access violation; an intermittent
        # crash in tests/test_dialog_close.py). The wait is microseconds.
        try:
            worker.wait(3000)
        except RuntimeError:
            pass
        _LIVE_WORKERS.discard(worker)

    worker.finished.connect(_cleanup)
    worker.start()
    return worker


class UpdateDialog(CinematicDialog):
    def __init__(self, parent=None, dark_mode=True):
        super().__init__(parent, "Check for Updates", dark_mode)
        self.setFixedSize(500, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        layout.addLayout(header("Updates", "What's installed, and whether anything newer exists."))

        # ---- the app itself ----
        app_card, app_layout = make_card("Awesome Downloader")
        app_row = QHBoxLayout()
        app_row.setSpacing(10)
        self.app_status = QLabel(f"Version {config.APP_VERSION}  ·  checking GitHub...")
        self.app_status.setObjectName("muted")
        self.app_status.setWordWrap(True)
        app_row.addWidget(self.app_status, 1)
        self.app_release_btn = Button("Update now")
        self.app_release_btn.setObjectName("accent")
        self.app_release_btn.setCursor(Qt.PointingHandCursor)
        self.app_release_btn.setVisible(False)
        self.app_release_btn.clicked.connect(self._open_release_page)
        app_row.addWidget(self.app_release_btn)
        app_layout.addLayout(app_row)
        layout.addWidget(app_card)
        # Kept under its old name for anything that reads it.
        self.app_version_label = self.app_status

        # ---- yt-dlp: the one that matters day to day ----
        yt_card, yt_layout = make_card("yt-dlp  ·  the downloader engine")
        self.yt_dlp_title = yt_card.title_label
        self.yt_dlp_status = QLabel("Checking...")
        self.yt_dlp_status.setObjectName("muted")
        self.yt_dlp_status.setWordWrap(True)
        yt_layout.addWidget(self.yt_dlp_status)
        self.yt_dlp_update_btn = Button("Update yt-dlp")
        self.yt_dlp_update_btn.setObjectName("accent")
        self.yt_dlp_update_btn.setCursor(Qt.PointingHandCursor)
        self.yt_dlp_update_btn.setVisible(False)
        self.yt_dlp_update_btn.clicked.connect(self._on_upgrade_clicked)
        yt_layout.addLayout(button_row(self.yt_dlp_update_btn, None, stretch_first=False))
        layout.addWidget(yt_card)

        # ---- the rest, for completeness ----
        tools_card, tools_layout = make_card("Components")
        self.ffmpeg_label = QLabel("ffmpeg: checking...")
        self.ffmpeg_label.setObjectName("mono")
        tools_layout.addWidget(self.ffmpeg_label)
        self.libtorrent_label = QLabel("libtorrent: checking...")
        self.libtorrent_label.setObjectName("mono")
        tools_layout.addWidget(self.libtorrent_label)
        layout.addWidget(tools_card)

        layout.addStretch(1)
        self.recheck_btn = Button("Check again")
        self.recheck_btn.clicked.connect(self._start_check)
        close_btn = Button("Close")
        close_btn.setMinimumWidth(110)
        close_btn.clicked.connect(self.close)
        layout.addLayout(button_row(self.recheck_btn, None, close_btn, stretch_first=False))

        self._release = None
        self._check_worker = None
        self._upgrade_worker = None
        self._start_check()

    def _start_check(self):
        self.recheck_btn.setEnabled(False)
        self.app_status.setText(f"Version {config.APP_VERSION}  ·  checking GitHub...")
        self.app_release_btn.setVisible(False)
        self.yt_dlp_status.setText("Checking...")
        self.yt_dlp_update_btn.setVisible(False)
        self.ffmpeg_label.setText("ffmpeg: checking...")
        self.libtorrent_label.setText("libtorrent: checking...")
        self._check_worker = _run_detached(_CheckWorker(), self._on_check_done)

    def _open_release_page(self):
        """Updates from inside the app (the panel checks the release first);
        the release page only where there's no window to do that from."""
        window = self.parent()
        if window is not None and hasattr(window, "open_app_update"):
            self.accept()
            window.open_app_update()
            return
        page = (self._release or {}).get("page") or config.RELEASES_PAGE
        QDesktopServices.openUrl(QUrl(page))

    def _on_check_done(self, result):
        self.recheck_btn.setEnabled(True)

        release = result.get("app_release")
        if release is None:
            self.app_status.setText(
                f"Version {config.APP_VERSION}  ·  couldn't reach GitHub to compare.")
        elif update_checker.is_outdated(config.APP_VERSION, release["version"]):
            self._release = release
            self.app_status.setText(
                f"Version {config.APP_VERSION}  ·  {release['version']} is available.")
            self.app_release_btn.setVisible(True)
        else:
            self.app_status.setText(f"Version {config.APP_VERSION}  ·  you're up to date.")

        installed = result["yt_dlp_installed"]
        latest = result["yt_dlp_latest"]

        if result["yt_dlp_error"]:
            # A failed network check is not the same thing as "you're up to
            # date" -- staying silent about *which* happened would be worse
            # than the missing signal this dialog exists to fix.
            self.yt_dlp_status.setText(
                f"Installed: {installed or 'unknown'}\nCouldn't check for a newer version "
                f"(no internet, or pypi.org is unreachable)."
            )
        elif update_checker.is_outdated(installed, latest):
            self.yt_dlp_status.setText(
                f"Installed: {installed}\nLatest: {latest}  ·  an update is available."
            )
            self.yt_dlp_update_btn.setVisible(True)
        else:
            self.yt_dlp_status.setText(f"Installed: {installed}\nYou're up to date.")

        if result["ffmpeg_available"]:
            self.ffmpeg_label.setText(f"ffmpeg: {result['ffmpeg_version'] or 'installed'}")
        else:
            self.ffmpeg_label.setText("ffmpeg: not installed")

        if result["libtorrent_available"]:
            self.libtorrent_label.setText(f"libtorrent: {result['libtorrent_version'] or 'installed'}")
        else:
            self.libtorrent_label.setText("libtorrent: not installed")

    def _on_upgrade_clicked(self):
        self.yt_dlp_update_btn.setEnabled(False)
        self.yt_dlp_update_btn.setText("Updating...")
        self._upgrade_worker = _run_detached(_UpgradeWorker(), self._on_upgrade_done)

    def closeEvent(self, event):
        """The workers keep running -- they are detached and will clean
        themselves up -- but their results must stop being delivered here,
        because these slots write to widgets this dialog is about to destroy.
        """
        for worker in (getattr(self, "_check_worker", None),
                       getattr(self, "_upgrade_worker", None)):
            if worker is None:
                continue
            try:
                worker.done.disconnect()
            except (RuntimeError, TypeError):
                # Already disconnected, or the worker's C++ side is gone.
                pass
        super().closeEvent(event)

    def _on_upgrade_done(self, ok, error):
        self.yt_dlp_update_btn.setEnabled(True)
        self.yt_dlp_update_btn.setText("Update yt-dlp")
        if ok:
            self.yt_dlp_update_btn.setVisible(False)
            QMessageBox.information(
                self, config.APP_NAME,
                "yt-dlp updated. Restart the app for the new version to take effect."
            )
        else:
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't update yt-dlp:\n{error}")


def show_update_dialog(parent=None, dark_mode=True):
    dialog = UpdateDialog(parent, dark_mode=dark_mode)
    dialog.exec()
