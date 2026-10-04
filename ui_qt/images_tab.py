"""Images tab: paste a post link, see every image
in it as a grid (Instagram/Facebook carousels, not just single-image posts),
pick which ones, download at highest available resolution.
"""
import os
import re
import threading

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from app import config
from app.core import downloader, errors
from app.logging_setup import get_logger
from app.utils import download_history, settings as settings_store

from .dialogs import signin_dialog
from . import cinema, motion, theme
from .widgets import EmptyState, centered_column, make_card, section_label
from .widgets.button import Button

logger = get_logger("images_tab")

_GRID_COLUMNS = 4          # only for the empty state's span
_TILE_MIN = 168            # tiles are at least this wide; the grid fills the row
_TILE_GAP = 12
_TILE_RATIO = 1.25         # 4:5 portrait, the shape of most social posts
_PREVIEW_PX = 520          # fetched this big, so a wide tile stays sharp
MAX_CONTENT_W = 1760


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class _ImageTile(QAbstractButton):
    """One image: the picture filling the tile, and a check badge in its
    corner. Selected tiles carry a ring; ones left out dim back."""

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.pixmap = None
        self.failed = False
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Image %d -- click to include or leave out" % (index + 1))
        self._hover = motion.Fader(self)
        self._on = motion.Fader(self, motion.FAST, value=1.0)
        self.toggled.connect(lambda on: self._on.to(1 if on else 0))

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap
        self.failed = pixmap is None
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        on, h = self._on.value, self._hover.value
        p.save()
        p.setClipPath(clip)
        if self.pixmap is not None and not self.pixmap.isNull():
            pm = self.pixmap
            dpr = pm.devicePixelRatio() or 1.0
            pw, ph = pm.width() / dpr, pm.height() / dpr
            scale = max(r.width() / pw, r.height() / ph) * (1.0 + 0.025 * h)
            w, hh = pw * scale, ph * scale
            p.drawPixmap(QRectF(r.center().x() - w / 2, r.center().y() - hh / 2, w, hh), pm, QRectF(pm.rect()))
        else:
            p.fillRect(r, theme.qcolor(t["field_bg"]))
            p.setPen(theme.qcolor(t["text_faint"]))
            f = QFont(self.font())
            f.setPixelSize(12)
            p.setFont(f)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "No preview" if self.failed else "Loading...")
        if on < 0.999:
            p.fillRect(r, QColor(0, 0, 0, round(130 * (1 - on))))
        if h > 0.01:
            # The number, on a soft shade along the bottom edge.
            g = QLinearGradient(0, r.bottom() - 46, 0, r.bottom())
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, round(150 * h)))
            p.fillRect(QRectF(r.left(), r.bottom() - 46, r.width(), 46), g)
            p.setPen(QColor(255, 255, 255, round(235 * h)))
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(r.adjusted(12, 0, -12, -9), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                       "%d" % (self.index + 1))
        p.restore()

        accent = QColor(t["accent"])
        if on > 0.01:
            ring = QColor(accent)
            ring.setAlphaF((0.45 + 0.4 * h) * on)
            p.setPen(QPen(ring, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 13, 13)
        else:
            p.setPen(QPen(theme.qcolor(t["card_border"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 14, 14)

        # The check badge.
        c = QPointF(r.left() + 20, r.top() + 20)
        rad = 11.0
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.6))
        fill = QColor(accent) if on > 0.5 else QColor(0, 0, 0, 90)
        p.setBrush(fill)
        p.drawEllipse(c, rad, rad)
        if on > 0.01:
            mark = QColor(t["accent_text"])
            mark.setAlphaF(on)
            pen = QPen(mark, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(c.x() - 4.6, c.y() + 0.3), QPointF(c.x() - 1.3, c.y() + 3.5),
                            QPointF(c.x() + 4.8, c.y() - 3.4)])
        p.end()


class _Segment(QAbstractButton):
    """One half of the selection card: a mark and a word. Lit when it
    describes the selection as it stands (everything, or nothing)."""

    def __init__(self, kind, text, parent=None):
        super().__init__(parent)
        self.kind = kind          # "all" | "none"
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(30)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        from PySide6.QtGui import QFontMetricsF
        self.setFixedWidth(int(QFontMetricsF(f).horizontalAdvance(text) + 46))
        self._hover = motion.Fader(self)
        self._on = motion.Fader(self, motion.FAST)
        self.toggled.connect(lambda on: self._on.to(1 if on else 0))

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = theme.tokens(cinema.is_dark(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on, h = self._on.value, self._hover.value
        if on > 0.01:
            fill = theme.qcolor(t["pressed_overlay"])
            fill.setAlphaF(fill.alphaF() * on)
            p.setPen(QPen(theme.qcolor(t["card_border"]), 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        elif h > 0.01 and self.isEnabled():
            wash = theme.qcolor(t["hover_overlay"])
            wash.setAlphaF(wash.alphaF() * h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(wash)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        lit = on > 0.5 or h > 0.5
        col = theme.qcolor(t["text"] if lit else t["text_muted"])
        if not self.isEnabled():
            col = theme.qcolor(t["text_faint"])
        accent = QColor(t["accent"])
        c = QPointF(17, r.center().y())
        if self.kind == "all":
            filled = on > 0.5 and self.isEnabled()
            if filled:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
            else:
                p.setPen(QPen(col, 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 6.5, 6.5)
            mark = QColor(t["accent_text"]) if filled else col
            pen = QPen(mark, 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(c.x() - 3.2, c.y() + 0.2), QPointF(c.x() - 0.9, c.y() + 2.4),
                            QPointF(c.x() + 3.3, c.y() - 2.4)])
        else:
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 6.0, 6.0)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(col)
        p.drawText(r.adjusted(30, 0, -10, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   self.text())
        p.end()


class _SelectCard(QWidget):
    """Select all / Select none as one small card of two buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.all_btn = _Segment("all", "Select all")
        self.none_btn = _Segment("none", "Select none")
        lay.addWidget(self.all_btn)
        lay.addWidget(self.none_btn)
        self.setFixedHeight(36)

    def set_state(self, n, total):
        """Lights the half that describes the selection: all, none, or
        neither for a partial pick."""
        for btn, on in ((self.all_btn, total > 0 and n == total), (self.none_btn, total > 0 and n == 0)):
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)
            btn._on.to(1 if on else 0)
        self.all_btn.setEnabled(total > 0)
        self.none_btn.setEnabled(total > 0)
        self.update()

    def paintEvent(self, event):
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        cinema.paint_glass(p, self, r, r.height() / 2, tier=1, sample=False)
        p.end()


class _TileGrid(QWidget):
    """Tiles in as many columns as fit, stretched to use the whole row --
    no fixed four columns of fixed tiles with dead space between them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles = []

    def set_tiles(self, tiles):
        for tile in self.tiles:
            tile.hide()
            tile.deleteLater()
        self.tiles = list(tiles)
        for tile in self.tiles:
            tile.setParent(self)
            tile.show()
        self._place()

    def clear(self):
        self.set_tiles([])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        w = max(1, self.width())
        cols = max(2, (w + _TILE_GAP) // (_TILE_MIN + _TILE_GAP))
        tile_w = (w - _TILE_GAP * (cols - 1)) / cols
        tile_h = round(tile_w * _TILE_RATIO)
        rows = (len(self.tiles) + cols - 1) // cols
        for i, tile in enumerate(self.tiles):
            x = round((i % cols) * (tile_w + _TILE_GAP))
            y = (i // cols) * (tile_h + _TILE_GAP)
            tile.setGeometry(x, y, round(tile_w), tile_h)
        self.setMinimumHeight(max(0, rows * tile_h + max(0, rows - 1) * _TILE_GAP))


class ImagesTab(QWidget):
    _fetch_done_sig = Signal(str, list, int)
    _fetch_error_sig = Signal(str)
    _tile_preview_sig = Signal(int, object)
    _download_progress_sig = Signal(str)
    _download_done_sig = Signal(str, int, int)
    # See VideoTab._cookie_notice_sig.
    _cookie_notice_sig = Signal(str, str)
    # Raised when the user picks "sign in inside this app" -- main_qt wires
    # it to switching over to the Browser tab.
    # A link to sign in for: the Browser tab opens it, so the site's own
    # login is one click away.
    open_browser_requested = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.download_dir = settings_store.get_save_dir(
            settings, "images", config.DEFAULT_DOWNLOAD_DIR)
        os.makedirs(self.download_dir, exist_ok=True)

        self.items = []
        self.post_title = ""

        self._fetch_done_sig.connect(self._on_fetch_done)
        self._fetch_error_sig.connect(self._on_fetch_error)
        self._tile_preview_sig.connect(self._on_tile_preview_ready)
        self._download_progress_sig.connect(lambda t: self.progress_label.setText(t))
        self._download_done_sig.connect(self._on_download_done)
        self._cookie_notice_sig.connect(
            lambda browser, message: self.status_label.setText(
                signin_dialog.handle_cookie_fallback(self.settings, browser, message)))
        downloader.cookie_fallback_listeners.append(self._cookie_notice_sig.emit)

        self._build_ui()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        column = centered_column(self, MAX_CONTENT_W)
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        url_card, url_layout = make_card("Paste a post link")
        root.addWidget(url_card)
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        self.url_entry = QLineEdit()
        self.url_entry.setObjectName("heroField")
        self.url_entry.setFixedHeight(46)
        self.url_entry.setPlaceholderText("Instagram, Facebook, Reddit, X ... every image in the post, full size")
        self.url_entry.returnPressed.connect(self.on_fetch)
        url_row.addWidget(self.url_entry, 1)
        self.fetch_btn = Button("Fetch")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.setFixedSize(116, 46)
        self.fetch_btn.setCursor(Qt.PointingHandCursor)
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
        select_row.setSpacing(10)
        select_row.addWidget(section_label("Images"))
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("mono")
        select_row.addWidget(self.selection_label)
        select_row.addStretch(1)
        self.select_card = _SelectCard()
        self.select_all_btn = self.select_card.all_btn
        self.select_none_btn = self.select_card.none_btn
        self.select_all_btn.clicked.connect(self._select_all)
        self.select_none_btn.clicked.connect(self._select_none)
        self.select_card.set_state(0, 0)
        select_row.addWidget(self.select_card)
        gallery_layout.addLayout(select_row)

        # A bounded, scrollable area: without one a 15-image carousel grew
        # the card, and the window with it, until Save/Download were pushed
        # off-screen with no way to reach them.
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.NoFrame)
        # Scoped by id -- an unscoped "background: transparent" outranks the
        # app stylesheet for every descendant and strips the buttons inside.
        grid_scroll.setObjectName("imagesScroll")
        grid_scroll.setStyleSheet("#imagesScroll { background: transparent; }")
        grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll = grid_scroll
        self.grid_widget = QWidget()
        self.grid_widget.setObjectName("imagesScrollBody")
        self.grid_widget.setStyleSheet("#imagesScrollBody { background: transparent; }")
        self.grid_layout = QVBoxLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 2, 10, 2)
        self.tile_grid = _TileGrid()
        self.grid_layout.addWidget(self.tile_grid)
        self.grid_layout.addStretch(1)
        grid_scroll.setWidget(self.grid_widget)
        gallery_layout.addWidget(grid_scroll, 1)
        self.empty_label = EmptyState(
            "images", "No post loaded",
            "Fetch a post to see every image in it — carousels included — and pick which to keep.")
        self.grid_layout.insertWidget(0, self.empty_label)
        self.tiles = []

        out_card, out_layout = make_card("Save to")
        root.addWidget(out_card)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dir_entry = QLineEdit(self.download_dir)
        dir_row.addWidget(self.dir_entry, 1)
        browse_btn = Button("Browse")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self.browse_dir)
        dir_row.addWidget(browse_btn)
        open_folder_btn = Button("Open Folder")
        open_folder_btn.setCursor(Qt.PointingHandCursor)
        open_folder_btn.clicked.connect(self.open_download_folder)
        dir_row.addWidget(open_folder_btn)
        dir_row.addSpacing(6)
        self.download_btn = Button("Download Selected")
        self.download_btn.setObjectName("accent")
        self.download_btn.setMinimumWidth(170)
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.on_download)
        dir_row.addWidget(self.download_btn)
        out_layout.addLayout(dir_row)

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
        self.fetch_btn.set_busy(True)
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
        self.fetch_btn.set_busy(False)

        if not image_items:
            self.status_label.setText(f"Loaded: {post_title} — no images found in this post.")
            self._rebuild_grid_placeholder("No images found in this post.")
            self.download_btn.setEnabled(False)
            return

        note = f"  ({skipped_videos} video slide(s) skipped — use the Video tab for those)" if skipped_videos else ""
        self.status_label.setText(f"Loaded: {post_title} — {len(image_items)} image(s) found.{note}")
        self._rebuild_grid()
        self.download_btn.setEnabled(True)

    def _on_fetch_error(self, err):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.set_busy(False)
        # yt-dlp's own "use --cookies-from-browser" text is a command-line
        # instruction, and showing it verbatim made the app's answer to "this
        # needs a login" a wall of terminal advice. A link that needs a
        # session gets the dialog that can actually set one up instead.
        if signin_dialog.needs_sign_in(err):
            self.status_label.setText("That link needs a signed-in account.")
            self._offer_sign_in()
            return
        headline, advice = errors.friendly(err)
        self.status_label.setText(headline)
        QMessageBox.warning(self, config.APP_NAME, f"{headline}\n\n{advice}".strip())

    def _offer_sign_in(self):
        choice = signin_dialog.show_sign_in_help(
            self, url=self.url_entry.text().strip(),
            dark_mode=(self.settings or {}).get("theme", "dark") != "light",
            current=self._cookies_from_browser())
        if choice is None:
            return
        if choice == "browser_tab":
            self.open_browser_requested.emit(self.url_entry.text().strip() or "")
            return
        self.settings["cookies_from_browser"] = choice
        settings_store.save_settings(self.settings)
        self.status_label.setText("Using your %s sign-in — fetching again..." % choice)
        self.on_fetch()

    # ------------------------------------------------------------ Gallery ---
    def _clear_grid(self):
        self.tile_grid.clear()
        self.tiles = []
        for w in self.grid_widget.findChildren(EmptyState):
            if w is not self.empty_label:
                w.deleteLater()

    def _rebuild_grid_placeholder(self, text):
        self._clear_grid()
        self.empty_label.hide()
        self.selection_label.setText("")
        self.select_card.set_state(0, 0)
        self.grid_layout.insertWidget(0, EmptyState("images", "No images", text))

    def _rebuild_grid(self):
        self._clear_grid()
        self.empty_label.hide()
        self._generation = getattr(self, "_generation", 0) + 1
        tiles = []
        for idx, item in enumerate(self.items):
            tile = _ImageTile(idx)
            tile.toggled.connect(self._update_selection_label)
            tiles.append(tile)
            threading.Thread(target=self._load_tile_preview, args=(self._generation, idx, item["url"]),
                             daemon=True).start()
        self.tiles = tiles
        self.tile_grid.set_tiles(tiles)
        self._update_selection_label()

    def _load_tile_preview(self, generation, idx, url):
        pil_image = downloader.fetch_thumbnail_image(url, size=(_PREVIEW_PX, _PREVIEW_PX))
        self._tile_preview_sig.emit(generation * 100000 + idx, pil_image)

    def _on_tile_preview_ready(self, tag, pil_image):
        generation, idx = divmod(tag, 100000)
        if generation != getattr(self, "_generation", 0) or idx >= len(self.tiles):
            return  # the grid was rebuilt (a new fetch) while this one loaded
        self.tiles[idx].set_pixmap(_pil_to_pixmap(pil_image))

    def _select_all(self):
        for tile in self.tiles:
            tile.setChecked(True)

    def _select_none(self):
        for tile in self.tiles:
            tile.setChecked(False)

    @property
    def checkboxes(self):
        """The tiles, under the name the rest of the tab (and 2.4) used."""
        return self.tiles

    def _update_selection_label(self):
        n = sum(1 for tile in self.tiles if tile.isChecked())
        self.selection_label.setText(f"{n} of {len(self.tiles)} selected")
        self.select_card.set_state(n, len(self.tiles))
        self.download_btn.setText(f"Download Selected ({n})" if n else "Download Selected")
        self.download_btn.setEnabled(bool(n))

    # ---------------------------------------------------------- Download ---
    def on_download(self):
        selected = [item for item, tile in zip(self.items, self.tiles) if tile.isChecked()]
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
        self.fetch_btn.set_busy(False)
        if saved:
            # Inline status + a self-clearing button confirmation instead of
            # a blocking popup on every completion -- same fix, same reason,
            # as video_tab.py's equivalent (reported directly as disruptive).
            self.progress_label.setText(
                f"✓ Saved {saved} of {total} image(s) to {save_dir}" if saved == total
                else f"Done — {saved} of {total} saved to {save_dir}"
            )
            original_text = self.download_btn.text()
            self.download_btn.setText("✓ Done")
            QTimer.singleShot(2500, lambda: self.download_btn.setText(original_text))
        else:
            self.progress_label.setText("Failed.")
            QMessageBox.critical(self, config.APP_NAME, "Couldn't save any of the selected images.")
