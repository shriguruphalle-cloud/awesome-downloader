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
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout

from ..widgets import make_card
from .base import CinematicDialog, button_row, header
from ..widgets.button import Button

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
    # Instagram's audience- and age-restricted posts
    "isn't available to everyone",
    "can't be seen by certain audiences",
    "restricted video",
    "only available to logged-in",
    "log in to",
    "login to",
)

# The browsers yt-dlp can read a cookie store from. Firefox first: Chrome and
# the other Chromium browsers now lock their cookies with app-bound
# encryption that nothing outside the browser can open, so on an up-to-date
# install borrowing their sign-in usually fails ("Failed to decrypt with
# DPAPI"). They stay listed -- older versions and some setups still work.
BROWSERS = [
    ("Firefox", "firefox"),
    ("Google Chrome", "chrome"),
    ("Microsoft Edge", "edge"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
]
BROWSER_NAMES = {key: label for label, key in BROWSERS}


def needs_sign_in(error_text):
    text = (error_text or "").lower()
    return any(marker in text for marker in _LOGIN_MARKERS)


# Chromium-based browsers. On Windows they encrypt cookies with app-bound
# encryption (Chrome 127+, and Edge, Brave and the rest since), which nothing
# outside the browser can decrypt -- closed or not.
_CHROMIUM = {"chrome", "chromium", "edge", "brave", "opera", "vivaldi", "whale"}


def is_permanent_cookie_failure(browser, message):
    """True when retrying `browser` can never work: its cookies are
    encrypted against other apps, or it isn't installed. "Could not copy
    Chrome cookie database" used to count as temporary -- Chrome holding the
    file open -- but on Windows closing Chrome only moves the failure one
    step on, to "Failed to decrypt with DPAPI"."""
    import sys

    text = (message or "").lower()
    if "decrypt" in text or "could not find" in text:
        return True
    return sys.platform == "win32" and (browser or "").lower() in _CHROMIUM


def handle_cookie_fallback(settings, browser, message):
    """The downloader couldn't read `browser`'s cookies and carried on
    without them (see downloader._drop_browser_cookies). Returns the line to
    show the user.

    A permanent failure (is_permanent_cookie_failure) moves the Sign-in
    setting back to this app's own Browser tab rather than failing the same
    way on every fetch. Anything else only explains."""
    from app.utils import settings as settings_store

    name = BROWSER_NAMES.get(browser, (browser or "that browser").title())
    if is_permanent_cookie_failure(browser, message):
        if settings is not None and settings.get("cookies_from_browser") == browser:
            settings["cookies_from_browser"] = None
            settings_store.save_settings(settings)
        if "could not find" in (message or "").lower():
            why = f"{name} isn't installed here"
        else:
            why = f"{name} keeps its sign-in locked against other apps"
        return (f"{why}, so this ran without it. Sign-in now uses this app's Browser "
                f"tab: sign in there once for posts that need an account.")
    return (f"Couldn't read {name}'s sign-in just now, so this ran without it. If a post "
            f"needs your account, close {name} and try again, or sign in in the Browser tab.")


def show_sign_in_help(parent, url="", dark_mode=True, current=None):
    """Returns the browser key the user chose to borrow cookies from, the
    string "browser_tab" if they want to sign in inside the app, or None if
    they dismissed it."""
    dialog = CinematicDialog(parent, "This link needs a sign-in", dark_mode)
    dialog.setMinimumWidth(500)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(26, 24, 26, 20)
    layout.setSpacing(12)

    layout.addLayout(header(
        "This post needs a signed-in account",
        "The site returned nothing for an anonymous request. It opens in your "
        "browser because you are signed in there. Pick how this app should "
        "get a session:"))

    card_a, lay_a = make_card("Sign in inside this app")
    detail_a = QLabel(
        "Opens the Browser tab. Nothing outside this app is read, and you can "
        "clear the session from the same place.")
    detail_a.setObjectName("muted")
    detail_a.setWordWrap(True)
    lay_a.addWidget(detail_a)
    open_browser_btn = Button("Open the Browser tab")
    open_browser_btn.setObjectName("accent")
    open_browser_btn.setCursor(Qt.PointingHandCursor)
    lay_a.addLayout(button_row(open_browser_btn, None, stretch_first=False))
    layout.addWidget(card_a)

    card_b, lay_b = make_card("Or borrow a browser's sign-in")
    detail_b = QLabel(
        "Reads that browser's cookies for the site you're downloading from — "
        "only that site's. Nothing is uploaded anywhere; it goes straight to "
        "the downloader on this machine. Close the browser first: it locks its "
        "own cookie file.")
    detail_b.setObjectName("muted")
    detail_b.setWordWrap(True)
    lay_b.addWidget(detail_b)

    row = QHBoxLayout()
    row.setSpacing(8)
    combo = QComboBox()
    for label, key in BROWSERS:
        combo.addItem(label, key)
    if current:
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
    row.addWidget(combo, 1)
    use_btn = Button("Use this browser")
    use_btn.setCursor(Qt.PointingHandCursor)
    row.addWidget(use_btn)
    lay_b.addLayout(row)
    layout.addWidget(card_b)

    cancel_btn = Button("Not now")
    cancel_btn.setObjectName("quiet")
    layout.addLayout(button_row(cancel_btn))

    chosen = {"value": None}

    def pick(value):
        chosen["value"] = value
        dialog.accept()

    open_browser_btn.clicked.connect(lambda: pick("browser_tab"))
    use_btn.clicked.connect(lambda: pick(combo.currentData()))
    cancel_btn.clicked.connect(dialog.reject)

    dialog.exec()
    return chosen["value"]
