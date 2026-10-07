"""About panel: the name, the version, that it's free and open source, what
it's built on, and who made it."""
from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app import config

from ..widgets import make_card
from .base import CinematicDialog, button_row
from ..widgets.button import Button

CREATOR_NAME = "Shriguru Phalle"
REPO_URL = "https://github.com/%s" % config.GITHUB_REPO
ISSUES_URL = REPO_URL + "/issues"
LICENCE = "GPL-3.0"

class OpenSourceBadge(QWidget):
    """"FREE & OPEN SOURCE · GPL-3.0" on a small glass pill with a lit dot --
    the product website's kicker, so the app and the site say it the same way."""

    TEXT = "Free & open source  ·  " + LICENCE

    def __init__(self, dark=True, parent=None):
        super().__init__(parent)
        self._dark = dark
        f = self._font()
        width = QFontMetricsF(f).horizontalAdvance(self.TEXT.upper())
        self.setFixedSize(int(width + 40), 26)
        self.setToolTip("Every line of the app is public on GitHub, under the %s licence." % LICENCE)

    def _font(self):
        f = QFont(self.font())
        f.setFamilies(["IBM Plex Mono", "Cascadia Mono", "Consolas"])
        f.setPixelSize(10)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        return f

    def sizeHint(self):
        return self.size()

    def paintEvent(self, event):
        from .. import theme
        t = theme.tokens(self._dark)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.qcolor(t["card_border"]), 1))
        p.setBrush(theme.qcolor(t["hover_overlay"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        brand = QColor(t["brand"])
        glow = QColor(brand)
        glow.setAlpha(70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(14, r.center().y()), 5.5, 5.5)
        p.setBrush(brand)
        p.drawEllipse(QPointF(14, r.center().y()), 3.0, 3.0)
        p.setFont(self._font())
        p.setPen(theme.qcolor(t["text_muted"]))
        p.drawText(r.adjusted(26, 0, -12, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   self.TEXT.upper())
        p.end()


def show_about(parent=None, dark_mode=True):
    dialog = CinematicDialog(parent, f"About {config.APP_NAME.title()}", dark_mode)
    dialog.setFixedSize(500, 600)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(26, 26, 26, 22)
    layout.setSpacing(14)

    # Identity: the name, as the title bar and the website set it.
    ident = QHBoxLayout()
    ident.setSpacing(14)
    names = QVBoxLayout()
    names.setSpacing(4)
    from .. import palettes, theme
    from ..widgets.wordmark import Wordmark
    t = theme.tokens(dark_mode)
    name_label = Wordmark(px=40)
    name_label.set_colors(t["text"], t["brand"], dark_mode, palettes.spec(dark_mode)["glows"][1][0])
    names.addWidget(name_label)
    version_label = QLabel(f"Version {config.APP_VERSION}")
    version_label.setObjectName("mono")
    version_label.setContentsMargins(int(name_label.text_rect().left()) + 2, 0, 0, 0)
    names.addWidget(version_label)
    badge_row = QHBoxLayout()
    badge_row.setContentsMargins(6, 4, 0, 0)
    badge_row.addWidget(OpenSourceBadge(dark_mode))
    badge_row.addStretch(1)
    names.addLayout(badge_row)
    ident.addLayout(names, 1)
    layout.addLayout(ident)

    oss_card, oss_layout = make_card("Open source")
    oss_text = QLabel(
        "Every line of Awesome Downloader is public on GitHub under the %s licence: "
        "read it, build it yourself, report a bug, or send an improvement. "
        "No ads, no tracking, no account — and it stays that way." % LICENCE)
    oss_text.setWordWrap(True)
    oss_layout.addWidget(oss_text)
    links = QHBoxLayout()
    links.setSpacing(8)
    source_btn = Button("View source on GitHub")
    source_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
    links.addWidget(source_btn)
    issue_btn = Button("Report an issue")
    issue_btn.setObjectName("quiet")
    issue_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)))
    links.addWidget(issue_btn)
    links.addStretch(1)
    oss_layout.addLayout(links)
    layout.addWidget(oss_card)

    card, card_layout = make_card("Built with")
    stack_label = QLabel("yt-dlp  ·  ffmpeg  ·  libtorrent  ·  PySide6  ·  WebView2  ·  AdGuard")
    stack_label.setWordWrap(True)
    card_layout.addWidget(stack_label)
    disclaimer_label = QLabel(
        "Downloading content may violate a site's Terms of Service unless "
        "you own it, have permission, or it's public domain. This app does "
        "not and will not support DRM-protected content."
    )
    disclaimer_label.setObjectName("muted")
    disclaimer_label.setWordWrap(True)
    card_layout.addWidget(disclaimer_label)
    layout.addWidget(card)

    # the credit in the name's own voice: "Downloader"'s italic serif and gradient
    from ..widgets.wordmark import GradientLine
    credit = GradientLine("Created by ", CREATOR_NAME, " — By an artist, for an artist.", px=21)
    credit.set_colors(t["text"], t["brand"], dark_mode, palettes.spec(dark_mode)["glows"][1][0])
    layout.addWidget(credit)

    tagline_label = QLabel("We don't lack creativity; we lack patience for tools that slow us down.")
    tagline_label.setObjectName("muted")
    tagline_label.setWordWrap(True)
    layout.addWidget(tagline_label)

    layout.addStretch(1)
    close_btn = Button("Close")
    close_btn.setObjectName("accent")
    close_btn.setMinimumWidth(110)
    close_btn.clicked.connect(dialog.close)
    layout.addLayout(button_row(close_btn))

    dialog.exec()
