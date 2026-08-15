"""History tab: everything successfully
downloaded, newest first, with a real thumbnail where cheap to produce and a
placeholder icon otherwise. Open Folder and Play use os.startfile(), same as
the CTk version.
"""
import os
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app import config
from app.core import thumbnails
from app.logging_setup import get_logger
from app.utils import download_history
from app.utils.formatting import humanize_size

from .widgets import make_card

logger = get_logger("history_tab")

_KIND_ICON = {"video": "🎬", "audio": "🎵", "torrent": "🧲", "image": "🖼"}
_THUMB_SIZE = 64


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class HistoryTab(QWidget):
    _thumb_ready_sig = Signal(object, object)  # (thumb_label, pil_image)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._thumb_ready_sig.connect(self._on_thumbnail_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.addStretch(1)
        clear_btn = QPushButton("Clear History")
        clear_btn.setObjectName("danger")
        clear_btn.clicked.connect(self._clear_all)
        header_row.addWidget(clear_btn)
        root.addLayout(header_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 4, 4, 4)
        self.body_layout.setSpacing(8)
        scroll.setWidget(self.body)
        root.addWidget(scroll)

        self.refresh()

    def refresh(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        entries = download_history.load()
        if not entries:
            empty = QLabel("Nothing downloaded yet -- completed videos, images, and torrents show up here.")
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignCenter)
            self.body_layout.addWidget(empty)
            self.body_layout.addStretch(1)
            return

        for i, entry in enumerate(entries):
            self.body_layout.addWidget(self._build_row(i, entry))
        self.body_layout.addStretch(1)

    def _build_row(self, index, entry):
        card, layout = make_card()
        row = QHBoxLayout()
        layout.addLayout(row)

        kind = entry.get("kind")
        icon = _KIND_ICON.get(kind, "📄")
        thumb_label = QLabel(icon)
        thumb_label.setFixedSize(_THUMB_SIZE, _THUMB_SIZE)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setObjectName("muted")
        thumb_label.setStyleSheet("font-size: 26px;")
        row.addWidget(thumb_label)
        threading.Thread(
            target=self._load_thumbnail, args=(entry.get("file_path"), kind, thumb_label), daemon=True
        ).start()

        text_col = QVBoxLayout()
        title_label = QLabel(entry.get("title") or "Untitled")
        title_label.setStyleSheet("font-weight: 600;")
        title_label.setWordWrap(True)
        text_col.addWidget(title_label)

        meta_bits = []
        size_bytes = entry.get("size_bytes")
        if size_bytes:
            meta_bits.append(humanize_size(size_bytes))
        ts = entry.get("completed_at")
        if ts:
            meta_bits.append(time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)))
        meta_label = QLabel("  •  ".join(meta_bits))
        meta_label.setObjectName("muted")
        text_col.addWidget(meta_label)

        # One compact row: Play / Delete File / Remove History plain and
        # small, then Open Folder pushed to the far right and kept green,
        # as the one action worth visually calling out. Sized by the
        # #historyPlain/#historyGreen QSS rules' own tight padding, not by
        # forcing a fixed pixel height -- an earlier version paired
        # setFixedHeight(24) with the ordinary QPushButton padding (7px top
        # + 7px bottom, meant for a ~27px-tall button) and the text got
        # cropped at the bottom because 24px never had room for both
        # (reported directly, from a real screenshot).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        play_btn = QPushButton("▶ Play")
        play_btn.setObjectName("historyPlain")
        play_btn.clicked.connect(lambda _c=False, e=entry: self._play(e))
        btn_row.addWidget(play_btn)

        # The one button here that touches the actual file -- confirms
        # first, same convention as the Torrent tab's "Delete Files". Kept
        # plain like Play/Remove rather than red: the confirmation dialog
        # itself is what guards against an accidental click, not the color.
        delete_btn = QPushButton("Delete File")
        delete_btn.setObjectName("historyPlain")
        delete_btn.clicked.connect(lambda _c=False, i=index, e=entry: self._delete_file(i, e))
        btn_row.addWidget(delete_btn)

        # Never touches the actual file on disk -- only removes this JSON
        # record (see download_history.remove_entry's docstring).
        remove_btn = QPushButton("Remove History")
        remove_btn.setObjectName("historyPlain")
        remove_btn.clicked.connect(lambda _c=False, i=index: self._remove(i))
        btn_row.addWidget(remove_btn)

        btn_row.addStretch(1)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setObjectName("historyGreen")
        open_folder_btn.clicked.connect(lambda _c=False, e=entry: self._open_folder(e))
        btn_row.addWidget(open_folder_btn)

        text_col.addLayout(btn_row)

        row.addLayout(text_col, 1)
        return card

    def _load_thumbnail(self, file_path, kind, thumb_label):
        pil_image = thumbnails.get_preview_image(file_path, kind, size=(_THUMB_SIZE, _THUMB_SIZE))
        if pil_image is None:
            return
        self._thumb_ready_sig.emit(thumb_label, pil_image)

    def _on_thumbnail_ready(self, thumb_label, pil_image):
        try:
            pix = _pil_to_pixmap(pil_image)
        except RuntimeError:
            return  # the underlying Qt label was already deleted (row removed/refreshed away)
        if pix is None:
            return
        thumb_label.setPixmap(pix.scaled(_THUMB_SIZE, _THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        thumb_label.setText("")

    def _open_folder(self, entry):
        folder = entry.get("folder_path")
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, config.APP_NAME, "That folder isn't there anymore.")
            return
        try:
            os.startfile(folder)
        except Exception:
            logger.exception("Failed to open folder %s", folder)
            QMessageBox.information(self, config.APP_NAME, f"Files were saved at:\n{folder}")

    def _play(self, entry):
        path = entry.get("file_path")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, config.APP_NAME, "That file isn't there anymore.")
            return
        try:
            os.startfile(path)
        except Exception:
            logger.exception("Failed to play %s", path)
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't open:\n{path}")

    def _remove(self, index):
        download_history.remove_entry(index)
        self.refresh()

    def _delete_file(self, index, entry):
        path = entry.get("file_path")
        title = entry.get("title") or "this file"
        if not path or not os.path.exists(path):
            # Nothing left to delete -- just drop the now-stale entry so
            # the list doesn't keep pointing at a file that's already gone.
            download_history.remove_entry(index)
            self.refresh()
            return
        confirmed = QMessageBox.question(
            self, config.APP_NAME,
            f"Permanently delete this file?\n\n{title}\n{path}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return
        try:
            os.remove(path)
        except Exception:
            logger.exception("Failed to delete %s", path)
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't delete:\n{path}")
            return
        download_history.remove_entry(index)
        self.refresh()

    def _clear_all(self):
        if not download_history.load():
            return
        confirmed = QMessageBox.question(
            self, config.APP_NAME,
            "Clear all download history?\n\nThis only clears this list -- none of your actual "
            "downloaded files are touched or deleted.",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes
        if confirmed:
            download_history.clear_all()
            self.refresh()
