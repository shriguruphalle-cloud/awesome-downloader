"""Update (or go back) from inside the app.

Shows what's new, then each step of app/utils/updater.py as it happens --
signature, download, checksum, version, Defender -- so the person can see
the update was checked before it runs. Ends with "Restart and install":
the installer takes over, closes the app, and starts it again on the new
version (or the old one, for a rollback). Anything that fails says so in
plain words and offers the release page instead.
"""
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QTextBrowser, QVBoxLayout

from app import config
from app.logging_setup import get_logger
from app.utils import updater

from .. import theme
from ..widgets import make_card
from ..widgets.button import Button
from ..widgets.progress import AnimatedProgressBar
from .base import CinematicDialog, button_row

logger = get_logger("app_update_dialog")

STEPS = ["signed", "download", "checksum", "version", "scan"]


class _Relay(QObject):
    """Background work -> the panel. Never destroyed, so work that outlives
    the panel has somewhere safe to report to."""
    # Each carries the panel it's for first: work from a panel that was
    # closed must not land in the next one.
    described = Signal(object, object)          # panel, the verified update
    progress = Signal(object, int, int)         # panel, bytes done, total
    checked = Signal(object, object, str)       # panel, [(label, ok, detail)], installer path
    failed = Signal(object, str, str)           # panel, message, release page


_relay = _Relay()


class AppUpdateDialog(CinematicDialog):
    """`rollback_to`: a version to go back to; None updates to the latest."""

    def __init__(self, window, rollback_to=None):
        dark = getattr(window, "dark_mode", True)
        super().__init__(window, "Roll back" if rollback_to else "Update", dark_mode=dark)
        self.window_ref = window
        self.rollback_to = rollback_to
        self._t = theme.tokens(dark_mode=dark)
        self._cancel = threading.Event()
        self._update = None
        self._path = None
        self._page = config.RELEASES_PAGE
        self.setMinimumWidth(620)

        col = QVBoxLayout(self)
        col.setContentsMargins(26, 24, 26, 22)
        col.setSpacing(14)
        if rollback_to:
            title, sub = ("Go back to version %s" % rollback_to,
                          "You have %s. The earlier version is downloaded from GitHub and checked "
                          "the same way as an update. Your settings, history and downloads stay." % config.APP_VERSION)
        else:
            title, sub = ("Update available",
                          "Looking up the latest version... You have %s." % config.APP_VERSION)
        heading = QLabel(title)
        heading.setObjectName("display")
        col.addWidget(heading)
        self.subtitle = QLabel(sub)
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        col.addWidget(self.subtitle)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(120)
        self.notes.setMaximumHeight(220)
        self.notes.setVisible(False)
        self.notes.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        col.addWidget(self.notes)

        card, lay = make_card("Checks before it installs")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self._marks, self._details = {}, {}
        labels = {
            "signed": "Published and signed by the developer",
            "download": "Downloaded from GitHub over HTTPS",
            "checksum": "Checksum matches the signed one",
            "version": "The installer is the right version",
            "scan": "Scanned by Microsoft Defender",
        }
        for row, key in enumerate(STEPS):
            mark = QLabel("○")
            mark.setFixedWidth(22)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name = QLabel(labels[key])
            detail = QLabel("")
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            grid.addWidget(mark, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(detail, row, 2)
            self._marks[key], self._details[key] = mark, detail
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        self.bar = AnimatedProgressBar(self._t["field_border"], self._t["progress"], self._t["success"])
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)
        col.addWidget(card)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        col.addWidget(self.message)

        self.later_btn = Button("Later")
        self.later_btn.clicked.connect(self.reject)
        self.page_btn = Button("Open release page")
        self.page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._page)))
        self.page_btn.setVisible(False)
        self.go_btn = Button("Go back" if rollback_to else "Download and install")
        self.go_btn.setObjectName("accent")
        self.go_btn.setMinimumWidth(190)
        self.go_btn.setEnabled(False)
        self.go_btn.clicked.connect(self._go)
        col.addLayout(button_row(self.later_btn, self.page_btn, self.go_btn))

        _relay.described.connect(self._on_described)
        _relay.progress.connect(self._on_progress)
        _relay.checked.connect(self._on_checked)
        _relay.failed.connect(self._on_failed)
        self._set("signed", "run", "checking the signature...")
        threading.Thread(target=self._describe_work, name="awd-update-describe", daemon=True).start()

    # ---- steps ----
    def _set(self, key, state, detail=""):
        glyph, color = {"wait": ("○", self._t["text_faint"]), "run": ("◐", self._t["brand"]),
                        "ok": ("✓", self._t["success"]), "bad": ("✕", self._t["danger"]),
                        "skip": ("–", self._t["text_muted"])}[state]
        self._marks[key].setText(glyph)
        self._marks[key].setStyleSheet("color: %s; font-weight: 700; font-size: 15px;" % color)
        self._details[key].setText(detail)

    # ---- background work ----
    def _describe_work(self):
        try:
            rel = updater.release(self.rollback_to)
            _relay.described.emit(self, updater.describe(rel))
        except updater.UpdateError as e:
            _relay.failed.emit(self, str(e), config.RELEASES_PAGE)
        except Exception as e:  # noqa: BLE001
            logger.exception("Update lookup failed")
            _relay.failed.emit(self, "Something went wrong looking up the update (%s)." % e.__class__.__name__,
                               config.RELEASES_PAGE)

    def _download_work(self, update):
        try:
            path = updater.download(update, progress=lambda d, t: _relay.progress.emit(self, d, t),
                                    cancelled=self._cancel.is_set)
            results = updater.check_installer(path, update)
            _relay.checked.emit(self, results, path)
        except updater.UpdateError as e:
            _relay.failed.emit(self, str(e), update.get("page") or config.RELEASES_PAGE)
        except Exception as e:  # noqa: BLE001
            logger.exception("Update download failed")
            _relay.failed.emit(self, "The download failed (%s). Try again, or use the release page."
                               % e.__class__.__name__, update.get("page") or config.RELEASES_PAGE)

    # ---- results, on the GUI thread ----
    def _on_described(self, owner, update):
        if owner is not self:
            return
        self._update = update
        self._page = update["page"]
        self._set("signed", "ok", "signed for version %s" % update["version"])
        mb = update["size"] / 1048576
        if self.rollback_to:
            self.subtitle.setText("Version %s (%.0f MB) is ready to download. You have %s; your settings, "
                                  "history and downloads stay." % (update["version"], mb, config.APP_VERSION))
        else:
            self.subtitle.setText("Version %s is ready (%.0f MB). You have %s; your settings, history and "
                                  "downloads stay." % (update["version"], mb, config.APP_VERSION))
        if update.get("notes"):
            self.notes.setMarkdown(update["notes"])
            self.notes.setVisible(True)
        self.go_btn.setEnabled(True)

    def _go(self):
        if self._path:
            return self._install()
        if not self._update:
            return
        self.go_btn.setEnabled(False)
        self.later_btn.setText("Cancel")
        self._set("download", "run", "starting...")
        threading.Thread(target=self._download_work, args=(self._update,), name="awd-update-download",
                         daemon=True).start()

    def _on_progress(self, owner, done, total):
        if owner is not self or not total:
            return
        self.bar.setValue(int(1000 * done / total))
        self._set("download", "run", "%.1f of %.1f MB" % (done / 1048576, total / 1048576))

    def _on_checked(self, owner, results, path):
        if owner is not self:
            return
        self._set("download", "ok", "%.1f MB" % (self._update["size"] / 1048576))
        self.bar.setValue(1000)
        for key, (label, ok, detail) in zip(("checksum", "version", "scan"), results):
            skipped = key == "scan" and ok and detail != "no threats found"
            self._set(key, "skip" if skipped else ("ok" if ok else "bad"), detail)
        if not all(ok for _, ok, _ in results):
            self._fail("The downloaded installer didn't pass the checks, so it was deleted and won't run.")
            return
        self._path = path
        self.later_btn.setText("Later")
        self.go_btn.setText("Restart and install")
        self.go_btn.setEnabled(True)
        self.message.setText("Ready. The app closes, Windows asks for permission, and it opens again on "
                             "version %s." % self._update["version"])
        self.message.setVisible(True)

    def _on_failed(self, owner, msg, page):
        if owner is not self or msg == "Cancelled.":
            return
        self._page = page or self._page
        for key in STEPS:
            if self._marks[key].text() == "◐":
                self._set(key, "bad", "")
        self._fail(msg)

    def _fail(self, msg):
        self.message.setText(msg)
        self.message.setStyleSheet("color: %s;" % self._t["danger"])
        self.message.setVisible(True)
        self.go_btn.setVisible(False)
        self.page_btn.setVisible(True)
        self.later_btn.setText("Close")

    def _install(self):
        ok = updater.install(self._path, config.APP_VERSION, self._update["version"],
                             rollback=bool(self.rollback_to))
        if not ok:
            self._fail("The installer couldn't be started. Run it yourself from %s, or use the release page."
                       % self._path)
            return
        self.accept()
        QApplication.instance().quit()

    def done(self, result):
        self._cancel.set()
        for sig, slot in ((_relay.described, self._on_described), (_relay.progress, self._on_progress),
                          (_relay.checked, self._on_checked), (_relay.failed, self._on_failed)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

