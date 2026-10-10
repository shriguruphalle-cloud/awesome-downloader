"""The music player's icons, drawn on a 24-unit grid (as browser_chrome's
are) in the manner of Apple's SF Symbols: solid, softly rounded transport
shapes; even-weight strokes with round ends for everything else; the "10"
of the skip buttons set in the app's own type, sized to sit inside its
arrow. draw() answers for the kinds it knows and returns False otherwise.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

KINDS = {"play", "pause", "next", "prev", "back10", "fwd10", "shuffle", "repeat", "repeat_one", "speaker",
         "speaker_low", "mute", "queue", "sliders", "eq", "mic", "note", "lyrics", "trend", "sparkle", "person",
         "disc", "folder", "target", "home", "import", "chevron", "playlist_import"}


def _pen(c, w):
    pen = QPen(QColor(c), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw(p, kind, rect, color):
    if kind not in KINDS:
        return False
    s = min(rect.width(), rect.height())
    u = s / 24.0
    ox = rect.center().x() - 12 * u
    oy = rect.center().y() - 12 * u
    c = QColor(color)

    def pt(x, y):
        return QPointF(ox + x * u, oy + y * u)

    def path(*pts, close=False):
        pa = QPainterPath(pt(*pts[0]))
        for q in pts[1:]:
            pa.lineTo(pt(*q))
        if close:
            pa.closeSubpath()
        return pa

    def solid(pa, round_=1.6):
        # filled, its corners rounded by a stroke of the same colour
        p.save()
        p.setPen(_pen(c, round_ * u))
        p.setBrush(c)
        p.drawPath(pa)
        p.restore()

    def stroke(pa, w=1.75):
        p.save()
        p.setPen(_pen(c, w * u))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(pa)
        p.restore()

    def rrect(x0, y0, x1, y1, r):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(pt(x0, y0), pt(x1, y1)), r * u, r * u)
        p.restore()

    def chevron(tip, ang, size=2.7, w=1.75):
        # an open arrowhead at `tip`, pointing along `ang` (radians, screen space)
        a1, a2 = ang + math.radians(140), ang - math.radians(140)
        stroke(path((tip[0] + size * math.cos(a1), tip[1] + size * math.sin(a1)), tip,
                    (tip[0] + size * math.cos(a2), tip[1] + size * math.sin(a2))), w)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if kind == "play":
        solid(path((7.6, 4.9), (19.2, 12), (7.6, 19.1), close=True), 2.4)
    elif kind == "pause":
        rrect(6.4, 4.6, 10.2, 19.4, 1.5)
        rrect(13.8, 4.6, 17.6, 19.4, 1.5)
    elif kind in ("next", "prev"):
        flip = kind == "prev"

        def fx(x):
            return 24 - x if flip else x
        solid(path((fx(5.0), 5.6), (fx(15.0), 12), (fx(5.0), 18.4), close=True), 2.2)
        x0, x1 = sorted((fx(16.9), fx(19.5)))
        rrect(x0, 5.2, x1, 18.8, 1.2)
    elif kind in ("back10", "fwd10"):
        fwd = kind == "fwd10"
        cx, cy, r = 12.0, 13.0, 8.0
        # an open circle, the gap at the top, an arrowhead into it
        start, span = (90, 300) if fwd else (90, -300)
        pa = QPainterPath()
        box = QRectF(pt(cx - r, cy - r), pt(cx + r, cy + r))
        pa.arcMoveTo(box, start)
        pa.arcTo(box, start, span)
        stroke(pa, 1.7)
        tip_x = cx + (3.4 if fwd else -3.4)
        head = path((tip_x, cy - r), (cx + (0.2 if fwd else -0.2), cy - r - 2.7),
                    (cx + (0.2 if fwd else -0.2), cy - r + 2.7), close=True)
        solid(head, 1.0)
        f = QFont()
        f.setFamilies(["Inter", "Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(max(6, int(round(7.4 * u))))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(c)
        p.drawText(QRectF(pt(cx - 6, cy - 4.6), pt(cx + 6, cy + 4.8)), int(Qt.AlignmentFlag.AlignCenter), "10")
    elif kind == "shuffle":
        a = QPainterPath(pt(3.4, 7.2))
        a.lineTo(pt(6.6, 7.2))
        a.cubicTo(pt(11.0, 7.2), pt(12.4, 16.8), pt(16.8, 16.8))
        a.lineTo(pt(19.6, 16.8))
        stroke(a)
        b = QPainterPath(pt(3.4, 16.8))
        b.lineTo(pt(6.6, 16.8))
        b.cubicTo(pt(11.0, 16.8), pt(12.4, 7.2), pt(16.8, 7.2))
        b.lineTo(pt(19.6, 7.2))
        stroke(b)
        chevron((20.2, 7.2), 0.0)
        chevron((20.2, 16.8), 0.0)
    elif kind in ("repeat", "repeat_one"):
        top = QPainterPath(pt(4.4, 12.6))
        top.lineTo(pt(4.4, 10.4))
        top.cubicTo(pt(4.4, 8.6), pt(5.6, 7.4), pt(7.4, 7.4))
        top.lineTo(pt(19.0, 7.4))
        stroke(top)
        chevron((19.6, 7.4), 0.0)
        bot = QPainterPath(pt(19.6, 11.4))
        bot.lineTo(pt(19.6, 13.6))
        bot.cubicTo(pt(19.6, 15.4), pt(18.4, 16.6), pt(16.6, 16.6))
        bot.lineTo(pt(5.0, 16.6))
        stroke(bot)
        chevron((4.4, 16.6), math.pi)
        if kind == "repeat_one":
            f = QFont()
            f.setFamilies(["Inter", "Segoe UI Variable Display", "Segoe UI"])
            f.setPixelSize(max(6, int(round(7.0 * u))))
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(c)
            p.drawText(QRectF(pt(8, 7.6), pt(16, 16.4)), int(Qt.AlignmentFlag.AlignCenter), "1")
    elif kind in ("speaker", "speaker_low", "mute"):
        body = path((3.6, 9.3), (6.8, 9.3), (11.2, 5.4), (11.2, 18.6), (6.8, 14.7), (3.6, 14.7), close=True)
        solid(body, 1.6)
        if kind == "mute":
            stroke(path((15.0, 9.4), (20.2, 14.6)))
            stroke(path((20.2, 9.4), (15.0, 14.6)))
        else:
            arc = QPainterPath()
            box = QRectF(pt(10.4, 8.2), pt(17.0, 15.8))
            arc.arcMoveTo(box, -52)
            arc.arcTo(box, -52, 104)
            stroke(arc)
            if kind == "speaker":
                arc2 = QPainterPath()
                box2 = QRectF(pt(9.6, 4.6), pt(21.0, 19.4))
                arc2.arcMoveTo(box2, -56)
                arc2.arcTo(box2, -56, 112)
                stroke(arc2)
    elif kind == "queue":
        for y in (6.4, 11.0):
            stroke(path((3.8, y), (15.4, y)))
        stroke(path((3.8, 15.6), (10.6, 15.6)))
        stroke(path((18.2, 8.6), (18.2, 17.4)))
        stroke(path((18.2, 8.6), (21.0, 9.6)))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(pt(16.3, 17.6), 2.15 * u, 1.85 * u)
        p.restore()
    elif kind == "sliders":
        for y, knob in ((6.6, 15.2), (12.0, 8.6), (17.4, 13.4)):
            stroke(path((3.8, y), (20.2, y)))
            p.save()
            p.setPen(_pen(c, 1.75 * u))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawLine(pt(knob, y - 2.6), pt(knob, y + 2.6))
            p.restore()
    elif kind == "eq":
        for x, knob in ((6.4, 8.6), (12.0, 15.0), (17.6, 10.6)):
            stroke(path((x, 4.0), (x, 20.0)))
            rrect(x - 2.3, knob - 1.1, x + 2.3, knob + 1.1, 1.0)
    elif kind in ("mic", "lyrics"):
        cap = QPainterPath()
        cap.addRoundedRect(QRectF(pt(8.9, 3.4), pt(15.1, 13.6)), 3.1 * u, 3.1 * u)
        stroke(cap)
        arc = QPainterPath()
        box = QRectF(pt(5.8, 4.4), pt(18.2, 16.8))
        arc.arcMoveTo(box, 200)
        arc.arcTo(box, 200, 140)
        stroke(arc)
        stroke(path((12, 16.9), (12, 20.4)))
        stroke(path((9.0, 20.4), (15.0, 20.4)))
    elif kind == "trend":
        stroke(path((3.6, 17.4), (9.0, 11.8), (12.8, 15.2), (20.0, 7.6)))
        stroke(path((15.2, 7.4), (20.2, 7.4), (20.2, 12.4)))
    elif kind == "sparkle":
        def star(cx, cy, r, w):
            pa = QPainterPath(pt(cx, cy - r))
            pa.quadTo(pt(cx + w, cy - w), pt(cx + r, cy))
            pa.quadTo(pt(cx + w, cy + w), pt(cx, cy + r))
            pa.quadTo(pt(cx - w, cy + w), pt(cx - r, cy))
            pa.quadTo(pt(cx - w, cy - w), pt(cx, cy - r))
            return pa
        stroke(star(10.0, 13.0, 7.2, 1.3), 1.6)
        solid(star(18.2, 6.0, 3.0, 0.6), 0.6)
    elif kind == "person":
        circ = QPainterPath()
        circ.addEllipse(pt(12, 8.2), 3.9 * u, 3.9 * u)
        stroke(circ)
        body = QPainterPath(pt(4.6, 20.2))
        body.cubicTo(pt(5.2, 15.6), pt(8.4, 13.6), pt(12, 13.6))
        body.cubicTo(pt(15.6, 13.6), pt(18.8, 15.6), pt(19.4, 20.2))
        stroke(body)
    elif kind == "disc":
        outer = QPainterPath()
        outer.addEllipse(pt(12, 12), 8.6 * u, 8.6 * u)
        stroke(outer)
        inner = QPainterPath()
        inner.addEllipse(pt(12, 12), 2.4 * u, 2.4 * u)
        stroke(inner)
        arc = QPainterPath()
        box = QRectF(pt(6.6, 6.6), pt(17.4, 17.4))
        arc.arcMoveTo(box, 100)
        arc.arcTo(box, 100, 60)
        stroke(arc, 1.3)
    elif kind == "folder":
        stroke(path((3.6, 7.6), (3.6, 18.2), (20.4, 18.2), (20.4, 9.2), (11.6, 9.2), (9.6, 6.4), (4.8, 6.4),
                    (3.6, 7.6)))
    elif kind == "target":
        for r in (8.4, 4.8):
            c2 = QPainterPath()
            c2.addEllipse(pt(12, 12), r * u, r * u)
            stroke(c2)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(pt(12, 12), 1.6 * u, 1.6 * u)
        p.restore()
    elif kind == "home":
        stroke(path((3.8, 11.2), (12, 4.2), (20.2, 11.2)))
        stroke(path((6.2, 9.4), (6.2, 19.6), (17.8, 19.6), (17.8, 9.4)))
        stroke(path((10.2, 19.6), (10.2, 14.6), (13.8, 14.6), (13.8, 19.6)))
    elif kind == "import":
        stroke(path((12, 3.8), (12, 14.2)))
        stroke(path((8.2, 10.6), (12, 14.4), (15.8, 10.6)))
        stroke(path((4.6, 14.6), (4.6, 19.4), (19.4, 19.4), (19.4, 14.6)))
    elif kind == "playlist_import":
        # a list, and a song coming into it
        for y, x1 in ((6.4, 12.6), (11.0, 12.6), (15.6, 9.6)):
            stroke(path((3.6, y), (x1, y)))
        stroke(path((17.6, 4.2), (17.6, 14.6)))
        stroke(path((14.6, 11.8), (17.6, 14.8), (20.6, 11.8)))
        stroke(path((13.4, 19.4), (21.0, 19.4)))
    elif kind == "chevron":
        stroke(path((9.4, 6.2), (15.2, 12), (9.4, 17.8)), 2.0)
    elif kind == "note":
        stroke(path((9.4, 17.4), (9.4, 5.6), (19.0, 3.8), (19.0, 15.6)))
        stroke(path((9.4, 9.2), (19.0, 7.4)))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(pt(7.0, 17.5), 2.6 * u, 2.2 * u)
        p.drawEllipse(pt(16.6, 15.7), 2.6 * u, 2.2 * u)
        p.restore()
    p.restore()
    return True
