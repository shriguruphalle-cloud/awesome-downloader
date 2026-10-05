"""Settings -- every preference the app keeps, in one panel.

Before 2.5 most of these existed only as keys in settings.json or not at
all: how many downloads run at once, the quality new links start on, which
browser's sign-in to borrow, whether to check for new versions. Changes
apply the moment they are made; there is no Save button to forget.
"""
import os
import threading

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from app import config
from app.utils import settings as settings_store
from app.utils import uninstall

from .. import cinema, palettes
from ..widgets import make_card
from ..widgets.palette_picker import PalettePicker
from .base import CinematicDialog, button_row, header
from ..widgets.button import Button

QUALITIES = [("Best available", "best"), ("2160p (4K)", 2160), ("1440p", 1440),
             ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]
FORMATS = ["mp4", "mkv", "mov"]
# Firefox before the Chromium browsers: Chrome, Edge and the rest now lock
# their cookies with app-bound encryption, so borrowing their sign-in usually
# fails on an up-to-date install (the downloader then carries on without it).
SIGN_IN = [("This app's Browser tab (recommended)", None), ("Firefox", "firefox"),
           ("Google Chrome -- often locked", "chrome"), ("Microsoft Edge -- often locked", "edge"),
           ("Brave -- often locked", "brave"), ("Opera -- often locked", "opera"),
           ("Vivaldi -- often locked", "vivaldi")]
THEMES = [("Night (dark)", "dark"), ("Pearl (light)", "light")]
LABEL_W = 168

BACKDROPS = [("Cinematic", cinema.CINEMATIC),
             ("Windows acrylic -- see-through", cinema.DESKTOP),
             ("Solid -- reduce transparency", cinema.SOLID)]


class _Relay(QObject):
    """Carries the background update check's answer back to the GUI
    thread. Module-level and never destroyed, so a check that outlives the
    panel has somewhere safe to deliver to."""
    checked = Signal(object, bool)


_relay = _Relay()


class SettingsDialog(CinematicDialog):
    def __init__(self, window):
        super().__init__(window, "Settings", dark_mode=getattr(window, "dark_mode", True))
        self.window_ref = window
        self.settings = window.settings if window.settings is not None else settings_store.load_settings()
        self.resize(640, 680)
        self.setMinimumSize(560, 420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")
        scroll.setStyleSheet("#settingsScroll, #settingsBody { background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsBody")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._body = body

        col = QVBoxLayout(body)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(14)
        col.addLayout(header("Settings", f"{config.APP_NAME.title()}  ·  version {config.APP_VERSION}"
                                         "  ·  free & open source (GPL-3.0)"))
        col.addSpacing(4)

        # ---- Downloads ----
        card, lay = make_card("Downloads")
        grid = self._grid(lay)
        self.folder_edit = QLineEdit(settings_store.get_save_dir(
            self.settings, "video", config.DEFAULT_DOWNLOAD_DIR))
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setCursorPosition(0)
        self.folder_edit.setToolTip(self.folder_edit.text())
        browse = Button("Browse")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._pick_folder)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        self._row(grid, 0, "Save videos to", folder_row)

        self.concurrent = QComboBox()
        lo, hi = settings_store.MAX_CONCURRENT_RANGE
        for n in range(lo, hi + 1):
            self.concurrent.addItem(f"{n} at a time" if n > 1 else "One at a time", n)
        self.concurrent.setCurrentIndex(settings_store.max_concurrent(self.settings) - lo)
        self.concurrent.currentIndexChanged.connect(
            lambda _i: self._set("max_concurrent", self.concurrent.currentData()))
        self._row(grid, 1, "Simultaneous downloads", self.concurrent,
                  "Links beyond this wait their turn, and start as others finish.")

        self.quality = self._combo(QUALITIES, self.settings.get("preferred_quality", "best"))
        self.quality.currentIndexChanged.connect(
            lambda _i: self._set("preferred_quality", self.quality.currentData()))
        self._row(grid, 3, "Quality for new links", self.quality,
                  "The nearest a link offers at or below this.")

        self.format = self._combo([(f.upper(), f) for f in FORMATS],
                                  self.settings.get("default_format", "mp4"))
        self.format.currentIndexChanged.connect(
            lambda _i: self._set("default_format", self.format.currentData()))
        self._row(grid, 5, "Video format", self.format)
        col.addWidget(card)

        # ---- Sign-in ----
        card, lay = make_card("Sign-in")
        grid = self._grid(lay)
        self.cookies = self._combo(SIGN_IN, self.settings.get("cookies_from_browser"))
        self.cookies.currentIndexChanged.connect(
            lambda _i: self._set("cookies_from_browser", self.cookies.currentData()))
        self._row(grid, 0, "Use the sign-in from", self.cookies,
                  "For posts that need an account. Only the cookies for the site "
                  "you're downloading from are used, and only on this machine.")
        col.addWidget(card)

        # ---- Appearance ----
        card, lay = make_card("Appearance")
        grid = self._grid(lay)
        self.theme = self._combo(THEMES, self.settings.get("theme", "dark"))
        self.theme.currentIndexChanged.connect(
            lambda _i: self._set("theme", self.theme.currentData()))
        self._row(grid, 0, "Theme", self.theme)
        self.palette = PalettePicker(self.settings.get("palette", palettes.DEFAULT))
        self.palette.chosen.connect(lambda name: self._set("palette", name))
        self._row(grid, 1, "Colour", self.palette,
                  "Jewel tones, each with a night and a day version.")
        self.backdrop = self._combo(BACKDROPS, self.settings.get("backdrop", cinema.CINEMATIC))
        self.backdrop.currentIndexChanged.connect(
            lambda _i: self._set("backdrop", self.backdrop.currentData()))
        self._row(grid, 3, "Backdrop", self.backdrop,
                  "Acrylic shows your desktop through the window. Solid turns off "
                  "every translucent surface.")
        self.motion = QCheckBox("Reduce motion")
        self.motion.setChecked(bool(self.settings.get("reduce_motion", False)))
        self.motion.toggled.connect(lambda on: self._set("reduce_motion", bool(on)))
        self._row(grid, 5, "", self.motion)
        col.addWidget(card)

        # ---- Updates ----
        card, lay = make_card("Updates")
        grid = self._grid(lay)
        self.auto_check = QCheckBox("Check for a new version when the app starts")
        self.auto_check.setChecked(bool(self.settings.get("check_app_updates", True)))
        self.auto_check.toggled.connect(lambda on: self._set("check_app_updates", bool(on)))
        self._row(grid, 0, "", self.auto_check)
        self.check_btn = Button("Check now")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.clicked.connect(self._check_now)
        self.check_status = QLabel(f"You have version {config.APP_VERSION}.")
        self.check_status.setObjectName("muted")
        self.check_status.setWordWrap(True)
        self.release_btn = Button("Update now")
        self.release_btn.setObjectName("accent")
        self.release_btn.setCursor(Qt.PointingHandCursor)
        self.release_btn.setVisible(False)
        self.release_btn.clicked.connect(self._open_release)
        check_row = QHBoxLayout()
        check_row.setSpacing(10)
        check_row.addWidget(self.check_btn)
        check_row.addWidget(self.check_status, 1)
        check_row.addWidget(self.release_btn)
        self._row(grid, 1, "", check_row)
        # Going back: offered after an update made from inside the app.
        from app.utils import updater
        self.rollback_to = updater.rollback_version()
        self.rollback_btn = None
        if self.rollback_to:
            self.rollback_btn = Button("Go back to version %s" % self.rollback_to)
            self.rollback_btn.setCursor(Qt.PointingHandCursor)
            self.rollback_btn.setToolTip("Reinstall the version you updated from -- checked the same way "
                                         "as an update. Your settings and history stay.")
            self.rollback_btn.clicked.connect(self._rollback)
            self._row(grid, 2, "", button_row(self.rollback_btn, None, stretch_first=False))
        col.addWidget(card)

        # ---- Data ----
        card, lay = make_card("Your data")
        note = QLabel("Settings, the queue, history and the Browser tab's profile are "
                      "kept in one folder on this PC. Nothing is sent anywhere.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        data_btn = Button("Open data folder")
        data_btn.setCursor(Qt.PointingHandCursor)
        data_btn.clicked.connect(self._open_data_folder)
        # Only in an installed copy: the installer's uninstaller beside the app.
        self.uninstall_btn = None
        if uninstall.uninstaller_path():
            self.uninstall_btn = Button("Uninstall %s\u2026" % config.APP_NAME.title())
            self.uninstall_btn.setObjectName("danger")
            self.uninstall_btn.setToolTip("Remove the app from this PC. You'll be asked whether to keep your data.")
            self.uninstall_btn.clicked.connect(self._uninstall)
        lay.addLayout(button_row(data_btn, None, self.uninstall_btn, stretch_first=False))
        col.addWidget(card)

        col.addStretch(1)
        done = Button("Done")
        done.setObjectName("accent")
        done.setMinimumWidth(110)
        done.clicked.connect(self.accept)
        col.addLayout(button_row(done))

        self._release = None
        _relay.checked.connect(self._on_checked)
        # Never narrower than what the cards need: the scroll area only
        # scrolls up and down, so anything wider was cut off at the right.
        need = body.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4
        if need > self.minimumWidth():
            self.setMinimumWidth(need)
        if need > self.width():
            self.resize(need, self.height())

    # ---- building blocks ----
    @staticmethod
    def _grid(card_layout):
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, LABEL_W)
        grid.setColumnStretch(1, 1)
        card_layout.addLayout(grid)
        return grid

    @staticmethod
    def _row(grid, row, label, control, hint=""):
        # Every label column is the same fixed width -- each card has its own
        # grid, and sized to its own labels the controls started at a
        # slightly different x in every card.
        lbl = QLabel(label)
        lbl.setObjectName("muted")
        lbl.setFixedWidth(LABEL_W)
        grid.addWidget(lbl, row, 0, Qt.AlignmentFlag.AlignVCenter)
        if isinstance(control, QHBoxLayout):
            grid.addLayout(control, row, 1)
        else:
            grid.addWidget(control, row, 1)
        if hint:
            h = QLabel(hint)
            h.setObjectName("faint")
            h.setWordWrap(True)
            grid.addWidget(h, row + 1, 1)

    @staticmethod
    def _combo(items, current):
        combo = QComboBox()
        combo.setCursor(Qt.PointingHandCursor)
        for label, value in items:
            combo.addItem(label, value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    # ---- changes ----
    def _set(self, key, value):
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        settings_store.save_settings(self.settings)
        apply = getattr(self.window_ref, "apply_settings", None)
        if apply is not None:
            apply({key})
        if key in ("theme", "palette"):
            # The panel itself follows the theme and the palette too. (The
            # window has switched the palette by now; this covers a panel
            # opened without one.)
            palettes.set_current(self.settings.get("palette", palettes.DEFAULT))
            self.dark_mode = self.settings.get("theme", "dark") != "light"
            from .. import theme as theme_mod
            self.setStyleSheet(theme_mod.build_stylesheet(dark_mode=self.dark_mode)
                               + "QDialog { background: transparent; }")
            cinema.set_dark_title_bar(self, self.dark_mode)
            for w in self.findChildren(QWidget):
                w.update()
            self.update()
        if key == "backdrop":
            self._solid = value == cinema.SOLID
            self.update()

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Save videos to", self.folder_edit.text())
        if not path:
            return
        self.folder_edit.setText(path)
        self.folder_edit.setCursorPosition(0)
        self.folder_edit.setToolTip(path)
        settings_store.set_save_dir(self.settings, "video", path)
        apply = getattr(self.window_ref, "apply_settings", None)
        if apply is not None:
            apply({"save_dirs"})

    def _open_data_folder(self):
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(config.APPDATA_DIR))

    def _uninstall(self):
        """Starts the uninstaller and quits, so the app has nothing open
        when the uninstaller removes it. The uninstaller asks whether to
        keep your data."""
        name = config.APP_NAME.title()
        confirmed = QMessageBox.question(
            self, "Uninstall %s" % name,
            "Remove %s from this PC?\n\nThe app closes and Windows' uninstaller takes over. "
            "It asks whether to keep your settings, history and bookmarks, in case you "
            "reinstall later." % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        if not confirmed:
            return
        if not uninstall.launch_uninstaller():
            QMessageBox.warning(self, "Uninstall %s" % name,
                                "Couldn't start the uninstaller. You can remove the app from "
                                "Windows Settings > Apps > Installed apps.")
            return
        self.accept()
        QApplication.instance().quit()

    # ---- update check ----
    def _check_now(self):
        self.check_btn.setEnabled(False)
        self.release_btn.setVisible(False)
        self.check_status.setText("Checking...")

        def work():
            from app.utils import app_update
            release = app_update.latest_release()
            try:
                _relay.checked.emit(release, release is not None)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="awd-check-now", daemon=True).start()

    def _on_checked(self, release, reached):
        from app.core.update_checker import is_outdated
        try:
            self.check_btn.setEnabled(True)
        except RuntimeError:
            return
        if not reached:
            self.check_status.setText("Couldn't reach GitHub -- check your connection and try again.")
            return
        if is_outdated(config.APP_VERSION, release["version"]):
            self._release = release
            self.check_status.setText(f"Version {release['version']} is available.")
            self.release_btn.setVisible(True)
        else:
            self.check_status.setText(f"You're on the latest version ({config.APP_VERSION}).")

    def _open_release(self):
        """Updates from inside the app; the release page only without a window."""
        if hasattr(self.window_ref, "open_app_update"):
            self.accept()
            self.window_ref.open_app_update()
            return
        page = (self._release or {}).get("page") or config.RELEASES_PAGE
        QDesktopServices.openUrl(QUrl(page))

    def _rollback(self):
        if hasattr(self.window_ref, "open_app_update"):
            self.accept()
            self.window_ref.open_app_update(rollback_to=self.rollback_to)

    def done(self, result):
        try:
            _relay.checked.disconnect(self._on_checked)
        except (RuntimeError, TypeError):
            pass
        super().done(result)


def show_settings(window):
    dialog = SettingsDialog(window)
    dialog.exec()
