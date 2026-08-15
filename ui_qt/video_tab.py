"""Video tab: paste a link, pick a resolution or format, download. Same core-layer
calls (app/core/downloader.py, ffmpeg_utils.py, size_estimate.py), ported
from CustomTkinter widgets/threading-via-.after() to Qt widgets/threading-
via-signals. Every non-UI method (range math, choice-list rebuilding,
progress-hook shape) is copied as-is from the CTk version; only the widget
glue changed.
"""
import os
import re
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app import config
from app.core import downloader, ffmpeg_utils, size_estimate
from app.logging_setup import get_logger
from app.utils import download_history, formatting, settings as settings_store

from . import theme
from .widgets import Chip, DownloadCard, make_card

logger = get_logger("video_tab")


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class VideoTab(QWidget):
    # Background threads emit these; Qt auto-queues delivery to this
    # object's slots on the main/GUI thread (the thread it was created on),
    # the same role Tk's `self.root.after(0, ...)` plays.
    # height_sizes is a dict with int keys -- Qt's Signal(dict) marshals
    # through QVariantMap, which requires string keys and silently fails
    # ("Cannot copy-convert ... to C++") for this shape; `object` passes
    # the Python dict through untouched instead.
    _fetch_done_sig = Signal(str, str, int, object, object, object, bool)
    _fetch_error_sig = Signal(str)
    # Every download signal carries its job id as the first argument: several
    # downloads run at once now, so a bare "progress = 40%" is meaningless
    # without saying *which* card it belongs to.
    _progress_sig = Signal(int, float, str)
    _download_done_sig = Signal(int, object, str, str)
    _download_error_sig = Signal(int, str)
    _thumb_save_done_sig = Signal(str)
    _thumb_save_error_sig = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.download_dir = settings_store.get_save_dir(
            settings, "video", config.DEFAULT_DOWNLOAD_DIR)
        os.makedirs(self.download_dir, exist_ok=True)

        self.res_height_map = {}
        self.bitrate_value_map = {}
        self.duration = 0
        self.height_sizes = {}
        self.is_image_mode = False
        self.thumbnail_url = None
        self._thumb_pixmap = None

        # job_id -> {"card", "cancel", "title", "save_dir"}. Downloads are
        # keyed rather than kept in a list so a finished/cancelled job can be
        # dropped without disturbing the others still running.
        self._jobs = {}
        self._job_counter = 0

        self._fetch_done_sig.connect(self._on_fetch_done)
        self._fetch_error_sig.connect(self._on_fetch_error)
        self._progress_sig.connect(self._update_progress)
        self._download_done_sig.connect(self._on_download_done)
        self._download_error_sig.connect(self._on_download_error)
        self._thumb_save_done_sig.connect(self._on_thumb_save_done)
        self._thumb_save_error_sig.connect(self._on_thumb_save_error)

        self._build_ui()
        self._check_ffmpeg()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---- URL card ----
        url_card, url_layout = make_card()
        root.addWidget(url_card)
        url_layout.addWidget(self._label("Video / Post URL", "muted"))
        url_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste a link here...")
        self.url_entry.returnPressed.connect(self.on_fetch)
        url_row.addWidget(self.url_entry, 1)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.setFixedWidth(100)
        self.fetch_btn.clicked.connect(self.on_fetch)
        url_row.addWidget(self.fetch_btn)
        url_layout.addLayout(url_row)
        self.status_label = QLabel("Paste a link, then click Fetch.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        url_layout.addWidget(self.status_label)

        # ---- Info card (hidden until Fetch succeeds) ----
        self.info_card, info_layout = make_card()
        info_row = QHBoxLayout()
        info_layout.addLayout(info_row)
        thumb_col = QVBoxLayout()
        self.thumb_label = QLabel("No preview")
        self.thumb_label.setFixedSize(140, 79)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setObjectName("muted")
        thumb_col.addWidget(self.thumb_label)
        self.save_thumb_btn = QPushButton("Save Thumbnail")
        self.save_thumb_btn.setEnabled(False)
        self.save_thumb_btn.clicked.connect(self.on_save_thumbnail)
        thumb_col.addWidget(self.save_thumb_btn)
        info_row.addLayout(thumb_col)

        text_col = QVBoxLayout()
        self.info_title_label = QLabel("")
        self.info_title_label.setObjectName("heading")
        self.info_title_label.setWordWrap(True)
        text_col.addWidget(self.info_title_label)
        self.info_meta_label = QLabel("")
        self.info_meta_label.setObjectName("muted")
        text_col.addWidget(self.info_meta_label)
        text_col.addStretch(1)
        info_row.addLayout(text_col, 1)
        self.info_card.setVisible(False)
        root.addWidget(self.info_card)

        # ---- Download options card ----
        self.opts_card, opts_layout = make_card("DOWNLOAD OPTIONS")
        root.addWidget(self.opts_card)

        mode_row = QHBoxLayout()
        self.video_radio = QRadioButton("Video")
        self.audio_radio = QRadioButton("Audio only (MP3)")
        self.video_radio.setChecked(True)
        self.video_radio.toggled.connect(self.on_mode_change)
        mode_row.addWidget(self.video_radio)
        mode_row.addWidget(self.audio_radio)
        mode_row.addStretch(1)
        opts_layout.addLayout(mode_row)

        self.video_row = QWidget()
        video_row_layout = QHBoxLayout(self.video_row)
        video_row_layout.setContentsMargins(0, 0, 0, 0)
        video_row_layout.setSpacing(0)

        # The resolution/format controls are grouped into their own widget so
        # image mode can hide *them* while leaving the Download button (which
        # image posts still need) sitting in the same place on the same row.
        self.fmt_group = QWidget()
        fmt_layout = QHBoxLayout(self.fmt_group)
        fmt_layout.setContentsMargins(0, 0, 0, 0)
        # Each label sits directly against its own dropdown, with a slightly
        # wider gap separating the two pairs, so the row reads as two groups
        # rather than four evenly-spaced controls.
        fmt_layout.setSpacing(6)
        video_row_layout = fmt_layout
        video_row_layout.addWidget(self._label("Resolution", "muted"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("best available")
        self.res_combo.currentTextChanged.connect(lambda _t: self._update_selection_summary())
        # AdjustToContents alone: it already sizes to the widest item this
        # ever holds ("2160p  •  ~250.4 MB"). The explicit
        # setMinimumContentsLength that used to sit here was padding it out
        # to 28 characters on top of that, which is where the dead space
        # between the box's text and its arrow came from (reported directly,
        # with the gap marked on a screenshot).
        self.res_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        video_row_layout.addWidget(self.res_combo)
        video_row_layout.addSpacing(18)
        video_row_layout.addWidget(self._label("Format", "muted"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp4", "mkv", "mov"])
        self.format_combo.currentTextChanged.connect(lambda _t: self._refresh_choices())
        # Same reasoning -- "mp4"/"mkv"/"mov" needs three characters, and the
        # old 8-character minimum was pure empty space.
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        video_row_layout.addWidget(self.format_combo)
        # Packs both groups to the left instead of letting the combos absorb
        # the leftover row width.
        video_row_layout.addStretch(1)

        # Download sits at the far right of this same row (asked for
        # directly, marked on a screenshot) rather than in the Save-to card
        # below -- that card is now purely about *where* files land, and
        # starting a download is an options-row action.
        outer_row = self.video_row.layout()
        outer_row.addWidget(self.fmt_group, 1)
        self.download_btn = QPushButton("Download")
        self.download_btn.setObjectName("accent")
        self.download_btn.setFixedWidth(150)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.on_download)
        outer_row.addWidget(self.download_btn, 0, Qt.AlignRight)
        opts_layout.addWidget(self.video_row)

        self.bitrate_row = QWidget()
        bitrate_row_layout = QHBoxLayout(self.bitrate_row)
        bitrate_row_layout.setContentsMargins(0, 0, 0, 0)
        bitrate_row_layout.addWidget(self._label("MP3 bitrate", "muted"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["128 kbps", "192 kbps", "256 kbps", "320 kbps"])
        self.bitrate_combo.setCurrentText("192 kbps")
        self.bitrate_combo.currentTextChanged.connect(lambda _t: self._update_selection_summary())
        bitrate_row_layout.addWidget(self.bitrate_combo, 1)
        opts_layout.addWidget(self.bitrate_row)
        self.bitrate_row.setVisible(False)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        opts_layout.addWidget(divider)

        self.range_check = QCheckBox("Download only part of this video (clip range)")
        self.range_check.setEnabled(False)
        self.range_check.toggled.connect(self.on_range_toggle)
        opts_layout.addWidget(self.range_check)

        self.range_fields = QWidget()
        range_layout = QHBoxLayout(self.range_fields)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self._label("Start", "muted"))
        self.start_entry = QLineEdit("0:00")
        self.start_entry.setFixedWidth(80)
        self.start_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.start_entry)
        range_layout.addWidget(self._label("End", "muted"))
        self.end_entry = QLineEdit("0:00")
        self.end_entry.setFixedWidth(80)
        self.end_entry.textChanged.connect(self.on_range_change)
        range_layout.addWidget(self.end_entry)
        self.range_info_label = QLabel("Format: HH:MM:SS or MM:SS")
        self.range_info_label.setObjectName("muted")
        range_layout.addWidget(self.range_info_label, 1)
        opts_layout.addWidget(self.range_fields)
        self.range_fields.setVisible(False)

        # A centered accent-tinted badge instead of a big left-aligned
        # heading floating in open space -- the previous version sat flush
        # against the left edge with a wide empty gap on the right and
        # nothing tying it visually to either card above or below it
        # (marked directly on a screenshot). A pill styled like the theme
        # toggle button reads as a proper summary chip, centered and
        # snug between the two cards it sits between.
        self.summary_chip = Chip()
        self._restyle_summary_chip()
        self.summary_label = self.summary_chip.label
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addStretch(1)
        summary_row.addWidget(self.summary_chip)
        summary_row.addStretch(1)
        root.addLayout(summary_row)
        self.summary_chip.setVisible(False)

        # ---- Save + progress + download card ----
        out_card, out_layout = make_card()
        root.addWidget(out_card)
        out_layout.addWidget(self._label("Save to", "muted"))
        dir_row = QHBoxLayout()
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self.open_download_folder)
        dir_row.addWidget(open_folder_btn)
        out_layout.addLayout(dir_row)

        self.ffmpeg_warn_label = QLabel("")
        self.ffmpeg_warn_label.setObjectName("dangerText")
        self.ffmpeg_warn_label.setWordWrap(True)
        self.ffmpeg_warn_label.setStyleSheet("color: #ff6961;")
        root.addWidget(self.ffmpeg_warn_label)

        # ---- Active downloads (one card each, newest first) ----
        # Scrollable so a long batch can't push the cards above off-screen,
        # which is exactly what happened to the Images tab's grid before it
        # got the same treatment.
        self.downloads_scroll = QScrollArea()
        self.downloads_scroll.setWidgetResizable(True)
        self.downloads_scroll.setFrameShape(QFrame.NoFrame)
        self.downloads_scroll.setStyleSheet("background: transparent;")
        self.downloads_body = QWidget()
        self.downloads_body.setStyleSheet("background: transparent;")
        self.downloads_layout = QVBoxLayout(self.downloads_body)
        self.downloads_layout.setContentsMargins(0, 0, 0, 0)
        self.downloads_layout.setSpacing(8)
        self.downloads_layout.addStretch(1)
        self.downloads_scroll.setWidget(self.downloads_body)
        root.addWidget(self.downloads_scroll, 1)

    def _dark_mode(self):
        """Current theme, for widgets that paint themselves and so can't get
        their colours from the stylesheet."""
        return (self.settings or {}).get("theme", "dark") != "light"

    def _restyle_summary_chip(self):
        # Solid (alpha=255) colors, not a translucent tint over the Acrylic
        # backdrop. A translucent chip's *effective* colour depends on
        # whatever's composited behind it, which is exactly what made the
        # first version of this badge unreadable: the background tint and
        # the accent-blue text measured only 3.45:1 apart against a light
        # backdrop (WCAG AA needs 4.5) -- "same colour of button and text."
        # A fixed, fully-opaque pair has a contrast ratio that's a plain
        # fact rather than a guess about what's behind the window; both
        # pairs below clear 6.5:1.
        if self._dark_mode():
            self.summary_chip.set_colors(QColor(28, 58, 92), QColor(45, 90, 140), "#96CDFF")
        else:
            self.summary_chip.set_colors(QColor(214, 234, 255), QColor(160, 205, 255), "#0A3278")

    def apply_theme(self):
        """Called by MainWindow after a live theme toggle -- re-applies
        colours for widgets that paint themselves and so don't pick up the
        new theme from the stylesheet the way ordinary QSS-styled widgets
        do."""
        self._restyle_summary_chip()
        for job in self._jobs.values():
            job["card"].apply_theme(self._dark_mode())

    @staticmethod
    def _label(text, object_name=None):
        lbl = QLabel(text)
        if object_name:
            lbl.setObjectName(object_name)
        return lbl

    def _check_ffmpeg(self):
        if not ffmpeg_utils.ffmpeg_available():
            self.ffmpeg_warn_label.setText(
                "⚠ ffmpeg not found on PATH. Merging/format conversion will fail until it's installed."
            )

    def on_mode_change(self):
        if self.audio_radio.isChecked():
            self.fmt_group.setVisible(False)
            self.bitrate_row.setVisible(True)
        else:
            self.bitrate_row.setVisible(False)
            self.fmt_group.setVisible(True)
        self._update_selection_summary()

    def browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", self.dir_entry.text())
        if d:
            self.dir_entry.setText(d)
            # Remembered across restarts -- previously this reset to the
            # default folder on every launch.
            settings_store.set_save_dir(self.settings, "video", d)

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

        self.status_label.setText("Fetching info...")
        self.fetch_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _fetch_thread(self, url):
        try:
            info, height_sizes = downloader.fetch_info_with_sizes(url)
            title = info.get("title", "Unknown title")
            uploader = info.get("uploader") or info.get("channel") or ""
            duration = info.get("duration") or 0
            has_video = any(
                f.get("vcodec") not in (None, "none") for f in (info.get("formats") or [])
            )
            is_image = not has_video
            thumbnail_url = downloader.best_thumbnail_url(info)
            thumb_image = downloader.fetch_thumbnail_image(thumbnail_url)
            self._fetch_done_sig.emit(title, uploader, duration, height_sizes,
                                       thumb_image, thumbnail_url, is_image)
        except Exception as e:
            logger.exception("Fetch failed for %s", url)
            self._fetch_error_sig.emit(str(e))

    def _on_fetch_done(self, title, uploader, duration, height_sizes, thumb_image, thumbnail_url, is_image):
        self.is_image_mode = is_image
        if is_image and not thumbnail_url:
            self.status_label.setText(f"Loaded: {title} -- but no video or downloadable image was found for this post.")
        elif is_image:
            self.status_label.setText(f"Loaded image post: {title}")
        else:
            self.status_label.setText(f"Loaded: {title}")
        self.duration = duration
        self.height_sizes = height_sizes
        self.thumbnail_url = thumbnail_url

        self.info_title_label.setText(title)
        meta_bits = []
        if uploader:
            meta_bits.append(uploader)
        if duration:
            meta_bits.append(f"Duration: {formatting.format_eta(duration)}")
        self.info_meta_label.setText("  •  ".join(meta_bits))

        pix = _pil_to_pixmap(thumb_image)
        # Held at full size so each download card can crop its own 16:9 tile
        # from it -- the label below scales a copy down to the preview box.
        self._thumb_pixmap = pix
        if pix is not None:
            self.thumb_label.setPixmap(pix.scaled(140, 79, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.thumb_label.setText("")
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText("No preview")
        self.save_thumb_btn.setText("Save Image" if is_image else "Save Thumbnail")
        self.save_thumb_btn.setEnabled(bool(thumbnail_url))

        self.info_card.setVisible(True)

        self.range_check.setEnabled(bool(duration))
        self.start_entry.setText("0:00")
        self.end_entry.setText(formatting.format_eta(duration) if duration else "0:00")
        self.range_check.setChecked(False)
        self.on_range_toggle()

        self._apply_mode_visibility()

        self.fetch_btn.setEnabled(True)
        self.download_btn.setEnabled(True)

    def _apply_mode_visibility(self):
        if self.is_image_mode:
            self.opts_card.setVisible(False)
            self.summary_label.setText("")
            self.summary_chip.setVisible(False)
            self.download_btn.setText("Save Image")
            try:
                self.download_btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.download_btn.clicked.connect(self._on_image_download)
        else:
            self.opts_card.setVisible(True)
            self.download_btn.setText("Download")
            try:
                self.download_btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.download_btn.clicked.connect(self.on_download)
            self._refresh_choices()

    def _on_fetch_error(self, err):
        self.status_label.setText("Could not load info for that link.")
        self.fetch_btn.setEnabled(True)
        QMessageBox.critical(self, config.APP_NAME, f"Failed to fetch info:\n{err}")

    def on_save_thumbnail(self):
        if not self.thumbnail_url:
            return
        default_name = re.sub(r"[^\w\-. ]", "_", self.info_title_label.text() or "thumbnail")[:80] + ".jpg"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Thumbnail",
            os.path.join(self.dir_entry.text().strip() or self.download_dir, default_name),
            "JPEG image (*.jpg);;All files (*.*)",
        )
        if not dest:
            return
        self.save_thumb_btn.setEnabled(False)
        threading.Thread(target=self._save_thumbnail_thread, args=(self.thumbnail_url, dest), daemon=True).start()

    def _save_thumbnail_thread(self, url, dest):
        try:
            # save_thumbnail appends the source image's *real* extension
            # (it saves the original bytes losslessly rather than re-encoding
            # everything to JPEG), so hand it a path without one and use the
            # path it actually wrote -- a WebP source stays .webp instead of
            # being mislabeled .jpg.
            dest = downloader.save_thumbnail(url, os.path.splitext(dest)[0])
            size_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
            download_history.add_entry(
                "image", self.info_title_label.text() or os.path.basename(dest),
                dest, os.path.dirname(dest), size_bytes,
            )
            self._thumb_save_done_sig.emit(dest)
        except Exception as e:
            logger.exception("Failed to save thumbnail to %s", dest)
            self._thumb_save_error_sig.emit(str(e))

    def _on_thumb_save_done(self, dest):
        # An inline, self-clearing confirmation on the button itself instead
        # of a blocking popup dialog on every single save -- reported
        # directly as disruptive interrupting the flow each time.
        self.save_thumb_btn.setEnabled(True)
        original_text = self.save_thumb_btn.text()
        self.save_thumb_btn.setText("✓ Saved")
        QTimer.singleShot(2000, lambda: self.save_thumb_btn.setText(original_text))

    def _on_thumb_save_error(self, err):
        self.save_thumb_btn.setEnabled(True)
        QMessageBox.critical(self, config.APP_NAME, f"Couldn't save image:\n{err}")

    # ------------------------------------------------------ Image download ---
    def _on_image_download(self):
        if not self.thumbnail_url:
            return
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)
        title = self.info_title_label.text() or "image"
        default_name = re.sub(r"[^\w\-. ]", "_", title)[:80] + ".jpg"
        dest = os.path.join(save_dir, default_name)

        # Image posts get a card too, so a mixed batch (a few videos plus an
        # image post) all reports in the same place.
        url = self.thumbnail_url
        job_id = self._start_job(title, self.info_meta_label.text(), save_dir)
        self._clear_form_for_next()
        threading.Thread(target=self._image_download_thread, args=(job_id, url, dest, save_dir),
                          daemon=True).start()

    def _image_download_thread(self, job_id, url, dest, save_dir):
        try:
            self._progress_sig.emit(job_id, 10, "Saving image...")
            dest = downloader.save_thumbnail(url, os.path.splitext(dest)[0])
            size_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
            job = self._jobs.get(job_id)
            download_history.add_entry(
                "image", (job or {}).get("title") or os.path.basename(dest),
                dest, save_dir, size_bytes,
            )
            self._download_done_sig.emit(job_id, "", "image", dest)
        except Exception as e:
            logger.exception("Failed to save image to %s", dest)
            self._download_error_sig.emit(job_id, str(e))

    # ------------------------------------------------------- Clip range ---
    def on_range_toggle(self):
        self.range_fields.setVisible(self.range_check.isChecked())
        self.on_range_change()

    def on_range_change(self):
        _, eff_duration, valid, err = self._current_fraction_and_duration()
        if not self.range_check.isChecked():
            self.range_info_label.setText("Format: HH:MM:SS or MM:SS")
        elif not valid:
            self.range_info_label.setText(err or "Format: HH:MM:SS or MM:SS")
        else:
            self.range_info_label.setText(f"Clip length: {formatting.format_eta(eff_duration)}")
        self._refresh_choices()

    def _current_fraction_and_duration(self):
        if not self.range_check.isChecked() or not self.duration:
            return 1.0, self.duration, True, None
        try:
            start = formatting.parse_timecode(self.start_entry.text()) or 0
            end = formatting.parse_timecode(self.end_entry.text())
        except ValueError as e:
            return 1.0, self.duration, False, str(e)
        if end is None:
            return 1.0, self.duration, False, "Enter an end time."
        if end <= start:
            return 1.0, self.duration, False, "End must be after start."
        if end > self.duration:
            return 1.0, self.duration, False, f"Video is only {formatting.format_eta(self.duration)} long."
        clip_len = end - start
        fraction = clip_len / self.duration if self.duration else 1.0
        return fraction, clip_len, True, None

    def _get_selected_range_seconds(self):
        if not self.range_check.isChecked():
            return None
        _, _, valid, _ = self._current_fraction_and_duration()
        if not valid:
            return None
        start = formatting.parse_timecode(self.start_entry.text()) or 0
        end = formatting.parse_timecode(self.end_entry.text())
        return (start, end)

    # ------------------------------------------------- Dynamic choice lists ---
    def _refresh_choices(self):
        fraction, eff_duration, _, _ = self._current_fraction_and_duration()
        self._rebuild_resolution_choices(fraction, eff_duration)
        self._rebuild_bitrate_choices(eff_duration)
        self._update_selection_summary()

    def _rebuild_resolution_choices(self, fraction=1.0, effective_duration=None):
        prev_height = self.res_height_map.get(self.res_combo.currentText())
        heights = sorted(self.height_sizes.keys(), reverse=True) if self.height_sizes else []
        container = self.format_combo.currentText()
        eff_duration = self.duration if effective_duration is None else effective_duration
        res_list, res_map = [], {}
        for h in heights:
            total_size = size_estimate.estimate_video_size(
                self.height_sizes, h, eff_duration, fraction, container
            )
            label = f"{h}p   •   ~{formatting.humanize_size(total_size)}"
            if container == "mov":
                label += " (est.)"
            res_list.append(label)
            res_map[label] = h
        if not res_list:
            res_list, res_map = ["best available"], {"best available": None}

        self.res_height_map = res_map
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        self.res_combo.addItems(res_list)
        match = next((lbl for lbl, h in res_map.items() if h == prev_height), None)
        self.res_combo.setCurrentText(match or res_list[0])
        self.res_combo.blockSignals(False)

    def _rebuild_bitrate_choices(self, effective_duration):
        prev_kbps = self.bitrate_value_map.get(self.bitrate_combo.currentText())
        bitrate_list, bitrate_map = [], {}
        for kbps in (128, 192, 256, 320):
            label = f"{kbps} kbps"
            if effective_duration:
                size_bytes = size_estimate.estimate_audio_size(effective_duration, kbps)
                label += f"   •   ~{formatting.humanize_size(size_bytes)}"
            bitrate_list.append(label)
            bitrate_map[label] = kbps

        self.bitrate_value_map = bitrate_map
        self.bitrate_combo.blockSignals(True)
        self.bitrate_combo.clear()
        self.bitrate_combo.addItems(bitrate_list)
        match = next((lbl for lbl, k in bitrate_map.items() if k == prev_kbps), None)
        self.bitrate_combo.setCurrentText(match or bitrate_list[min(1, len(bitrate_list) - 1)])
        self.bitrate_combo.blockSignals(False)

    def _update_selection_summary(self):
        fraction, eff_duration, valid, _ = self._current_fraction_and_duration()
        range_on = self.range_check.isChecked() and valid

        # Parenthesized size instead of another "• ~X MB" segment, and no
        # "You'll get:" preamble -- the badge's own shape and position
        # (centered, right above the Save-to card) already say "this is
        # what you're about to download," so the label can just state it.
        if self.audio_radio.isChecked():
            kbps = self.bitrate_value_map.get(self.bitrate_combo.currentText(), 192)
            size = size_estimate.estimate_audio_size(eff_duration, kbps)
            text = f"MP3 · {kbps} kbps  ({formatting.humanize_size(size)})"
        else:
            height = self.res_height_map.get(self.res_combo.currentText())
            container = self.format_combo.currentText()
            total = size_estimate.estimate_video_size(self.height_sizes, height, eff_duration, fraction, container)
            res_text = f"{height}p" if height else "Best available"
            text = f"{res_text} {container.upper()}  ({formatting.humanize_size(total)})"
            if container == "mov":
                text += "  · re-encoded"

        if range_on:
            text += f"  ·  Clip {formatting.format_eta(eff_duration)}"
        self.summary_label.setText(text)
        self.summary_chip.setVisible(bool(text))

    # ---------------------------------------------------------- Download ---
    def on_download(self):
        url = self.url_entry.text().strip()
        if not url:
            return
        save_dir = self.dir_entry.text().strip() or self.download_dir
        os.makedirs(save_dir, exist_ok=True)

        mode = "audio" if self.audio_radio.isChecked() else "video"
        container = self.format_combo.currentText()

        res_display = self.res_combo.currentText()
        height = self.res_height_map.get(res_display)
        if height is None and res_display not in self.res_height_map:
            m = re.match(r"(\d+)", res_display)
            height = int(m.group(1)) if m else None

        bitrate_display = self.bitrate_combo.currentText()
        bitrate = self.bitrate_value_map.get(bitrate_display)
        if bitrate is None:
            m = re.match(r"(\d+)", bitrate_display)
            bitrate = int(m.group(1)) if m else 192

        time_range = None
        if self.range_check.isChecked():
            _, _, valid, err = self._current_fraction_and_duration()
            if not valid:
                QMessageBox.warning(self, config.APP_NAME, f"Fix the clip range first: {err}")
                return
            time_range = self._get_selected_range_seconds()

        title = self.info_title_label.text() or url
        meta = self.info_meta_label.text()
        job_id = self._start_job(title, meta, save_dir)

        # The form resets rather than locking: the whole point of batching is
        # that the next link can be pasted while this one runs.
        self._clear_form_for_next()

        threading.Thread(
            target=self._download_thread,
            args=(job_id, url, save_dir, mode, height, container, str(bitrate), time_range),
            daemon=True,
        ).start()

    def _start_job(self, title, meta, save_dir):
        """Creates the card (newest on top) and registers the job."""
        self._job_counter += 1
        job_id = self._job_counter
        card = DownloadCard(
            title, meta, self._thumb_pixmap, self._dark_mode(),
            on_cancel=lambda jid=job_id: self._cancel_job(jid),
        )
        # insertWidget(0, ...) rather than addWidget: new downloads belong at
        # the top so the one just started is the one you're looking at. The
        # trailing stretch stays last, which is what keeps a short list packed
        # upward instead of spread down the tab.
        self.downloads_layout.insertWidget(0, card)
        self._jobs[job_id] = {
            "card": card, "cancel": False, "title": title, "save_dir": save_dir,
        }
        return job_id

    def _clear_form_for_next(self):
        """Puts the tab back into 'ready for the next link' state -- the URL
        box is cleared and focused, and the fetched-info panels collapse."""
        self.url_entry.clear()
        self.url_entry.setFocus()
        self.info_card.setVisible(False)
        self.summary_chip.setVisible(False)
        self.download_btn.setEnabled(False)
        self.fetch_btn.setEnabled(True)
        self.status_label.setText("Paste the next link, then click Fetch.")
        self.thumbnail_url = None
        self._thumb_pixmap = None
        self.height_sizes = {}
        self.duration = 0
        self.range_check.setChecked(False)
        self.range_check.setEnabled(False)
        self.is_image_mode = False
        self._apply_mode_visibility()

    def _cancel_job(self, job_id):
        job = self._jobs.get(job_id)
        if job:
            job["cancel"] = True

    def _finish_job(self, job_id, delay_ms=4000):
        """Drops the card a few seconds after it finishes -- long enough to
        register that it completed, then out of the way. The file itself is
        already in the History tab by this point."""
        job = self._jobs.pop(job_id, None)
        if not job:
            return
        card = job["card"]
        QTimer.singleShot(delay_ms, lambda: self._remove_card(card))

    def _remove_card(self, card):
        try:
            self.downloads_layout.removeWidget(card)
            card.deleteLater()
        except RuntimeError:
            pass  # already torn down (tab closed / theme rebuild)

    def _progress_hook(self, job_id, d):
        job = self._jobs.get(job_id)
        if job is None or job["cancel"]:
            raise downloader.DownloadCancelled("Cancelled by user")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else 0

            parts = []
            if total:
                parts.append(f"{formatting.humanize_size(downloaded)} / {formatting.humanize_size(total)}")
            if speed:
                parts.append(f"{formatting.humanize_size(speed)}/s")
            eta_str = formatting.format_eta(eta) if eta is not None else None
            if eta_str:
                parts.append(f"ETA {eta_str}")
            detail = "  •  ".join(parts) if parts else "Downloading..."
            self._progress_sig.emit(job_id, pct, detail)
        elif d.get("status") == "finished":
            self._progress_sig.emit(job_id, 100, "Merging...")

    def _update_progress(self, job_id, pct, label):
        job = self._jobs.get(job_id)
        if job:
            job["card"].set_progress(pct, label)

    def _download_thread(self, job_id, url, save_dir, mode, height, container,
                          bitrate="192", time_range=None):
        hook = lambda d: self._progress_hook(job_id, d)
        try:
            if mode == "audio":
                _, final_path = downloader.download_audio(url, save_dir, bitrate, hook, time_range)
                self._download_done_sig.emit(job_id, "", "audio", final_path)
                return

            info, merged_path = downloader.download_video(
                url, save_dir, height, hook, time_range
            )

            warning = ""
            final_path = merged_path
            if merged_path and container != "mkv":
                self._progress_sig.emit(job_id, 100, f"Converting to {container.upper()}...")
                final_path, warning = ffmpeg_utils.convert_container(merged_path, container)

            self._download_done_sig.emit(job_id, warning or "", "video", final_path)
        except Exception as e:
            logger.exception("Download failed for %s", url)
            self._download_error_sig.emit(job_id, str(e))

    def _on_download_done(self, job_id, warning, kind, final_path):
        job = self._jobs.get(job_id)
        if job is None:
            return
        card = job["card"]
        save_dir = job["save_dir"]
        card.set_complete("✓ Completed" if not warning else "✓ Saved (kept as .mkv)")

        if final_path and os.path.exists(final_path):
            try:
                size_bytes = os.path.getsize(final_path)
            except OSError:
                size_bytes = 0
            if kind != "image":  # image path already recorded its own history entry
                download_history.add_entry(
                    kind, job["title"] or os.path.basename(final_path),
                    final_path, save_dir, size_bytes,
                )

        self._finish_job(job_id)

        if warning:
            # A real, non-routine problem (chosen format failed to convert,
            # kept as .mkv instead) -- worth an actual popup, unlike the two
            # plain-success cases below, which used to *also* pop a blocking
            # dialog on every single completion (reported directly as
            # disruptive) and now just update the inline status text/button
            # instead.
            QMessageBox.warning(
                self, config.APP_NAME,
                f"Downloaded successfully, but converting to your chosen format failed, "
                f"so the file was kept as .mkv instead.\n\nSaved to:\n{save_dir}\n\n"
                f"Details: {warning}",
            )

    def _on_download_error(self, job_id, err):
        job = self._jobs.get(job_id)
        if job is None:
            return
        card = job["card"]
        cancelled = job["cancel"]

        if cancelled:
            card.set_failed("Cancelled.")
            self._finish_job(job_id, delay_ms=1500)
            return

        card.set_failed(f"Failed: {err.splitlines()[0][:120]}" if err else "Failed.")
        # Left on screen rather than auto-dismissed: a failure is the one
        # outcome worth reading, and with several downloads running a popup
        # per failure would be its own problem. The card's button becomes
        # "Dismiss" so it can be cleared when it's been seen.
        job["card"].cancel_btn.clicked.disconnect()
        job["card"].cancel_btn.clicked.connect(
            lambda _c=False, jid=job_id: self._dismiss_failed(jid))
        logger.error("Download job %s failed: %s", job_id, err)

    def _dismiss_failed(self, job_id):
        job = self._jobs.pop(job_id, None)
        if job:
            self._remove_card(job["card"])
