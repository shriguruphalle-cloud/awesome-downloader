"""What came over from the phone app that isn't about people (mixed into MusicTab): the DJ (a run of short named sets,
steered by what you ask for, how new and how lively), moods and today's charts, and an artist's every song. The
choosing is the phone's own logic (app.core.music_brain); this draws it with the tab's own parts.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from app.core import music_brain as brain
from app.core import social
from app.logging_setup import get_logger

from . import music_look as look
from .music_social import Field, _alive, _label
from .music_widgets import Pill, SectionHeader

logger = get_logger("music_more")

VIBES = (("familiar", "Familiar"), ("balanced", "Balanced"), ("adventurous", "Adventurous"))
ENERGIES = (("chill", "Chill"), ("steady", "Steady"), ("hype", "Hype"))
EXTEND_LEFT = 3         # this few songs left in the DJ's run: more are asked for


class MusicMore:
    MORE_PAGES = ("dj", "discover", "catalog")

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
    def _build_discover(self):
        page = self._pages["discover"]
        page.clear()
        page.art_url = None
        page.hero.set_text("Discover", "Moods & charts", "A mix for how you feel, and what's on top today.")
        moods = QWidget()
        grid = QGridLayout(moods)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for i, (key, m) in enumerate(brain.MOOD_MIXES.items()):
            b = Pill(m.get("title") or key.title(), style="glass")
            b.clicked.connect(lambda _c=False, k=key: self._open_mood(k))
            grid.addWidget(b, i // 6, i % 6, Qt.AlignmentFlag.AlignLeft)
        page.add(moods, "Moods and genres")
        note = _label("Finding today's charts…", 13, color=look.TEXT_3)
        page.add(note, "Charts")
        seeds = (self.taste.state.get("seeds") or {}).get("languages") or []
        region = self._region()

        def ok(got, fresh=False):
            if not _alive(note) or not self._here or self._here[0] != "discover":
                return
            cards = (got or {}).get("charts") or []
            if not cards and not fresh:
                return
            if not cards:
                note.setText("The charts couldn't be read just now.")
                return
            note.hide()
            old = getattr(self, "_chart_grid", None)
            if old is not None and _alive(old):
                old.setParent(None)
                old.deleteLater()
            self._chart_grid = self._tile_grid("album", rows=4)
            self._chart_grid.set_items(cards)
            page.sec.insertWidget(page.sec.indexOf(note) + 1, self._chart_grid)

        def fail(_e):
            if _alive(note):
                note.setText("The charts couldn't be read just now.")
        # what was kept comes at once; the fresh ones follow
        self._brain(ok, lambda _e: None, brain.home_charts, region, seeds, social.BASE, False)
        self._brain(lambda got: ok(got, True), fail, brain.home_charts, region, seeds, social.BASE, True)

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

