"""Add-torrent dialog: opens immediately when a
magnet/.torrent is added (matching uTorrent's own add-torrent screen), polls
the libtorrent handle for metadata while the user waits, swaps a placeholder
for the real file table once available. Cancel tears the torrent back out of
the session since it was already added to start fetching metadata.

The old CTk version used a ttk.Treeview with a manually-toggled unicode
checkbox column; Qt's QTableWidget has real per-item checkboxes, so that
click-region-detection dance isn't needed here.
"""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app import config
from app.logging_setup import get_logger
from app.utils.formatting import humanize_size

logger = get_logger("add_torrent_dialog")


class AddTorrentDialog(QDialog):
    def __init__(self, manager, handle, settings, save_path, name_hint=None, parent=None):
        """result is set on close: None if cancelled (handle already removed
        from the session), otherwise {"selected": set(idx)|None, "auto_start": bool}.
        selected is None when the dialog closed before metadata ever arrived,
        meaning "everything, don't wait."
        """
        super().__init__(parent)
        self.manager = manager
        self.handle = handle
        self.result = None
        self.file_list = None
        self.checked = {}
        self._elapsed_ticks = 0

        self.setWindowTitle("Adding Torrent")
        self.resize(620, 540)
        self.setMinimumSize(520, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(6)

        self.name_label = QLabel(name_hint or "New Torrent")
        self.name_label.setObjectName("heading")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        self.status_label = QLabel("Retrieving file details...")
        self.status_label.setObjectName("muted")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setEnabled(False)
        self.select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setEnabled(False)
        self.select_none_btn.clicked.connect(self._select_none)
        btn_row.addWidget(self.select_none_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.placeholder_label = QLabel("Retrieving file details from peers/trackers...")
        self.placeholder_label.setObjectName("muted")
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.placeholder_label, 1)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["File", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setVisible(False)
        layout.addWidget(self.table, 1)

        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("Save to:"))
        save_entry = QLineEdit(save_path)
        save_entry.setReadOnly(True)
        save_row.addWidget(save_entry, 1)
        layout.addLayout(save_row)

        self.autostart_check = QCheckBox("Start downloading when added")
        self.autostart_check.setChecked(True)
        layout.addWidget(self.autostart_check)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        footer.addWidget(cancel_btn)
        self.add_btn = QPushButton("Add")
        self.add_btn.setObjectName("accent")
        self.add_btn.clicked.connect(self._confirm)
        footer.addWidget(self.add_btn)
        layout.addLayout(footer)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_metadata)
        self._poll_timer.start(500)
        self._poll_metadata()

    def _poll_metadata(self):
        try:
            if self.file_list is None and self.handle.is_valid():
                status = self.handle.status()
                if status.has_metadata:
                    self._populate_files()
                    self._poll_timer.stop()
                else:
                    self._elapsed_ticks += 1
                    self._update_waiting_text(status)
        except Exception:
            logger.exception("Metadata poll failed")

    def _update_waiting_text(self, status):
        seconds = self._elapsed_ticks // 2
        peers = getattr(status, "num_peers", 0)
        peer_bit = f"{peers} peer(s) found so far. " if peers else "Looking for peers... "
        if seconds < 15:
            self.placeholder_label.setText(f"Retrieving file details from peers/trackers...\n{peer_bit}")
        else:
            self.placeholder_label.setText(
                f"Still retrieving file details after {seconds}s.\n{peer_bit}"
                "Magnets with few seeders can take a while (or may need a VPN/different "
                "network if trackers are blocked).\n\nTip: click Add now to start with all "
                "files once metadata does arrive -- no need to keep waiting here."
            )

    def _populate_files(self):
        try:
            ti = self.handle.torrent_file()
            files = ti.files()
            self.file_list = [(i, files.file_path(i), files.file_size(i)) for i in range(files.num_files())]
            self.checked = {idx: True for idx, _, _ in self.file_list}
            name = ti.name()
        except Exception:
            logger.exception("Failed to read torrent metadata")
            return

        self.name_label.setText(name)
        total = sum(size for _, _, size in self.file_list)
        self.status_label.setText(f"{len(self.file_list)} file(s)  •  {humanize_size(total)} total")

        self.placeholder_label.setVisible(False)
        self.table.setVisible(True)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.file_list))
        for row, (idx, path, size) in enumerate(self.file_list):
            file_item = QTableWidgetItem(path)
            file_item.setFlags(file_item.flags() | Qt.ItemIsUserCheckable)
            file_item.setCheckState(Qt.Checked)
            file_item.setData(Qt.UserRole, idx)
            self.table.setItem(row, 0, file_item)
            size_item = QTableWidgetItem(humanize_size(size))
            size_item.setFlags(size_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, size_item)
        self.table.blockSignals(False)

        self.select_all_btn.setEnabled(True)
        self.select_none_btn.setEnabled(True)

    def _on_item_changed(self, item):
        if item.column() != 0:
            return
        idx = item.data(Qt.UserRole)
        self.checked[idx] = item.checkState() == Qt.Checked

    def _select_all(self):
        self._set_all(True)

    def _select_none(self):
        self._set_all(False)

    def _set_all(self, value):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            idx = item.data(Qt.UserRole)
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)
            self.checked[idx] = value
        self.table.blockSignals(False)

    def _confirm(self):
        if self.file_list is not None and not any(self.checked.values()):
            QMessageBox.warning(self, config.APP_NAME, "Select at least one file to download.")
            return
        self._poll_timer.stop()
        selected = {idx for idx, c in self.checked.items() if c} if self.file_list is not None else None
        self.result = {"selected": selected, "auto_start": self.autostart_check.isChecked()}
        self.accept()

    def _cancel(self):
        self._poll_timer.stop()
        self.result = None
        try:
            self.manager.remove(self.handle)
        except Exception:
            logger.exception("Failed to remove cancelled torrent from session")
        self.reject()
