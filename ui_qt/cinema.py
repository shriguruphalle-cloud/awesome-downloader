"""The window's backdrop, and the glass that sits on it.

The look follows the product's own website (awesome-downloader.pages.dev),
so the app and the page people download it from read as one thing:

  * A deep navy field (#050A18) with a faint 68px grid, the way the site's
    hero has one -- strongest at the top and fading out down the window, so
    it gives the page structure without turning into graph paper.
  * Three soft lights, the site's own: sky blue from above the top centre,
    indigo from the upper right, a deeper sky from the lower left. Blue is
    the identity colour -- it is the colour the logo glows.
  * Glass panels. Each one paints a blurred, colour-boosted copy of the
    backdrop behind itself (the site's `backdrop-filter: blur(22px)
    saturate(1.8)`), then 5% white, a hairline border, and a brighter 1px
    edge along the top where light catches it. Boosting the colour of what
    is behind the glass is what makes it read as *blue* glass rather than a
    grey one: the lights come through it richer than they are around it.

Why painted and not the Windows acrylic blur: acrylic is what 2.0-2.4 used,
and it is the source of most of the window bugs in their history -- ghosting
between tabs, a washed-out window whenever it was maximized (DWM stops
blurring a maximized window), a desynced compositor next to the Browser tab's
Chromium surface. A backdrop the app paints is opaque, identical on every
machine and in every window state, and cheap to composite. The acrylic look
is still one setting away (Settings > Appearance > Backdrop).
"""
import os
import random

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient, QTransform,
)

from . import palettes

# Backdrop modes (settings["backdrop"]).
CINEMATIC = "cinematic"
DESKTOP = "desktop"      # real Windows acrylic, see-through to the desktop
SOLID = "solid"          # flat ink: no glows, no glass sampling -- "reduce transparency"
MODES = (CINEMATIC, DESKTOP, SOLID)

# How far the glass copy is shrunk before being scaled back up. Larger is a
# softer frost; 14 is close to the site's 22px blur at normal window sizes
# without the bilinear upscale showing its grid.
_BLUR_DIVISOR = 14
# The site's saturate(1.8): how much richer the lights are through glass.
_FROST_SATURATION = 1.8

# The light rig -- the Sapphire palette's, kept here as the reference the
# others in palettes.py were drawn against; render_backdrop() reads the
# current palette's. Positions and sizes are fractions of the window, as the
# site's CSS radial gradients are fractions of the viewport, so the
# composition holds at any size.
#   glow: (rgb, peak alpha 0-1, centre (x, y), radii (x, y), fades out at)
_RIG = {
    True: {  # night
        # A lit ocean-navy. 2.5's first cut used the website's near-black
        # #050A18, which on a desktop window read as simply dark rather than
        # blue; this keeps the same hue family with light in it.
        "base": (16, 28, 62),
        # The site's three lights, a third brighter: a desktop window is far
        # smaller than the browser viewport they were tuned on, so less of
        # each glow is ever in view.
        "glows": (
            ((56, 189, 248), 0.30, (0.50, -0.08), (0.95, 0.66), 0.62),
            ((129, 140, 248), 0.28, (0.90, 0.22), (0.72, 0.58), 0.62),
            ((14, 165, 233), 0.22, (0.04, 0.74), (0.82, 0.62), 0.64),
            ((6, 12, 32), 0.45, (0.50, 1.20), (1.20, 0.60), 0.66),
        ),
        # (line rgb, alpha 0-1, spacing px)
        "grid": ((255, 255, 255), 0.05, 68),
        "dither": 3,
    },
    False: {  # pearl
        # A mid-tone pearl, not paper white: 2.5's first light theme was a
        # near-white field under near-white glass, which read as glare with
        # nothing separating one surface from the next. Blue-grey with a lilac
        # cast, the same sky and indigo lights, a faint warm light low on the
        # left (the colour play that makes pearl pearl), and a shade settling
        # toward the bottom so the window has depth rather than a flat wash.
        "base": (184, 197, 224),
        "glows": (
            ((104, 168, 246), 0.42, (0.50, -0.10), (0.92, 0.62), 0.62),
            ((160, 146, 244), 0.36, (0.92, 0.22), (0.72, 0.58), 0.62),
            ((244, 184, 168), 0.24, (0.02, 0.88), (0.78, 0.58), 0.64),
            ((120, 136, 178), 0.34, (0.50, 1.20), (1.20, 0.62), 0.66),
        ),
        "grid": ((30, 50, 100), 0.05, 68),
        "dither": 2,
    },
}
# The grid's fade: an ellipse centred on the top edge, strongest there. The
# site's own mask (125% x 95%, gone by 78%) left it showing only in a band
# under the title bar on a desktop window (reported: "only visible at the
# top"); this one carries it down the whole window, fading gently.
_GRID_MASK = ((0.5, 0.0), (1.45, 1.65), 0.32, 1.0)

class _Ink:
    """Flat ink for SOLID mode, and for a panel with nothing behind it to
    frost: INK[dark] -> the current palette's field colour."""

    def __getitem__(self, dark):
        return QColor(*palettes.ink(bool(dark)))


INK = _Ink()


# ------------------------------------------------------------- dither ----
_noise_tiles = {}


def _noise_tile(strength):
    """A 128px tile of faint monochrome noise. Not a visible grain: at this
    strength it only breaks up the 8-bit steps a wide, dark, low-contrast
    gradient otherwise shows as bands."""
    tile = _noise_tiles.get(strength)
    if tile is not None:
        return tile
    size = 128
    rng = random.Random(1895)  # fixed: must not reshuffle on every resize
    buf = bytearray(size * size * 4)
    for i in range(0, len(buf), 4):
        v = rng.random() * 2.0 - 1.0
        a = int(round(abs(v) * strength))
        if v > 0:  # premultiplied white; black specks keep colour 0
            buf[i] = buf[i + 1] = buf[i + 2] = a
        buf[i + 3] = a
    img = QImage(bytes(buf), size, size, size * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    _noise_tiles[strength] = img
    return img


# ----------------------------------------------------------- painting ----
def _smooth_fade(t):
    """1 at the centre to 0 at the edge, flat at both ends -- a CSS linear
    fade has a visible point at its centre on a dark field."""
    t = min(max(t, 0.0), 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def _ellipse_glow(p, w, h, color, alpha, centre, radii, fade_at):
    cx, cy = w * centre[0], h * centre[1]
    rx, ry = max(1.0, w * radii[0]), max(1.0, h * radii[1])
    g = QRadialGradient(QPointF(0, 0), 1.0)
    for i in range(9):
        t = i / 8.0
        c = QColor(*color)
        c.setAlphaF(max(0.0, min(1.0, alpha * _smooth_fade(t / fade_at))) if t < fade_at else 0.0)
        g.setColorAt(t, c)
    p.save()
    p.translate(cx, cy)
    p.scale(rx, ry)
    p.setPen(Qt.PenStyle.NoPen)
    p.fillRect(QRectF(-cx / rx, -cy / ry, w / rx, h / ry), QBrush(g))
    p.restore()


def _sheen(p, w, h, color, alpha):
    """A soft diagonal fall of light across the field, the way light lies
    across satin or brushed metal: two wide, faint bands, the second
    fainter, running from the upper left toward the lower right."""
    g = QLinearGradient(QPointF(w * 0.05, 0), QPointF(w * 0.80, h))
    stops = ((0.00, 0.0), (0.18, 0.0), (0.30, 1.0), (0.42, 0.0),
             (0.52, 0.0), (0.60, 0.45), (0.68, 0.0), (1.00, 0.0))
    for t, k in stops:
        c = QColor(*color)
        c.setAlphaF(alpha * k)
        g.setColorAt(t, c)
    p.fillRect(0, 0, w, h, QBrush(g))


def _grid_layer(w, h, dpr, color, alpha, spacing):
    """The grid, already faded by its mask, as its own layer. Lines are
    drawn on whole device pixels so they stay a crisp hairline at any
    display scaling; one vertical line runs through the window's centre so
    the grid sits symmetrically under the centred content."""
    layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    p = QPainter(layer)
    line = QColor(*color)
    line.setAlphaF(alpha)
    step = spacing * dpr
    width = max(1, int(round(dpr)))
    x = (w / 2.0) % step
    while x < w:
        p.fillRect(int(round(x)), 0, width, h, line)
        x += step
    y = 0.0
    while y < h:
        p.fillRect(0, int(round(y)), w, width, line)
        y += step

    (mx, my), (rx, ry), solid_to, clear_at = _GRID_MASK
    cx, cy = w * mx, h * my
    rx, ry = w * rx, h * ry
    mask = QRadialGradient(QPointF(0, 0), 1.0)
    for i in range(11):
        t = i / 10.0
        k = 1.0 if t <= solid_to else (0.0 if t >= clear_at else
                                        _smooth_fade((t - solid_to) / (clear_at - solid_to)))
        mask.setColorAt(t, QColor(0, 0, 0, int(255 * k)))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.translate(cx, cy)
    p.scale(rx, ry)
    p.fillRect(QRectF(-cx / rx, -cy / ry, w / rx, h / ry), QBrush(mask))
    p.end()
    return layer


def _saturate(img, factor):
    """Boosts colour away from grey, like CSS saturate(): each channel moves
    `factor` times further from the pixel's luminance. Run on the small
    frost image only (a few thousand pixels), so plain Python is fine."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    data = bytearray(img.constBits().tobytes())
    stride = img.bytesPerLine()
    for y in range(h):
        row = y * stride
        for x in range(w):
            i = row + x * 4          # little-endian ARGB32: B, G, R, A
            b, g, r = data[i], data[i + 1], data[i + 2]
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            data[i] = max(0, min(255, int(lum + (b - lum) * factor)))
            data[i + 1] = max(0, min(255, int(lum + (g - lum) * factor)))
            data[i + 2] = max(0, min(255, int(lum + (r - lum) * factor)))
    return QImage(bytes(data), w, h, stride, QImage.Format.Format_ARGB32).copy()


# The title bar's band of dark glass, and the line that separates it from the
# page below: a rim of light along the glass's lower edge, a dark seam under
# it and a soft shadow falling onto the page. Without it the title row and
# the page were one slab of backdrop (reported, with a sketch of the band).
# Its colours come from the palette (palettes.band).
BAND_SHADOW = 12     # px the band's shadow reaches down onto the page


def paint_title_band(p, width, band, dark, glass=None, glass_size=None):
    """Paints the title band, in logical pixels, onto painter `p`.

    `glass` is the frosted backdrop (render_backdrop's small image) and
    `glass_size` the size it covers: drawn first, it is what makes the band
    glass rather than a tint -- the backdrop's grid doesn't run through it.
    Without it (the see-through and the flat modes) only the tint and the
    edge are painted."""
    if band <= 0:
        return
    top, bottom, rim, seam, shadow = palettes.band(bool(dark))
    spec = {"tint": (top, bottom), "rim": rim, "seam": seam, "shadow": shadow}
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rect = QRectF(0, 0, width, band)
    if glass is not None and not glass.isNull() and glass_size is not None:
        p.save()
        p.setClipRect(rect)
        p.drawImage(QRectF(0, 0, glass_size[0], glass_size[1]), glass)
        p.restore()
    top, bottom = spec["tint"]
    tint = QLinearGradient(0, 0, 0, band)
    tint.setColorAt(0.0, QColor(*top))
    tint.setColorAt(1.0, QColor(*bottom))
    p.fillRect(rect, QBrush(tint))
    p.fillRect(QRectF(0, band - 1, width, 1), QColor(*spec["rim"]))
    p.fillRect(QRectF(0, band, width, 1), QColor(*spec["seam"]))
    shadow = QLinearGradient(0, band + 1, 0, band + 1 + BAND_SHADOW)
    shade = QColor(*spec["shadow"])
    clear = QColor(shade)
    clear.setAlpha(0)
    shadow.setColorAt(0.0, shade)
    shadow.setColorAt(0.35, QColor(shade.red(), shade.green(), shade.blue(), shade.alpha() // 3))
    shadow.setColorAt(1.0, clear)
    p.fillRect(QRectF(0, band + 1, width, BAND_SHADOW), QBrush(shadow))
    p.restore()


def render_backdrop(width, height, dark=True, dpr=1.0, band=0):
    """Returns (sharp QPixmap at device resolution, small frosted QImage).

    `band` is the title bar's height in logical pixels (0: no title bar on
    screen), painted as a band of dark glass across the top of the sharp
    render. The frost is left without it: the title bar's own glass
    capsules sample the frost and lay their own tint on it."""
    w = max(8, int(round(width * dpr)))
    h = max(8, int(round(height * dpr)))
    rig = palettes.rig(bool(dark))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rig["base"]))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for color, alpha, centre, radii, fade_at in rig["glows"]:
        _ellipse_glow(p, w, h, color, alpha, centre, radii, fade_at)
    if rig.get("sheen"):
        _sheen(p, w, h, *rig["sheen"])
    p.fillRect(0, 0, w, h, QBrush(_noise_tile(rig["dither"])))
    p.end()

    # The frost is taken before the grid goes on: 22px of blur erases a 1px
    # line anyway, and leaving it out keeps faint ghost-stripes out of the
    # glass.
    small = img
    target_w = max(4, w // _BLUR_DIVISOR)
    target_h = max(4, h // _BLUR_DIVISOR)
    # Stepwise halving averages properly at every step; one big jump lets
    # the smooth scaler skip source pixels and the frost comes out blotchy.
    while small.width() // 2 >= target_w * 2:
        small = small.scaled(small.width() // 2, small.height() // 2,
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    small = small.scaled(target_w, target_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    # The site's saturate(1.8); the jewel palettes ask for less, so their
    # glass reads as tinted rather than as a vivid sheet of colour.
    frost = _saturate(small, rig.get("saturation", _FROST_SATURATION))

    color, alpha, spacing = rig["grid"]
    p = QPainter(img)
    p.drawImage(0, 0, _grid_layer(w, h, dpr, color, alpha, spacing))
    if band > 0:
        p.scale(dpr, dpr)
        # The plain blur, not the frost: the frost's saturation boost is for
        # small panes and turned a whole band of light theme bright blue.
        paint_title_band(p, width, band, dark, glass=small, glass_size=(width, height))
    p.end()

    sharp = QPixmap.fromImage(img)
    sharp.setDevicePixelRatio(dpr)
    return sharp, frost


# --------------------------------------------------------------- glass ----
# Per theme and tier:
#   0  panel  -- cards: the main surfaces of a page
#   1  raised -- something sitting on a panel (a queue row, a nested card)
#   2  chrome -- the nav island and the action tray in the title bar
# fill: laid over the frost.  tint: an extra wash first (the site's nav bar
# is navy glass, not white glass).  edge: the hairline border.  top/bottom:
# the 1px inner light-catch along the top edge and the shadow along the
# bottom one (the site's inset box-shadows).
_GLASS = {
    True: {
        0: {"tint": None, "fill": (255, 255, 255, 16), "edge": (255, 255, 255, 32),
            "top": (255, 255, 255, 56), "bottom": (0, 0, 0, 70)},
        1: {"tint": None, "fill": (255, 255, 255, 18), "edge": (255, 255, 255, 34),
            "top": (255, 255, 255, 40), "bottom": (0, 0, 0, 50)},
        2: {"tint": "palette", "fill": (255, 255, 255, 10), "edge": (255, 255, 255, 30),
            "top": (255, 255, 255, 46), "bottom": (0, 0, 0, 60)},
    },
    # Pearl glass is frosted, not white: under half-white over the frost,
    # a white hairline for its edge (glass catches light at its rim; a dark
    # outline read as a printed box), and the contact shadow below it
    # (paint_contact_shadow) to lift it off the field.
    False: {
        0: {"tint": None, "fill": (255, 255, 255, 74), "edge": (255, 255, 255, 140),
            "top": (255, 255, 255, 230), "bottom": (40, 60, 110, 24)},
        1: {"tint": None, "fill": (255, 255, 255, 60), "edge": (255, 255, 255, 120),
            "top": (255, 255, 255, 190), "bottom": (40, 60, 110, 18)},
        2: {"tint": (255, 255, 255, 56), "fill": (255, 255, 255, 26), "edge": (255, 255, 255, 140),
            "top": (255, 255, 255, 230), "bottom": (40, 60, 110, 20)},
    },
}


def backdrop_for(widget):
    """The window's BackdropSurface, or None (a detached widget)."""
    try:
        win = widget.window()
    except RuntimeError:
        return None
    return getattr(win, "backdrop_surface", None)


def is_dark(widget):
    try:
        return getattr(widget.window(), "dark_mode", True)
    except RuntimeError:
        return True


def glass_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass(p, widget, rect=None, radius=16.0, tier=0, dark=None, sample=True):
    """Paints one glass surface into `widget` with painter `p`.

    `sample=False` skips the frost -- for a surface that sits on another
    glass panel, where the panel below has already frosted the backdrop and
    sampling it again would cut a window straight through that panel."""
    if dark is None:
        dark = is_dark(widget)
    if rect is None:
        rect = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    spec = _GLASS[bool(dark)][tier]
    path = glass_path(rect, radius)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)

    surface = backdrop_for(widget) if sample else None
    frost = surface.frost() if surface is not None else None
    if frost is not None and not frost.isNull():
        # Where this widget sits on the backdrop, in the backdrop's own
        # coordinates -- through global space, because the title bar is a
        # sibling of the surface rather than a child of it.
        origin = surface.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
        sx = surface.width() / float(frost.width())
        sy = surface.height() / float(frost.height())
        brush = QBrush(frost)
        brush.setTransform(_frost_transform(origin, sx, sy))
        p.fillPath(path, brush)
    elif sample and tier != 1:
        # Nothing to frost: the desktop-acrylic mode, where the window is
        # see-through and the blur is DWM's, or the flat "solid" mode. A
        # denser tint keeps text legible over whatever is behind.
        if dark:
            ink = QColor(*palettes.ink(True)).darker(112)
            ink.setAlpha(165)
            p.fillPath(path, ink)
        else:
            p.fillPath(path, QColor(255, 255, 255, 170))

    if spec["tint"] == "palette":
        p.fillPath(path, QColor(*palettes.chrome_tint(bool(dark))))
    elif spec["tint"]:
        p.fillPath(path, QColor(*spec["tint"]))
    p.fillPath(path, QColor(*spec["fill"]))

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(*spec["edge"]), 1.0))
    p.drawPath(path)

    # The light-catch and the shadow: the same outline 1px inside, lit only
    # where it faces up (or down), fading out round the corners -- which is
    # what an inset 0 1px shadow looks like on a rounded box.
    inner = glass_path(rect.adjusted(1.0, 1.0, -1.0, -1.0), max(0.0, radius - 1.0))
    reach = max(radius, 6.0) / max(rect.height(), 1.0)
    top = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top.setColorAt(0.0, QColor(*spec["top"]))
    faded = QColor(*spec["top"])
    faded.setAlpha(0)
    top.setColorAt(min(0.5, reach * 0.55), faded)
    top.setColorAt(1.0, faded)
    p.setPen(QPen(QBrush(top), 1.0))
    p.drawPath(inner)
    if spec["bottom"]:
        bottom = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shade = QColor(*spec["bottom"])
        clear = QColor(*spec["bottom"])
        clear.setAlpha(0)
        bottom.setColorAt(0.0, clear)
        bottom.setColorAt(max(0.5, 1.0 - reach * 0.55), clear)
        bottom.setColorAt(1.0, shade)
        p.setPen(QPen(QBrush(bottom), 1.0))
        p.drawPath(inner)


def paint_contact_shadow(p, rect, radius, dark, depth=3.0):
    """A soft shadow `depth` px deep under a glass panel's lower edge -- the
    panel sits on the backdrop instead of being printed on it. Only the part
    outside the panel is painted, so glass that lets its backdrop through
    (the acrylic and solid modes) doesn't show a smudge inside."""
    if depth <= 0:
        return
    tone = (0, 0, 0) if dark else (30, 45, 90)
    strengths = (0.16, 0.09, 0.05) if dark else (0.085, 0.055, 0.035)
    panel = glass_path(rect, radius)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for i, alpha in enumerate(strengths):
        dy = depth * (i + 1) / len(strengths)
        c = QColor(*tone)
        c.setAlphaF(alpha)
        layer = glass_path(rect.adjusted(0.5, dy, -0.5, dy), radius).subtracted(panel)
        p.fillPath(layer, c)
    p.restore()


def _frost_transform(origin, sx, sy):
    """Texture transform mapping the small frost image onto the window:
    scale it up to the surface's size, then shift it so the widget's own
    top-left lands on the right spot of it. (QTransform applies the last
    operation first, so this scales and then translates.)"""
    t = QTransform()
    t.translate(-origin.x(), -origin.y())
    t.scale(sx, sy)
    return t


# ----------------------------------------------------------- dialogs ----
class DialogBackdrop:
    """A cached backdrop render for a dialog, which has no BackdropSurface
    of its own. Same rig, fitted to the dialog's size."""

    def __init__(self):
        self._key = None
        self._pix = None
        self.frost = None

    def ensure(self, widget, dark):
        dpr = widget.devicePixelRatioF()
        key = (widget.width(), widget.height(), bool(dark), dpr, palettes.current())
        if key != self._key:
            self._pix, self.frost = render_backdrop(widget.width(), widget.height(), dark, dpr)
            self._key = key

    def paint(self, widget, painter, dark):
        self.ensure(widget, dark)
        painter.drawPixmap(0, 0, self._pix)


def set_dark_title_bar(widget, dark=True):
    """Windows 11 draws a native dialog title bar light unless told
    otherwise; a white strip above a navy panel is the first thing the eye
    lands on. DWMWA_USE_IMMERSIVE_DARK_MODE (20), then -- where the build
    supports them -- the caption (35), its text (36) and the window's rim
    (34), all in the current palette: the caption a shade deeper than the
    panel's own field, the way the main window's title band is. It was
    always Sapphire's navy, whatever the palette (reported: an emerald
    About panel under a navy title bar)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from . import theme
        hwnd = int(widget.winId())

        def put(attr, value):
            v = ctypes.c_uint(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(rgb):
            return int(rgb[0]) | (int(rgb[1]) << 8) | (int(rgb[2]) << 16)

        on = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        base = palettes.ink(bool(dark))
        caption = tuple(round(c * 0.72) for c in base) if dark else base
        put(35, colorref(caption))
        text = theme.qcolor(theme.tokens(bool(dark))["text"])
        put(36, colorref((text.red(), text.green(), text.blue())))
        put(34, colorref(palettes.window_border(bool(dark))))
    except Exception:
        pass
