"""Made-up pictures for the showcase: thumbnails and gallery images drawn
from scratch with PIL, so no real video, photo or person appears anywhere.

Each scene is a few soft layers -- a sky gradient, a sun or moon, ridges or
waves or lights -- deterministic for a given seed."""
import math
import random

from PIL import Image, ImageDraw, ImageFilter


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _vgrad(w, h, stops):
    """Vertical gradient through (position, rgb) stops."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                col = _lerp(c0, c1, (t - p0) / max(1e-6, p1 - p0))
                break
        else:
            col = stops[-1][1]
        for x in range(w):
            px[x, y] = col
    return img


def _ridge(rng, w, base, amp, rough, steps=48):
    pts = []
    y = base
    for i in range(steps + 1):
        x = w * i / steps
        y += rng.uniform(-rough, rough)
        y = min(base + amp, max(base - amp, y))
        pts.append((x, y))
    return pts


def _glow(img, centre, radius, colour, strength):
    layer = Image.new("RGB", img.size, (0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = centre
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.6))
    return Image.blend(img, Image.composite(layer, img, layer.convert("L")), strength)


def mountains(w, h, seed, sky, sun, ridges, sun_pos=(0.68, 0.42), stars=False):
    rng = random.Random(seed)
    img = _vgrad(w, h, sky)
    if stars:
        d = ImageDraw.Draw(img)
        for _ in range(int(w * h / 2600)):
            x, y = rng.uniform(0, w), rng.uniform(0, h * 0.6)
            r = rng.choice((0.6, 0.8, 1.0, 1.4)) * w / 1280
            a = rng.randint(140, 255)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(a, a, min(255, a + 10)))
    img = _glow(img, (w * sun_pos[0], h * sun_pos[1]), w * 0.09, sun, 0.85)
    d = ImageDraw.Draw(img)
    sx, sy = w * sun_pos[0], h * sun_pos[1]
    r = w * 0.035
    d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=_lerp(sun, (255, 255, 255), 0.5))
    for k, colour in enumerate(ridges):
        base = h * (0.52 + 0.12 * k)
        pts = _ridge(rng, w, base, h * 0.08, h * 0.035)
        d.polygon(pts + [(w, h), (0, h)], fill=colour)
    return img.filter(ImageFilter.GaussianBlur(w / 1400))


def aurora(w, h, seed):
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (6, 10, 28)), (0.6, (10, 28, 52)), (1, (4, 12, 22))])
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for band in range(3):
        phase = rng.uniform(0, 6.28)
        colour = [(60, 230, 160), (90, 200, 255), (170, 120, 255)][band]
        for x in range(0, w, 3):
            top = h * (0.12 + 0.06 * band) + math.sin(x / w * 6 + phase) * h * 0.06
            length = h * (0.25 + 0.15 * math.sin(x / w * 11 + phase * 2) ** 2)
            d.line((x, top, x, top + length), fill=colour, width=3)
    layer = layer.filter(ImageFilter.GaussianBlur(w / 90))
    img = Image.blend(img, Image.composite(layer, img, layer.convert("L")), 0.9)
    d = ImageDraw.Draw(img)
    for _ in range(int(w * h / 3000)):
        x, y = rng.uniform(0, w), rng.uniform(0, h * 0.55)
        r = rng.choice((0.5, 0.8, 1.1)) * w / 1280
        d.ellipse((x - r, y - r, x + r, y + r), fill=(230, 240, 255))
    for k, colour in enumerate(((8, 18, 30), (4, 10, 18))):
        pts = _ridge(rng, w, h * (0.68 + 0.12 * k), h * 0.06, h * 0.03)
        d.polygon(pts + [(w, h), (0, h)], fill=colour)
    return img


def city_lights(w, h, seed):
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (12, 10, 34)), (0.55, (40, 22, 70)), (1, (16, 8, 26))])
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(70):
        x, y = rng.uniform(0, w), rng.uniform(h * 0.25, h)
        r = rng.uniform(w * 0.01, w * 0.045)
        colour = rng.choice(((255, 170, 80), (255, 110, 150), (120, 180, 255), (255, 220, 140)))
        d.ellipse((x - r, y - r, x + r, y + r), fill=colour)
    layer = layer.filter(ImageFilter.GaussianBlur(w / 160))
    img = Image.blend(img, Image.composite(layer, img, layer.convert("L")), 0.75)
    d = ImageDraw.Draw(img)
    x = 0
    while x < w:
        bw = rng.uniform(w * 0.03, w * 0.08)
        bh = rng.uniform(h * 0.15, h * 0.45)
        d.rectangle((x, h - bh, x + bw, h), fill=(10, 8, 22))
        for wy in range(int(h - bh + 8), h - 6, max(6, int(h / 60))):
            for wx in range(int(x + 4), int(x + bw - 4), max(6, int(w / 140))):
                if rng.random() < 0.35:
                    d.rectangle((wx, wy, wx + w / 400, wy + h / 300), fill=(255, 214, 140))
        x += bw + rng.uniform(1, w * 0.01)
    return img


def dunes(w, h, seed):
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (250, 196, 140)), (0.45, (246, 150, 96)), (1, (120, 60, 40))])
    img = _glow(img, (w * 0.3, h * 0.35), w * 0.08, (255, 236, 190), 0.9)
    d = ImageDraw.Draw(img)
    for k in range(5):
        base = h * (0.5 + 0.1 * k)
        phase = rng.uniform(0, 6.28)
        pts = [(x, base + math.sin(x / w * (2.2 + k * 0.4) * math.pi + phase) * h * 0.05) for x in range(0, w + 8, 8)]
        shade = _lerp((214, 120, 70), (90, 40, 26), k / 4)
        d.polygon(pts + [(w, h), (0, h)], fill=shade)
    return img.filter(ImageFilter.GaussianBlur(w / 1600))


def ocean(w, h, seed):
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (120, 190, 240)), (0.48, (200, 228, 245)), (0.5, (30, 110, 170)), (1, (6, 40, 80))])
    img = _glow(img, (w * 0.62, h * 0.4), w * 0.07, (255, 250, 230), 0.8)
    d = ImageDraw.Draw(img)
    for _ in range(140):
        y = rng.uniform(h * 0.52, h)
        x = rng.uniform(-w * 0.1, w)
        length = rng.uniform(w * 0.04, w * 0.16) * (y / h)
        d.line((x, y, x + length, y), fill=(220, 240, 255), width=max(1, int(w / 900)))
    return img.filter(ImageFilter.GaussianBlur(w / 1500))


def forest(w, h, seed):
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (190, 220, 210)), (0.5, (120, 170, 150)), (1, (30, 60, 46))])
    d = ImageDraw.Draw(img)
    for k, colour in enumerate(((96, 140, 118), (60, 104, 82), (34, 70, 54), (18, 42, 32))):
        base = h * (0.42 + 0.15 * k)
        x = -w * 0.05
        while x < w * 1.05:
            tw = rng.uniform(w * 0.03, w * 0.06) * (1 + k * 0.3)
            th = rng.uniform(h * 0.18, h * 0.32) * (1 + k * 0.25)
            d.polygon(((x, base), (x + tw / 2, base - th), (x + tw, base)), fill=colour)
            x += tw * rng.uniform(0.45, 0.8)
        d.rectangle((0, base, w, h), fill=colour)
    return img.filter(ImageFilter.GaussianBlur(w / 1700))


def coffee(w, h, seed):
    """A cup on a table, top-down-ish: warm, simple shapes."""
    rng = random.Random(seed)
    img = _vgrad(w, h, [(0, (70, 46, 34)), (1, (36, 22, 16))])
    d = ImageDraw.Draw(img)
    for _ in range(60):
        y = rng.uniform(0, h)
        d.line((0, y, w, y + rng.uniform(-6, 6)), fill=(84, 56, 40), width=1)
    cx, cy, r = w * 0.5, h * 0.52, h * 0.32
    d.ellipse((cx - r * 1.25, cy - r * 1.25, cx + r * 1.25, cy + r * 1.25), fill=(236, 230, 222))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 72, 40))
    d.ellipse((cx - r * 0.8, cy - r * 0.8, cx + r * 0.8, cy + r * 0.8), fill=(150, 96, 58))
    d.ellipse((cx + r * 1.2, cy - r * 0.18, cx + r * 1.62, cy + r * 0.18), outline=(236, 230, 222), width=int(r * 0.12))
    return img.filter(ImageFilter.GaussianBlur(w / 1500))


SCENES = {
    "aurora": lambda w, h: aurora(w, h, 11),
    "sunset": lambda w, h: mountains(w, h, 3, [(0, (40, 30, 80)), (0.5, (230, 110, 90)), (0.8, (255, 190, 120)), (1, (255, 214, 160))],
                                     (255, 200, 140), [(90, 50, 90), (58, 32, 70), (30, 18, 44)]),
    "alpine": lambda w, h: mountains(w, h, 7, [(0, (80, 140, 220)), (0.7, (190, 220, 245)), (1, (230, 240, 250))],
                                     (255, 250, 230), [(120, 150, 190), (80, 110, 150), (46, 70, 100)], sun_pos=(0.25, 0.3)),
    "night": lambda w, h: mountains(w, h, 5, [(0, (4, 8, 26)), (0.7, (20, 36, 80)), (1, (40, 60, 110))],
                                    (220, 230, 255), [(16, 24, 52), (10, 16, 36), (4, 8, 20)], sun_pos=(0.75, 0.25), stars=True),
    "city": lambda w, h: city_lights(w, h, 9),
    "dunes": lambda w, h: dunes(w, h, 4),
    "ocean": lambda w, h: ocean(w, h, 8),
    "forest": lambda w, h: forest(w, h, 6),
    "coffee": lambda w, h: coffee(w, h, 2),
}


def scene(name, w=1280, h=720):
    return SCENES[name](w, h)
