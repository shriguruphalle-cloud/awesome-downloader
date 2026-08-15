"""Design tokens + QSS generator for the PySide6 UI.

Adapted for a frosted-glass window: the window itself is real translucent
Acrylic (see mica.py), so "card" surfaces are semi-transparent overlays
that let the blur show through, the way macOS vibrancy panels work.
"""
import os

from PySide6.QtGui import QFontDatabase

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

DARK = {
    "window_bg": "transparent",          # Mica/Acrylic paints the real backdrop
    "card_bg": "rgba(40, 40, 43, 150)",     # translucent card over the blur
    "card_bg_solid": "rgba(30, 30, 32, 235)",  # for panels that must stay legible over any wallpaper (menus, dialogs)
    "card_border": "rgba(255, 255, 255, 28)",
    "divider": "rgba(255, 255, 255, 18)",
    "text": "#f2f2f4",
    "text_muted": "#9b9b9d",
    # Back to a flat iOS/macOS blue -- the earlier switch to a purple
    # "systemIndigo" accent was never asked for and got reverted directly.
    "accent": "#0A84FF",
    "accent_hover": "#3B9CFF",
    "accent_text": "#ffffff",
    "danger": "#ff6961",
    "success": "#008F11",
    "progress": "#0d47a1",
    "warning": "#f2a93b",
    "hover_overlay": "rgba(255, 255, 255, 20)",
    "pressed_overlay": "rgba(255, 255, 255, 35)",
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
    "accent": "#007AFF",
    "accent_hover": "#0064D6",
    "accent_text": "#ffffff",
    "danger": "#ff3b30",
    "success": "#008F11",
    "progress": "#0d47a1",
    "warning": "#c77700",
    "hover_overlay": "rgba(0, 0, 0, 12)",
    "pressed_overlay": "rgba(0, 0, 0, 22)",
}


def build_stylesheet(dark_mode=True):
    t = DARK if dark_mode else LIGHT
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
    border-radius: 5px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    padding: 6px 10px;
    color: {t['text']};
    selection-background-color: {t['accent']};
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
    selection-background-color: {t['accent']};
    selection-color: {t['accent_text']};
    padding: 4px;
}}

QTabWidget::pane {{
    border: none;
    background: transparent;
    /* Gap between the tab-bar island and the first content card below it
       -- QTabWidget renders the bar and the page as one integrated unit
       with no gap by default, so this has to come from the pane's own
       padding, not from spacing at the layout level (reported directly,
       with a screenshot: the tab row and the URL card were touching). */
    padding-top: 8px;
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
QPushButton#tabPill {{
    background: transparent;
    border: none;
    border-radius: 17px;
    padding: 0px 22px;
    color: {t['text_muted']};
    font-weight: 500;
}}
QPushButton#tabPill:hover:!checked {{
    background: {t['hover_overlay']};
    color: {t['text']};
}}
QPushButton#tabPill:checked {{
    background: {t['accent']};
    color: {t['accent_text']};
    font-weight: 600;
}}

QProgressBar {{
    background: {t['hover_overlay']};
    border: none;
    border-radius: 5px;
    height: 10px;
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
    selection-background-color: {t['accent']};
    selection-color: {t['accent_text']};
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
