"""Torrent tab: paste a magnet link or open a
.torrent file, download it. Each torrent renders as its own card in a
scrollable list, polled on a 1s QTimer (same cadence as the CTk version's
.after(1000, ...) loop).
"""
import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from PySide6.QtGui import QColor

from app import config
from app.core.torrent_manager import LIBTORRENT_AVAILABLE, TorrentManager
from app.logging_setup import get_logger
from app.utils import (
    download_history, protocol_handler, settings as settings_store, startup, torrent_state,
)
from app.utils.formatting import format_eta, humanize_rate, humanize_size

from . import theme
from .dialogs.add_torrent_dialog import AddTorrentDialog
from .widgets import AnimatedProgressBar, _PaintedCard, make_card

logger = get_logger("torrent_tab")


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

        if not LIBTORRENT_AVAILABLE:
            self._build_missing_engine_ui()
            return

        self.manager = TorrentManager()
        self._build_ui()
        self._restore_saved_torrents()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(1000)

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

        self.install_btn = QPushButton("Install libtorrent")
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        add_card, add_layout = make_card("ADD TORRENT")
        root.addWidget(add_card)

        add_layout.addWidget(self._label("Magnet link", "muted"))
        magnet_row = QHBoxLayout()
        self.magnet_entry = QLineEdit()
        self.magnet_entry.setPlaceholderText("magnet:?xt=urn:btih:...")
        magnet_row.addWidget(self.magnet_entry, 1)
        add_magnet_btn = QPushButton("Add Magnet")
        add_magnet_btn.setObjectName("accent")
        add_magnet_btn.clicked.connect(self.on_add_magnet)
        magnet_row.addWidget(add_magnet_btn)
        add_layout.addLayout(magnet_row)

        open_file_btn = QPushButton("Open .torrent file...")
        open_file_btn.clicked.connect(self.on_add_torrent_file)
        open_file_row = QHBoxLayout()
        open_file_row.addWidget(open_file_btn)
        open_file_row.addStretch(1)
        add_layout.addLayout(open_file_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(self._label("Save to:", "muted"))
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        add_layout.addLayout(dir_row)

        self.startup_check = QCheckBox("Launch at Windows startup (so background downloads can resume)")
        self.startup_check.setChecked(startup.is_enabled())
        self.startup_check.toggled.connect(self._on_startup_toggle)
        add_layout.addWidget(self.startup_check)

        self.magnet_handler_check = QCheckBox(
            "Open magnet links with Awesome Downloader")
        self.magnet_handler_check.setToolTip(
            "Clicking a magnet link in your browser opens it here instead of "
            "whatever torrent client is currently registered. Per-user only; "
            "unticking restores the previous handler.")
        self.magnet_handler_check.setChecked(protocol_handler.is_default())
        self.magnet_handler_check.toggled.connect(self._on_magnet_handler_toggle)
        add_layout.addWidget(self.magnet_handler_check)

        list_card, list_layout = make_card()
        root.addWidget(list_card, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        self.list_body = QWidget()
        self.list_body.setStyleSheet("background: transparent;")
        self.list_body_layout = QVBoxLayout(self.list_body)
        self.list_body_layout.setContentsMargins(0, 0, 0, 0)
        self.list_body_layout.setSpacing(6)
        self.empty_label = QLabel("No torrents yet -- add a magnet link or .torrent file above.")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.list_body_layout.addWidget(self.empty_label)
        self.list_body_layout.addStretch(1)
        scroll.setWidget(self.list_body)
        list_layout.addWidget(scroll)

        footer = QHBoxLayout()
        self.total_down_label = QLabel("↓ 0 B/s")
        self.total_down_label.setObjectName("muted")
        footer.addWidget(self.total_down_label)
        self.total_up_label = QLabel("↑ 0 B/s")
        self.total_up_label.setObjectName("muted")
        footer.addWidget(self.total_up_label)
        footer.addStretch(1)
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
        track = QColor(255, 255, 255, 26) if self._dark_mode() else QColor(0, 0, 0, 26)
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
            QMessageBox.information(
                self, config.APP_NAME,
                "Windows has magnet links locked to another app"
                + (f" ({owner})" if owner else "") + ".\n\n"
                "Open Settings > Apps > Default apps, search for \"magnet\", "
                "and pick Awesome Downloader there -- Windows only allows that "
                "change from its own settings screen.")

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
        if not path:
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
                if kind == "magnet":
                    handle = self.manager.add_magnet(uri_or_path, save_dir)
                elif kind == "file" and os.path.exists(uri_or_path):
                    handle = self.manager.add_torrent_file(uri_or_path, save_dir)
                else:
                    logger.warning("Skipping resume, source unavailable: %s", entry)
                    continue
                self._add_row(entry.get("name") or "Resuming...", kind, uri_or_path, save_dir,
                              handle, entry.get("selected"), pending_priorities=entry.get("selected"),
                              initial_progress=float(entry.get("progress") or 0.0))
            except Exception:
                logger.exception("Failed to resume torrent entry %s", entry)

    # -------------------------------------------------------- Row cards ---
    def _add_row(self, name, kind, uri_or_path, save_path, handle, selected,
                  pending_priorities="__unset__", initial_progress=0.0):
        self.empty_label.setVisible(False)

        row_id = f"row{self._row_counter}"
        self._row_counter += 1

        # Painted directly, not QFrame#card QSS -- this card is nested
        # inside a QScrollArea's viewport (list_body), where a QSS-driven
        # background confirmed painted nothing at all (see widgets/card.py).
        card = _PaintedCard()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(3)

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
            QColor(255, 255, 255, 26) if self._dark_mode() else QColor(0, 0, 0, 26),
            t["progress"], t["success"])
        progress_row.addWidget(progress_bar, 1)
        pct_label = QLabel("0%")
        pct_label.setFixedWidth(40)
        pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(pct_label)
        card_layout.addLayout(progress_row)

        detail_label = QLabel("")
        detail_label.setObjectName("muted")
        card_layout.addWidget(detail_label)

        # These were ⏸ / 📂 / 🗑 emoji glyphs and rendered as three empty
        # boxes (reported directly, with a screenshot) -- the bundled Inter
        # font has no coverage for those codepoints, and because Inter is set
        # as the *application* font (see main_window.py) Qt had no fallback
        # to reach Segoe UI Emoji for them. Real word labels avoid depending
        # on font glyph coverage entirely, and say what each button does
        # rather than leaving it to be guessed from a pictogram.
        # All four row actions share the outlined "pill" shape; only the
        # destructive one is red. See theme.py's QPushButton#pill comment.
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        pause_btn = QPushButton("Pause")
        pause_btn.setObjectName("pill")
        pause_btn.setCursor(Qt.PointingHandCursor)
        pause_btn.setFixedHeight(28)
        pause_btn.clicked.connect(lambda: self._toggle_pause(row_id))
        ctrl_row.addWidget(pause_btn)

        folder_btn = QPushButton("Open Folder")
        folder_btn.setObjectName("pill")
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.setFixedHeight(28)
        folder_btn.clicked.connect(lambda: self._open_row_folder(row_id))
        ctrl_row.addWidget(folder_btn)

        # Split into two explicit buttons instead of one 🗑 that popped a
        # "also delete the files?" Yes/No dialog -- asked for directly, and
        # it makes the destructive choice visible up front rather than
        # hiding it behind a prompt after the fact.
        remove_btn = QPushButton("Remove Torrent")
        remove_btn.setObjectName("pill")
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.setFixedHeight(28)
        remove_btn.setToolTip("Stop and remove this torrent, but keep the files already downloaded.")
        remove_btn.clicked.connect(lambda: self._remove_row(row_id, delete_files=False))
        ctrl_row.addWidget(remove_btn)

        delete_btn = QPushButton("Delete Files")
        delete_btn.setObjectName("danger")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setFixedHeight(28)
        delete_btn.setToolTip("Remove this torrent AND delete everything it downloaded from disk.")
        delete_btn.clicked.connect(lambda: self._remove_row(row_id, delete_files=True))
        ctrl_row.addWidget(delete_btn)

        ctrl_row.addStretch(1)

        # Far right of the row, sitting under the percentage -- hidden until
        # the download actually finishes, so a completed torrent offers the
        # obvious next action (watch it) right where the progress was.
        play_btn = QPushButton("▶  Play")
        play_btn.setObjectName("success")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setFixedHeight(28)
        play_btn.setToolTip("Open the downloaded file in your default player.")
        play_btn.clicked.connect(lambda: self._play_row(row_id))
        play_btn.setVisible(False)
        ctrl_row.addWidget(play_btn)

        card_layout.addLayout(ctrl_row)

        # Insert before the trailing stretch so new rows always land above it.
        self.list_body_layout.insertWidget(self.list_body_layout.count() - 1, card)

        self.rows[row_id] = {
            "handle": handle, "save_path": save_path, "kind": kind, "uri_or_path": uri_or_path,
            "selected": selected, "card": card, "name_label": name_label, "status_label": status_label,
            "progress_bar": progress_bar, "pct_label": pct_label, "detail_label": detail_label,
            "pause_btn": pause_btn, "play_btn": play_btn, "history_recorded": False,
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
        self.manager.remove(info["handle"], delete_files=delete_files)
        info["card"].deleteLater()
        if not self.rows:
            self.empty_label.setVisible(True)
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

    def _poll(self):
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

            size_str = humanize_size(status.total_wanted) if status.total_wanted else "—"
            down_str = humanize_rate(status.download_rate)
            up_str = humanize_rate(status.upload_rate)
            eta_str = self._estimate_eta(status)
            info["detail_label"].setText(
                f"{size_str}  •  ↓{down_str}  •  ↑{up_str}  •  {status.num_peers} peers  •  ETA {eta_str}"
            )

        self.total_down_label.setText(f"↓ {humanize_rate(total_down)}")
        self.total_up_label.setText(f"↑ {humanize_rate(total_up)}")
        self._autosave_state()

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
        } for info in self.rows.values()]

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
