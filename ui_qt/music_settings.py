"""The Music tab's settings, in one glass window: how songs play and run
into each other, the sound, the lyrics, what's suggested, and the library.
Every change is applied at once and kept (MusicTab saves the settings)."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from app.core import lyrics as lyrics_db
from app.core import music_sources

from . import music_look as look
from . import theme
from .browser_chrome import style_menu
from .music_panels import GlassSheet, Switch
from .music_widgets import LinkLabel, Pill, ThinSlider


def _text(text, px, weight=QFont.Weight.Medium, alpha=255):
    lab = QLabel(text)
    lab.setFont(look.font(px, weight))
    lab.setStyleSheet("color: rgba(255,255,255,%d); background: transparent;" % alpha)
    lab.setWordWrap(True)
    return lab


class MusicSettings(GlassSheet):
    def __init__(self, tab):
        super().__init__(tab, max_w=760, max_h=760)
        self.tab = tab
        lay = self.card_lay
        top = QHBoxLayout()
        over = QLabel("MUSIC  ·  SETTINGS")
        over.setFont(look.label_font(11))
        over.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        top.addWidget(over)
        top.addStretch(1)
        close = LinkLabel("Done")
        close.clicked.connect(self.hide)
        top.addWidget(close)
        lay.addLayout(top)
        title = QLabel("YOUR PLAYER")
        title.setFont(look.display_font(40))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        lay.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                             " { background: transparent; } QScrollBar:vertical { width: 6px; background:"
                             " transparent; } QScrollBar::handle:vertical { background: rgba(255,255,255,50);"
                             " border-radius: 3px; } QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        body = QWidget()
        self.col = QVBoxLayout(body)
        self.col.setContentsMargins(0, 6, 10, 6)
        self.col.setSpacing(4)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self._build()

    # ---- rows ----
    def _section(self, name):
        if self.col.count():
            self.col.addSpacing(14)
        lab = QLabel(name.upper())
        lab.setFont(look.label_font(10.5))
        lab.setStyleSheet("color: rgba(255,255,255,140); background: transparent;")
        self.col.addWidget(lab)

    def _row(self, title, note, *controls):
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 8)
        row.setSpacing(12)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_text(title, 14.5, QFont.Weight.DemiBold))
        if note:
            words.addWidget(_text(note, 12.5, QFont.Weight.Normal, 160))
        row.addLayout(words, 1)
        for c in controls:
            row.addWidget(c, 0, Qt.AlignmentFlag.AlignVCenter)
        self.col.addLayout(row)

    def _switch(self, on, changed):
        sw = Switch()
        sw.accent = self.tab._np_pal["accent"]
        sw.setChecked(bool(on))
        sw.toggled.connect(changed)
        return sw

    def _pill(self, text, slot, style="ghost"):
        b = Pill(text, style=style)
        b.clicked.connect(slot)
        return b

    def _build(self):
        t, st = self.tab, self.tab.settings or {}
        self._section("Playback")
        blend_s = float(st.get("music_blend_s", t.BLEND_S))
        self.blend_value = _text("%d s" % blend_s if blend_s else "Off", 13, QFont.Weight.DemiBold, 200)
        self.blend_value.setFixedWidth(40)
        self.blend = ThinSlider()
        self.blend.setRange(0, 12)
        self.blend.setValue(int(blend_s))
        self.blend.setFixedWidth(170)
        self.blend.accent = t._np_pal["accent"]
        self.blend.valueChanged.connect(self._blend_changed)
        self._row("Blend songs", "The end of one song fades into the next (not between an album's tracks, "
                                 "which are meant to run on as they are). Off: the next song follows with no gap.",
                  self.blend, self.blend_value)
        self._row("Resume where you left off", "When the app opens, the last song is back where it was, paused "
                                               "-- press play to carry on.",
                  self._switch(st.get("music_resume_play", True), lambda on: t.set_pref("music_resume_play", on)))
        self._row("Autoplay", "When the queue ends, songs like the last one keep playing — same language and "
                              "mood, never the same song twice.",
                  self._switch(t.taste.state["prefs"].get("autoplay", True), t.set_autoplay))
        self._row("Level the volume", "Every song brought to the same loudness (measured, as Spotify and Apple "
                                      "Music do), so a loud one doesn't jump out.",
                  self._switch(t.eq.get("normalize"), t.set_normalize))
        self._section("Look")
        from .music_backdrop import MODES
        current = st.get("music_backdrop", "aurora")
        self.bg_btns = []
        for key, name in MODES:
            b = Pill(name, style="tab_on" if key == current else "glass")
            b.clicked.connect(lambda _c=False, k=key: self._backdrop(k))
            b.key = key
            self.bg_btns.append(b)
        self._row("Full-screen background", "A fluid animation in the playing cover's colours that moves with "
                                           "the beat -- or the cover itself, still.", *self.bg_btns)
        self._section("Sound")
        self._row("Equalizer & effects", "Ten bands, presets, bass boost, virtual surround and mono — heard as "
                                         "you change them.", self._pill("Open", lambda: (self.hide(),
                                                                                         t.open_side("eq"))))
        self._section("Lyrics")
        self.lyr_btn = self._pill(self._lyrics_label(), self._lyrics_menu)
        self._row("Under each line", "Shown under the words in full screen: the same words in another script, "
                                     "how they're said, or a translation.", self.lyr_btn)
        self._section("Suggestions")
        disc = float(t.taste.state["prefs"].get("discover", 0.3))
        self.disc_btns = []
        for name, value in (("Fewer", 0.1), ("Some", 0.3), ("More", 0.6)):
            b = Pill(name, style="tab_on" if abs(disc - value) < 0.05 else "glass")
            b.clicked.connect(lambda _c=False, v=value: self._discover(v))
            b.value = value
            self.disc_btns.append(b)
        self._row("New artists in your mixes", "How much of Made for you is from artists you haven't played yet.",
                  *self.disc_btns)
        self._row("Your taste", "The languages, genres and artists you started with — they count for less "
                                "as you play.", self._pill("Edit", lambda: (self.hide(), t.open_taste(editing=True))))
        self._row("Your mixes", "Made again from what you've been playing.",
                  self._pill("Refresh", lambda: (t.refresh_feed(force=True),
                                                 t.status.setText("Making your mixes again..."))))
        self._row("Start afresh", "Forget everything learned from your listening.",
                  self._pill("Forget", self._forget))
        self._section("Library")
        self._row("Import a playlist", "From Spotify, Apple Music, YouTube Music or YouTube — or a list of songs.",
                  self._pill("Import", lambda: (self.hide(), t.importer.open())))
        self.folder_note = _text(t.music_dir, 12, QFont.Weight.Normal, 150)
        self._row("Music folder", "Where downloaded songs are saved, and On this PC is read from.",
                  self._pill("Change", self._folder))
        self.col.addWidget(self.folder_note)
        self.cache_btn = self._pill("Clear (%d MB)" % self._cache_mb(), self._clear)
        self._row("Fetched songs", "Songs kept to play again at once (the last %d)." % music_sources.CACHE_KEEP,
                  self.cache_btn)
        self.col.addStretch(1)

    # ---- changes ----
    def _blend_changed(self, v):
        self.blend_value.setText("%d s" % v if v else "Off")
        self.tab.set_blend(v)

    def _backdrop(self, key):
        self.tab.set_backdrop(key)
        for b in self.bg_btns:
            b.style = "tab_on" if b.key == key else "glass"
            b.update()

    def _discover(self, value):
        self.tab.taste.state["prefs"]["discover"] = value
        self.tab.taste.save()
        for b in self.disc_btns:
            b.style = "tab_on" if b.value == value else "glass"
            b.update()

    def _lyrics_label(self):
        under = (self.tab.settings or {}).get("lyrics_sub", "auto")
        if under in ("auto", None):
            return "Automatic"
        if under == "":
            return "Nothing"
        if under == "pron":
            return "Pronunciation"
        if under.startswith("tr:"):
            return "Translation: " + dict(lyrics_db.TRANSLATE_TO).get(under[3:], under[3:])
        return lyrics_db.SHORT.get(under[2:], under[2:])

    def _lyrics_menu(self):
        menu = style_menu(QMenu(self), theme.tokens(True))
        current = (self.tab.settings or {}).get("lyrics_sub", "auto")
        for value, text in (("auto", "Automatic — as Apple Music shows the song"), ("", "Nothing"),
                            ("pron", "How it's said (in English letters)")):
            a = menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(current == value)
            a.triggered.connect(lambda _c=False, v=value: self._set_lyrics(v))
        tr = style_menu(menu.addMenu("Translation"), theme.tokens(True))
        for code, name in lyrics_db.TRANSLATE_TO:
            a = tr.addAction(name)
            a.setCheckable(True)
            a.setChecked(current == "tr:" + code)
            a.triggered.connect(lambda _c=False, v="tr:" + code: self._set_lyrics(v))
        menu.exec(QCursor.pos())

    def _set_lyrics(self, value):
        self.tab._set_lyrics_pref(sub=value)
        self.lyr_btn.setText(self._lyrics_label())
        self.lyr_btn.adjustSize()

    def _forget(self):
        if QMessageBox.question(self, "Start afresh", "Forget everything learned from your listening, and your "
                                "taste picks?") == QMessageBox.StandardButton.Yes:
            self.tab._taste_reset()

    def _folder(self):
        self.tab._choose_folder()
        self.folder_note.setText(self.tab.music_dir)

    @staticmethod
    def _cache_mb():
        d = music_sources.CACHE_DIR
        try:
            return sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)) // (1024 * 1024)
        except OSError:
            return 0

    def _clear(self):
        self.tab._clear_cache()
        self.cache_btn.setText("Clear (0 MB)")
        self.cache_btn.adjustSize()

    def open(self):
        self.open_sheet()

