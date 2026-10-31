"""Download tab: the one shared place every in-progress download shows up,
regardless of which tab started it (Video tab's yt-dlp fetches, Browser
tab's direct file downloads). Pulled out into its own module after direct
feedback that neither "downloads live under Video" nor "downloads live
under Browser" felt right -- a single universal downloads list, the way
every real download manager (and every browser's own Downloads page)
does it, is the placement that doesn't imply the tab belongs to the wrong
kind of file.

This module only owns card *lifecycle* (create/update/complete/fail/
remove) and the scrollable list they sit in. It knows nothing about yt-dlp,
HTTP, or file I/O -- the tab that actually runs a download (video_tab.py,
browser_tab.py) keeps that logic and its own small per-job "cancel" flag,
and just calls into this tab to reflect progress.
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .widgets import DownloadCard, EmptyState, centered_column
from .widgets.button import Button
from . import motion

# Past this width a list of cards stops reading as a list and starts reading
# as a spreadsheet; the column centres in whatever is left over instead.
MAX_CONTENT_W = 1760


class DownloadTab(QWidget):
    # A download started somewhere -- Video tab or Browser tab, either can
    # trigger this. MainWindow uses it to show a small "something happened
    # here" badge on this tab's pill, since a card appearing in a tab you
    # aren't currently looking at is otherwise invisible.
    job_started = Signal()
    # A download finished (done/cancelled/failed) -- separate from
    # job_started because visiting this tab while a job is still running
    # clears the start badge, and without this the *finish* would then be
    # silent: reported directly ("notification dot even after ... something
    # happens in a different tab" -- a completion after an earlier visit
    # had already cleared the dot).
    job_finished = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._cards = {}
        self._active = set()        # jobs still running or waiting -- what "Cancel all" stops
        self._job_counter = 0
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Kept as empty_label: it is what shows/hides as cards come and go.
        self.empty_label = EmptyState(
            "download", "Nothing downloading",
            "Fetch a video in the Video tab, or use the download button in the "
            "Browser. Every download shows up here, wherever it started.")
        root.addWidget(self.empty_label, 1)

        # Over the list while anything is running: how many, and Cancel all
        self.header = QWidget()
        self.header.setObjectName("downloadHeader")
        self.header.setStyleSheet("#downloadHeader { background: transparent; }")
        head = QHBoxLayout(self.header)
        head.setContentsMargins(4, 0, 4, 0)
        self.count_label = QLabel("")
        self.count_label.setObjectName("downloadCount")
        head.addWidget(self.count_label)
        head.addStretch(1)
        self.cancel_all_btn = Button("Cancel all")
        self.cancel_all_btn.setObjectName("historyPlain")
        self.cancel_all_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_all_btn.setToolTip("Stop every download that's running or waiting, music included")
        self.cancel_all_btn.clicked.connect(self.cancel_all)
        head.addWidget(self.cancel_all_btn)
        self.header.setVisible(False)
        root.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Scoped by id. An unscoped "background: transparent" is a
        # widget-level stylesheet, and a widget stylesheet outranks the
        # application one for every descendant -- so it also repainted the
        # accent/quiet buttons on the rows inside transparent, which is why
        # they rendered as bare text with no fill.
        scroll.setObjectName("downloadScroll")
        scroll.setStyleSheet("#downloadScroll { background: transparent; }")
        body = QWidget()
        body.setObjectName("downloadScrollBody")
        body.setStyleSheet("#downloadScrollBody { background: transparent; }")
        column = centered_column(body, MAX_CONTENT_W)
        self._list_layout = QVBoxLayout(column)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch(1)
        scroll.setWidget(body)
        self._scroll = scroll
        scroll.setVisible(False)
        root.addWidget(scroll, 1)

    def _dark_mode(self):
        return (self.settings or {}).get("theme", "dark") != "light"

    # ------------------------------------------------------- Card lifecycle
    def start_job(self, title, meta, thumb_pixmap, make_on_cancel,
                   make_on_pause_toggle=None, make_on_play=None, make_on_retry=None):
        """`make_on_cancel(job_id) -> callable` -- a factory rather than a
        plain callback, so the caller can close over the id this method is
        about to generate without a chicken-and-egg ordering problem (the
        card, which needs the real on_cancel callback, has to exist before
        this can return the id).

        `make_on_pause_toggle`/`make_on_play`/`make_on_retry` follow the
        same factory pattern and are optional -- a caller that has no
        genuine pause, play, or retry support just leaves the card's own
        default of "no button" in place instead of wiring a no-op."""
        self._job_counter += 1
        job_id = self._job_counter
        card = DownloadCard(
            title, meta, thumb_pixmap, self._dark_mode(),
            on_cancel=make_on_cancel(job_id),
            on_pause_toggle=make_on_pause_toggle(job_id) if make_on_pause_toggle else None,
            on_play=make_on_play(job_id) if make_on_play else None,
            on_retry=make_on_retry(job_id) if make_on_retry else None,
        )
        # insertWidget(0, ...), not addWidget: newest download belongs at
        # the top, ahead of the trailing stretch that keeps a short list
        # packed upward instead of spread down the tab.
        self._list_layout.insertWidget(0, card)
        motion.grow_in(card)
        self._cards[job_id] = card
        self._active.add(job_id)
        self._sync_header()
        self.empty_label.setVisible(False)
        self._scroll.setVisible(True)
        self.job_started.emit()
        return job_id

    def update_progress(self, job_id, pct, detail):
        card = self._cards.get(job_id)
        if card:
            card.set_progress(pct, detail)

    def set_waiting(self, job_id, position):
        card = self._cards.get(job_id)
        if card:
            card.set_waiting(position)

    def set_started(self, job_id):
        card = self._cards.get(job_id)
        if card:
            card.set_started()

    def set_playable(self, job_id, playable=True):
        card = self._cards.get(job_id)
        if card:
            card.set_playable(playable)

    def _finished(self, job_id):
        self._active.discard(job_id)
        self._sync_header()

    def _sync_header(self):
        n = len(self._active)
        self.header.setVisible(n > 0)
        self.count_label.setText("1 download" if n == 1 else "%d downloads" % n)

    def active_jobs(self):
        return sorted(self._active)

    def cancel_all(self):
        """Cancels every download still running or waiting -- each through
        its own card's Cancel, so each tab stops its own work the usual way."""
        for job_id in list(self._active):
            card = self._cards.get(job_id)
            if card is not None and card.cancel_btn.isEnabled() and card.cancel_btn.text() == "Cancel":
                card._handle_cancel()

    def mark_done(self, job_id, text="✓ Completed", delay_ms=4000):
        self._finished(job_id)
        card = self._cards.get(job_id)
        if card:
            card.set_complete(text)
        self._schedule_removal(job_id, delay_ms)
        self.job_finished.emit()

    def mark_cancelled(self, job_id, text="Cancelled.", delay_ms=1500):
        self._finished(job_id)
        card = self._cards.get(job_id)
        if card:
            card.set_failed(text)
        self._schedule_removal(job_id, delay_ms)
        self.job_finished.emit()

    def mark_failed(self, job_id, text):
        """Left on screen (no auto-removal) rather than dismissed like the
        two outcomes above -- a failure is the one result worth reading,
        and with several downloads running an auto-vanishing failure card
        would be easy to miss. The card's own button becomes "Dismiss" so
        it can be cleared once it's been seen."""
        self._finished(job_id)
        card = self._cards.get(job_id)
        if not card:
            return
        card.set_failed(text)
        card.cancel_btn.clicked.disconnect()
        card.cancel_btn.clicked.connect(lambda _c=False, jid=job_id: self.remove_job(jid))
        self.job_finished.emit()

    def reset_for_retry(self, job_id):
        if job_id in self._cards:
            self._active.add(job_id)
            self._sync_header()
        card = self._cards.get(job_id)
        if card:
            card.reset_for_retry()

    def remove_job(self, job_id):
        self._finished(job_id)
        card = self._cards.pop(job_id, None)
        if card:
            self._remove_card(card)

    def _schedule_removal(self, job_id, delay_ms):
        QTimer.singleShot(delay_ms, lambda: self.remove_job(job_id))

    def _remove_card(self, card):
        # Folds away first; the cards below slide up into its place.
        try:
            motion.shrink_out(card, lambda: self._drop_card(card))
        except RuntimeError:
            self._drop_card(card)

    def _drop_card(self, card):
        try:
            self._list_layout.removeWidget(card)
            card.deleteLater()
        except RuntimeError:
            pass  # already torn down (tab closed / theme rebuild)
        if not self._cards:
            self.empty_label.setVisible(True)
            self._scroll.setVisible(False)

    def apply_theme(self):
        """Duck-typed by MainWindow._retheme_tabs() after a live theme
        toggle, same as every other tab."""
        for card in self._cards.values():
            card.apply_theme(self._dark_mode())
