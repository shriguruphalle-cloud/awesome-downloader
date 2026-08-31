"""Shown when a link needs a signed-in session.

Sites like Instagram answer an anonymous request with an empty media
response. yt-dlp's own error text explains this in terms of `--cookies-from-
browser` command-line flags, which is useless inside a GUI -- it was being
shown verbatim, so the app's answer to "this link needs a login" was a wall
of terminal instructions.

There are two honest ways to get a session, and this offers both rather than
picking one silently, because they differ in what they touch:

  * sign in inside this app's own Browser tab -- nothing outside the app is
    read, and the session can be cleared from the same place;
  * borrow the cookies from an installed browser -- nothing to sign into
    again, but it means reading that browser's cookie store.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from app import config

from .. import theme

# yt-dlp's wording for "this needs a login", across extractors.
_LOGIN_MARKERS = (
    "empty media response",
    "cookies-from-browser",
    "login required",
    "requires authentication",
    "sign in to confirm",
    "private video",
    "this video is only available for registered users",
    "use --cookies",
)

# The browsers yt-dlp can read a cookie store from.
BROWSERS = [
    ("Google Chrome", "chrome"),
    ("Microsoft Edge", "edge"),
    ("Firefox", "firefox"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
]


def needs_sign_in(error_text):
    text = (error_text or "").lower()
    return any(marker in text for marker in _LOGIN_MARKERS)


def show_sign_in_help(parent, url="", dark_mode=True, current=None):
    """Returns the browser key the user chose to borrow cookies from, the
    string "browser_tab" if they want to sign in inside the app, or None if
    they dismissed it."""
    t = theme.tokens(dark_mode=dark_mode)
    dialog = QDialog(parent)
    dialog.setWindowTitle("This link needs a sign-in")
    dialog.setMinimumWidth(460)
    dialog.setStyleSheet(
        theme.build_stylesheet(dark_mode=dark_mode)
        + f"QDialog {{ background: {t['card_bg_solid']}; }}"
    )

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(10)

    heading = QLabel("This post is only visible to a signed-in account")
    heading.setObjectName("heading")
    heading.setWordWrap(True)
    layout.addWidget(heading)

    body = QLabel(
        "The site returned nothing for an anonymous request. It opens in your "
        "browser because you are signed in there.\n\nPick how this app should "
        "get a session:")
    body.setObjectName("muted")
    body.setWordWrap(True)
    layout.addWidget(body)

    layout.addSpacing(4)
    option_a = QLabel("Sign in inside this app")
    option_a.setStyleSheet("font-weight: 600;")
    layout.addWidget(option_a)
    detail_a = QLabel(
        "Opens the Browser tab. Nothing outside this app is read, and you can "
        "clear the session from the same place.")
    detail_a.setObjectName("muted")
    detail_a.setWordWrap(True)
    layout.addWidget(detail_a)

    open_browser_btn = QPushButton("Open the Browser tab")
    open_browser_btn.setObjectName("accent")
    open_browser_btn.setCursor(Qt.PointingHandCursor)
    layout.addWidget(open_browser_btn)

    layout.addSpacing(10)
    option_b = QLabel("Or borrow a sign-in from a browser you already use")
    option_b.setStyleSheet("font-weight: 600;")
    layout.addWidget(option_b)
    detail_b = QLabel(
        "Reads that browser's cookies for the sites you download from. Nothing "
        "is uploaded anywhere; it is passed straight to the downloader on this "
        "machine. Close the browser first -- it locks its own cookie file.")
    detail_b.setObjectName("muted")
    detail_b.setWordWrap(True)
    layout.addWidget(detail_b)

    row = QHBoxLayout()
    combo = QComboBox()
    for label, key in BROWSERS:
        combo.addItem(label, key)
    if current:
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
    row.addWidget(combo, 1)
    use_btn = QPushButton("Use this browser")
    use_btn.setCursor(Qt.PointingHandCursor)
    row.addWidget(use_btn)
    layout.addLayout(row)

    layout.addSpacing(6)
    footer = QHBoxLayout()
    footer.addStretch(1)
    cancel_btn = QPushButton("Not now")
    cancel_btn.setObjectName("quiet")
    cancel_btn.setCursor(Qt.PointingHandCursor)
    footer.addWidget(cancel_btn)
    layout.addLayout(footer)

    chosen = {"value": None}

    def pick(value):
        chosen["value"] = value
        dialog.accept()

    open_browser_btn.clicked.connect(lambda: pick("browser_tab"))
    use_btn.clicked.connect(lambda: pick(combo.currentData()))
    cancel_btn.clicked.connect(dialog.reject)

    dialog.exec()
    return chosen["value"]
