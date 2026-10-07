"""The Music tab's look: a page that takes its colours from the album cover.

The design language (docs/music-design.md has the long form):

  * Every page is tinted by one picture -- the album's cover, the artist's
    photo, or the song playing. Its colours are read off the picture
    (palette_from_image: median-cut swatches scored the way Android's Palette
    scores them) and the page is painted in them: a deep-to-lit gradient, a
    glow of the picture's second colour, and the picture itself on the right,
    melting into the page (blend_art).
  * Film, not glass: a fine grain, a few specks of dust and soft light leaks
    over the colour (paint_backdrop), so a flat gradient reads as a printed
    poster rather than a screen.
  * Type does the work: names in Inter Black, upper case, as large as the
    space allows (display_font); everything else small, white, at three
    strengths (TEXT, TEXT_2, TEXT_3). Text is always white -- the palette's
    colours are darkened until white reads on them (contrast >= 4.5:1).
  * A dark, quiet rail on the left (the library and the moods) and the player
    along the bottom, tinted by the song playing.
"""
import colorsys
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)

# --------------------------------------------------------------- tokens ----
TEXT = QColor(255, 255, 255)
TEXT_2 = QColor(255, 255, 255, 190)     # 75 % -- secondary lines
TEXT_3 = QColor(255, 255, 255, 135)     # 53 % -- labels, numbers, times
HAIR = QColor(255, 255, 255, 26)
HOVER = QColor(255, 255, 255, 20)
CURRENT = QColor(255, 255, 255, 34)

RAIL_BG = QColor("#101116")
RAIL_TEXT = QColor("#e9eaef")
RAIL_MUTED = QColor("#9a9eab")
RAIL_LABEL = QColor("#5d6170")
RAIL_FIELD = QColor("#1b1d24")

RAIL_W = 224
BAR_H = 78
ROW_H = 50
TILE_MIN, TILE_MAX = 148, 196
GUTTER = 36          # a page's side margin

# ChromeButton tokens for icons on a tinted page: white, at two strengths.
ON_COLOUR = {
    "text": "#ffffff",
    "text_muted": "rgba(255, 255, 255, 185)",
    "text_faint": "rgba(255, 255, 255, 90)",
    "hover_overlay": "rgba(255, 255, 255, 30)",
    "pressed_overlay": "rgba(255, 255, 255, 48)",
    "card_bg_solid": "rgba(16, 17, 22, 250)",
    "brand": "#ffffff",
    "brand_text": "#000000",
}

_family = None


def family():
    """Inter, as the app bundles it (theme.load_custom_fonts)."""
    global _family
    if _family is None:
        names = set(QFontDatabase.families())
        _family = next((f for f in ("Inter", "Inter Variable", "Segoe UI Variable Display", "Segoe UI")
                        if f in names), "Segoe UI")
    return _family


def font(px, weight=QFont.Weight.Normal, spacing=None):
    f = QFont(family())
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(weight)
    if spacing is not None:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def display_font(px):
    """The names: Inter Black, tracked a touch tight."""
    return font(px, QFont.Weight.Black, spacing=-px * 0.012)


def label_font(px=11):
    """Section labels and overlines: small caps, widely tracked."""
    return font(px, QFont.Weight.Bold, spacing=px * 0.16)


# -------------------------------------------------------------- palette ----
def _hsl(h, s, l, a=255):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return QColor(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), a)


def luminance(c):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _readable(h, s, l, against=TEXT, ratio=4.6):
    """hsl(h, s, l), darkened until `against` (white) reads on it."""
    c = _hsl(h, s, l)
    while l > 0.05 and contrast(c, against) < ratio:
        l -= 0.02
        c = _hsl(h, s, l)
    return c


def make_palette(h, s, h2=None, s2=None, vh=None, vs=None, neutral=False):
    """The page's colours from a hue (and a second hue for the glow)."""
    h2 = h if h2 is None else h2
    s2 = s if s2 is None else s2
    vh = h if vh is None else vh
    vs = s if vs is None else vs
    if neutral:
        s = s2 = min(s, 0.08)
        vs = 0.0
    sb = s if neutral else max(0.30, min(0.80, s))
    base = _readable(h, sb, 0.38)
    deep = _hsl(h, min(0.9, sb + 0.06), 0.105)
    glow = _hsl(h2, s2 if neutral else max(0.40, min(0.92, s2 + 0.10)), 0.56)
    accent = QColor(236, 238, 245) if neutral else _hsl(vh, max(0.62, vs), 0.70)
    ink = QColor(10, 11, 16) if luminance(accent) > 0.35 else QColor(255, 255, 255)
    return {"base": base, "deep": deep, "glow": glow, "accent": accent, "ink": ink}


# The app's own colours, for a page with no picture: night navy lit sky blue.
DEFAULT = make_palette(0.62, 0.55, h2=0.55, s2=0.85, vh=0.55, vs=0.9)


def _swatches(img, size=48, colors=10):
    from PIL import Image
    small = img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
    pil = Image.frombuffer("RGB", (size, size), bytes(small.constBits()), "raw", "RGB", small.bytesPerLine(), 1)
    q = pil.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    total = float(size * size)
    out = []
    for count, idx in q.getcolors() or []:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        out.append({"pop": count / total, "h": h, "s": s, "l": l, "rgb": (r, g, b)})
    return out


def _best(swatches, ts, tl, smin=0.0, smax=1.0, lmin=0.0, lmax=1.0):
    """Android Palette's target scoring: closeness in saturation and
    lightness, and how much of the picture the colour covers."""
    pool = [w for w in swatches if smin <= w["s"] <= smax and lmin <= w["l"] <= lmax and w["pop"] >= 0.015]
    if not pool:
        return None
    top = max(w["pop"] for w in pool)
    return max(pool, key=lambda w: 0.24 * (1 - abs(w["s"] - ts)) + 0.52 * (1 - abs(w["l"] - tl))
               + 0.24 * (w["pop"] / top))


def _chroma(w):
    return w["s"] * (1 - abs(2 * w["l"] - 1))


def palette_from_image(img):
    """The page colours for a picture (a QImage); DEFAULT without one."""
    if img is None or img.isNull():
        return DEFAULT
    try:
        sw = _swatches(img)
    except Exception:   # noqa: BLE001 -- no Pillow, an odd format: the app's own colours
        return DEFAULT
    if not sw:
        return DEFAULT
    vibrant = (_best(sw, 1.0, 0.5, smin=0.35, lmin=0.25, lmax=0.78)
               or _best(sw, 1.0, 0.26, smin=0.35, lmax=0.45)
               or _best(sw, 1.0, 0.74, smin=0.35, lmin=0.55))
    dominant = max(sw, key=lambda w: w["pop"])
    colourful = [w for w in sw if _chroma(w) > 0.10 and w["pop"] >= 0.02]
    if not colourful:
        # black and white, sepia, grey: a quiet page, in the picture's own slight cast
        return make_palette(dominant["h"], dominant["s"], neutral=True)
    # the hue that covers the most of the picture, weighted by how colourful it is
    key = max(colourful, key=lambda w: w["pop"] * (0.35 + _chroma(w)))
    second = max((w for w in colourful if _hue_gap(w["h"], key["h"]) > 0.07),
                 key=lambda w: w["pop"] * _chroma(w), default=None)
    v = vibrant or key
    return make_palette(key["h"], key["s"], h2=(second or key)["h"], s2=(second or key)["s"],
                        vh=v["h"], vs=v["s"])


def _hue_gap(a, b):
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def mix(a, b, t):
    """Colour a -> b at t (0..1)."""
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def mix_palette(a, b, t):
    return {k: mix(a[k], b[k], t) for k in a}


def with_alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


# ------------------------------------------------------------- backdrop ----
_grain = None


def grain_tile():
    """Film grain: a 160 px tile of soft monochrome specks."""
    global _grain
    if _grain is None:
        size, strength = 160, 30
        rng = random.Random(1972)
        buf = bytearray(size * size * 4)
        for i in range(0, len(buf), 4):
            v = max(-1.0, min(1.0, rng.gauss(0.0, 0.45)))
            a = int(abs(v) * strength)
            if v > 0:                      # premultiplied white; dark specks stay colour 0
                buf[i] = buf[i + 1] = buf[i + 2] = a
            buf[i + 3] = a
        _grain = QImage(bytes(buf), size, size, size * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    return _grain


_DUST = None


def _dust():
    global _DUST
    if _DUST is None:
        rng = random.Random(4417)
        specks = [(rng.random(), rng.random(), rng.uniform(0.5, 1.5), rng.uniform(0.08, 0.32)) for _ in range(46)]
        hairs = [(rng.uniform(0.04, 0.96), rng.uniform(0.1, 0.9), rng.uniform(0.02, 0.06), rng.uniform(-0.6, 0.6))
                 for _ in range(4)]
        _DUST = (specks, hairs)
    return _DUST


def paint_grain(p, rect, opacity=1.0):
    p.save()
    p.setOpacity(opacity)
    p.drawTiledPixmap(rect, QPixmap.fromImage(grain_tile()))
    p.restore()


def paint_backdrop(p, rect, pal, leaks=True):
    """The page: deep in the lower left, lit in the upper right where the
    picture sits, its second colour glowing there, light leaking in along
    the left, grain and dust over all of it."""
    w, h = rect.width(), rect.height()
    x0, y0 = rect.left(), rect.top()
    g = QLinearGradient(QPointF(x0, y0 + h), QPointF(x0 + w, y0))
    g.setColorAt(0.0, pal["deep"])
    g.setColorAt(0.55, mix(pal["deep"], pal["base"], 0.72))
    g.setColorAt(1.0, pal["base"])
    p.fillRect(rect, g)

    glow = QRadialGradient(QPointF(x0 + w * 0.80, y0 + h * 0.16), max(w, h) * 0.62)
    glow.setColorAt(0.0, with_alpha(pal["glow"], 0.50))
    glow.setColorAt(0.45, with_alpha(pal["glow"], 0.16))
    glow.setColorAt(1.0, with_alpha(pal["glow"], 0.0))
    p.fillRect(rect, glow)

    if leaks:
        for cx, width, alpha in ((0.05, 0.05, 0.10), (0.12, 0.018, 0.12), (0.21, 0.04, 0.07)):
            lg = QLinearGradient(QPointF(x0 + w * (cx - width), 0), QPointF(x0 + w * (cx + width), 0))
            lg.setColorAt(0.0, with_alpha(pal["glow"], 0.0))
            lg.setColorAt(0.5, with_alpha(mix(pal["glow"], TEXT, 0.35), alpha))
            lg.setColorAt(1.0, with_alpha(pal["glow"], 0.0))
            p.fillRect(QRectF(x0 + w * (cx - width), y0, w * width * 2, h), lg)

    # the lower page darkens, so lists read over it
    shade = QLinearGradient(QPointF(0, y0 + h * 0.38), QPointF(0, y0 + h))
    shade.setColorAt(0.0, with_alpha(pal["deep"], 0.0))
    shade.setColorAt(1.0, with_alpha(pal["deep"], 0.82))
    p.fillRect(rect, shade)

    vig = QRadialGradient(QPointF(x0 + w * 0.5, y0 + h * 0.42), max(w, h) * 0.85)
    vig.setColorAt(0.55, QColor(0, 0, 0, 0))
    vig.setColorAt(1.0, QColor(0, 0, 0, 92))
    p.fillRect(rect, vig)

    paint_grain(p, rect, 0.85)
    specks, hairs = _dust()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for fx, fy, r, a in specks:
        p.setBrush(QColor(255, 255, 255, int(255 * a)))
        p.drawEllipse(QPointF(x0 + fx * w, y0 + fy * h), r, r)
    for fx, fy, length, bend in hairs:
        path = QPainterPath(QPointF(x0 + fx * w, y0 + fy * h))
        path.quadTo(QPointF(x0 + fx * w + bend * 14, y0 + (fy + length / 2) * h),
                    QPointF(x0 + fx * w + bend * 6, y0 + (fy + length) * h))
        p.setPen(QPen(QColor(255, 255, 255, 26), 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.restore()


def render_backdrop(w, h, pal, dpr=1.0):
    pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_backdrop(p, QRectF(0, 0, w, h), pal)
    p.end()
    return pm


# ---------------------------------------------------------- the picture ----
def blend_art(pix, w, h, pal, wide=False, dpr=1.0):
    """The picture on the right of a page, melting into it: faded in from
    the left and out at the bottom, warmed toward the page's colour, with
    the same grain over it. `wide` for an artist's banner (cover the right
    two thirds), else a square cover (as tall as the page)."""
    out = QImage(max(1, int(w * dpr)), max(1, int(h * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    if pix is None or pix.isNull() or w < 2 or h < 2:
        return QPixmap.fromImage(out)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pw, ph = pix.width(), pix.height()
    if wide:
        area = QRectF(w * 0.26, 0, w * 0.74, h)
    else:
        side = min(h * 1.04, w * 0.62)
        area = QRectF(w - side * 0.94, -h * 0.02, side, side)
    # cover-fit the picture into `area`
    scale = max(area.width() / pw, area.height() / ph)
    sw, sh = area.width() / scale, area.height() / scale
    # a wide banner's subject usually stands right of centre
    src = QRectF((pw - sw) * (0.58 if wide else 0.5), (ph - sh) * (0.30 if wide else 0.5), sw, sh)
    p.drawPixmap(area, pix, src)
    # warmed into the page: the page's colour, softly over the picture
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    tint = QLinearGradient(area.topLeft(), area.bottomLeft())
    tint.setColorAt(0.0, with_alpha(pal["base"], 0.10))
    tint.setColorAt(1.0, with_alpha(pal["deep"], 0.42))
    p.fillRect(area, tint)
    # melt the edges: in from the left, out at the bottom, a little at the top
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    fade_w = area.width() * (0.58 if wide else 0.52)
    hg = QLinearGradient(QPointF(area.left(), 0), QPointF(area.left() + fade_w, 0))
    hg.setColorAt(0.0, QColor(0, 0, 0, 0))
    hg.setColorAt(0.55, QColor(0, 0, 0, 150))
    hg.setColorAt(1.0, QColor(0, 0, 0, 255))
    p.fillRect(QRectF(0, 0, w, h), hg)
    vg = QLinearGradient(QPointF(0, 0), QPointF(0, h))
    vg.setColorAt(0.0, QColor(0, 0, 0, 120))
    vg.setColorAt(0.10, QColor(0, 0, 0, 255))
    vg.setColorAt(0.62, QColor(0, 0, 0, 255))
    vg.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(0, 0, w, h), vg)
    # the right edge, so a cover never ends in a hard line against the page
    rg = QLinearGradient(QPointF(w - 28, 0), QPointF(w, 0))
    rg.setColorAt(0.0, QColor(0, 0, 0, 255))
    rg.setColorAt(1.0, QColor(0, 0, 0, 120))
    p.fillRect(QRectF(w - 28, 0, 28, h), rg)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    p.drawTiledPixmap(QRectF(0, 0, w, h), QPixmap.fromImage(grain_tile()))
    p.end()
    return QPixmap.fromImage(out)


def rounded(pix, w, h, radius, circle=False):
    """`pix` cover-cropped to w x h with rounded corners (or a circle)."""
    if pix is None or pix.isNull():
        return None
    dpr = pix.devicePixelRatio() or 1.0
    out = QPixmap(int(w * 2), int(h * 2))
    out.setDevicePixelRatio(2.0)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    if circle:
        path.addEllipse(QRectF(0, 0, w, h))
    else:
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    pw, ph = pix.width() / dpr, pix.height() / dpr
    scale = max(w / pw, h / ph)
    sw, sh = w / scale, h / scale
    p.drawPixmap(QRectF(0, 0, w, h), pix, QRectF((pw - sw) / 2 * dpr, (ph - sh) / 2 * dpr, sw * dpr, sh * dpr))
    p.end()
    return out


def placeholder(p, rect, pal, radius=6, circle=False, glyph=True):
    """Where a cover will be: the page's colour, a note on it."""
    from .browser_chrome import draw_icon
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, with_alpha(mix(pal["base"], TEXT, 0.12), 1.0))
    g.setColorAt(1, pal["deep"])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    if circle:
        p.drawEllipse(rect)
    else:
        p.drawRoundedRect(rect, radius, radius)
    if glyph:
        s = min(rect.width(), rect.height()) * 0.36
        draw_icon(p, "music", QRectF(rect.center().x() - s / 2, rect.center().y() - s / 2, s, s),
                  with_alpha(TEXT, 0.45))
    p.restore()


def fit_display(text, width, max_px, min_px, lines=2):
    """The largest display size (<= max_px) at which `text` fits in `width`
    on at most `lines` lines -- and those lines."""
    from PySide6.QtGui import QFontMetricsF
    words = text.split()
    px = max_px
    while True:
        fm = QFontMetricsF(display_font(px))
        out, cur = [], ""
        for word in words:
            trial = (cur + " " + word).strip()
            if fm.horizontalAdvance(trial) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
        fits = len(out) <= lines and all(fm.horizontalAdvance(x) <= width for x in out)
        if fits or px <= min_px:
            if not fits:
                # still too long at the smallest size: the last line ends in an ellipsis
                out = out[:lines]
                out[-1] = fm.elidedText(out[-1] + "…", Qt.TextElideMode.ElideRight, width)
            return px, out
        px -= 2


def ease(t):
    return 1 - (1 - t) ** 3


def equalizer(p, rect, colour, phase, playing=True):
    """Three bars, bouncing while the song plays."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    bw = rect.width() / 5.0
    for i in range(3):
        k = (0.35 + 0.65 * abs(math.sin(phase * (1.3 + i * 0.47) + i * 1.7))) if playing else (0.35, 0.7, 0.5)[i]
        bh = rect.height() * k
        p.drawRoundedRect(QRectF(rect.left() + i * bw * 2, rect.bottom() - bh, bw, bh), bw / 2, bw / 2)
    p.restore()
