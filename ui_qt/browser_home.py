"""Browser tab's home/new-tab page -- shown instead of a real web page when
the tab first opens or the Home button is clicked, replacing the previous
behaviour of always loading youtube.com. Own module since it's a real,
sizeable chunk of UI (search bar, shortcut tiles, an add-shortcut dialog)
that doesn't belong mixed into browser_tab.py's own toolbar/WebEngine focus.

Chrome's own new-tab page is the explicit model: search bar, a row of
shortcut tiles to frequently-visited sites, a "+" tile to add more.
"""
import threading

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from app.core import favicon
from app.utils import browser_data

from . import theme


def _pil_to_pixmap_rgba(img):
    """RGBA version of video_tab.py's own _pil_to_pixmap -- favicons
    commonly carry real transparency (a logo on a transparent square),
    unlike video thumbnails, which that helper flattens to plain RGB."""
    if img is None:
        return None
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def _search_icon(color, size=16):
    """Vector magnifying-glass icon (a stroked circle + a short diagonal
    handle) -- no Unicode glyph, matching every other icon in this app."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    lens_d = size * 0.62
    painter.drawEllipse(int(size * 0.06), int(size * 0.06), int(lens_d), int(lens_d))
    start = size * 0.62
    painter.drawLine(int(start), int(start), int(size * 0.94), int(size * 0.94))
    painter.end()
    return QIcon(pixmap)


def _theme_icon(color, size=16):
    """Vector accent-color-swatch icon -- two half-circles (a plain
    contrast/theme-toggle glyph), not a Unicode symbol."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    r = size * 0.42
    cx, cy = size / 2, size / 2
    path = QPainterPath()
    path.addEllipse(cx - r, cy - r, r * 2, r * 2)
    painter.setBrush(QColor(color))
    painter.setClipPath(path)
    painter.drawRect(int(cx), int(cy - r), int(r + 1), int(r * 2 + 1))
    painter.setClipping(False)
    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
    painter.end()
    return QIcon(pixmap)


def _image_icon(color, size=16):
    """Vector "picture" icon -- a rounded frame with a small sun circle and
    a mountain triangle, the standard image-placeholder glyph, hand-drawn
    rather than a Unicode symbol."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.1)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.14
    frame = QPainterPath()
    frame.addRoundedRect(m, m, size - 2 * m, size - 2 * m, size * 0.12, size * 0.12)
    painter.drawPath(frame)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    sun_r = size * 0.09
    painter.drawEllipse(int(size * 0.28 - sun_r), int(size * 0.32 - sun_r), int(sun_r * 2), int(sun_r * 2))
    mountain = QPainterPath()
    mountain.moveTo(m + 1, size - m - 1)
    mountain.lineTo(size * 0.42, size * 0.5)
    mountain.lineTo(size * 0.6, size * 0.68)
    mountain.lineTo(size * 0.74, size * 0.52)
    mountain.lineTo(size - m - 1, size - m - 1)
    mountain.closeSubpath()
    painter.setClipPath(frame)
    painter.drawPath(mountain)
    painter.end()
    return QIcon(pixmap)

# 48px reduced 40% -- reported as blurry, and shrinking it also puts the
# display size much closer to a real favicon's native resolution (most
# favicon.ico files are only 16-32px), which was the actual cause of the
# blur (upscaling a small source), not just a size preference.
_ICON_SIZE = 29

_TILE_COLORS = ["#0A84FF", "#FF6961", "#34C759", "#FF9F0A", "#BF5AF2", "#64D2FF", "#FF375F"]


def _tile_color(seed_text):
    return _TILE_COLORS[sum(ord(c) for c in seed_text) % len(_TILE_COLORS)]


class _ShortcutTile(QWidget):
    clicked = Signal()
    remove_requested = Signal()
    # Background-thread payload is a plain PIL Image, not a QPixmap --
    # building Qt image types off the GUI thread isn't safe. The actual
    # QPixmap only gets built in _apply_favicon(), on the GUI thread this
    # signal delivers to.
    _favicon_ready = Signal(object)

    def __init__(self, url, title, dark_mode, parent=None):
        super().__init__(parent)
        self.url = url
        self.setFixedSize(84, 92)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{title}\nRight-click to remove")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignHCenter)

        # Letter-avatar shown immediately (no blocking on network); swapped
        # for the site's real favicon if/when the background fetch below
        # finds one -- most sites don't serve one at the standard
        # /favicon.ico path this looks at, so this fallback is the normal
        # end state for plenty of shortcuts, not an error case.
        #
        # Fetched and displayed at the same resolution (_ICON_SIZE) rather
        # than fetching at one size and scaling to another -- most real
        # favicon.ico files are natively only 16-32px, and upscaling that
        # small a source to fill a much bigger circle is exactly what read
        # as "unclear and blurred". Sized down 40% from the original 48px.
        self.icon_label = QLabel((title or url or "?").strip()[:1].upper())
        self.icon_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self.icon_label.setAlignment(Qt.AlignCenter)
        color = _tile_color(title or url)
        self.icon_label.setStyleSheet(f"""
            background: {color}; color: white; border-radius: {_ICON_SIZE // 2}px;
            font-size: 14px; font-weight: 700;
        """)
        layout.addWidget(self.icon_label, 0, Qt.AlignHCenter)

        self.name_label = QLabel()
        self.name_label.setAlignment(Qt.AlignHCenter)
        self.name_label.setFixedWidth(84)
        self._full_title = title or url
        layout.addWidget(self.name_label, 0, Qt.AlignHCenter)
        self._apply_elided_name()

        self._favicon_ready.connect(self._apply_favicon)
        threading.Thread(target=self._fetch_favicon_thread, args=(url,), daemon=True).start()

    def _fetch_favicon_thread(self, url):
        img = favicon.get_favicon(url, size=_ICON_SIZE)
        if img is not None:
            self._favicon_ready.emit(img)

    def _apply_favicon(self, pil_image):
        pixmap = _pil_to_pixmap_rgba(pil_image)
        if pixmap is None or pixmap.isNull():
            return
        # Circular-cropped to match the letter-avatar it's replacing --
        # most real favicons are square/rectangular, not pre-masked.
        rounded = QPixmap(_ICON_SIZE, _ICON_SIZE)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addEllipse(0, 0, _ICON_SIZE, _ICON_SIZE)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, pixmap.scaled(
            _ICON_SIZE, _ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        painter.end()
        self.icon_label.setPixmap(rounded)
        self.icon_label.setText("")
        self.icon_label.setStyleSheet(f"border-radius: {_ICON_SIZE // 2}px; background: transparent;")

    def _apply_elided_name(self):
        elided = self.name_label.fontMetrics().elidedText(self._full_title, Qt.ElideRight, 80)
        self.name_label.setText(elided)

    def set_text_color(self, color):
        self.name_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        remove_action = menu.addAction("Remove shortcut")
        chosen = menu.exec(event.globalPos())
        if chosen == remove_action:
            self.remove_requested.emit()


class _AddTile(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(84, 92)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignHCenter)

        self.plus_label = QLabel("+")
        self.plus_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self.plus_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.plus_label, 0, Qt.AlignHCenter)

        self.name_label = QLabel("Add shortcut")
        self.name_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.name_label, 0, Qt.AlignHCenter)

    def apply_theme(self, t):
        self.plus_label.setStyleSheet(f"""
            background: {t['card_bg_solid']}; color: {t['text_muted']};
            border: 1px dashed {t['card_border']}; border-radius: {_ICON_SIZE // 2}px; font-size: 15px;
        """)
        self.name_label.setStyleSheet(f"color: {t['text_muted']}; font-size: 11px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class BrowserHomePage(QWidget):
    # Raw text from the search bar, or a raw URL from a tile click -- left
    # un-normalized on purpose; browser_tab.py already owns exactly that
    # logic (_normalize_address) and this module deliberately doesn't
    # import from browser_tab.py to keep the dependency one-directional.
    navigate_requested = Signal(str)
    # The accent choice is a single Browser-tab-wide setting, not something
    # local to one tab's home page -- browser_tab.py's own apply_theme()
    # (which already loops every open tab's home page) is what actually
    # re-colors everything, so this just asks it to run again.
    accent_changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._tiles = []
        self._bg_pixmap = None
        self._load_bg_pixmap()
        self._build_ui()

    def _dark_mode(self):
        return (self.settings or {}).get("theme", "dark") != "light"

    def _load_bg_pixmap(self):
        path = browser_data.get_home_background_image()
        pixmap = QPixmap(path) if path else None
        self._bg_pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(24)
        root.addStretch(2)

        # Name only now, no icon -- just the two-tone wordmark ("Awesome" +
        # "Downloader" in different colours, matching the reference logo),
        # with a real gap between the words this time (spacing between two
        # adjacent labels, not text-embedded whitespace, which Qt tends to
        # collapse at label edges).
        brand_row = QHBoxLayout()
        brand_row.setSpacing(0)
        brand_row.addStretch(1)
        wordmark_row = QHBoxLayout()
        wordmark_row.setSpacing(8)
        font = QFont()
        font.setPointSize(22)
        font.setWeight(QFont.Weight.Bold)
        self.word1_label = QLabel("Awesome")
        self.word2_label = QLabel("Downloader")
        self.word1_label.setFont(font)
        self.word2_label.setFont(font)
        wordmark_row.addWidget(self.word1_label)
        wordmark_row.addWidget(self.word2_label)
        brand_row.addLayout(wordmark_row)
        brand_row.addStretch(1)
        root.addLayout(brand_row)

        # A real button, not a QAction icon embedded in the QLineEdit --
        # the icon-inside-a-line-edit approach looked unpolished and its
        # hit region was unreliable enough to be reported as "the search
        # button doesn't work". A distinct circular button sitting inside
        # the pill's own rounded frame is unambiguously a real, clickable
        # control, matching the reference style directly (a plain white
        # pill with a solid circular search button at its trailing edge).
        search_row = QHBoxLayout()
        search_row.addStretch(1)
        self.search_frame = QFrame()
        self.search_frame.setFixedHeight(48)
        self.search_frame.setFixedWidth(560)
        search_frame_layout = QHBoxLayout(self.search_frame)
        search_frame_layout.setContentsMargins(20, 4, 4, 4)
        search_frame_layout.setSpacing(8)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search the web or enter an address")
        self.search_bar.returnPressed.connect(self._on_search)
        search_frame_layout.addWidget(self.search_bar, 1)

        self.search_btn = QPushButton()
        self.search_btn.setFixedSize(40, 40)
        self.search_btn.setToolTip("Search")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(self._on_search)
        search_frame_layout.addWidget(self.search_btn)

        search_row.addWidget(self.search_frame)
        search_row.addStretch(1)
        root.addLayout(search_row)

        tiles_wrap = QHBoxLayout()
        tiles_wrap.addStretch(1)
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(18)
        tiles_wrap.addLayout(self.tiles_row)
        tiles_wrap.addStretch(1)
        root.addLayout(tiles_wrap)

        root.addStretch(3)

        # Bottom-right controls, pinned to the corner (positioned in
        # resizeEvent below) rather than in the document flow -- Chrome's
        # own new-tab "Customize" affordance sits the same way, out of the
        # centered content's path.
        self.theme_btn = QPushButton(self)
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("Switch accent color")
        self.theme_btn.clicked.connect(self._toggle_accent)

        self.bg_btn = QPushButton(self)
        self.bg_btn.setFixedSize(36, 36)
        self.bg_btn.setCursor(Qt.PointingHandCursor)
        self.bg_btn.setToolTip("Change background image")
        self.bg_btn.clicked.connect(self._pick_background_image)

        self._refresh_tiles()
        self.apply_theme()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margin = 20
        self.bg_btn.move(self.width() - margin - self.bg_btn.width(),
                          self.height() - margin - self.bg_btn.height())
        self.theme_btn.move(self.bg_btn.x() - 8 - self.theme_btn.width(),
                             self.height() - margin - self.theme_btn.height())

    def paintEvent(self, event):
        # Only paints anything itself when a custom background image is
        # set -- otherwise this stays a no-op and the page shows through
        # to the window's own frosted background exactly as before, so
        # nothing changes for anyone who hasn't picked an image.
        if self._bg_pixmap is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scaled = self._bg_pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.drawPixmap(-x, -y, scaled)
            # A scrim so the search bar/tiles/wordmark stay readable over
            # any photo, the same way Chrome's own custom backgrounds do.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
            painter.end()
        super().paintEvent(event)

    def _toggle_accent(self):
        current = browser_data.get_browser_accent()
        browser_data.set_browser_accent("classic" if current == "warm" else "warm")
        self.accent_changed.emit()

    def _pick_background_image(self):
        t = getattr(self, "_theme_tokens", None) or theme.browser_tokens(
            accent=browser_data.get_browser_accent(), dark_mode=self._dark_mode())
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {t['card_bg_solid']};
                color: {t['text']};
                border: 1px solid {t['card_border']};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {t['hover_overlay']}; }}
            QMenu::item:disabled {{ color: {t['text_muted']}; }}
        """)
        set_action = menu.addAction("Choose background image...")
        clear_action = menu.addAction("Remove background image")
        clear_action.setEnabled(self._bg_pixmap is not None)
        chosen = menu.exec(self.bg_btn.mapToGlobal(self.bg_btn.rect().topLeft()))
        if chosen == set_action:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a background image", "",
                "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
            if path:
                browser_data.set_home_background_image(path)
                self._load_bg_pixmap()
                self.update()
        elif chosen == clear_action:
            browser_data.clear_home_background_image()
            self._bg_pixmap = None
            self.update()

    def _on_search(self):
        text = self.search_bar.text().strip()
        if text:
            self.navigate_requested.emit(text)

    def _refresh_tiles(self):
        while self.tiles_row.count():
            item = self.tiles_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._tiles = []

        for s in browser_data.load_shortcuts():
            tile = _ShortcutTile(s["url"], s.get("title", s["url"]), self._dark_mode())
            tile.clicked.connect(lambda _c=False, u=s["url"]: self.navigate_requested.emit(u))
            tile.remove_requested.connect(lambda u=s["url"]: self._remove_shortcut(u))
            self.tiles_row.addWidget(tile)
            self._tiles.append(tile)

        self._add_tile = _AddTile()
        self._add_tile.clicked.connect(self._show_add_dialog)
        self.tiles_row.addWidget(self._add_tile)
        self._add_tile.apply_theme(theme.tokens(dark_mode=self._dark_mode()))
        for tile in self._tiles:
            tile.set_text_color(theme.tokens(dark_mode=self._dark_mode())["text_muted"])

    def _remove_shortcut(self, url):
        browser_data.remove_shortcut(url)
        self._refresh_tiles()

    def _show_add_dialog(self):
        t = theme.tokens(dark_mode=self._dark_mode())
        dlg = QDialog(self)
        dlg.setWindowTitle("Add shortcut")
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"QDialog {{ background: {t['card_bg_solid']}; color: {t['text']}; }}")
        layout = QVBoxLayout(dlg)

        field_style = f"""
            QLineEdit {{
                background: {'#1c1c1e' if self._dark_mode() else '#ffffff'};
                color: {t['text']}; border: 1px solid {t['card_border']};
                border-radius: 6px; padding: 6px 8px;
            }}
        """
        label_style = f"color: {t['text_muted']}; font-size: 11px;"

        name_label = QLabel("Name")
        name_label.setStyleSheet(label_style)
        layout.addWidget(name_label)
        name_edit = QLineEdit()
        name_edit.setStyleSheet(field_style)
        layout.addWidget(name_edit)

        url_label = QLabel("URL")
        url_label.setStyleSheet(label_style)
        layout.addWidget(url_label)
        url_edit = QLineEdit()
        url_edit.setPlaceholderText("example.com")
        url_edit.setStyleSheet(field_style)
        layout.addWidget(url_edit)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        add_btn = QPushButton("Add")
        add_btn.setObjectName("accent")
        for b in (cancel_btn, add_btn):
            b.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{ border-radius: 6px; padding: 6px 14px;
                background: {t['hover_overlay']}; color: {t['text']}; border: none; }}
        """)
        add_btn.setStyleSheet(f"""
            QPushButton {{ border-radius: 6px; padding: 6px 14px;
                background: {t['accent']}; color: {t['accent_text']}; border: none; font-weight: 600; }}
        """)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row)

        def _do_add():
            url = url_edit.text().strip()
            if not url:
                return
            if "://" not in url:
                url = "https://" + url
            title = name_edit.text().strip() or url
            browser_data.add_shortcut(url, title)
            self._refresh_tiles()
            dlg.accept()

        add_btn.clicked.connect(_do_add)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def apply_theme(self):
        """Called once after initial construction and again on every live
        theme toggle -- re-colors in place rather than calling
        _refresh_tiles(), which tears down and rebuilds every tile (and
        re-triggers a favicon fetch for each). That mattered concretely:
        _build_ui() already runs _refresh_tiles() once, and this method
        used to call it too -- a background favicon thread from that first
        build occasionally lost its race with the second build deleting
        the tile out from under it ("Signal source has been deleted")."""
        t = theme.browser_tokens(accent=browser_data.get_browser_accent(), dark_mode=self._dark_mode())
        self._theme_tokens = t
        self.word1_label.setStyleSheet(f"color: {t['text']};")
        # Identity, not action -- the wordmark matches the main window's
        # own blue rather than wearing the orange action colour.
        self.word2_label.setStyleSheet(f"color: {t['brand']};")
        self.search_frame.setStyleSheet(f"""
            QFrame {{
                background: {t['card_bg_solid']};
                border: 1px solid {t['card_border']};
                border-radius: 24px;
            }}
        """)
        self.search_bar.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                color: {t['text']};
                border: none;
                font-size: 14px;
            }}
        """)
        self.search_btn.setStyleSheet(f"""
            QPushButton {{
                background: {t['accent']};
                border: none;
                border-radius: 20px;
            }}
            QPushButton:hover {{ background: {t['accent_hover']}; }}
        """)
        self.search_btn.setIconSize(QSize(18, 18))
        self.search_btn.setIcon(_search_icon(t["accent_text"], size=18))
        corner_btn_style = f"""
            QPushButton {{
                background: {t['card_bg_solid']};
                border: 1px solid {t['card_border']};
                border-radius: 18px;
            }}
            QPushButton:hover {{ background: {t['hover_overlay']}; }}
        """
        self.theme_btn.setStyleSheet(corner_btn_style)
        self.theme_btn.setIconSize(QSize(18, 18))
        self.theme_btn.setIcon(_theme_icon(t["text"], size=18))
        self.bg_btn.setStyleSheet(corner_btn_style)
        self.bg_btn.setIconSize(QSize(18, 18))
        self.bg_btn.setIcon(_image_icon(t["text"], size=18))
        for tile in self._tiles:
            tile.set_text_color(t["text_muted"])
        if hasattr(self, "_add_tile"):
            self._add_tile.apply_theme(t)
