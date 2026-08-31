"""Design tokens + QSS generator for the PySide6 UI.

Adapted for a frosted-glass window: the window itself is real translucent
Acrylic (see mica.py), so "card" surfaces are semi-transparent overlays
that let the blur show through, the way macOS vibrancy panels work.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap

from app import config

# Real SF Pro (Apple's system font) is proprietary and licensed only for use
# on Apple's own platforms -- can't legally bundle it here. Inter (SIL Open
# Font License, free to redistribute) is the standard, widely-used
# substitute specifically because its metrics/x-height/weight read as
# near-identical to SF Pro at UI sizes; bundling the actual font file (not
# just naming it in a CSS stack and hoping it's installed) is what makes
# this consistent across every Windows machine regardless of what fonts
# they already have.
_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_INTER_PATH = os.path.join(_FONTS_DIR, "InterVariable.ttf")
FONT_STACK = '"Inter", "SF Pro Display", "Segoe UI Variable", "Segoe UI", sans-serif'

# Technical voice: anything that is a quantity, a key, a code or a section
# label is set in mono, so numbers align in columns and labels read as
# instrument markings rather than prose. Cascadia Mono ships with Windows
# 11 and Consolas with every Windows back to Vista, so this resolves to a
# real face without bundling another font.
MONO_STACK = '"JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", monospace'

_fonts_loaded = False


def load_custom_fonts():
    """Registers the bundled Inter font with Qt so "Inter" resolves in QSS
    font-family regardless of whether it's separately installed on this
    machine. Safe to call more than once (idempotent past the first call);
    every MainWindow.__init__ calls this rather than requiring a separate
    app-startup step to remember."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    if os.path.exists(_INTER_PATH):
        QFontDatabase.addApplicationFont(_INTER_PATH)
    _fonts_loaded = True

# ── "Instrument" palette ────────────────────────────────────────────────
# Raycast's chrome discipline (a near-black surface ladder carrying all
# elevation, hairline borders instead of shadows) with Teenage
# Engineering's colour discipline: exactly two saturated colours that
# never trade places.
#
#   accent (#FF6600)  ACTION ONLY  -- the thing you are about to do:
#                     primary buttons, progress, focus rings.
#   brand  (#0A84FF)  IDENTITY ONLY -- the bolt, the wordmark, the active
#                     nav pill. It never sits on a control that performs
#                     an action.
#
# The split exists for a measured reason, not taste: at full saturation on
# a near-black canvas the blue reads considerably more luminous than the
# orange, so when blue carried every button the whole UI shouted. Confining
# it to identity keeps it as the first thing you see without it competing
# with everything else.
DARK = {
    "window_bg": "transparent",          # Mica/Acrylic paints the real backdrop
    # Surface ladder, kept translucent so the real Acrylic blur still reads
    # through (the design system's flat #0d0d0e/#121213 steps are the
    # opaque equivalents of these two).
    "card_bg": "rgba(18, 18, 20, 150)",
    "card_bg_solid": "rgba(16, 17, 18, 238)",
    "card_border": "rgba(255, 255, 255, 20)",   # Raycast hairline, ~0.08
    "divider": "rgba(255, 255, 255, 16)",
    "text": "#f4f4f6",
    "text_muted": "#9c9c9d",
    "accent": "#ff6600",
    "accent_hover": "#ff7a1f",
    # Black on orange measures ~8:1; white on orange only ~2.6:1. Black is
    # both the accessible choice and the Teenage Engineering one.
    "accent_text": "#0a0a0a",
    "brand": "#0a84ff",
    "brand_hover": "#3da8ff",
    "brand_text": "#ffffff",
    "danger": "#ff3b30",
    "success": "#2fd35a",
    "progress": "#ff6600",              # progress is an action, so it wears the action colour
    "warning": "#ffc533",
    "hover_overlay": "rgba(255, 255, 255, 18)",
    "pressed_overlay": "rgba(255, 255, 255, 30)",
}

LIGHT = {
    "window_bg": "transparent",
    "card_bg": "rgba(255, 255, 255, 160)",
    "card_bg_solid": "rgba(255, 255, 255, 240)",
    "card_border": "rgba(0, 0, 0, 18)",
    "divider": "rgba(0, 0, 0, 14)",
    "text": "#1c1c1e",
    # #6b6b70 measured at 5.3:1 against the light card's near-white
    # background -- passes WCAG AA by a hair, but read as washed-out light
    # grey in practice (reported directly: "light grey text and white bg,
    # contrast is very low"). #48484e clears 9:1 (AAA), well past the
    # complaint with real margin rather than another borderline value.
    "text_muted": "#48484e",
    # Darkened from #FF6600 so the same orange still clears contrast on a
    # near-white ground -- the dark theme's value is too light there.
    "accent": "#e05500",
    "accent_hover": "#c44a00",
    "accent_text": "#ffffff",
    "brand": "#007aff",
    "brand_hover": "#0064d6",
    "brand_text": "#ffffff",
    "danger": "#e02d22",
    "success": "#12a150",
    "progress": "#e05500",
    "warning": "#c77700",
    "hover_overlay": "rgba(0, 0, 0, 12)",
    "pressed_overlay": "rgba(0, 0, 0, 22)",
}


_CHECKMARK_PATH = os.path.join(config.APPDATA_DIR, "checkbox_checkmark.png")


def _ensure_checkmark_icon():
    """Generates the checked-checkbox glyph once into APPDATA_DIR (never
    next to the installed .exe -- Program Files isn't writable at runtime
    for a standard install) so QCheckBox::indicator:checked can reference
    it via QSS's image: url(...), which needs a real file path, not a
    QPainter call at style-build time. A plain solid-filled box with no
    checkmark at all was reported directly as unclear/wrong-looking."""
    if os.path.exists(_CHECKMARK_PATH):
        return _CHECKMARK_PATH
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(size * 0.23, size * 0.52)
        path.lineTo(size * 0.42, size * 0.72)
        path.lineTo(size * 0.80, size * 0.28)
        painter.drawPath(path)
        painter.end()
        pixmap.save(_CHECKMARK_PATH, "PNG")
    except Exception:
        return None
    return _CHECKMARK_PATH


# A fixed, deliberately-styled accent for radio button/checkbox
# indicators -- left entirely to Qt's native rendering before, which
# meant a checked one showed whatever the user's own Windows accent
# color happened to be (reported directly, an unrelated orange) rather
# than a color this app actually controls.
#
# This started life as its own one-off #FF4D00, picked before the palette
# had a real action colour. It is now folded into that action colour
# instead: two oranges 20 hue-degrees apart, sitting in the same window,
# read as a mistake rather than a system. Same value regardless of
# dark/light mode -- a checked control means the same thing in both.
RADIO_CHECK_ACCENT = DARK["accent"]


def build_stylesheet(dark_mode=True):
    t = DARK if dark_mode else LIGHT
    checkmark_path = _ensure_checkmark_icon()
    checkmark_css = f"image: url({checkmark_path.replace(os.sep, '/')});" if checkmark_path else ""
    return f"""
QMainWindow, QDialog, QWidget#centralSurface {{
    background: {t['window_bg']};
    color: {t['text']};
    font-family: {FONT_STACK};
    font-size: 13px;
}}

QWidget {{
    color: {t['text']};
}}

QFrame#card {{
    background: {t['card_bg']};
    border: 1px solid {t['card_border']};
    border-radius: 20px;
}}

QFrame#cardSolid {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 20px;
}}

/* One stacked link in the video tab's queue. Sits inside the QUEUE card, so
   it is a step *up* the surface ladder from its parent rather than another
   full card -- a hairline and a lift, not a second border box. The radius is
   deliberately smaller than the 20px parent: nesting the same radius makes
   the inner edge look like it is bulging out of the outer one. */
QFrame#queueCard {{
    background: {t['hover_overlay']};
    border: 1px solid transparent;
    border-radius: 12px;
}}
QFrame#queueCard:hover {{
    border: 1px solid {t['card_border']};
}}
QFrame#queueCard[selected="true"] {{
    background: {t['pressed_overlay']};
    border: 1px solid {t['brand']};
}}
QFrame#queueCard QLabel {{
    background: transparent;
}}
/* Compact per-link resolution picker: quieter than the form's combo above,
   because it is a refinement of a choice already made, not the main event. */
QComboBox#queueCombo {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 7px;
    padding: 2px 8px;
    font-family: {MONO_STACK};
    font-size: 10px;
    color: {t['text_muted']};
    min-height: 22px;
}}
QComboBox#queueCombo:hover {{
    color: {t['text']};
    border: 1px solid {t['divider']};
}}
QComboBox#queueCombo::drop-down {{
    border: none;
    width: 14px;
}}

QLabel {{
    background: transparent;
    color: {t['text']};
}}

QLabel#muted {{
    color: {t['text_muted']};
}}

QLabel#heading {{
    font-size: 18px;
    font-weight: 600;
}}

QPushButton {{
    background: {t['hover_overlay']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    padding: 7px 16px;
    color: {t['text']};
}}
QPushButton:hover {{
    background: {t['pressed_overlay']};
}}
QPushButton:pressed {{
    background: {t['card_border']};
}}
QPushButton:disabled {{
    color: {t['text_muted']};
}}

QPushButton#accent {{
    background: {t['accent']};
    border: none;
    color: {t['accent_text']};
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: {t['accent_hover']};
}}
QPushButton#accent:disabled {{
    background: {t['hover_overlay']};
    color: {t['text_muted']};
}}

QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {t['text_muted']};
    background: transparent;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator:checked {{
    border: 2px solid {RADIO_CHECK_ACCENT};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {RADIO_CHECK_ACCENT}, stop:0.45 {RADIO_CHECK_ACCENT},
        stop:0.55 transparent, stop:1 transparent);
}}
QCheckBox::indicator:checked {{
    border: 2px solid {RADIO_CHECK_ACCENT};
    background: {RADIO_CHECK_ACCENT};
    {checkmark_css}
}}
QRadioButton::indicator:disabled, QCheckBox::indicator:disabled {{
    border-color: {t['card_border']};
}}

QPushButton#danger {{
    background: transparent;
    border: 1px solid {t['danger']};
    color: {t['danger']};
}}
QPushButton#danger:hover {{
    background: rgba(255, 59, 48, 30);
}}

/* Outlined pill -- the same shape as #danger above but in the accent color,
   for row actions that aren't destructive. Asked for directly: the torrent
   row's Pause/Open Folder/Remove buttons were solid filled accent while
   Delete Files was outlined, and three heavy blue blocks next to one thin
   red outline read as four unrelated controls. Outlining all four makes
   them one set, with red reserved for the only one that destroys data. */
QPushButton#pill {{
    background: transparent;
    border: 1px solid {t['accent']};
    color: {t['accent']};
    font-weight: 500;
}}
QPushButton#pill:hover {{
    background: {t['hover_overlay']};
}}
QPushButton#pill:pressed {{
    background: {t['pressed_overlay']};
}}

/* Quiet secondary: no border, no accent. This is what most row actions
   should be. The outlined #pill above put the accent colour on every
   action in a torrent row -- roughly 25 accent-coloured buttons on a full
   screen -- which drained the colour of meaning and made "Delete Files"
   indistinguishable from "Open Folder". A row now gets exactly one filled
   accent control (the primary), quiet buttons beside it, and its
   destructive actions behind an overflow menu. */
QPushButton#quiet {{
    background: {t['hover_overlay']};
    border: 1px solid transparent;
    color: {t['text']};
    font-weight: 500;
    border-radius: 8px;
    padding: 4px 12px;
}}
QPushButton#quiet:hover {{
    background: {t['pressed_overlay']};
}}
QPushButton#quiet:pressed {{
    background: {t['card_border']};
}}
QPushButton#quiet:disabled {{
    color: {t['text_muted']};
}}

/* Finished torrent: green outline + green bar, so "done" reads at a glance
   without having to check the percentage. */
QPushButton#success {{
    background: transparent;
    border: 1px solid {t['success']};
    color: {t['success']};
    font-weight: 600;
}}
QPushButton#success:hover {{
    background: {t['hover_overlay']};
}}

/* Compact row buttons (History tab) -- the base QPushButton rule's
   padding: 7px 16px needs ~13px text + 14px padding = ~27px minimum, but
   these are pinned to a fixed 24px height to sit small next to a card's
   title/meta text. Combining that fixed height with the full-size padding
   left no room for the glyphs and cropped them at the bottom (reported
   directly, from a real screenshot) -- these two rules carry their own
   much tighter padding instead of relying on setFixedHeight() alone to
   force a shrink that padding never actually allowed. Qt only honours one
   objectName per widget, so "plain small button" and "green small button"
   need their own complete rules rather than combining with #success. */
QPushButton#historyPlain {{
    background: {t['hover_overlay']};
    border: 1px solid {t['card_border']};
    border-radius: 8px;
    padding: 2px 10px;
    font-size: 12px;
    color: {t['text']};
}}
QPushButton#historyPlain:hover {{
    background: {t['pressed_overlay']};
}}
QPushButton#historyGreen {{
    background: transparent;
    border: 1px solid {t['success']};
    border-radius: 8px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
    color: {t['success']};
}}
QPushButton#historyGreen:hover {{
    background: {t['hover_overlay']};
}}
QProgressBar#complete::chunk {{
    background: {t['success']};
    border-radius: 3px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    padding: 6px 10px;
    color: {t['text']};
    selection-background-color: {t['brand']};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 1px solid {t['accent']};
}}

/* Default Qt combo-box drop-down renders as an unstyled square button with
   a native-theme arrow glyph -- against this app's dark rounded inputs
   that reads as a visible seam/box clashing with the rest of the control
   (reported directly: "visual issue... near the drop down arrow"). Giving
   the drop-down subcontrol its own transparent background removes the
   seam; a small explicit arrow size keeps the glyph legible instead of
   whatever oversized default the current style would draw. */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    outline: none;
    selection-background-color: {t['brand']};
    selection-color: {t['brand_text']};
    padding: 4px;
}}

QTabWidget::pane {{
    border: none;
    background: transparent;
    /* No padding here any more. This used to carry the gap between the tab
       island and the first content card, but the island now lives up in the
       title bar and the gap belongs to the window's content layout instead
       -- where it can be dropped for a tab that wants to run edge to edge
       (the Browser), which a pane rule applying to every page cannot do. */
    padding: 0px;
}}
/* Centered floating "island" segmented control (dynamic-island style)
   rather than a left-aligned underlined tab strip.

   Both radii are half the rendered height, which is what makes a real
   capsule: the island is 46px tall (34px pill + margins + border), the pills
   34px. These are QPushButtons, not QTabBar tabs, because Qt's style
   engine ignores border-radius on QTabBar::tab -- the QSS asked for 22px
   and it still drew near-square boxes. A button's radius is honoured by
   the ordinary QSS box model on every style. */
QFrame#tabIsland {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 24px;
}}
/* Instrument radii: 6px rows inside an 8px tray, rather than a full
   stadium capsule. The tighter geometry is what makes a dense tool read
   as machined instead of soft. */
QPushButton#tabPill {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    /* 12px, down from 15. Six pills carried 180px of pure padding in a row
       that also has to hold a wordmark and three action buttons; trimming
       3px a side gives 36px back to the labels themselves. */
    padding: 0px 12px;
    font-size: 12.5px;
    color: {t['text_muted']};
    font-weight: 500;
}}
/* Backgrounds are painted by _TabPill, not by QSS: Qt does not antialias a
   QSS border-radius, and the stair-stepped corners were visible against the
   antialiased island behind them. These rules keep only the text treatment. */
QPushButton#tabPill:hover:!checked {{
    background: transparent;
    color: {t['text']};
}}
/* The active pill rises one step on the surface ladder and states itself
   in brand blue *text* -- not a saturated blue fill. A filled pill was
   tried and became the loudest object in the window, sitting right beside
   an already-blue wordmark and breaking the rule the whole language rests
   on: chrome stays monochrome, saturation is reserved for meaning. The
   raised surface carries the selection; the colour only names it. */
QPushButton#tabPill:checked {{
    background: transparent;
    /* A hairline on the raised pill. Without it the selected surface and the
       island behind it are two dark greys a step apart, which reads as a
       smudge rather than a raised control; the border is what actually gives
       it an edge. Same 1px white-alpha hairline every other raised surface in
       the app uses, so the selection is built from the existing vocabulary
       rather than a new treatment. */
    border: 1px solid transparent;
    color: {t['brand']};
    font-weight: 600;
}}

QProgressBar {{
    background: {t['hover_overlay']};
    border: none;
    border-radius: 3px;
    height: 7px;
    text-align: center;
    color: {t['text']};
}}
QProgressBar::chunk {{
    background: {t['progress']};
    border-radius: 5px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t['card_border']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['hover_overlay']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QTableView {{
    background: transparent;
    border: none;
    gridline-color: {t['divider']};
    selection-background-color: {t['brand']};
    selection-color: {t['brand_text']};
}}
QHeaderView::section {{
    background: transparent;
    color: {t['text_muted']};
    border: none;
    border-bottom: 1px solid {t['divider']};
    padding: 6px;
    font-weight: 600;
}}

QMenu {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 12px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px;
    border-radius: 8px;
}}
QMenu::item:selected {{
    background: {t['accent']};
    color: {t['accent_text']};
}}

QToolTip {{
    background: {t['card_bg_solid']};
    color: {t['text']};
    border: 1px solid {t['card_border']};
    border-radius: 8px;
    padding: 4px 8px;
}}
"""


def tokens(dark_mode=True):
    return DARK if dark_mode else LIGHT


# The Browser tab's own accent choice -- warm off-black instead of pure
# black, a terracotta accent instead of blue -- independent of the
# app-wide Dark/Light toggle every other tab uses. Same key set as
# DARK/LIGHT so it's a drop-in wherever the Browser tab already calls
# theme.tokens(...).
WARM = {
    "window_bg": "transparent",
    "card_bg": "rgba(51, 45, 39, 150)",
    "card_bg_solid": "rgba(51, 45, 39, 235)",
    "card_border": "rgba(255, 255, 255, 24)",
    "divider": "rgba(255, 255, 255, 16)",
    "text": "#F1EAE0",
    "text_muted": "#B3A797",
    # Same action/identity split as DARK -- see its comment. The warm
    # surface is the only thing that differs here.
    "accent": "#ff6600",
    "accent_hover": "#ff7a1f",
    "accent_text": "#0a0a0a",
    "brand": "#0a84ff",
    "brand_hover": "#3da8ff",
    "brand_text": "#ffffff",
    "danger": "#ff3b30",
    "success": "#2fd35a",
    "progress": "#ff6600",
    "warning": "#ffc533",
    "hover_overlay": "rgba(255, 255, 255, 18)",
    "pressed_overlay": "rgba(255, 255, 255, 30)",
}


def browser_tokens(accent="warm", dark_mode=True):
    """Same shape/keys as tokens() -- a drop-in for every spot in the
    Browser tab that already calls theme.tokens(dark_mode=...) -- but with
    an extra "warm" choice on top of the normal dark/light pair."""
    if accent == "warm":
        return WARM
    return tokens(dark_mode=dark_mode)
