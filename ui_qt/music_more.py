"""What came over from the phone app that isn't about people (mixed into MusicTab): the DJ (a run of short named sets,
steered by what you ask for, how new and how lively), moods and today's charts, and an artist's every song. The
choosing is the phone's own logic (app.core.music_brain); this draws it with the tab's own parts.
"""
import threading

import os
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from app.core import mixer, music_sources
from app.core import music_brain as brain
from app.core import social
from app.logging_setup import get_logger

from . import music_look as look
from app.utils import music_library

from .browser_chrome import draw_icon
from .music_social import Field, Glass, _alive, _label
from .music_widgets import NavItem, Pill, SectionHeader

logger = get_logger("music_more")

VIBES = (("familiar", "Familiar"), ("balanced", "Balanced"), ("adventurous", "Adventurous"))
ENERGIES = (("chill", "Chill"), ("steady", "Steady"), ("hype", "Hype"))
EXTEND_LEFT = 3         # this few songs left in the DJ's run: more are asked for


HUES = {"chill": 0.52, "focus": 0.60, "workout": 0.02, "party": 0.88, "romance": 0.96, "sad": 0.64, "lofi": 0.76,
        "indie": 0.40, "jazz": 0.10, "classical": 0.14, "hiphop": 0.08, "phonk": 0.80}


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
        moods = [{"kind": "mood", "id": "mood:" + k, "title": m.get("title") or k.title(), "hue": HUES.get(k, 0.6)}
                 for k, m in brain.MOOD_MIXES.items()]
        grid = self._tile_grid("mood", rows=rows)
        grid.set_items(moods)
        page.add(grid, "Moods and genres", "See all")

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
        for p in music_library.playlists():
            it = NavItem(p["id"], p["title"], sub=n(len(p.get("tracks") or []), "song") or "Empty",
                         art_url=p.get("artwork") or next((t.get("artwork") for t in p.get("tracks") or []
                                                           if t.get("artwork")), None))
            it.setCheckable(False)
            it.clicked.connect(lambda _c=False, k=p["id"]: self.go("playlist", k))
            col.addWidget(it)
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
        page.hero.set_text("Your music", "Recent", "The last %d songs you played." % len(songs) if songs else
                           "What you play shows here.")
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
            self.go("mix", dict(item, over=item.get("over") or "Mood"))
        self._brain(ok, lambda e: self.status.setText(str(e)), brain.mood_mix, key)

    # ---- an artist's every song ----
    def open_catalog(self, artist):
        self.go("catalog", {"browse_id": artist.get("browse_id"), "title": artist.get("title", "")})

    def _build_catalog(self, artist):
        page = self._pages["catalog"]
        page.clear()
        page.art_url = None
        bid = artist.get("browse_id")
        state = self._catalog = {"id": bid, "tracks": [], "next": 0, "busy": False}
        page.hero.set_text("Every song", artist.get("title") or "Artist", "Reading the catalogue…")
        lst = self._track_list()
        page.add(lst)
        more = Pill("More songs", style="glass")
        page.add(more, gap=12)
        more.hide()
        h = page.hero

        def load():
            if state["busy"] or state["next"] is None or self._catalog is not state:
                return
            state["busy"] = True
            more.setEnabled(False)

            def ok(got):
                state["busy"] = False
                if self._catalog is not state or not _alive(lst):
                    return
                have = {t.get("id") for t in state["tracks"]}
                state["tracks"] += [t for t in (got or {}).get("tracks") or [] if t.get("id") not in have]
                state["next"] = (got or {}).get("next")
                lst.set_tracks(state["tracks"])
                self._set_current_marks()
                years = "%s–%s" % (got.get("first_year"), got.get("last_year")) if got.get("first_year") else ""
                h.set_text("Every song", artist.get("title") or "Artist",
                           " · ".join(x for x in ("%d songs so far" % len(state["tracks"]) if state["next"] is not None
                                                  else "%d songs" % len(state["tracks"]), years) if x))
                if state["tracks"] and not getattr(h, "_catalog_played", False):
                    h._catalog_played = True
                    h.clear_actions()
                    h.add_action(Pill("Play", "play")).clicked.connect(
                        lambda: self.play_from(state["tracks"][0], state["tracks"]))
                    h.add_action(self._ring("shuffle", "Shuffle",
                                            lambda: self._play_list(state["tracks"], shuffle=True)))
                more.setVisible(state["next"] is not None)
                more.setEnabled(True)

            def fail(e):
                state["busy"] = False
                more.setEnabled(True)
                h.set_text("Every song", artist.get("title") or "Artist", str(e))
            self._brain(ok, fail, brain.artist_catalog, bid, state["next"], 8)
        h._catalog_played = False
        more.clicked.connect(load)
        load()

