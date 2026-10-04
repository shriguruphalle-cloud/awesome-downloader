"""Colour palettes: the backdrop's light, and the colours that go with it.

Sapphire is the original look, the website's navy and sky. The others are
jewel tones with a metal to go with each -- ruby and gold, gold on espresso,
emerald and gold, obsidian and champagne, amethyst and gold, rose gold -- and
each has a night and a day version, so the Theme switch keeps working.

A palette is a handful of colours (the field, its lights, the metal for
identity and the one for action); everything else -- the ink behind menus,
text tinted toward the palette, recessed fields, the title band -- is
derived from them here, so a new palette is one entry, not a stylesheet.
"""

_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def _rgba(rgb, alpha):
    return "rgba(%d, %d, %d, %d)" % (rgb[0], rgb[1], rgb[2], alpha)


# Each variant:
#   base   the field
#   glows  (rgb, peak alpha, centre, radii, fade) -- see cinema._ellipse_glow
#   grid   (rgb, alpha, spacing)
#   sheen  optional (rgb, alpha): a soft diagonal fall of light, like satin
#   brand  identity metal: the wordmark, section labels, "you are here"
#   accent action colour: primary buttons, progress
#   band   (day only) the title band's tone
#   deep   (day only) the darkest tone, for text
_TOP, _RIGHT, _LEFT, _FOOT = (0.50, -0.08), (0.90, 0.22), (0.04, 0.74), (0.50, 1.20)
_TOP_R, _RIGHT_R, _LEFT_R, _FOOT_R = (0.95, 0.66), (0.72, 0.58), (0.82, 0.62), (1.20, 0.60)

PALETTES = {
    # Every palette is deliberately low-key: dark fields with soft light in
    # them, and muted metals. The first cut of the jewel tones was vivid
    # enough to pull the eye off the content (reported: "too vibrant").
    "sapphire": {
        "label": "Sapphire",
        "swatch": (46, 82, 158),
        "night": {
            "base": (13, 23, 52),
            "glows": (
                ((56, 189, 248), 0.24, _TOP, _TOP_R, 0.62),
                ((129, 140, 248), 0.21, _RIGHT, _RIGHT_R, 0.62),
                ((14, 165, 233), 0.16, _LEFT, _LEFT_R, 0.64),
                ((5, 10, 26), 0.50, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((255, 255, 255), 0.068, 68),
            "brand": (56, 189, 248),
            "accent": (255, 106, 19),
        },
        "day": {
            "base": (184, 197, 224),
            "glows": (
                ((104, 168, 246), 0.36, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((160, 146, 244), 0.30, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((244, 184, 168), 0.20, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((120, 136, 178), 0.34, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((30, 50, 100), 0.075, 68),
            "brand": (2, 132, 199),
            "accent": (208, 70, 14),
            "band": (44, 62, 118),
            "deep": (11, 21, 48),
        },
    },
    "ruby": {
        "label": "Ruby",
        "swatch": (122, 30, 52),
        "night": {
            "base": (24, 8, 13),
            "glows": (
                ((190, 46, 76), 0.17, _TOP, _TOP_R, 0.62),
                ((228, 142, 124), 0.09, _RIGHT, _RIGHT_R, 0.62),
                ((128, 20, 44), 0.20, _LEFT, _LEFT_R, 0.64),
                ((8, 2, 4), 0.58, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((255, 214, 222), 0.053, 68),
            "sheen": ((255, 190, 200), 0.03),
            "saturation": 1.2,
            "brand": (226, 150, 160),
            "accent": (214, 176, 112),
        },
        "day": {
            "base": (226, 212, 216),
            "glows": (
                ((214, 120, 140), 0.24, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((236, 180, 160), 0.18, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((240, 214, 190), 0.14, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((160, 116, 126), 0.28, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((110, 30, 50), 0.060, 68),
            "saturation": 1.3,
            "brand": (140, 30, 58),
            "accent": (150, 32, 62),
            "band": (110, 44, 62),
            "deep": (40, 10, 20),
        },
    },
    "gold": {
        "label": "Gold",
        "swatch": (160, 122, 54),
        "night": {
            "base": (19, 15, 10),
            "glows": (
                ((212, 160, 66), 0.15, _TOP, _TOP_R, 0.62),
                ((236, 210, 150), 0.07, _RIGHT, _RIGHT_R, 0.62),
                ((170, 98, 24), 0.13, _LEFT, _LEFT_R, 0.64),
                ((5, 4, 2), 0.60, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((255, 236, 200), 0.053, 68),
            "sheen": ((255, 226, 160), 0.035),
            "saturation": 1.2,
            "brand": (222, 194, 132),
            "accent": (214, 168, 76),
        },
        "day": {
            "base": (226, 218, 202),
            "glows": (
                ((226, 184, 100), 0.24, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((244, 226, 184), 0.22, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((230, 196, 170), 0.12, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((160, 140, 104), 0.28, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((90, 60, 10), 0.060, 68),
            "sheen": ((255, 248, 225), 0.08),
            "saturation": 1.3,
            "brand": (128, 72, 22),
            "accent": (150, 92, 28),
            "band": (110, 84, 44),
            "deep": (38, 26, 8),
        },
    },
    "emerald": {
        "label": "Emerald",
        "swatch": (26, 98, 78),
        "night": {
            "base": (6, 21, 18),
            "glows": (
                ((24, 150, 112), 0.17, _TOP, _TOP_R, 0.62),
                ((116, 200, 168), 0.07, _RIGHT, _RIGHT_R, 0.62),
                ((14, 112, 104), 0.15, _LEFT, _LEFT_R, 0.64),
                ((1, 6, 5), 0.58, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((220, 255, 240), 0.053, 68),
            "saturation": 1.2,
            "brand": (124, 206, 172),
            "accent": (212, 172, 92),
        },
        "day": {
            "base": (206, 222, 214),
            "glows": (
                ((80, 190, 150), 0.22, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((176, 230, 206), 0.22, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((230, 214, 176), 0.14, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((104, 140, 126), 0.28, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((10, 70, 50), 0.060, 68),
            "saturation": 1.3,
            "brand": (12, 104, 78),
            "accent": (10, 104, 78),
            "band": (30, 86, 68),
            "deep": (4, 34, 26),
        },
    },
    "obsidian": {
        "label": "Obsidian",
        "swatch": (34, 34, 38),
        "night": {
            "base": (11, 11, 13),
            "glows": (
                ((214, 222, 236), 0.09, _TOP, _TOP_R, 0.62),
                ((226, 190, 120), 0.06, _RIGHT, _RIGHT_R, 0.62),
                ((120, 134, 160), 0.07, _LEFT, _LEFT_R, 0.64),
                ((0, 0, 0), 0.62, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((255, 255, 255), 0.045, 68),
            "sheen": ((255, 255, 255), 0.025),
            "saturation": 1.1,
            "brand": (214, 192, 152),
            "accent": (206, 166, 94),
        },
        "day": {
            "base": (214, 215, 220),
            "glows": (
                ((255, 255, 255), 0.40, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((228, 208, 166), 0.14, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((192, 198, 212), 0.20, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((136, 140, 152), 0.32, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((20, 20, 30), 0.053, 68),
            "sheen": ((255, 255, 255), 0.10),
            "saturation": 1.1,
            "brand": (128, 98, 40),
            "accent": (24, 24, 27),
            "band": (52, 54, 62),
            "deep": (17, 17, 20),
        },
    },
    "amethyst": {
        "label": "Amethyst",
        "swatch": (86, 62, 146),
        "night": {
            "base": (18, 12, 32),
            "glows": (
                ((140, 98, 220), 0.18, _TOP, _TOP_R, 0.62),
                ((200, 130, 226), 0.09, _RIGHT, _RIGHT_R, 0.62),
                ((90, 96, 210), 0.15, _LEFT, _LEFT_R, 0.64),
                ((6, 3, 12), 0.56, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((240, 228, 255), 0.060, 68),
            "saturation": 1.25,
            "brand": (190, 178, 245),
            # A soft periwinkle with dark text, like the primary buttons of
            # the design reference -- not a shout of colour.
            "accent": (176, 162, 238),
        },
        "day": {
            "base": (220, 214, 234),
            "glows": (
                ((178, 140, 236), 0.24, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((226, 184, 238), 0.18, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((176, 186, 240), 0.16, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((136, 120, 164), 0.28, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((50, 20, 90), 0.060, 68),
            "saturation": 1.3,
            "brand": (92, 48, 178),
            "accent": (104, 66, 196),
            "band": (76, 56, 122),
            "deep": (28, 12, 50),
        },
    },
    "rose": {
        "label": "Rose Gold",
        "swatch": (168, 116, 104),
        "night": {
            "base": (26, 17, 19),
            "glows": (
                ((222, 156, 136), 0.14, _TOP, _TOP_R, 0.62),
                ((246, 210, 196), 0.07, _RIGHT, _RIGHT_R, 0.62),
                ((190, 106, 116), 0.12, _LEFT, _LEFT_R, 0.64),
                ((8, 4, 5), 0.58, _FOOT, _FOOT_R, 0.66),
            ),
            "grid": ((255, 235, 228), 0.053, 68),
            "sheen": ((255, 222, 210), 0.035),
            "saturation": 1.2,
            "brand": (232, 184, 168),
            "accent": (218, 150, 128),
        },
        "day": {
            "base": (232, 220, 216),
            "glows": (
                ((230, 166, 146), 0.24, (0.50, -0.10), (0.92, 0.62), 0.62),
                ((248, 214, 198), 0.22, (0.92, 0.22), (0.72, 0.58), 0.62),
                ((226, 194, 204), 0.14, (0.02, 0.88), (0.78, 0.58), 0.64),
                ((164, 130, 124), 0.28, (0.50, 1.20), (1.20, 0.62), 0.66),
            ),
            "grid": ((90, 40, 30), 0.060, 68),
            "sheen": ((255, 244, 238), 0.08),
            "saturation": 1.3,
            "brand": (146, 78, 60),
            "accent": (160, 82, 62),
            "band": (116, 70, 60),
            "deep": (42, 20, 16),
        },
    },
}

# Black and white, for a private tab (browser_tab.py puts the window in it
# while one is showing). Not one you pick: it isn't in ORDER.
PALETTES["mono"] = {
    "label": "Private",
    "swatch": (128, 128, 128),
    "night": {
        "base": (12, 12, 13),
        "glows": (
            ((236, 236, 238), 0.10, _TOP, _TOP_R, 0.62),
            ((176, 176, 180), 0.07, _RIGHT, _RIGHT_R, 0.62),
            ((132, 132, 136), 0.06, _LEFT, _LEFT_R, 0.64),
            ((0, 0, 0), 0.62, _FOOT, _FOOT_R, 0.66),
        ),
        "grid": ((255, 255, 255), 0.045, 68),
        "sheen": ((255, 255, 255), 0.02),
        "saturation": 1.0,
        "brand": (228, 228, 230),
        "accent": (238, 238, 240),
    },
    "day": {
        "base": (224, 224, 226),
        "glows": (
            ((255, 255, 255), 0.42, (0.50, -0.10), (0.92, 0.62), 0.62),
            ((206, 206, 210), 0.18, (0.92, 0.22), (0.72, 0.58), 0.62),
            ((190, 190, 194), 0.18, (0.02, 0.88), (0.78, 0.58), 0.64),
            ((132, 132, 138), 0.30, (0.50, 1.20), (1.20, 0.62), 0.66),
        ),
        "grid": ((20, 20, 24), 0.052, 68),
        "saturation": 1.0,
        "brand": (44, 44, 48),
        "accent": (34, 34, 38),
        "band": (62, 62, 66),
        "deep": (18, 18, 20),
    },
}
MONO = "mono"

DEFAULT = "sapphire"
ORDER = ("sapphire", "ruby", "gold", "emerald", "obsidian", "amethyst", "rose")

_current = DEFAULT
_token_cache = {}


def current():
    return _current


def set_current(name):
    """Switches the palette. Returns True when it actually changed."""
    global _current
    name = name if name in PALETTES else DEFAULT
    if name == _current:
        return False
    _current = name
    _token_cache.clear()
    return True


def label(name):
    return PALETTES.get(name, PALETTES[DEFAULT])["label"]


def spec(dark, name=None):
    pal = PALETTES.get(name or _current, PALETTES[DEFAULT])
    return pal["night" if dark else "day"]


def rig(dark):
    """The backdrop's light rig for cinema.render_backdrop."""
    s = spec(dark)
    return {"base": s["base"], "glows": s["glows"], "grid": s["grid"],
            "dither": 3 if dark else 2, "sheen": s.get("sheen"),
            "saturation": s.get("saturation", 1.8)}


def ink(dark):
    """The flat field colour: the solid backdrop mode, and anything with no
    backdrop to frost."""
    return spec(dark)["base"]


def chrome_tint(dark):
    """The tint the title bar's glass capsules lay over their frost."""
    if not dark:
        return (255, 255, 255, 56)
    if _current == DEFAULT:
        return (9, 17, 42, 120)
    return _mix(spec(True)["base"], _BLACK, 0.45) + (120,)


def band(dark):
    """The title band: (tint top rgba, tint bottom rgba, rim, seam, shadow)."""
    s = spec(dark)
    if dark:
        base = s["base"]
        top, bottom = _mix(base, _BLACK, 0.80), _mix(base, _BLACK, 0.66)
        seam = _mix(base, _BLACK, 0.90)
        return (top + (128,), bottom + (96,), (255, 255, 255, 22), seam + (120,), seam + (64,))
    tone = s["band"]
    return (tone + (48,), tone + (30,), (255, 255, 255, 150), tone + (44,), tone + (26,))


def window_border(dark):
    """The 1px rim Windows draws round a floating window, as an rgb."""
    if _current == DEFAULT:
        return (30, 42, 74) if dark else (180, 192, 210)
    base = spec(dark)["base"]
    return _mix(base, _WHITE, 0.08) if dark else _mix(base, _BLACK, 0.12)


def page_background(dark):
    """What a web page shows before it has painted."""
    base = spec(dark)["base"]
    return _mix(base, _BLACK, 0.70) if dark else _mix(base, _WHITE, 0.60)


def swatch(name):
    """The one colour the palette picker shows for a palette."""
    return PALETTES.get(name, PALETTES[DEFAULT])["swatch"]


def token_overrides(dark):
    """What this palette changes in theme.DARK / theme.LIGHT."""
    key = (_current, bool(dark))
    cached = _token_cache.get(key)
    if cached is not None:
        return cached
    if _current == DEFAULT:
        # The original look, except that its field was deepened.
        result = {"ink": _hex(spec(dark)["base"])} if dark else {}
    elif dark:
        result = _night_tokens(spec(True))
    else:
        result = _day_tokens(spec(False))
    _token_cache[key] = result
    return result


def _night_tokens(s):
    base, brand, accent = s["base"], s["brand"], s["accent"]
    text = _mix(_WHITE, brand, 0.07)
    line = _mix(_WHITE, brand, 0.18)
    return {
        "ink": _hex(base),
        "card_bg_solid": _rgba(_mix(base, _WHITE, 0.06), 248),
        "card_border": _rgba(line, 28),
        "divider": _rgba(line, 18),
        "text": _hex(text),
        "text_muted": _hex(_mix(text, base, 0.30)),
        "text_faint": _hex(_mix(text, base, 0.47)),
        "eyebrow": _hex(brand),
        "brand": _hex(brand),
        "brand_hover": _hex(_mix(brand, _WHITE, 0.35)),
        "brand_text": _hex(_mix(brand, _BLACK, 0.86)),
        "accent": _hex(accent),
        "accent_hover": _hex(_mix(accent, _WHITE, 0.16)),
        "accent_pressed": _hex(_mix(accent, _BLACK, 0.12)),
        "accent_top": _hex(_mix(accent, _WHITE, 0.28)),
        "accent_text": _hex(_mix(accent, _BLACK, 0.90)),
        "progress": _hex(accent),
        "field_bg": _rgba(_mix(base, _BLACK, 0.62), 118),
        "focus": _rgba(brand, 210),
        "selection": _rgba(brand, 105),
    }


def _day_tokens(s):
    base, brand, accent, deep = s["base"], s["brand"], s["accent"], s["deep"]
    return {
        "ink": _hex(base),
        "card_bg_solid": _rgba(_mix(base, _WHITE, 0.62), 250),
        "card_border": _rgba(deep, 30),
        "divider": _rgba(deep, 20),
        "text": _hex(deep),
        "text_muted": _hex(_mix(deep, base, 0.36)),
        "text_faint": _hex(_mix(deep, base, 0.50)),
        "eyebrow": _hex(brand),
        "brand": _hex(brand),
        "brand_hover": _hex(_mix(brand, _BLACK, 0.16)),
        "brand_text": "#ffffff",
        "accent": _hex(accent),
        "accent_hover": _hex(_mix(accent, _BLACK, 0.10)),
        "accent_pressed": _hex(_mix(accent, _BLACK, 0.20)),
        "accent_top": _hex(_mix(accent, _WHITE, 0.14)),
        "accent_text": "#ffffff",
        "progress": _hex(accent),
        "hover_overlay": _rgba(deep, 14),
        "pressed_overlay": _rgba(deep, 26),
        "field_border": _rgba(deep, 34),
        "field_hover": _rgba(deep, 60),
        "focus": _rgba(brand, 200),
        "selection": _rgba(brand, 80),
        "scroll_handle": _rgba(deep, 40),
        "scroll_handle_hover": _rgba(deep, 70),
    }
