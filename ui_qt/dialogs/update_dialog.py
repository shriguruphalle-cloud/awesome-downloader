"""Check for Updates dialog.

Reports installed-vs-latest for yt-dlp (the one that matters: sites change
constantly, yt-dlp ships near-daily fixes, and a stale copy doesn't error
out cleanly -- downloads just start failing or silently pick the wrong
format, with nothing in the UI to explain why) plus ffmpeg and libtorrent
status for completeness. The network check runs on a background thread so
it can't freeze the window; results come back via a signal, the same
pattern TorrentTab's libtorrent installer already uses.
"""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from app import config
from app.core import update_checker
from app.logging_setup import get_logger

from .. import theme

logger = get_logger("update_dialog")


class _CheckWorker(QThread):
    done = Signal(dict)

    def run(self):
        result = {}
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


class UpdateDialog(QDialog):
    def __init__(self, parent=None, dark_mode=True):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.setFixedSize(420, 340)
        t = theme.tokens(dark_mode=dark_mode)
        # Same non-translucent-dialog styling as about_dialog.py -- this
        # window has no WA_TranslucentBackground, so QSS's "background:
        # transparent" would just leave Qt's plain grey default showing.
        self.setStyleSheet(
            theme.build_stylesheet(dark_mode=dark_mode) + f"QDialog {{ background: {t['card_bg_solid']}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)

        title = QLabel("Check for Updates")
        title.setObjectName("heading")
        layout.addWidget(title)
        layout.addSpacing(4)

        self.app_version_label = QLabel(f"{config.APP_NAME}  ·  version {config.APP_VERSION}")
        self.app_version_label.setObjectName("muted")
        layout.addWidget(self.app_version_label)
        layout.addSpacing(14)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(14)

        self.yt_dlp_title = QLabel("yt-dlp")
        self.yt_dlp_title.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.yt_dlp_title)
        self.yt_dlp_status = QLabel("Checking...")
        self.yt_dlp_status.setObjectName("muted")
        self.yt_dlp_status.setWordWrap(True)
        layout.addWidget(self.yt_dlp_status)
        self.yt_dlp_update_btn = QPushButton("Update yt-dlp")
        self.yt_dlp_update_btn.setObjectName("accent")
        self.yt_dlp_update_btn.setVisible(False)
        self.yt_dlp_update_btn.clicked.connect(self._on_upgrade_clicked)
        layout.addWidget(self.yt_dlp_update_btn)
        layout.addSpacing(14)

        self.ffmpeg_label = QLabel("ffmpeg: checking...")
        self.ffmpeg_label.setObjectName("muted")
        layout.addWidget(self.ffmpeg_label)
        self.libtorrent_label = QLabel("libtorrent: checking...")
        self.libtorrent_label.setObjectName("muted")
        layout.addWidget(self.libtorrent_label)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        self.recheck_btn = QPushButton("Check Again")
        self.recheck_btn.clicked.connect(self._start_check)
        btn_row.addWidget(self.recheck_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("accent")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._check_worker = None
        self._upgrade_worker = None
        self._start_check()

    def _start_check(self):
        self.recheck_btn.setEnabled(False)
        self.yt_dlp_status.setText("Checking...")
        self.yt_dlp_update_btn.setVisible(False)
        self.ffmpeg_label.setText("ffmpeg: checking...")
        self.libtorrent_label.setText("libtorrent: checking...")
        self._check_worker = _CheckWorker(self)
        self._check_worker.done.connect(self._on_check_done)
        self._check_worker.start()

    def _on_check_done(self, result):
        self.recheck_btn.setEnabled(True)
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
                f"Installed: {installed}\nLatest: {latest}  --  an update is available."
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
        self._upgrade_worker = _UpgradeWorker(self)
        self._upgrade_worker.done.connect(self._on_upgrade_done)
        self._upgrade_worker.start()

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
