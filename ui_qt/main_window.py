"""PySide6 main window shell: frameless + frosted-glass (Mica/Acrylic)
backdrop, custom titlebar, tab container. Tabs are added by app/main.py (or
the smoke test below) via add_tab() once each ui_qt/*_tab.py module exists --
this module only owns the window chrome, not any feature logic.
"""
import os
import sys

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)
from qframelesswindow import FramelessMainWindow, StandardTitleBar

from app.logging_setup import get_logger
from app.utils import settings as settings_store

from . import theme
from .dialogs.about_dialog import show_about
from .dialogs.update_dialog import show_update_dialog
from .mica import apply_frosted_glass

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(APP_ROOT, "app_icon.ico")

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
        window = self.window()
        if window is not None and (window.isMaximized() or window.isFullScreen()):
            painter.fillRect(self.rect(), self.opaque_base)
        else:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.end()
        super().paintEvent(event)


class _IslandFrame(QWidget):
    """Capsule behind the tab pills, painted directly.

    QSS could not be made to produce this shape: `QFrame#tabIsland` asked
    for a 24px radius on a 46px-tall box (a full stadium) and Qt kept
    drawing a lightly-rounded rectangle regardless, with or without
    WA_StyledBackground. Painting one rounded rect ourselves is both
    shorter and unambiguous -- the radius is derived from the widget's own
    height, so it stays a true capsule at any font size or DPI.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = QColor(30, 30, 32, 235)
        self._border = QColor(255, 255, 255, 28)

    def set_colors(self, bg, border):
        self._bg, self._border = QColor(bg), QColor(border)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


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

    # Solid color chip -- no glyph at all. Every previous version drew a
    # *character* (the library's 1px-pen rect, then "─ □ ✕"), and an
    # outlined glyph like □ has both an outer and an inner edge; scaled up,
    # or antialiased against a translucent backdrop, that inner edge reads
    # as a second offset copy of the shape. That is what kept getting
    # reported as ghosting, and no amount of clearing pixels before painting
    # could remove it, because it was the glyph itself. A filled rounded
    # square has one edge and no interior detail, so there is nothing left
    # that can look doubled -- macOS traffic lights, square rather than
    # round, as asked for.
    _BTN_QSS = """
        QPushButton {{
            background: {color};
            border: none;
            border-radius: 4px;
            padding: 0px;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {pressed}; }}
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
        for color, hover, pressed, tip, slot in (
            ("#27C93F", "#4EE063", "#1FA833", "Minimize", self._on_min),
            ("#FFBD2E", "#FFD268", "#D99F1F", "Maximize", self._on_max),
            ("#FF5F57", "#FF8A84", "#D94A43", "Close", self._on_close),
        ):
            b = QPushButton(self)
            b.setFixedSize(14, 14)
            b.setCursor(Qt.ArrowCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            b.setStyleSheet(self._BTN_QSS.format(
                color=color, hover=hover, pressed=pressed))
            b.clicked.connect(slot)
            self._buttons.append(b)
            self.hBoxLayout.addSpacing(10)
            self.hBoxLayout.addWidget(b, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.hBoxLayout.addSpacing(14)

    def _on_min(self):
        self.window().showMinimized()

    def _on_max(self):
        w = self.window()
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def _on_close(self):
        self.window().close()


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
        self.resize(1000, 680)

        self.setTitleBar(_CleanTitleBar(self))
        # Window-control icons default to pure black (QColor(0,0,0), baked
        # into qframelesswindow's TitleBarButton base class) -- invisible
        # against this app's dark Acrylic background (reported directly,
        # with a screenshot showing an empty top-right corner). Recolored
        # macOS-style (green minimize, yellow maximize, red close) via the
        # library's own public setNormalColor()/setHoverColor() API, which
        # fixes the visibility problem and matches what was asked for at
        # the same time. True stroke *boldness* isn't reachable through
        # this API -- Minimize/Maximize hardcode a 1px QPen and Close draws
        # an SVG with its own fixed stroke-width, both inside paintEvent
        # methods this library owns; changing that needs replacing the
        # button widgets outright, not just recoloring them, so it's left
        # as ordinary weight for now rather than risk that.
        _GREEN, _YELLOW, _RED = QColor("#27C93F"), QColor("#FFBD2E"), QColor("#FF5F57")
        self.titleBar.minBtn.setNormalColor(_GREEN)
        self.titleBar.minBtn.setHoverColor(_GREEN)
        self.titleBar.minBtn.setPressedColor(_GREEN)
        self.titleBar.maxBtn.setNormalColor(_YELLOW)
        self.titleBar.maxBtn.setHoverColor(_YELLOW)
        self.titleBar.maxBtn.setPressedColor(_YELLOW)
        self.titleBar.closeBtn.setNormalColor(_RED)
        self.titleBar.closeBtn.setPressedColor(_RED)

        # Required for the Mica/Acrylic backdrop to actually show through:
        # without this, Qt's raster backing store on Windows has no alpha
        # channel, so it always blits an opaque fill over whatever DWM
        # composited behind the window -- the effect call succeeds silently
        # but nothing behind the glass is ever visible. (macOS's own
        # vibrancy path in this same library sets this same attribute.)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Strips WS_MINIMIZEBOX / WS_MAXIMIZEBOX from the native window
        # style. Not a Qt-level fix -- this is the real cause of the
        # recurring "ghosting" reports on these buttons, confirmed by
        # capturing the live window with PrintWindow (bypassing Qt/DWM
        # compositing) rather than a synthetic in-process render: a native
        # dash/square/X was drawn over top of the colored chips even though
        # every one of our own titlebar widgets was verified hidden.
        # Windows 11 independently overlays its own caption-button glyphs
        # (including the Snap Layout hover flyout) on any window that still
        # carries these style bits, purely from DWM's own non-client
        # rendering -- entirely outside Qt's paint system, which is exactly
        # why clearing pixels or reparenting widgets could never remove it.
        # WS_SYSMENU is left in place (needed for the taskbar/Alt-Tab entry
        # and the system menu); minimize/maximize/restore still work
        # because the colored chips call showMinimized()/showMaximized()/
        # showNormal() directly through Qt, which doesn't depend on these
        # style bits at all.
        self._strip_native_caption_buttons()

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
        layout.setContentsMargins(12, self.titleBar.height() + 2, 12, 12)
        layout.setSpacing(10)

        # Content-level top bar (icon+title left, theme toggle+About right) --
        # Lives in `central` (code this module fully owns) rather than in
        # the library's own titlebar layout -- inserting widgets there
        # corrupted the window-control buttons.
        topbar = QWidget()
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(4, 0, 4, 0)
        icon_label = QLabel()
        if os.path.exists(ICON_PATH):
            icon_label.setPixmap(QPixmap(ICON_PATH).scaled(
                26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        topbar_layout.addWidget(icon_label)
        title_label = QLabel("AWESOME DOWNLOADER")
        title_label.setStyleSheet("font-weight: 700; font-size: 14px; letter-spacing: 0.3px;")
        topbar_layout.addWidget(title_label)
        topbar_layout.addStretch(1)

        self.theme_btn = QPushButton()
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedHeight(30)
        self.theme_btn.clicked.connect(self.toggle_theme)
        topbar_layout.addWidget(self.theme_btn)

        update_btn = QPushButton("Check for Updates")
        update_btn.setCursor(Qt.PointingHandCursor)
        update_btn.setFixedHeight(30)
        update_btn.clicked.connect(lambda: show_update_dialog(self, dark_mode=self.dark_mode))
        topbar_layout.addWidget(update_btn)

        about_btn = QPushButton("About")
        about_btn.setCursor(Qt.PointingHandCursor)
        about_btn.setFixedHeight(30)
        about_btn.clicked.connect(lambda: show_about(self, dark_mode=self.dark_mode))
        topbar_layout.addWidget(about_btn)
        layout.addWidget(topbar)

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
        self._island_layout.setContentsMargins(5, 5, 5, 5)
        self._island_layout.setSpacing(4)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addStretch(1)
        tab_row.addWidget(island)
        tab_row.addStretch(1)
        layout.addLayout(tab_row)
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

    _TAB_HEIGHT = 34

    def add_tab(self, widget, label):
        """Adds a page. The page goes into the (bar-less) QTabWidget and a
        pill button into the island -- indices stay in lockstep because both
        only ever grow through here."""
        index = self.tabs.count()
        self.tabs.addTab(widget, label)

        btn = QPushButton(label)
        btn.setObjectName("tabPill")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedHeight(self._TAB_HEIGHT)
        btn.clicked.connect(lambda _c=False, i=index: self.tabs.setCurrentIndex(i))
        self._island_layout.addWidget(btn)
        self._tab_buttons.append(btn)
        self._sync_island_selection(self.tabs.currentIndex())

    def _sync_island_selection(self, index):
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self._effect = apply_frosted_glass(self, dark_mode=self.dark_mode)
        self.setStyleSheet(theme.build_stylesheet(dark_mode=self.dark_mode))
        self._update_theme_btn_style()
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
        if getattr(self, "_island", None) is not None:
            self._island.set_colors(
                QColor(30, 30, 32, 235) if self.dark_mode else QColor(255, 255, 255, 240),
                QColor(255, 255, 255, 28) if self.dark_mode else QColor(0, 0, 0, 18))

    def changeEvent(self, event):
        super().changeEvent(event)
        # Maximize/restore flips which branch _TranslucentSurface.paintEvent
        # takes, and Qt doesn't repaint the central widget on a window-state
        # change by itself -- without this the washed-out fill persisted
        # until something else happened to dirty the widget.
        if event.type() == QEvent.WindowStateChange and getattr(self, "_central", None):
            self._central.update()

    def _strip_native_caption_buttons(self):
        if sys.platform != "win32":
            return
        try:
            import win32con
            import win32gui
            hwnd = int(self.winId())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            stripped = style & ~(win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)
            if stripped != style:
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, stripped)
        except Exception:
            logger.exception("Failed to strip native caption buttons")

    def showEvent(self, event):
        super().showEvent(event)
        self._strip_native_caption_buttons()

    def nativeEvent(self, eventType, message):
        # Reapplying the strip only in showEvent() was not enough either --
        # confirmed by reproducing the exact reported sequence (minimize,
        # resize, toggle theme twice) and capturing the live window with
        # PrintWindow at each step: WS_MINIMIZEBOX/WS_MAXIMIZEBOX measured
        # False right after showEvent, but toggle_theme()'s call into
        # apply_frosted_glass() -> setAcrylicEffect() put both back to True,
        # and the ghosted native dash/square/X reappeared in that exact
        # capture. toggle_theme() isn't the only thing that could ever touch
        # window composition attributes, so patching that one call site
        # would just move the whack-a-mole, not end it.
        #
        # WM_STYLECHANGED (0x7D) is Windows' own notification that GWL_STYLE
        # was just changed, by anything, for any reason -- hooking it here
        # means the strip re-applies immediately no matter what internal
        # qframelesswindow/DWM codepath put the bits back, present or
        # future, without needing to chase each call site individually.
        if sys.platform == "win32" and eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x7D:  # WM_STYLECHANGED
                self._strip_native_caption_buttons()
            elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
                self._maybe_snap_to_top()
        return super().nativeEvent(eventType, message)

    def _maybe_snap_to_top(self):
        """Drag-to-top-edge maximize, done by hand.

        Windows normally provides this (and Snap Layouts) for free, but only
        for windows carrying WS_MAXIMIZEBOX -- and that bit is exactly what
        makes DWM paint its own caption glyphs over the colored chips, the
        long-running "ghosting" complaint. Verified both directions: putting
        the bit back brings the native artifacts straight back, and DWM's
        NCRENDERING_POLICY=DISABLED does suppress them but takes the whole
        acrylic backdrop with it (the window renders flat white).

        So the bit stays off and the one behaviour actually being missed --
        "hold a window and snap it to the top" -- is reimplemented here.
        WM_EXITSIZEMOVE fires once when the user finishes dragging, which is
        the right moment to check where they let go.
        """
        if sys.platform != "win32" or self.isMaximized():
            return
        try:
            import win32api
            x, y = win32api.GetCursorPos()
            monitor = win32api.MonitorFromPoint((x, y), 1)  # MONITOR_DEFAULTTOPRIMARY
            top = win32api.GetMonitorInfo(monitor)["Work"][1]
            # Same few-pixel band Windows itself uses for the gesture.
            if y <= top + 6:
                self.showMaximized()
        except Exception:
            logger.exception("Drag-to-top maximize check failed")

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

    def _update_theme_btn_style(self):
        t = theme.tokens(dark_mode=self.dark_mode)
        self.theme_btn.setText("🌙  Dark" if self.dark_mode else "☀️  Light")
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
