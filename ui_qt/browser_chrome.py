"""Painted chrome for the Browser tab: the tab strip, the toolbar's controls,
the address field, the load bar, and the small panels the toolbar opens.

Painted rather than QSS-styled because nearly every piece animates -- tabs
grow out of the + button and slide aside when one closes, the load bar eases
through a page's loading stages, hover and focus fade rather than snap -- and
because a stylesheet radius isn't antialiased.

One hard rule shapes the rest: a web page is a native window, and a native
window always draws above anything Qt paints beside it. Nothing here may
overlap the page area; everything that opens over the page (menus, the
AdGuard panel, the bookmark editor) is a top-level popup window.
"""
import math
import threading
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from . import motion, theme
from .theme import qcolor

PRIVATE_TINT = QColor(155, 123, 255)


def private_tint(dark=True):
    """What marks a private tab: violet among normal tabs; with a private
    tab showing (the window gone black and white, palettes.MONO), plain
    light or dark like everything else."""
    from . import palettes
    if palettes.current() == palettes.MONO:
        return QColor(236, 236, 238) if dark else QColor(40, 40, 44)
    return QColor(PRIVATE_TINT)


# --------------------------------------------------------------- icons ----
def _pen(color, width):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def draw_icon(p, kind, rect, color, weight=1.0):
    """Line icons drawn on a 24-unit grid and scaled into `rect`, so they
    stay crisp at any size and display scale (font glyphs didn't: several
    rendered as a few faint pixels in this font stack)."""
    s = min(rect.width(), rect.height())
    u = s / 24.0
    ox = rect.center().x() - 12 * u
    oy = rect.center().y() - 12 * u
    c = QColor(color)

    def pt(x, y):
        return QPointF(ox + x * u, oy + y * u)

    def poly(*pts, close=False, fill=False):
        path = QPainterPath(pt(*pts[0]))
        for q in pts[1:]:
            path.lineTo(pt(*q))
        if close:
            path.closeSubpath()
        if fill:
            p.save()
            p.setBrush(c)
            p.drawPath(path)
            p.restore()
        else:
            p.drawPath(path)

    def dot(x, y, r):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(pt(x, y), r * u, r * u)
        p.restore()

    def circle(x, y, r):
        p.drawEllipse(pt(x, y), r * u, r * u)

    def arc(x, y, r, start, span):
        p.drawArc(QRectF(pt(x - r, y - r), pt(x + r, y + r)), int(start * 16), int(span * 16))

    def shield_path():
        path = QPainterPath(pt(12, 3.4))
        path.lineTo(pt(19.2, 6.3))
        path.lineTo(pt(19.2, 11.2))
        path.cubicTo(pt(19.2, 15.9), pt(16.1, 19.2), pt(12, 20.7))
        path.cubicTo(pt(7.9, 19.2), pt(4.8, 15.9), pt(4.8, 11.2))
        path.lineTo(pt(4.8, 6.3))
        path.closeSubpath()
        return path

    def cone():
        poly((3.8, 9.4), (7.4, 9.4), (11.8, 5.4), (11.8, 18.6), (7.4, 14.6), (3.8, 14.6),
             close=True, fill=True)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(_pen(c, max(1.2, 1.7 * u) * weight))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "back":
        poly((19, 12), (5, 12))
        poly((11, 6), (5, 12), (11, 18))
    elif kind == "forward":
        poly((5, 12), (19, 12))
        poly((13, 6), (19, 12), (13, 18))
    elif kind == "reload":
        arc(12, 12.3, 7.2, 70, 280)
        a = math.radians(70)
        ax, ay = 12 + 7.2 * math.cos(a), 12.3 - 7.2 * math.sin(a)
        dx, dy = math.sin(a), math.cos(a)          # clockwise tangent, screen space
        px, py = -dy, dx
        tip = (ax + dx * 3.4, ay + dy * 3.4)
        base = (ax - dx * 0.6, ay - dy * 0.6)
        poly(tip, (base[0] + px * 3.0, base[1] + py * 3.0), (base[0] - px * 3.0, base[1] - py * 3.0),
             close=True, fill=True)
    elif kind == "stop":
        poly((6.5, 6.5), (17.5, 17.5))
        poly((17.5, 6.5), (6.5, 17.5))
    elif kind == "close":
        poly((7.5, 7.5), (16.5, 16.5))
        poly((16.5, 7.5), (7.5, 16.5))
    elif kind == "home":
        poly((3.6, 11.6), (12, 4.4), (20.4, 11.6))
        poly((6.2, 9.6), (6.2, 19.6), (17.8, 19.6), (17.8, 9.6))
        poly((10.2, 19.6), (10.2, 14.6), (13.8, 14.6), (13.8, 19.6))
    elif kind == "menu":
        for y in (5.6, 12, 18.4):
            dot(12, y, 1.75)
    elif kind == "plus":
        poly((12, 5), (12, 19))
        poly((5, 12), (19, 12))
    elif kind in ("star", "star_filled"):
        pts = []
        for i in range(10):
            ang = math.radians(-90 + 36 * i)
            r = 8.6 if i % 2 == 0 else 3.9
            pts.append((12 + r * math.cos(ang), 12.9 + r * math.sin(ang)))
        poly(*pts, close=True, fill=kind == "star_filled")
    elif kind == "lock":
        body = QRectF(pt(6, 10.6), pt(18, 20))
        p.drawRoundedRect(body, 2.2 * u, 2.2 * u)
        arc(12, 8.4, 3.6, 0, 180)
        poly((8.4, 8.4), (8.4, 10.6))
        poly((15.6, 8.4), (15.6, 10.6))
        dot(12, 15.2, 1.35)
    elif kind == "warn":
        poly((12, 4), (21, 19.6), (3, 19.6), close=True)
        poly((12, 10), (12, 14))
        dot(12, 16.9, 1.15)
    elif kind == "info":
        circle(12, 12, 8.6)
        poly((12, 11), (12, 16.6))
        dot(12, 7.9, 1.15)
    elif kind == "search":
        circle(10.6, 10.6, 5.8)
        poly((14.9, 14.9), (19.6, 19.6))
    elif kind in ("shield", "shield_check", "shield_off"):
        p.drawPath(shield_path())
        if kind == "shield_check":
            poly((8.9, 12), (11.1, 14.2), (15.2, 9.9))
        elif kind == "shield_off":
            poly((4, 3.6), (20, 20.6))
    elif kind == "download":
        poly((12, 4), (12, 15))
        poly((7.5, 10.5), (12, 15), (16.5, 10.5))
        poly((5, 16.6), (5, 19.6), (19, 19.6), (19, 16.6))
    elif kind == "play":
        p.setPen(_pen(c, 1.2 * u))
        poly((8, 5.6), (18.6, 12), (8, 18.4), close=True, fill=True)
    elif kind == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(pt(6.8, 5.4), pt(10.4, 18.6)), 1.1 * u, 1.1 * u)
        p.drawRoundedRect(QRectF(pt(13.6, 5.4), pt(17.2, 18.6)), 1.1 * u, 1.1 * u)
    elif kind == "next":
        p.setPen(_pen(c, 1.2 * u))
        poly((6, 6), (14.6, 12), (6, 18), close=True, fill=True)
        p.setPen(_pen(c, 2.2 * u))
        poly((17.8, 6.2), (17.8, 17.8))
    elif kind == "prev":
        p.setPen(_pen(c, 1.2 * u))
        poly((18, 6), (9.4, 12), (18, 18), close=True, fill=True)
        p.setPen(_pen(c, 2.2 * u))
        poly((6.2, 6.2), (6.2, 17.8))
    elif kind == "speaker":
        p.setPen(Qt.PenStyle.NoPen)
        cone()
        p.setPen(_pen(c, max(1.2, 1.7 * u) * weight))
        arc(12, 12, 3.6, -48, 96)
        arc(12, 12, 6.8, -52, 104)
    elif kind == "mute":
        p.setPen(Qt.PenStyle.NoPen)
        cone()
        p.setPen(_pen(c, max(1.2, 1.7 * u) * weight))
        poly((15.2, 9.4), (20.4, 14.6))
        poly((20.4, 9.4), (15.2, 14.6))
    elif kind == "private":
        poly((3.4, 11), (20.6, 11))
        p.setPen(_pen(c, 1.0 * u))
        poly((6.6, 10.6), (8.1, 5.6), (10.6, 6.5), (12, 5.3), (13.4, 6.5), (15.9, 5.6), (17.4, 10.6),
             close=True, fill=True)
        p.setPen(_pen(c, max(1.2, 1.6 * u)))
        circle(7.9, 16, 2.8)
        circle(16.1, 16, 2.8)
        poly((10.7, 15.6), (13.3, 15.6))
    elif kind == "globe":
        circle(12, 12, 8.6)
        poly((3.4, 12), (20.6, 12))
        p.drawEllipse(pt(12, 12), 3.8 * u, 8.6 * u)
    elif kind == "chevrons":
        poly((6, 7), (11, 12), (6, 17))
        poly((13, 7), (18, 12), (13, 17))
    elif kind == "external":
        poly((18, 13.6), (18, 19.6), (4.4, 19.6), (4.4, 6), (10.4, 6))
        poly((14, 4.4), (19.6, 4.4), (19.6, 10))
        poly((19.6, 4.4), (11, 13))
    elif kind == "clock":
        circle(12, 12, 8.6)
        poly((12, 7.4), (12, 12), (15.2, 14))
    elif kind in ("bookmark", "bookmark_filled"):
        poly((7, 4), (17, 4), (17, 20), (12, 16), (7, 20), close=True, fill=kind == "bookmark_filled")
    elif kind == "bookmarks":
        poly((5, 7), (14.6, 7), (14.6, 20.4), (9.8, 16.8), (5, 20.4), close=True)
        poly((9.4, 7), (9.4, 3.6), (19, 3.6), (19, 17), (14.6, 13.8))
    elif kind in ("zoom_in", "zoom_out"):
        circle(10.6, 10.6, 5.8)
        poly((14.9, 14.9), (19.6, 19.6))
        poly((8, 10.6), (13.2, 10.6))
        if kind == "zoom_in":
            poly((10.6, 8), (10.6, 13.2))
    elif kind == "print":
        poly((7, 8), (7, 4), (17, 4), (17, 8))
        p.drawRoundedRect(QRectF(pt(3.8, 8), pt(20.2, 16.2)), 2 * u, 2 * u)
        poly((7, 13), (7, 20), (17, 20), (17, 13))
    elif kind == "code":
        poly((8.4, 7.4), (3.8, 12), (8.4, 16.6))
        poly((15.6, 7.4), (20.2, 12), (15.6, 16.6))
        poly((13.4, 5), (10.6, 19))
    elif kind == "trash":
        poly((4.4, 7), (19.6, 7))
        poly((9.4, 7), (9.4, 4.4), (14.6, 4.4), (14.6, 7))
        poly((6.4, 7), (7.4, 19.6), (16.6, 19.6), (17.6, 7))
    elif kind == "sliders":
        for y, x in ((7, 9), (12, 15.5), (17, 7)):
            poly((4, y), (20, y))
            dot(x, y, 2.3)
    elif kind == "list":
        for y in (7, 12, 17):
            dot(5, y, 1.3)
            poly((9, y), (20, y))
    elif kind == "tab":
        p.drawRoundedRect(QRectF(pt(3.6, 5), pt(20.4, 19)), 2.4 * u, 2.4 * u)
        poly((3.6, 9.6), (20.4, 9.6))
    elif kind == "save":
        poly((12, 4), (12, 14))
        poly((8, 10), (12, 14), (16, 10))
        poly((4.6, 15), (4.6, 19.6), (19.4, 19.6), (19.4, 15))
    elif kind == "gauge":
        arc(12, 14, 8, 0, 180)
        poly((12, 14), (16, 9))
        dot(12, 14, 1.4)
    elif kind == "coffee":
        # The website's coffee cup: a mug, its handle, three wisps of steam.
        cup = QPainterPath(pt(3.5, 9))
        cup.lineTo(pt(16.5, 9))
        cup.lineTo(pt(16.5, 16.5))
        cup.arcTo(QRectF(pt(9.5, 13), pt(16.5, 20)), 0, -90)
        cup.lineTo(pt(7, 20))
        cup.arcTo(QRectF(pt(3.5, 13), pt(10.5, 20)), 270, -90)
        cup.closeSubpath()
        p.drawPath(cup)
        handle = QPainterPath(pt(16.5, 10.5))
        handle.lineTo(pt(17.5, 10.5))
        handle.arcTo(QRectF(pt(14.5, 10.5), pt(20.5, 16.5)), 90, -180)
        handle.lineTo(pt(16.5, 16.5))
        p.drawPath(handle)
        for x in (7, 10, 13):
            poly((x, 3.2), (x, 5.8))
    elif kind == "card":
        p.drawRoundedRect(QRectF(pt(2.5, 5.5), pt(21.5, 18.5)), 2 * u, 2 * u)
        poly((2.5, 10), (21.5, 10))
        poly((6.5, 14.5), (8.5, 14.5))
        poly((12, 14.5), (16, 14.5))
    p.restore()


def icon(kind, color, size=16):
    """A QIcon of draw_icon's glyph, rendered at 2x for high-DPI menus."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, kind, QRectF(0, 0, size * scale, size * scale), color)
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


def style_menu(menu, t):
    """A menu as a rounded glass-dark panel. The window flags let the
    stylesheet's rounded corners show instead of a square frame."""
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setStyleSheet(f"""
        QMenu {{
            background: {t['card_bg_solid']};
            color: {t['text']};
            border: 1px solid {t['card_border']};
            border-radius: 12px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 26px 7px 10px;
            border-radius: 8px;
            background: transparent;
        }}
        QMenu::item:selected {{ background: {t['hover_overlay']}; }}
        QMenu::item:disabled {{ color: {t['text_faint']}; }}
        QMenu::separator {{ height: 1px; background: {t['divider']}; margin: 5px 10px; }}
        QMenu::icon {{ padding-left: 6px; }}
    """)
    return menu


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(round(motion.lerp(a.red(), b.red(), t)), round(motion.lerp(a.green(), b.green(), t)),
                  round(motion.lerp(a.blue(), b.blue(), t)), round(motion.lerp(a.alpha(), b.alpha(), t)))


def _font(widget, px, weight=QFont.Weight.Normal):
    f = QFont(widget.font())
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def _faded_text(p, rect, text, color, font, dpr, fade=18.0):
    """Text that fades out at its right edge when it doesn't fit, the way a
    browser tab's title does, instead of ending in "...". """
    fm = QFontMetricsF(font)
    p.setFont(font)
    p.setPen(color)
    if fm.horizontalAdvance(text) <= rect.width():
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        return
    w, h = max(1, int(rect.width() * dpr)), max(1, int(rect.height() * dpr))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    ip.setFont(font)
    ip.setPen(color)
    ip.drawText(QRectF(0, 0, rect.width(), rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
    ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    g = QLinearGradient(rect.width() - fade, 0, rect.width(), 0)
    g.setColorAt(0, QColor(0, 0, 0, 255))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    ip.fillRect(QRectF(rect.width() - fade, 0, fade, rect.height()), g)
    ip.end()
    p.drawImage(rect.topLeft(), img)


# ------------------------------------------------------------- buttons ----
class ChromeButton(QAbstractButton):
    """A round icon button: the hover fill fades in, press darkens it, and an
    optional count sits on its shoulder (the AdGuard shield's)."""

    def __init__(self, kind, tip="", size=32, icon_size=18, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.icon_size = icon_size
        self.badge = ""
        self.tint = None
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_tip(tip)
        self._hover = motion.Fader(self)
        self._t = theme.tokens(True)

    def set_tip(self, tip):
        self.setToolTip(tip)
        self.setAccessibleName(tip.split(" (")[0] if tip else self.kind)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_badge(self, text):
        text = str(text or "")
        if text != self.badge:
            self.badge = text
            self.update()

    def apply_theme(self, t):
        self._t = t
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def sizeHint(self):
        return self.size()

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = None
        if self.isDown() or self.isChecked():
            fill = qcolor(t["pressed_overlay"])
        elif self._hover.value > 0 and self.isEnabled():
            fill = qcolor(t["hover_overlay"])
            fill.setAlphaF(fill.alphaF() * self._hover.value)
        if fill is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        color = QColor(self.tint or t["text"])
        if not self.isEnabled():
            color = qcolor(t["text_faint"])
            color.setAlphaF(color.alphaF() * 0.6)
        elif self.tint is None:
            color = _mix(qcolor(t["text_muted"]), qcolor(t["text"]), 0.55 + 0.45 * self._hover.value)
        ir = QRectF(0, 0, self.icon_size, self.icon_size)
        ir.moveCenter(r.center())
        if self.isDown():
            ir.translate(0, 0.5)
        draw_icon(p, self.kind, ir, color)
        if self.badge:
            f = _font(self, 9, QFont.Weight.Bold)
            fm = QFontMetricsF(f)
            bw = max(15.0, fm.horizontalAdvance(self.badge) + 8)
            br = QRectF(self.width() - bw, 0, bw, 14)
            p.setPen(QPen(qcolor(t["card_bg_solid"]), 1.5))
            p.setBrush(qcolor(t["brand"]))
            p.drawRoundedRect(br, 7, 7)
            p.setFont(f)
            p.setPen(qcolor(t["brand_text"]))
            p.drawText(br, int(Qt.AlignmentFlag.AlignCenter), self.badge)
        p.end()


class ActionButton(QAbstractButton):
    """The toolbar's one ember control: send this page to the downloader.
    It glows when the page is playing a video -- the moment it's most
    likely to be wanted -- and goes quiet on pages with nothing to fetch."""

    def __init__(self, text, kind="download", parent=None):
        super().__init__(parent)
        self._text = text
        self.kind = kind
        self.setFixedHeight(32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = motion.Fader(self)
        self._lit = motion.Fader(self, motion.SLOW)
        self._pulse = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._step)
        self._pulse_started = 0.0
        self._t = theme.tokens(True)
        f = _font(self, 12, QFont.Weight.DemiBold)
        self.setFixedWidth(int(QFontMetricsF(f).horizontalAdvance(text)) + 46)

    def apply_theme(self, t):
        self._t = t
        self.update()

    def set_lit(self, lit):
        """True while the page shows a video: a soft ember glow breathes a
        few times, then stays."""
        self._lit.to(1 if lit else 0)
        if lit and not motion.reduced():
            self._pulse_started = time.monotonic()
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse = 0.0

    def _step(self):
        elapsed = time.monotonic() - self._pulse_started
        if elapsed > 3.6:
            self._pulse_timer.stop()
            self._pulse = 0.0
        else:
            self._pulse = 0.5 - 0.5 * math.cos(elapsed / 1.2 * 2 * math.pi)
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = r.height() / 2
        if not self.isEnabled():
            p.setPen(QPen(qcolor(t["card_border"]), 1))
            p.setBrush(qcolor(t["hover_overlay"]))
            p.drawRoundedRect(r, rad, rad)
            fg = qcolor(t["text_faint"])
        else:
            glow = self._lit.value * (0.35 + 0.65 * self._pulse) if self._pulse else self._lit.value * 0.35
            if glow > 0.01:
                g = QColor(t["accent"])
                for i, spread in enumerate((5.0, 3.0, 1.5)):
                    g.setAlphaF(0.10 * glow * (i + 1))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(g)
                    gr = r.adjusted(-spread, -spread, spread, spread)
                    p.drawRoundedRect(gr, gr.height() / 2, gr.height() / 2)
            top = QColor(t["accent_top"])
            bottom = QColor(t["accent_pressed"] if self.isDown() else t["accent"])
            if self._hover.value and not self.isDown():
                top = _mix(top, QColor(t["accent_hover"]).lighter(112), self._hover.value * 0.6)
                bottom = _mix(bottom, QColor(t["accent_hover"]), self._hover.value * 0.7)
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(r, rad, rad)
            sheen = QLinearGradient(r.topLeft(), r.center())
            sheen.setColorAt(0, QColor(255, 255, 255, 60))
            sheen.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(sheen)
            p.drawRoundedRect(r, rad, rad)
            fg = QColor(t["accent_text"])
        ir = QRectF(r.left() + 11, r.center().y() - 8, 16, 16)
        draw_icon(p, self.kind, ir, fg, weight=1.1)
        p.setFont(_font(self, 12, QFont.Weight.DemiBold))
        p.setPen(fg)
        p.drawText(QRectF(ir.right() + 6, r.top(), r.right() - ir.right() - 6, r.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text)
        p.end()


class Toggle(QAbstractButton):
    """An on/off switch. On is ember, like every checked control in the app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = motion.Fader(self, motion.MEDIUM)
        self._t = theme.tokens(True)
        self.toggled.connect(lambda on: self._pos.to(1 if on else 0))

    def setChecked(self, on):  # noqa: N802 -- Qt's name
        blocked = self.blockSignals(True)
        super().setChecked(on)
        self.blockSignals(blocked)
        self._pos.snap(1 if on else 0)

    def apply_theme(self, t):
        self._t = t
        self.update()

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        v = self._pos.value
        off = qcolor(t["field_border"])
        track = _mix(off, QColor(t["accent"]), v)
        if not self.isEnabled():
            track.setAlphaF(track.alphaF() * 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = motion.lerp(r.left() + 2, r.right() - 2 - d, v)
        knob = QRectF(x, r.top() + 2, d, d)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(knob.translated(0, 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(knob)
        p.end()


# ------------------------------------------------------------ load bar ----
class LoadBar(QWidget):
    """A 2 px line under the toolbar that follows a page load through its
    stages (request sent, first bytes, document parsed, done), creeping
    between them the way a browser's does, then filling and fading out."""

    H = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedHeight(self.H + 6)      # room for the tip's glow
        self._value = 0.0
        self._cap = 0.0
        self._alpha = 0.0
        self._finishing = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._t = theme.tokens(True)

    def apply_theme(self, t):
        self._t = t
        self.update()

    def begin(self):
        self._finishing = False
        self._value = 0.03
        self._cap = 0.2
        self._alpha = 1.0
        self._run()

    def stage(self, percent):
        if percent >= 100:
            self.finish()
            return
        if self._alpha == 0 or self._finishing:
            self.begin()
        self._cap = max(self._cap, min(0.92, percent / 100.0 + 0.12))
        self._run()

    def finish(self):
        if self._alpha == 0:
            return
        self._finishing = True
        self._run()

    def reset(self):
        self._timer.stop()
        self._value = self._alpha = 0.0
        self._finishing = False
        self.update()

    def _run(self):
        if motion.reduced():
            self._value = 1.0 if self._finishing else self._cap
            if self._finishing:
                self._alpha = 0.0
            self.update()
            return
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        if self._finishing:
            self._value += (1.0 - self._value) * 0.28
            if self._value > 0.995:
                self._value = 1.0
                self._alpha -= 0.08
                if self._alpha <= 0:
                    self._alpha = 0.0
                    self._timer.stop()
        else:
            # Creep toward the stage's cap; the cap itself inches up so a
            # slow page still shows movement.
            self._cap = min(0.94, self._cap + 0.0006)
            self._value += (self._cap - self._value) * 0.04
        self.update()

    def paintEvent(self, event):
        if self._alpha <= 0 or self._value <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._alpha)
        w = self.width() * self._value
        y = self.height() - self.H
        ember = QColor(self._t["progress"])
        grad = QLinearGradient(0, 0, w, 0)
        faint = QColor(ember)
        faint.setAlpha(40)
        grad.setColorAt(0, faint)
        grad.setColorAt(max(0.0, 1 - 120 / max(w, 1)), ember)
        grad.setColorAt(1, QColor(self._t["accent_top"]).lighter(120))
        p.fillRect(QRectF(0, y, w, self.H), grad)
        if self._value < 1.0:
            glow = QRadialGradient(QPointF(w, y + 1), 16)
            g = QColor(ember)
            g.setAlpha(120)
            glow.setColorAt(0, g)
            g.setAlpha(0)
            glow.setColorAt(1, g)
            p.fillRect(QRectF(w - 16, 0, 32, self.height()), glow)
        p.end()


# ------------------------------------------------------------ tab pill ----
class TabPill(QWidget):
    """One tab: favicon (or a spinner while loading), title fading out at
    its edge, a speaker while it plays sound, a close button on hover. The
    active tab is a pane of glass with the app's sky light strip under it --
    the same mark the main navigation uses for "you are here"."""

    clicked = Signal()
    close_clicked = Signal()
    mute_clicked = Signal()
    context_requested = Signal(QPoint)
    drag_moved = Signal(object, int)       # (pill, global x)
    drag_finished = Signal(object)

    H = 30
    RADIUS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.title = "New Tab"
        self.url = ""
        self.favicon = None
        self.private = False
        self.loading = False
        self.audible = False
        self.muted = False
        self.sleeping = False
        self.home = True
        self.active = False
        self._t = theme.tokens(True)
        self._dark = True
        self._hover = motion.Fader(self)
        self._act = motion.Fader(self, motion.MEDIUM)
        self._icon_in = motion.Fader(self, motion.MEDIUM, value=1.0)
        self._spin = 0.0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._advance_spin)
        self._press_pos = None
        self._dragging = False

        self.close_btn = ChromeButton("close", "Close tab (Ctrl+W)", size=20, icon_size=12, parent=self)
        self.close_btn.clicked.connect(self.close_clicked)
        self.audio_btn = ChromeButton("speaker", "Mute tab", size=20, icon_size=13, parent=self)
        self.audio_btn.clicked.connect(self.mute_clicked)
        self.close_btn.hide()
        self.audio_btn.hide()

    # ---- state ----
    def apply_theme(self, t, dark):
        self._t, self._dark = t, dark
        self.close_btn.apply_theme(t)
        self.audio_btn.apply_theme(t)
        self.update()

    def set_active(self, active):
        self.active = active
        self._act.to(1 if active else 0)
        self._place()

    def set_title(self, title):
        self.title = title or "New Tab"
        self.setToolTip(("Private -- " if self.private else "") + self.title
                        + ("\n" + self.url if self.url and not self.home else ""))
        self.update()

    def set_url(self, url, home=False):
        self.url = url or ""
        self.home = home
        self.set_title(self.title)

    def set_icon(self, pixmap):
        self.favicon = pixmap
        self._icon_in.snap(0)
        self._icon_in.to(1)

    def set_loading(self, loading):
        if loading == self.loading:
            return
        self.loading = loading
        if loading and not motion.reduced():
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
        self.update()

    def set_audio(self, audible, muted):
        self.audible, self.muted = audible, muted
        self.audio_btn.set_kind("mute" if muted else "speaker")
        self.audio_btn.set_tip("Unmute tab" if muted else "Mute tab")
        self._place()

    def set_sleeping(self, sleeping):
        if sleeping != self.sleeping:
            self.sleeping = sleeping
            self.update()

    def set_private(self, private):
        self.private = private
        self.update()

    def _advance_spin(self):
        self._spin = (self._spin + 7.5) % 360
        self.update(QRect(0, 0, 36, self.H))

    # ---- layout ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        w = self.width()
        y = (self.H - 20) // 2
        x = w - 6 - 20
        show_close = (self.active or self._hover.value > 0.5 or self.underMouse()) and w >= 56
        if self.active and w >= 40:
            show_close = True
        self.close_btn.setVisible(show_close)
        if show_close:
            self.close_btn.move(x, y)
            x -= 22
        show_audio = (self.audible or self.muted) and w >= (84 if show_close else 60)
        self.audio_btn.setVisible(show_audio)
        if show_audio:
            self.audio_btn.move(x, y)
            x -= 22
        self._text_right = x + 18
        self.update()

    # ---- input ----
    def enterEvent(self, event):
        self._hover.to(1)
        self._place()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        QTimer.singleShot(0, self._place)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False
            self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            gx = event.globalPosition().toPoint()
            if not self._dragging and abs(gx.x() - self._press_pos.x()) > 6:
                self._dragging = True
            if self._dragging:
                self.drag_moved.emit(self, gx.x())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.close_clicked.emit()
        elif event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.drag_finished.emit(self)
        self._press_pos = None
        self._dragging = False
        super().mouseReleaseEvent(event)

    # ---- paint ----
    def paintEvent(self, event):
        t, dark = self._t, self._dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.RADIUS
        a = self._act.value
        h = self._hover.value

        # The tabs you're not on are cards as well -- quieter than the active
        # one, but each its own shape. With nothing but text and an icon
        # they ran together into one strip (reported as unpolished).
        rest = 1.0 - a
        if rest > 0.001:
            card = QColor(255, 255, 255, round((10 + 6 * h) * rest)) if dark else \
                QColor(255, 255, 255, round((92 + 40 * h) * rest))
            line = QColor(255, 255, 255, round((20 + 10 * h) * rest)) if dark else \
                QColor(15, 35, 90, round((22 + 10 * h) * rest))
            p.setPen(QPen(line, 1))
            p.setBrush(card)
            p.drawRoundedRect(r, rad, rad)

        if a > 0.001:
            fill = QColor(255, 255, 255, round(22 * a)) if dark else QColor(255, 255, 255, round(210 * a))
            edge = QColor(255, 255, 255, round(36 * a)) if dark else QColor(15, 35, 90, round(34 * a))
            p.setPen(QPen(edge, 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, rad, rad)
            sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.center().y()))
            sheen.setColorAt(0, QColor(255, 255, 255, round((30 if dark else 70) * a)))
            sheen.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sheen)
            p.drawRoundedRect(r, rad, rad)
        if h > 0.001 and a < 0.999:
            hov = qcolor(t["hover_overlay"])
            hov.setAlphaF(hov.alphaF() * h * (1 - a))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hov)
            p.drawRoundedRect(r, rad, rad)
        if self.private:
            tint = private_tint(dark)
            tint.setAlpha(round(30 + 18 * a))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, rad, rad)
        if a > 0.01:
            strip = private_tint(dark) if self.private else QColor(t["brand"])
            half = 9 * a
            cx = r.center().x()
            y = r.bottom() - 1.2
            glow = QColor(strip)
            glow.setAlpha(round(70 * a))
            p.setPen(_pen(glow, 4))
            p.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
            strip.setAlpha(round(255 * a))
            p.setPen(_pen(strip, 2))
            p.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))

        narrow = self.width() < 52
        ix = (self.width() - 16) / 2 if narrow else 10
        icon_rect = QRectF(ix, (self.H - 16) / 2, 16, 16)
        if self.loading:
            track = qcolor(t["divider"])
            p.setPen(_pen(track, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(icon_rect.adjusted(1.5, 1.5, -1.5, -1.5))
            p.setPen(_pen(QColor(t["brand"]), 2))
            span = 100 if motion.reduced() else 90 + 40 * math.sin(math.radians(self._spin * 2))
            p.drawArc(icon_rect.adjusted(1.5, 1.5, -1.5, -1.5), int(-self._spin * 16), int(span * 16))
        else:
            p.save()
            opacity = self._icon_in.value * (0.45 if self.sleeping else 1.0)
            p.setOpacity(opacity)
            if self.favicon is not None and not self.favicon.isNull():
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawPixmap(icon_rect, self.favicon, QRectF(self.favicon.rect()))
            elif self.private:
                tint = private_tint(dark)
                draw_icon(p, "private", icon_rect, tint.lighter(130) if dark and tint == PRIVATE_TINT else tint)
            elif self.home:
                draw_icon(p, "tab", icon_rect, qcolor(t["text_muted"]))
            else:
                draw_icon(p, "globe", icon_rect, qcolor(t["text_muted"]))
            p.restore()

        if not narrow:
            left = icon_rect.right() + 8
            right = getattr(self, "_text_right", self.width() - 8)
            if right - left > 8:
                col = _mix(qcolor(t["text_muted"]), qcolor(t["text"]), max(a, h * 0.5))
                if self.sleeping and not self.active:
                    col.setAlphaF(col.alphaF() * 0.7)
                weight = QFont.Weight.DemiBold if self.active else QFont.Weight.Medium
                _faded_text(p, QRectF(left, 0, right - left, self.H - 1), self.title, col,
                            _font(self, 12, weight), self.devicePixelRatioF())
        p.end()


class TabStrip(QWidget):
    """The row of tabs. Laid out by hand so every change animates: a new tab
    grows out of its slot, a closed one shrinks away while its neighbours
    slide over, and a dragged tab pushes the others aside as it passes."""

    new_tab_clicked = Signal()
    close_all_clicked = Signal()
    reordered = Signal(int, int)

    H = 40
    GAP = 4
    PAD_L = 8
    PAD_R = 8
    MIN_W = 40
    MAX_W = 180       # 20% narrower than the first 2.5 build's 224, as asked
    LEAD_GAP = 11     # between the close-all button and the first tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.pills = []
        self._leaving = []
        self._anim = None
        self._from = {}
        self._to = {}
        self._dragged = None
        self._grab_dx = 0
        self._t = theme.tokens(True)
        self.close_all_btn = ChromeButton("close", "Close all tabs (Ctrl+Shift+W)",
                                          size=26, icon_size=11, parent=self)
        self.close_all_btn.clicked.connect(self.close_all_clicked)
        self.new_btn = ChromeButton("plus", "New tab (Ctrl+T) -- right-click for a private tab",
                                    size=28, icon_size=15, parent=self)
        self.new_btn.clicked.connect(self.new_tab_clicked)

    def apply_theme(self, t, dark):
        self._t = t
        self.new_btn.apply_theme(t)
        self.close_all_btn.apply_theme(t)
        for pill in self.pills:
            pill.apply_theme(t, dark)
        self.update()

    def _lead(self):
        """Where the first tab starts: past the close-all button."""
        return self.PAD_L + self.close_all_btn.width() + self.LEAD_GAP

    def paintEvent(self, event):
        # A hairline between the close-all button and the tabs it closes.
        p = QPainter(self)
        x = self.PAD_L + self.close_all_btn.width() + self.LEAD_GAP / 2.0
        y = (self.H - TabPill.H) // 2 + 3
        p.fillRect(QRectF(x - 0.5, y + 7, 1, TabPill.H - 14), qcolor(self._t["divider"]))
        p.end()

    # ---- membership ----
    def add_pill(self, pill, index=None, animate=True):
        pill.setParent(self)
        if index is None or index > len(self.pills):
            index = len(self.pills)
        self.pills.insert(index, pill)
        pill.drag_moved.connect(self._on_drag)
        pill.drag_finished.connect(self._on_drop)
        targets, _ = self._targets()
        start = targets[pill]
        pill.setGeometry(QRect(start.x(), start.y(), 0 if animate else start.width(), start.height()))
        pill.show()
        self.relayout(animate)

    def remove_pill(self, pill, animate=True):
        if pill not in self.pills:
            return
        self.pills.remove(pill)
        if animate and self.isVisible() and not motion.reduced():
            self._leaving.append(pill)
            pill.setEnabled(False)
        else:
            pill.hide()
            pill.deleteLater()
        self.relayout(animate)

    # ---- geometry ----
    def _slot_width(self):
        n = max(1, len(self.pills))
        avail = self.width() - self._lead() - self.PAD_R - self.new_btn.width() - self.GAP
        w = (avail - (n - 1) * self.GAP) / n
        return max(self.MIN_W, min(self.MAX_W, w))

    def _targets(self):
        w = self._slot_width()
        y = (self.H - TabPill.H) // 2 + 3
        rects = {}
        x = float(self._lead())
        for pill in self.pills:
            rects[pill] = QRect(round(x), y, round(w), TabPill.H)
            x += w + self.GAP
        new_rect = QRect(min(round(x), self.width() - self.PAD_R - self.new_btn.width()),
                         y + (TabPill.H - self.new_btn.height()) // 2,
                         self.new_btn.width(), self.new_btn.height())
        return rects, new_rect

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout(False)

    def relayout(self, animate=True):
        targets, new_rect = self._targets()
        size = self.close_all_btn.height()
        self.close_all_btn.move(self.PAD_L, new_rect.y() + (new_rect.height() - size) // 2)
        for pill in self._leaving:
            g = pill.geometry()
            targets[pill] = QRect(g.x(), g.y(), 0, g.height())
        if self._dragged is not None:
            targets.pop(self._dragged, None)
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not animate or motion.reduced() or not self.isVisible():
            for pill, rect in targets.items():
                pill.setGeometry(rect)
            self.new_btn.setGeometry(new_rect)
            self._drop_leaving()
            return
        self._from = {pill: QRectF(pill.geometry()) for pill in targets}
        self._from[self.new_btn] = QRectF(self.new_btn.geometry())
        self._to = {pill: QRectF(rect) for pill, rect in targets.items()}
        self._to[self.new_btn] = QRectF(new_rect)
        self._anim = motion.tween(self, 0.0, 1.0, motion.MEDIUM, self._step, self._settled)

    def _step(self, v):
        for w, a in self._from.items():
            b = self._to.get(w)
            if b is None:
                continue
            w.setGeometry(QRect(round(motion.lerp(a.x(), b.x(), v)), round(motion.lerp(a.y(), b.y(), v)),
                                max(0, round(motion.lerp(a.width(), b.width(), v))),
                                round(motion.lerp(a.height(), b.height(), v))))

    def _settled(self):
        self._anim = None
        self._drop_leaving()

    def _drop_leaving(self):
        for pill in self._leaving:
            pill.hide()
            pill.deleteLater()
        self._leaving = []

    # ---- dragging ----
    def _on_drag(self, pill, global_x):
        if pill not in self.pills:
            return
        local_x = self.mapFromGlobal(QPoint(global_x, 0)).x()
        if self._dragged is None:
            self._dragged = pill
            self._grab_dx = local_x - pill.x()
            pill.raise_()
        x = max(self._lead(), min(local_x - self._grab_dx,
                                  self.width() - self.PAD_R - self.new_btn.width() - pill.width()))
        pill.move(round(x), pill.y())
        centre = x + pill.width() / 2
        targets, _ = self._targets()
        order = sorted(self.pills, key=lambda q: targets[q].center().x() if q is not pill else centre)
        new_index = order.index(pill)
        old_index = self.pills.index(pill)
        if new_index != old_index:
            self.pills.remove(pill)
            self.pills.insert(new_index, pill)
            self.reordered.emit(old_index, new_index)
            self.relayout(True)

    def _on_drop(self, pill):
        self._dragged = None
        self.relayout(True)

    def mouseDoubleClickEvent(self, event):
        # Double-clicking the empty strip opens a tab, as it does in Chrome.
        if self.childAt(event.position().toPoint()) is None:
            self.new_tab_clicked.emit()
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------- address bar ----
class _AddressEdit(QLineEdit):
    focused = Signal(bool)
    escaped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_on_click = False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit(True)
        # Select everything on the click that focuses the field, the way a
        # browser's address bar does -- but not on every later click.
        self._select_on_click = event.reason() == Qt.FocusReason.MouseFocusReason
        QTimer.singleShot(0, self.selectAll)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._select_on_click and not self.hasSelectedText():
            self.selectAll()
        self._select_on_click = False

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focused.emit(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            popup = self.completer().popup() if self.completer() else None
            if popup is not None and popup.isVisible():
                popup.hide()
                return
            self.escaped.emit()
            return
        super().keyPressEvent(event)


def display_url(url):
    """What the address field shows while you're not typing in it: the
    address without "https://" (plain "http://" stays, as the warning it
    is), and without a bare trailing slash."""
    if not url:
        return ""
    shown = url[8:] if url.startswith("https://") else url
    if shown.endswith("/") and shown.count("/") == 1:
        shown = shown[:-1]
    return shown


class AddressBar(QWidget):
    """The address and search field. Its left edge says how the page was
    reached (a lock for HTTPS, a warning for plain HTTP, a magnifier while
    typing); its right edge holds the page zoom -- only when it isn't 100%
    -- and the bookmark star."""

    submitted = Signal(str)
    escaped = Signal()
    star_clicked = Signal()
    site_clicked = Signal()
    zoom_reset = Signal()

    H = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self._url = ""
        self._t = theme.tokens(True)
        self._dark = True
        self._focus = motion.Fader(self, motion.MEDIUM)
        self._hover = motion.Fader(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(5, 0, 5, 0)
        lay.setSpacing(2)
        self.site_btn = ChromeButton("search", "", size=26, icon_size=15, parent=self)
        self.site_btn.clicked.connect(self.site_clicked)
        lay.addWidget(self.site_btn)
        self.edit = _AddressEdit(self)
        self.edit.setPlaceholderText("Search the web or type an address")
        self.edit.setFrame(False)
        self.edit.returnPressed.connect(lambda: self.submitted.emit(self.edit.text()))
        self.edit.focused.connect(self._on_focus)
        self.edit.escaped.connect(self._on_escape)
        lay.addWidget(self.edit, 1)
        self.zoom_chip = QPushButton("100%", self)
        self.zoom_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_chip.setToolTip("Page zoom -- click to reset to 100% (Ctrl+0)")
        self.zoom_chip.clicked.connect(self.zoom_reset)
        self.zoom_chip.hide()
        lay.addWidget(self.zoom_chip)
        self.star_btn = ChromeButton("star", "Bookmark this page (Ctrl+D)", size=26, icon_size=16, parent=self)
        self.star_btn.clicked.connect(self.star_clicked)
        lay.addWidget(self.star_btn)

    def apply_theme(self, t, dark):
        self._t, self._dark = t, dark
        for b in (self.site_btn, self.star_btn):
            b.apply_theme(t)
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {t['text']};"
            f" selection-background-color: {t['selection']}; font-size: 13px; padding: 0 2px; }}")
        self.zoom_chip.setStyleSheet(
            f"QPushButton {{ background: {t['hover_overlay']}; color: {t['text_muted']}; border: none;"
            f" border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600; min-height: 18px; }}"
            f"QPushButton:hover {{ background: {t['pressed_overlay']}; color: {t['text']}; }}")
        self._sync_site_icon()
        self.update()

    # ---- content ----
    def set_url(self, url):
        self._url = url or ""
        if not self.edit.hasFocus():
            self.edit.setText(display_url(self._url))
            self.edit.setCursorPosition(0)
        self._sync_site_icon()

    def url(self):
        return self._url

    def set_bookmarked(self, on):
        self.star_btn.set_kind("star_filled" if on else "star")
        self.star_btn.tint = self._t["accent"] if on else None
        self.star_btn.set_tip("Edit bookmark (Ctrl+D)" if on else "Bookmark this page (Ctrl+D)")
        self.star_btn.update()

    def set_star_visible(self, visible):
        self.star_btn.setVisible(visible)

    def set_zoom(self, factor):
        pct = round(factor * 100)
        self.zoom_chip.setText(f"{pct}%")
        self.zoom_chip.setVisible(pct != 100)

    def focus_and_select(self):
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.edit.selectAll()

    def _sync_site_icon(self):
        t = self._t
        if self.edit.hasFocus() or not self._url:
            kind, tip, tint = "search", "", None
        elif self._url.startswith("https://"):
            kind, tip, tint = "lock", "Connection is secure", None
        elif self._url.startswith("http://"):
            kind, tip, tint = "warn", "Not secure -- this page isn't using HTTPS", t["warning"]
        else:
            kind, tip, tint = "info", "", None
        self.site_btn.set_kind(kind)
        self.site_btn.set_tip(tip)
        self.site_btn.tint = tint
        self.site_btn.update()

    def _on_focus(self, focused):
        self._focus.to(1 if focused else 0)
        if focused:
            if self._url:
                self.edit.setText(self._url)
        else:
            self.edit.setText(display_url(self._url))
            self.edit.setCursorPosition(0)
        self._sync_site_icon()

    def _on_escape(self):
        self.edit.setText(self._url)
        self.edit.selectAll()
        self.escaped.emit()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = r.height() / 2
        f = self._focus.value
        if f > 0.01:
            glow = qcolor(t["focus"])
            glow.setAlphaF(0.22 * f)
            p.setPen(_pen(glow, 3.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, rad, rad)
        fill = qcolor(t["field_bg"])
        border = _mix(qcolor(t["field_border"]), qcolor(t["field_hover"]), self._hover.value)
        border = _mix(border, qcolor(t["focus"]), f)
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, rad, rad)
        p.end()


# ------------------------------------------------------------ now playing ----
class NowPlaying(QWidget):
    """What's playing in any tab, with play/pause (and next/previous on the
    sites the page script knows how to drive). Click it to go to that tab.
    From the media control in the user's own browser project."""

    activated = Signal()
    toggle_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()

    H = 30
    TEXT_MAX = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._media = None
        self._t = theme.tokens(True)
        self._hover = motion.Fader(self)
        self._phase = 0.0
        self._eq = QTimer(self)
        self._eq.setInterval(50)
        self._eq.timeout.connect(self._tick)
        self.prev_btn = ChromeButton("prev", "Previous", size=22, icon_size=11, parent=self)
        self.play_btn = ChromeButton("pause", "Pause", size=22, icon_size=11, parent=self)
        self.next_btn = ChromeButton("next", "Next", size=22, icon_size=11, parent=self)
        self.prev_btn.clicked.connect(self.prev_clicked)
        self.play_btn.clicked.connect(self.toggle_clicked)
        self.next_btn.clicked.connect(self.next_clicked)
        self.setFixedWidth(0)
        self.hide()

    def apply_theme(self, t):
        self._t = t
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.apply_theme(t)
        self.update()

    def media(self):
        return self._media

    def set_media(self, media):
        """`media` is the page's last report ({title, artist, playing,
        canSkip}), or None to fold the control away."""
        was = self._media
        self._media = media
        if media is None:
            self._eq.stop()
            if was is not None:
                motion.tween(self, self.width(), 0, motion.MEDIUM, lambda v: self.setFixedWidth(round(v)),
                             lambda: self.hide() if self._media is None else None)
            return
        playing = bool(media.get("playing"))
        self.play_btn.set_kind("pause" if playing else "play")
        self.play_btn.set_tip("Pause" if playing else "Play")
        skip = bool(media.get("canSkip"))
        self.prev_btn.setVisible(skip)
        self.next_btn.setVisible(skip)
        self.setToolTip(" -- ".join(x for x in (media.get("title"), media.get("artist")) if x))
        if playing and not motion.reduced():
            self._eq.start()
        else:
            self._eq.stop()
        target = self._natural_width()
        self._layout_buttons(target)
        if not self.isVisible():
            self.setFixedWidth(0)
            self.show()
        if self.width() != target:
            motion.tween(self, self.width(), target, motion.MEDIUM, lambda v: self.setFixedWidth(round(v)))
        self.update()

    def _text(self):
        m = self._media or {}
        return m.get("title") or m.get("artist") or "Playing"

    def _natural_width(self):
        fm = QFontMetricsF(_font(self, 12, QFont.Weight.Medium))
        text_w = min(self.TEXT_MAX, fm.horizontalAdvance(self._text()) + 2)
        buttons = 22 + (2 * 24 if (self._media or {}).get("canSkip") else 0)
        return round(10 + 16 + 8 + text_w + 6 + buttons + 5)

    def _layout_buttons(self, width):
        x = width - 5 - 22
        y = (self.H - 22) // 2
        if self.next_btn.isVisibleTo(self):
            self.next_btn.move(x, y)
            x -= 24
        self.play_btn.move(x, y)
        x -= 24
        if self.prev_btn.isVisibleTo(self):
            self.prev_btn.move(x, y)
            x -= 2
        self._text_right = x + 22 - 4 if not self.prev_btn.isVisibleTo(self) else x - 2

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._media is not None:
            self._layout_buttons(self._natural_width())

    def _tick(self):
        self._phase += 0.35
        self.update(QRect(0, 0, 30, self.H))

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if self._media is None and self.width() == 0:
            return
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = qcolor(t["hover_overlay"])
        fill.setAlphaF(min(1.0, fill.alphaF() * (1.0 + self._hover.value)))
        p.setPen(QPen(qcolor(t["card_border"]), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        brand = QColor(t["brand"])
        playing = bool((self._media or {}).get("playing"))
        for i in range(3):
            if playing and not motion.reduced():
                hgt = 4 + 8 * (0.5 + 0.5 * math.sin(self._phase * (1.0 + 0.35 * i) + i * 1.7))
            else:
                hgt = (5, 8, 4)[i]
            x = 11 + i * 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(brand)
            p.drawRoundedRect(QRectF(x, self.H / 2 + 6 - hgt, 3, hgt), 1.5, 1.5)
        right = getattr(self, "_text_right", self.width() - 30)
        left = 34
        if right - left > 10:
            _faded_text(p, QRectF(left, 0, right - left, self.H), self._text(), qcolor(t["text"]),
                        _font(self, 12, QFont.Weight.Medium), self.devicePixelRatioF())
        p.end()


# ---------------------------------------------------------- glass popups ----
class GlassPopup(QWidget):
    """A panel that opens from a toolbar button: a top-level popup (it has to
    be -- the page below is a native window), rounded, shadowed, dropping a
    few pixels into place as it fades in."""

    closed = Signal()
    MARGIN = 14

    def __init__(self, parent, width, t, dark):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._t, self._dark = t, dark
        m = self.MARGIN
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(m + 16, m + 14, m + 16, m + 14)
        self.body.setSpacing(10)
        self.setFixedWidth(width + 2 * m)

    def label(self, text, role="text", px=13, weight=QFont.Weight.Normal, wrap=False):
        lab = QLabel(text)
        lab.setWordWrap(wrap)
        color = self._t[{"text": "text", "muted": "text_muted", "faint": "text_faint",
                         "eyebrow": "eyebrow"}.get(role, "text")]
        lab.setStyleSheet(f"color: {color}; font-size: {px}px; font-weight: {int(weight.value) if hasattr(weight, 'value') else int(weight)};"
                          " background: transparent;")
        return lab

    def divider(self):
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {self._t['divider']};")
        return line

    def open_under(self, anchor, align="right"):
        self.adjustSize()
        m = self.MARGIN
        if align == "right":
            corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
            x = corner.x() - self.width() + m
        else:
            corner = anchor.mapToGlobal(QPoint(0, anchor.height()))
            x = corner.x() - m
        y = corner.y() + 6 - m
        screen = anchor.screen().availableGeometry() if anchor.screen() else None
        if screen is not None:
            x = max(screen.left() - m, min(x, screen.right() - self.width() + m))
        self.move(x, y - 6)
        self.setWindowOpacity(0.0)
        self.show()
        motion.tween(self, 0.0, 1.0, motion.MEDIUM, lambda v: (
            self.setWindowOpacity(v), self.move(x, round(y - 6 * (1 - v)))))

    def open_above(self, anchor, align="right"):
        """As open_under, for an anchor near the bottom of the screen."""
        self.adjustSize()
        m = self.MARGIN
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        if align == "right":
            x = top_left.x() + anchor.width() - self.width() + m
        else:
            x = top_left.x() - m
        y = top_left.y() - self.height() - 6 + m
        screen = anchor.screen().availableGeometry() if anchor.screen() else None
        if screen is not None:
            x = max(screen.left() - m, min(x, screen.right() - self.width() + m))
            y = max(screen.top() - m, y)
        self.move(x, y + 6)
        self.setWindowOpacity(0.0)
        self.show()
        motion.tween(self, 0.0, 1.0, motion.MEDIUM, lambda v: (
            self.setWindowOpacity(v), self.move(x, round(y + 6 * (1 - v)))))

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()

    def paintEvent(self, event):
        t, dark = self._t, self._dark
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.MARGIN
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        for i in range(m, 0, -2):
            a = 0.018 * (m - i + 2) if dark else 0.010 * (m - i + 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, round(255 * a)))
            sr = r.adjusted(-i + 2, -i + 4, i - 2, i)
            p.drawRoundedRect(sr, 14 + i / 2, 14 + i / 2)
        p.setPen(QPen(qcolor(t["card_border"]), 1))
        p.setBrush(qcolor(t["card_bg_solid"]))
        p.drawRoundedRect(r, 14, 14)
        sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + 60))
        sheen.setColorAt(0, QColor(255, 255, 255, 16 if dark else 90))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sheen)
        p.drawRoundedRect(r, 14, 14)
        p.end()


def _quiet_button(text, t):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background: {t['hover_overlay']}; color: {t['text']}; border: 1px solid {t['card_border']};"
        f" border-radius: 10px; padding: 6px 12px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {t['pressed_overlay']}; }}"
        f"QPushButton:disabled {{ color: {t['text_faint']}; }}")
    return b


def _accent_button(text, t):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t['accent_top']}, stop:1 {t['accent']});"
        f" color: {t['accent_text']}; border: none; border-radius: 10px; padding: 6px 16px;"
        f" font-size: 12px; font-weight: 700; }}"
        f"QPushButton:hover {{ background: {t['accent_hover']}; }}"
        f"QPushButton:pressed {{ background: {t['accent_pressed']}; }}")
    return b


class _ShieldMark(QWidget):
    def __init__(self, t, parent=None):
        super().__init__(parent)
        self._t = t
        self.state = "on"
        self.setFixedSize(34, 34)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._t["brand"]) if self.state == "on" else qcolor(self._t["text_faint"])
        halo = QRadialGradient(QPointF(17, 17), 17)
        c = QColor(color)
        c.setAlpha(60)
        halo.setColorAt(0.3, c)
        c.setAlpha(0)
        halo.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(0, 0, 34, 34))
        draw_icon(p, "shield_check" if self.state == "on" else "shield_off", QRectF(6, 6, 22, 22), color)
        p.end()


class AdGuardPanel(GlassPopup):
    """What AdGuard did on this page, and the two switches people actually
    reach for: allow this site, or pause everywhere."""

    site_toggled = Signal(bool)          # True = protect this site
    pause_toggled = Signal(bool)         # True = paused everywhere
    open_settings = Signal()
    open_log = Signal()

    def __init__(self, parent, t, dark, host, status, available=True, message=""):
        super().__init__(parent, 300, t, dark)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.mark = _ShieldMark(t)
        head.addWidget(self.mark)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.label("AdGuard", px=14, weight=QFont.Weight.Bold))
        self.state_label = self.label("", role="muted", px=11)
        col.addWidget(self.state_label)
        head.addLayout(col, 1)
        self.body.addLayout(head)

        if not available:
            self.mark.state = "off"
            self.state_label.setText("Not running")
            msg = self.label(message or "AdGuard isn't available in this build.", role="muted", px=12, wrap=True)
            self.body.addWidget(msg)
            return

        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        self.count_label = self.label("0", px=30, weight=QFont.Weight.Bold)
        count_row.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignBottom)
        self.count_caption = self.label("ads and trackers blocked\non this page", role="muted", px=11)
        count_row.addWidget(self.count_caption, 1, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(count_row)
        self.total_label = self.label("", role="faint", px=11)
        self.body.addWidget(self.total_label)
        self.body.addWidget(self.divider())

        site_row = QHBoxLayout()
        site_col = QVBoxLayout()
        site_col.setSpacing(1)
        site_col.addWidget(self.label("Block ads on this site", px=12, weight=QFont.Weight.DemiBold))
        self.host_label = self.label(host or "--", role="muted", px=11)
        site_col.addWidget(self.host_label)
        site_row.addLayout(site_col, 1)
        self.site_switch = Toggle()
        self.site_switch.apply_theme(t)
        self.site_switch.toggled.connect(self.site_toggled)
        site_row.addWidget(self.site_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(site_row)

        pause_row = QHBoxLayout()
        pause_row.addWidget(self.label("Pause AdGuard everywhere", px=12, weight=QFont.Weight.DemiBold), 1)
        self.pause_switch = Toggle()
        self.pause_switch.apply_theme(t)
        self.pause_switch.toggled.connect(self.pause_toggled)
        pause_row.addWidget(self.pause_switch)
        self.body.addLayout(pause_row)
        self.body.addWidget(self.divider())

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        log_btn = _quiet_button("Filtering log", t)
        log_btn.clicked.connect(lambda: (self.close(), self.open_log.emit()))
        set_btn = _quiet_button("Filters and settings", t)
        set_btn.clicked.connect(lambda: (self.close(), self.open_settings.emit()))
        buttons.addWidget(log_btn)
        buttons.addWidget(set_btn, 1)
        self.body.addLayout(buttons)
        self.body.addWidget(self.label("AdGuard AdBlocker, built in (GPL-3.0)", role="faint", px=10))
        self.set_status(status)

    def set_status(self, status):
        if not hasattr(self, "count_label"):
            return
        if not status:
            self.state_label.setText("Protecting you")
            self.count_label.setText("--")
            self.total_label.setText("")
            self.site_switch.setEnabled(False)
            return
        paused = bool(status.get("paused"))
        allowed = bool(status.get("allowlisted"))
        self.mark.state = "off" if paused or allowed else "on"
        self.mark.update()
        self.state_label.setText("Paused everywhere" if paused else
                                 "Off on this site" if allowed else "Protecting you")
        self.count_label.setText(f"{int(status.get('blocked') or 0):,}")
        total = int(status.get("total") or 0)
        self.total_label.setText(f"{total:,} blocked since it was installed" if total else "")
        self.site_switch.setChecked(not allowed)
        self.site_switch.setEnabled(bool(status.get("can_toggle", True)) and not paused)
        self.pause_switch.setChecked(paused)


class BookmarkPopup(GlassPopup):
    """Chrome's flow: the star adds the bookmark at once, then this opens to
    rename it or take it back out."""

    saved = Signal(str)
    removed = Signal()
    show_all = Signal()

    def __init__(self, parent, t, dark, title, added):
        super().__init__(parent, 300, t, dark)
        self.body.addWidget(self.label("Bookmark added" if added else "Edit bookmark",
                                       px=14, weight=QFont.Weight.Bold))
        self.body.addWidget(self.label("Name", role="muted", px=11))
        self.name = QLineEdit(title)
        self.name.setStyleSheet(
            f"QLineEdit {{ background: {t['field_bg']}; color: {t['text']}; border: 1px solid {t['field_border']};"
            f" border-radius: 9px; padding: 6px 9px; font-size: 13px;"
            f" selection-background-color: {t['selection']}; }}"
            f"QLineEdit:focus {{ border-color: {t['focus']}; }}")
        self.name.returnPressed.connect(self._save)
        self.body.addWidget(self.name)
        row = QHBoxLayout()
        remove = _quiet_button("Remove", t)
        remove.clicked.connect(lambda: (self.removed.emit(), self.close()))
        every = _quiet_button("All bookmarks", t)
        every.setToolTip("Every bookmark, in a list you can search")
        every.clicked.connect(lambda: (self._save(), self.show_all.emit()))
        done = _accent_button("Done", t)
        done.clicked.connect(self._save)
        row.addWidget(remove)
        row.addWidget(every)
        row.addStretch(1)
        row.addWidget(done)
        self.body.addLayout(row)
        QTimer.singleShot(0, lambda: (self.name.setFocus(), self.name.selectAll()))

    def _save(self):
        self.saved.emit(self.name.text().strip())
        self.close()


# ----------------------------------------------------- bookmarks panel ----
class _BookmarkRow(QAbstractButton):
    """One bookmark in the panel: its icon, its name, and the site it's on."""

    middle_clicked = Signal()
    context = Signal(QPoint)

    H = 42

    def __init__(self, url, title, t, parent=None):
        super().__init__(parent)
        from urllib.parse import urlparse
        self.url = url
        self.title = title or url
        self.host = (urlparse(url).hostname or url).removeprefix("www.")
        self._t = t
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"{self.title}\n{url}\n\nClick: new tab  ·  Middle-click: background tab")
        self._hover = motion.Fader(self)
        self._icon = favicons().get(url)

    def icon_arrived(self, host, pixmap):
        from urllib.parse import urlparse
        if urlparse(self.url).hostname == host:
            self._icon = pixmap
            self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.middle_clicked.emit()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context.emit(event.globalPosition().toPoint())
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover.value > 0 or self.isDown():
            fill = qcolor(t["pressed_overlay"] if self.isDown() else t["hover_overlay"])
            if not self.isDown():
                fill.setAlphaF(fill.alphaF() * self._hover.value)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 10, 10)
        # The icon sits on a small tile, the way Chrome's bookmark manager has it.
        tile = QRectF(8, (self.H - 28) / 2, 28, 28)
        p.setPen(QPen(qcolor(t["card_border"]), 1))
        p.setBrush(qcolor(t["hover_overlay"]))
        p.drawRoundedRect(tile, 8, 8)
        ir = QRectF(0, 0, 16, 16)
        ir.moveCenter(tile.center())
        if self._icon is not None and not self._icon.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(ir, self._icon, QRectF(self._icon.rect()))
        else:
            draw_icon(p, "globe", ir, qcolor(t["text_faint"]))
        left = tile.right() + 10
        width = self.width() - left - 10
        dpr = self.devicePixelRatioF()
        _faded_text(p, QRectF(left, 3, width, self.H / 2), self.title, qcolor(t["text"]),
                    _font(self, 12, QFont.Weight.DemiBold), dpr, fade=16)
        _faded_text(p, QRectF(left, self.H / 2 - 1, width, self.H / 2 - 3), self.host,
                    qcolor(t["text_faint"]), _font(self, 11), dpr, fade=16)
        p.end()


class BookmarksPanel(GlassPopup):
    """Every bookmark, one click away from the toolbar -- Chrome's bookmarks
    menu, as a panel you can search. A bookmark opens in a new tab (the page
    you're on stays where it is); middle-click opens it behind, and keeps
    the panel open for the next."""

    open_url = Signal(str, bool)        # url, in a background tab
    remove_requested = Signal(str)
    edit_requested = Signal(str)
    bar_toggled = Signal(bool)
    add_current = Signal()

    MAX_LIST_H = 384

    def __init__(self, parent, t, dark, items, bar_visible=False, can_add=False, added=False):
        super().__init__(parent, 340, t, dark)
        self.body.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.label("Bookmarks", px=14, weight=QFont.Weight.Bold))
        count = len([i for i in items if i.get("url")])
        head.addWidget(self.label(str(count) if count else "", role="faint", px=11), 0,
                       Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.add_btn = ChromeButton("star_filled" if added else "star",
                                    "This page is bookmarked" if added else "Bookmark this page (Ctrl+D)",
                                    size=28, icon_size=15)
        self.add_btn.apply_theme(t)
        if added:
            self.add_btn.tint = QColor(t["brand"])
        self.add_btn.setEnabled(can_add and not added)
        self.add_btn.clicked.connect(lambda: (self.close(), self.add_current.emit()))
        head.addWidget(self.add_btn)
        self.body.addLayout(head)

        self.search = None
        if count > 6:
            self.search = QLineEdit()
            self.search.setPlaceholderText("Search bookmarks")
            self.search.setClearButtonEnabled(True)
            self.search.setStyleSheet(
                f"QLineEdit {{ background: {t['field_bg']}; color: {t['text']}; border: 1px solid {t['field_border']};"
                f" border-radius: 10px; padding: 6px 10px; font-size: 12px;"
                f" selection-background-color: {t['selection']}; }}"
                f"QLineEdit:focus {{ border-color: {t['focus']}; }}")
            self.search.textChanged.connect(self._filter)
            self.search.returnPressed.connect(self._open_first)
            self.body.addWidget(self.search)

        self.rows = []
        listing = QWidget()
        listing.setStyleSheet("background: transparent;")
        col = QVBoxLayout(listing)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        for item in items:
            url = item.get("url")
            if not url:
                continue
            row = _BookmarkRow(url, item.get("title"), t)
            row.clicked.connect(lambda _c=False, u=url: (self.close(), self.open_url.emit(u, False)))
            row.middle_clicked.connect(lambda u=url: self.open_url.emit(u, True))
            row.context.connect(lambda pos, u=url: self._row_menu(u, pos))
            col.addWidget(row)
            self.rows.append(row)
        col.addStretch(1)
        favicons().ready.connect(self._icon_arrived)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll.setWidget(listing)
        rows_h = len(self.rows) * (_BookmarkRow.H + 1)
        self.scroll.setFixedHeight(max(1, min(self.MAX_LIST_H, rows_h)))
        # Into the panel first, then shown or hidden: setVisible(True) on a
        # widget with no parent yet opens it as a window of its own. That
        # window flashed up for a few ms, took the activation, and Qt closed
        # the panel as it went -- All bookmarks opened and shut in a blink.
        self.body.addWidget(self.scroll)
        self.scroll.setVisible(bool(self.rows))

        self.empty = self.label("Nothing saved yet. Click the star in the address bar, or press "
                                "Ctrl+D, to keep the page you're on here.", role="muted", px=12, wrap=True)
        self.body.addWidget(self.empty)
        self.empty.setVisible(not self.rows)

        self.body.addWidget(self.divider())
        bar_row = QHBoxLayout()
        bar_row.addWidget(self.label("Show bookmarks bar", px=12, weight=QFont.Weight.DemiBold), 1)
        bar_row.addWidget(self.label("Ctrl+Shift+B", role="faint", px=11))
        self.bar_switch = Toggle()
        self.bar_switch.apply_theme(t)
        self.bar_switch.setChecked(bar_visible)
        self.bar_switch.toggled.connect(self.bar_toggled)
        bar_row.addWidget(self.bar_switch)
        self.body.addLayout(bar_row)
        if self.search is not None:
            QTimer.singleShot(0, self.search.setFocus)

    def _icon_arrived(self, host, pixmap):
        for row in self.rows:
            row.icon_arrived(host, pixmap)

    def _filter(self, text):
        words = text.lower().split()
        for row in self.rows:
            hay = (row.title + " " + row.url).lower()
            row.setVisible(all(w in hay for w in words))

    def _open_first(self):
        row = next((r for r in self.rows if r.isVisible()), None)
        if row is not None:
            self.close()
            self.open_url.emit(row.url, False)

    def _row_menu(self, url, pos):
        menu = style_menu(QMenu(self), self._t)
        menu.addAction("Open in new tab").triggered.connect(
            lambda: (self.close(), self.open_url.emit(url, False)))
        menu.addAction("Open in background tab").triggered.connect(lambda: self.open_url.emit(url, True))
        menu.addSeparator()
        # The editor is a window of its own: the panel closes first.
        menu.addAction("Edit\u2026").triggered.connect(lambda: (self.close(), self.edit_requested.emit(url)))

        def remove():
            self.remove_requested.emit(url)
            for row in self.rows:
                if row.url == url:
                    row.hide()
        menu.addAction("Remove bookmark").triggered.connect(remove)
        menu.exec(pos)


# ------------------------------------------------------- bookmarks bar ----
def _pil_to_pixmap_rgba(img):
    """A PIL image, with its transparency, as a QPixmap -- favicons commonly
    carry real transparency (a logo on a clear square)."""
    if img is None:
        return None
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class _FaviconCache(QObject):
    """Site icons for the bookmarks bar, fetched once per host off the GUI
    thread (app.core.favicon) and shared by every chip."""

    ready = Signal(str, object)     # host, QPixmap

    def __init__(self):
        super().__init__()
        self._icons = {}
        self._pending = set()
        self._arrived.connect(self._store)

    _arrived = Signal(str, object)  # host, PIL image or None

    def get(self, url):
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if not host:
            return None
        if host in self._icons:
            return self._icons[host]
        if host not in self._pending:
            self._pending.add(host)
            threading.Thread(target=self._fetch, args=(host, url), daemon=True).start()
        return None

    def _fetch(self, host, url):
        img = None
        try:
            from app.core import favicon
            img = favicon.get_favicon(url, size=32)
        except Exception:   # noqa: BLE001 -- an icon is decoration
            img = None
        self._arrived.emit(host, img)

    def _store(self, host, img):
        pm = None
        if img is not None:
            try:
                pm = _pil_to_pixmap_rgba(img)
            except Exception:   # noqa: BLE001
                pm = None
        self._icons[host] = pm
        self._pending.discard(host)
        if pm is not None:
            self.ready.emit(host, pm)


_favicons = None


def favicons():
    global _favicons
    if _favicons is None:
        _favicons = _FaviconCache()
    return _favicons


class _BookmarkChip(QAbstractButton):
    middle_clicked = Signal()
    context = Signal(QPoint)

    MAX_W = 170

    def __init__(self, url, title, parent=None):
        super().__init__(parent)
        self.url = url
        self.title = title or url
        self.setToolTip(f"{self.title}\n{url}")
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = motion.Fader(self)
        self._t = theme.tokens(True)
        self._icon = favicons().get(url)
        fm = QFontMetricsF(_font(self, 12, QFont.Weight.Medium))
        self.setFixedWidth(round(min(self.MAX_W, 8 + 16 + 6 + fm.horizontalAdvance(self.title) + 10)))

    def apply_theme(self, t):
        self._t = t
        self.update()

    def icon_arrived(self, host, pixmap):
        from urllib.parse import urlparse
        if urlparse(self.url).hostname == host:
            self._icon = pixmap
            self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.middle_clicked.emit()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context.emit(event.globalPosition().toPoint())
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover.value > 0 or self.isDown():
            fill = qcolor(t["pressed_overlay"] if self.isDown() else t["hover_overlay"])
            if not self.isDown():
                fill.setAlphaF(fill.alphaF() * self._hover.value)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 8, 8)
        ir = QRectF(8, (self.height() - 16) / 2, 16, 16)
        if self._icon is not None and not self._icon.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(ir, self._icon, QRectF(self._icon.rect()))
        else:
            draw_icon(p, "globe", ir, qcolor(t["text_faint"]))
        _faded_text(p, QRectF(ir.right() + 6, 0, self.width() - ir.right() - 12, self.height()), self.title,
                    qcolor(t["text_muted"]), _font(self, 12, QFont.Weight.Medium), self.devicePixelRatioF(),
                    fade=14)
        p.end()


class _AllBookmarksButton(QAbstractButton):
    """Chrome's "All Bookmarks", at the bookmarks bar's right end: a word
    beside the icon, so it can't be mistaken for the toolbar's bookmark
    button (it was an icon of its own, and two bookmark icons side by side
    read as the same thing twice -- reported)."""

    TEXT = "All bookmarks"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText(self.TEXT)
        self.setToolTip("Every bookmark, in a list you can search")
        self.setAccessibleName(self.TEXT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(26)
        self._t = theme.tokens(True)
        self._hover = motion.Fader(self)
        fm = QFontMetricsF(_font(self, 12, QFont.Weight.Medium))
        self.setFixedWidth(round(10 + 15 + 7 + fm.horizontalAdvance(self.TEXT) + 12))

    def apply_theme(self, t):
        self._t = t
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = self._hover.value
        fill = qcolor(t["pressed_overlay"] if self.isDown() else t["hover_overlay"])
        if not self.isDown():
            fill.setAlphaF(fill.alphaF() * (0.45 + 0.55 * h))
        p.setPen(QPen(qcolor(t["card_border"]), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        color = qcolor(t["text"] if h > 0.5 or self.isDown() else t["text_muted"])
        draw_icon(p, "bookmarks", QRectF(10, (self.height() - 15) / 2, 15, 15), color)
        p.setFont(_font(self, 12, QFont.Weight.Medium))
        p.setPen(color)
        p.drawText(QRectF(32, 0, self.width() - 36, self.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.TEXT)
        p.end()


class BookmarksBar(QWidget):
    """A row of bookmark chips under the toolbar; the ones that don't fit
    wait behind a >> button."""

    open_url = Signal(str, bool)       # url, in a background tab
    remove_requested = Signal(str)
    edit_requested = Signal(str)
    all_clicked = Signal()

    # Lined up with the toolbar above it: the same side insets as its
    # buttons, and the toolbar's own bottom margin repeated under the chips,
    # so they sit midway between the address bar and the page's hairline
    # (centred in a 32px bar they had 11px above and 2px below -- reported
    # as misaligned, the All bookmarks pill hugging the line).
    CHIP = 26
    GAP = 8
    LEFT, RIGHT = 8, 10
    H = CHIP + GAP + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self._chips = []
        self._items = []
        self._t = theme.tokens(True)
        self._dark = True
        self.more_btn = ChromeButton("chevrons", "More bookmarks", size=26, icon_size=14, parent=self)
        self.more_btn.clicked.connect(self._show_more)
        # Chrome's "All Bookmarks", at the bar's right end.
        self.all_btn = _AllBookmarksButton(self)
        self.all_btn.clicked.connect(self.all_clicked)
        self.empty = QLabel("Bookmark a page with the bookmark button in the toolbar (Ctrl+D), "
                            "and it shows up here.", self)
        favicons().ready.connect(self._icon_arrived)

    def apply_theme(self, t, dark):
        self._t, self._dark = t, dark
        self.more_btn.apply_theme(t)
        self.all_btn.apply_theme(t)
        self.empty.setStyleSheet(f"color: {t['text_faint']}; font-size: 11px; background: transparent;")
        for chip in self._chips:
            chip.apply_theme(t)

    def set_bookmarks(self, items):
        self._items = list(items)
        for chip in self._chips:
            chip.deleteLater()
        self._chips = []
        for item in self._items:
            url = item.get("url")
            if not url:
                continue
            chip = _BookmarkChip(url, item.get("title"), self)
            chip.apply_theme(self._t)
            chip.clicked.connect(lambda _c=False, u=url: self.open_url.emit(u, False))
            chip.middle_clicked.connect(lambda u=url: self.open_url.emit(u, True))
            chip.context.connect(lambda pos, u=url: self._chip_menu(u, pos))
            self._chips.append(chip)
        self.empty.setVisible(not self._chips)
        self._layout()

    def _icon_arrived(self, host, pixmap):
        for chip in self._chips:
            chip.icon_arrived(host, pixmap)

    def flash(self, url):
        """Draws the eye to a bookmark just added: its chip lights up, then
        settles back."""
        for chip in self._chips:
            if chip.url == url and chip.isVisible():
                chip._hover.snap(1)
                QTimer.singleShot(1400, lambda c=chip: c._hover.to(0))
                return

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout()

    def _layout(self):
        x = self.LEFT
        y = 0
        self.all_btn.move(self.width() - self.RIGHT - self.all_btn.width(), y)
        right = self.all_btn.x() - 6
        limit = right - self.more_btn.width() - 4
        hidden = False
        for chip in self._chips:
            if not hidden and x + chip.width() <= limit:
                chip.move(x, y)
                chip.show()
                x += chip.width() + 2
            else:
                hidden = True
                chip.hide()
        self.more_btn.setVisible(hidden)
        self.more_btn.move(right - self.more_btn.width(), y + (self.CHIP - self.more_btn.height()) // 2)
        self.empty.adjustSize()
        self.empty.move(self.LEFT + 4, y + (self.CHIP - self.empty.height()) // 2)

    def _show_more(self):
        menu = style_menu(QMenu(self), self._t)
        for chip in self._chips:
            if not chip.isVisible():
                act = menu.addAction(chip.title[:60])
                act.triggered.connect(lambda _c=False, u=chip.url: self.open_url.emit(u, False))
        menu.exec(self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height())))

    def _chip_menu(self, url, pos):
        menu = style_menu(QMenu(self), self._t)
        menu.addAction("Open in new tab").triggered.connect(lambda: self.open_url.emit(url, False))
        menu.addAction("Open in background tab").triggered.connect(lambda: self.open_url.emit(url, True))
        menu.addSeparator()
        menu.addAction("Edit\u2026").triggered.connect(lambda: self.edit_requested.emit(url))
        menu.addAction("Remove bookmark").triggered.connect(lambda: self.remove_requested.emit(url))
        menu.exec(pos)


# ------------------------------------------------------- engine missing ----
class EngineMissing(QWidget):
    """Shown in place of pages when WebView2 can't be loaded -- rare on
    Windows 10 and 11, where it ships with the system."""

    get_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from .widgets.empty_state import EmptyState
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        self.state = EmptyState(
            "link", "The Browser needs Microsoft Edge WebView2",
            "It's part of Windows 10 and 11, but it's missing or damaged here. "
            "Installing it takes a minute; the rest of the app works without it.")
        lay.addWidget(self.state)
        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton("Get WebView2")
        self.button.setObjectName("accent")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.get_clicked)
        row.addWidget(self.button)
        row.addStretch(1)
        lay.addLayout(row)
        self.detail = QLabel("")
        self.detail.setObjectName("faint")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        lay.addStretch(2)

    def set_error(self, text):
        self.detail.setText(text or "")
