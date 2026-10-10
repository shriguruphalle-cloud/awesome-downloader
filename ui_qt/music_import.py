"""Import a playlist from another app (app/core/playlist_import.py): paste a
public link from Spotify, Apple Music, YouTube Music or YouTube, or a list
of songs, and it's matched and saved as one of your playlists."""
import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit

from app.core import playlist_import
from app.logging_setup import get_logger
from app.utils import music_library

from . import music_look as look
from .music_panels import GlassSheet
from .music_widgets import LinkLabel, Pill, ThinSlider

logger = get_logger("music_import")

FIELD_QSS = ("background: rgba(0,0,0,70); border: 1px solid rgba(255,255,255,40); border-radius: 14px;"
             " color: #ffffff; padding: 10px 12px; font-size: 14px;"
             " selection-background-color: rgba(125,211,252,110);")


class ImportSheet(GlassSheet):
    imported = Signal(object)            # the new playlist
    _progress_sig = Signal(int, int, str)
    _done_sig = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent, max_w=820, max_h=600)
        self._busy = False
        self._progress_sig.connect(self._on_progress)
        self._done_sig.connect(self._on_done)
        lay = self.card_lay
        top = QHBoxLayout()
        over = QLabel("YOUR MUSIC  ·  IMPORT")
        over.setFont(look.label_font(11))
        over.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        top.addWidget(over)
        top.addStretch(1)
        self.close_link = LinkLabel("Close")
        self.close_link.clicked.connect(self.hide)
        top.addWidget(self.close_link)
        lay.addLayout(top)
        title = QLabel("BRING YOUR PLAYLISTS")
        title.setFont(look.display_font(42))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        lay.addWidget(title)
        sub = QLabel("Paste a public playlist or album link from Spotify, Apple Music, YouTube Music or YouTube — "
                     "or a list of songs, one per line (\"Artist - Song\"), or a CSV export.")
        sub.setWordWrap(True)
        sub.setFont(look.font(14, QFont.Weight.Medium))
        sub.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        lay.addWidget(sub)
        lay.addSpacing(6)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("https://open.spotify.com/playlist/…\n\nor\n\nArijit Singh - Tum Hi Ho\n"
                                     "Coldplay - Yellow")
        self.text.setStyleSheet("QPlainTextEdit { %s }" % FIELD_QSS)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name (optional — taken from the playlist)")
        self.name.setFixedHeight(42)
        self.name.setStyleSheet("QLineEdit { %s }" % FIELD_QSS.replace("padding: 10px 12px", "padding: 0 12px"))
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.bar = ThinSlider()
        self.bar.setEnabled(False)
        self.bar.hide()
        lay.addWidget(self.bar)
        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setFont(look.font(13, QFont.Weight.Medium))
        self.status.setStyleSheet("color: rgba(255,255,255,175); background: transparent;")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.go_btn = Pill("Import")
        self.go_btn.clicked.connect(self.start)
        foot.addWidget(self.go_btn)
        lay.addLayout(foot)

    def open(self, text=""):
        if not self._busy:
            self.text.setPlainText(text)
            self.name.clear()
            self.status.setText("")
            self.bar.hide()
        self.open_sheet()
        self.text.setFocus()

    def start(self):
        raw = self.text.toPlainText().strip()
        if not raw or self._busy:
            return
        self._busy = True
        self.go_btn.setEnabled(False)
        self.status.setText("Reading the playlist...")
        name = self.name.text().strip()

        def work():
            try:
                found = playlist_import.read(raw)
                tracks = list(found.get("tracks") or [])
                if found.get("items"):
                    total = len(found["items"])
                    self._progress_sig.emit(0, total, "Finding %d songs..." % total)
                    matched = playlist_import.match(
                        found["items"], lambda d, t: self._progress_sig.emit(d, t, "Finding songs... %d of %d"
                                                                             % (d, t)))
                    tracks += [m for m in matched if m]
                if not tracks:
                    raise playlist_import.ImportError_("None of its songs could be found.")
                missing = len(found.get("items") or []) - (len(tracks) - len(found.get("tracks") or []))
                pl = music_library.create_playlist(name or found["title"], tracks, source=found["source"],
                                                   artwork=tracks[0].get("artwork"))
                self._done_sig.emit(pl, "%d songs imported%s." % (
                    len(tracks), (", %d couldn't be found" % missing) if missing > 0 else ""))
            except Exception as e:   # noqa: BLE001
                logger.info("Playlist import failed", exc_info=True)
                self._done_sig.emit(None, str(e).splitlines()[0][:200] if str(e) else "The import failed.")
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done, total, text):
        self.bar.show()
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.status.setText(text)

    def _on_done(self, pl, text):
        self._busy = False
        self.go_btn.setEnabled(True)
        self.status.setText(text)
        if pl:
            self.hide()
            self.imported.emit(pl)
