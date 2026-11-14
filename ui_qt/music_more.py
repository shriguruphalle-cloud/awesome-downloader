"""What came over from the phone app that isn't about people (mixed into MusicTab): the DJ (a run of short named sets,
steered by what you ask for, how new and how lively), moods and today's charts, and an artist's every song. The
choosing is the phone's own logic (app.core.music_brain); this draws it with the tab's own parts.
"""
import threading

import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QGridLayout, QHBoxLayout, QMenu, QVBoxLayout, QWidget

from app.core import mixer, music_sources
from app.core import music_brain as brain
from app.core import social
from app.logging_setup import get_logger

from . import music_look as look
from app.utils import music_library

from . import theme
from .browser_chrome import draw_icon, style_menu
from .music_social import Field, Glass, _alive, _label
from .music_widgets import NavItem, Pill, RingButton, SectionHeader, TopResultCard, art, icon_button, thumb_url

logger = get_logger("music_more")

VIBES = (("familiar", "Familiar"), ("balanced", "Balanced"), ("adventurous", "Adventurous"))
ENERGIES = (("chill", "Chill"), ("steady", "Steady"), ("hype", "Hype"))
EXTEND_LEFT = 3         # this few songs left in the DJ's run: more are asked for


HUES = {"chill": 0.52, "focus": 0.60, "workout": 0.02, "party": 0.88, "romance": 0.96, "sad": 0.64, "lofi": 0.76,
        "indie": 0.40, "jazz": 0.10, "classical": 0.14, "hiphop": 0.08, "phonk": 0.80}


MOOD_LINES = {"chill": "Slow down, breathe out", "focus": "Head down, in the zone", "workout": "Louder, faster, one more",
              "party": "Turn it up tonight", "romance": "Songs for two", "sad": "For the heavy days",
              "lofi": "Soft beats, warm crackle", "indie": "Off the beaten track", "jazz": "Smoke, brass and late hours",
              "classical": "Strings, keys and calm", "hiphop": "Bars, beats and swagger",
              "phonk": "Cowbells and drift nights"}


SORTS = {"liked": ("music_liked_sort", "recent", (("recent", "Recently added"), ("oldest", "Oldest"), ("title", "Title"),
                                                  ("artist", "Artist"), ("longest", "Longest"))),
         "playlist_songs": ("music_playlist_song_sort", "custom", (("custom", "Playlist order"),
                                                                   ("recent", "Recently added"), ("title", "Title"),
                                                                   ("artist", "Artist"))),
         "playlists": ("music_playlist_sort", "recent", (("recent", "Recently added"), ("oldest", "Oldest"),
                                                         ("az", "A–Z"), ("songs", "Most songs")))}


def _low(t, key):
    return (t.get(key) or "").lower()


class AllSongsCard(QAbstractButton):
    """The way into everything an artist has put out, as on the phone: lit in the page's colours, the latest covers
    fanned out at the right."""
    SIDE = 104

    def __init__(self, name, covers, pal_of, parent=None):
        super().__init__(parent)
        self.name, self.pal_of = name, pal_of
        self.covers = [None] * len(covers)
        self.setFixedHeight(160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False
        for i, url in enumerate(covers):
            art().want(thumb_url(url, 240), lambda pm, i=i, me=self: me._got(i, pm) if _alive(me) else None)

    def _got(self, i, pm):
        self.covers[i] = look.rounded(pm, self.SIDE, self.SIDE, 10)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pal = self.pal_of._pal
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, look.with_alpha(pal["glow"], 0.70 if self._hover else 0.55))
        g.setColorAt(1, look.with_alpha(pal["deep"], 0.92))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(r, 22, 22)
        for i in reversed(range(len(self.covers))):     # the first on top, the rest behind it to the left
            pix = self.covers[i]
            if pix is None:
                continue
            p.save()
            p.translate(self.width() - 24 - self.SIDE / 2 - i * 44, self.height() / 2)
            p.rotate((i - 1) * -7)
            p.drawPixmap(int(-self.SIDE / 2), int(-self.SIDE / 2), pix)
            p.restore()
        x, w = 24, max(160, self.width() - 24 - 240)
        p.setPen(QColor(255, 255, 255, 190))
        p.setFont(look.label_font(10.5))
        p.drawText(QPointF(x, 34), "ALL SONGS FROM")
        px, lines = look.fit_display((self.name or "").upper(), w, max_px=30, min_px=18, lines=1)
        p.setFont(look.display_font(px))
        p.setPen(look.TEXT)
        p.drawText(QPointF(x, 68), lines[0] if lines else "")
        p.setFont(look.font(13, QFont.Weight.Medium))
        p.setPen(QColor(255, 255, 255, 205))
        p.drawText(QPointF(x, 92), "Every album & single, first to latest")
        pill = QRectF(x, 108, 96, 32)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 36))           # tonal: the page's Play is its one white action
        p.drawRoundedRect(pill, 16, 16)
        p.setFont(look.font(13, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255))
        p.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), "Explore  ›")


class LibraryCard(QAbstractButton):
    """A place in your library: a pane of lit glass in its own colour, its icon, name, what it is and how many."""

    def __init__(self, icon, title, sub, count, hue, parent=None):
        super().__init__(parent)
        self.icon_kind, self.title, self.sub, self.count_text, self.hue = icon, title, sub, count, hue
        self.setFixedHeight(92)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = QColor.fromHsvF(self.hue, 0.62, 0.72)
        c.setAlpha(84 if self.underMouse() else 58)
        p.setBrush(c)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(r, 18, 18)
        draw_icon(p, self.icon_kind, QRectF(18, 32, 28, 28), look.TEXT)
        x = 60
        w = self.width() - x - 12
        p.setFont(look.font(15, QFont.Weight.Bold))
        p.setPen(look.TEXT)
        p.drawText(QRectF(x, 16, w, 22), int(Qt.AlignmentFlag.AlignVCenter), self.title)
        p.setFont(look.font(12))
        p.setPen(look.TEXT_2)
        p.drawText(QRectF(x, 38, w, 18), int(Qt.AlignmentFlag.AlignVCenter), self.sub)
        p.setPen(look.TEXT_3)
        p.drawText(QRectF(x, 56, w, 18), int(Qt.AlignmentFlag.AlignVCenter), self.count_text)
        p.end()


class MusicMore:
    MORE_PAGES = ("dj", "discover", "catalog", "library", "recent")

    def _init_more(self):
        brain.use_taste(self.taste)
        self._dj_on = False
        self._dj_busy = False
        self._dj_theme = ""
        self._dj_request = ""
        self._dj_gen = 0
        self._dj_sets = []
        st = self.settings or {}
        self._dj_vibe = st.get("music_dj_vibe", "balanced")
        self._dj_energy = st.get("music_dj_energy", "steady")
        self._catalog = {}
        self._charts = {}
        self._charts_at = 0.0
        self._charts_busy = False
        self._chart_watchers = []

    def _brain(self, ok, fail, fn, *args):
        self.hub.run(lambda: brain.value(fn, *args), ok, fail)

    # ---- the DJ ----
    def _dj_options(self):
        cur = self.current()
        return {"vibe": self._dj_vibe, "energy": self._dj_energy, "request": self._dj_request,
                "seed": cur if self._dj_on and cur else None}

    def _dj_heard(self):
        return [t.get("id") for t in self.queue[:self.index + 1]] if self._dj_on else []

    def _dj_fetch(self, done):
        """The next sets, off the UI thread; done(sets) back on it. An answer overtaken by a newer ask is dropped."""
        self._dj_gen += 1
        gen = self._dj_gen
        self._dj_busy = True
        self._dj_show()

        def ok(sets):
            if gen != self._dj_gen:
                return
            self._dj_busy = False
            done(sets or [])
            self._dj_show()

        def fail(e):
            if gen != self._dj_gen:
                return
            self._dj_busy = False
            self.status.setText("The DJ couldn't pick songs: %s" % e)
            self._dj_show()
        self._brain(ok, fail, brain.dj_session, self._dj_heard(), self._dj_options())

    @staticmethod
    def _dj_flat(sets):
        return [dict(t, _dj=True, _dj_theme=s.get("theme") or "") for s in sets for t in s.get("tracks") or []]

    def dj_start(self, ask=""):
        self._dj_request = (ask or "").strip()

        def done(sets):
            flat = self._dj_flat(sets)
            if not flat:
                self.status.setText("The DJ found nothing for that. Try other words.")
                return
            self._dj_sets = sets
            self._dj_on = True
            self.play_from(flat[0], flat, context="dj")
        self._dj_fetch(done)

    def dj_stop(self):
        self._dj_on = False
        self._dj_gen += 1
        self._dj_busy = False
        self._dj_theme = ""
        self._dj_sets = []
        self._dj_show()

    def dj_remake(self, note=None):
        """What's still to come made again (the song playing is left alone)."""
        if not self._dj_on:
            return
        here = self.current()

        def done(sets):
            flat = self._dj_flat(sets)
            if flat and self._dj_on and self.current() is here:
                self._dj_sets = sets
                self.queue = self.queue[:self.index + 1] + flat
                self._render_queue()
                self._arm_next()
                if note:
                    self.status.setText(note)
        self._dj_fetch(done)

    def dj_ask(self, text):
        text = (text or "").strip()
        if not self._dj_on:
            self.dj_start(text)
            return
        self._dj_request = text
        self.dj_remake("Up next: %s" % text if text else None)

    def _dj_set(self, what, value):
        setattr(self, "_dj_" + what, value)
        self.set_pref("music_dj_" + what, value)
        self._dj_show()
        self.dj_remake()

    def dj_track_started(self, track):
        """A song started: the set's name when it changes; more songs when the run is nearly over."""
        if not self._dj_on:
            return
        if not track.get("_dj"):
            self.dj_stop()
            return
        theme = track.get("_dj_theme") or ""
        if theme and theme != self._dj_theme:
            self._dj_theme = theme
            self.status.setText("DJ: %s" % theme)
            self._dj_show()
        if len(self.queue) - self.index - 1 <= EXTEND_LEFT and not self._dj_busy:
            def done(sets):
                if self._dj_on:
                    self._dj_sets = sets
                    have = {t.get("id") for t in self.queue}
                    self.queue += [t for t in self._dj_flat(sets) if t.get("id") not in have]
                    self._render_queue()
                    self._arm_next()
            self._dj_fetch(done)

    def _dj_source(self, track):
        """Where a song's sound can be had right now (a file or a known stream), or None."""
        if track.get("source") == "youtube":
            return music_sources.cached(track) or music_sources.known_stream(track)
        return track.get("stream")

    def _dj_choose_next(self, key):
        """About a minute before this song ends: the next few of the DJ's own songs are listened to, and the one
        that follows on best (tempo, punch) moves up to be next. Your own picks are never moved."""
        cur = self.current()
        if not self._dj_on or cur is None or cur.get("id") != key or self._dj_chose_for == key:
            return
        ahead = []
        for t in self.queue[self.index + 1:self.index + 4]:
            if not t.get("_dj"):
                break
            src = self._dj_source(t)
            if src:
                ahead.append((t, src))
        here = self._dj_source(cur)
        if len(ahead) < 2 or not here:
            return
        self._dj_chose_for = key
        dur = int(cur.get("duration") or 0) * 1000

        def run():
            a = mixer.analyse(key, here, dur)
            heard = [(t, mixer.analyse(t["id"], src, int(t.get("duration") or 0) * 1000)) for t, src in ahead]
            heard = [(t, p) for t, p in heard if p is not None]
            if a is not None and heard:
                self._dj_pick_sig.emit(key, min(heard, key=lambda x: mixer.fit(a, x[1]))[0]["id"])
        threading.Thread(target=run, daemon=True, name="dj-listen").start()

    def _dj_picked(self, key, best):
        cur = self.current()
        if not self._dj_on or cur is None or cur.get("id") != key:
            return
        i = next((k for k in range(self.index + 1, len(self.queue)) if self.queue[k].get("id") == best), None)
        if i is None or i == self.index + 1:
            return
        self.queue.insert(self.index + 1, self.queue.pop(i))
        self._render_queue()
        self._arm_next()

    def _build_dj(self):
        page = self._pages["dj"]
        page.clear()
        page.art_url = None
        self._dj_box = QWidget()
        col = QVBoxLayout(self._dj_box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)
        ask_row = QHBoxLayout()
        ask_row.setSpacing(10)
        ask = Field("Tell the DJ: \"90s Hindi\", \"running very fast\", \"rainy night, in Punjabi\"")
        ask.setMaximumWidth(620)
        ask.edit.setText(self._dj_request)
        go_btn = Pill("Ask", style="glass")
        go_btn.clicked.connect(lambda: self.dj_ask(ask.text()))
        ask.edit.returnPressed.connect(go_btn.click)
        ask_row.addWidget(ask, 1)
        ask_row.addWidget(go_btn)
        ask_row.addStretch(0)
        col.addLayout(ask_row)
        self._dj_dials = QWidget()
        self._dj_dials_col = QVBoxLayout(self._dj_dials)
        self._dj_dials_col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._dj_dials)
        self._dj_list = QWidget()
        self._dj_list_col = QVBoxLayout(self._dj_list)
        self._dj_list_col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._dj_list)
        page.add(self._dj_box)
        self._dj_show()

    def _dj_show(self):
        """The page as things stand (only when it's open)."""
        if not self._here or self._here[0] != "dj" or not _alive(getattr(self, "_dj_box", None)):
            return
        page = self._pages["dj"]
        if self._dj_busy and not self._dj_on:
            sub = "Picking songs…"
        elif self._dj_on:
            sub = "On air: %s" % (self._dj_theme or "your mix")
            # the mix, said plainly: this one's tempo, how the next one will come in
            cur = self.current()
            heard = mixer.known(cur.get("id")) if cur else None
            if heard is not None and heard.bpm > 0:
                sub += "  ·  %d BPM" % heard.bpm
            if not self.player.smart_mix:
                sub += "  ·  Mixing off"
            elif self.player.upcoming_mix() is not None:
                sub += "  ·  next mixes in on the beat"
        else:
            sub = "Songs you love and new ones you'll love, in short named sets, one running into the next."
        page.hero.set_text("Beta", "DJ", sub)
        page.hero.clear_actions()
        if self._dj_on:
            page.hero.add_action(Pill("Switch it up", style="glass")).clicked.connect(
                lambda: self.dj_remake("New songs coming up."))
            page.hero.add_action(Pill("Stop the DJ", style="outline")).clicked.connect(self.dj_stop)
        else:
            b = page.hero.add_action(Pill("Picking…" if self._dj_busy else "Start the DJ", "play"))
            b.setEnabled(not self._dj_busy)
            b.clicked.connect(lambda: self.dj_start(self._dj_request))
        col = self._dj_dials_col
        self._clear_col(col)
        for label, what, options, now in (("How new", "vibe", VIBES, self._dj_vibe),
                                          ("Energy", "energy", ENERGIES, self._dj_energy)):
            row = QHBoxLayout()
            row.setSpacing(8)
            lab = _label(label, 12.5, color=look.TEXT_3)
            lab.setFixedWidth(70)
            row.addWidget(lab)
            for key, name in options:
                p = Pill(name, style="tab_on" if key == now else "glass")
                p.clicked.connect(lambda _c=False, w=what, k=key: self._dj_set(w, k))
                row.addWidget(p)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            col.addWidget(holder)
        col = self._dj_list_col
        self._clear_col(col)
        for s in self._dj_sets:
            tracks = s.get("tracks") or []
            if tracks:
                col.addWidget(SectionHeader(s.get("theme") or "Next"))
                lst = self._track_list(show_album=False)
                lst.set_tracks(tracks)
                col.addWidget(lst)

    @staticmethod
    def _clear_col(col):
        while col.count():
            w = col.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    # ---- moods and charts ----
    # ---- Home's parts ----
    def _home_top(self, page):
        """The date and a greeting; with nothing played yet, where to begin."""
        now = time.localtime()
        hour = now.tm_hour
        hello = ("Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 17
                 else "Good evening" if 17 <= hour < 22 else "Good night")
        me = self.hub.me if self.hub.signed_in() else None
        first = ((me or {}).get("name") or "").strip().split(" ")[0]
        played = self.current() is not None or bool(music_library.recent())
        page.hero.set_text(time.strftime("%A, %d %B", now), "%s, %s" % (hello, first) if first else hello,
                           "" if played else "Find a song, an artist or a film to begin.")
        if not played:
            page.hero.add_action(Pill("Search", "search")).clicked.connect(lambda: self._rail_go("search"))

    def _add_moods(self, page, rows):
        try:
            covers = brain.value(brain.mood_covers) or {}     # from what's here already; no network
        except brain.MusicError:
            covers = {}
        moods = [{"kind": "mood", "id": "mood:" + k, "title": m.get("title") or k.title(), "hue": HUES.get(k, 0.6),
                  "line": MOOD_LINES.get(k, ""), "artwork": covers.get(k)}
                 for k, m in brain.MOOD_MIXES.items()]
        grid = self._tile_grid("mood", rows=rows)
        grid.set_items(moods)
        page.add(grid, "Moods and genres")

    def _add_charts(self, page, here):
        """Top charts: what's kept shows at once, fresh ones replace it (asked for at most every half hour)."""
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        got = self._charts
        head = page.add(holder, "Top charts" + (" · %s" % got["place"] if got.get("place") else ""))

        def show(data):
            if not _alive(holder) or not self._here or self._here[0] != here:
                return
            self._clear_col(col)
            cards = (data or {}).get("charts") or []
            if cards:
                self._chart_grid = self._tile_grid("album", rows=1)
                self._chart_grid.set_items(cards)
                col.addWidget(self._chart_grid)
                if _alive(head) and data.get("place"):
                    head.set_title("Top charts · %s" % data["place"]) if hasattr(head, "set_title") else None
            else:
                col.addWidget(_label("Getting today's charts…", 13, color=look.TEXT_3))

        def keep(data):
            if (data or {}).get("charts"):
                self._charts = data
                self._charts_at = time.time()
                show(data)
                box = getattr(self, "_new_box", None)
                if box is not None and _alive(box):
                    self._fill_new(box)
            elif not self._charts.get("charts") and _alive(holder):
                self._clear_col(col)
                col.addWidget(_label("The charts couldn't be read just now.", 13, color=look.TEXT_3))
        show(got)
        self._chart_watchers = [keep]         # the page on screen now; an older one's holder is gone
        offline = os.environ.get("AWD_OFFLINE_LISTS") and brain.home_charts.__name__ == "home_charts"   # the test suite
        if offline or time.time() - self._charts_at < 1800 or self._charts_busy:
            return      # one fetch at a time: Home is rebuilt often, and each one takes a while
        self._charts_busy = True
        seeds = (self.taste.state.get("seeds") or {}).get("languages") or []
        region = self._region()

        def tell(data):
            for fn in list(self._chart_watchers):
                fn(data)

        def fetch():
            import threading
            if not self._charts.get("charts"):      # what was kept last time, at once
                try:
                    kept = brain.value(brain.home_charts, region, seeds, social.BASE, False)
                    if (kept or {}).get("charts"):
                        self._kept_charts.emit(kept)
                except brain.MusicError:
                    pass
            return brain.value(brain.home_charts, region, seeds, social.BASE, True), threading

        def ok(res):
            self._charts_busy = False
            tell(res[0])

        def fail(_e):
            self._charts_busy = False
            tell(None)
        if not getattr(self, "_kept_wired", False):
            self._kept_wired = True
            self._kept_charts.connect(tell)
        self.hub.run(fetch, ok, fail)

    def _add_new_releases(self, page):
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        self._new_box = box
        page.add(box, gap=0)
        self._fill_new(box)

    def _fill_new(self, box):
        lay = box.layout()
        self._clear_col(lay)
        new = self._charts.get("new") or []
        if new:
            lay.addWidget(SectionHeader("New releases"))
            grid = self._tile_grid("album", rows=1)
            grid.set_items(new)
            lay.addWidget(grid)

    def _build_discover(self):
        page = self._pages["discover"]
        page.clear()
        page.art_url = None
        page.hero.set_text("Discover", "Moods & charts", "A mix for how you feel, and what's on top today.")
        self._add_moods(page, 2)
        self._add_charts(page, "discover")

    # ---- Your music ----
    def _build_library(self):
        page = self._pages["library"]
        page.clear()
        page.art_url = None
        page.hero.set_text("Library", "Your music", "Everything you've liked, kept and made.")
        dj = Glass(20)
        row = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(_label("DJ", 19, QFont.Weight.Bold, look.TEXT))
        words.addWidget(_label("On air: %s" % (self._dj_theme or "your mix") if self._dj_on else
                               "Songs you love and new ones you'll love, mixed into each other.", 13))
        row.addLayout(words, 1)
        open_dj = Pill("Open" if self._dj_on else "Start", "play")
        open_dj.clicked.connect(lambda: self.go("dj"))
        row.addWidget(open_dj)
        dj.col.addLayout(row)
        page.add(dj)
        n = lambda v, one: "%d %s%s" % (v, one, "" if v == 1 else "s") if v else ""
        cards = (("songs", "heart", "Liked", "All your favourites", n(len(music_library.saved("songs")), "song"), 0.95),
                 ("artists", "person", "Artists", "The voices you follow", n(len(music_library.saved("artists")), "artist"), 0.75),
                 ("albums", "disc", "Albums", "Front to back", n(len(music_library.saved("albums")), "album"), 0.08),
                 ("local", "folder", "On this PC", "Your downloads", "", 0.40),
                 ("recent", "note", "Recent", "Pick up again", "", 0.60),
                 ("queue", "queue", "Queue", "What plays next", n(len(self.queue), "song"), 0.52))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for i, (key, icon, title, sub, count, hue) in enumerate(cards):
            c = LibraryCard(icon, title, sub, count, hue)
            c.clicked.connect(lambda _c=False, k=key: self.go(k))
            grid.addWidget(c, i // 3, i % 3)
        page.add(holder, gap=10)
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        rows = [("New playlist", "", "plus", lambda: self._rail_go("newplaylist")),
                ("Import a playlist", "Spotify, Apple Music, YouTube Music, YouTube — or a list", "playlist_import",
                 self.importer.open),
                ("Import liked songs", "From Spotify or YouTube Music", "heart", self.liked_import.open)]
        for title, sub, icon, fn in rows:
            it = NavItem("x", title, sub=sub, icon=icon)
            it.setCheckable(False)
            it.clicked.connect(lambda _c=False, f=fn: f())
            col.addWidget(it)
        shown = self._playlists_in_order()
        pins = self._pins()
        for p in shown:
            it = NavItem(p["id"], p["title"], sub=n(len(p.get("tracks") or []), "song") or "Empty",
                         art_url=p.get("artwork") or next((t.get("artwork") for t in p.get("tracks") or []
                                                           if t.get("artwork")), None))
            it.setCheckable(False)
            it.clicked.connect(lambda _c=False, k=p["id"]: self.go("playlist", k))
            line = QWidget()
            lr = QHBoxLayout(line)
            lr.setContentsMargins(0, 0, 0, 0)
            lr.setSpacing(2)
            lr.addWidget(it, 1)
            pin = icon_button("pin", "Unpin" if p["id"] in pins else "Pin to the top", 34, 16)
            pin.tint = None if p["id"] in pins else "#55ffffff"
            pin.clicked.connect(lambda _c=False, x=p: self._toggle_pin(x))
            lr.addWidget(pin)
            more = icon_button("dots", "More", 34, 17)
            more.clicked.connect(lambda _c=False, x=p: self._playlist_more(x))
            lr.addWidget(more)
            col.addWidget(line)
        if len(shown) > 1:
            page.add(self._sort_bar("playlists", self._build_library), "Playlists")
            page.add(box, gap=6)
        else:
            page.add(box, "Playlists")
        st = NavItem("x", "Settings", icon="sliders")
        st.setCheckable(False)
        st.clicked.connect(lambda _c=False: self.open_settings())
        page.add(st, gap=12)

    def _build_recent(self):
        page = self._pages["recent"]
        page.clear()
        page.art_url = None
        songs = [t for t in music_library.recent() if t.get("kind", "track") == "track"]
        page.hero.set_text("Your music", "Recently played", "The last %d songs" % len(songs) if songs else
                           "Songs you play show up here.")
        if songs:
            page.hero.add_action(Pill("Play", "play")).clicked.connect(lambda: self.play_from(songs[0], songs))
            lst = self._track_list()
            lst.set_tracks(songs)
            page.add(lst)
            self._set_current_marks()

    def _open_mood(self, key):
        self.status.setText("Making your %s mix…" % brain.MOOD_MIXES[key].get("title", key))

        def ok(item):
            tracks = (item or {}).get("tracks") or []
            if not tracks:
                self.status.setText("Nothing found for that just now.")
                return
            self.status.setText("")
            line = MOOD_LINES.get(key, "")
            self.go("mix", dict(item, over=item.get("over") or "Mood mix · made for you",
                                subtitle=item.get("subtitle") or line))
        self._brain(ok, lambda e: self.status.setText(str(e)), brain.mood_mix, key)

    # ---- the phone's orders ----
    def _sort_now(self, name):
        pref, first, _opts = SORTS[name]
        return (self.settings or {}).get(pref) or first

    def _sort_bar(self, name, rebuild):
        """An order to choose: one small button with the choice on it, opening the choices under it."""
        pref, _first, opts = SORTS[name]
        now = self._sort_now(name)
        b = Pill("%s  ▾" % dict(opts).get(now, opts[0][1]), "sort", style="glass")

        def choose(key):
            self.set_pref(pref, key)
            QTimer.singleShot(0, rebuild)

        def show():
            menu = style_menu(QMenu(self), theme.tokens(True))
            for key, words in opts:
                act = menu.addAction(words, lambda k=key: choose(k))
                act.setCheckable(True)
                act.setChecked(key == now)
            menu.exec(b.mapToGlobal(b.rect().bottomLeft()))
        b.clicked.connect(show)
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(b)
        row.addStretch(1)
        return bar

    def _liked_in_order(self, songs):
        by = self._sort_now("liked")
        if by == "oldest":
            return list(reversed(songs))
        if by in ("title", "artist"):
            return sorted(songs, key=lambda t: _low(t, by))
        if by == "longest":
            return sorted(songs, key=lambda t: -(t.get("duration") or 0))
        return list(songs)

    def _playlist_in_order(self, songs):
        by = self._sort_now("playlist_songs")
        if by == "recent":
            return list(reversed(songs))
        if by in ("title", "artist"):
            return sorted(songs, key=lambda t: _low(t, by))
        return list(songs)

    def _playlists_in_order(self):
        by = self._sort_now("playlists")
        pls = music_library.playlists()
        if by == "oldest":
            pls = list(reversed(pls))
        elif by == "az":
            pls = sorted(pls, key=lambda p: _low(p, "title"))
        elif by == "songs":
            pls = sorted(pls, key=lambda p: -len(p.get("tracks") or []))
        pins = self._pins()
        return sorted(pls, key=lambda p: pins.index(p["id"]) if p["id"] in pins else len(pins))

    def _pins(self):
        return list((self.settings or {}).get("music_pinned_playlists") or [])

    def _toggle_pin(self, pl):
        pins = self._pins()
        pins.remove(pl["id"]) if pl["id"] in pins else pins.insert(0, pl["id"])
        self.set_pref("music_pinned_playlists", pins)
        self.status.setText(("Pinned %s" if pl["id"] in pins else "Unpinned %s") % pl["title"])
        QTimer.singleShot(0, self._build_library)

    def _profile_ring(self, pl):
        """A playlist on your profile, or only yours."""
        on = pl.get("public", True) is not False
        b = RingButton("people" if on else "lock", "On your profile — click to keep it private" if on
                       else "Private — click to show it on your profile")

        def flip():
            now = not (pl.get("public", True) is not False)
            brain.value(brain.set_playlist_public, pl["id"], now)
            pl["public"] = now
            b.set_kind("people" if now else "lock")
            b.set_tip("On your profile — click to keep it private" if now
                      else "Private — click to show it on your profile")
            self.status.setText("%s shows on your profile" % pl["title"] if now
                                else "%s is private now — only you see it" % pl["title"])
        b.clicked.connect(flip)
        return b

    def _playlist_more(self, pl):
        menu = style_menu(QMenu(self), theme.tokens(True))
        songs = pl.get("tracks") or []
        if songs:
            menu.addAction("Play", lambda: self._play_list(songs))
            menu.addAction("Shuffle", lambda: self._play_list(songs, True))
            menu.addAction("Add to the queue", lambda: [self.add_to_queue(t, quiet=True) for t in songs])
            menu.addAction("Download all", lambda: [self.keep(t) for t in songs])
            menu.addSeparator()
        menu.addAction("Unpin from the top" if pl["id"] in self._pins() else "Pin to the top",
                       lambda: self._toggle_pin(pl))
        public = pl.get("public", True) is not False
        menu.addAction("Make private" if public else "Show on your profile", lambda: self._set_on_profile(pl, not public))
        menu.addAction("Rename", lambda: self._rename_playlist(pl))
        if songs:
            menu.addAction("Copy the song list", lambda: QApplication.clipboard().setText(
                "\n".join("%s - %s" % (t.get("artist", ""), t.get("title", "")) for t in songs)))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self._confirm_delete(pl))
        menu.exec(QCursor.pos())

    def _set_on_profile(self, pl, on):
        brain.value(brain.set_playlist_public, pl["id"], on)
        pl["public"] = on
        self.status.setText("%s shows on your profile" % pl["title"] if on
                            else "%s is private now — only you see it" % pl["title"])
        if (self._here or (None,))[0] == "library":
            QTimer.singleShot(0, self._build_library)

    def _confirm_delete(self, pl):
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Delete %s?" % pl["title"]).setEnabled(False)
        menu.addAction("Delete it", lambda: self._delete_playlist(pl))
        menu.addAction("Keep it")
        menu.exec(QCursor.pos())

    # ---- the full-screen player's top bar ----
    def _player_share(self):
        track = self.current()
        if not track:
            return
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Share as a video…", lambda: self.sharer.open_for(track))
        self.social_song_actions(menu, track)
        if track.get("page_url") and track.get("source") != "local":
            menu.addAction("Copy link", lambda: QApplication.clipboard().setText(track["page_url"]))
        menu.exec(QCursor.pos())

    def _player_more(self):
        """The dots: what the card has under its controls, as a list, and the song's own menu."""
        track = self.current()
        if not track:
            return
        f = self.full
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Liked" if music_library.is_saved(track) else "Like", lambda: self.toggle_saved(track))
        menu.addAction("Back to the player" if f.view == "lyrics" else "Lyrics",
                       lambda: f.set_view("card" if f.view == "lyrics" else "lyrics"))
        menu.addAction("Lyrics language", lambda: self._lyrics_menu(QCursor.pos()))

        def panel(card):
            f.show_card(card, True)
            self._refresh_panels()
        menu.addAction("Queue", lambda: panel(f.queue_card))
        menu.addAction({"one": "Repeat this song", "all": "Repeat all"}.get(self.repeat, "Repeat"), self._cycle_repeat)
        menu.addAction("Shuffle: on" if self.shuffle else "Shuffle", self._toggle_shuffle)
        menu.addAction("Equalizer & effects", lambda: panel(f.fx_card))
        menu.addAction("Share", self._player_share)
        menu.addSeparator()
        menu.addAction("More for this song", lambda: self._song_menu(QCursor.pos()))
        menu.exec(QCursor.pos())

    # ---- the sleep timer: the music fades out and stops when the time is up ----
    def _sleep_lit(self):
        on = getattr(self, "_sleep_end", False) or getattr(self, "_sleep_due", None)
        self.full.moon_btn.tint = self._np_pal["accent"].name() if on else None
        self.full.moon_btn.set_tip(self._sleep_words().rstrip("…"))
        self.full.moon_btn.update()

    def _sleep_words(self):
        if getattr(self, "_sleep_end", False):
            return "Sleep timer: end of this song"
        at = getattr(self, "_sleep_due", None)
        if at:
            return "Sleep timer: %d min left" % max(1, round((at - time.monotonic()) / 60))
        return "Sleep timer…"

    def _sleep_menu(self):
        menu = style_menu(QMenu(self), theme.tokens(True))
        for words, minutes in (("15 minutes", 15), ("30 minutes", 30), ("45 minutes", 45), ("1 hour", 60),
                               ("End of this song", -1)):
            menu.addAction(words, lambda m=minutes: self.set_sleep(m))
        if getattr(self, "_sleep_end", False) or getattr(self, "_sleep_due", None):
            menu.addSeparator()
            menu.addAction("Turn off", lambda: self.set_sleep(None))
        menu.exec(QCursor.pos())

    def set_sleep(self, minutes):
        if not hasattr(self, "_sleep_timer"):
            self._sleep_timer = QTimer(self)
            self._sleep_timer.setSingleShot(True)
            self._sleep_timer.timeout.connect(self._sleep_fade)
        self._sleep_timer.stop()
        self._sleep_end, self._sleep_due = minutes == -1, None
        if minutes and minutes > 0:
            self._sleep_due = time.monotonic() + minutes * 60
            self._sleep_timer.start(minutes * 60000)
        self.status.setText("Sleep timer off" if minutes is None else "Music stops at the end of this song"
                            if minutes == -1 else "Music stops in %d minutes" % minutes)
        self._sleep_lit()

    def _sleep_at_song_end(self):
        if not getattr(self, "_sleep_end", False):
            return False
        self._sleep_end = False
        self.status.setText("Stopped by the sleep timer")
        self._sleep_lit()
        return True

    def _sleep_fade(self):
        self._sleep_due = None
        self._sleep_lit()
        full, steps = self.audio.volume(), [20]
        fade = QTimer(self)
        fade.setInterval(500)

        def step():
            steps[0] -= 1
            self.audio.setVolume(full * steps[0] / 20)
            if steps[0] <= 0:
                fade.stop()
                fade.deleteLater()
                self.player.pause()
                self.audio.setVolume(full)
                self.status.setText("Stopped by the sleep timer")
        fade.timeout.connect(step)
        fade.start()

    # ---- what you looked for ----
    def _clear_search(self):
        """Back to the Search page's moods: the search box emptied, the Search tab pressed on the results."""
        self._search = {}
        self._gen += 1
        self.rail.search.clear()
        page = self._pages["search"]
        page.art_url = None
        self._build_search_empty()
        self._paint_page(page)

    def _search_box_changed(self, text):
        if not text.strip() and self._search and (self._here or (None,))[0] == "search":
            self._clear_search()

    def _remember_search(self, q):
        had = [x for x in (self.settings or {}).get("music_recent_searches") or [] if x.lower() != q.lower()]
        self.set_pref("music_recent_searches", ([q] + had)[:12])

    def _forget_search(self, q=None):
        had = (self.settings or {}).get("music_recent_searches") or []
        self.set_pref("music_recent_searches", [x for x in had if q is not None and x != q])
        QTimer.singleShot(0, self._build_search_empty)

    def _add_recent_searches(self, page):
        had = (self.settings or {}).get("music_recent_searches") or []
        if not had:
            return
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for i, q in enumerate(had[:6]):
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            it = NavItem("x", q, icon="search")
            it.setCheckable(False)
            it.clicked.connect(lambda _c=False, t=q: self.run_search(t))
            row.addWidget(it, 1)
            off = icon_button("close", "Forget this search", 32, 13)
            off.clicked.connect(lambda _c=False, t=q: self._forget_search(t))
            row.addWidget(off)
            grid.addWidget(line, i // 2, i % 2)
        page.add(holder, "Recently searched")
        clear = Pill("Clear", style="glass")
        clear.clicked.connect(lambda: self._forget_search())
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(clear)
        row.addStretch(1)
        page.add(bar, gap=6)

    # ---- Explore: the newest charts come on their own, so the page doesn't wait for them ----
    def _add_fresh_charts(self, page, region):
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        wait = _label("Getting today's Apple Music and Spotify charts…", 13, color=look.TEXT_3)
        col.addWidget(wait)
        page.add(holder)

        def ok(got):
            if not _alive(holder):
                return
            wait.hide()
            for key, fallback in (("apple", "Top songs today"), ("spotify", "Spotify Top 30")):
                songs = (got or {}).get(key) or []
                if songs:
                    lst = self._track_list(limit=15)
                    lst.set_tracks(songs)
                    col.addWidget(SectionHeader((got or {}).get(key + "_title") or fallback))
                    col.addWidget(lst)
                    self._want_cover(songs[:30])
            self._set_current_marks()

        def fail(_e):
            if _alive(holder):
                holder.hide()
        self._brain(ok, fail, brain.fresh_charts, region)

    # ---- an artist's every song ----
    def _all_songs_card(self, artist):
        covers = []
        for r in (artist.get("albums") or []) + (artist.get("singles") or []):
            if r.get("artwork") and r["artwork"] not in covers:
                covers.append(r["artwork"])
        c = AllSongsCard(artist.get("title", ""), covers[:3], self._pages["artist"].hero)
        c.clicked.connect(lambda: self.open_catalog(artist))
        return c

    # ---- what a search found first ----
    def _top_result(self, page, title, over, circle, on_open, picture=None):
        """The card, at the head of the results; the short header steps aside for it."""
        old = getattr(self, "_top_holder", None)
        if _alive(old):
            old.setParent(None)
            old.deleteLater()
        card = TopResultCard(title, over, circle, page.hero)
        card.opened.connect(on_open)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 52, 0, 0)     # clear of back and reload
        col.addWidget(card)
        page.sec.insertWidget(0, holder)
        self._top_holder, self._top_card = holder, card
        page.hero.hide()
        if picture:
            art().want(thumb_url(picture, 480), lambda pm, c=card: c.set_cover(pm) if _alive(c) else None, first=True)
        return card

    def open_catalog(self, artist):
        self.go("catalog", {"browse_id": artist.get("browse_id"), "title": artist.get("title", "")})

    def _build_catalog(self, artist):
        """Everything an artist has put out, shown as it loads: narrowed by language, kind and year, searched
        within, newest or oldest first. What's shown plays as one list."""
        page = self._pages["catalog"]
        page.clear()
        page.art_url = None
        bid = artist.get("browse_id")
        state = self._catalog = {"id": bid, "tracks": [], "next": 0, "lang": "", "kind": "", "year": "",
                                 "newest": True, "shown": []}
        name = artist.get("title") or "Artist"
        h = page.hero
        h.set_text("Every song", name, "Reading the catalogue…")
        h.add_action(Pill("Play", "play")).clicked.connect(
            lambda: state["shown"] and self.play_from(state["shown"][0], state["shown"]))
        h.add_action(self._ring("shuffle", "Shuffle",
                                lambda: state["shown"] and self._play_list(state["shown"], shuffle=True)))
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        find = Field("Search these songs")
        row.addWidget(find, 1)
        btns = {}
        for key, label in (("lang", "Language"), ("kind", "Kind"), ("year", "Year")):
            b = Pill(label, style="glass")
            b.clicked.connect(lambda _c=False, k=key, l=label: pick(k, l))
            btns[key] = b
            row.addWidget(b)
        order = Pill("Newest first", style="glass")
        row.addWidget(order)
        page.add(bar)
        lst = self._track_list()
        page.add(lst, gap=8)
        note = _label("", 13, color=look.TEXT_3)
        page.add(note, gap=4)

        def value_of(t, key):
            return {"lang": t.get("lang") or "Other", "kind": t.get("kind_of") or "Albums",
                    "year": str(t.get("year") or "")}[key]

        def show():
            q = find.edit.text().strip().lower()
            out = [t for t in state["tracks"]
                   if all(not state[k] or value_of(t, k) == state[k] for k in ("lang", "kind", "year"))
                   and (not q or q in (t.get("title") or "").lower() or q in (t.get("album") or "").lower())]
            out.sort(key=lambda t: str(t.get("year") or "0"), reverse=state["newest"])
            state["shown"] = out
            lst.set_tracks(out[:400])
            self._set_current_marks()
            h.set_text("Every song", name, "%d songs%s" % (len(state["tracks"]),
                                                           " so far…" if state["next"] is not None else ""))
            note.setText("No songs match — try another filter." if state["tracks"] and not out else
                         ("Showing the first 400 of %d — narrow it down." % len(out) if len(out) > 400 else ""))

        def pick(key, label):
            counts = {}
            for t in state["tracks"]:
                v = value_of(t, key)
                if v:
                    counts[v] = counts.get(v, 0) + 1
            vals = sorted(counts, reverse=True) if key == "year" else sorted(counts, key=lambda v: -counts[v])
            menu = style_menu(QMenu(self), theme.tokens(True))
            menu.addAction("All", lambda: choose(key, label, ""))
            for v in vals:
                menu.addAction("%s (%d)" % (v, counts[v]), lambda v=v: choose(key, label, v))
            menu.exec(QCursor.pos())

        def choose(key, label, v):
            state[key] = v
            btns[key].setText(v or label)
            btns[key].style = "tab_on" if v else "glass"
            btns[key].updateGeometry()
            btns[key].update()
            show()

        def flip():
            state["newest"] = not state["newest"]
            order.setText("Newest first" if state["newest"] else "Oldest first")
            show()
        order.clicked.connect(flip)
        find.edit.textChanged.connect(lambda _t: show())

        def load():
            if state["next"] is None or self._catalog is not state:
                return

            def ok(got):
                if self._catalog is not state or not _alive(lst):
                    return
                have = {t.get("id") for t in state["tracks"]}
                state["tracks"] += [t for t in (got or {}).get("tracks") or [] if t.get("id") not in have]
                state["next"] = (got or {}).get("next")
                show()
                load()

            def fail(e):
                if self._catalog is state and _alive(lst):
                    state["next"] = None
                    h.set_text("Every song", name, str(e))
            self._brain(ok, fail, brain.artist_catalog, bid, state["next"], 8)
        load()
