"""About dialog
exactly, just built from Qt widgets instead of CTk ones.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app import config

from .. import theme

CREATOR_NAME = "Shriguru Phalle"
CREATOR_NAME_COLOR = "#e5484d"  # red


def show_about(parent=None, dark_mode=True):
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"About {config.APP_NAME}")
    dialog.setFixedSize(440, 380)
    t = theme.tokens(dark_mode=dark_mode)
    # This dialog is a plain (non-translucent) top-level window, unlike
    # MainWindow -- the shared stylesheet's "background: transparent" on
    # QDialog is meant for a window with WA_TranslucentBackground set (so
    # the Acrylic blur shows through); without that attribute here,
    # "transparent" would just leave Qt's plain default gray dialog
    # background showing instead. card_bg_solid's alpha is already near-
    # opaque (~92%), so using it directly still reads as a solid panel.
    dialog.setStyleSheet(
        theme.build_stylesheet(dark_mode=dark_mode) + f"QDialog {{ background: {t['card_bg_solid']}; }}"
    )

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 20)

    name_label = QLabel(config.APP_NAME)
    name_label.setObjectName("heading")
    layout.addWidget(name_label)

    version_label = QLabel(f"Version {config.APP_VERSION}")
    version_label.setObjectName("muted")
    layout.addSpacing(2)
    layout.addWidget(version_label)
    layout.addSpacing(12)

    built_with_label = QLabel("Built with")
    built_with_label.setStyleSheet("font-weight: 600;")
    layout.addWidget(built_with_label)
    stack_label = QLabel("yt-dlp  •  ffmpeg  •  libtorrent  •  PySide6")
    stack_label.setObjectName("muted")
    layout.addWidget(stack_label)
    layout.addSpacing(12)

    disclaimer_label = QLabel(
        "Downloading content may violate a site's Terms of Service unless "
        "you own it, have permission, or it's public domain. This app does "
        "not and will not support DRM-protected content."
    )
    disclaimer_label.setObjectName("muted")
    disclaimer_label.setWordWrap(True)
    layout.addWidget(disclaimer_label)
    layout.addSpacing(16)

    divider = QFrame()
    divider.setObjectName("divider")
    divider.setFixedHeight(1)
    layout.addWidget(divider)
    layout.addSpacing(16)

    credit_row = QHBoxLayout()
    prefix_label = QLabel("Created by ")
    prefix_label.setStyleSheet("font-weight: 600;")
    credit_row.addWidget(prefix_label)
    creator_label = QLabel(CREATOR_NAME)
    creator_label.setStyleSheet(f"font-weight: 600; color: {CREATOR_NAME_COLOR};")
    credit_row.addWidget(creator_label)
    suffix_label = QLabel(" — By an editor, for editors.")
    suffix_label.setStyleSheet("font-weight: 600;")
    credit_row.addWidget(suffix_label)
    credit_row.addStretch(1)
    layout.addLayout(credit_row)

    tagline_label = QLabel("Editors don't lack creativity; we lack patience for tools that slow us down.")
    tagline_label.setWordWrap(True)
    layout.addWidget(tagline_label)

    layout.addStretch(1)

    close_row = QHBoxLayout()
    close_row.addStretch(1)
    close_btn = QPushButton("Close")
    close_btn.setObjectName("accent")
    close_btn.clicked.connect(dialog.close)
    close_row.addWidget(close_btn)
    layout.addLayout(close_row)

    dialog.exec()
