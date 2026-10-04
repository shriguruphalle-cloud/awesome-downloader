"""Design tokens + QSS for the PySide6 UI.

2.5 design language: the website's navy grid and blue glass (cinema.py),
with the chrome kept quiet so the two saturated colours can mean something:

  accent (ember #FF6A13)  ACTION ONLY -- the thing you are about to do:
                          primary buttons, progress, focus rings.
  brand  (sky #38BDF8)    IDENTITY ONLY -- the logo's own glow and the
                          website's blue: the wordmark, section labels, the
                          nav indicator's light strip, focus rings. Never on
                          a control that does something.

That split predates this redesign and survives it for the same reason it
was made: at full saturation on a dark canvas the blue reads far more
luminous than the orange, so when blue carried every button the whole UI
shouted. Blue is the light the whole window is lit with; ember is what you
press.

Token names are unchanged from 2.4 (every tab reads them by name); new ones
are additions.
"""
import os

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap

from app import config

from . import palettes

# Real SF Pro is licensed only for Apple platforms. Inter (SIL OFL) is the
# standard substitute -- its metrics read as near-identical at UI sizes --
# and bundling the file is what makes the type consistent on every machine.
_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_INTER_PATH = os.path.join(_FONTS_DIR, "InterVariable.ttf")
FONT_STACK = '"Inter", "SF Pro Display", "Segoe UI Variable", "Segoe UI", sans-serif'

# Quantities, codes and timecodes are set in mono so they align in columns.
# Cascadia Mono ships with Windows 11 and Consolas with every Windows since
# Vista, so this resolves to a real face without bundling another font.
MONO_STACK = '"JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", monospace'

_fonts_loaded = False


# The name's own face (widgets/wordmark.py): the website's Instrument Serif.
_SERIF_PATHS = [os.path.join(_FONTS_DIR, f) for f in ("InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf")]


def load_custom_fonts():
    """Registers the bundled Inter and Instrument Serif with Qt. Idempotent."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    for path in [_INTER_PATH] + _SERIF_PATHS:
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    _fonts_loaded = True


# ── Night (dark) ──────────────────────────────────────────────────────────
# The website's palette: navy glass, #EAF2FF text at 100/72/50% strength,
# sky blue for identity. Only the action colour is the app's own.
DARK = {
    "window_bg": "transparent",
    "ink": "#101c3e",
    # Glass. Cards are painted (widgets/card.py + cinema.paint_glass); these
    # strings are for the QSS-styled surfaces that sit on them.
    "card_bg": "rgba(255, 255, 255, 13)",
    # Near-opaque navy: popups, menus and tooltips are top-level windows of
    # their own, with no backdrop behind them to frost.
    "card_bg_solid": "rgba(18, 30, 64, 248)",
    "card_border": "rgba(255, 255, 255, 28)",
    "divider": "rgba(255, 255, 255, 18)",
    "text": "#eaf2ff",
    "text_muted": "#a9b4c9",      # the site's 72% text
    "text_faint": "#7b86a0",      # the site's 50% text
    "eyebrow": "#38bdf8",         # section labels, as the site's eyebrows
    "accent": "#ff6a13",
    "accent_hover": "#ff8038",
    "accent_pressed": "#e25a0a",
    "accent_top": "#ff8b47",      # the lit top of a primary button's gradient
    # Near-black on ember measures ~8:1; white only ~2.8:1.
    "accent_text": "#170b04",
    "brand": "#38bdf8",
    "brand_hover": "#7dd3fc",
    "brand_text": "#04121f",
    "danger": "#ff6b6b",
    "success": "#34d399",
    "progress": "#ff6a13",
    "warning": "#fcd34d",
    "hover_overlay": "rgba(255, 255, 255, 18)",
    "pressed_overlay": "rgba(255, 255, 255, 30)",
    # Inputs are recessed into the glass rather than raised off it.
    "field_bg": "rgba(4, 10, 30, 110)",
    "field_border": "rgba(255, 255, 255, 26)",
    "field_hover": "rgba(255, 255, 255, 46)",
    # Where you're typing is a state, not an action: sky, not ember.
    "focus": "rgba(56, 189, 248, 210)",
    "selection": "rgba(56, 189, 248, 110)",
    "scroll_handle": "rgba(255, 255, 255, 34)",
    "scroll_handle_hover": "rgba(255, 255, 255, 60)",
}

# ── Pearl (light) ─────────────────────────────────────────────────────────
LIGHT = {
    "window_bg": "transparent",
    "ink": "#b8c5e0",
    "card_bg": "rgba(255, 255, 255, 118)",
    # Menus and popups: pearl, a shade off white, so they don't glare.
    "card_bg_solid": "rgba(226, 233, 246, 250)",
    "card_border": "rgba(30, 50, 100, 30)",
    "divider": "rgba(30, 50, 100, 20)",
    "text": "#0b1530",
    "text_muted": "#45506a",      # 7.9:1 on white
    "text_faint": "#5d6883",
    "eyebrow": "#0284c7",
    # Deepened from the night ember so white text on it still clears 4.5:1.
    "accent": "#d0460e",
    "accent_hover": "#b93d0a",
    "accent_pressed": "#a53508",
    "accent_top": "#e2581a",
    "accent_text": "#ffffff",
    "brand": "#0284c7",
    "brand_hover": "#0369a1",
    "brand_text": "#ffffff",
    "danger": "#d92d20",
    "success": "#0e9f6e",
    "progress": "#d0460e",
    "warning": "#b7791f",
    "hover_overlay": "rgba(30, 50, 100, 14)",
    "pressed_overlay": "rgba(30, 50, 100, 26)",
    # Inputs are lighter than the glass they sit in, but not white.
    "field_bg": "rgba(255, 255, 255, 120)",
    "field_border": "rgba(30, 50, 100, 34)",
    "field_hover": "rgba(30, 50, 100, 60)",
    "focus": "rgba(2, 132, 199, 200)",
    "selection": "rgba(2, 132, 199, 80)",
    "scroll_handle": "rgba(15, 35, 90, 40)",
    "scroll_handle_hover": "rgba(15, 35, 90, 70)",
}

# The Browser tab's optional warm grade (its home page has the toggle):
# same glass, warmer whites. Same key set as DARK so it is a drop-in.
WARM = dict(DARK, **{
    "card_bg_solid": "rgba(26, 21, 18, 247)",
    "card_border": "rgba(255, 236, 214, 28)",
    "divider": "rgba(255, 236, 214, 18)",
    "text": "#f4ede4",
    "text_muted": "#b6a898",
    "text_faint": "#8e8173",
    "field_bg": "rgba(12, 8, 5, 120)",
    "field_border": "rgba(255, 236, 214, 26)",
})


_resolved = {}


def tokens(dark_mode=True):
    """The tokens for a theme, in the current colour palette (palettes.py):
    DARK or LIGHT with the palette's own colours laid over them."""
    key = (palettes.current(), bool(dark_mode))
    t = _resolved.get(key)
    if t is None:
        base = DARK if dark_mode else LIGHT
        overrides = palettes.token_overrides(dark_mode)
        t = dict(base, **overrides) if overrides else base
        _resolved[key] = t
    return t


def browser_tokens(accent="classic", dark_mode=True):
    """Same shape/keys as tokens(), with the Browser tab's optional warm
    grade on top of the night Sapphire palette (the jewel palettes bring
    their own warmth)."""
    if accent == "warm" and dark_mode and palettes.current() == palettes.DEFAULT:
        return WARM
    return tokens(dark_mode=dark_mode)


def qcolor(value):
    """A token as a QColor. QColor() silently returns black for a CSS
    "rgba(r, g, b, a)" string, which bit this app more than once."""
    value = value.strip()
    if value.startswith("rgba("):
        r, g, b, a = (int(float(x)) for x in value[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(value)


# ── Generated glyph files ─────────────────────────────────────────────────
# QSS can only reference an image by path, so the few glyphs the stylesheet
# needs are drawn once into APPDATA (never next to the .exe -- Program Files
# isn't writable for a standard install).
def _glyph_path(name):
    return os.path.join(config.APPDATA_DIR, "ui", name)


def _save_glyph(name, draw, size=16, scale=2):
    """Draws at 2x so the glyph stays crisp at 125-200% display scaling."""
    path = _glyph_path(name)
    if os.path.exists(path):
        return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        draw(painter, size)
        painter.end()
        pixmap.save(path, "PNG")
    except Exception:
        return None
    return path


def _checkmark(color):
    def draw(p, s):
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(s * 0.25, s * 0.53)
        path.lineTo(s * 0.43, s * 0.70)
        path.lineTo(s * 0.77, s * 0.32)
        p.drawPath(path)
    return draw


def _chevron(color):
    def draw(p, s):
        pen = QPen(QColor(color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(QPointF(s * 0.28, s * 0.40))
        path.lineTo(QPointF(s * 0.50, s * 0.62))
        path.lineTo(QPointF(s * 0.72, s * 0.40))
        p.drawPath(path)
    return draw


def _qss_url(path):
    return f"url({path.replace(os.sep, '/')})" if path else "none"


def build_stylesheet(dark_mode=True):
    t = tokens(dark_mode)

    def tag(color):
        # Glyph files are named for their colour, so each palette gets its own.
        return color.lstrip("#").lower()
    check = _save_glyph(f"check-{tag(t['accent_text'])}.png", _checkmark(t["accent_text"]))
    chevron = _save_glyph(f"chevron-{tag(t['text_muted'])}.png", _chevron(t["text_muted"]))
    chevron_hover = _save_glyph(f"chevron-{tag(t['text'])}.png", _chevron(t["text"]))
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

/* ── Type ─────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {t['text']};
}}
QLabel#muted {{
    color: {t['text_muted']};
}}
QLabel#faint {{
    color: {t['text_faint']};
}}
/* Section labels: small caps, widely tracked -- a title card, not a heading. */
QLabel#sectionLabel {{
    color: {t['eyebrow']};
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 1.9px;
}}
QLabel#heading {{
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.1px;
}}
QLabel#display {{
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.2px;
}}
QLabel#mono {{
    font-family: {MONO_STACK};
    font-size: 11px;
    color: {t['text_muted']};
}}
QLabel#dangerText, QLabel[state="error"], QLabel#mono[state="error"],
QLabel#muted[state="error"] {{
    color: {t['danger']};
}}
QLabel#mono[state="done"] {{
    color: {t['success']};
}}
QFrame#divider {{
    background: {t['divider']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ── Legacy QSS cards (painted cards are the norm; see widgets/card.py) ── */
QFrame#card, QFrame#cardSolid {{
    background: {t['card_bg']};
    border: 1px solid {t['card_border']};
    border-radius: 16px;
}}

/* One stacked link in the Video tab's queue: a raised row on the queue's
   glass, not a second card. Smaller radius than the 16px panel it sits in --
   the same radius nested reads as the inner edge bulging out. */
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
QComboBox#queueCombo {{
    background: {t['field_bg']};
    border: 1px solid {t['field_border']};
    border-radius: 8px;
    padding: 2px 8px;
    font-family: {MONO_STACK};
    font-size: 10px;
    color: {t['text_muted']};
    min-height: 22px;
}}
QComboBox#queueCombo:hover {{
    color: {t['text']};
    border: 1px solid {t['field_hover']};
}}
QComboBox#queueCombo::drop-down {{
    border: none;
    width: 16px;
}}

/* ── Buttons ──────────────────────────────────────────────────────── */
/* Every rounded fill carries a border. Qt clips a QSS background to its
   radius without antialiasing, but it strokes the border with it -- so the
   border is what keeps a corner smooth. */
QPushButton {{
    background: {t['hover_overlay']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    padding: 7px 16px;
    color: {t['text']};
    font-weight: 500;
}}
QPushButton:hover {{
    background: {t['pressed_overlay']};
    border-color: {t['field_hover']};
}}
QPushButton:pressed {{
    background: {t['hover_overlay']};
}}
QPushButton:disabled {{
    color: {t['text_faint']};
    background: transparent;
    border-color: {t['divider']};
}}

/* The one action on a view. A lit gradient -- brighter along the top edge,
   the way light falls on it in the backdrop. */
QPushButton#accent {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['accent_top']}, stop:1 {t['accent']});
    border: 1px solid {t['accent']};
    border-top-color: {t['accent_top']};
    color: {t['accent_text']};
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['accent_hover']}, stop:1 {t['accent_hover']});
    border-color: {t['accent_hover']};
}}
QPushButton#accent:pressed {{
    background: {t['accent_pressed']};
    border-color: {t['accent_pressed']};
}}
QPushButton#accent:disabled {{
    background: {t['hover_overlay']};
    border: 1px solid {t['divider']};
    color: {t['text_faint']};
}}

/* Quiet secondary: no fill until hovered. Most row actions are this. */
QPushButton#quiet {{
    background: transparent;
    border: 1px solid transparent;
    color: {t['text_muted']};
    font-weight: 500;
    border-radius: 8px;
    padding: 4px 12px;
}}
QPushButton#quiet:hover {{
    background: {t['hover_overlay']};
    border-color: {t['card_border']};
    color: {t['text']};
}}
QPushButton#quiet:pressed {{
    background: {t['pressed_overlay']};
}}
QPushButton#quiet:disabled {{
    color: {t['text_faint']};
    background: transparent;
    border-color: transparent;
}}

/* Outlined action -- the accent as a line rather than a fill. */
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

QPushButton#danger {{
    background: transparent;
    border: 1px solid {t['danger']};
    color: {t['danger']};
}}
QPushButton#danger:hover {{
    background: {t['hover_overlay']};
}}

QPushButton#success {{
    background: transparent;
    border: 1px solid {t['success']};
    color: {t['success']};
    font-weight: 600;
}}
QPushButton#success:hover {{
    background: {t['hover_overlay']};
}}

/* Compact row buttons (History, the Download tab's cards). Their own tight
   padding: they are pinned to small fixed heights, and the base rule's
   7px/16px would crop the glyphs at the bottom. */
QPushButton#historyPlain {{
    background: {t['hover_overlay']};
    border: 1px solid {t['card_border']};
    border-radius: 8px;
    padding: 3px 12px;
    font-size: 12px;
    color: {t['text']};
}}
QPushButton#historyPlain:hover {{
    background: {t['pressed_overlay']};
    border-color: {t['field_hover']};
}}
QPushButton#historyPlain:disabled {{
    color: {t['text_faint']};
    background: transparent;
}}
QPushButton#historyGreen {{
    background: transparent;
    border: 1px solid {t['success']};
    border-radius: 8px;
    padding: 3px 12px;
    font-size: 12px;
    font-weight: 600;
    color: {t['success']};
}}
QPushButton#historyGreen:hover {{
    background: {t['hover_overlay']};
}}

/* Title-bar nav labels. Their shapes (the sliding indicator, hover) are
   painted -- QSS radii are not antialiased -- so these rules carry only the
   type. */
QPushButton#tabPill {{
    background: transparent;
    border: none;
    padding: 0px 11px;
    font-size: 12.5px;
    font-weight: 500;
    color: {t['text_muted']};
}}
QPushButton#tabPill:hover:!checked {{
    color: {t['text']};
}}
QPushButton#tabPill:checked {{
    color: {t['text']};
    font-weight: 600;
}}

/* ── Checks and radios ────────────────────────────────────────────── */
QRadioButton, QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t['field_hover']};
    background: {t['field_bg']};
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator {{
    border-radius: 5px;
}}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
    border-color: {t['text_muted']};
}}
QRadioButton::indicator:checked {{
    border: 1px solid {t['accent']};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {t['accent']}, stop:0.42 {t['accent']},
        stop:0.52 transparent, stop:1 transparent);
}}
QCheckBox::indicator:checked {{
    border: 1px solid {t['accent']};
    background: {t['accent']};
    image: {_qss_url(check)};
}}
QRadioButton::indicator:disabled, QCheckBox::indicator:disabled {{
    border-color: {t['divider']};
    background: transparent;
}}
QRadioButton:disabled, QCheckBox:disabled {{
    color: {t['text_faint']};
}}

/* ── Fields ───────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {t['field_bg']};
    border: 1px solid {t['field_border']};
    border-radius: 10px;
    padding: 7px 12px;
    color: {t['text']};
    selection-background-color: {t['selection']};
    selection-color: {t['text']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border-color: {t['field_hover']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {t['focus']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {t['text_faint']};
    border-color: {t['divider']};
}}
/* The Video tab's link field: the page's hero, so a size up. */
QLineEdit#heroField {{
    font-size: 14px;
    border-radius: 12px;
    padding: 0px 14px 0px 6px;
}}
QLineEdit#timecode {{
    font-family: {MONO_STACK};
    font-size: 12px;
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: {_qss_url(chevron)};
    width: 14px;
    height: 14px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: {_qss_url(chevron_hover)};
}}
QComboBox QAbstractItemView {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    outline: none;
    padding: 4px;
    color: {t['text']};
    selection-background-color: {t['pressed_overlay']};
    selection-color: {t['text']};
}}

/* ── Containers ───────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: transparent;
    padding: 0px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}

QProgressBar {{
    background: {t['hover_overlay']};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: {t['text']};
}}
QProgressBar::chunk {{
    background: {t['progress']};
    border-radius: 3px;
}}
QProgressBar#complete::chunk {{
    background: {t['success']};
    border-radius: 3px;
}}

/* Scrollbars: a hairline that thickens under the cursor. */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 1px 2px 1px;
}}
QScrollBar::handle:vertical {{
    background: {t['scroll_handle']};
    border-radius: 4px;
    min-height: 36px;
    margin: 0px 1px 0px 1px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['scroll_handle_hover']};
    margin: 0px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 1px 2px 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scroll_handle']};
    border-radius: 4px;
    min-width: 36px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QTableView, QTreeView, QListView {{
    background: transparent;
    border: none;
    gridline-color: {t['divider']};
    selection-background-color: {t['pressed_overlay']};
    selection-color: {t['text']};
    outline: none;
}}
QHeaderView::section {{
    background: transparent;
    color: {t['text_faint']};
    border: none;
    border-bottom: 1px solid {t['divider']};
    padding: 6px;
    font-size: 11px;
    font-weight: 600;
}}

QMenu {{
    background: {t['card_bg_solid']};
    border: 1px solid {t['card_border']};
    border-radius: 10px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 22px 7px 12px;
    border-radius: 6px;
    color: {t['text']};
}}
QMenu::item:selected {{
    background: {t['pressed_overlay']};
}}
QMenu::item:disabled {{
    color: {t['text_faint']};
}}
QMenu::separator {{
    height: 1px;
    background: {t['divider']};
    margin: 5px 8px;
}}

QToolTip {{
    background: {t['card_bg_solid']};
    color: {t['text']};
    border: 1px solid {t['card_border']};
    border-radius: 8px;
    padding: 5px 9px;
}}
"""
