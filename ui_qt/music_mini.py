"""The mini player: for when the app is out of sight. A square of the
song's cover inside a thin dark rim, floating above everything. Under the
mouse, its controls come up over the cover -- a frosted pill at the top with
the song and who sings it, a heart and close beside it; at the bottom the
time gone and the time left over a thin line, and round frosted buttons:
back 10 s, previous, play / pause, next, forward 10 s -- and when the mouse
leaves they fade away, leaving just the cover.

It appears when the app is minimised or another tab is in front while a
song plays, and goes when the Music tab is back. Drag it anywhere; drag a
corner to make it bigger or smaller (it stays square, and keeps its size
for next time); double-click it (or click the pill) to bring the app back.
Closed, it stays away until you come back to the Music tab."""
from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, \
    QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget

from app.utils import ui_state
from app.utils.formatting import format_eta

from . import music_look as look
from .browser_chrome import draw_icon
from .music_widgets import ThinSlider


class _Round(QAbstractButton):
    """A round frosted button: the mini player paints the frost under it,
    this draws the icon (and lightens on hover, dips on a press)."""

    def __init__(self, kind, tip, size, icon, parent=None):
        super().__init__(parent)
        self.kind, self.base, self.base_icon = kind, size, icon
        self.icon_px = icon
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False

    def scale(self, k):
        n = max(20, int(round(self.base * k)))
        self.setFixedSize(n, n)
        self.icon_px = self.base_icon * k

    def set_kind(self, kind, tip=None):
        self.kind = kind
        if tip:
            self.setToolTip(tip)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 18 if self.isDown() else 34))
            p.drawEllipse(r)
        s = self.icon_px * (0.92 if self.isDown() else 1.0)
        c = QPointF(r.center())
        colour = QColor("#ff5a6e") if self.kind == "heart_filled" else QColor(255, 255, 255)
        draw_icon(p, self.kind, QRectF(c.x() - s / 2, c.y() - s / 2, s, s), colour)
        p.end()


class _Pill(QAbstractButton):
    """The song and its singer, by a little round cover -- a click brings
    the app back."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Back to the app")
        self.title, self.artist, self.thumb = "", "", None
        self.scale(1.0)

    def scale(self, k):
        self.f_title = look.font(13 * k, QFont.Weight.Bold)
        self.f_artist = look.font(11 * k, QFont.Weight.Medium)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        h = self.height()
        inset = h * 0.11
        d = h - 2 * inset
        disc = QRectF(inset, inset, d, d)
        if self.thumb is not None:
            path = QPainterPath()
            path.addEllipse(disc)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(disc, self.thumb, QRectF(self.thumb.rect()))
            p.restore()
        x = disc.right() + h * 0.22
        w = self.width() - x - h * 0.3
        p.setPen(QColor(255, 255, 255))
        p.setFont(self.f_title)
        fm = QFontMetricsF(self.f_title)
        p.drawText(QRectF(x, h / 2 - fm.height() + 1, w, fm.height()), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, fm.elidedText(self.title, Qt.TextElideMode.ElideRight, w))
        p.setPen(QColor(255, 255, 255, 175))
        p.setFont(self.f_artist)
        fa = QFontMetricsF(self.f_artist)
        p.drawText(QRectF(x, h / 2 + 1, w, fa.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fa.elidedText(self.artist, Qt.TextElideMode.ElideRight, w))
        p.end()


class MiniPlayer(QWidget):
    restore_requested = Signal()
    closed_by_user = Signal()
    like_requested = Signal()
    BASE = 312                  # the size everything below is drawn for; it scales from there
    MIN_SIDE, MAX_SIDE = 200, 560
    SIDE = 312                  # its size, until it's resized
    RIM = 8                     # the thin dark rim round the cover
    INNER_R = 26
    GRIP = 18                   # how far in from a corner a drag resizes it
    HIDE_AFTER_MS = 700         # the controls fade this long after the mouse leaves

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)   # never what keeps the app running
        self.setWindowTitle("Awesome Downloader — mini player")
        self.setMouseTracking(True)
        self._pal = look.DEFAULT
        self._cover = self._blur = None
        self._drag = None
        self._resize = None             # (the fixed corner, global) while a corner is dragged
        self.moved_by_user = False
        self._ms = self._dur = 0
        self.k = 1.0

        self.pill = _Pill(self)
        self.pill.clicked.connect(self.restore_requested)
        self.heart_btn = _Round("heart", "Like", 40, 17, self)
        self.heart_btn.clicked.connect(self.like_requested)
        self.close_btn = _Round("close", "Close the mini player", 40, 13, self)
        self.close_btn.clicked.connect(self._close)
        self.back10_btn = _Round("back10", "Back 10 seconds", 36, 20, self)
        self.prev_btn = _Round("prev", "Previous", 42, 17, self)
        self.play_btn = _Round("play", "Play", 50, 20, self)
        self.next_btn = _Round("next", "Next", 42, 17, self)
        self.fwd10_btn = _Round("fwd10", "Forward 10 seconds", 36, 20, self)
        self.seek = ThinSlider(parent=self, knob=False)
        self.seek.accent = QColor(255, 255, 255)
        self.seek.valueChanged.connect(lambda _v: self.update(self._times_rect().toAlignedRect()))
        self.rounds = [self.heart_btn, self.close_btn, self.back10_btn, self.prev_btn, self.play_btn,
                       self.next_btn, self.fwd10_btn]
        self.controls = [self.pill, self.seek] + self.rounds
        self._effects = []
        for w in self.controls:
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
            w.hide()
            self._effects.append(eff)

        side = ui_state.get("mini_side")
        side = int(side) if isinstance(side, (int, float)) else self.SIDE
        self.resize_to(max(self.MIN_SIDE, min(self.MAX_SIDE, side)))

        # the window fading in and out
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(240)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        self._fade.finished.connect(self._fade_done)
        self._fading_out = False
        # the controls coming up under the mouse, and going after it leaves
        self.ui = 0.0
        self._ui_anim = QVariantAnimation(self)
        self._ui_anim.setDuration(220)
        self._ui_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ui_anim.valueChanged.connect(self._set_ui)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_AFTER_MS)
        self._hide_timer.timeout.connect(self._maybe_hide_controls)

    # ---- size, and where everything goes ----
    def resize_to(self, side):
        side = int(max(self.MIN_SIDE, min(self.MAX_SIDE, side)))
        self.setFixedSize(side, side)
        self.k = side / float(self.BASE)
        self._layout()
        self.update()

    def _rim(self):
        return max(5.0, self.RIM * self.k)

    def _outer(self):
        return QRectF(self.rect()).adjusted(3, 3, -3, -3)

    def _inner(self):
        rim = self._rim()
        return self._outer().adjusted(rim, rim, -rim, -rim)

    def _times_rect(self):
        i, k = self._inner(), self.k
        return QRectF(i.x() + 16 * k, i.bottom() - 106 * k, i.width() - 32 * k, 18 * k)

    def _layout(self):
        i, k = self._inner(), self.k
        for b in self.rounds:
            b.scale(k)
        self.pill.scale(k)
        pad = 12 * k
        top = i.y() + pad
        rb = self.close_btn.width()
        self.close_btn.move(int(i.right() - pad - rb), int(top + 2 * k))
        self.heart_btn.move(int(i.right() - pad - rb - 8 * k - rb), int(top + 2 * k))
        self.pill.setGeometry(int(i.x() + pad), int(top), int(self.heart_btn.x() - 10 * k - (i.x() + pad)),
                              int(44 * k))
        self.seek.setGeometry(int(i.x() + 16 * k), int(i.bottom() - 86 * k), int(i.width() - 32 * k),
                              max(10, int(14 * k)))
        row = [self.back10_btn, self.prev_btn, self.play_btn, self.next_btn, self.fwd10_btn]
        gap = 8 * k
        total = sum(b.width() for b in row) + gap * (len(row) - 1)
        x = i.center().x() - total / 2
        cy = i.bottom() - 40 * k
        for b in row:
            b.move(int(round(x)), int(round(cy - b.height() / 2)))
            x += b.width() + gap

    # ---- what it shows ----
    def set_track(self, title, artist, pixmap, pal, frost=None):
        self.pill.title, self.pill.artist = title or "", artist or ""
        self._pal = pal or look.DEFAULT
        self._cover = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._blur = None
        if self._cover is not None:
            small = self._cover.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            self._blur = small.scaled(self._cover.width(), self._cover.height(),
                                      Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.pill.thumb = self._cover.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                 Qt.TransformationMode.SmoothTransformation)
        else:
            self.pill.thumb = None
        self.pill.update()
        self.update()

    def set_liked(self, liked):
        self.heart_btn.set_kind("heart_filled" if liked else "heart", "Unlike" if liked else "Like")

    def set_playing(self, playing):
        self.play_btn.set_kind("pause" if playing else "play", "Pause" if playing else "Play")

    def set_position(self, ms, duration):
        self._ms, self._dur = int(ms), int(duration)
        if self.seek.isSliderDown():
            return
        self.seek.blockSignals(True)
        self.seek.setRange(0, max(0, int(duration)))
        self.seek.setValue(int(ms))
        self.seek.blockSignals(False)
        if self.ui > 0.01:
            self.update(self._times_rect().toAlignedRect())

    # ---- the controls: there under the mouse, gone when it leaves ----
    def _set_ui(self, v):
        self.ui = float(v)
        on = self.ui > 0.01
        for w, eff in zip(self.controls, self._effects):
            eff.setOpacity(self.ui)
            if w.isVisible() != on:
                w.setVisible(on)
        self.update()

    def show_controls(self, on):
        target = 1.0 if on else 0.0
        if on:
            self._hide_timer.stop()
        if abs(self.ui - target) < 0.01 and self._ui_anim.state() != QVariantAnimation.State.Running:
            return
        self._ui_anim.stop()
        self._ui_anim.setDuration(180 if on else 420)
        self._ui_anim.setStartValue(self.ui)
        self._ui_anim.setEndValue(target)
        self._ui_anim.start()

    def _maybe_hide_controls(self):
        if self._drag is not None or self._resize is not None or self.seek.isSliderDown():
            self._hide_timer.start()
            return
        if self.isVisible() and self.frameGeometry().contains(QCursor.pos()):
            return                      # still over it (on a button, say)
        self.show_controls(False)

    def enterEvent(self, e):
        self.show_controls(True)

    def leaveEvent(self, e):
        self._hide_timer.start()
        if self._resize is None:
            self.unsetCursor()

    # ---- where it is ----
    def show_mini(self):
        """Shows it (or keeps it, if it's on its way out)."""
        if self.isVisible() and not self._fading_out:
            self.raise_()
            return
        if not self.isVisible() and (not self.moved_by_user or not self._on_a_screen()):
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 20)
        start = self.windowOpacity() if self.isVisible() else 0.0
        self._fading_out = False
        self.setWindowOpacity(start)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(start)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def hide_mini(self):
        if not self.isVisible() or self._fading_out:
            return
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _fade_done(self):
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self.setWindowOpacity(1.0)

    def _on_a_screen(self):
        return any(s.availableGeometry().intersects(self.frameGeometry()) for s in QGuiApplication.screens())

    def _close(self):
        self.hide_mini()
        self.closed_by_user.emit()

    # ---- moving it, and sizing it by a corner ----
    def _corner_at(self, pos):
        g = self.GRIP
        w, h = self.width(), self.height()
        left, right = pos.x() <= g, pos.x() >= w - g
        top, bottom = pos.y() <= g, pos.y() >= h - g
        if (left or right) and (top or bottom):
            return ("l" if left else "r") + ("t" if top else "b")
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        corner = self._corner_at(e.position().toPoint())
        geo = self.frameGeometry()
        if corner:
            # the corner opposite the one dragged stays where it is
            fixed_x = geo.right() if corner[0] == "l" else geo.left()
            fixed_y = geo.bottom() if corner[1] == "t" else geo.top()
            self._resize = (corner, QPoint(fixed_x, fixed_y))
        else:
            self._drag = e.globalPosition().toPoint() - geo.topLeft()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._resize is not None and e.buttons() & Qt.MouseButton.LeftButton:
            corner, fixed = self._resize
            g = e.globalPosition().toPoint()
            side = max(abs(g.x() - fixed.x()), abs(g.y() - fixed.y()))
            side = int(max(self.MIN_SIDE, min(self.MAX_SIDE, side)))
            x = fixed.x() - side + 1 if corner[0] == "l" else fixed.x()
            y = fixed.y() - side + 1 if corner[1] == "t" else fixed.y()
            self.resize_to(side)
            self.move(x, y)
            self.moved_by_user = True
            e.accept()
            return
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            self.moved_by_user = True
            e.accept()
            return
        corner = self._corner_at(e.position().toPoint())
        if corner in ("lt", "rb"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif corner in ("rt", "lb"):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        if self._resize is not None:
            ui_state.put("mini_side", self.width())
        self._drag = None
        self._resize = None

    def mouseDoubleClickEvent(self, e):
        if self._corner_at(e.position().toPoint()) is None:
            self.restore_requested.emit()

    # ---- the look ----
    def _cover_rect(self, pm, into):
        """The part of `pm` that fills `into` (cropped, not squashed)."""
        sw, sh = pm.width(), pm.height()
        scale = max(into.width() / sw, into.height() / sh)
        w, h = into.width() / scale, into.height() / scale
        return QRectF((sw - w) / 2, (sh - h) / 2, w, h)

    def _frost(self, p, path, inner):
        """Frosted glass in `path`: the cover blurred under it, a little darker."""
        p.save()
        p.setOpacity(self.ui)
        p.setClipPath(path)
        if self._blur is not None:
            p.drawPixmap(inner, self._blur, self._cover_rect(self._blur, inner))
        p.fillPath(path, QColor(24, 22, 22, 95))
        p.fillPath(path, QColor(255, 255, 255, 26))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = self._outer()
        inner_r = self.INNER_R * self.k
        outer_r = inner_r + self._rim()
        # the rim: dark, soft, a touch of the cover's colour
        rim = QPainterPath()
        rim.addRoundedRect(outer, outer_r, outer_r)
        deep = QColor(self._pal.get("deep", QColor(30, 26, 24)))
        p.fillPath(rim, look.mix(deep, QColor(14, 12, 12), 0.65))
        g = QLinearGradient(outer.topLeft(), outer.bottomLeft())
        g.setColorAt(0, QColor(255, 255, 255, 26))
        g.setColorAt(0.5, QColor(255, 255, 255, 6))
        g.setColorAt(1, QColor(0, 0, 0, 40))
        p.fillPath(rim, g)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawRoundedRect(outer.adjusted(0.5, 0.5, -0.5, -0.5), outer_r, outer_r)
        # the cover, filling the card -- shaded top and bottom only while the controls are up
        inner = self._inner()
        card = QPainterPath()
        card.addRoundedRect(inner, inner_r, inner_r)
        p.save()
        p.setClipPath(card)
        if self._cover is not None:
            p.drawPixmap(inner, self._cover, self._cover_rect(self._cover, inner))
        else:
            p.fillRect(inner, QColor(self._pal.get("base", QColor(60, 52, 48))))
        if self.ui > 0.01:
            k = self.k
            p.setOpacity(self.ui)
            top = QLinearGradient(inner.topLeft(), QPointF(inner.x(), inner.y() + 90 * k))
            top.setColorAt(0, QColor(0, 0, 0, 80))
            top.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(inner, top)
            foot = QLinearGradient(QPointF(inner.x(), inner.bottom() - 150 * k), inner.bottomLeft())
            foot.setColorAt(0, QColor(0, 0, 0, 0))
            foot.setColorAt(0.55, QColor(0, 0, 0, 110))
            foot.setColorAt(1, QColor(0, 0, 0, 180))
            p.fillRect(inner, foot)
        p.restore()
        if self.ui <= 0.01:
            p.end()
            return
        # frosted glass under the pill and each round button
        pill = QPainterPath()
        pr = QRectF(self.pill.geometry())
        pill.addRoundedRect(pr, pr.height() / 2, pr.height() / 2)
        self._frost(p, pill, inner)
        for b in self.rounds:
            path = QPainterPath()
            path.addEllipse(QRectF(b.geometry()).adjusted(0.5, 0.5, -0.5, -0.5))
            self._frost(p, path, inner)
        # the time gone, the time left
        t = self._times_rect()
        p.setOpacity(self.ui)
        p.setFont(look.font(11 * self.k, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(t, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, format_eta(self._ms // 1000))
        left = max(0, self._dur - self._ms) // 1000
        p.drawText(t, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "-" + format_eta(left) if self._dur else "")
        p.end()

