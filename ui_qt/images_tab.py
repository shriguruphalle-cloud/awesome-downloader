"""Images tab: paste a post link, see every image
in it as a grid (Instagram/Facebook carousels, not just single-image posts),
pick which ones, download at highest available resolution.
"""
import os
import re
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app import config
from app.core import downloader
from app.logging_setup import get_logger
from app.utils import download_history, settings as settings_store

from .dialogs import signin_dialog
from .widgets import _PaintedCard, make_card

logger = get_logger("images_tab")

_GRID_COLUMNS = 4
_TILE_SIZE = 150


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class ImagesTab(QWidget):
    _fetch_done_sig = Signal(str, list, int)
    _fetch_error_sig = Signal(str)
    _tile_preview_sig = Signal(int, object)
    _download_progress_sig = Signal(str)
    _download_done_sig = Signal(str, int, int)
    # Raised when the user picks "sign in inside this app" -- main_qt wires
    # it to switching over to the Browser tab.
    open_browser_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.download_dir = settings_store.get_save_dir(
            settings, "images", config.DEFAULT_DOWNLOAD_DIR)
        os.makedirs(self.download_dir, exist_ok=True)

        self.items = []
        self.tile_labels = []
        self.checkboxes = []
        self.post_title = ""

        self._fetch_done_sig.connect(self._on_fetch_done)
        self._fetch_error_sig.connect(self._on_fetch_error)
        self._tile_preview_sig.connect(self._on_tile_preview_ready)
        self._download_progress_sig.connect(lambda t: self.progress_label.setText(t))
        self._download_done_sig.connect(self._on_download_done)

        self._build_ui()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        url_card, url_layout = make_card()
        root.addWidget(url_card)
        url_layout.addWidget(self._label("Post URL (Instagram, Facebook, Reddit, ...)", "muted"))
        url_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste a post link here...")
        self.url_entry.returnPressed.connect(self.on_fetch)
        url_row.addWidget(self.url_entry, 1)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.setFixedWidth(100)
        self.fetch_btn.clicked.connect(self.on_fetch)
        url_row.addWidget(self.fetch_btn)
        url_layout.addLayout(url_row)
        self.status_label = QLabel("Paste a post link, then click Fetch.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        url_layout.addWidget(self.status_label)

        gallery_card, gallery_layout = make_card()
        root.addWidget(gallery_card, 1)

        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setEnabled(False)
        self.select_all_btn.clicked.connect(self._select_all)
        select_row.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setEnabled(False)
        self.select_none_btn.clicked.connect(self._select_none)
        select_row.addWidget(self.select_none_btn)
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("muted")
        select_row.addWidget(self.selection_label)
        select_row.addStretch(1)
        gallery_layout.addLayout(select_row)

        # Wrapped in a bounded, scrollable area instead of adding
        # grid_widget to the layout directly -- with no scroll container, a
        # post with many images (Instagram carousels commonly have 10-15)
        # just grew the whole card, and the whole window along with it,
        # until the Save-to/Download controls below were pushed off-screen
        # with no way to reach them (reported directly, with a screenshot:
        # the window resized itself "vertical" and couldn't be scrolled).
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.NoFrame)
        # Scoped by id. An unscoped "background: transparent" is a
        # widget-level stylesheet, and a widget stylesheet outranks the
        # application one for every descendant -- so it also repainted the
        # accent/quiet buttons on the rows inside transparent, which is why
        # they rendered as bare text with no fill.
        grid_scroll.setObjectName("imagesScroll")
        grid_scroll.setStyleSheet("#imagesScroll { background: transparent; }")
        grid_scroll.setMaximumHeight(420)
        self.grid_widget = QWidget()
        self.grid_widget.setObjectName("imagesScrollBody")
        self.grid_widget.setStyleSheet("#imagesScrollBody { background: transparent; }")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8)
        grid_scroll.setWidget(self.grid_widget)
        gallery_layout.addWidget(grid_scroll)
        self.empty_label = QLabel("Fetch a post to see its images here.")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.grid_layout.addWidget(self.empty_label, 0, 0, 1, _GRID_COLUMNS)

        out_card, out_layout = make_card()
        root.addWidget(out_card)
        out_layout.addWidget(self._label("Save to", "muted"))
        dir_row = QHBoxLayout()
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        out_layout.addLayout(dir_row)

        action_row = QHBoxLayout()
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("accent")
        self.download_btn.setFixedWidth(200)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.on_download)
        action_row.addWidget(self.download_btn)
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self.open_download_folder)
        action_row.addWidget(open_folder_btn)
        action_row.addStretch(1)
        out_layout.addLayout(action_row)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("muted")
        root.addWidget(self.progress_label)

    @staticmethod
    def _label(text, object_name=None):
        lbl = QLabel(text)
        if object_name:
            lbl.setObjectName(object_name)
        return lbl

    def browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.dir_entry.text())
        if d:
            self.dir_entry.setText(d)
            settings_store.set_save_dir(self.settings, "images", d)

    def open_download_folder(self):
        path = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception:
            logger.exception("Failed to open folder %s", path)
            QMessageBox.information(self, config.APP_NAME, f"Your files are saved at:\n{path}")

    # ----------------------------------------------------------- Fetching ---
    def on_fetch(self):
        url = self.url_entry.text().strip()
        if not url:
            from PySide6.QtWidgets import QApplication
            clip_text = (QApplication.clipboard().text() or "").strip()
            if clip_text:
                url = clip_text
                self.url_entry.setText(url)
        if not url:
            QMessageBox.warning(self, config.APP_NAME, "Paste a link first, or copy one to your clipboard.")
            return

        self.status_label.setText("Fetching images...")
        self.fetch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _cookies_from_browser(self):
        return (self.settings or {}).get("cookies_from_browser")

    def _fetch_thread(self, url):
        try:
            post_title, all_items = downloader.fetch_image_gallery(
                url, cookies_from_browser=self._cookies_from_browser())
            image_items = [it for it in all_items if not it["is_video"]]
            skipped_videos = len(all_items) - len(image_items)
            self._fetch_done_sig.emit(post_title, image_items, skipped_videos)
        except Exception as e:
            logger.exception("Image gallery fetch failed for %s", url)
            self._fetch_error_sig.emit(str(e))

    def _on_fetch_done(self, post_title, image_items, skipped_videos):
        self.post_title = post_title
        self.items = image_items
        self.fetch_btn.setEnabled(True)

        if not image_items:
            self.status_label.setText(f"Loaded: {post_title} -- no images found in this post.")
            self._rebuild_grid_placeholder("No images found in this post.")
            self.download_btn.setEnabled(False)
            return

        note = f"  ({skipped_videos} video slide(s) skipped -- use Video Downloader for those)" if skipped_videos else ""
        self.status_label.setText(f"Loaded: {post_title} -- {len(image_items)} image(s) found.{note}")
        self._rebuild_grid()
        self.download_btn.setEnabled(True)

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        # yt-dlp's own "use --cookies-from-browser" text is a command-line
        # instruction, and showing it verbatim made the app's answer to "this
        # needs a login" a wall of terminal advice. A link that needs a
        # session gets the dialog that can actually set one up instead.
        if signin_dialog.needs_sign_in(err):
            self.status_label.setText("That link needs a signed-in account.")
            self._offer_sign_in()
            return
        self.status_label.setText("Could not load images for that link.")
        QMessageBox.critical(self, config.APP_NAME, f"Failed to fetch images:\n{err}")

    def _offer_sign_in(self):
        choice = signin_dialog.show_sign_in_help(
            self, url=self.url_entry.text().strip(),
            dark_mode=(self.settings or {}).get("theme", "dark") != "light",
            current=self._cookies_from_browser())
        if choice is None:
            return
        if choice == "browser_tab":
            self.open_browser_requested.emit()
            return
        self.settings["cookies_from_browser"] = choice
        settings_store.save_settings(self.settings)
        self.status_label.setText("Using your %s sign-in -- fetching again..." % choice)
        self.on_fetch()

    # ------------------------------------------------------------ Gallery ---
    def _clear_grid(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.tile_labels = []
        self.checkboxes = []

    def _rebuild_grid_placeholder(self, text):
        self._clear_grid()
        self.selection_label.setText("")
        self.select_all_btn.setEnabled(False)
        self.select_none_btn.setEnabled(False)
        placeholder = QLabel(text)
        placeholder.setObjectName("muted")
        placeholder.setAlignment(Qt.AlignCenter)
        self.grid_layout.addWidget(placeholder, 0, 0, 1, _GRID_COLUMNS)

    def _rebuild_grid(self):
        self._clear_grid()

        for idx, item in enumerate(self.items):
            # Painted directly -- same fix as torrent_tab.py's row cards;
            # this tile lives inside grid_scroll's QScrollArea viewport.
            tile = _PaintedCard()
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(8, 8, 8, 8)

            img_label = QLabel("Loading...")
            img_label.setFixedSize(_TILE_SIZE, _TILE_SIZE)
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setObjectName("muted")
            tile_layout.addWidget(img_label)
            self.tile_labels.append(img_label)

            checkbox = QCheckBox(f"Image {idx + 1}")
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._update_selection_label)
            tile_layout.addWidget(checkbox, alignment=Qt.AlignCenter)
            self.checkboxes.append(checkbox)

            # Explicit alignment on the grid cell itself, not just within the
            # tile -- QGridLayout stretches a cell's column to the widest
            # tile in it by default, and without this the tile then sits
            # flush against one edge of that wider cell instead of centered
            # in it (reported directly: thumbnails "not centered in card").
            self.grid_layout.addWidget(
                tile, idx // _GRID_COLUMNS, idx % _GRID_COLUMNS, alignment=Qt.AlignCenter
            )

            threading.Thread(target=self._load_tile_preview, args=(idx, item["url"]), daemon=True).start()

        self.select_all_btn.setEnabled(True)
        self.select_none_btn.setEnabled(True)
        self._update_selection_label()

    def _load_tile_preview(self, idx, url):
        pil_image = downloader.fetch_thumbnail_image(url, size=(_TILE_SIZE, _TILE_SIZE))
        self._tile_preview_sig.emit(idx, pil_image)

    def _on_tile_preview_ready(self, idx, pil_image):
        if idx >= len(self.tile_labels):
            return  # grid was rebuilt (new fetch) while this preview was loading
        label = self.tile_labels[idx]
        pix = _pil_to_pixmap(pil_image)
        if pix is None:
            label.setText("No preview")
            return
        label.setPixmap(pix.scaled(_TILE_SIZE, _TILE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label.setText("")

    def _select_all(self):
        for cb in self.checkboxes:
            cb.setChecked(True)

    def _select_none(self):
        for cb in self.checkboxes:
            cb.setChecked(False)

    def _update_selection_label(self):
        n = sum(1 for cb in self.checkboxes if cb.isChecked())
        self.selection_label.setText(f"{n} of {len(self.checkboxes)} selected")
        self.download_btn.setText(f"Download Selected ({n})" if n else "Download Selected")
        self.download_btn.setEnabled(bool(n))

    # ---------------------------------------------------------- Download ---
    def on_download(self):
        selected = [item for item, cb in zip(self.items, self.checkboxes) if cb.isChecked()]
        if not selected:
            return
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)

        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        threading.Thread(target=self._download_thread, args=(selected, save_dir), daemon=True).start()

    def _download_thread(self, selected, save_dir):
        base_name = re.sub(r"[^\w\-. ]", "_", self.post_title or "image")[:60]
        saved = 0
        for i, item in enumerate(selected, start=1):
            self._download_progress_sig.emit(f"Downloading {i} of {len(selected)}...")
            suffix = f"_{i}" if len(selected) > 1 else ""
            # No extension here on purpose -- save_thumbnail appends the real
            # one from the source image (it saves the original bytes rather
            # than re-encoding everything to JPEG, which was silently costing
            # a second generation of lossy compression on every save).
            dest_base = os.path.join(save_dir, f"{base_name}{suffix}")
            try:
                dest = downloader.save_thumbnail(item["url"], dest_base)
                size_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
                download_history.add_entry("image", item["title"] or base_name, dest, save_dir, size_bytes)
                saved += 1
            except Exception:
                logger.exception("Failed to save image %s", item["url"])
        self._download_done_sig.emit(save_dir, saved, len(selected))

    def _on_download_done(self, save_dir, saved, total):
        self.download_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        if saved:
            # Inline status + a self-clearing button confirmation instead of
            # a blocking popup on every completion -- same fix, same reason,
            # as video_tab.py's equivalent (reported directly as disruptive).
            self.progress_label.setText(
                f"✓ Saved {saved} of {total} image(s) to {save_dir}" if saved == total
                else f"Done -- {saved} of {total} saved to {save_dir}"
            )
            original_text = self.download_btn.text()
            self.download_btn.setText("✓ Done")
            QTimer.singleShot(2500, lambda: self.download_btn.setText(original_text))
        else:
            self.progress_label.setText("Failed.")
            QMessageBox.critical(self, config.APP_NAME, "Couldn't save any of the selected images.")
