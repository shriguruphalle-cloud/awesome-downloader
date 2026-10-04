"""History tab: everything downloaded, newest first.

Each row is a pane of glass with the file's thumbnail, what it is, and the
two things done with it most (Play, Open Folder; the rest under the "..."
menu). Rows can be selected -- the box on each row, a click anywhere on it,
Shift+click for a run of rows, Ctrl+A for all -- and while anything is
selected a bar rises from the bottom with what to do with the lot: remove
them from this list, or delete the files. Bulk deletes go to the Recycle
Bin, so a slip of the mouse is one Restore away.

A filter (All / Videos / Audio / Images / Torrents) and a search narrow the
list; "select all" means everything the filter shows.
"""
import os
import threading
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QScrollArea,
    QVBoxLayout, QWidget,
)

from app import config
from app.core import thumbnails
from app.logging_setup import get_logger
from app.utils import download_history, recycle
from app.utils.formatting import humanize_size

from . import cinema, motion, theme
from .theme import qcolor
from .widgets import EmptyState, RoundedImage, centered_column, section_label
from .widgets.button import Button

logger = get_logger("history_tab")

# A word in the placeholder rather than an emoji: the emoji rendered in the
# OS colour font, ignored the theme, and was the loudest thing on every row.
_KIND_LABEL = {"video": "VIDEO", "audio": "AUDIO", "torrent": "TORRENT", "image": "IMAGE"}
_FILTERS = (("all", "All"), ("video", "Videos"), ("audio", "Audio"), ("image", "Images"), ("torrent", "Torrents"))
_THUMB_W, _THUMB_H = 104, 58      # 16:9
MAX_CONTENT_W = 1760


def _pil_to_pixmap(img):
    if img is None:
        return None
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def _key(entry):
    """A stable identity for an entry -- list positions move as rows go."""
    return (entry.get("completed_at"), entry.get("file_path"), entry.get("title"))


# ------------------------------------------------------------------ pieces
class _Check(QAbstractButton):
    """A round-cornered check box, painted: on, off, or partly (a dash)."""

    S = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.S + 4, self.S + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.partial = False
        self._on = motion.Fader(self, motion.FAST)
        self._hover = motion.Fader(self)
        self.toggled.connect(lambda on: self._on.to(1 if on else 0))

    def set_state(self, checked, partial=False):
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self.partial = partial and not checked
        self._on.snap(1 if (checked or self.partial) else 0)
        self.update()

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def sizeHint(self):
        return QSize(self.S + 4, self.S + 4)

    def paintEvent(self, event):
        t = theme.tokens(cinema.is_dark(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, self.S, self.S)
        on = max(self._on.value, 1.0 if self.partial else 0.0)
        edge = qcolor(t["text_muted"])
        edge.setAlphaF(0.55 + 0.35 * self._hover.value)
        fill = QColor(t["accent"])
        fill.setAlphaF(on)
        p.setPen(QPen(edge if on < 0.5 else fill, 1.4))
        p.setBrush(fill if on > 0.01 else qcolor(t["field_bg"]))
        p.drawRoundedRect(r, 6, 6)
        if on > 0.01:
            mark = QColor(t["accent_text"])
            mark.setAlphaF(on)
            pen = QPen(mark, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            c = r.center()
            if self.partial:
                p.drawLine(QPointF(c.x() - 4.5, c.y()), QPointF(c.x() + 4.5, c.y()))
            else:
                p.drawPolyline([QPointF(c.x() - 4.6, c.y() + 0.2), QPointF(c.x() - 1.2, c.y() + 3.4),
                                QPointF(c.x() + 4.8, c.y() - 3.6)])
        p.end()


class _Chip(QAbstractButton):
    """A filter: label and count, a soft pill when on."""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.count = 0
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(30)
        self._hover = motion.Fader(self)

    def set_count(self, n):
        self.count = n
        f = self._font()
        fm = QFontMetricsF(f)
        self.setFixedWidth(int(fm.horizontalAdvance(self._text()) + 26))
        self.update()

    def _text(self):
        return f"{self.label}  {self.count}" if self.count else self.label

    def _font(self):
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Medium)
        return f

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = theme.tokens(cinema.is_dark(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            fill = qcolor(t["pressed_overlay"])
            p.setPen(QPen(qcolor(t["card_border"]), 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        elif self._hover.value > 0:
            fill = qcolor(t["hover_overlay"])
            fill.setAlphaF(fill.alphaF() * self._hover.value)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(self._font())
        p.setPen(qcolor(t["text"] if self.isChecked() else t["text_muted"]))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self._text())
        p.end()


class _Row(QWidget):
    """One download: glass, its thumbnail, what it is, and its actions."""

    toggled = Signal(object, bool, bool)      # row, checked, shift held
    activated = Signal(object)                # double-click: open it

    H = 82

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.key = _key(entry)
        self.selected = False
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sel = motion.Fader(self, motion.FAST)
        self._hover = motion.Fader(self)
        path = entry.get("file_path")
        self.missing = not path or not os.path.exists(path)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 14, 10)
        lay.setSpacing(14)
        self.check = _Check()
        self.check.clicked.connect(lambda on: self.toggled.emit(self, bool(on), False))
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)

        kind = entry.get("kind")
        self.thumb = RoundedImage(_THUMB_W, _THUMB_H, radius=8.0, placeholder=_KIND_LABEL.get(kind, "FILE"))
        lay.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(5)
        col.addStretch(1)
        self.title = QLabel(entry.get("title") or "Untitled")
        self.title.setStyleSheet("font-weight: 600; font-size: 13.5px; background: transparent;")
        self.title.setMinimumWidth(80)
        col.addWidget(self.title)
        bits = [_KIND_LABEL.get(kind, "FILE").title()]
        if entry.get("size_bytes"):
            bits.append(humanize_size(entry["size_bytes"]))
        if entry.get("completed_at"):
            bits.append(time.strftime("%d %b %Y, %H:%M", time.localtime(entry["completed_at"])))
        if self.missing:
            bits.append("file not found")
        self.meta = QLabel("  ·  ".join(bits))
        self.meta.setObjectName("mono")
        if self.missing:
            self.meta.setProperty("state", "error")
        col.addWidget(self.meta)
        col.addStretch(1)
        lay.addLayout(col, 1)

        self.play_btn = Button("Play")
        self.play_btn.setObjectName("historyPlain")
        self.play_btn.setEnabled(not self.missing)
        lay.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.folder_btn = Button("Open Folder")
        self.folder_btn.setObjectName("historyGreen")
        lay.addWidget(self.folder_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.more_btn = Button("···")
        self.more_btn.setObjectName("historyPlain")
        self.more_btn.setToolTip("More")
        self.more_btn.setFixedWidth(40)
        lay.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # One line, elided, so long titles never push the actions around.
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(self.entry.get("title") or "Untitled", Qt.TextElideMode.ElideRight,
                                         max(60, self.title.width())))

    def set_selected(self, on, animate=True):
        self.selected = on
        self.check.set_state(on)
        if animate:
            self._sel.to(1 if on else 0)
        else:
            self._sel.snap(1 if on else 0)

    def enterEvent(self, event):
        self._hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.to(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.toggled.emit(self, not self.selected, shift)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -3)
        cinema.paint_contact_shadow(p, r, 14, dark)
        cinema.paint_glass(p, self, r, 14, tier=0)
        s, h = self._sel.value, self._hover.value
        if h > 0.01 and s < 0.99:
            wash = qcolor(t["hover_overlay"])
            wash.setAlphaF(wash.alphaF() * 0.7 * h * (1 - s))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(wash)
            p.drawRoundedRect(r, 14, 14)
        if s > 0.01:
            tint = QColor(t["accent"])
            tint.setAlphaF(0.10 * s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, 14, 14)
            ring = QColor(t["accent"])
            ring.setAlphaF(0.85 * s)
            p.setPen(QPen(ring, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.8, 0.8, -0.8, -0.8), 13.2, 13.2)
        p.end()


class _SelectionBar(QWidget):
    """What to do with the selection, in a row of its own under the header
    that opens while anything is selected and pushes the list down. It rose
    from the bottom of the window first, over the last rows (asked to be at
    the top); then it sat in the header's free space, where it ended up over
    the filters (reported) -- a row of its own covers nothing."""

    H = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 5, 5, 5)
        self.want_all = True
        lay.setSpacing(6)
        self.count = QLabel("")
        self.count.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")
        lay.addWidget(self.count)
        self.size = QLabel("")
        self.size.setObjectName("mono")
        lay.addWidget(self.size)
        lay.addSpacing(6)
        self.all_btn = Button("Select all")
        self.all_btn.setObjectName("quiet")
        lay.addWidget(self.all_btn)
        self.none_btn = Button("Deselect all")
        self.none_btn.setObjectName("quiet")
        lay.addWidget(self.none_btn)
        lay.addStretch(1)
        self.remove_btn = Button("Remove from History")
        lay.addWidget(self.remove_btn)
        self.delete_btn = Button("Delete files")
        self.delete_btn.setObjectName("danger")
        lay.addWidget(self.delete_btn)
        for b in (self.all_btn, self.none_btn, self.remove_btn, self.delete_btn):
            b.setFixedHeight(30)

    def natural_width(self):
        self.layout().invalidate()
        return self.layout().sizeHint().width() + 8

    def fit(self, width):
        """Short of room, the least needed go first: Select all and
        Deselect all (the header's checkbox does both), then the size; then
        "Remove from History" says just "Remove"."""
        self.remove_btn.setText("Remove from History")
        steps = ((self.all_btn, self.want_all), (self.none_btn, True), (self.size, bool(self.size.text())))
        for w, want in steps:
            w.setVisible(want)
        if self.natural_width() <= width:
            return
        for w, _want in steps:
            w.setVisible(False)
            if self.natural_width() <= width:
                return
        self.remove_btn.setText("Remove")

    def paintEvent(self, event):
        dark = cinema.is_dark(self)
        t = theme.tokens(dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = r.height() / 2
        # A soft glow of the brand round it: it's a note on the list, lit.
        glow = QColor(t["brand"])
        for i, a in enumerate((0.16, 0.08, 0.04)):
            g = QColor(glow)
            g.setAlphaF(a if dark else a * 0.8)
            p.setPen(QPen(g, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-i, -i, i, i), rad + i, rad + i)
        rim = QColor(t["brand"])
        rim.setAlphaF(0.55)
        p.setPen(QPen(rim, 1.2))
        p.setBrush(qcolor(t["card_bg_solid"]))
        p.drawRoundedRect(r, rad, rad)
        # Its dot: the selection's colour, at the start of the note.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent"]))
        p.drawEllipse(QPointF(r.left() + 9, r.center().y()), 3, 3)
        p.end()


# --------------------------------------------------------------------- tab
class HistoryTab(QWidget):
    _thumb_ready_sig = Signal(object, object)  # (row, pil_image)
    # Fired whenever download_history.add_entry() runs anywhere in the app
    # (video/audio download, image save, torrent completion, browser direct
    # download -- five separate call sites). MainWindow badges this tab's
    # label off of it, since none of those call sites can reach the nav.
    entry_added = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._thumb_ready_sig.connect(self._on_thumbnail_ready)
        self.rows = []
        self.selected = set()          # entry keys
        self._anchor = None            # last row clicked, for Shift+click ranges
        self._filter = "all"
        self._query = ""

        column = centered_column(self, MAX_CONTENT_W)
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(12, 0, 0, 0)
        header.setSpacing(10)
        self.all_check = _Check()
        self.all_check.setToolTip("Select all (Ctrl+A)")
        self.all_check.clicked.connect(self._on_all_check)
        header.addWidget(self.all_check)
        header.addSpacing(4)
        self._title = section_label("History")
        header.addWidget(self._title)
        self.count_label = QLabel("")
        self.count_label.setObjectName("mono")
        header.addWidget(self.count_label)
        header.addSpacing(10)
        self.chips = {}
        for key, label in _FILTERS:
            chip = _Chip(label)
            chip.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self.chips[key] = chip
            header.addWidget(chip)
        self.chips["all"].setChecked(True)
        header.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search history")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._on_search)
        header.addWidget(self.search)
        self.clear_btn = Button("Clear History")
        self.clear_btn.setObjectName("danger")
        self.clear_btn.clicked.connect(self._clear_all)
        header.addWidget(self.clear_btn)
        root.addLayout(header)
        self._header = header

        self.bar = _SelectionBar()
        self.bar_row = QWidget()
        bar_lay = QVBoxLayout(self.bar_row)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignTop)
        self.bar_row.setFixedHeight(0)
        self.bar_row.hide()
        root.addWidget(self.bar_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Scoped by id -- an unscoped "background: transparent" outranks the
        # app stylesheet for every descendant and strips the row buttons.
        scroll.setObjectName("historyScroll")
        scroll.setStyleSheet("#historyScroll { background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        self.body = QWidget()
        self.body.setObjectName("historyScrollBody")
        self.body.setStyleSheet("#historyScrollBody { background: transparent; }")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        scroll.setWidget(self.body)
        root.addWidget(scroll, 1)

        self.bar.all_btn.clicked.connect(lambda: self._select_all(True))
        self.bar.none_btn.clicked.connect(lambda: self._select_all(False))
        self.bar.remove_btn.clicked.connect(self._remove_selected)
        self.bar.delete_btn.clicked.connect(self._delete_selected)
        self._bar_shown = False

        for seq, fn in (("Ctrl+A", lambda: self._select_all(True)),
                        ("Escape", lambda: self._select_all(False)),
                        ("Delete", self._delete_selected)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        self.refresh()

    # ---- building ----
    def refresh(self):
        for row in self.rows:
            row.hide()
            row.deleteLater()
        self.rows = []
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        entries = download_history.load()
        keys = {_key(e) for e in entries}
        self.selected &= keys
        counts = {"all": len(entries)}
        for e in entries:
            counts[e.get("kind")] = counts.get(e.get("kind"), 0) + 1
        for key, chip in self.chips.items():
            chip.set_count(counts.get(key, 0))
            chip.setVisible(key == "all" or counts.get(key, 0) > 0)
        if self._filter != "all" and not counts.get(self._filter):
            self._set_filter("all", rebuild=False)
        n = len(entries)
        self.count_label.setText(f"{n} item{'s' if n != 1 else ''}" if n else "")
        self.clear_btn.setVisible(bool(entries))
        self.search.setVisible(bool(entries))
        self.all_check.setVisible(bool(entries))
        self._fit_header()
        if not entries:
            self.body_layout.addWidget(EmptyState(
                "history", "Nothing here yet",
                "Finished videos, audio, images and torrents are listed here, "
                "newest first, with the file one click away."), 1)
            self._sync_selection()
            return

        words = self._query.lower().split()
        for entry in entries:
            if self._filter != "all" and entry.get("kind") != self._filter:
                continue
            if words and not all(w in (entry.get("title") or "").lower() for w in words):
                continue
            row = _Row(entry)
            row.set_selected(row.key in self.selected, animate=False)
            row.toggled.connect(self._on_row_toggled)
            row.activated.connect(lambda r: self._play(r.entry))
            row.play_btn.clicked.connect(lambda _c=False, e=entry: self._play(e))
            row.folder_btn.clicked.connect(lambda _c=False, e=entry: self._open_folder(e))
            row.more_btn.clicked.connect(lambda _c=False, r=row: self._row_menu(r))
            self.body_layout.addWidget(row)
            self.rows.append(row)
            threading.Thread(target=self._load_thumbnail,
                             args=(entry.get("file_path"), entry.get("kind"), row), daemon=True).start()
        if not self.rows:
            hint = QLabel("Nothing matches.")
            hint.setObjectName("muted")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.body_layout.addWidget(hint)
        self.body_layout.addStretch(1)
        self._sync_selection()

    def _set_filter(self, key, rebuild=True):
        self._filter = key
        for k, chip in self.chips.items():
            chip.setChecked(k == key)
            chip.set_count(chip.count)
        if rebuild:
            self.refresh()

    def _on_search(self, text):
        self._query = text.strip()
        self.refresh()

    # ---- selection ----
    def _visible_keys(self):
        return [row.key for row in self.rows]

    def _on_row_toggled(self, row, checked, shift):
        if shift and self._anchor in self._visible_keys():
            keys = self._visible_keys()
            a, b = keys.index(self._anchor), keys.index(row.key)
            lo, hi = min(a, b), max(a, b)
            for k in keys[lo:hi + 1]:
                self.selected.add(k)
        elif checked:
            self.selected.add(row.key)
        else:
            self.selected.discard(row.key)
        self._anchor = row.key
        for r in self.rows:
            want = r.key in self.selected
            if r.selected != want:
                r.set_selected(want)
        self._sync_selection()

    def _on_all_check(self):
        visible = set(self._visible_keys())
        self._select_all(not (visible and visible <= self.selected))

    def _select_all(self, on):
        if on:
            self.selected |= set(self._visible_keys())
        else:
            self.selected.clear()
        for r in self.rows:
            r.set_selected(r.key in self.selected)
        self._sync_selection()

    def _selected_entries(self):
        return [e for e in download_history.load() if _key(e) in self.selected]

    def _sync_selection(self):
        visible = set(self._visible_keys())
        chosen = visible & self.selected
        self.all_check.set_state(bool(visible) and chosen == visible, partial=bool(chosen))
        n = len(self.selected)
        if n:
            entries = self._selected_entries()
            size = sum(int(e.get("size_bytes") or 0) for e in entries)
            on_disk = sum(1 for e in entries if e.get("file_path") and os.path.exists(e["file_path"]))
            self.bar.count.setText(f"{n} selected")
            self.bar.size.setText(humanize_size(size) if size else "")
            self.bar.want_all = chosen != visible
            self.bar.delete_btn.setEnabled(on_disk > 0)
            self.bar.delete_btn.setText(f"Delete {on_disk} file{'s' if on_disk != 1 else ''}"
                                        if on_disk else "Delete files")
        self._show_bar(bool(n))

    # ---- the bar: a row of its own, opening under the header ----
    def _show_bar(self, show):
        if show == self._bar_shown:
            if show:
                self.bar.fit(self.bar_row.width())
            return
        self._bar_shown = show
        start = self.bar_row.height() if self.bar_row.isVisible() else 0
        end = self.bar.H + 4 if show else 0
        if show:
            self.bar_row.setFixedHeight(start)
            self.bar_row.show()
            self.bar.fit(self.bar_row.width() or self.width())

        def step(v):
            self.bar_row.setFixedHeight(round(start + (end - start) * v))

        def done():
            if not self._bar_shown:
                self.bar_row.hide()
        if motion.reduced():
            step(1.0)
            done()
        else:
            motion.tween(self.bar_row, 0.0, 1.0, motion.MEDIUM, step, done)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_header()
        if self._bar_shown:
            QTimer.singleShot(0, lambda: self.bar.fit(self.bar_row.width()))

    def _fit_header(self):
        """The search box gives up width before anything overlaps: at 220 px
        while there's room, down to 110 beside every filter on a narrow
        window (it ran under Clear History)."""
        header = getattr(self, "_header", None)
        if header is None:
            return
        avail = (header.geometry().width() or self.width()) - 12
        others = [self.all_check, self._title, self.count_label, *self.chips.values(), self.clear_btn]
        shown = [w for w in others if w.isVisible()]
        used = sum(w.sizeHint().width() for w in shown) + header.spacing() * (len(shown) + 1) + 4 + 10
        self.search.setFixedWidth(int(max(110, min(220, avail - used - 16))))
        QTimer.singleShot(0, self._settle_header)

    def _settle_header(self):
        """After layout: if the search still comes closer than a gap to
        Clear History, it gives up the difference."""
        if not (self.search.isVisible() and self.clear_btn.isVisible()):
            return
        gap = self.clear_btn.geometry().left() - self.search.geometry().right()
        want = self._header.spacing() + 2
        if gap < want and self.search.width() > 110:
            self.search.setFixedWidth(max(110, self.search.width() - (want - gap)))

    # ---- acting on the selection ----
    def _remove_selected(self):
        keys = set(self.selected)
        entries = download_history.load()
        indices = [i for i, e in enumerate(entries) if _key(e) in keys]
        if not indices:
            return
        download_history.remove_entries(indices)
        self.selected.clear()
        self.refresh()

    def _delete_selected(self):
        entries = self._selected_entries()
        files = [e.get("file_path") for e in entries if e.get("file_path") and os.path.exists(e["file_path"])]
        if not entries:
            return
        if not files:
            self._remove_selected()
            return
        size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        names = "\n".join("  " + os.path.basename(f) for f in files[:6])
        more = f"\n  ...and {len(files) - 6} more" if len(files) > 6 else ""
        confirmed = QMessageBox.question(
            self, config.APP_NAME,
            f"Move {len(files)} file{'s' if len(files) != 1 else ''} ({humanize_size(size)}) to the Recycle Bin?\n\n"
            f"{names}{more}\n\nThey're also removed from History. You can restore them from the Recycle Bin.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return
        recycled, failed = recycle.to_recycle_bin(files)
        gone = set(recycled)
        keys = {_key(e) for e in entries
                if not e.get("file_path") or e["file_path"] in gone or not os.path.exists(e["file_path"])}
        all_entries = download_history.load()
        download_history.remove_entries([i for i, e in enumerate(all_entries) if _key(e) in keys])
        self.selected -= keys
        self.refresh()
        if failed:
            QMessageBox.warning(
                self, config.APP_NAME,
                f"{len(failed)} file{'s' if len(failed) != 1 else ''} couldn't be moved to the Recycle Bin "
                "(in use, or no permission):\n\n" + "\n".join(failed[:6]))

    def _row_menu(self, row):
        e = row.entry
        menu = QMenu(self)
        menu.addAction("Play").triggered.connect(lambda: self._play(e))
        menu.addAction("Open Folder").triggered.connect(lambda: self._open_folder(e))
        menu.addSeparator()
        menu.addAction("Select").triggered.connect(lambda: self._on_row_toggled(row, True, False))
        menu.addSeparator()
        menu.addAction("Remove from History").triggered.connect(lambda: self._remove_one(e))
        a = menu.addAction("Delete File")
        a.setEnabled(not row.missing)
        a.triggered.connect(lambda: self._delete_one(e))
        menu.exec(row.more_btn.mapToGlobal(QPoint(0, row.more_btn.height())))

    def _remove_one(self, entry):
        entries = download_history.load()
        download_history.remove_entries([i for i, e in enumerate(entries) if _key(e) == _key(entry)])
        self.selected.discard(_key(entry))
        self.refresh()

    def _delete_one(self, entry):
        path = entry.get("file_path")
        title = entry.get("title") or "this file"
        if not path or not os.path.exists(path):
            self._remove_one(entry)
            return
        confirmed = QMessageBox.question(
            self, config.APP_NAME,
            f"Move this file to the Recycle Bin?\n\n{title}\n{path}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return
        recycled, failed = recycle.to_recycle_bin([path])
        if failed:
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't delete:\n{path}")
            return
        self._remove_one(entry)

    # ---- thumbnails ----
    def _load_thumbnail(self, file_path, kind, row):
        pil_image = thumbnails.get_preview_image(file_path, kind, size=(_THUMB_W * 2, _THUMB_H * 2))
        if pil_image is not None:
            self._thumb_ready_sig.emit(row, pil_image)

    def _on_thumbnail_ready(self, row, pil_image):
        try:
            pix = _pil_to_pixmap(pil_image)
            if pix is not None:
                row.thumb.setPixmap(pix)
                row.thumb.setText("")
        except RuntimeError:
            return  # the row was rebuilt while its thumbnail was loading

    # ---- single-file actions ----
    def _open_folder(self, entry):
        folder = entry.get("folder_path")
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, config.APP_NAME, "That folder isn't there anymore.")
            return
        try:
            os.startfile(folder)
        except Exception:
            logger.exception("Failed to open folder %s", folder)
            QMessageBox.information(self, config.APP_NAME, f"Files were saved at:\n{folder}")

    def _play(self, entry):
        path = entry.get("file_path")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, config.APP_NAME, "That file isn't there anymore.")
            return
        try:
            os.startfile(path)
        except Exception:
            logger.exception("Failed to play %s", path)
            QMessageBox.critical(self, config.APP_NAME, f"Couldn't open:\n{path}")

    def _clear_all(self):
        if not download_history.load():
            return
        confirmed = QMessageBox.question(
            self, config.APP_NAME,
            "Clear all download history?\n\nThis only clears this list — none of your actual "
            "downloaded files are touched or deleted.",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes
        if confirmed:
            download_history.clear_all()
            self.selected.clear()
            self.refresh()
