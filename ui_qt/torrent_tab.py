"""Torrent tab: paste a magnet link or open a
.torrent file, download it. Each torrent renders as its own card in a
scrollable list, polled on a 1s QTimer (same cadence as the CTk version's
.after(1000, ...) loop).
"""
import os
import time
import subprocess
import sys
import threading

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QApplication, QMenu,
    QMessageBox, QScrollArea, QVBoxLayout, QWidget,
)

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from app import config
from app.core.torrent_manager import LIBTORRENT_AVAILABLE, TorrentManager
from app.logging_setup import get_logger
from app.utils import (
    download_history, protocol_handler, settings as settings_store, startup, torrent_state,
)
from app.utils.formatting import format_eta, humanize_rate, humanize_size

from . import motion, theme
from .widgets.speed_graph import GraphCard
from .widgets.stats_strip import StatsStrip
from .dialogs.add_torrent_dialog import AddTorrentDialog
from .widgets import AnimatedProgressBar, EmptyState, centered_column, make_card
from .widgets.button import Button

# The page is a centred column no wider than this.
MAX_CONTENT_W = 1760

logger = get_logger("torrent_tab")


def _duration(seconds):
    """14m 03s, 2h 05m, 3d 4h -- the two units that matter."""
    seconds = int(max(0, seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    m, sec = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def _when(timestamp):
    """Today 14:32 / Yesterday 09:10 / 12 Sep 14:32."""
    import datetime
    if not timestamp:
        return "--"
    then = datetime.datetime.fromtimestamp(timestamp)
    today = datetime.date.today()
    if then.date() == today:
        day = "Today"
    elif then.date() == today - datetime.timedelta(days=1):
        day = "Yesterday"
    else:
        day = then.strftime("%d %b").lstrip("0")
    return f"{day} {then:%H:%M}"


def _dots_icon(color, width=14, height=4, dot=3):
    """The overflow button's three dots, drawn rather than typed."""
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    gap = (width - 3 * dot) / 2.0
    y = (height - dot) / 2.0
    for i in range(3):
        painter.drawEllipse(QRectF(i * (dot + gap), y, dot, dot))
    painter.end()
    return QIcon(pixmap)


def _dot_icon(color, size=10):
    """Small filled dot used to mark destructive menu entries -- drawn,
    not a Unicode bullet, so it keeps its colour regardless of the font
    the menu happens to resolve."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    d = size * 0.62
    off = (size - d) / 2.0
    painter.drawEllipse(QRectF(off, off, d, d))
    painter.end()
    return QIcon(pixmap)


def _python_version_str():
    return f"{sys.version_info.major}.{sys.version_info.minor}"


class TorrentTab(QWidget):
    _install_done_sig = Signal(str)
    _install_error_sig = Signal(str)
    # single_instance.py's listener (magnet-link forwarding from a second
    # launch) calls this from a background socket thread -- emitting a
    # signal is thread-safe and Qt auto-queues delivery to
    # _on_magnet_forwarded on the GUI thread, the same pattern
    magnet_forwarded = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.download_dir = settings_store.get_save_dir(
            settings, "torrent", config.DEFAULT_TORRENT_DIR)
        os.makedirs(self.download_dir, exist_ok=True)
        self.rows = {}
        self._row_counter = 0
        self.magnet_forwarded.connect(self._on_magnet_forwarded)
        self._manager = None

        if not LIBTORRENT_AVAILABLE:
            self._build_missing_engine_ui()
            return

        # The torrent engine starts with the first torrent -- one to restore,
        # or one added -- not with the app: with none, libtorrent would still
        # bootstrap the DHT, map ports and listen for peers in the background.
        # Earlier builds recorded a finished torrent in History again at every
        # launch (see torrent_manager.py); one entry each is enough, dated by
        # its file rather than by the launch that re-recorded it.
        download_history.collapse_duplicates("torrent")
        download_history.correct_torrent_times()
        self._build_ui()
        self._offer_magnet_handler()
        self._restore_saved_torrents()

        self._polls = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(1000)
        QTimer.singleShot(0, self._reschedule_poll)

    @property
    def manager(self):
        if self._manager is None:
            self._manager = TorrentManager()
        return self._manager

    # ------------------------------------------------------- fallback UI ---
    def _build_missing_engine_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        heading = QLabel("Torrent engine not installed")
        heading.setObjectName("heading")
        layout.addWidget(heading)

        py_ver = _python_version_str()
        min_v = ".".join(map(str, config.LIBTORRENT_MIN_PY))
        max_v = ".".join(map(str, config.LIBTORRENT_MAX_PY))
        compatible = config.LIBTORRENT_MIN_PY <= sys.version_info[:2] <= config.LIBTORRENT_MAX_PY
        if compatible:
            detail = (
                "This tab needs the 'libtorrent' package — the same engine used by "
                "qBittorrent and Deluge. It isn't installed yet; click below to install it."
            )
        else:
            detail = (
                f"This tab needs the 'libtorrent' package, but there is no prebuilt wheel "
                f"for the Python version currently running this app (Python {py_ver}). "
                f"libtorrent's PyPI wheels currently support Python {min_v}–{max_v}. "
                f"Use Check for Updates → Python environment to set up a compatible "
                f"environment automatically."
            )
        detail_label = QLabel(detail)
        detail_label.setObjectName("muted")
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)

        self.install_btn = Button("Install libtorrent")
        self.install_btn.setObjectName("accent")
        self.install_btn.setEnabled(compatible)
        self.install_btn.clicked.connect(self._install_libtorrent)
        layout.addWidget(self.install_btn)

        self.install_status_label = QLabel("")
        self.install_status_label.setObjectName("muted")
        layout.addWidget(self.install_status_label)
        layout.addStretch(1)

        self._install_done_sig.connect(self._on_install_done)
        self._install_error_sig.connect(self._on_install_error)

    def _install_libtorrent(self):
        if config.IS_FROZEN:
            QMessageBox.information(
                self, config.APP_NAME,
                "Auto-install isn't available in the packaged .exe.\n\n"
                "Run this in a terminal (Python 3.11-3.13), then restart the app:\n"
                "pip install libtorrent",
            )
            return
        self.install_btn.setEnabled(False)
        self.install_status_label.setText("Installing... this can take a minute.")
        threading.Thread(target=self._install_libtorrent_thread, daemon=True).start()

    def _install_libtorrent_thread(self):
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 300}
            if config.CREATE_NO_WINDOW:
                kwargs["creationflags"] = config.CREATE_NO_WINDOW
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "libtorrent"], **kwargs
            )
            if result.returncode == 0:
                self._install_done_sig.emit("Installed! Please restart the app to enable the Torrent tab.")
            else:
                logger.error("libtorrent install failed: %s", (result.stderr or "")[-800:])
                self._install_done_sig.emit(f"Install failed:\n\n{(result.stderr or '')[-400:]}")
        except Exception as e:
            logger.exception("libtorrent install crashed")
            self._install_error_sig.emit(str(e))

    def _on_install_done(self, msg):
        self.install_btn.setEnabled(True)
        self.install_status_label.setText("")
        QMessageBox.information(self, config.APP_NAME, msg)

    def _on_install_error(self, err):
        self.install_btn.setEnabled(True)
        self.install_status_label.setText("")
        QMessageBox.critical(self, config.APP_NAME, f"Install failed:\n{err}")

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        column = centered_column(self, MAX_CONTENT_W)
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        add_card, add_layout = make_card("Add a torrent")
        add_layout.setSpacing(10)
        root.addWidget(add_card)

        magnet_row = QHBoxLayout()
        magnet_row.setSpacing(10)
        self.magnet_entry = QLineEdit()
        self.magnet_entry.setObjectName("heroField")
        self.magnet_entry.setFixedHeight(44)
        self.magnet_entry.setPlaceholderText("Paste a magnet link  —  magnet:?xt=urn:btih:...")
        self.magnet_entry.returnPressed.connect(self.on_add_magnet)
        magnet_row.addWidget(self.magnet_entry, 1)
        add_magnet_btn = Button("Add Magnet")
        add_magnet_btn.setObjectName("accent")
        add_magnet_btn.setFixedHeight(44)
        add_magnet_btn.setMinimumWidth(128)
        add_magnet_btn.setCursor(Qt.PointingHandCursor)
        add_magnet_btn.clicked.connect(self.on_add_magnet)
        magnet_row.addWidget(add_magnet_btn)
        add_layout.addLayout(magnet_row)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        open_file_btn = Button("Open .torrent file...")
        open_file_btn.setCursor(Qt.PointingHandCursor)
        open_file_btn.clicked.connect(self.on_add_torrent_file)
        dir_row.addWidget(open_file_btn)
        dir_row.addSpacing(10)
        dir_row.addWidget(self._label("Save to", "muted"))
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = Button("Browse")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        add_layout.addLayout(dir_row)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        add_layout.addWidget(divider)

        checks = QHBoxLayout()
        checks.setSpacing(22)
        self.startup_check = QCheckBox("Launch at Windows startup, so downloads resume")
        self.startup_check.setChecked(startup.is_enabled())
        self.startup_check.toggled.connect(self._on_startup_toggle)
        checks.addWidget(self.startup_check)

        self.magnet_handler_check = QCheckBox("Open magnet links with this app")
        self.magnet_handler_check.setToolTip(
            "Clicking a magnet link in your browser opens it here instead of "
            "whatever torrent client is currently registered. Per-user only; "
            "unticking restores the previous handler.")
        self.magnet_handler_check.setChecked(protocol_handler.is_default())
        self.magnet_handler_check.toggled.connect(self._on_magnet_handler_toggle)
        checks.addWidget(self.magnet_handler_check)
        checks.addStretch(1)
        add_layout.addLayout(checks)

        # The rows sit straight on the backdrop, each its own pane of glass,
        # rather than inside one big panel: glass inside glass had to give up
        # the frost to stay legible, and the list then read as a grey box.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Scoped by id -- an unscoped "background: transparent" outranks the
        # app stylesheet for every descendant and strips the row buttons.
        scroll.setObjectName("torrentScroll")
        scroll.setStyleSheet("#torrentScroll { background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_body = QWidget()
        self.list_body.setObjectName("torrentScrollBody")
        self.list_body.setStyleSheet("#torrentScrollBody { background: transparent; }")
        self.list_body_layout = QVBoxLayout(self.list_body)
        self.list_body_layout.setContentsMargins(0, 0, 0, 0)
        self.list_body_layout.setSpacing(10)
        self.empty_label = EmptyState(
            "torrent", "No torrents yet",
            "Paste a magnet link or open a .torrent file above. Downloads keep "
            "going in the background and pick up where they left off.")
        self.list_body_layout.addWidget(self.empty_label)
        self.list_body_layout.addStretch(1)
        scroll.setWidget(self.list_body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(16)
        self.total_down_label = QLabel("↓ 0 B/s")
        self.total_down_label.setObjectName("mono")
        footer.addWidget(self.total_down_label)
        self.total_up_label = QLabel("↑ 0 B/s")
        self.total_up_label.setObjectName("mono")
        footer.addWidget(self.total_up_label)
        footer.addStretch(1)
        self.totals_label = QLabel("")
        self.totals_label.setObjectName("muted")
        footer.addWidget(self.totals_label)
        root.addLayout(footer)

    def _dark_mode(self):
        """Current theme, for widgets that paint themselves and so can't get
        their colours from the stylesheet."""
        return (self.settings or {}).get("theme", "dark") != "light"

    def apply_theme(self):
        """Called by MainWindow after a live theme toggle -- every row's
        progress bar paints itself directly (see widgets/progress.py) and
        so doesn't pick up new colours from the stylesheet the way ordinary
        QSS-styled widgets do."""
        t = theme.tokens(dark_mode=self._dark_mode())
        track = QColor(255, 255, 255, 20) if self._dark_mode() else QColor(15, 23, 42, 20)
        for info in self.rows.values():
            info["progress_bar"].set_colors(track, t["progress"], t["success"])

    @staticmethod
    def _label(text, object_name=None):
        lbl = QLabel(text)
        if object_name:
            lbl.setObjectName(object_name)
        return lbl

    def _on_startup_toggle(self, checked):
        ok = startup.set_enabled(checked)
        if not ok:
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(not checked)
            self.startup_check.blockSignals(False)
            QMessageBox.critical(
                self, config.APP_NAME,
                "Couldn't update the Windows startup setting. Check the log for details.",
            )

    def _offer_magnet_handler(self):
        """Magnet links open in this app by default: registered once, on the
        first run that has a Torrent tab. Unticking the box later is the
        person's choice and sticks -- this never registers again after that."""
        if (self.settings or {}).get("magnet_handler_offered"):
            return
        if self.settings is not None:
            self.settings["magnet_handler_offered"] = True
            settings_store.save_settings(self.settings)
        if protocol_handler.is_default():
            return
        try:
            if protocol_handler.set_enabled(True) and protocol_handler.is_default():
                self.magnet_handler_check.blockSignals(True)
                self.magnet_handler_check.setChecked(True)
                self.magnet_handler_check.blockSignals(False)
        except Exception:   # noqa: BLE001 -- Windows may have it locked to another app
            logger.exception("Couldn't register as the magnet link handler")

    def _on_magnet_handler_toggle(self, checked):
        ok = protocol_handler.set_enabled(checked)
        if not ok:
            self.magnet_handler_check.blockSignals(True)
            self.magnet_handler_check.setChecked(not checked)
            self.magnet_handler_check.blockSignals(False)
            QMessageBox.critical(
                self, config.APP_NAME,
                "Couldn't change the magnet link handler. Check the log for details.")
            return
        # Windows honors an explicit Settings > Default apps choice over any
        # registry write, so registering can silently do nothing. Say so
        # rather than leaving a ticked box that doesn't work.
        if checked and not protocol_handler.is_default():
            owner = protocol_handler.userchoice_owner()
            self.magnet_handler_check.blockSignals(True)
            self.magnet_handler_check.setChecked(False)
            self.magnet_handler_check.blockSignals(False)
            go = QMessageBox.question(
                self, config.APP_NAME,
                "Windows has magnet links locked to another app"
                + (f" ({owner})" if owner else "") + ".\n\n"
                "Windows only lets you change that in its own Default apps "
                "settings. Open them now? Choose Awesome Downloader for MAGNET there.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes
            if go:
                protocol_handler.open_default_apps()

    def browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.dir_entry.text())
        if d:
            self.dir_entry.setText(d)
            settings_store.set_save_dir(self.settings, "torrent", d)

    def _on_magnet_forwarded(self, uri):
        if not hasattr(self, "magnet_entry"):
            logger.warning("Got a forwarded magnet link, but the Torrent tab's engine "
                            "isn't available (libtorrent missing) -- dropping it: %s", uri)
            return
        self.magnet_entry.setText(uri)
        self.on_add_magnet()

    # ----------------------------------------------------------- Adding ---
    def on_add_magnet(self):
        uri = self.magnet_entry.text().strip()
        if not uri.lower().startswith("magnet:"):
            QMessageBox.warning(self, config.APP_NAME, "Paste a valid magnet link (it should start with 'magnet:').")
            return
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)
        try:
            handle = self.manager.add_magnet(uri, save_dir)
        except Exception as e:
            logger.exception("Failed to add magnet link")
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't add that magnet link:\n{e}")
            return
        self.magnet_entry.clear()
        self._finish_add(handle, save_dir, kind="magnet", uri_or_path=uri)

    def on_add_torrent_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open .torrent file", "", "Torrent files (*.torrent)")
        if path:
            self.add_torrent_path(path)

    def add_torrent_path(self, path):
        """Adds a .torrent file already on disk -- the file picker's pick, or
        one the Browser tab just downloaded."""
        if not hasattr(self, "manager") or not path:
            logger.warning("Can't add %s: the Torrent tab's engine isn't available", path)
            return
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)
        try:
            handle = self.manager.add_torrent_file(path, save_dir)
        except Exception as e:
            logger.exception("Failed to add torrent file %s", path)
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't add that torrent:\n{e}")
            return
        self._finish_add(handle, save_dir, kind="file", uri_or_path=path)

    def _finish_add(self, handle, save_dir, kind, uri_or_path):
        name_hint = None
        try:
            if handle.status().has_metadata:
                name_hint = handle.status().name
        except Exception:
            pass

        dialog = AddTorrentDialog(self.manager, handle, self.settings, save_dir, name_hint, parent=self)
        dialog.exec()
        if dialog.result is None:
            return  # cancelled -- AddTorrentDialog already removed it from the session

        selected = dialog.result["selected"]
        if selected is not None and dialog.file_list is not None:
            try:
                priorities = [4 if i in selected else 0 for i in range(len(dialog.file_list))]
                handle.prioritize_files(priorities)
            except Exception:
                logger.exception("Failed to set file priorities")

        if not dialog.result["auto_start"]:
            try:
                handle.pause()
            except Exception:
                pass

        name = dialog.name_label.text()
        self._add_row(name, kind, uri_or_path, save_dir, handle, selected)
        self.save_state()

    def _restore_saved_torrents(self):
        for entry in torrent_state.load():
            try:
                save_dir = entry.get("save_path") or self.download_dir
                os.makedirs(save_dir, exist_ok=True)
                kind = entry.get("kind")
                uri_or_path = entry.get("uri_or_path")
                # Its fast-resume data first: back exactly as it was, with no
                # re-check of the files and no hunt for the magnet's metadata.
                handle = self.manager.add_from_resume(entry.get("info_hash"), save_dir)
                if handle is not None:
                    pass
                elif kind == "magnet":
                    handle = self.manager.add_magnet(uri_or_path, save_dir)
                elif kind == "file" and os.path.exists(uri_or_path):
                    handle = self.manager.add_torrent_file(uri_or_path, save_dir)
                else:
                    logger.warning("Skipping resume, source unavailable: %s", entry)
                    continue
                self._add_row(entry.get("name") or "Resuming...", kind, uri_or_path, save_dir,
                              handle, entry.get("selected"), pending_priorities=entry.get("selected"),
                              initial_progress=float(entry.get("progress") or 0.0), saved=entry)
            except Exception:
                logger.exception("Failed to resume torrent entry %s", entry)

    # -------------------------------------------------------- Row cards ---
    def _add_row(self, name, kind, uri_or_path, save_path, handle, selected,
                  pending_priorities="__unset__", initial_progress=0.0, saved=None):
        self.empty_label.setVisible(False)

        row_id = f"row{self._row_counter}"
        self._row_counter += 1

        # Painted directly, not QFrame#card QSS -- this card is nested
        # inside a QScrollArea's viewport (list_body), where a QSS-driven
        # background confirmed painted nothing at all (see widgets/card.py).
        # Its speed over the last minute is drawn in the card itself, behind
        # everything on it (widgets/speed_graph.py).
        card = GraphCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(6)

        top_row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: 600;")
        top_row.addWidget(name_label, 1)
        status_label = QLabel("Starting")
        status_label.setObjectName("muted")
        top_row.addWidget(status_label)
        card_layout.addLayout(top_row)

        progress_row = QHBoxLayout()
        t = theme.tokens(dark_mode=self._dark_mode())
        progress_bar = AnimatedProgressBar(
            QColor(255, 255, 255, 20) if self._dark_mode() else QColor(15, 23, 42, 20),
            t["progress"], t["success"])
        progress_row.addWidget(progress_bar, 1)
        pct_label = QLabel("0%")
        pct_label.setObjectName("mono")
        pct_label.setFixedWidth(44)
        pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(pct_label)
        card_layout.addLayout(progress_row)

        # The numbers: speeds over a live speed graph, how much is done,
        # time left, time spent, peers, ratio (widgets/stats_strip.py).
        stats = StatsStrip()

        # These were ⏸ / 📂 / 🗑 emoji glyphs and rendered as three empty
        # boxes (reported directly, with a screenshot) -- the bundled Inter
        # font has no coverage for those codepoints, and because Inter is set
        # as the *application* font (see main_window.py) Qt had no fallback
        # to reach Segoe UI Emoji for them. Real word labels avoid depending
        # on font glyph coverage entirely, and say what each button does
        # rather than leaving it to be guessed from a pictogram.
        # Row actions follow the "one primary, then overflow" shape rather
        # than laying every action out in a line. Five outlined accent-
        # coloured buttons per row put ~25 of them on a full screen, which
        # made the accent colour meaningless and -- worse -- left "Delete
        # Files" visually identical to "Open Folder". Now: the primary
        # (Pause/Resume, or Play once finished) is the only filled control,
        # Open Folder sits beside it as a quiet secondary, and both
        # destructive actions live behind the "..." menu, away from
        # anything clicked by habit.
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(0, 2, 0, 0)
        ctrl_row.setSpacing(6)
        pause_btn = Button("Pause")
        pause_btn.setObjectName("accent")
        pause_btn.setCursor(Qt.PointingHandCursor)
        pause_btn.setFixedHeight(30)
        pause_btn.setMinimumWidth(84)
        pause_btn.clicked.connect(lambda: self._toggle_pause(row_id))
        ctrl_row.addWidget(pause_btn)

        folder_btn = Button("Open Folder")
        folder_btn.setObjectName("quiet")
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.setFixedHeight(30)
        folder_btn.clicked.connect(lambda: self._open_row_folder(row_id))
        ctrl_row.addWidget(folder_btn)

        # Destructive actions are deliberately NOT inline. Both still exist
        # as two explicit choices (rather than one button plus an
        # "...and the files?" prompt afterwards), they have just moved one
        # click away so they cannot be hit by muscle memory.
        # Drawn, not typed. As a "..." text label this rendered at the
        # label's own size and weight and came out as one faint dot -- an
        # overflow control nobody would find. Three explicit dots at a fixed
        # size read the same at any font, and the padding reset stops the
        # shared #quiet rule (4px/12px, sized for word buttons) from
        # squeezing them out of a 32px square.
        more_btn = Button()
        more_btn.setObjectName("quiet")
        more_btn.setIcon(_dots_icon(theme.tokens(self._dark_mode())["text_muted"]))
        more_btn.setIconSize(QSize(14, 4))
        more_btn.setStyleSheet("padding: 0px;")
        more_btn.setCursor(Qt.PointingHandCursor)
        more_btn.setFixedSize(32, 30)
        more_btn.setToolTip("More actions")
        more_btn.setAccessibleName("More actions")
        more_btn.clicked.connect(lambda: self._show_row_menu(row_id, more_btn))
        ctrl_row.addSpacing(2)
        ctrl_row.addWidget(more_btn)

        ctrl_row.addStretch(1)

        # Far right of the row, sitting under the percentage -- hidden until
        # the download actually finishes, so a completed torrent offers the
        # obvious next action (watch it) right where the progress was.
        play_btn = Button("▶  Play")
        play_btn.setObjectName("success")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setFixedHeight(30)
        play_btn.setMinimumWidth(84)
        play_btn.setToolTip("Open the downloaded file in your default player.")
        play_btn.clicked.connect(lambda: self._play_row(row_id))
        play_btn.setVisible(False)
        ctrl_row.addWidget(play_btn)

        card_layout.addLayout(ctrl_row)
        card_layout.addWidget(stats)

        # Insert before the trailing stretch so new rows always land above it.
        self.list_body_layout.insertWidget(self.list_body_layout.count() - 1, card)
        if saved is None:
            motion.grow_in(card)
        saved = dict(saved or {})
        if saved.get("stats_v") == 3 and saved.get("took_s") is None:
            # Not watched finishing, so its date came from History -- which
            # the re-check bug had re-dated to that launch. Its files say.
            saved.pop("finished_at", None)
        elif saved.get("stats_v") not in (3, 4):
            # Written by the 2.5 test builds, which stamped a torrent that was
            # already complete on disk -- re-checked, or its metadata fetched
            # again, at launch -- as "finished now, took seconds". Those two
            # values are dropped; the finish time is read from History.
            saved.pop("took_s", None)
            saved.pop("finished_at", None)

        QTimer.singleShot(0, self._reschedule_poll)
        self.rows[row_id] = {
            "handle": handle, "save_path": save_path, "kind": kind, "uri_or_path": uri_or_path,
            "selected": selected, "card": card, "name_label": name_label, "status_label": status_label,
            "progress_bar": progress_bar, "pct_label": pct_label, "stats": stats, "graph": card,
            # Time is kept by the app, not libtorrent: a torrent is re-added
            # on every launch, so libtorrent's own clocks restart each time.
            "added_at": float(saved.get("added_at") or time.time()),
            "active_s": float(saved.get("active_s") or 0.0),
            "took_s": saved.get("took_s"),
            # A torrent that was already complete when it was added back (at
            # launch) wasn't watched finishing: its finish time comes from
            # History, which recorded it at the time, and how long it took is
            # unknown -- never "now" and "3 seconds".
            "finished_at": saved.get("finished_at") or (
                self._files_finish_time(handle, save_path) or self._history_finish_time(name)
                if initial_progress >= 1.0 else None),
            "uploaded_base": int(saved.get("uploaded") or 0),
            # Payload this app actually downloaded for it, across launches.
            "downloaded_base": int(saved.get("downloaded") or 0),
            "downloaded": int(saved.get("downloaded") or 0),
            "uploaded": int(saved.get("uploaded") or 0),
            "last_poll": time.monotonic(),
            "pause_btn": pause_btn, "play_btn": play_btn,
            # A row restored from torrents.json at 100% was already recorded
            # in history back when it first completed, in whatever session
            # that was -- only a row starting below 100% (a genuinely new
            # torrent) still needs its completion caught by _poll() below.
            # Without this, every completed torrent silently re-added itself
            # to history on every single relaunch, even right after Clear
            # History, since this flag lives only in memory and used to
            # always start False regardless of restored progress.
            "history_recorded": initial_progress >= 1.0,
            "was_complete": None,
            # saved_progress is the floor shown while libtorrent re-hashes on
            # resume; last_progress is what gets written back to torrents.json.
            "saved_progress": initial_progress,
            "last_progress": initial_progress,
            "pending_priorities": selected if pending_priorities == "__unset__" else pending_priorities,
        }
        # Painted immediately rather than waiting for the first 1s poll, so a
        # restored torrent never flashes 0% before its real value arrives.
        if initial_progress > 0:
            progress_bar.setValue(int(initial_progress * 100))
            pct_label.setText(f"{initial_progress * 100:.0f}%")
            if initial_progress >= 1.0:
                progress_bar.set_complete(True)
                play_btn.setVisible(True)
        return row_id

    def _toggle_pause(self, row_id):
        info = self.rows.get(row_id)
        if not info:
            return
        try:
            if info["handle"].status().paused:
                self.manager.resume(info["handle"])
            else:
                self.manager.pause(info["handle"])
            # Repaint the row now instead of waiting up to a second for the
            # next poll -- otherwise the button appears not to have reacted.
            self._poll()
        except Exception:
            logger.exception("Failed to toggle pause for %s", row_id)

    def _play_row(self, row_id):
        """Opens the finished download in whatever the system plays it with.
        Picks the largest file in the save path -- for a multi-file torrent
        (a release folder with samples, subtitles, artwork) that is reliably
        the actual feature rather than a sample clip or a .nfo."""
        info = self.rows.get(row_id)
        if not info:
            return
        target = self._torrent_main_file(info)
        if not target or not os.path.exists(target):
            QMessageBox.information(
                self, config.APP_NAME,
                f"Couldn't find a file to open in:\n{info['save_path']}")
            return
        try:
            os.startfile(target)
        except Exception:
            logger.exception("Failed to open %s", target)
            QMessageBox.information(self, config.APP_NAME, f"The file is at:\n{target}")

    def _show_row_menu(self, row_id, anchor_btn):
        """Overflow menu for a torrent row: the low-frequency and
        destructive actions that used to sit inline as their own buttons.

        Both destructive entries are separated from the rest by a divider
        and are the only red items, so the dangerous choices are visually
        distinct from "copy a link" -- which was exactly what the old
        all-identical row of pills could not express."""
        if row_id not in self.rows:
            return
        t = theme.tokens(dark_mode=self._dark_mode())
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {t['card_bg_solid']};
                color: {t['text']};
                border: 1px solid {t['card_border']};
                border-radius: 9px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {t['hover_overlay']}; }}
            QMenu::separator {{ height: 1px; background: {t['divider']}; margin: 4px 8px; }}
        """)

        copy_action = menu.addAction("Copy magnet link")
        copy_action.triggered.connect(lambda: self._copy_row_magnet(row_id))

        menu.addSeparator()

        remove_action = menu.addAction("Remove torrent")
        remove_action.setToolTip(
            "Stop and remove this torrent, but keep the files already downloaded.")
        remove_action.triggered.connect(
            lambda: self._remove_row(row_id, delete_files=False))

        delete_action = menu.addAction("Delete files from disk")
        delete_action.setToolTip(
            "Remove this torrent AND delete everything it downloaded from disk.")
        delete_action.triggered.connect(
            lambda: self._remove_row(row_id, delete_files=True))

        # QSS cannot target an individual QMenu item, so the destructive
        # pair is marked with a small red dot icon instead of red text.
        # Combined with the divider above them, that is what separates
        # "copy a link" from "erase 31 GB" -- the distinction the old row
        # of identical pills could not make at all.
        dot = _dot_icon(t["danger"])
        for act in (remove_action, delete_action):
            act.setIcon(dot)
            act.setIconVisibleInMenu(True)

        menu.exec(anchor_btn.mapToGlobal(anchor_btn.rect().bottomLeft()))

    def _copy_row_magnet(self, row_id):
        """Puts the row's magnet URI (or its .torrent path, for a file-added
        torrent) on the clipboard -- the only genuinely new action this menu
        introduces, and the one that made an overflow worth having rather
        than just hiding two buttons."""
        info = self.rows.get(row_id)
        if not info:
            return
        value = info.get("uri_or_path") or ""
        if value:
            QApplication.clipboard().setText(value)

    def _open_row_folder(self, row_id):
        info = self.rows.get(row_id)
        if not info:
            return
        try:
            os.startfile(info["save_path"])
        except Exception:
            logger.exception("Failed to open folder %s", info["save_path"])
            QMessageBox.information(self, config.APP_NAME, f"Files are saved at:\n{info['save_path']}")

    def _remove_row(self, row_id, delete_files=False):
        info = self.rows.get(row_id)
        if not info:
            return
        # Only the destructive path confirms. "Remove Torrent" keeps every
        # downloaded byte on disk, so it's cheap to undo (re-add the magnet)
        # and doesn't warrant a prompt; deleting the files is not undoable.
        if delete_files:
            name = info["name_label"].text()
            confirmed = QMessageBox.question(
                self, config.APP_NAME,
                f"Permanently delete the downloaded files for:\n\n{name}\n\n"
                f"from {info['save_path']}?\n\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) == QMessageBox.Yes
            if not confirmed:
                return
        self.rows.pop(row_id, None)
        self._reschedule_poll()
        self.manager.remove(info["handle"], delete_files=delete_files)
        card = info["card"]

        def drop():
            card.deleteLater()
            if not self.rows:
                self.empty_label.setVisible(True)
        # Folds away; the rows below slide up into its place.
        motion.shrink_out(card, drop)
        self.save_state()

    # ------------------------------------------------------------- Polling ---
    def _estimate_eta(self, status):
        if status.download_rate <= 0:
            return "—"
        remaining = status.total_wanted - status.total_wanted_done
        if remaining <= 0:
            return "—"
        return format_eta(remaining / status.download_rate) or "—"

    def _state_label(self, status):
        # A finished torrent reads "Completed", not "Seeding" -- asked for
        # directly. libtorrent calls it seeding because it's still uploading,
        # which is true but answers a question nobody asked; what matters at
        # a glance is that the download is done. Checked before the paused
        # branch so a finished-then-paused torrent still says Completed.
        if status.progress >= 1.0:
            return "Completed"
        if status.paused:
            return "Paused"
        if status.is_seeding:
            return "Seeding"
        return str(status.state).replace("_", " ").title()

    def _reschedule_poll(self):
        """Once a second while the tab is in view, every five behind other
        tabs (downloads still finish and get recorded), and not at all with
        no torrents -- an empty tab costs nothing."""
        timer = getattr(self, "_poll_timer", None)
        if timer is None:
            return
        if not self.rows:
            timer.stop()
            return
        interval = 1000 if self.isVisible() else 5000
        if timer.interval() != interval or not timer.isActive():
            timer.start(interval)

    def showEvent(self, event):
        super().showEvent(event)
        self._reschedule_poll()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._reschedule_poll()

    def _poll(self):
        self.manager.process_alerts()
        self._polls += 1
        if self._polls % 30 == 0:
            # Resume data for whatever has changed, every half minute: a
            # crash or a power cut loses at most that much re-checking.
            for info in self.rows.values():
                self.manager.request_resume(info["handle"])
        total_down = total_up = 0
        for row_id, info in list(self.rows.items()):
            handle = info["handle"]
            if not handle.is_valid():
                continue
            status = handle.status()
            total_down += status.download_rate
            total_up += status.upload_rate

            if status.has_metadata:
                if status.name:
                    info["name_label"].setText(status.name)
                if info.get("pending_priorities"):
                    try:
                        total_files = handle.torrent_file().files().num_files()
                        sel = info["pending_priorities"]
                        priorities = [4 if i in sel else 0 for i in range(total_files)]
                        handle.prioritize_files(priorities)
                    except Exception:
                        logger.exception("Failed to apply resumed file priorities for %s", row_id)
                    info["pending_priorities"] = None

            # libtorrent re-hashes the files on resume, and during that pass
            # status.progress counts the *check*, climbing from 0 -- which is
            # exactly why a restored torrent (even a finished one) appeared to
            # restart from zero (reported directly). Hold the last known
            # progress until the check finishes and the number means
            # "downloaded" again.
            checking = "checking" in str(status.state).lower()
            shown = max(status.progress, info.get("saved_progress", 0.0)) if checking else status.progress
            if not checking:
                info["saved_progress"] = 0.0
            info["last_progress"] = shown

            info["progress_bar"].setValue(int(shown * 100))
            info["progress_bar"].set_animating(
                not status.paused and shown < 1.0)
            info["pct_label"].setText(f"{shown * 100:.0f}%")
            state_str = self._state_label(status)
            info["status_label"].setText(state_str)
            if shown >= 1.0:
                info["pause_btn"].setText("Seed" if status.paused else "Stop seeding")
            else:
                info["pause_btn"].setText("Resume" if status.paused else "Pause")

            # Green bar + Play button once finished. Only touched when the
            # state actually flips, so the stylesheet isn't rebuilt every
            # second for every row.
            complete = shown >= 1.0
            if complete != info["was_complete"]:
                info["was_complete"] = complete
                info["progress_bar"].set_complete(complete)
                info["play_btn"].setVisible(complete)

            if complete and not info["history_recorded"]:
                info["history_recorded"] = True
                self._record_completion(info)

            self._update_stats(info, status, shown, complete, checking)

        self.total_down_label.setText(f"↓ {humanize_rate(total_down)}")
        self.total_up_label.setText(f"↑ {humanize_rate(total_up)}")
        self._update_totals()
        self._autosave_state()

    @staticmethod
    def _files_finish_time(handle, save_path):
        """When the torrent's files were last written -- when its last piece
        landed, i.e. when it finished. Re-checking only reads them, so this
        survives every relaunch, where a "finished now" stamp did not."""
        try:
            if handle is None or not save_path or not handle.status().has_metadata:
                return None
            files = handle.torrent_file().files()
            stamps = []
            for i in range(files.num_files()):
                path = os.path.join(save_path, files.file_path(i))
                if os.path.exists(path):
                    stamps.append(os.path.getmtime(path))
            return max(stamps) if stamps else None
        except Exception:   # noqa: BLE001
            return None

    def _history_finish_time(self, name):
        """When History first recorded this torrent finishing, or None. The
        first record, not the latest: earlier builds added one at every
        launch, each dated that day."""
        try:
            return download_history.first_completion("torrent", name)
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't read History for %s", name)
        return None

    def _update_stats(self, info, status, shown, complete, checking):
        now = time.monotonic()
        dt = min(5.0, max(0.0, now - info.get("last_poll", now)))
        info["last_poll"] = now
        downloading = not status.paused and not complete and not checking
        if downloading and status.has_metadata:
            info["active_s"] = info.get("active_s", 0.0) + dt
        info["downloaded"] = info.get("downloaded_base", 0) + int(status.total_payload_download or 0)
        wanted_bytes = int(status.total_wanted or 0)
        # Finished *here*: most of it came over the network through this app.
        # A torrent whose files were already on disk also turns complete a
        # few seconds after launch (metadata fetched again, files checked),
        # and that must not read as "finished today, took 4 s".
        really_downloaded = wanted_bytes > 0 and info["downloaded"] >= 0.5 * wanted_bytes
        if complete and info.get("took_s") is None and really_downloaded and info.get("active_s", 0) > 0:
            info["took_s"] = round(info["active_s"])
            info["finished_at"] = time.time()
        info["uploaded"] = info.get("uploaded_base", 0) + int(status.total_upload or 0)
        done = int(status.total_wanted_done or 0)
        wanted = int(status.total_wanted or 0)
        ratio = info["uploaded"] / done if done else 0.0
        seeds = int(getattr(status, "num_seeds", 0) or 0)
        peers = f"{status.num_peers}" + (f" · {seeds} seeds" if seeds else "")
        stats = info["stats"]
        graph = info.get("graph")
        if graph is not None:
            paused = bool(status.paused)
            graph.push(0 if paused else status.download_rate, 0 if paused else status.upload_rate)
        if complete and not info.get("finished_at") and info.get("took_s") is None:
            info["finished_at"] = self._files_finish_time(info.get("handle"), info.get("save_path"))
        if complete:
            took = info.get("took_s")
            avg = (wanted / took) if took else 0
            items = [
                ("Took", _duration(took) if took else "--", "done"),
                ("Avg speed", humanize_rate(avg) if avg else "--", None),
                ("Size", humanize_size(wanted) if wanted else "--", None),
                ("↑ Up", humanize_rate(status.upload_rate) if not status.paused else "--", "up"),
                ("Uploaded", humanize_size(info["uploaded"]) if info["uploaded"] else "0 B", None),
                ("Ratio", f"{ratio:.2f}", None),
                ("Finished", _when(info.get("finished_at")), "faint"),
            ]
        else:
            waiting = not status.has_metadata
            items = [
                ("↓ Down", "--" if status.paused else humanize_rate(status.download_rate), "down"),
                ("↑ Up", "--" if status.paused else humanize_rate(status.upload_rate), "up"),
                ("Done", "--" if waiting else f"{humanize_size(done) or '0 B'} / {humanize_size(wanted) or '--'}", None),
                ("Left", "--" if status.paused else self._estimate_eta(status), None),
                ("Active", _duration(info.get("active_s", 0)), None),
                ("Peers", peers, "warn" if not status.paused and status.num_peers == 0 else None),
                ("Ratio", f"{ratio:.2f}", None),
            ]
        stats.set_items(items)

    def _update_totals(self):
        downloading = seeding = paused = 0
        for info in self.rows.values():
            try:
                st = info["handle"].status()
            except Exception:   # noqa: BLE001
                continue
            if st.progress >= 1.0:
                seeding += 1
            elif st.paused:
                paused += 1
            else:
                downloading += 1
        parts = []
        if downloading:
            parts.append(f"{downloading} downloading")
        if seeding:
            parts.append(f"{seeding} complete")
        if paused:
            parts.append(f"{paused} paused")
        label = getattr(self, "totals_label", None)
        if label is not None:
            label.setText("  ·  ".join(parts))

    # ------------------------------------------------------------- History ---
    def _torrent_main_file(self, info):
        """Largest file belonging to *this* torrent.

        Asking the handle for its own file list matters: the fallback below
        walks the whole save directory, and everything shares one Torrents
        folder by default, so the "largest file" there was whatever the
        biggest download on disk happened to be. Every torrent's history
        entry ended up pointing at the same unrelated file (a 14 GB game
        archive, in practice) and History's Play button opened a .bin
        Windows has no handler for -- reported as torrent files not playing.
        """
        handle = info["handle"]
        try:
            if handle.status().has_metadata:
                files = handle.torrent_file().files()
                best_i, best_size = None, -1
                for i in range(files.num_files()):
                    size = files.file_size(i)
                    if size > best_size:
                        best_i, best_size = i, size
                if best_i is not None:
                    path = os.path.join(info["save_path"], files.file_path(best_i))
                    if os.path.exists(path):
                        return path
        except Exception:
            logger.exception("Failed to read torrent file list; falling back to a disk scan")
        # No metadata (or it failed): scanning the save dir is all that's
        # left, and it's still right for a torrent in its own subfolder.
        return self._find_largest_file(info["save_path"])

    def _record_completion(self, info):
        name = info["name_label"].text()
        if download_history.first_completion("torrent", name):
            # Already in History: it finished in an earlier session and only
            # came back complete now (re-checked, or re-added).
            if not info.get("finished_at"):
                info["finished_at"] = download_history.first_completion("torrent", name)
            return
        try:
            save_path = info["save_path"]
            status = info["handle"].status()
            main_file = self._torrent_main_file(info)
            download_history.add_entry(
                "torrent", info["name_label"].text(), main_file, save_path, status.total_wanted or 0
            )
        except Exception:
            logger.exception("Failed to record torrent completion in history")

    def _find_largest_file(self, directory):
        largest, largest_size = None, -1
        try:
            for root, _dirs, files in os.walk(directory):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        size = os.path.getsize(fpath)
                    except OSError:
                        continue
                    if size > largest_size:
                        largest_size, largest = size, fpath
        except Exception:
            logger.exception("Failed to scan for largest file in %s", directory)
        return largest

    # --------------------------------------------------------- Persistence ---
    def _state_entries(self):
        return [{
            "kind": info["kind"],
            "uri_or_path": info["uri_or_path"],
            "save_path": info["save_path"],
            "selected": list(info["selected"]) if info["selected"] is not None else None,
            "name": info["name_label"].text(),
            # Persisted so a restart can show the bar where it actually left
            # off instead of snapping to 0% -- see _add_row's saved_progress.
            "progress": info.get("last_progress", 0.0),
            # The stats' clocks and totals, which libtorrent forgets.
            "added_at": round(info.get("added_at") or 0, 1),
            "active_s": round(info.get("active_s") or 0.0),
            "took_s": info.get("took_s"),
            "finished_at": info.get("finished_at"),
            "uploaded": info.get("uploaded", 0),
            "downloaded": info.get("downloaded", 0),
            "info_hash": TorrentManager.key(info["handle"]),
            "stats_v": 4,
        } for info in self.rows.values()]

    def shutdown(self):
        """On quit: fresh fast-resume data for every torrent, then the list."""
        try:
            if self._manager is not None:
                self._manager.save_all_resume([info["handle"] for info in self.rows.values()])
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't save fast-resume data on exit")
        self.save_state()

    def save_state(self):
        entries = self._state_entries()
        torrent_state.save(entries)
        self._last_saved_state = entries

    def _autosave_state(self):
        """Called from the 1s poll. Until this existed, torrents.json was
        only written on add, on remove, and on a clean quit -- so a crash,
        a force-kill, or a Windows shutdown/power loss left it stale and the
        torrent was silently gone on next launch, even though its data was
        sitting right there on disk. Resuming across a *computer* restart
        was asked for explicitly, and that's exactly the case a
        quit-handler alone can't cover.

        Only writes when the serialized state actually differs from what was
        last written, so a steady queue costs one comparison per second and
        no disk I/O at all. The name is part of that state, which also means
        the real torrent name gets persisted once metadata resolves it,
        replacing the "Resuming..." placeholder a restored row starts with.
        """
        entries = self._state_entries()
        if entries != getattr(self, "_last_saved_state", None):
            torrent_state.save(entries)
            self._last_saved_state = entries
