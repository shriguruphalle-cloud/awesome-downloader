"""PySide6 main window shell: frameless window, the cinematic backdrop,
the title bar that hosts the app's nav, and the tab container. Tabs are
added by app/main_qt.py via add_tab() -- this module owns the chrome, not
any feature logic.
"""
import math
import os
import sys
import threading
import time

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetricsF, QIcon, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)
from qframelesswindow import FramelessMainWindow, StandardTitleBar
from qframelesswindow.utils import startSystemMove

from app import config
from app.logging_setup import get_logger
from app.utils import settings as settings_store

from . import cinema, motion, palettes, theme
from .widgets import progress as progress_widgets
from .widgets.wordmark import Wordmark
from .dialogs.about_dialog import show_about
from .dialogs.update_dialog import show_update_dialog
from . import mica
from .mica import apply_frosted_glass, refresh_blur_region, retint_frosted_glass

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(APP_ROOT, "app_icon.ico")
# setWindowIcon() wants the .ico (it carries the small pre-rendered sizes
# Windows picks from). The in-app logo is a single small render, and
# scaling that down from the .ico let Qt pick a mismatched embedded size;
# the clean 512px .png stays sharp at any size.
ICON_PNG_PATH = os.path.join(APP_ROOT, "app_icon.png")

logger = get_logger("main_window")


class BackdropSurface(QWidget):
    """The window's central widget, and the backdrop everything sits on.

    cinematic -- paints the lit backdrop from cinema.py and offers a frosted
                 copy of it to every glass panel (frost()).
    desktop   -- the 2.0-2.4 look: transparent, so DWM's acrylic blur of the
                 desktop shows through. A maximized window gets the painted
                 backdrop instead, because DWM stops compositing the blur
                 behind a maximized window and the see-through tint then
                 washes the whole UI out to a pale grey.
    solid     -- flat ink. Nothing translucent, nothing to sample.

    It always paints every pixel, with CompositionMode_Source where it
    clears: on a WA_TranslucentBackground window a widget that paints
    nothing leaves the previous frame's pixels in the backing store, and a
    new tab's panels then blend over the old tab's -- the ghosting that was
    reported between tabs in earlier versions.
    """

    # How long a resize must pause before the backdrop is re-rendered at the
    # new size. In between, the last render is stretched -- invisible on a
    # backdrop made of soft light, and it keeps a live drag-resize smooth.
    _RERENDER_MS = 110

    # A floating window's corner radius -- Windows' own. Windows 11 rounds a
    # window by 8 px and won't go further. 2.5's first build cut 12 px corners
    # into the backdrop itself, which left a sliver between its curve and
    # Windows' where the desktop showed through ("weird white corners" on a
    # light wallpaper); the rounding is left to Windows now.
    CORNER_RADIUS = 8.0

    def __init__(self, parent=None, mode=cinema.CINEMATIC, dark=True):
        super().__init__(parent)
        self._mode = mode if mode in cinema.MODES else cinema.CINEMATIC
        self._dark = dark
        self._sharp = None
        self._frost = None
        self._key = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._RERENDER_MS)
        self._timer.timeout.connect(self._render)
        # The Browser's home-page wallpaper, continued behind the bars above
        # the page (set_top_image); None for the window's own backdrop.
        self._top_img = None
        self._top_h = 0
        self._frost_top = None

    # ---- state ----
    def mode(self):
        return self._mode

    def set_look(self, mode=None, dark=None):
        if mode is not None and mode in cinema.MODES:
            self._mode = mode
        if dark is not None:
            self._dark = dark
        self._key = None
        self._render()
        self.update()

    # How much of the painted backdrop the acrylic mode keeps over the page
    # area while the window floats. It used to keep none: floating, the window
    # was clear glass over the desktop, and maximized (where DWM stops
    # blurring) it was the painted backdrop -- so every minimize and maximize
    # swapped one look for the other (reported as a "transparency issue").
    # Now both are the same palette; floating, the desktop just shows faintly
    # through it. The title band is always solid.
    DESKTOP_OPACITY = 0.82

    def _painted(self):
        """Whether the lit backdrop is what the window shows right now (in
        the acrylic mode, partly: see DESKTOP_OPACITY)."""
        return self._mode in (cinema.CINEMATIC, cinema.DESKTOP)

    def _see_through(self):
        """The acrylic mode on a floating window: the backdrop at
        DESKTOP_OPACITY over DWM's blur of the desktop."""
        if self._mode != cinema.DESKTOP:
            return False
        window = self.window()
        return window is None or not (window.isMaximized() or window.isFullScreen())

    def set_top_image(self, image, height):
        """Shows `image` (the Browser home page's wallpaper, as it lies above
        the page) across the window's top `height` px, frosted -- the title
        bar and the browser's bars then sit on the wallpaper, not on a
        different backdrop. None puts the window's own backdrop back."""
        if image is not None and (image.isNull() or height <= 0):
            image = None
        if image is self._top_img and height == self._top_h:
            return
        self._top_img, self._top_h = image, int(height)
        self._frost_top = None
        self.update(0, 0, self.width(), max(self._top_h, self._band()) + cinema.BAND_SHADOW + 2)

    def frost(self):
        """The blurred backdrop for glass to sample, or None when the window
        isn't showing the painted backdrop."""
        if not self._painted():
            return None
        if self._top_img is not None and self._frost is not None:
            # Glass up there frosts the wallpaper, not the backdrop under it.
            if self._frost_top is None:
                frost = self._frost.copy()
                fp = QPainter(frost)
                fp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                ky = frost.height() / max(1.0, float(self.height()))
                fp.drawImage(QRectF(0, 0, frost.width(), self._top_h * ky), self._top_img)
                fp.end()
                self._frost_top = frost
            return self._frost_top
        if self._frost is None:
            self._render()
        return self._frost

    # ---- rendering ----
    def _band(self):
        """The title bar's height while it's on screen, else 0 -- the band of
        dark glass painted across the top of the backdrop under it."""
        window = self.window()
        bar = getattr(window, "titleBar", None) if window is not None else None
        if bar is None or bar.isHidden():
            return 0
        return bar.height()

    def _wanted_key(self):
        return (self.width(), self.height(), self._dark, round(self.devicePixelRatioF(), 3),
                self._band(), palettes.current())

    def _render(self):
        if self.width() < 8 or self.height() < 8:
            return
        key = self._wanted_key()
        if key == self._key and self._sharp is not None:
            return
        self._frost_top = None
        self._sharp, self._frost = cinema.render_backdrop(
            self.width(), self.height(), self._dark, self.devicePixelRatioF(), band=key[4])
        self._key = key
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._sharp is None:
            self._render()
        else:
            self._timer.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        if self._painted():
            if self._sharp is None or self._key[2] != self._dark or self._key[5] != palettes.current():
                self._render()
            elif self._key != self._wanted_key() and not self._timer.isActive():
                self._timer.start()
            if self._sharp is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                if self._see_through():
                    painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
                    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                    band = self._band() + cinema.BAND_SHADOW
                    sx = self._sharp.width() / max(1.0, float(self.width()))
                    sy = self._sharp.height() / max(1.0, float(self.height()))
                    # The band solid (it hides DWM's own caption glyphs too),
                    # the page beneath it at DESKTOP_OPACITY.
                    painter.drawPixmap(QRectF(0, 0, self.width(), band), self._sharp,
                                       QRectF(0, 0, self.width() * sx, band * sy))
                    painter.setOpacity(self.DESKTOP_OPACITY)
                    painter.drawPixmap(QRectF(0, band, self.width(), self.height() - band), self._sharp,
                                       QRectF(0, band * sy, self.width() * sx, (self.height() - band) * sy))
                    painter.setOpacity(1.0)
                else:
                    painter.drawPixmap(self.rect(), self._sharp)
            else:
                painter.fillRect(self.rect(), cinema.INK[self._dark])
            if self._top_img is not None:
                self._paint_top_image(painter)
        elif self._mode == cinema.SOLID:
            painter.fillRect(self.rect(), cinema.INK[self._dark])
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            cinema.paint_title_band(painter, self.width(), self._band(), self._dark)
        else:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            self._paint_see_through_band(painter)
        painter.end()
        super().paintEvent(event)

    def _paint_top_image(self, painter):
        """The wallpaper behind the bars: drawn up from a small, blurred strip
        (so it's already soft), then frosted -- a dark wash over all of it,
        and the title band's own glass on top."""
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setOpacity(1.0)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        h = self._top_h
        painter.drawImage(QRectF(0, 0, self.width(), h), self._top_img)
        wash = QColor(*palettes.ink(True)) if self._dark else QColor(*palettes.ink(False))
        wash.setAlpha(96 if self._dark else 120)
        painter.fillRect(QRectF(0, 0, self.width(), h), wash)
        cinema.paint_title_band(painter, self.width(), self._band(), self._dark)
        # Where the bars end and the page begins: a hairline catch of light.
        painter.fillRect(QRectF(0, h - 1, self.width(), 1), QColor(255, 255, 255, 26 if self._dark else 120))
        painter.restore()

    def _paint_see_through_band(self, painter):
        """The band in the desktop (see-through) mode: a denser tint, since
        whatever is behind the window shows through it, and fully opaque over
        the spot where DWM draws its own caption glyphs beneath the window's
        content -- they would read through anything less (see the note above
        _TITLEBAR_H)."""
        band = self._band()
        if band <= 0:
            return
        dark = self._dark
        ink = QColor(*palettes.ink(dark))
        base = ink.darker(150) if dark else ink.lighter(112)
        tint = QColor(base)
        tint.setAlpha(168 if dark else 150)
        painter.fillRect(QRectF(0, 0, self.width(), band), tint)
        cinema.paint_title_band(painter, self.width(), band, dark)
        glyphs = self._native_caption_rect()
        if glyphs is not None:
            fade = QLinearGradient(glyphs.left() - 48, 0, glyphs.left(), 0)
            fade.setColorAt(0.0, tint)
            fade.setColorAt(1.0, base)
            painter.fillRect(QRectF(glyphs.left() - 48, 0, 48, glyphs.bottom() + 1), QBrush(fade))
            painter.fillRect(QRectF(glyphs.left(), 0, self.width() - glyphs.left(),
                                    glyphs.bottom() + 1), base)

    def _native_caption_rect(self):
        """Where DWM draws its own min/max/close glyphs, in this widget's
        coordinates (DWMWA_CAPTION_BUTTON_BOUNDS), or None."""
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            import ctypes.wintypes as wintypes
            import win32gui
            window = self.window()
            hwnd = int(window.winId())
            r = wintypes.RECT()
            if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 5, ctypes.byref(r), ctypes.sizeof(r)) != 0:
                return None
            left, top, _, _ = win32gui.GetWindowRect(hwnd)
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            dpr = max(1.0, window.devicePixelRatioF())
            rect = QRectF((r.left - (cx - left)) / dpr, (r.top - (cy - top)) / dpr,
                          (r.right - r.left) / dpr, (r.bottom - r.top) / dpr)
            origin = self.mapFrom(window, QPoint(0, 0))
            return rect.translated(origin.x(), origin.y()).adjusted(-2, 0, 2, 2)
        except Exception:
            return None

    def corner_radius(self):
        """The radius the window's corners are cut to right now (0 = square)."""
        window = self.window()
        if window is None or window.isMaximized() or window.isFullScreen():
            return 0.0
        if getattr(window, "_snapped", False):
            return 0.0      # snapped to screen edges: square, as Windows does it
        if self._mode == cinema.DESKTOP:
            return 0.0      # DWM's own acrylic frame keeps its own corners
        return self.CORNER_RADIUS


class _Snapshot(QWidget):
    """A still of what was on screen a moment ago, fading away over what's
    there now. How a page change or a theme change cross-fades without a
    graphics effect on the live widgets (which this window can't afford:
    see the note on addWindowAnimation in mica.py)."""

    def __init__(self, parent, pixmap, origin):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pixmap = pixmap
        self._opacity = 1.0
        size = pixmap.deviceIndependentSize().toSize()
        self.setGeometry(QRect(origin, size))
        self.show()
        self.raise_()

    def set_opacity(self, value):
        self._opacity = value
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.drawPixmap(0, 0, self._pixmap)
        p.end()


class _Pages(QTabWidget):
    """The pages, switched with a short cross-fade: the page being left is
    held as a still and fades out over the arriving one, which rises a few
    pixels into place -- one view handing over to the next rather than a
    hard cut. A page hosting a native web view (the Browser) switches
    instantly: nothing Qt paints can sit over a native window."""

    def setCurrentIndex(self, index):  # noqa: N802 -- Qt's name
        old = self.currentWidget()
        new = self.widget(index)
        if (index == self.currentIndex() or old is None or new is None or motion.reduced()
                or not self.isVisible() or getattr(old, "hosts_native_views", False)
                or getattr(new, "hosts_native_views", False)):
            super().setCurrentIndex(index)
            return
        try:
            still = old.grab()
            origin = old.mapTo(self, QPoint(0, 0))
        except RuntimeError:
            super().setCurrentIndex(index)
            return
        super().setCurrentIndex(index)
        overlay = _Snapshot(self, still, origin)
        motion.tween(overlay, 1.0, 0.0, 200, overlay.set_opacity, overlay.deleteLater)
        pos = new.pos()
        motion.tween(new, 12.0, 0.0, 260, lambda v: new.move(pos.x(), pos.y() + round(v)))


class _DragRow(QWidget):
    """The nav row, which is also the window's drag handle.

    Hosting this row inside the title bar covered the title bar completely,
    and the title bar is what the frameless library turns into a drag: every
    press landed on this widget or its children, the library's handler never
    ran, and the window could not be dragged -- so dragging it to a screen
    edge to snap it did nothing at all.

    Qt::WA_TransparentForMouseEvents was the obvious fix and is the wrong one:
    Qt's hit testing skips a transparent widget *and everything inside it*,
    so marking this container made the tab pills and the icon buttons
    unreachable. Verified by checking QApplication.widgetAt() on each button
    -- sending events straight to a widget hides the problem entirely,
    because that path never hit-tests.

    So the drag is implemented here instead: a press that lands on this row
    and not on one of its children starts the same system move the title bar
    would have started, which is what Windows watches for snapping.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_was_bare = False

    def _is_bare(self, pos):
        return self.childAt(pos) is None

    def mousePressEvent(self, event):
        self._press_was_bare = (event.button() == Qt.LeftButton
                                and self._is_bare(event.position().toPoint()))
        if self._press_was_bare:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_was_bare and (event.buttons() & Qt.LeftButton):
            self._press_was_bare = False
            startSystemMove(self.window(), event.globalPosition().toPoint())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_was_bare = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-clicking a title bar maximises it; this row is the title
        bar as far as anyone using it is concerned."""
        if event.button() == Qt.LeftButton and self._is_bare(event.position().toPoint()):
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def _updates_glyph_icon(color, size=16):
    """A refresh loop: a three-quarter arc closed by a solid arrowhead.

    It was a downward arrow into a tray first, which is the universal
    download glyph -- and in a downloader that is the one thing it must not
    look like. "Check for updates" is a check, not a fetch.

    The arrowhead is a filled triangle rather than two stroked lines: at
    16px a stroked chevron on the end of a 1.6px arc merges into the arc and
    reads as a blob, which is exactly how the first attempt rendered.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    inset = size * 0.20
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    # Open at the top, where the arrowhead sits. Qt angles: sixteenths of a
    # degree, counter-clockwise from 3 o'clock.
    p.drawArc(box, int(95 * 16), int(300 * 16))

    r = box.width() / 2.0
    cx, cy = box.center().x(), box.center().y()
    head = size * 0.30
    tip_x = cx + r * 0.10
    # Sits on the arc's open end at the top, pointing clockwise (to the
    # right), which is what makes the loop read as turning.
    path = QPainterPath()
    path.moveTo(QPointF(tip_x + head * 0.62, cy - r))
    path.lineTo(QPointF(tip_x - head * 0.32, cy - r - head * 0.42))
    path.lineTo(QPointF(tip_x - head * 0.32, cy - r + head * 0.42))
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.end()
    return QIcon(pixmap)


def _about_glyph_icon(color, size=16):
    """Lowercase i in a ring.

    The stem is deliberately short and the dot is a separate filled circle
    with real space above it: drawn any taller, or with a heavier pen, the
    two fuse at 16px and the glyph reads as an exclamation mark -- which
    says "warning", not "about".
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inset = 1.5
    p.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))

    cx = size / 2.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    dot = size * 0.115
    p.drawEllipse(QRectF(cx - dot / 2.0, size * 0.255, dot, dot))
    stem_w = size * 0.105
    p.drawRoundedRect(QRectF(cx - stem_w / 2.0, size * 0.455, stem_w, size * 0.275),
                      stem_w / 2.0, stem_w / 2.0)
    p.end()
    return QIcon(pixmap)


def _theme_glyph_icon(color, moon=True, size=14):
    """Crescent moon (dark) or sun (light), drawn as vector.

    The crescent is a filled disc with a second disc punched out of it via
    Clear composition -- a real crescent, rather than two overlapping
    shapes that only look right against one specific background."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))

    if moon:
        r = size * 0.44
        cx = cy = size / 2.0
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.drawEllipse(QRectF(cx - r + size * 0.26, cy - r - size * 0.10,
                                   r * 2, r * 2))
    else:
        r = size * 0.24
        cx = cy = size / 2.0
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        pen = QPen(QColor(color))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            x0, y0 = cx + math.cos(a) * r * 1.7, cy + math.sin(a) * r * 1.7
            x1, y1 = cx + math.cos(a) * r * 2.4, cy + math.sin(a) * r * 2.4
            painter.drawLine(int(x0), int(y0), int(x1), int(y1))
    painter.end()
    return QIcon(pixmap)



def _settings_glyph_icon(color, size=16):
    """A gear: a ring with six squared teeth and an open hub. Six, not
    eight -- at 16px eight teeth close up into a serrated disc."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    c = QPointF(size / 2.0, size / 2.0)
    outer, inner, hub = size * 0.40, size * 0.29, size * 0.12
    path = QPainterPath()
    teeth = 6
    for i in range(teeth * 2):
        a0 = math.radians(i * 180.0 / teeth - 90 - 180.0 / teeth / 2)
        a1 = math.radians((i + 1) * 180.0 / teeth - 90 - 180.0 / teeth / 2)
        r = outer if i % 2 == 0 else inner
        p0 = QPointF(c.x() + r * math.cos(a0), c.y() + r * math.sin(a0))
        p1 = QPointF(c.x() + r * math.cos(a1), c.y() + r * math.sin(a1))
        if i == 0:
            path.moveTo(p0)
        else:
            path.lineTo(p0)
        path.lineTo(p1)
    path.closeSubpath()
    ring = QPainterPath()
    ring.addEllipse(c, hub + 0.2, hub + 0.2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path.subtracted(ring))
    p.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


# ── Title-bar chrome ────────────────────────────────────────────────────
_NAV_H = 34        # the nav island and the action tray
_PILL_H = 28       # a nav label / an action button, inside a 3px inset
_ACTION_BTN = 28


def _chrome_colors(dark):
    """Fills for things that sit on the title bar's glass."""
    if dark:
        return {
            "indicator": QColor(255, 255, 255, 24),
            "indicator_edge_top": QColor(255, 255, 255, 56),
            "indicator_edge_bottom": QColor(255, 255, 255, 12),
            "hover": QColor(255, 255, 255, 12),
            "press": QColor(255, 255, 255, 22),
        }
    return {
        "indicator": QColor(255, 255, 255, 235),
        "indicator_edge_top": QColor(255, 255, 255, 255),
        "indicator_edge_bottom": QColor(15, 23, 42, 34),
        "hover": QColor(15, 23, 42, 10),
        "press": QColor(15, 23, 42, 20),
    }


class _GlassCapsule(QWidget):
    """A pane of the title bar's glass, rounded to a full capsule."""

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        cinema.paint_glass(painter, self, rect, rect.height() / 2.0, tier=2)
        painter.end()


class _TabPill(QPushButton):
    """One nav label. The selected state's surface is the island's sliding
    indicator, so a pill paints only its own hover wash -- antialiased,
    which a QSS radius is not."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("tabPill")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(_PILL_H)
        self.setAttribute(Qt.WA_Hover, True)
        self._badge = False

    def fit_width(self):
        """Exactly wide enough for the label in its *selected* weight, plus
        the padding. Selected labels are semibold, wider than the regular
        weight the button's own sizeHint measures -- sized to that,
        "Download" lost its last letter the moment it was selected. And not
        the sizeHint at all: Qt gives every text button an 80px minimum, so
        "Video" carried 45px of dead space and the row ran out of room."""
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        width = QFontMetricsF(font).horizontalAdvance(self.text())
        self.setFixedWidth(int(math.ceil(width)) + 26)

    def set_badge(self, visible):
        self._badge = bool(visible)
        self.update()

    def paintEvent(self, event):
        if self.underMouse() and not self.isChecked():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            colors = _chrome_colors(cinema.is_dark(self))
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors["press"] if self.isDown() else colors["hover"])
            painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
            painter.end()
        super().paintEvent(event)
        if self._badge:
            # Something happened in this tab while you were elsewhere: a small
            # ember light at the label's shoulder.
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            c = QPointF(self.width() - 9.0, 8.0)
            glow = QRadialGradient(c, 7.0)
            ember = theme.qcolor(theme.tokens(cinema.is_dark(self))["accent"])
            soft = QColor(ember)
            soft.setAlpha(90)
            clear = QColor(ember)
            clear.setAlpha(0)
            glow.setColorAt(0.0, soft)
            glow.setColorAt(1.0, clear)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(c, 7.0, 7.0)
            painter.setBrush(ember)
            painter.drawEllipse(c, 3.2, 3.2)
            painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _NavIsland(_GlassCapsule):
    """The tab strip: labels on a glass capsule, with a raised indicator
    that slides to the selected one.

    The indicator is painted by the island, under the labels, rather than
    by each pill -- one object moving between positions reads as *the*
    selection travelling, where pills lighting up in turn read as switches.
    A thin line of the brand blue along its foot is the one place the nav
    carries the identity colour.
    """

    _SLIDE_MS = 280

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_NAV_H)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(0)
        self._pills = []
        self._current = -1
        self._sliding = None
        self.reduce_motion = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self._SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_slide)
        self._anim.finished.connect(self._on_slide_done)

    def add_pill(self, pill):
        self._row.addWidget(pill)
        self._pills.append(pill)

    def set_current(self, index, animate=True):
        if not 0 <= index < len(self._pills):
            return
        previous = self._current
        self._current = index
        can_slide = (animate and not self.reduce_motion and self.isVisible()
                     and 0 <= previous < len(self._pills) and previous != index)
        if not can_slide:
            self._anim.stop()
            self._sliding = None
            self.update()
            return
        start = self._sliding if self._sliding is not None else self._rest_rect(previous)
        self._anim.stop()
        self._anim.setStartValue(QRectF(start))
        self._anim.setEndValue(self._rest_rect(index))
        self._anim.start()

    def _rest_rect(self, index):
        return QRectF(self._pills[index].geometry())

    def _on_slide(self, value):
        self._sliding = QRectF(value)
        self.update()

    def _on_slide_done(self):
        self._sliding = None
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not 0 <= self._current < len(self._pills):
            return
        rect = self._sliding if self._sliding is not None else self._rest_rect(self._current)
        if rect.width() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = cinema.is_dark(self)
        colors = _chrome_colors(dark)
        pill = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        radius = pill.height() / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colors["indicator"])
        painter.drawRoundedRect(pill, radius, radius)
        edge = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        edge.setColorAt(0.0, colors["indicator_edge_top"])
        edge.setColorAt(1.0, colors["indicator_edge_bottom"])
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(pill, radius, radius)

        # The light strip.
        brand = theme.qcolor(theme.tokens(dark)["brand"])
        strip_w = min(22.0, pill.width() * 0.34)
        cx, y = pill.center().x(), pill.bottom() - 2.0
        glow = QRadialGradient(QPointF(cx, y), strip_w)
        soft = QColor(brand)
        soft.setAlpha(110 if dark else 70)
        clear = QColor(brand)
        clear.setAlpha(0)
        glow.setColorAt(0.0, soft)
        glow.setColorAt(1.0, clear)
        painter.save()
        painter.translate(cx, y)
        painter.scale(1.0, 0.22)
        painter.translate(-cx, -y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(cx, y), strip_w, strip_w)
        painter.restore()
        painter.setBrush(brand)
        painter.drawRoundedRect(QRectF(cx - strip_w / 2.0, y - 0.75, strip_w, 1.5), 0.75, 0.75)
        painter.end()


class _IconButton(QPushButton):
    """A round icon button for the title bar's action tray, with its own
    antialiased hover disc."""

    _QSS = "QPushButton { background: transparent; border: none; padding: 0px; }"

    def __init__(self, tip, parent=None):
        super().__init__(parent)
        self.setFixedSize(_ACTION_BTN, _ACTION_BTN)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setIconSize(QSize(16, 16))
        self.setStyleSheet(self._QSS)
        self.setAttribute(Qt.WA_Hover, True)

    def paintEvent(self, event):
        if self.underMouse():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            colors = _chrome_colors(cinema.is_dark(self))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors["press"] if self.isDown() else colors["hover"])
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            painter.end()
        super().paintEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _UpdatePill(QPushButton):
    """"Update available" -- in the title bar, beside the coffee cup, only
    when GitHub has a newer release. Ember, because it is the one thing in
    the chrome that asks you to act. As tall as the glass capsules beside it,
    on their centre line. It opens the update panel (dialogs/
    app_update_dialog.py), which checks the release before installing it."""

    _QSS = "QPushButton { background: transparent; border: none; padding: 0px 14px 0px 34px; }"
    TEXT = "Update available"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_NAV_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(self._QSS)
        self.setAttribute(Qt.WA_Hover, True)
        self.release = None
        self.setVisible(False)

    def show_release(self, release):
        self.release = release
        version = release.get("version", "")
        self.setText(self.TEXT)
        self.setToolTip(f"Awesome Downloader {version} is available -- click to update from inside the app")
        self.setAccessibleName(self.toolTip())
        self.setVisible(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        ember = theme.qcolor(t["accent"])
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        wash = QColor(ember)
        wash.setAlpha(46 if self.underMouse() else 26)
        painter.setBrush(wash)
        line = QColor(ember)
        line.setAlpha(170)
        painter.setPen(QPen(line, 1.0))
        painter.drawRoundedRect(rect, radius, radius)
        # A download arrow in an ember disc, then the words.
        c = QPointF(18.0, rect.center().y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ember)
        painter.drawEllipse(c, 9.0, 9.0)
        pen = QPen(theme.qcolor(t["accent_text"]), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(c.x(), c.y() - 4.5), QPointF(c.x(), c.y() + 3.0))
        painter.drawPolyline([QPointF(c.x() - 3.2, c.y() - 0.2), QPointF(c.x(), c.y() + 3.0),
                              QPointF(c.x() + 3.2, c.y() - 0.2)])
        painter.setPen(theme.qcolor(t["text"]))
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(rect.adjusted(34, 0, -14, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()

    def sizeHint(self):
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        width = QFontMetricsF(font).horizontalAdvance(self.text())
        return QSize(int(math.ceil(width)) + 50, _NAV_H)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


# ── Caption buttons ─────────────────────────────────────────────────────
# Three traffic-light dots in their own glass capsule at the right end of the
# title row: centred on the same line as the nav island and the action tray,
# and inset by the pages' own side margin so the capsule lines up with the
# cards below it.
#
# Windows is told these are the real caption buttons (WM_NCHITTEST answers
# HTMINBUTTON / HTMAXBUTTON / HTCLOSE over them), which is what gives the
# maximize dot Windows 11's Snap Layouts flyout. DWM still draws its own
# min/max/close glyphs in the window's top-right corner (where
# DWMWA_CAPTION_BUTTON_BOUNDS says they are), *beneath* the app's content.
# The cinematic and solid backdrops are opaque there and hide them; the
# see-through desktop mode lays an opaque patch of the title band over that
# spot (BackdropSurface._paint_see_through_band). 2.0-2.4 were see-through,
# so their dots had to sit right on top of those glyphs to hide them --
# which is why the dots used to hug the top edge, above the row's centre.
#
# The title row: the 34px nav island with 9px of glass above and below it.
_TITLEBAR_H = 52
# 12px (macOS's own size) was reported as too small to find at a glance.
_CAPTION_DOT = 16        # dot diameter
_CAPTION_BTN_W = 28      # one dot's hit area; also the dots' pitch
_CAPTION_PAD = 8         # capsule padding either side of the three dots
# Room between the action tray and the caption capsule.
_CAPTION_CLEARANCE = 10

_CAPTION_GLYPH = QColor(40, 14, 8, 175)


def _caption_dot_icon(kind, color, state, dark, maximized=False, dpr=1.0):
    """One caption dot, drawn on a canvas the size of its button.

    state: "rest"; "near" -- the pointer is over the cluster, so all three
    show what they do, as on a Mac; "hot" -- over this one. They keep their
    colour while the window is inactive: greyed out (as on a Mac) they read
    as missing."""
    w, h = _CAPTION_BTN_W, _NAV_H
    pixmap = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy, r = w / 2.0, h / 2.0, _CAPTION_DOT / 2.0
    dot = QRectF(cx - r, cy - r, 2 * r, 2 * r)

    fill = QColor(color)
    rim = QColor(color).darker(128)
    rim.setAlpha(150)
    if state == "hot":
        # A soft halo, outside the dot, so the dot itself never moves.
        halo = QColor(color)
        halo.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(dot.adjusted(-4.0, -4.0, 4.0, 4.0))
        fill = fill.lighter(108)
    p.setPen(QPen(rim, 0.8))
    p.setBrush(fill)
    p.drawEllipse(dot.adjusted(0.4, 0.4, -0.4, -0.4))
    # The light-catch across the upper half of the dot.
    gloss = QLinearGradient(dot.topLeft(), dot.bottomLeft())
    gloss.setColorAt(0.0, QColor(255, 255, 255, 95))
    gloss.setColorAt(0.55, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gloss)
    p.drawEllipse(dot.adjusted(2.0, 1.0, -2.0, -r * 0.7))

    if state in ("near", "hot"):
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(_CAPTION_GLYPH, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if kind == "min":
            p.drawLine(QPointF(cx - 4.0, cy), QPointF(cx + 4.0, cy))
        elif kind == "close":
            a = 3.4
            p.drawLine(QPointF(cx - a, cy - a), QPointF(cx + a, cy + a))
            p.drawLine(QPointF(cx - a, cy + a), QPointF(cx + a, cy - a))
        else:
            # Two corner wedges: pointing out to maximize, in to restore.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_CAPTION_GLYPH)
            a, b = 4.3, 0.9
            if maximized:
                p.drawPolygon(QPolygonF([QPointF(cx - b, cy - b), QPointF(cx - b, cy - a),
                                         QPointF(cx - a, cy - b)]))
                p.drawPolygon(QPolygonF([QPointF(cx + b, cy + b), QPointF(cx + b, cy + a),
                                         QPointF(cx + a, cy + b)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(cx - a, cy - a), QPointF(cx + 1.4, cy - a),
                                         QPointF(cx - a, cy + 1.4)]))
                p.drawPolygon(QPolygonF([QPointF(cx + a, cy + a), QPointF(cx - 1.4, cy + a),
                                         QPointF(cx + a, cy - 1.4)]))
    p.end()
    return QIcon(pixmap)


class _CleanTitleBar(StandardTitleBar):
    """Titlebar whose window controls are ordinary QSS-styled QPushButtons
    instead of qframelesswindow's own custom-painted TitleBarButtons.

    Ghosting on the min/max/close icons was reported four separate times and
    survived two attempts to fix it by clearing pixels before painting --
    first on this widget, then on the content surface. The reason those
    didn't work: Qt repaints a child widget on its own when only that child
    is dirty (a hover, a press), *without* repainting its parent. So
    clearing here only helped on the rare frames where the whole titlebar
    happened to repaint; on a hover, the button re-drew its icon straight
    over its own stale pixels, and on a WA_TranslucentBackground window
    nothing had overwritten them. Hence doubled strokes.

    Rather than chase that with a third clear, this drops the custom
    painting altogether. A QPushButton styled through QSS is drawn by Qt's
    own style engine, which handles its own background fill correctly on a
    translucent window -- there is no paintEvent of ours left to get wrong.
    It also finally makes the icons real text, so their weight is a font
    weight ("bold" was asked for and wasn't reachable through the library's
    hardcoded 1px QPen).

    The library's own buttons are hidden rather than removed: they stay
    parented and wired exactly as the base class expects, so none of its
    internal geometry/drag handling changes -- they just never render.
    """

    # Transparent button: the dot is the icon (_caption_dot_icon), drawn on a
    # canvas the size of the button, so the hit area can be bigger than it.
    _BTN_QSS = """
        QPushButton {
            background: transparent;
            border: none;
            padding: 0px;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # hide() alone was not enough and is why this kept coming back: the
        # base class re-shows these buttons on its own (window state changes
        # call into maxBtn, and a re-show puts all three back), so the
        # library's grey dash and ✕ ended up drawn alongside -- and on top of
        # -- the colored chips below. Reported repeatedly as the buttons
        # "getting bad every time", which is exactly what a second, stale set
        # of controls sharing the row looks like.
        #
        # These stay alive because the base class still calls methods on them
        # (maxBtn.setMaxState on maximize/restore), but they are pulled out of
        # the layout, pinned to zero size, and flagged never to reach the
        # screen. A 0x0 WA_DontShowOnScreen widget renders nothing even if
        # something calls show() on it again, so there is no path back to the
        # doubled row.
        self._orphans = []
        for btn in (self.minBtn, self.maxBtn, self.closeBtn):
            self.hBoxLayout.removeWidget(btn)
            btn.setAttribute(Qt.WA_DontShowOnScreen, True)
            btn.setFixedSize(0, 0)
            btn.setVisible(False)
            # Reparented away from this titlebar entirely: while a button is
            # still our child, any repaint of ours can give it a chance to
            # draw, which is how the library's dash and ✕ kept reappearing
            # on top of the colored chips. A parentless hidden widget cannot
            # paint onto our window at all. The list keeps a Python
            # reference so they aren't garbage-collected -- the base class
            # still calls into maxBtn on maximize/restore.
            btn.setParent(None)
            self._orphans.append(btn)

        # macOS traffic-light colours in Windows order (minimize, maximize,
        # close, left to right) -- the colours were asked for, the order is
        # left alone so the buttons stay where Windows users reach for them.
        self._cluster = _GlassCapsule(self)
        self._cluster.setFixedSize(3 * _CAPTION_BTN_W + 2 * _CAPTION_PAD, _NAV_H)
        cluster_row = QHBoxLayout(self._cluster)
        cluster_row.setContentsMargins(_CAPTION_PAD, 0, _CAPTION_PAD, 0)
        cluster_row.setSpacing(0)
        self._buttons = []
        self._icon_cache = {}
        for kind, color, hover, tip, slot in (
            ("min", "#27C93F", "#4EE063", "Minimize", self._on_min),
            ("max", "#FFBD2E", "#FFD268", "Maximize", self._on_max),
            ("close", "#FF5F57", "#FF8A84", "Close", self._on_close),
        ):
            # Plain QPushButtons, reported to Windows as non-client caption
            # buttons (see MainWindow._titlebar_nc_buttons): Qt never sees the
            # pointer enter or leave them, so hover is driven by hand from
            # WM_NCMOUSEMOVE (MainWindow._update_nc_hover -> refresh_caption).
            b = QPushButton(self._cluster)
            b.setFixedSize(_CAPTION_BTN_W, _NAV_H)
            b.setIconSize(QSize(_CAPTION_BTN_W, _NAV_H))
            b.setCursor(Qt.ArrowCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setStyleSheet(self._BTN_QSS)
            b.clicked.connect(slot)
            b._kind, b._color = kind, color
            cluster_row.addWidget(b)
            self._buttons.append(b)
        self.refresh_caption()
        self._layout_caption_buttons()

        # The library's own title text is redundant once the app's wordmark
        # is hosted in this same row, and it sat behind it.
        for name in ("titleLabel", "iconLabel"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.hide()

    @staticmethod
    def _pass_through_mouse(*widgets):
        """Lets a press fall through decoration to the title bar underneath.

        The title bar is the window's drag handle, and dragging it to a screen
        edge is how Windows snapping is triggered. Hosting the nav row inside
        the title bar covered 100% of that handle: every press landed on the
        row's container, the island's painted tray or a label, none of which
        forward anything, so the library's drag handler never ran and the
        window could not be dragged or snapped at all.

        Only pass widgets with no interactive children. The attribute is
        per-widget, but Qt's hit testing skips a transparent widget entirely
        rather than looking inside it, so anything nested under one becomes
        unclickable.
        """
        for widget in widgets:
            if widget is not None:
                widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def host(self, widget):
        """Puts the app's own top row inside the title bar.

        It used to be a separate row below, which meant the top of the window
        was a full-width 32px strip containing nothing but three small caption
        chips at the right -- the "negative space" this removes. Sharing one
        band also makes the whole strip a drag handle: dragging the window to
        a screen edge to snap it previously meant hitting that thin empty
        band, since the nav row below it swallowed the press.
        """
        self.hBoxLayout.insertWidget(0, widget, 1)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # The base class keeps its own stretch=1 spacer between the (now
        # hidden) title labels and the buttons. Left in place it competes with
        # the hosted row for the slack and each gets half, which stranded the
        # theme/Updates/About cluster in the middle of the row instead of at
        # the right-hand end where it has always sat. Every spacer but ours
        # gives up its stretch.
        for i in range(self.hBoxLayout.count()):
            item = self.hBoxLayout.itemAt(i)
            if item.spacerItem() is not None:
                self.hBoxLayout.setStretch(i, 0)

    @staticmethod
    def _inset():
        """The capsule's distance from the window's right edge: the pages'
        side margin, so its edge lines up with the cards' edges below."""
        return MainWindow._PADDED_MARGINS[2]

    def caption_width(self):
        """How much room at the right end of the row the caption capsule
        takes, so the hosted nav row keeps its last button clear of it."""
        return self._cluster.width() + self._inset()

    def _layout_caption_buttons(self):
        x = self.width() - self._inset() - self._cluster.width()
        y = (self.height() - self._cluster.height()) // 2
        self._cluster.move(x, y)
        self._cluster.raise_()

    def refresh_caption(self, hot=None):
        """Repaints the dots for where the pointer is (`hot` is the button
        under it, or None), whether the window is active, and the theme."""
        window = self.window()
        dark = cinema.is_dark(self)
        maximized = window is not None and window.isMaximized()
        dpr = max(1.0, self.devicePixelRatioF())
        for b in self._buttons:
            state = "rest" if hot is None else ("hot" if b is hot else "near")
            key = (b._kind, state, dark, maximized, dpr)
            icon = self._icon_cache.get(key)
            if icon is None:
                color = b._color
                icon = _caption_dot_icon(b._kind, color, state, dark, maximized, dpr)
                self._icon_cache[key] = icon
            b.setIcon(icon)
        self._cluster.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_caption_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_caption_buttons()

    def _on_min(self):
        self.window().showMinimized()

    def _on_max(self):
        w = self.window()
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def _on_close(self):
        self.window().close()



class MainWindow(FramelessMainWindow):
    # single_instance.py's listener calls focus_window() from a background
    # socket thread; Qt queues this signal onto the GUI thread.
    _focus_requested = Signal()
    # The startup update check runs on a plain thread and reports back here.
    _update_found = Signal(object)

    def __init__(self, dark_mode=True, settings=None):
        super().__init__()
        # True while the window is snapped against the screen's edges
        # (Win+arrow, or dragged to an edge) -- see _update_snap_state.
        self._snapped = False
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(60)
        self._snap_timer.timeout.connect(self._update_snap_state)
        self._focus_requested.connect(self._focus_window)
        self._update_found.connect(self._on_update_found)
        self.dark_mode = dark_mode
        # Kept so toggle_theme() and the Settings panel can write back.
        self.settings = settings
        palettes.set_current(self._setting("palette", palettes.DEFAULT))
        self._backdrop_mode = self._setting("backdrop", cinema.CINEMATIC)
        if self._backdrop_mode not in cinema.MODES:
            self._backdrop_mode = cinema.CINEMATIC

        self.setWindowTitle("AWESOME DOWNLOADER")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        # 1000x680 is the size this was designed at, clamped to what the
        # screen actually offers: on a 1366x768 laptop, or any display at
        # 125%/150% scaling, a window that size would open with its lower
        # edge off the bottom of the screen.
        preferred = QSize(1100, 720)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            preferred.setWidth(min(preferred.width(), avail.width() - 40))
            preferred.setHeight(min(preferred.height(), avail.height() - 40))
        # A floor, not a target: every tab scrolls its own content. Small
        # enough to snap to half of a 1366-wide screen.
        self.setMinimumSize(760, 480)
        self.resize(preferred)
        if screen is not None:
            geo = self.frameGeometry()
            geo.moveCenter(screen.availableGeometry().center())
            self.move(geo.topLeft())

        self.setTitleBar(_CleanTitleBar(self))
        self.titleBar.setFixedHeight(_TITLEBAR_H)

        # Needed for the desktop-acrylic backdrop to show through at all:
        # without it Qt's raster backing store has no alpha channel. The
        # painted backdrops fill every pixel, so it costs them nothing.
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Snap Layouts: WS_MINIMIZEBOX / WS_MAXIMIZEBOX stay set, and the
        # coloured chips are reported to DWM as the real caption buttons via
        # WM_NCHITTEST -- see nativeEvent() and _update_nc_hover().
        self._nc_hover_btn = None
        if sys.platform == "win32":
            import win32con
            self._NC_BUTTON_CODES = {win32con.HTMINBUTTON, win32con.HTMAXBUTTON, win32con.HTCLOSE}
        else:
            self._NC_BUTTON_CODES = set()
        self._ensure_native_caption_buttons()

        central = BackdropSurface(self, mode=self._backdrop_mode, dark=self.dark_mode)
        central.setObjectName("centralSurface")
        self._central = central
        # Glass panels find the backdrop to frost through this attribute
        # (cinema.backdrop_for).
        self.backdrop_surface = central
        layout = QVBoxLayout(central)
        layout.setContentsMargins(*self._PADDED_MARGINS)
        self._content_layout = layout
        self._full_bleed_pages = set()
        layout.setSpacing(0)

        # ---- the title row: identity left, nav centred, actions right ----
        topbar = _DragRow()
        self.topbar = topbar
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setSpacing(10)
        self._topbar_layout = topbar_layout

        # The lightning logo and the name in the website's serif, one painted
        # piece (widgets/wordmark.py): the logo as tall as the letters, right
        # against them. When the row is too narrow for the name, the logo
        # stays on its own, the same size.
        self._wordmark = Wordmark(px=23, logo=ICON_PNG_PATH)
        d = max(1, round(self._wordmark.logo_diameter()))
        self._logo_label = QLabel()
        self._logo_label.setFixedSize(d, d)
        self._set_logo_pixmap()
        self._logo_label.hide()
        topbar_layout.addWidget(self._logo_label)
        topbar_layout.addWidget(self._wordmark)
        # Kept as names for anything still reading them; the wordmark is one
        # painted widget now.
        self.title_word1 = self.title_word2 = self._wordmark

        self.tabs = _Pages(central)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().hide()
        self._tab_buttons = []
        self.tabs.currentChanged.connect(self._sync_island_selection)

        self._island = _NavIsland()
        self._island.reduce_motion = bool(self._setting("reduce_motion", False))
        progress_widgets.set_reduce_motion(self._island.reduce_motion)
        motion.set_reduce_motion(self._island.reduce_motion)
        topbar_layout.addStretch(1)
        topbar_layout.addWidget(self._island)
        topbar_layout.addStretch(1)

        self.update_pill = _UpdatePill()
        self.update_pill.clicked.connect(lambda: self.open_app_update())

        # Window actions in their own glass tray: one group, not four loose
        # marks drifting between the nav and the caption chips.
        self._action_tray = _GlassCapsule()
        self._action_tray.setFixedHeight(_NAV_H)
        tray_layout = QHBoxLayout(self._action_tray)
        tray_layout.setContentsMargins(3, 3, 3, 3)
        tray_layout.setSpacing(2)
        self.settings_btn = _IconButton("Settings")
        self.settings_btn.clicked.connect(lambda: self.open_panel("settings"))
        self.theme_btn = _IconButton("Switch theme")
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.update_btn = _IconButton("Check for updates")
        self.update_btn.clicked.connect(lambda: self.open_panel("updates"))
        self.about_btn = _IconButton("About Awesome Downloader")
        self.about_btn.clicked.connect(lambda: self.open_panel("about"))
        for btn in (self.settings_btn, self.theme_btn, self.update_btn, self.about_btn):
            tray_layout.addWidget(btn)
        topbar_layout.addWidget(self._action_tray)
        # "Update available", when there is one: between the tray and the cup.
        topbar_layout.addWidget(self.update_pill)

        # Donate: a coffee cup in a little glass disc of its own, between the
        # tray and the caption dots -- the website's "Buy me a coffee".
        self._donate_capsule = _GlassCapsule()
        self._donate_capsule.setFixedSize(_NAV_H, _NAV_H)
        donate_layout = QHBoxLayout(self._donate_capsule)
        donate_layout.setContentsMargins(3, 3, 3, 3)
        self.donate_btn = _IconButton("Buy me a coffee -- support Awesome Downloader")
        self.donate_btn.clicked.connect(self.open_donate)
        donate_layout.addWidget(self.donate_btn)
        topbar_layout.addWidget(self._donate_capsule)

        # Full screen: the whole app, borderless, filling the screen (F11) --
        # beside the caption dots, the other way of sizing the window.
        self._fullscreen_capsule = _GlassCapsule()
        self._fullscreen_capsule.setFixedSize(_NAV_H, _NAV_H)
        fs_layout = QHBoxLayout(self._fullscreen_capsule)
        fs_layout.setContentsMargins(3, 3, 3, 3)
        self.fullscreen_btn = _IconButton("Full screen (F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_app_fullscreen)
        fs_layout.addWidget(self.fullscreen_btn)
        topbar_layout.addWidget(self._fullscreen_capsule)

        # Left margin matches the content's, so the logo starts on the same
        # vertical line as the panels below it. The right margin is the
        # caption capsule's footprint: it is positioned by hand (see
        # _CleanTitleBar), so the row has to reserve its room itself.
        topbar_layout.setContentsMargins(
            self._PADDED_MARGINS[0], 0,
            self.titleBar.caption_width() + _CAPTION_CLEARANCE, 0)
        self.titleBar.host(topbar)
        _CleanTitleBar._pass_through_mouse(self._logo_label)
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)
        # QMainWindow's central-widget plumbing can restack siblings; the
        # title bar has to stay on top to keep receiving clicks.
        self.titleBar.raise_()

        self._app_fullscreen = False
        self._edge_shown = False
        self._edge_left_at = None
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(90)
        self._edge_timer.timeout.connect(self._watch_top_edge)
        f11 = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        f11.setContext(Qt.ShortcutContext.ApplicationShortcut)
        f11.activated.connect(self.toggle_app_fullscreen)

        theme.load_custom_fonts()
        app = QApplication.instance()
        if app is not None:
            # A family *chain*: Inter has no glyphs for the few symbols the
            # UI still uses, and with a single family Qt drew them as boxes.
            # Weight pinned because Inter is a variable font whose default
            # instance isn't guaranteed to be Regular.
            font = QFont()
            font.setFamilies(["Inter", "Segoe UI Variable", "Segoe UI",
                              "Segoe UI Emoji", "Segoe UI Symbol"])
            font.setPointSize(10)
            font.setWeight(QFont.Weight.Medium)
            app.setFont(font)

        self._effect = None
        if self._backdrop_mode == cinema.DESKTOP:
            self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        self.setStyleSheet(theme.build_stylesheet(dark_mode=self.dark_mode))
        self._restyle_chrome()

    def _setting(self, key, default=None):
        return (self.settings or {}).get(key, default)

    def _set_logo_pixmap(self):
        if not os.path.exists(ICON_PNG_PATH):
            return
        dpr = max(1.0, self.devicePixelRatioF())
        size = int(round(self._logo_label.width() * dpr))
        pixmap = QPixmap(ICON_PNG_PATH).scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(dpr)
        self._logo_label.setPixmap(pixmap)

    # ------------------------------------------------------------- tabs ---
    def add_tab(self, widget, label):
        """Adds a page. The page goes into the bar-less QTabWidget and a
        label into the nav island; both only ever grow through here, so
        their indices stay in lockstep."""
        index = self.tabs.count()
        self.tabs.addTab(widget, label)
        btn = _TabPill(label)
        btn.fit_width()
        btn.clicked.connect(lambda _c=False, i=index: self.tabs.setCurrentIndex(i))
        self._island.add_pill(btn)
        self._tab_buttons.append(btn)
        self._sync_island_selection(self.tabs.currentIndex(), animate=False)
        self._fit_title_row()

    # Normal tabs are glass panels on the backdrop and want room around
    # them. The Browser is a web page: any margin around it is a strip of
    # dead chrome framing the site, so margins belong to the page.
    _PADDED_MARGINS = (18, 0, 18, 14)
    _BLEED_MARGINS = (0, 0, 0, 0)
    _TOP_GAP = 10

    def set_full_bleed(self, widget, full_bleed=True):
        """Marks a page as one that should run edge to edge."""
        if full_bleed:
            self._full_bleed_pages.add(widget)
        else:
            self._full_bleed_pages.discard(widget)
        self._apply_page_margins(self.tabs.currentIndex())

    def _apply_page_margins(self, index):
        if getattr(self, "_app_fullscreen", False):
            page = self.tabs.widget(index)
            if page in self._full_bleed_pages:
                self._content_layout.setContentsMargins(0, 0, 0, 0)
            else:
                left, _top, right, bottom = self._PADDED_MARGINS
                self._content_layout.setContentsMargins(left, self._TOP_GAP + 8, right, bottom)
            return
        if getattr(self, "_video_fullscreen", False):
            # A page's video fills the whole screen: no room kept for the
            # hidden title bar (that was the dead band along the top).
            self._content_layout.setContentsMargins(0, 0, 0, 0)
            return
        page = self.tabs.widget(index)
        left, _, right, bottom = (
            self._BLEED_MARGINS if page in self._full_bleed_pages else self._PADDED_MARGINS)
        top = self.titleBar.height() + (
            0 if page in self._full_bleed_pages else self._TOP_GAP)
        self._content_layout.setContentsMargins(left, top, right, bottom)

    def _sync_island_selection(self, index, animate=True):
        self._apply_page_margins(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)
        self._island.set_current(index, animate=animate)
        # Switching into a tab is itself "I've seen what's in here now".
        self.set_tab_badge(index, False)

    def set_tab_badge(self, index, visible):
        """A small light on a tab's label -- e.g. a download started in the
        Download tab while you were looking at another one."""
        if 0 <= index < len(self._tab_buttons):
            self._tab_buttons[index].set_badge(visible)

    def action_buttons(self):
        """Every control in the title row's action area, in order."""
        return [self.update_pill, self.settings_btn, self.theme_btn,
                self.update_btn, self.about_btn, self.donate_btn]

    def open_donate(self):
        """The donate panel, dropping from the coffee button: PayPal, or UPI
        by QR -- what the website's "Buy me a coffee" offers."""
        from .dialogs.donate_panel import DonatePanel
        t = theme.tokens(dark_mode=self.dark_mode)
        panel = DonatePanel(self, t, self.dark_mode)
        panel.open_under(self.donate_btn)
        return panel

    # ------------------------------------------------------------ panels ---
    def open_panel(self, name):
        """Opens one of the chrome's panels by name: "settings", "updates"
        or "about". Blocks until it closes, like the dialogs themselves."""
        if name == "about":
            show_about(self, dark_mode=self.dark_mode)
        elif name == "updates":
            show_update_dialog(self, dark_mode=self.dark_mode)
        elif name == "settings":
            from .dialogs.settings_dialog import show_settings
            show_settings(self)
        else:
            raise ValueError(f"no panel called {name!r}")

    # ----------------------------------------------------------- updates ---
    def start_update_check(self):
        """Asks GitHub once, off the GUI thread, whether a newer release
        exists. Silent on every failure (app_update.latest_release)."""
        if not self._setting("check_app_updates", True):
            return
        from app.utils import app_update

        def work():
            release = app_update.newer_release()
            if release:
                self._update_found.emit(release)

        threading.Thread(target=work, name="awd-app-update", daemon=True).start()

    def _on_update_found(self, release):
        self.available_release = release
        self.update_pill.show_release(release)
        self._fit_title_row()
        # The Browser tab shows a bar about it too (each start, until "Don't
        # remind me" for that version).
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if hasattr(page, "show_update_notice"):
                page.show_update_notice(release)

    def open_app_update(self, rollback_to=None):
        """The update panel: the latest version, or `rollback_to` to go back."""
        from .dialogs.app_update_dialog import AppUpdateDialog
        dlg = AppUpdateDialog(self, rollback_to=rollback_to)
        dlg.exec()

    # ------------------------------------------------------------- theme ---
    def _crossfade_theme(self, change):
        """Runs `change` (a theme switch) behind a still of the window as it
        was, which then fades -- night dissolves into pearl instead of the
        whole window flashing over in one frame."""
        page = self.tabs.currentWidget()
        if motion.reduced() or not self.isVisible() or getattr(page, "hosts_native_views", False):
            change()
            return
        still = self.grab()
        change()
        overlay = _Snapshot(self, still, QPoint(0, 0))
        motion.tween(overlay, 1.0, 0.0, motion.SLOW + 80, overlay.set_opacity, overlay.deleteLater)

    def toggle_theme(self):
        def change():
            self.dark_mode = not self.dark_mode
            self._apply_look()
            self._persist("theme", "dark" if self.dark_mode else "light")
            self._retheme_tabs()
        self._crossfade_theme(change)

    def apply_settings(self, changed):
        """Called by the Settings panel with the keys it changed."""
        if "theme" in changed:
            dark = self._setting("theme", "dark") != "light"
            if dark != self.dark_mode:
                def change():
                    self.dark_mode = dark
                    self._apply_look()
                    self._retheme_tabs()
                self._crossfade_theme(change)
        if "palette" in changed and palettes.current() != self._wanted_palette():
            def change():
                palettes.set_current(self._wanted_palette())
                self._apply_look()
                self._retheme_tabs()
            self._crossfade_theme(change)
        if "backdrop" in changed:
            self.set_backdrop_mode(self._setting("backdrop", cinema.CINEMATIC))
        if "reduce_motion" in changed:
            self._island.reduce_motion = bool(self._setting("reduce_motion", False))
            progress_widgets.set_reduce_motion(self._island.reduce_motion)
            motion.set_reduce_motion(self._island.reduce_motion)
        if "check_app_updates" in changed and self._setting("check_app_updates", True):
            self.start_update_check()
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if hasattr(page, "settings_changed"):
                page.settings_changed()

    def _wanted_palette(self):
        return getattr(self, "_palette_override", None) or self._setting("palette", palettes.DEFAULT)

    def set_palette_override(self, name):
        """Puts the window in another palette for a while -- a private tab
        showing turns everything black and white (palettes.MONO) -- without
        touching the one chosen in Settings, which comes back with None."""
        self._palette_override = name
        want = self._wanted_palette()
        if palettes.current() == want:
            return

        def change():
            palettes.set_current(want)
            self._apply_look()
            self._retheme_tabs()
        self._crossfade_theme(change)

    def set_backdrop_mode(self, mode):
        if mode not in cinema.MODES or mode == self._backdrop_mode:
            return
        leaving_desktop = self._backdrop_mode == cinema.DESKTOP
        self._backdrop_mode = mode
        if mode == cinema.DESKTOP:
            self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        elif leaving_desktop:
            mica.remove_frosted_glass(self)
            self._effect = None
        self._central.set_look(mode=mode)
        self._apply_window_border()
        self.update()

    def _apply_look(self):
        """Re-applies everything that follows the theme."""
        if self._backdrop_mode == cinema.DESKTOP:
            # Tint only: replaying the full DWM setup while the Browser tab's
            # Chromium surface is alive knocked the two compositors out of
            # step and froze the window (see mica.retint_frosted_glass).
            self._effect = retint_frosted_glass(self, dark_mode=self.dark_mode)
        self.setStyleSheet(theme.build_stylesheet(dark_mode=self.dark_mode))
        self._central.set_look(dark=self.dark_mode)
        self._restyle_chrome()
        self._apply_window_border()
        self.repaint()

    def _restyle_chrome(self):
        t = theme.tokens(dark_mode=self.dark_mode)
        self._wordmark.set_colors(t["text"], t["brand"], self.dark_mode,
                                  palettes.spec(self.dark_mode)["glows"][1][0])
        muted = t["text_muted"]
        self.settings_btn.setIcon(_settings_glyph_icon(muted))
        self.theme_btn.setIcon(_theme_glyph_icon(muted, moon=self.dark_mode, size=16))
        self.update_btn.setIcon(_updates_glyph_icon(muted))
        self.about_btn.setIcon(_about_glyph_icon(muted))
        from .browser_chrome import icon as line_icon
        self.donate_btn.setIcon(line_icon("coffee", muted, 16))
        self.fullscreen_btn.setIcon(line_icon("shrink" if getattr(self, "_app_fullscreen", False) else "expand",
                                              muted, 16))
        tip = "Switch to light theme" if self.dark_mode else "Switch to dark theme"
        self.theme_btn.setToolTip(tip)
        self.theme_btn.setAccessibleName(tip)
        for w in (self._island, self._action_tray, self._donate_capsule, self._fullscreen_capsule, self.update_pill,
                  *self._tab_buttons):
            w.update()
        self.titleBar.refresh_caption(getattr(self, "_nc_hover_btn", None))

    def _apply_window_border(self):
        """The 1px edge Windows 11 draws around a window, set to a tone of
        the backdrop instead of the system accent -- a bright system-blue
        rim around a dark cinematic window reads as a highlight on the wrong
        thing. DWMWA_BORDER_COLOR (34); ignored where unsupported."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            if self.isMaximized() or self.isFullScreen() or getattr(self, "_snapped", False):
                # No edge at all: an explicit colour is drawn even on a
                # maximized window, which put a 1px line around the whole
                # screen (caught by tests/test_maximized_corners.py). A
                # snapped window lies flush with the screen edges the same way.
                value = 0xFFFFFFFE   # DWMWA_COLOR_NONE
            elif self._backdrop_mode == cinema.DESKTOP:
                value = 0xFFFFFFFF   # DWMWA_COLOR_DEFAULT
            else:
                # A tone of the palette's field (Sapphire: a lit navy at night,
                # the pearl field's rim by day). COLORREF is 0x00BBGGRR.
                r, g, b = palettes.window_border(self.dark_mode)
                value = (b << 16) | (g << 8) | r
            color = ctypes.c_uint(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 34, ctypes.byref(color), ctypes.sizeof(color))
        except Exception:
            pass

    def _retheme_tabs(self):
        """Widgets that paint themselves don't pick up a new theme from
        setStyleSheet(); each tab that has any exposes apply_theme()."""
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if hasattr(page, "apply_theme"):
                page.apply_theme()

    def _persist(self, key, value):
        if self.settings is None:
            return
        try:
            self.settings[key] = value
            settings_store.save_settings(self.settings)
        except Exception:
            # Not worth taking the window down for -- it applied for this session.
            logger.exception("Failed to persist %s", key)

    # Kept for callers from before the Settings panel existed.

    # --------------------------------------------------- window states ---
    def toggle_app_fullscreen(self):
        self.set_app_fullscreen(not getattr(self, "_app_fullscreen", False))

    def set_app_fullscreen(self, on):
        """F11, or the full-screen button: the whole app fills the screen,
        borderless. Its title bar steps away; touching the screen's top edge
        brings it back (to switch tabs, or to leave), and Esc or F11 leaves."""
        on = bool(on)
        if on == getattr(self, "_app_fullscreen", False) or getattr(self, "_video_fullscreen", False):
            return
        self._app_fullscreen = on
        if on:
            self._pre_fullscreen_maximized = self.isMaximized()
            self.titleBar.hide()
            self.topbar.hide()
            self._apply_page_margins(self.tabs.currentIndex())
            self.showFullScreen()
            self._edge_timer.start()
        else:
            self._edge_timer.stop()
            self._edge_shown = False
            self.titleBar.show()
            self.topbar.show()
            self._apply_page_margins(self.tabs.currentIndex())
            if getattr(self, "_pre_fullscreen_maximized", False):
                self.showMaximized()
            else:
                self.showNormal()
            self._ensure_native_caption_buttons()
            QTimer.singleShot(80, self._on_dpi_or_scale_changed)
        tip = "Leave full screen (F11 or Esc)" if on else "Full screen (F11)"
        self.fullscreen_btn.setToolTip(tip)
        self.fullscreen_btn.setAccessibleName(tip)
        self._restyle_chrome()

    def leave_fullscreen(self):
        """Esc: out of whichever full screen the window is in."""
        if getattr(self, "_app_fullscreen", False):
            self.set_app_fullscreen(False)
        elif getattr(self, "_video_fullscreen", False):
            self.set_video_fullscreen(False)

    def _watch_top_edge(self):
        """In full screen, the title bar slides back over the page while the
        pointer is at the top edge (or on the bar), and steps away after."""
        if not getattr(self, "_app_fullscreen", False):
            return
        pos = self.mapFromGlobal(QCursor.pos())
        inside = 0 <= pos.x() < self.width()
        bar_h = self.titleBar.height()
        if not self._edge_shown and inside and 0 <= pos.y() <= 2:
            self._edge_shown = True
            self.titleBar.show()
            self.topbar.show()
            self.titleBar.raise_()
            self._edge_left_at = None
        elif self._edge_shown:
            if inside and 0 <= pos.y() <= bar_h + 24:
                self._edge_left_at = None
            elif self._edge_left_at is None:
                self._edge_left_at = time.monotonic()
            elif time.monotonic() - self._edge_left_at > 0.7:
                self._edge_shown = False
                self.titleBar.hide()
                self.topbar.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and (getattr(self, "_app_fullscreen", False)
                                                 or getattr(self, "_video_fullscreen", False)):
            self.leave_fullscreen()
            return
        super().keyPressEvent(event)

    def set_video_fullscreen(self, is_fullscreen):
        """Wired to BrowserTab.fullscreen_requested -- a page's own player
        asked to go fullscreen, which QtWebEngine grants only pixels for; the
        window has to hide its own chrome and fill the screen itself."""
        self._video_fullscreen = bool(is_fullscreen)
        self._apply_page_margins(self.tabs.currentIndex())
        if is_fullscreen:
            self._pre_fullscreen_maximized = self.isMaximized()
            self.titleBar.hide()
            self.topbar.hide()
            self.showFullScreen()
        else:
            self.titleBar.show()
            self.topbar.show()
            if getattr(self, "_pre_fullscreen_maximized", False):
                self.showMaximized()
            else:
                self.showNormal()
            # Leaving fullscreen is exactly the kind of drastic attribute
            # change known to clear the Snap Layout style bits and desync the
            # acrylic region; re-assert both.
            self._ensure_native_caption_buttons()
            QTimer.singleShot(80, self._on_dpi_or_scale_changed)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.ActivationChange, QEvent.WindowStateChange):
            bar = getattr(self, "titleBar", None)
            if isinstance(bar, _CleanTitleBar):
                bar.refresh_caption(getattr(self, "_nc_hover_btn", None))
        if event.type() == QEvent.WindowStateChange and getattr(self, "_central", None):
            # Maximize/restore flips what the desktop mode paints.
            self._central.update()
            # Corners follow the window state, as every Windows 11 window's
            # do: rounded while floating, square while maximized -- rounded
            # corners pressed into the screen's own corners show the desktop
            # through them.
            mica.set_rounded_corners(
                self, not (self.isMaximized() or self.isFullScreen() or self._snapped))
            self._apply_window_border()
            self._ensure_native_caption_buttons()
            QTimer.singleShot(80, self._on_dpi_or_scale_changed)
            self._schedule_snap_check()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._schedule_snap_check()

    def _schedule_snap_check(self):
        # Moves and resizes arrive while the frameless base class is still
        # constructing the window, before the timer exists.
        timer = getattr(self, "_snap_timer", None)
        if timer is not None:
            timer.start()

    def _update_snap_state(self):
        """Windows 11 squares a window's corners while it's snapped to the
        screen's edges, just as when it's maximized -- rounded corners pressed
        into a screen edge look like a mistake. Windows doesn't say whether a
        window is snapped, so this reads it off the geometry: a floating
        window lying flush against two or more edges of its screen's work
        area is treated as snapped."""
        snapped = False
        if self.isVisible() and not (self.isMaximized() or self.isFullScreen()):
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry()
                g = self.frameGeometry()
                tol = 2
                flush = sum((abs(g.left() - area.left()) <= tol,
                             abs(g.top() - area.top()) <= tol,
                             abs(g.right() - area.right()) <= tol,
                             abs(g.bottom() - area.bottom()) <= tol))
                snapped = flush >= 2
        if snapped == self._snapped:
            return
        self._snapped = snapped
        mica.set_rounded_corners(self, not (snapped or self.isMaximized() or self.isFullScreen()))
        self._apply_window_border()
        if getattr(self, "_central", None) is not None:
            self._central.update()

    def _ensure_native_caption_buttons(self):
        """Re-asserts WS_MINIMIZEBOX/WS_MAXIMIZEBOX if anything (Qt itself,
        qframelesswindow, a theme toggle re-running its own DWM setup)
        cleared them -- these bits used to get stripped for the opposite
        reason (see the __init__ comment), so a leftover codepath somewhere
        could still clear them; asserting rather than only setting once
        keeps that from silently regressing Snap Layout support again."""
        if sys.platform != "win32":
            return
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            wanted = style | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX
            if wanted != style:
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, wanted)
        except Exception:
            logger.exception("Failed to ensure native caption buttons")

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_native_caption_buttons()
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            mica.set_rounded_corners(self, not (self.isMaximized() or self.isFullScreen()))
            self._apply_window_border()
            self._set_logo_pixmap()
            self._fit_title_row()
            if self._backdrop_mode == cinema.DESKTOP:
                # DWM doesn't reliably start compositing the blur for a
                # window that wasn't mapped yet when the effect was applied;
                # two staggered re-applies once it is on screen catch it.
                QTimer.singleShot(60, self._kick_acrylic)
                QTimer.singleShot(400, self._kick_acrylic)
            # After the first frame, so the check never slows the launch.
            QTimer.singleShot(2500, self.start_update_check)

    def _kick_acrylic(self):
        if self._backdrop_mode != cinema.DESKTOP:
            return
        self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        self.repaint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_title_row()
        self._schedule_snap_check()

    def _fit_title_row(self):
        """Drops the wordmark, and then the update pill, when the row can't
        hold everything beside the caption chips. The logo always shows: in
        the wordmark, or on its own once the name has gone. Measured each time rather
        than set at a fixed window width, so it stays right whatever the
        labels, the font or the display scaling turn out to be."""
        row = getattr(self, "_topbar_layout", None)
        if row is None:
            return
        margins = row.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        spacing = row.spacing()
        fixed = [self._island, self._action_tray, self._donate_capsule]
        # The two stretches either side of the nav are layout items too, and
        # each one costs a spacing gap even at zero width.
        need = sum(w.sizeHint().width() for w in fixed) + spacing * (len(fixed) + 2 - 1)
        pill_w = (self.update_pill.sizeHint().width() + spacing) if self.update_pill.release else 0
        mark_w = self._wordmark.width() + spacing
        logo_w = self._logo_label.sizeHint().width() + spacing
        show_mark = need + pill_w + mark_w <= room
        show_pill = bool(self.update_pill.release) and need + pill_w + logo_w <= room
        if self._wordmark.isVisible() != show_mark:
            self._wordmark.setVisible(show_mark)
        if self._logo_label.isVisible() == show_mark:
            self._logo_label.setVisible(not show_mark)
        if self.update_pill.isVisible() != show_pill:
            self.update_pill.setVisible(show_pill)

    def nativeEvent(self, eventType, message):
        # Reapplying the ensure-call only in showEvent() was not enough
        # either -- confirmed by reproducing the exact reported sequence
        # (minimize, resize, toggle theme twice) and capturing the live
        # window with PrintWindow at each step: the style bits changed
        # underneath us at points other than just show. WM_STYLECHANGED
        # (0x7D) is Windows' own notification that GWL_STYLE was just
        # changed, by anything, for any reason -- hooking it here means the
        # bits get re-asserted immediately no matter what internal
        # qframelesswindow/DWM codepath touched them, present or future,
        # without needing to chase each call site individually.
        if sys.platform == "win32" and eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            import ctypes
            import win32con
            msg = ctypes.wintypes.MSG.from_address(int(message))

            if msg.message == win32con.WM_NCHITTEST:
                hit = self._hit_test_titlebar_buttons()
                if hit is not None:
                    return True, hit

            elif msg.message == win32con.WM_NCLBUTTONDOWN:
                # Confirmed directly: once DWM hosts the Snap affordance for
                # a reported HTMAXBUTTON region, it swallows the matching
                # WM_NCLBUTTONUP for itself and never forwards it here --
                # WM_NCLBUTTONDOWN, though, arrives reliably every time
                # (verified for both the maximize and the restore click).
                # Acting on down instead of up is the trade a real title
                # bar's maximize button doesn't have to make, but it's the
                # only one of the two this app actually receives.
                #
                # The actual action is deferred (QTimer, not called inline
                # here) -- confirmed directly this matters, not just
                # defensive caution: calling showMaximized()/showNormal()
                # synchronously from inside this handler left Qt's own
                # isMaximized() flipped correctly while the *real* HWND
                # never actually resized, because DWM is still mid-way
                # through its own tracking of this same button-down when our
                # handler runs. Posting the call to the event loop lets that
                # native sequence finish unwinding first.
                wparam = msg.wParam
                if wparam in self._NC_BUTTON_CODES:
                    QTimer.singleShot(0, lambda wp=wparam: self._handle_nc_button_activate(wp))
                    return True, 0

            elif msg.message == win32con.WM_NCMOUSEMOVE:
                self._update_nc_hover(msg.wParam)

            elif msg.message == 0x02A2:  # WM_NCMOUSELEAVE (not in win32con)
                self._clear_nc_hover()

            elif msg.message == 0x7D:  # WM_STYLECHANGED
                self._ensure_native_caption_buttons()

            elif msg.message == 0x02E0:  # WM_DPICHANGED
                # Deferred, not called inline: DWM/Qt are still mid-transition
                # at the moment this message arrives (the window hasn't been
                # resized to the new monitor's suggested rect yet), and
                # re-establishing the blur region against the *old* size is
                # exactly the kind of stale-vs-live mismatch that read as the
                # backdrop randomly flipping between glass and a flat solid
                # fill while scaling (reported directly). A short delay lets
                # the resize this message implies actually land first.
                QTimer.singleShot(80, self._on_dpi_or_scale_changed)

            elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
                # Fires once when an interactive drag-resize/drag-move
                # finishes -- the same stale-blur-region mismatch
                # WM_DPICHANGED above targets can also show up from a plain
                # same-DPI resize (DWM doesn't always keep the blur region
                # perfectly in sync with many rapid resizeEvents during a
                # live drag), so this covers "scaling" read as resizing the
                # window, not just an actual DPI/monitor-scale change.
                QTimer.singleShot(80, self._on_dpi_or_scale_changed)

        return super().nativeEvent(eventType, message)

    # ---------------------------------------------- Non-client hit-testing --
    # WM_NCHITTEST classifying a region as HTMAXBUTTON is the actual,
    # documented mechanism for a custom-drawn titlebar to keep real Windows
    # 11 Snap Layout support (the maximize-button hover flyout, and being
    # recognized as snap-eligible at all) -- DWM then draws its snap
    # affordance directly over *this* rect instead of painting its own
    # default button elsewhere, which is what caused the long-running
    # "ghosting" complaint when WS_MAXIMIZEBOX was simply left in place with
    # no hit-testing at all. Clicks and hover on a non-client-classified
    # region never reach Qt's normal widget event system, so both are
    # handled by hand below instead of relying on the buttons' own
    # clicked signal / QSS :hover rule.
    #
    # Minimize/close were briefly dropped from this classification (kept
    # only on maximize, the one with a real OS feature -- the snap-layout
    # hover flyout -- tied to it) on the theory that DWM's own native-glyph
    # painting was somehow specific to non-client classification. That
    # experiment reintroduced exactly the *original*, worse bug this
    # technique exists to prevent: WS_MINIMIZEBOX/WS_SYSMENU stay set
    # regardless (see _ensure_native_caption_buttons, needed for Snap
    # Layout at all), and without hit-testing telling DWM "the app has its
    # own custom button here," DWM falls back to painting its own default
    # system minimize/close glyphs separately, at its own standard
    # position -- confirmed directly via a clean screenshot (mouse away
    # from the buttons) showing a plain dash and X, offset from the
    # colored chips rather than merged into them. All three buttons are
    # non-client-classified again.
    def _titlebar_nc_buttons(self):
        import win32con
        tb = getattr(self, "titleBar", None)
        buttons = getattr(tb, "_buttons", None)
        if not buttons or len(buttons) < 3:
            return []
        min_btn, max_btn, close_btn = buttons
        return [
            (win32con.HTMINBUTTON, min_btn),
            (win32con.HTMAXBUTTON, max_btn),
            (win32con.HTCLOSE, close_btn),
        ]

    def _button_at_cursor(self):
        # QCursor.pos() is in Qt's own (device-independent) coordinates, the
        # same space mapToGlobal() answers in; win32api.GetCursorPos() is in
        # physical pixels and missed the buttons on a display scaled past 100%.
        pos = QCursor.pos()
        for ht_code, btn in self._titlebar_nc_buttons():
            top_left = btn.mapToGlobal(btn.rect().topLeft())
            rect = QRect(top_left, btn.size())
            if rect.contains(pos):
                return ht_code, btn
        return None, None

    def _hit_test_titlebar_buttons(self):
        # nativeEvent() can fire before __init__ has finished (base-class
        # construction sends real window messages already) -- titleBar may
        # not exist yet at that point, same reason _titlebar_nc_buttons()
        # already guards for it below.
        tb = getattr(self, "titleBar", None)
        if tb is None or not tb.isVisible():
            return None
        ht_code, _ = self._button_at_cursor()
        return ht_code

    def _handle_nc_button_activate(self, wparam):
        import win32con
        tb = getattr(self, "titleBar", None)
        if tb is None:
            return False
        if wparam == win32con.HTMINBUTTON:
            tb._on_min()
        elif wparam == win32con.HTMAXBUTTON:
            tb._on_max()
        elif wparam == win32con.HTCLOSE:
            tb._on_close()
        else:
            return False
        return True

    def _update_nc_hover(self, wparam):
        target = None
        for ht_code, btn in self._titlebar_nc_buttons():
            if ht_code == wparam:
                target = btn
                break
        current = getattr(self, "_nc_hover_btn", None)
        if target is current:
            return
        self._nc_hover_btn = target
        self.titleBar.refresh_caption(hot=target)

    def _clear_nc_hover(self):
        if getattr(self, "_nc_hover_btn", None) is not None:
            self._nc_hover_btn = None
            self.titleBar.refresh_caption()

    def _on_dpi_or_scale_changed(self):
        """Re-establishes the DWM blur-behind region after a DPI/monitor-
        scale change -- see the WM_DPICHANGED comment in nativeEvent() and
        mica.refresh_blur_region()'s own docstring for why this exists and
        why it's safe to call here (unlike a full apply_frosted_glass())."""
        if getattr(self, "_effect", None) is None or sys.platform != "win32":
            return
        try:
            self._effect = refresh_blur_region(self, dark_mode=self.dark_mode)
        except Exception:
            logger.exception("Failed to refresh blur-behind region after a DPI/scale change")
        if getattr(self, "_central", None) is not None:
            self._central.update()

    def focus_window(self):
        """Thread-safe: bring the window to the front. Called from
        single_instance.py's background listener thread."""
        self._focus_requested.emit()

    def _focus_window(self):
        # showNormal() first: activateWindow()/raise_() alone do nothing for
        # a window that's currently minimized to the taskbar, which is
        # exactly the state someone is in when they relaunch the app
        # expecting it to come back.
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _open_data_folder(self):
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        try:
            os.startfile(config.APPDATA_DIR)
        except Exception:
            logger.exception("Failed to open %s", config.APPDATA_DIR)


def _run_standalone():
    """python -m ui_qt.main_window -- launches the shell with placeholder
    tabs so the chrome can be checked on its own."""
    app = QApplication(sys.argv)
    win = MainWindow(dark_mode=True)
    for name in ("Video", "Torrent", "Images", "Browser", "Download", "History"):
        placeholder = QLabel(f"{name} placeholder")
        placeholder.setObjectName("muted")
        placeholder.setAlignment(Qt.AlignCenter)
        win.add_tab(placeholder, name)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(APP_ROOT))
    _run_standalone()
