"""Sharing a song: to a friend, as a link, or as a video for an Instagram Story, Reel or post (the phone app's
ShareSheet.kt). On the PC a finished video is saved to your Videos, shown in its folder, or copied, to attach anywhere."""
import os
import shutil
import threading

from PySide6.QtCore import QMimeData, Qt, QStandardPaths, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core import music_sources
from app.logging_setup import get_logger

from . import music_look as look
from . import share_video
from .music_panels import GlassSheet
from .music_widgets import LinkLabel, art, Pill, ThinSlider

logger = get_logger("music_share")

LENGTHS = ((15, "15 s"), (30, "30 s"), (60, "60 s"))


def videos_dir():
    """Your Videos, Awesome Music's folder in it."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    return os.path.join(base, "Awesome Music")


def _text(text, px, weight=QFont.Weight.Medium, alpha=255):
    lab = QLabel(text)
    lab.setFont(look.font(px, weight))
    lab.setStyleSheet("color: rgba(255,255,255,%d); background: transparent;" % alpha)
    lab.setWordWrap(True)
    return lab


class _Preview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pix = None

    def set_image(self, img):
        self.pix = QPixmap.fromImage(img)
        self.update()

    def paintEvent(self, _event):
        if self.pix is None:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        scale = min(self.width() / self.pix.width(), self.height() / self.pix.height())
        w, h = self.pix.width() * scale, self.pix.height() * scale
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        path = QPainterPath()
        path.addRoundedRect(x, y, w, h, 16, 16)
        p.setClipPath(path)
        p.drawPixmap(int(x), int(y), int(w), int(h), self.pix)


class ShareSheet(GlassSheet):
    """The preview on the left; on the right what to make, how it's going, or where it should go."""
    _fetched = Signal(int, object, str)

    def __init__(self, tab):
        super().__init__(tab, max_w=1040, max_h=780)
        self.tab = tab
        self.track = None
        self.art = None
        self.pal = look.DEFAULT
        self.story, self.seconds, self.start, self.sound, self.look = True, 15, "now", True, "card"
        self.pick_ms = 0
        self.lines = []
        self.file = None
        self._gen = 0
        self._groups = {}
        self.maker = share_video.VideoMaker(self)
        self.maker.progress.connect(self._on_progress)
        self.maker.done.connect(self._on_done)
        self.maker.failed.connect(self._on_failed)
        self._fetched.connect(self._on_fetched)
        lay = self.card_lay
        top = QHBoxLayout()
        self.over = QLabel("SHARE")
        self.over.setFont(look.label_font(11))
        self.over.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        top.addWidget(self.over)
        top.addStretch(1)
        close = LinkLabel("Close")
        close.clicked.connect(self.close_sheet)
        top.addWidget(close)
        lay.addLayout(top)
        self.name = _text("", 22, QFont.Weight.Bold)
        lay.addWidget(self.name)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.friend_btn = Pill("Send to a friend", style="glass")
        self.friend_btn.clicked.connect(self._to_friend)
        self.link_btn = Pill("Copy link", style="glass")
        self.link_btn.clicked.connect(self._copy_link)
        row.addWidget(self.friend_btn)
        row.addWidget(self.link_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(6)
        body = QHBoxLayout()
        body.setSpacing(28)
        self.preview = _Preview()
        self.preview.setFixedWidth(320)
        body.addWidget(self.preview)
        self.side = QVBoxLayout()
        self.side.setSpacing(8)
        body.addLayout(self.side, 1)
        lay.addLayout(body, 1)
        self._build_choices()
        self._build_making()
        self._build_ready()

    # ---- what's on the right ----
    def _group(self, col, key, label, options, current):
        col.addWidget(_text(label.upper(), 10.5, QFont.Weight.DemiBold, 150))
        row = QHBoxLayout()
        row.setSpacing(6)
        pills = []
        for value, name in options:
            b = Pill(name, style="tab_on" if value == current else "glass")
            b.clicked.connect(lambda _c=False, k=key, v=value: self._choose(k, v))
            b.value = value
            pills.append(b)
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        col.addSpacing(4)
        self._groups[key] = pills

    def _build_choices(self):
        self.choices = QWidget()
        col = QVBoxLayout(self.choices)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(_text("A VIDEO FOR INSTAGRAM", 11, QFont.Weight.DemiBold, 190))
        self.look_box = QVBoxLayout()
        col.addLayout(self.look_box)
        self._group(col, "story", "Shape", ((True, "Story · Reel  9:16"), (False, "Post  4:5")), self.story)
        self._group(col, "seconds", "Length", LENGTHS, self.seconds)
        self._group(col, "start", "Starts", (("now", "Where it's playing"), ("beginning", "Beginning"),
                                             ("pick", "Pick a moment")), self.start)
        self.pick_note = _text("", 13, QFont.Weight.DemiBold)
        self.pick = ThinSlider()
        self.pick.valueChanged.connect(self._picked)
        col.addWidget(self.pick_note)
        col.addWidget(self.pick)
        self._group(col, "sound", "Sound", ((True, "The song"), (False, "Silent")), self.sound)
        self.fail_note = _text("", 13.5, alpha=255)
        self.fail_note.setStyleSheet("color: #ffb4a6; background: transparent;")
        self.fail_note.hide()
        col.addWidget(self.fail_note)
        self.make_btn = Pill("Make the video")
        self.make_btn.clicked.connect(self.make)
        col.addWidget(self.make_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(_text("Instagram may mute music it holds the rights to.", 12, QFont.Weight.Normal, 150))
        col.addStretch(1)
        self.side.addWidget(self.choices, 1)

    def _build_making(self):
        self.making = QWidget()
        col = QVBoxLayout(self.making)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        self.making_note = _text("Getting the song…", 15, QFont.Weight.DemiBold)
        col.addWidget(self.making_note)
        self.bar = ThinSlider()
        self.bar.setRange(0, 1000)
        self.bar.setEnabled(False)
        col.addWidget(self.bar)
        b = Pill("Cancel", style="outline")
        b.clicked.connect(self.cancel)
        col.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(2)
        self.side.addWidget(self.making, 1)

    def _build_ready(self):
        self.ready = QWidget()
        col = QVBoxLayout(self.ready)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        col.addWidget(_text("Ready — where should it go?", 18, QFont.Weight.Bold))
        row = QHBoxLayout()
        row.setSpacing(8)
        for name, slot, style in (("Save to Videos", self.save, "solid"), ("Show in folder", self.show_folder, "glass"),
                                  ("Copy the video", self.copy_file, "glass")):
            b = Pill(name, style=style) if style != "solid" else Pill(name)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        self.ready_note = _text("Attach it in Instagram on the web, or copy it to your phone. The app's link is copied "
                                "with it: add it with Instagram's Link sticker.", 13, QFont.Weight.Normal, 170)
        col.addWidget(self.ready_note)
        again = Pill("Make another", style="outline")
        again.clicked.connect(lambda: self._show("choices"))
        col.addWidget(again, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(2)
        self.side.addWidget(self.ready, 1)

    def _show(self, which):
        for name in ("choices", "making", "ready"):
            getattr(self, name).setVisible(name == which)

    def _choose(self, key, value):
        setattr(self, key, value)
        for b in self._groups[key]:
            b.style = "tab_on" if b.value == value else "glass"
            b.update()
        self._update_pick()
        self._refresh_preview()

    # ---- the song ----
    def open_for(self, track):
        self.track = track
        self.file = None
        cur = self.tab.current()
        self.current = cur is not None and cur.get("id") == track.get("id")
        self.song_ms = (self.tab.player.duration() if self.current else 0) or int(track.get("duration") or 0) * 1000
        self.now_ms = self.tab.player.position() if self.current else 0
        self.over.setText("SHARE")
        self.name.setText(track.get("title", ""))
        self.link_btn.setVisible(bool(track.get("page_url")) and track.get("source") != "local")
        self.friend_btn.setVisible(self.tab.hub.signed_in())
        self.pal = self.tab._np_pal if self.current else look.DEFAULT
        self.art = None
        url = track.get("artwork")
        if url:
            art().want(url, self._got_art, first=True)
        # what the video shows: what the player is showing now (the words only when this song has them in time)
        self.lines = self.tab.synced_lines(track)
        full = getattr(self.tab, "full", None)
        showing = self.current and full is not None and full.isVisible() and full.lyrics.isVisible()
        self.look = "lyrics" if showing and self.lines else "card"
        self._rebuild_looks()
        self.fail_note.hide()
        self._update_pick()
        self._refresh_preview()
        self._show("making" if self.maker.running else "choices")
        self.open_sheet()

    def _got_art(self, pix):
        if self.track is None:
            return
        self.art = pix.toImage() if isinstance(pix, QPixmap) else pix
        if not self.current:
            url = self.track.get("artwork")
            pal = art().palette(url) if url else None
            if pal:
                self.pal = pal
        self._refresh_preview()

    def _rebuild_looks(self):
        while self.look_box.count():
            item = self.look_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.setParent(None)
                        sub.deleteLater()
        options = [("card", "Player"), ("focus", "Focus")] + ([("lyrics", "Lyrics")] if self.lines else [])
        self._group(self.look_box, "look", "Look", options, self.look)

    def _update_pick(self):
        room = max(0, self.song_ms - self.seconds * 1000) if self.track else 0
        show = self.start == "pick" and room > 0
        self.pick_note.setVisible(show)
        self.pick.setVisible(show)
        if show:
            self.pick.blockSignals(True)
            self.pick.setRange(0, room // 1000)
            self.pick.setValue(min(room, self.pick_ms or self.now_ms) // 1000)
            self.pick.blockSignals(False)
            self._picked(self.pick.value())

    def _picked(self, seconds):
        self.pick_ms = int(seconds) * 1000
        self.pick_note.setText("From %s to %s" % (share_video.clock(self.pick_ms),
                                                  share_video.clock(self.pick_ms + self.seconds * 1000)))

    def _refresh_preview(self):
        if self.track is None:
            return
        self.preview.set_image(share_video.still(540, 960 if self.story else 675, self.track, self.art, self.pal,
                                                 self.song_ms, self.look, self.lines, self.now_ms))

    # ---- making it ----
    def start_ms(self):
        return {"beginning": 0, "pick": self.pick_ms}.get(self.start, self.now_ms)

    def make(self):
        track = self.track
        if track is None or self.maker.running:
            return
        self.fail_note.hide()
        self.making_note.setText("Getting the song…")
        self.bar.setValue(0)
        self._show("making")
        self._gen += 1
        gen = self._gen

        def work():
            try:
                src = track["stream"] if track.get("source") == "local" else music_sources.fetch_song(track)
                self._fetched.emit(gen, src, "")
            except Exception as e:   # noqa: BLE001
                logger.info("Couldn't fetch a song for its video: %s", type(e).__name__)
                self._fetched.emit(gen, None, music_sources.friendly(e))
        threading.Thread(target=work, daemon=True, name="share-fetch").start()

    def _on_fetched(self, gen, src, why):
        if gen != self._gen:
            return
        if not src:
            self._on_failed(why or "The song couldn't be fetched.")
            return
        settings = self.tab.settings or {}
        mode = settings.get("music_backdrop", "aurora")
        self.maker.make(src, self.track, self.art, self.pal, self.song_ms, self.start_ms(), mode,
                        bool(settings.get("music_backdrop_mono")), story=self.story, seconds=self.seconds,
                        sound=self.sound, look_name=self.look, lines=self.lines if self.look == "lyrics" else ())

    def _on_progress(self, pct, what):
        self.making_note.setText("%s  %d%%" % (what, int(pct * 100)))
        self.bar.setValue(int(pct * 1000))

    def _on_done(self, path):
        self.file = path
        self._show("ready")

    def _on_failed(self, why):
        self.fail_note.setText(why or "The video couldn't be made.")
        self.fail_note.show()
        self._show("choices")

    def cancel(self):
        self._gen += 1
        self.maker.cancel()
        self._show("choices")

    # ---- where it goes ----
    def save(self):
        if not self.file:
            return
        try:
            folder = videos_dir()
            os.makedirs(folder, exist_ok=True)
            shutil.copy2(self.file, os.path.join(folder, os.path.basename(self.file)))
            self.tab.status.setText("Saved to your Videos (Awesome Music).")
        except OSError as e:
            self.tab.status.setText("Couldn't save the video: %s" % (e.strerror or "no room?"))

    def show_folder(self):
        if self.file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.file)))

    def copy_file(self):
        if not self.file:
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(self.file)])
        data.setText(share_video.APP_LINK)
        QApplication.clipboard().setMimeData(data)
        self.tab.status.setText("Video copied. Paste it wherever it should go.")

    def _copy_link(self):
        if self.track and self.track.get("page_url"):
            QApplication.clipboard().setText(self.track["page_url"])
            self.tab.status.setText("Link copied.")

    def _to_friend(self):
        track = self.track
        self.close_sheet()
        if track is not None:
            self.tab.share_menu(track, QCursor.pos())

    def close_sheet(self):
        self.track = None if not self.maker.running else self.track
        self.hide()
