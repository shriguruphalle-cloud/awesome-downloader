"""The Music tab's first-run taste setup -- under a minute, and skippable:
the languages you listen in (ranked by the order you tap them), three or
more genres or moods, then five or more artists, offered from those choices
(with search, and more on request). Opened again from Your music > Your
taste to change it, or to start over."""
import threading

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from app.core import taste as taste_core
from app.logging_setup import get_logger

from . import music_look as look
from .music_panels import GlassSheet
from .music_widgets import LinkLabel, Pill, TileGrid

logger = get_logger("music_onboarding")


class Chip(QAbstractButton):
    """A pick: outlined until chosen, then white -- with its place in the
    order for ranked picks (languages)."""

    def __init__(self, key, text, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.rank = 0
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        f = look.font(14, QFont.Weight.DemiBold)
        self.setFixedSize(QSize(int(QFontMetricsF(f).horizontalAdvance(text)) + 46, 42))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(QPen(QColor(255, 255, 255, 230 if on else (120 if self._hover else 70)), 1.2))
        p.setBrush(QColor(255, 255, 255) if on else QColor(255, 255, 255, 18 if self._hover else 0))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(look.font(14, QFont.Weight.DemiBold))
        p.setPen(QColor(10, 11, 16) if on else look.TEXT)
        text_r = r.adjusted(0, 0, -14 if (on and self.rank) else 0, 0)
        p.drawText(text_r, int(Qt.AlignmentFlag.AlignCenter), self.text())
        if on and self.rank:
            p.setFont(look.font(10.5, QFont.Weight.Bold))
            p.drawText(QRectF(r.right() - 26, r.top(), 20, r.height()), int(Qt.AlignmentFlag.AlignCenter),
                       str(self.rank))
        p.end()


class _Flow(QWidget):
    """Chips in rows, wrapping to the width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chips = []

    def add(self, chip):
        chip.setParent(self)
        chip.show()
        self.chips.append(chip)
        self._relayout()

    def _relayout(self):
        x = y = 0
        w = max(200, self.width())
        for c in self.chips:
            if x and x + c.width() > w:
                x, y = 0, y + c.height() + 10
            c.move(x, y)
            x += c.width() + 10
        self.setFixedHeight(y + (self.chips[-1].height() if self.chips else 0) + 4)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()


class Onboarding(GlassSheet):
    done = Signal(list, list, list)     # language codes (ranked), genres, artists
    skipped = Signal()
    reset_requested = Signal()
    _artists_sig = Signal(int, object)

    STEPS = (("Step 1 of 3", "What do you listen in?", "Tap languages in the order you like them most."),
             ("Step 2 of 3", "What moves you?", "Pick three or more genres or moods."),
             ("Step 3 of 3", "Who do you love?", "Pick five or more artists — search for anyone missing."))

    def __init__(self, parent=None):
        super().__init__(parent, max_w=1040, max_h=760)
        self.step = 0
        self.lang_order = []
        self.picked = {}            # artist id -> {"id","name","artwork"}
        self._queries_done = 0
        self._gen = 0
        self._artists_sig.connect(self._on_artists)

        outer = self.card_lay
        top = QHBoxLayout()
        self.overline = QLabel()
        self.overline.setFont(look.label_font(11))
        self.overline.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        top.addWidget(self.overline)
        top.addStretch(1)
        self.skip_btn = LinkLabel("Skip for now")
        self.skip_btn.clicked.connect(self.skipped)
        top.addWidget(self.skip_btn)
        outer.addLayout(top)
        self.title = QLabel()
        self.title.setFont(look.display_font(48))
        self.title.setStyleSheet("color: #ffffff; background: transparent;")
        outer.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setFont(look.font(15, QFont.Weight.Medium))
        self.sub.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        outer.addWidget(self.sub)
        outer.addSpacing(14)

        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background: transparent;")
        self.lang_flow = _Flow()
        for code, name in taste_core.LANGUAGES:
            c = Chip(code, name)
            c.clicked.connect(lambda _c=False, ch=c: self._lang_clicked(ch))
            self.lang_flow.add(c)
        self.genre_flow = _Flow()
        for g in taste_core.GENRES:
            c = Chip(g, g)
            c.clicked.connect(self._sync)
            self.genre_flow.add(c)
        art_page = QWidget()
        art_col = QVBoxLayout(art_page)
        art_col.setContentsMargins(0, 0, 0, 0)
        art_col.setSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search for an artist")
        self.search.setFixedHeight(40)
        self.search.setFixedWidth(360)
        self.search.setStyleSheet(
            "QLineEdit { background: rgba(0,0,0,70); border: 1px solid rgba(255,255,255,40); border-radius: 10px;"
            " color: #ffffff; padding: 0 12px; font-size: 14px; }")
        self.search.returnPressed.connect(self._search_artist)
        art_col.addWidget(self.search)
        self.grid = TileGrid(self._wire_tile, mode="artist", rows=999, min_w=112, max_w=132, gap=18)
        art_col.addWidget(self.grid)
        self.more_btn = LinkLabel("Show more")
        self.more_btn.clicked.connect(self._load_more)
        art_col.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.art_note = QLabel("")
        self.art_note.setFont(look.font(13))
        self.art_note.setStyleSheet("color: rgba(255,255,255,165); background: transparent;")
        art_col.addWidget(self.art_note)
        art_col.addStretch(1)
        for page in (self.lang_flow, self.genre_flow, art_page):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 0, 8, 0)
            hl.addWidget(page)
            hl.addStretch(1)
            scroll.setWidget(holder)
            self.pages.addWidget(scroll)
        outer.addWidget(self.pages, 1)

        foot = QHBoxLayout()
        self.reset_btn = LinkLabel("Start over")
        self.reset_btn.setToolTip("Forget your picks and what was learned from your listening")
        self.reset_btn.clicked.connect(self.reset_requested)
        foot.addWidget(self.reset_btn)
        foot.addStretch(1)
        self.count = QLabel("")
        self.count.setFont(look.font(13, QFont.Weight.Medium))
        self.count.setStyleSheet("color: rgba(255,255,255,170); background: transparent;")
        foot.addWidget(self.count)
        foot.addSpacing(14)
        self.back_btn = Pill("Back", style="outline")
        self.back_btn.clicked.connect(lambda: self.show_step(self.step - 1))
        self.next_btn = Pill("Next")
        self.next_btn.clicked.connect(self._next)
        foot.addWidget(self.back_btn)
        foot.addWidget(self.next_btn)
        outer.addLayout(foot)

    # ---- opening ----
    def open(self, seeds=None, editing=False):
        seeds = seeds or {}
        self.lang_order = list(seeds.get("languages") or [])
        for c in self.lang_flow.chips:
            c.setChecked(c.key in self.lang_order)
        for c in self.genre_flow.chips:
            c.setChecked(c.key in (seeds.get("genres") or []))
        self.picked = {a["id"]: a for a in seeds.get("artists") or []}
        self.grid.set_items(list(self.picked.values()))
        self._queries_done = 0
        self.reset_btn.setVisible(editing)
        self.skip_btn.setText("Close" if editing else "Skip for now")
        self.open_sheet()
        self.show_step(0)

    def show_step(self, step):
        self.step = max(0, min(2, step))
        over, title, sub = self.STEPS[self.step]
        self.overline.setText(over.upper() + "  ·  YOUR TASTE")
        self.title.setText(title.upper())
        self.sub.setText(sub)
        self.pages.setCurrentIndex(self.step)
        self.back_btn.setVisible(self.step > 0)
        self.next_btn.setText("Done" if self.step == 2 else "Next")
        if self.step == 2 and not self._queries_done:
            self._load_more()
        self._sync()

    def _lang_clicked(self, chip):
        if chip.isChecked():
            self.lang_order.append(chip.key)
        elif chip.key in self.lang_order:
            self.lang_order.remove(chip.key)
        self._sync()

    def genres(self):
        return [c.key for c in self.genre_flow.chips if c.isChecked()]

    def _sync(self):
        for c in self.lang_flow.chips:
            c.rank = self.lang_order.index(c.key) + 1 if c.key in self.lang_order else 0
            c.update()
        need = {0: (len(self.lang_order), 1, "language"), 1: (len(self.genres()), 3, "genre"),
                2: (len(self.picked), 5, "artist")}[self.step]
        have, least, noun = need
        self.count.setText("%d picked%s" % (have, "" if have >= least else " — %d more to go" % (least - have)))
        self.next_btn.setEnabled(have >= least)
        self.next_btn.update()

    def _next(self):
        if self.step < 2:
            self.show_step(self.step + 1)
            return
        self.hide()
        self.done.emit(list(self.lang_order), self.genres(), list(self.picked.values()))

    # ---- artists ----
    def _queries(self):
        langs = [taste_core.LANGUAGE_NAMES.get(c, c) for c in self.lang_order[:3]] or ["Top"]
        genres = self.genres()[:4] or ["popular"]
        qs = []
        for lang in langs:
            qs.append("%s top singers" % lang)
            for g in genres:
                qs.append("%s %s artists" % (lang, g))
        return qs

    def _load_more(self):
        qs = self._queries()[self._queries_done:self._queries_done + 4]
        self._queries_done += len(qs)
        self.more_btn.setVisible(self._queries_done < len(self._queries()))
        if qs:
            self._fetch(qs)

    def _search_artist(self):
        text = self.search.text().strip()
        if text:
            self._fetch([text], first=True)

    def _fetch(self, queries, first=False):
        self._gen += 1
        gen = self._gen
        self.art_note.setText("Finding artists...")
        lang = self.lang_order[0] if self.lang_order else None

        def work():
            from concurrent.futures import ThreadPoolExecutor
            from app.core import ytmusic
            out = []

            def one(q):
                try:
                    return ytmusic.search(q).get("artists") or []
                except Exception:   # noqa: BLE001 -- the rest still come
                    logger.info("Artist search failed for %r", q, exc_info=True)
                    return []
            with ThreadPoolExecutor(max_workers=4) as pool:
                for found in pool.map(one, queries):
                    out += found
            self._artists_sig.emit(gen, {"first": first, "artists": out, "language": lang})
        threading.Thread(target=work, daemon=True).start()

    def _on_artists(self, gen, found):
        have = {self._aid(t.item) for t in self.grid.tiles}
        new, seen = [], set(have)
        for a in found["artists"]:
            if self._aid(a) not in seen:
                seen.add(self._aid(a))
                new.append(dict(a, language=found["language"]))
        items = [t.item for t in self.grid.tiles]
        items = (new + items) if found["first"] else (items + new)
        self.grid.set_items(items)
        self.art_note.setText("" if items else "No artists found -- try a search.")

    @staticmethod
    def _aid(a):
        return a.get("browse_id") or a["id"]

    def _wire_tile(self, tile):
        tile.picked = self._aid(tile.item) in self.picked
        tile.clicked.connect(lambda item, t=tile: self._toggle(t))
        tile.play.connect(lambda item, t=tile: self._toggle(t))

    def _toggle(self, tile):
        a = tile.item
        aid = self._aid(a)
        if aid in self.picked:
            del self.picked[aid]
            tile.picked = False
        else:
            self.picked[aid] = {"id": aid, "name": a.get("title") or a.get("name", ""),
                                "title": a.get("title") or a.get("name", ""), "artwork": a.get("artwork"),
                                "language": a.get("language"), "kind": "artist", "browse_id": aid}
            tile.picked = True
        tile.update()
        self._sync()
