"""PySide6 main window shell: frameless + frosted-glass (Mica/Acrylic)
backdrop, custom titlebar, tab container. Tabs are added by app/main.py (or
the smoke test below) via add_tab() once each ui_qt/*_tab.py module exists --
this module only owns the window chrome, not any feature logic.
"""
import os
import sys

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QTabWidget, QVBoxLayout, QWidget,
)
from qframelesswindow import FramelessMainWindow, StandardTitleBar
from qframelesswindow.utils import startSystemMove

from app.logging_setup import get_logger
from app.utils import settings as settings_store

from . import theme
from .dialogs.about_dialog import show_about
from .dialogs.update_dialog import show_update_dialog
from . import mica
from .mica import apply_frosted_glass, refresh_blur_region, retint_frosted_glass

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(APP_ROOT, "app_icon.ico")
# setWindowIcon() genuinely wants the .ico (it bundles the several small
# pre-rendered sizes Windows picks from for the taskbar/titlebar/Alt-Tab).
# The in-app topbar pixmap below is a different case -- a single small
# QLabel render, not an OS icon slot -- and scaling that down from the
# .ico let Qt pick a mismatched embedded size and read as blurry; the
# single clean 512x512 .png source stays sharp scaled down to any size.
ICON_PNG_PATH = os.path.join(APP_ROOT, "app_icon.png")

logger = get_logger("main_window")


class _TranslucentSurface(QWidget):
    """A plain QWidget never clears its own pixels before children paint --
    invisible on an opaque window (the next frame just overwrites the last
    one anyway) but not here: widgets with "background: transparent"
    (deliberate, so the Acrylic blur shows through) paint nothing at all, so
    stale ARGB pixels from whatever tab was showing before stay in the
    backing store, and the new tab's semi-transparent cards blend on TOP of
    them instead of replacing them -- visible as ghosting when switching
    QTabWidget pages. This subclass forces a real overwrite
    (CompositionMode_Source, not the default SourceOver blend) to
    (0,0,0,0) first, every paint.

    Scoped to just this one plain QWidget we fully own, not MainWindow
    itself -- qframelesswindow's frameless base class does its own native
    painting for the window border/shadow, and applying this same clear
    there fought with it (produced a solid white window instead of fixing
    anything).
    """

    # Opaque backdrop painted instead of transparency while maximized -- see
    # paintEvent. Set by MainWindow so it tracks the current theme.
    opaque_base = QColor(24, 24, 27)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        # DWM stops compositing the acrylic blur behind a *maximized*
        # window. The effect call still succeeds, so the window keeps
        # rendering its acrylic tint -- but that tint is deliberately only
        # ~40% alpha (it's meant to sit over a blur), and with the blur gone
        # it composites over the desktop instead. Result: the whole UI
        # washed out to pale grey with barely-readable text, which reads as
        # the app flipping to light mode on maximize and back on restore
        # (reported exactly that way). Painting a real opaque base in that
        # state keeps contrast correct; floating windows still get the full
        # transparent-clear and the glass effect.
        #
        # NOTE: real Acrylic blur-behind (mica.py) was restored here at the
        # user's own explicit request after previously being removed
        # entirely for reliability -- the ghosting/vanishing-background
        # bugs this class of issue produces are a known, likely risk of
        # that real blur, not a new regression if they resurface.
        window = self.window()
        if window is not None and (window.isMaximized() or window.isFullScreen()):
            painter.fillRect(self.rect(), self.opaque_base)
        else:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.end()
        super().paintEvent(event)


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


class _IslandFrame(QWidget):
    """The tray the nav pills sit in, painted directly.

    QSS could not be made to produce this shape: `QFrame#tabIsland` asked
    for a 24px radius and Qt kept drawing a lightly-rounded rectangle
    regardless, with or without WA_StyledBackground. Painting the rounded
    rect ourselves is both shorter and unambiguous.

    This used to be a full stadium capsule (radius = height/2). The
    Instrument language pulls it back to a fixed 10px tray with a hairline
    edge and a 1px inner light-catch along the top: a stadium reads soft
    and consumer, while a tray with a milled highlight reads as a control
    surface. The highlight is the same trick the caption marks use -- one
    faint line where light would actually land is the difference between
    "flat rectangle" and "machined part".
    """

    _RADIUS = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = QColor(18, 18, 20, 238)
        self._border = QColor(255, 255, 255, 20)

    def set_colors(self, bg, border):
        self._bg, self._border = QColor(bg), QColor(border)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        # Inner light-catch: a hairline arc across the top edge only, sitting
        # just inside the border so it reads as a bevel rather than a second
        # outline. Skipped on light themes, where a white highlight on a
        # near-white tray is invisible anyway.
        if self._bg.lightness() < 128:
            inner = rect.adjusted(1.0, 1.0, -1.0, -1.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
            path = QPainterPath()
            r = self._RADIUS - 1.0
            path.moveTo(inner.left(), inner.top() + r)
            path.arcTo(QRectF(inner.left(), inner.top(), r * 2, r * 2), 180, -90)
            path.lineTo(inner.right() - r, inner.top())
            path.arcTo(QRectF(inner.right() - r * 2, inner.top(), r * 2, r * 2), 90, -90)
            painter.drawPath(path)
        painter.end()


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


# ── Caption-mark invariants (both learned the hard way; do not "tidy") ──
#
# 1. THE MARK MUST STAY CENTRED. It is not decoration -- it is physically
#    covering the native minimize/maximize/close glyph DWM paints under it.
#    WM_NCHITTEST reporting a region as HTMINBUTTON/HTMAXBUTTON/HTCLOSE is
#    what buys real Windows 11 Snap Layout support, and the cost is that DWM
#    also draws its own dash/square/X there. Shifting the mark off-centre to
#    tighten the visual spacing was tried and immediately exposed those
#    glyphs again (reported with a clean screenshot, cursor away from the
#    buttons). offset_x exists only so that mistake stays greppable.
#
# 2. THE MARK MUST STAY OPAQUE AND BIG ENOUGH. A centred circle of diameter
#    D covers a centred WxW glyph only while D >= W*sqrt(2). The native
#    glyphs run about 10px, so 10*1.414 = 14.2px is the floor; _MARK_D below
#    sits comfortably past it. Anything translucent, or smaller than that,
#    lets the glyph read through.
_MARK_D = 17
# The title bar hosts the app's nav row (wordmark, tab island, the theme and
# Updates/About buttons) alongside the caption buttons, so it is sized for
# that content rather than for the 32px chips alone.
#
# 52, not 46: the nav island is 36px, so 46 left it 5px from the top edge of
# the window -- close enough to read as touching, and out of step with the
# 8px the content below the bar gets. 52 gives it the same 8px above and
# below, so the one number governs the vertical rhythm in both places.
_TITLEBAR_H = 36 + 2 * 8
# Horizontal room reserved at the right end of the hosted row so its last
# button cannot slide under the caption chips.
_CAPTION_CLEARANCE = 4          # mark diameter -- see invariant 2 before lowering
_CAPTION_BTN_W = 46   # matches Windows' own caption-button pitch
_CAPTION_BTN_H = 32


def _caption_mark_icon(color, hovered=False, divider=False,
                       btn_w=_CAPTION_BTN_W, btn_h=_CAPTION_BTN_H, offset_x=0):
    """One segment of the caption cluster, drawn onto a canvas the size of
    the whole button so the mark's position is controlled explicitly rather
    than left to Qt's icon centring.

    The three buttons sit at Windows' native 46px pitch, which is wider
    apart than macOS traffic lights -- and that pitch cannot be closed up,
    because closing it means moving the mark off the glyph it is covering
    (invariant 1 above). So rather than fight it, the cluster leans into
    it: a hairline divider between segments turns the spacing into
    deliberate instrument-panel rhythm instead of three dots that drifted
    apart. Hover lifts a soft ring around the mark rather than filling the
    whole segment, which keeps the chrome monochrome and quiet."""
    pixmap = QPixmap(btn_w, btn_h)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    cx = btn_w / 2.0 + offset_x
    cy = btn_h / 2.0

    # Hairline segment divider -- the machined-cluster cue.
    if divider:
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(0, int(cy - 6), 0, int(cy + 6))

    # Hover ring: a halo outside the mark, so the mark itself -- and its
    # glyph coverage -- is never redrawn smaller or moved.
    if hovered:
        painter.setPen(Qt.PenStyle.NoPen)
        c = QColor(color)
        c.setAlpha(46)
        painter.setBrush(c)
        r = _MARK_D / 2.0 + 4.5
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    r = _MARK_D / 2.0
    painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

    # Top inner highlight -- the single detail that reads as machined
    # rather than flat: a faint light-catch across the mark's upper arc.
    gloss = QColor(255, 255, 255, 38 if not hovered else 54)
    painter.setPen(QPen(gloss, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(cx - r + 0.6, cy - r + 0.6, r * 2 - 1.2, r * 2 - 1.2),
                    35 * 16, 110 * 16)

    painter.end()
    return QIcon(pixmap)


class _CleanTitleBar(StandardTitleBar):
    # Breathing room between the close chip and the window's right edge.
    _CAPTION_GAP = 6

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

    # Transparent button background now -- the visible color chip is a
    # small icon (_caption_mark_icon), not a QSS fill covering the whole
    # button. The button itself is sized to Windows' real native
    # caption-button footprint (see __init__ below), which needs to stay
    # visually invisible outside the small chip rather than becoming one
    # big colored block at that size.
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

        # macOS traffic-light colors in Windows order (minimize, maximize,
        # close, left to right) -- the colors were asked for, the order is
        # left alone so the buttons stay where Windows users reach for them.
        self._buttons = []
        # offset_x = 0 for all three now -- a nonzero offset here was
        # tried once to cluster the visible dots closer together, but
        # turned out to be load-bearing: centered was quietly covering
        # each button's native dash/square/X glyph (still painted
        # underneath by the library/DWM at that same centered position),
        # and shifting the dot moved it off that glyph rather than moving
        # the glyph. Confirmed directly: the ghosting only appeared once
        # the offset was introduced. Kept at 0 -- the chip spacing looking
        # slightly wide is a much smaller problem than the glyphs showing
        # through.
        for i, (color, hover, pressed, tip, slot) in enumerate((
            ("#27C93F", "#4EE063", "#1FA833", "Minimize", self._on_min),
            ("#FFBD2E", "#FFD268", "#D99F1F", "Maximize", self._on_max),
            ("#FF5F57", "#FF8A84", "#D94A43", "Close", self._on_close),
        )):
            # Plain QPushButton for all three -- all non-client-classified
            # again (see _titlebar_nc_buttons's docstring), so real Qt mouse
            # enter/leave events never reach any of them; hover is driven
            # by hand from WM_NCMOUSEMOVE for all three (_update_nc_hover).
            b = QPushButton(self)
            b.setFixedSize(_CAPTION_BTN_W, _CAPTION_BTN_H)
            b.setIconSize(QSize(_CAPTION_BTN_W, _CAPTION_BTN_H))
            b.setCursor(Qt.ArrowCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            # Stashed as plain attributes (not just baked into one
            # stylesheet with a QSS :hover rule) -- MainWindow's WM_NCHITTEST
            # handling reports these buttons as real non-client hit-test
            # codes (HTMINBUTTON/HTMAXBUTTON/HTCLOSE, for real Snap Layout
            # support), and mouse messages classified non-client never reach
            # Qt's own client-area hover tracking, so the QSS :hover
            # pseudo-state (and a plain QIcon set once) can never update on
            # their own. MainWindow swaps the icon by hand instead, from its
            # own WM_NCMOUSEMOVE handling.
            divider = i > 0  # hairline between segments, not before the first
            b._normal_icon = _caption_mark_icon(color, hovered=False, divider=divider)
            b._hover_icon = _caption_mark_icon(hover, hovered=True, divider=divider)
            b.setIcon(b._normal_icon)
            b.setStyleSheet(self._BTN_QSS)
            b.clicked.connect(slot)
            self._buttons.append(b)
            # 3. THE MARK MUST STAY AT THE TOP OF THE WINDOW.
            #
            # These are NOT in the layout, and that is deliberate. DWM paints
            # its own minimise/maximise/close glyphs in the system caption
            # area, which is anchored to the top edge of the window -- it
            # neither knows nor cares how tall this widget is. While the bar
            # was 32px the layout happened to put the chips on top of those
            # glyphs; once it grew to 52px to host the nav row, the layout
            # centred them 10px lower, the glyphs stayed where they were, and
            # the dash and X showed through from underneath (reported with a
            # screenshot, again). Qt::AlignTop was tried first and still left
            # them 8px down, because the layout's own content rect is not the
            # top of the window either. Positioned by hand in resizeEvent,
            # they sit at y=0 whatever the bar's height becomes.
            b.setParent(self)
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

    def caption_width(self):
        """How much room at the right end of the row the chips occupy, so the
        hosted nav row can keep its last button clear of them."""
        return sum(b.width() for b in self._buttons) + self._CAPTION_GAP

    def _layout_caption_buttons(self):
        x = self.width() - self._CAPTION_GAP
        for btn in reversed(self._buttons):
            x -= btn.width()
            btn.move(x, 0)
            btn.raise_()

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


class _TabPill(QPushButton):
    """A nav pill that paints its own rounded rect.

    Qt draws a QSS `border-radius` through its raster path without
    antialiasing the corner, so the selected pill's edges came out visibly
    stair-stepped next to the island behind it -- which *is* antialiased,
    because _IslandFrame paints itself. Two rounded rectangles, 3px apart,
    with only one of them smooth: that difference is what reads as
    "pixelated edges".

    This is the same conclusion _IslandFrame reached and for the same reason;
    the shape is painted here with QPainter's antialiasing on, and QSS is
    left to carry only the text colour and weight.
    """

    _RADIUS = 8.0

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._bg = None
        self._border = None

    def set_surface(self, bg, border):
        self._bg = QColor(bg) if bg is not None else None
        self._border = QColor(border) if border is not None else None
        self.update()

    def paintEvent(self, event):
        if self._bg is not None and (self.isChecked() or self.underMouse()):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setBrush(self._bg)
            if self._border is not None and self.isChecked():
                pen = QPen(self._border)
                pen.setWidthF(1.0)
                painter.setPen(pen)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)
            painter.end()
        # The label itself still goes through the style engine, so the QSS
        # colour/weight rules for #tabPill:checked keep applying.
        super().paintEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class MainWindow(FramelessMainWindow):
    # single_instance.py's listener calls focus_window() from a background
    # socket thread when a second launch forwards a bare focus request.
    # Touching window state off the GUI thread isn't safe, so that call just
    # emits this; Qt auto-queues delivery to _focus_window on the GUI thread.
    # Same pattern TorrentTab uses for forwarded magnet links.
    _focus_requested = Signal()

    def __init__(self, dark_mode=True, settings=None):
        super().__init__()
        self._focus_requested.connect(self._focus_window)
        self.dark_mode = dark_mode
        # Kept so toggle_theme() can write the choice back -- the theme was
        # read from settings.json at startup but never saved, so switching to
        # light mode silently reverted to dark on the next launch.
        self.settings = settings

        self.setWindowTitle("AWESOME DOWNLOADER")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        # 1000x680 is the size this was designed at, but it is a wish, not a
        # promise: on a 1366x768 laptop -- or any display running at 125%/150%
        # scaling, where the usable area shrinks by the same factor -- a window
        # that size is taller than the screen minus the taskbar, and opens with
        # its lower edge (and the Start all button on it) off the bottom. The
        # preferred size is clamped to what the screen actually offers and the
        # window is centred in it.
        preferred = QSize(1000, 680)
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            preferred.setWidth(min(preferred.width(), avail.width() - 40))
            preferred.setHeight(min(preferred.height(), avail.height() - 40))
        # Below this the tabs stop being usable rather than merely cramped;
        # every tab scrolls its own content, so this is a floor, not a target.
        self.setMinimumSize(760, 480)
        self.resize(preferred)
        if screen is not None:
            geo = self.frameGeometry()
            geo.moveCenter(screen.availableGeometry().center())
            self.move(geo.topLeft())

        self.setTitleBar(_CleanTitleBar(self))
        # Tall enough to hold the nav row it now hosts, rather than the bare
        # caption-button height it needed when it held only those.
        self.titleBar.setFixedHeight(_TITLEBAR_H)
        # NOTE: this used to also recolor self.titleBar.minBtn/maxBtn/
        # closeBtn -- qframelesswindow's own native-shaped buttons (a plain
        # dash, a restore-style square, an SVG X, each drawn by that
        # library's own paintEvent) -- green/yellow/red via their public
        # setNormalColor()/setHoverColor() API. That was leftover from
        # before _CleanTitleBar existed: it now fully hides and reparents
        # those exact same widgets to None (see _CleanTitleBar.__init__),
        # so recoloring them served no visible purpose any more -- and
        # very likely explains a "classic minimize/maximize/close symbols
        # visible behind the colored buttons" report that survived two
        # completely different attempts to fix it (widening the custom
        # buttons to Windows' own caption-button size, then dropping
        # non-client hit-test classification for minimize/close): calling
        # a public setter on a hidden widget still calls that widget's own
        # update()/repaint() internally, and on a WA_TranslucentBackground
        # window with WA_DontShowOnScreen set, that turned out not to be a
        # perfectly reliable no-op. Removed outright rather than guessing
        # at a workaround -- _CleanTitleBar's own chip icons already own
        # 100% of the visible coloring now.

        # Required for the Mica/Acrylic backdrop to actually show through:
        # without this, Qt's raster backing store on Windows has no alpha
        # channel, so it always blits an opaque fill over whatever DWM
        # composited behind the window -- the effect call succeeds silently
        # but nothing behind the glass is ever visible. (macOS's own
        # vibrancy path in this same library sets this same attribute.)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # WS_MINIMIZEBOX / WS_MAXIMIZEBOX used to get stripped here to stop
        # DWM's own non-client rendering from ghosting a native dash/square/X
        # on top of the colored chips (confirmed via PrintWindow capture,
        # bypassing Qt/DWM compositing entirely) -- but that same style bit
        # is *also* exactly what tells Windows 11 this window supports Snap
        # Layouts at all, so stripping it silently killed drag-to-edge
        # snapping and the maximize-button hover flyout along with the
        # ghost buttons (reported directly: "doesn't snap like Windows 11").
        # The real fix keeps the bits and instead tells DWM, via
        # WM_NCHITTEST, that *these* colored chips are the window's real
        # min/max/close buttons (HTMINBUTTON/HTMAXBUTTON/HTCLOSE) -- once
        # DWM knows that, it draws its Snap Layout affordance and hover
        # flyout directly over the app's own button instead of painting a
        # separate default one elsewhere, which is what caused the ghosting
        # in the first place. See nativeEvent() for the hit-testing and
        # _handle_nc_button_activate()/_update_nc_hover() for the click and
        # hover handling that non-client-classified mouse messages need done
        # by hand (they never reach Qt's normal button/event machinery).
        self._nc_hover_btn = None
        if sys.platform == "win32":
            import win32con
            self._NC_BUTTON_CODES = {win32con.HTMINBUTTON, win32con.HTMAXBUTTON, win32con.HTCLOSE}
        else:
            self._NC_BUTTON_CODES = set()
        self._ensure_native_caption_buttons()

        central = _TranslucentSurface(self)
        central.setObjectName("centralSurface")
        self._central = central
        self._apply_opaque_base()
        layout = QVBoxLayout(central)
        # Leave room at the top for the custom titlebar (it's an overlay,
        # not part of layout flow, in qframelesswindow's model). Gap kept
        # tight (+2, not the +8 this had before) -- reported directly as
        # too much dead space between the OS title bar row and the app
        # name/icon row below it ("shift my app name to top").
        layout.setContentsMargins(*self._PADDED_MARGINS)
        self._content_layout = layout
        self._full_bleed_pages = set()
        layout.setSpacing(4)

        # Everything above the tab content -- icon, app name, the tab pills,
        # theme toggle, Updates, About -- lives in one single row now,
        # rather than a title row stacked above a separate centered tab
        # row. Reported directly as too much dead vertical space between
        # them; folding both into one line gets that height back for
        # actual tab content instead. Lives in `central` (code this module
        # fully owns) rather than in the library's own titlebar layout --
        # inserting widgets there corrupted the window-control buttons.
        topbar = _DragRow()
        self.topbar = topbar
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(6, 2, 6, 2)
        topbar_layout.setSpacing(8)
        icon_label = QLabel()
        self._logo_label = icon_label
        if os.path.exists(ICON_PNG_PATH):
            icon_label.setPixmap(QPixmap(ICON_PNG_PATH).scaled(
                20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        topbar_layout.addWidget(icon_label)

        # Two-tone wordmark ("AWESOME" + "DOWNLOADER" in different colours)
        # -- two adjacent labels in their own zero-spacing row rather than
        # one label, so each half's colour is a plain QSS token that
        # updates cleanly on a live theme toggle (matches the same
        # treatment on the Browser tab's own home page).
        # Set in mono with wide tracking and a mid-dot separator rather than
        # a space: the wordmark is the app's own technical marking, and mono
        # is what makes it read as stamped onto the instrument instead of
        # typeset. Colours are applied in _update_title_colors() so a live
        # theme toggle reaches them.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        # 4px between the words, set here rather than with a leading space in
        # the second label. The space was being rendered at the wordmark's own
        # letter-spacing on top of its own width, which is why the two halves
        # drifted apart as the tracking was tuned -- a layout gap is one number
        # that means one thing.
        title_row.setSpacing(4)
        self.title_word1 = QLabel("AWESOME")
        self.title_word2 = QLabel("DOWNLOADER")
        title_row.addWidget(self.title_word1)
        title_row.addWidget(self.title_word2)
        topbar_layout.addLayout(title_row)

        self.tabs = QTabWidget(central)
        self.tabs.setDocumentMode(True)
        # Forces central's paintEvent (the transparent-clear above) to run
        # on every tab switch -- repaint() is synchronous and whole-rect,
        # unlike update(), which can coalesce/limit the redraw region.
        self.tabs.currentChanged.connect(lambda _i: central.repaint())

        # The tab strip is driven by a *standalone* QTabBar in its own
        # centered row rather than QTabWidget's built-in one, which is
        # hidden. Two rounds of trying to make the built-in bar look like a
        # floating "island" both failed for the same structural reason: the
        # built-in bar is laid out by QTabWidget itself, always spanning the
        # full widget width, so its rounded QSS background stretched
        # edge-to-edge no matter what. `QTabWidget::tab-bar { alignment:
        # center }` only centers the *tabs inside* that full-width bar, not
        # the bar; and capping the bar's maximumWidth to its sizeHint made
        # QTabBar fall back to its own scroll-arrow overflow UI and cut off
        # a tab entirely (reported directly, twice).
        #
        # A free-standing QTabBar between two stretches has neither problem:
        # it takes exactly its natural width, is genuinely centered by the
        # layout, and never needs an overflow mode. setUsesScrollButtons(
        # False) is belt-and-braces so the arrows can't come back.
        # Tab strip built from real QPushButtons rather than a styled
        # QTabBar. Qt's style engine does not honour `border-radius` on
        # QTabBar::tab the way it does on a button -- the QSS said 22px and
        # the tabs still rendered as near-square boxes (reported directly:
        # "not pebble or with round edges at all"). Buttons are drawn
        # through the ordinary QSS box model, so a radius of half their
        # height gives a real capsule every time, on any Qt style.
        self._tab_buttons = []
        self.tabs.tabBar().hide()
        # Anything that changes the page programmatically (app/main_qt.py's
        # history-refresh wiring reads self.tabs directly) keeps the island's
        # highlight in sync.
        self.tabs.currentChanged.connect(self._sync_island_selection)

        # The capsule background is drawn by this wrapper frame; the buttons
        # sit inside it with real layout margins, so a selected pill is
        # inset from the container's rounded border instead of clipping it.
        island = _IslandFrame()
        self._island = island
        self._island_layout = QHBoxLayout(island)
        self._island_layout.setContentsMargins(3, 3, 3, 3)
        self._island_layout.setSpacing(2)
        topbar_layout.addStretch(1)
        topbar_layout.addWidget(island)
        topbar_layout.addStretch(1)

        # Icon-only, all three. Carrying the words "Dark", "Updates" and
        # "About" cost about 150px of a row that also has to hold six tab
        # names -- and it was the tab names that lost, rendering as "Browse",
        # "ownloa", "-listory". These are chrome actions reached occasionally;
        # the tab strip is the thing being read constantly, so the space goes
        # there. Each keeps a tooltip and an accessible name.
        # The three sit in their own tray, the same painted surface the nav
        # island uses. Loose on the bar they read as three unrelated marks
        # floating between the island and the caption chips; in a tray they
        # read as one group of window actions, and the row gains a structure
        # (nav in the middle, actions at the end) instead of a drift of
        # icons. Same 10px radius and hairline, so it is the existing
        # vocabulary rather than a second treatment.
        self._action_tray = _IslandFrame()
        tray_layout = QHBoxLayout(self._action_tray)
        tray_layout.setContentsMargins(3, 3, 3, 3)
        tray_layout.setSpacing(2)

        self.theme_btn = QPushButton()
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedSize(self._ACTION_BTN, self._ACTION_BTN)
        self.theme_btn.setFocusPolicy(Qt.NoFocus)
        self.theme_btn.clicked.connect(self.toggle_theme)
        tray_layout.addWidget(self.theme_btn)

        self.update_btn = QPushButton()
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setFixedSize(self._ACTION_BTN, self._ACTION_BTN)
        self.update_btn.setFocusPolicy(Qt.NoFocus)
        self.update_btn.setToolTip("Check for updates")
        self.update_btn.setAccessibleName("Check for updates")
        self.update_btn.clicked.connect(
            lambda: show_update_dialog(self, dark_mode=self.dark_mode))
        tray_layout.addWidget(self.update_btn)

        self.about_btn = QPushButton()
        self.about_btn.setCursor(Qt.PointingHandCursor)
        self.about_btn.setFixedSize(self._ACTION_BTN, self._ACTION_BTN)
        self.about_btn.setFocusPolicy(Qt.NoFocus)
        self.about_btn.setToolTip("About Awesome Downloader")
        self.about_btn.setAccessibleName("About Awesome Downloader")
        self.about_btn.clicked.connect(lambda: show_about(self, dark_mode=self.dark_mode))
        tray_layout.addWidget(self.about_btn)
        topbar_layout.addWidget(self._action_tray)

        # Hosted in the title bar rather than added as a second row here.
        # Right margin clears the caption buttons, which are laid out after it
        # in that same row.
        # Left margin matches the 12px the content below the title bar has,
        # so the logo starts on the same vertical line as the cards under it
        # instead of being pinned to the window edge. The right margin is the
        # caption chips' own footprint plus a gap: they are positioned by hand
        # rather than laid out, so the row has to reserve their space itself.
        topbar_layout.setContentsMargins(
            12, 0, self.titleBar.caption_width() + _CAPTION_CLEARANCE, 0)
        self.titleBar.host(topbar)
        # Everything in the row that is decoration rather than a control gives
        # its mouse events back to the title bar, so the whole strip is a drag
        # handle again -- which is what makes snapping reachable.
        # Only the labels, which have no children of their own to hide. The
        # row itself handles the drag (see _DragRow) rather than giving its
        # events away, because a transparent container takes its whole
        # subtree out of hit testing along with it.
        _CleanTitleBar._pass_through_mouse(
            getattr(self, "title_word1", None), getattr(self, "title_word2", None),
            getattr(self, "_logo_label", None))
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)
        # Re-raise after setCentralWidget: QMainWindow's own internal
        # central-widget plumbing can silently restack sibling widgets when
        # it's set, which left the titlebar's buttons visually on top but
        # not actually receiving clicks (confirmed: min/max/close and the
        # theme toggle stopped responding once a real central widget with
        # real content was set, not just placeholders).
        self.titleBar.raise_()

        theme.load_custom_fonts()
        # QSS font-family on the top-level widget doesn't reliably cascade
        # to every nested child in Qt the way CSS does in a browser -- the
        # font looked unchanged despite loading correctly (confirmed: Inter
        # registers fine, see theme.load_custom_fonts()). Setting it at the
        # QApplication level makes it the real default for every widget
        # instead of hoping QSS inheritance carries it down.
        app = QApplication.instance()
        if app is not None:
            # Inter is a *variable* font -- loaded via addApplicationFont
            # with no weight specified, Qt/DirectWrite picks whatever that
            # variable font's own default named instance is, which isn't
            # guaranteed to land on a crisp "Regular" the way a static font
            # would. Reported directly as text reading grey/thin rather than
            # black/bold in light mode -- explicitly pinning the weight
            # avoids depending on the variable font's ambiguous default.
            #
            # setFamilies (a chain), not setFamily (a single name): Inter
            # covers Latin text beautifully but has no glyphs at all for the
            # emoji/symbol codepoints this UI uses as icons (⏸ 📂 🗑 🎬 🧲
            # ...). With a single family set as the *application* font, Qt
            # had nothing to fall back to and drew them as empty boxes --
            # reported directly, with a screenshot of three blank squares
            # where the torrent row's controls should be. Naming the Windows
            # emoji/symbol fonts explicitly after Inter means each character
            # resolves against the first family in the chain that actually
            # has it, so Latin still renders as Inter and the icons render
            # as real icons.
            font = QFont()
            font.setFamilies(["Inter", "Segoe UI Variable", "Segoe UI",
                               "Segoe UI Emoji", "Segoe UI Symbol"])
            font.setPointSize(10)
            font.setWeight(QFont.Weight.Medium)
            app.setFont(font)

        self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        self.setStyleSheet(theme.build_stylesheet(dark_mode=self.dark_mode))
        self._update_theme_btn_style()
        self._update_title_colors()

    _TAB_HEIGHT = 30

    def add_tab(self, widget, label):
        """Adds a page. The page goes into the (bar-less) QTabWidget and a
        pill button into the island -- indices stay in lockstep because both
        only ever grow through here."""
        index = self.tabs.count()
        self.tabs.addTab(widget, label)

        btn = _TabPill(label)
        btn.setObjectName("tabPill")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedHeight(self._TAB_HEIGHT)
        # QPushButton elides its label when it is laid out narrower than its
        # own sizeHint, which is how "Download" and "History" ended up as
        # "ownloa" and "-listory" once the row got tight. Pinning the minimum
        # to the natural width makes the pill refuse to be squeezed instead.
        btn.setMinimumWidth(btn.sizeHint().width())
        btn.clicked.connect(lambda _c=False, i=index: self.tabs.setCurrentIndex(i))
        # Tabs are added after the window is built, so the surface colours
        # applied during construction never reached them.
        t = theme.tokens(dark_mode=self.dark_mode)
        btn.set_surface(t["card_bg_solid"], t["card_border"])
        self._island_layout.addWidget(btn)
        self._tab_buttons.append(btn)
        self._sync_island_selection(self.tabs.currentIndex())

    # Normal tabs are cards on a background and want breathing room around
    # them. The Browser is not a card -- it is a web page, and every pixel of
    # margin around it is a strip of dead app chrome framing the site. So the
    # margins are a property of the current tab rather than of the window.
    _PADDED_MARGINS = (12, 0, 12, 6)
    _BLEED_MARGINS = (0, 0, 0, 0)
    _TOP_GAP = 8

    def set_full_bleed(self, widget, full_bleed=True):
        """Marks a page as one that should run edge to edge."""
        if full_bleed:
            self._full_bleed_pages.add(widget)
        else:
            self._full_bleed_pages.discard(widget)
        self._apply_page_margins(self.tabs.currentIndex())

    def _apply_page_margins(self, index):
        page = self.tabs.widget(index)
        left, _, right, bottom = (
            self._BLEED_MARGINS if page in self._full_bleed_pages else self._PADDED_MARGINS)
        top = self.titleBar.height() + (
            0 if page in self._full_bleed_pages else self._TOP_GAP)
        self._content_layout.setContentsMargins(left, top, right, bottom)

    def _sync_island_selection(self, index):
        self._apply_page_margins(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)
        # Switching into a tab is itself "I've seen what's in here now" --
        # clears whichever badge (if any) that tab was showing.
        self.set_tab_badge(index, False)

    def set_tab_badge(self, index, visible):
        """Small notification dot on a tab pill -- e.g. a download started
        in the Download tab while looking at a different one, which
        otherwise has zero visible indication anything happened at all."""
        if not (0 <= index < len(self._tab_buttons)):
            return
        btn = self._tab_buttons[index]
        badge = getattr(btn, "_notif_badge", None)
        if badge is None:
            badge = QLabel(btn)
            badge.setFixedSize(9, 9)
            badge.setStyleSheet(
                "background: #ff3b30; border-radius: 4px; border: 1.5px solid rgba(0,0,0,120);")
            btn._notif_badge = badge
        badge.move(max(0, btn.width() - 13), 3)
        badge.setVisible(visible)
        if visible:
            badge.raise_()

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        # retint_frosted_glass, not apply_frosted_glass, on every toggle
        # after the first -- see its docstring: replaying the full DWM
        # blur-behind + open-animation setup while the Browser tab's
        # QWebEngineView (a real native child window) is alive can knock
        # DWM's and Chromium's compositors out of sync, which showed up as
        # the whole window going visually "fixed" (frozen) right after a
        # theme toggle. Only the tint color actually needs to change here.
        self._effect = retint_frosted_glass(self, dark_mode=self.dark_mode)
        self.setStyleSheet(theme.build_stylesheet(dark_mode=self.dark_mode))
        self._update_theme_btn_style()
        self._update_title_colors()
        self._apply_opaque_base()
        # The Acrylic gradientColor DWM call above succeeds (confirmed:
        # card/text colors from the QSS reapply do switch correctly) but the
        # actual backdrop tint visually stayed stuck on whatever it was at
        # first launch -- reported directly: switching to light mode left
        # the tab bar strip (the one area with no opaque QSS fill over it,
        # so the raw Acrylic tint shows through unfiltered) still dark.
        # DWM doesn't always recomposite a translucent window's backdrop
        # just because SetWindowCompositionAttribute was called again; a
        # real forced repaint after the effect call is what actually gets
        # the new tint to show.
        self.repaint()
        self._persist_theme()
        self._retheme_tabs()

    def _retheme_tabs(self):
        """Hand-painted widgets (chips, progress bars) don't pick up a new
        theme from setStyleSheet() the way ordinary QSS-styled widgets do --
        each tab that has any exposes apply_theme() to re-run its own
        colour setup after a live toggle. Duck-typed rather than importing
        every tab class here, matching the hasattr(...) pattern
        app/main_qt.py already uses for TorrentTab.save_state.
        _persist_theme() runs first so self.settings["theme"] (the shared
        dict every tab reads its own _dark_mode() from) is already current
        by the time this fires."""
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if hasattr(page, "apply_theme"):
                page.apply_theme()

    def set_video_fullscreen(self, is_fullscreen):
        """Wired to BrowserTab.fullscreen_requested -- a page's own video
        player asked to go fullscreen (its own Fullscreen API request, e.g.
        clicking YouTube's fullscreen button), which QtWebEngine only grants
        pixels for; it does nothing about the app's own titlebar/tab island
        still eating screen space above it unless this window cooperates by
        hiding its own chrome and actually resizing to fill the screen."""
        if is_fullscreen:
            self._pre_fullscreen_maximized = self.isMaximized()
            self.titleBar.hide()
            self.topbar.hide()  # now holds the tab pills too, one merged row
            self.showFullScreen()
        else:
            self.titleBar.show()
            self.topbar.show()
            if getattr(self, "_pre_fullscreen_maximized", False):
                self.showMaximized()
            else:
                self.showNormal()
            # showFullScreen()/showNormal() is exactly the kind of drastic
            # window-attribute change already known to desync both the
            # Snap-Layout caption-button style bits and the Acrylic
            # blur-behind region (see _ensure_native_caption_buttons's and
            # _on_dpi_or_scale_changed's own docstrings for the same
            # fragility from DPI/resize changes) -- reported directly as
            # "window snapping stops working" and the transparency
            # ghosting bug recurring right after leaving fullscreen.
            # Re-run both explicitly rather than trusting showEvent's own
            # timing relative to Windows' own post-fullscreen style change.
            self._ensure_native_caption_buttons()
            QTimer.singleShot(80, self._on_dpi_or_scale_changed)

    def _apply_opaque_base(self):
        """Keeps the maximized-state backdrop and the hand-painted tab island
        in sync with the theme (neither gets its colours from the QSS).

        Light mode's value (205, 209, 215) must match widgets/card.py's
        _FULLSCREEN_OPAQUE_BASE_LIGHT -- (242, 243, 245), the old value,
        left every card invisible once maximized: a translucent white card
        blended over a backdrop that close to white measured 1.07:1
        contrast (confirmed directly, from a real screenshot -- no card
        edges visible anywhere in the window). A real light grey gives the
        cards' own opaque border something to actually stand out against."""
        if getattr(self, "_central", None) is not None:
            self._central.opaque_base = (
                QColor(24, 24, 27) if self.dark_mode else QColor(205, 209, 215))
        island_bg = QColor(30, 30, 32, 235) if self.dark_mode else QColor(255, 255, 255, 240)
        island_border = QColor(255, 255, 255, 28) if self.dark_mode else QColor(0, 0, 0, 18)
        for tray in (getattr(self, "_island", None), getattr(self, "_action_tray", None)):
            if tray is not None:
                tray.set_colors(island_bg, island_border)
        t = theme.tokens(dark_mode=self.dark_mode)
        for btn in getattr(self, "_tab_buttons", []):
            btn.set_surface(t["card_bg_solid"], t["card_border"])

    def changeEvent(self, event):
        super().changeEvent(event)
        # Maximize/restore flips which branch _TranslucentSurface.paintEvent
        # takes, and Qt doesn't repaint the central widget on a window-state
        # change by itself -- without this the washed-out fill persisted
        # until something else happened to dirty the widget.
        if event.type() == QEvent.WindowStateChange and getattr(self, "_central", None):
            self._central.update()
            # Corners follow the window state, the way every other Windows 11
            # window's do: rounded while floating, square while maximized or
            # fullscreen. Left permanently rounded, a maximized window keeps
            # cut-out corners pressed into the screen's own corners, with the
            # desktop visible through them.
            mica.set_rounded_corners(
                self, not (self.isMaximized() or self.isFullScreen()))
            # Every window-state transition (minimize/restore, maximize/
            # restore, fullscreen enter/exit -- either direction) is
            # exactly the class of drastic window-attribute change already
            # known to desync both the Snap-Layout caption-button style
            # bits and the Acrylic blur-behind region. Previously only
            # re-asserted after leaving fullscreen specifically; reported
            # directly that scaling/snapping and the transparency ghosting
            # break "after one point" more generally, not only there --
            # doing this on every state change, not just that one path,
            # is what actually closes it off.
            self._ensure_native_caption_buttons()
            QTimer.singleShot(80, self._on_dpi_or_scale_changed)

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
        # Acrylic applied in __init__ happens before the window is actually
        # mapped to the screen -- DWM doesn't reliably start compositing the
        # blur-behind surface for a window that isn't visible yet, which
        # showed up as a real, reported bug: the backdrop stays fully
        # invisible (flat, opaque-looking) right after launch until
        # something -- any theme toggle -- forces a re-application. A
        # one-time, delayed re-apply right after the window is genuinely on
        # screen is that same forced re-application, just automatic instead
        # of needing a manual click. Guarded so it only runs once -- later
        # showEvents (restore from minimize, etc.) don't need it and
        # re-running it there would just be a needless visual flicker.
        if not getattr(self, "_acrylic_kicked", False):
            self._acrylic_kicked = True
            # Two staggered attempts, not one -- reported directly as still
            # ghosting/invisible at launch on some runs, meaning a single
            # 60ms delay isn't reliably late enough for DWM to have started
            # compositing yet on every machine. A second, later kick costs
            # nothing (frosted-glass reapplication isn't visually
            # disruptive) and catches the cases the first one missed.
            QTimer.singleShot(60, self._kick_acrylic)
            QTimer.singleShot(400, self._kick_acrylic)

    def _kick_acrylic(self):
        self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        self.repaint()

    # Width below which the wordmark is dropped from the merged top row.
    # Measured, not guessed: the row needs ~745px at its natural size and the
    # caption cluster another 138, so a window narrower than this cannot show
    # both and would clip the About button under the close chip. Dropping the
    # text (the logo mark stays) frees ~150px, which is what lets the window
    # minimum stay small enough to snap to half of a 1366-wide screen -- a
    # large minimum width silently breaks snapping, since Windows cannot give
    # a snapped window less than it asks for.
    _WORDMARK_MIN_W = 980

    def resizeEvent(self, event):
        super().resizeEvent(event)
        show_wordmark = self.width() >= self._WORDMARK_MIN_W
        for label in (getattr(self, "title_word1", None), getattr(self, "title_word2", None)):
            if label is not None and label.isVisible() != show_wordmark:
                label.setVisible(show_wordmark)

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
        import win32api
        cursor = win32api.GetCursorPos()
        pos = QPoint(cursor[0], cursor[1])
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
        if current is not None:
            current.setIcon(current._normal_icon)
        if target is not None:
            target.setIcon(target._hover_icon)
        self._nc_hover_btn = target

    def _clear_nc_hover(self):
        current = getattr(self, "_nc_hover_btn", None)
        if current is not None:
            current.setIcon(current._normal_icon)
            self._nc_hover_btn = None

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

    def _persist_theme(self):
        if self.settings is None:
            return
        try:
            self.settings["theme"] = "dark" if self.dark_mode else "light"
            settings_store.save_settings(self.settings)
        except Exception:
            # A settings file that can't be written is not worth taking the
            # window down for -- the theme still applied for this session.
            logger.exception("Failed to persist theme preference")

    def _update_title_colors(self):
        t = theme.tokens(dark_mode=self.dark_mode)
        # One name, so no separator between the words -- an interpunct made
        # it read as two things. Bold, because a wordmark is the one place
        # in this chrome that is allowed to assert itself.
        # 13px, up from 11: at 11 the app's own name was smaller than the tab
        # labels beside it, which read as the wordmark apologising for itself.
        # Tracking eases back as the size goes up -- letter-spacing that suits
        # 11px mono is too loose once the glyphs are bigger.
        base = (f"font-family: {theme.MONO_STACK}; font-weight: 700; "
                f"font-size: 13px; letter-spacing: 0.8px;")
        self.title_word1.setStyleSheet(f"{base} color: {t['text']};")
        # The wordmark is identity, not action -- it wears the brand blue,
        # never the orange action colour. See theme.DARK's palette comment.
        self.title_word2.setStyleSheet(f"{base} color: {t['brand']};")

    _ACTION_BTN = 30

    # Inside the tray, so the buttons themselves carry no border of their own
    # -- a bordered button inside a bordered tray is two edges 3px apart, and
    # reads as a mistake. The hover is a filled rounded square matching the
    # nav pills' 8px, not a circle: circles beside the capsule pills made the
    # two groups look like they came from different designs.
    _ACTION_QSS = """
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:pressed {{
                background: {pressed};
            }}
    """

    def _update_theme_btn_style(self):
        t = theme.tokens(dark_mode=self.dark_mode)
        qss = self._ACTION_QSS.format(hover=t["hover_overlay"],
                                      pressed=t["pressed_overlay"])
        for btn, icon in (
            (self.theme_btn, _theme_glyph_icon(t["text"], moon=self.dark_mode)),
            (getattr(self, "update_btn", None), _updates_glyph_icon(t["text"])),
            (getattr(self, "about_btn", None), _about_glyph_icon(t["text"])),
        ):
            if btn is None:
                continue
            btn.setIcon(icon)
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(qss)
        self.theme_btn.setToolTip(
            "Switch to light theme" if self.dark_mode else "Switch to dark theme")
        self.theme_btn.setAccessibleName(self.theme_btn.toolTip())
        return

    def _update_theme_btn_style_old(self):
        t = theme.tokens(dark_mode=self.dark_mode)
        # Drawn icon, not an emoji. The emoji here rendered in the OS colour
        # font, so it ignored the theme entirely and was the one object in
        # the chrome that couldn't be restyled -- exactly the "no Unicode
        # glyphs as icons" rule the rest of this app already follows.
        self.theme_btn.setText("Dark" if self.dark_mode else "Light")
        self.theme_btn.setIcon(_theme_glyph_icon(t["text_muted"], moon=self.dark_mode))
        self.theme_btn.setIconSize(QSize(14, 14))
        self.theme_btn.setStyleSheet(f"""
            QPushButton {{
                background: {t['hover_overlay']};
                border: 1px solid {t['card_border']};
                border-radius: 13px;
                padding: 4px 14px 4px 10px;
                color: {t['text']};
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {t['pressed_overlay']};
            }}
        """)


def _run_standalone():
    """python -m ui_qt.main_window -- smoke test: launches the shell with a
    couple of placeholder tabs so the window chrome/theme can be checked
    without needing every real tab ported yet."""
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication(sys.argv)
    win = MainWindow(dark_mode=True)

    placeholder = QLabel("Video tab placeholder -- ported next.")
    placeholder.setObjectName("muted")
    placeholder.setAlignment(Qt.AlignCenter)
    win.add_tab(placeholder, "Video")

    placeholder2 = QLabel("Downloads tab placeholder -- Phase 3.")
    placeholder2.setObjectName("muted")
    placeholder2.setAlignment(Qt.AlignCenter)
    win.add_tab(placeholder2, "Downloads")

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(APP_ROOT))
    _run_standalone()
