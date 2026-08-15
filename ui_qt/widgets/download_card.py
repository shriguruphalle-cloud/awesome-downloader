"""One active download, rendered as a compact card.

16:9 thumbnail on the left, title/meta on the right, progress bar underneath.
Several of these stack in the Video tab so a batch of downloads is all
visible at once, newest on top.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .card import _PaintedCard
from .progress import AnimatedProgressBar

# 16:9 exactly -- asked for directly. Everything downstream (the placeholder,
# the scaled pixmap) derives from these two numbers so the ratio can't drift.
THUMB_W, THUMB_H = 128, 72


class DownloadCard(_PaintedCard):
    def __init__(self, title, meta, pixmap, dark_mode, on_cancel, parent=None):
        super().__init__(parent=parent)
        self._on_cancel = on_cancel

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_W, THUMB_H)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setObjectName("muted")
        self.thumb_label.setScaledContents(False)
        self.set_thumbnail(pixmap)
        top.addWidget(self.thumb_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.title_label.setWordWrap(True)
        text_col.addWidget(self.title_label)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("muted")
        self.meta_label.setWordWrap(True)
        text_col.addWidget(self.meta_label)
        self.detail_label = QLabel("Starting...")
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        text_col.addWidget(self.detail_label)
        text_col.addStretch(1)
        top.addLayout(text_col, 1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("historyPlain")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._handle_cancel)
        top.addWidget(self.cancel_btn, 0, Qt.AlignTop)

        root.addLayout(top)

        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self.progress_bar = _make_bar(dark_mode)
        bar_row.addWidget(self.progress_bar, 1)
        self.pct_label = QLabel("0%")
        self.pct_label.setFixedWidth(42)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar_row.addWidget(self.pct_label)
        root.addLayout(bar_row)

    # ------------------------------------------------------------ content ---
    def set_thumbnail(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            # KeepAspectRatioByExpanding + crop keeps the tile exactly 16:9
            # even when the source thumbnail isn't (YouTube serves 4:3 for
            # some older uploads), instead of letterboxing it inside the box.
            scaled = pixmap.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatioByExpanding,
                                    Qt.SmoothTransformation)
            x = max(0, (scaled.width() - THUMB_W) // 2)
            y = max(0, (scaled.height() - THUMB_H) // 2)
            self.thumb_label.setPixmap(scaled.copy(x, y, THUMB_W, THUMB_H))
            self.thumb_label.setText("")
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText("No preview")

    def set_title(self, text):
        self.title_label.setText(text)

    def set_meta(self, text):
        self.meta_label.setText(text)

    # ----------------------------------------------------------- progress ---
    def set_progress(self, pct, detail):
        self.progress_bar.setValue(int(max(0.0, min(100.0, pct))))
        self.progress_bar.set_animating(True)
        self.pct_label.setText(f"{pct:.0f}%")
        if detail:
            self.detail_label.setText(detail)

    def set_complete(self, text="✓ Completed"):
        self.progress_bar.setValue(100)
        self.progress_bar.set_complete(True)
        self.pct_label.setText("100%")
        self.detail_label.setText(text)
        self.cancel_btn.setVisible(False)

    def set_failed(self, text):
        self.progress_bar.set_animating(False)
        self.detail_label.setText(text)
        self.cancel_btn.setText("Dismiss")
        self.cancel_btn.setEnabled(True)

    def apply_theme(self, dark_mode):
        self.progress_bar.set_colors(*_bar_colors(dark_mode))

    # ------------------------------------------------------------- cancel ---
    def _handle_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling...")
        if self._on_cancel:
            self._on_cancel()


def _bar_colors(dark_mode):
    from .. import theme
    t = theme.tokens(dark_mode=dark_mode)
    track = QColor(255, 255, 255, 26) if dark_mode else QColor(0, 0, 0, 26)
    return track, t["progress"], t["success"]


def _make_bar(dark_mode):
    return AnimatedProgressBar(*_bar_colors(dark_mode))
