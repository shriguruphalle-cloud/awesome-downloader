"""The owner's dashboard (the phone app's Owner.kt; mixed into MusicTab). Its door is hidden, as on the phone: Settings,
ten quick clicks on the maker's name, a PIN. Its data comes from /v1/owner/..., which the server answers only for the
owner's account with the right PIN: this code only keeps the door out of sight, the server is the lock. It knows a hash
of the owner's email and never the PIN, which is held in memory while the page is open and never saved or logged.
"""
import hashlib
import time
from urllib.parse import quote, urlsplit

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIntValidator, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from app.core import social
from app.logging_setup import get_logger

from . import music_look as look
from .music_panels import GlassSheet
from .music_social import Field, _alive
from .music_widgets import Pill, art

logger = get_logger("music_owner")

EMAIL_SHA = "386c476158d3a0f6d523974f183186629d86ff6973e578073a22c4a038136dbe"
LOCAL_TEST_OWNER = "owner.dashboard@example.test"      # the local test server's owner (its .dev.vars), there only
TABS = (("overview", "Overview"), ("users", "Users"), ("music", "Music"), ("revenue", "Revenue"), ("system", "System"))
ROWS_AT_ONCE = 40
BLUE, GREEN, AMBER, RED, VIOLET = "#3B82F6", "#22C55E", "#F59E0B", "#EF4444", "#8B5CF6"


def is_owner_account():
    email = str((social.me() or {}).get("email") or "").strip().lower()
    if not email:
        return False
    if email == LOCAL_TEST_OWNER and urlsplit(social.BASE).hostname in ("127.0.0.1", "localhost"):
        return True
    return hashlib.sha256(("owner-email:" + email).encode("utf-8")).hexdigest() == EMAIL_SHA


def span(secs):
    m = int((secs or 0) / 60)
    return "—" if m <= 0 else "%dm" % m if m < 60 else "%dh %dm" % (m // 60, m % 60)


def ago(ms):
    if not ms:
        return "never"
    s = max(0, int(time.time() - ms / 1000))
    return "now" if s < 90 else "%dm ago" % (s // 60) if s < 3600 else "%dh ago" % (s // 3600) if s < 86400 \
        else "%dd ago" % (s // 86400)


def _t(text, px=13, weight=QFont.Weight.Medium, color="rgba(255,255,255,180)", wrap=True):
    lab = QLabel(str(text))
    lab.setFont(look.font(px, weight))
    lab.setStyleSheet("color: %s; background: transparent;" % color)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.TextFormat.PlainText)      # people's names are data
    return lab


def _glass():
    f = QFrame()
    f.setObjectName("ownerGlass")
    f.setStyleSheet("#ownerGlass { background: rgba(255,255,255,20); border: 1px solid rgba(255,255,255,26);"
                    " border-radius: 18px; }")
    col = QVBoxLayout(f)
    col.setContentsMargins(16, 14, 16, 14)
    col.setSpacing(6)
    return f, col


class _Chart(QWidget):
    """A line or bars, with labels under them."""

    def __init__(self, values, labels, line, color):
        super().__init__()
        self.values, self.labels, self.line, self.color = values, labels, line, QColor(color)
        self.setFixedHeight(150)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 22
        for i in range(4):
            p.setPen(QColor(255, 255, 255, 16))
            p.drawLine(0, int(h * i / 3), w, int(h * i / 3))
        n = len(self.values)
        top = max(max(self.values, default=1), 1e-6)
        if self.line:
            path = QPainterPath()
            step = w / max(1, n - 1)
            for i, v in enumerate(self.values):
                pt = QPointF(i * step, h - v / top * h)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(self.color, 3))
            p.drawPath(path)
        else:
            slot = w / max(1, n)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color)
            for i, v in enumerate(self.values):
                bh = v / top * h
                p.drawRoundedRect(QRectF(i * slot + slot * 0.19, h - bh, slot * 0.62, bh), 4, 4)
        p.setFont(look.font(10))
        p.setPen(QColor(255, 255, 255, 170))
        skip = 2 if n > 7 else 1
        slot = w / max(1, n)
        for i, text in enumerate(self.labels):
            if i % skip == skip - 1:
                p.drawText(QRectF(i * slot - 10, h + 4, slot + 20, 18), Qt.AlignmentFlag.AlignCenter, text)


class _PinSheet(GlassSheet):
    """The PIN, asked for quietly: anything but the right one just closes."""

    def __init__(self, tab):
        super().__init__(tab, max_w=300, max_h=170)
        self.tab = tab
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setValidator(QIntValidator(0, 9999, self.edit))
        self.edit.setMaxLength(4)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setFont(look.font(28, QFont.Weight.Bold))
        self.edit.setStyleSheet("QLineEdit { color: white; background: transparent; border: none; }")
        self.edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.edit.textChanged.connect(self._typed)
        self.card_lay.addStretch(1)
        self.card_lay.addWidget(self.edit)
        self.card_lay.addStretch(1)

    def ask(self):
        self.edit.clear()
        self.open_sheet()
        self.edit.setFocus()

    def mousePressEvent(self, e):
        if not self.card.geometry().contains(e.position().toPoint()):
            self.edit.clear()
            self.hide()
        e.accept()

    def _typed(self, text):
        if len(text) == 4:
            self.edit.blockSignals(True)
            self.edit.clear()
            self.edit.blockSignals(False)
            self.hide()
            self.tab._owner_try(text)


class MusicOwner:
    OWNER_PAGES = ("owner",)

    def _init_owner(self):
        self._owner_pin = None
        self._owner_tab = "overview"
        self._owner_data = None
        self._owner_error = None
        self._owner_users = None
        self._owner_query = ""
        self._owner_sort = "seen"
        self._owner_only = False
        self._owner_limit = ROWS_AT_ONCE
        self._owner_sheet = _PinSheet(self)
        self._owner_timer = QTimer(self)
        self._owner_timer.setInterval(30000)
        self._owner_timer.timeout.connect(self._owner_tick)

    # ---- the door ----
    def owner_door(self):
        """Ten clicks on the maker's name: on anyone else's account, nothing at all."""
        if is_owner_account():
            self._owner_sheet.ask()

    def _owner_headers(self):
        return {"X-Owner-Pin": self._owner_pin} if self._owner_pin else {}

    def _owner_try(self, pin):
        if not is_owner_account():
            return

        def ok(_r):
            self._owner_pin = pin
            sheet = getattr(self, "_settings_sheet", None)
            if sheet is not None:
                sheet.hide()
            self.go("owner")
        self.hub.run(lambda: social.call("POST", "/v1/owner/unlock", headers={"X-Owner-Pin": pin}), ok, lambda _e: None)

    def _owner_leave(self):
        """Off the page: the PIN is forgotten."""
        self._owner_pin = None
        self._owner_data = self._owner_users = None
        self._owner_timer.stop()

    def _owner_tick(self):
        if not self._here or self._here[0] != "owner":
            self._owner_leave()
            return
        self._owner_refresh()

    # ---- the page ----
    def _build_owner(self):
        page = self._pages["owner"]
        page.clear()
        page.art_url = None
        if not self._owner_pin or not is_owner_account():
            self._owner_leave()
            QTimer.singleShot(0, lambda: self.go("home", push=False))
            return
        self._owner_box = QWidget()
        col = QVBoxLayout(self._owner_box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)
        self._owner_tabs = QHBoxLayout()
        self._owner_tabs.setSpacing(8)
        col.addLayout(self._owner_tabs)
        self._owner_body = QWidget()
        self._owner_col = QVBoxLayout(self._owner_body)
        self._owner_col.setContentsMargins(0, 0, 0, 0)
        self._owner_col.setSpacing(12)
        col.addWidget(self._owner_body)
        page.add(self._owner_box)
        self._owner_timer.start()
        self._owner_show_tabs()
        self._owner_refresh()

    def _owner_alive(self):
        return self._here and self._here[0] == "owner" and _alive(getattr(self, "_owner_box", None)) and self._owner_pin

    def _owner_show_tabs(self):
        while self._owner_tabs.count():
            w = self._owner_tabs.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for key, name in TABS:
            b = Pill(name, style="tab_on" if key == self._owner_tab else "glass")
            b.clicked.connect(lambda _c=False, k=key: self._owner_set_tab(k))
            self._owner_tabs.addWidget(b)
        self._owner_tabs.addStretch(1)
        b = Pill("Refresh", style="outline")
        b.clicked.connect(self._owner_refresh)
        self._owner_tabs.addWidget(b)

    def _owner_set_tab(self, key):
        self._owner_tab = key
        self._owner_show_tabs()
        self._owner_render()
        if key == "users":
            self._owner_load_users()

    def _owner_refresh(self):
        headers = self._owner_headers()

        def ok(d):
            if self._owner_alive():
                self._owner_data, self._owner_error = d, None
                self._owner_hero()
                if self._owner_tab != "users":
                    self._owner_render()

        def fail(e):
            if self._owner_alive():
                not_there = getattr(e, "status", 0) == 404 or "Not found" in str(e)
                self._owner_error = "This dashboard is only for the owner's account." if not_there else str(e)
                self._owner_hero()
                if self._owner_data is None:
                    self._owner_render()
        self._owner_hero()
        self.hub.run(lambda: social.call("GET", "/v1/owner/overview", headers=headers), ok, fail)
        if self._owner_tab == "users":
            self._owner_load_users()

    def _owner_hero(self):
        d = self._owner_data
        sub = "Live · synced %s" % time.strftime("%H:%M:%S", time.localtime(d["at"] / 1000)) if d and not \
            self._owner_error else (self._owner_error or "Connecting…")
        self._pages["owner"].hero.set_text("Owner", "Dashboard", sub)

    def _owner_render(self):
        self._clear_col(self._owner_col)
        d, col = self._owner_data, self._owner_col
        tab = self._owner_tab
        if tab == "users":
            self._owner_users_tab()
        elif d is None:
            col.addWidget(_t(self._owner_error or "Loading…", 14))
        elif tab == "overview":
            self._owner_overview(d)
        elif tab == "music":
            self._owner_ranked("Top songs · 7 days", d.get("topSongs") or [], False)
            self._owner_ranked("Top artists · 7 days", d.get("topArtists") or [], True)
            self._owner_ranked("Today", d.get("songsToday") or [], False)
        elif tab == "revenue":
            f, c = _glass()
            c.addWidget(_t("Revenue", 16, QFont.Weight.Bold, "white"))
            c.addWidget(_t("Awesome Music is free, with no premium plan or ads, so there's no revenue to show. Donations "
                           "go straight to UPI and PayPal, outside the app, and aren't counted here.", 13.5))
            col.addWidget(f)
        else:
            self._owner_system(d)

    # ---- overview ----
    def _owner_overview(self, d):
        c = d.get("cards") or {}
        cards = (("Total users", "totalUsers", "", None, BLUE), ("Active today", "activeToday", "", "activeTrend", GREEN),
                 ("Online now", "activeNow", "", None, GREEN), ("Active, 30 days", "activeMonth", "", None, BLUE),
                 ("Today vs 30 days", "stickiness", "%", None, BLUE), ("People listening today", "listenersToday", "", None, BLUE),
                 ("Listening, all time", "listenHoursAll", "h", None, BLUE), ("Listening today", "listenHoursToday", "h", "listenTrend", BLUE),
                 ("Songs played today", "songsToday", "", "songsTrend", BLUE), ("New users today", "newToday", "", "newTrend", VIOLET),
                 ("Avg listening / user", "avgSessionMin", "min", None, BLUE), ("Active this week", "activeWeek", "", None, GREEN),
                 ("Back next day", "returnNextDay", "%", None, AMBER), ("Still here after a week", "returnWeek", "%", None, VIOLET),
                 ("Crash rate today", "crashRate", "%", None, RED if (c.get("crashRate") or 0) > 2 else AMBER))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        rows = [(n, c.get(k), u, c.get(t) if t else None, tint) for n, k, u, t, tint in cards]
        rows.append(("Database", d.get("dbMs"), "ms", None, GREEN if (d.get("dbMs") or 0) < 300 else AMBER))
        for i, (name, value, unit, trend, tint) in enumerate(rows):
            f, col = _glass()
            col.addWidget(_t(name, 12, color=tint))
            shown = "—" if value is None else ("%s %s" % (format(value, ","), unit)).strip()
            col.addWidget(_t(shown, 24, QFont.Weight.Bold, "white"))
            if trend is not None:
                up = trend >= 0
                col.addWidget(_t(("▲ +" if up else "▼ ") + "%d%%" % trend, 11.5, QFont.Weight.DemiBold, GREEN if up else RED))
            grid.addWidget(f, i // 4, i % 4)
        self._owner_col.addWidget(holder)
        versions = c.get("versions") or []
        if versions:
            f, col = _glass()
            col.addWidget(_t("App versions in use", 12))
            col.addWidget(_t("  ·  ".join("%s (%d)" % (v.get("v"), v.get("n") or 0) for v in versions), 13,
                             QFont.Weight.DemiBold, "white"))
            self._owner_col.addWidget(f)
        f, col = _glass()
        col.addWidget(_t("Premium users: no premium plan yet", 12.5))
        self._owner_col.addWidget(f)
        s = d.get("series") or {}
        days = [str(x)[-2:] for x in s.get("days") or []]
        self._owner_chart("Daily active users · 14 days", s.get("dau") or [], days, True, GREEN)
        self._owner_chart("Listening time · hours", s.get("listen") or [], days, False, BLUE)
        self._owner_chart("New users", s.get("newUsers") or [], days, False, VIOLET)
        cs = d.get("countries") or []
        self._owner_chart("Top countries · 30 days", [x.get("n") or 0 for x in cs], [str(x.get("c")) for x in cs], False, AMBER)
        f, col = _glass()
        col.addWidget(_t("Insights", 16, QFont.Weight.Bold, "white"))
        col.addWidget(_t("Read from your numbers", 11.5))
        for line in d.get("insights") or []:
            col.addWidget(_t("•  " + str(line), 13.5, color="white"))
        self._owner_col.addWidget(f)
        f, col = _glass()
        col.addWidget(_t("Quick actions", 15, QFont.Weight.Bold, "white"))
        row = QHBoxLayout()
        for name, slot in (("Refresh analytics", self._owner_refresh),
                           ("Reload recommendations", lambda: (self.refresh_feed(force=True),
                                                               self.status.setText("Remaking your mixes..."))),
                           ("Clear cache", self._clear_cache)):
            b = Pill(name, style="glass")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        self._owner_col.addWidget(f)

    def _owner_chart(self, title, values, labels, line, color):
        f, col = _glass()
        row = QHBoxLayout()
        row.addWidget(_t(title, 14, QFont.Weight.DemiBold, "white"), 1)
        row.addWidget(_t(values[-1] if values else "—", 14, QFont.Weight.Bold, color))
        col.addLayout(row)
        if not any(values):
            col.addWidget(_t("Nothing yet.", 12.5))
        else:
            col.addWidget(_Chart(values, labels, line, color))
        self._owner_col.addWidget(f)

    # ---- users ----
    def _owner_users_tab(self):
        field = Field("Search a name or @username")
        field.setMaximumWidth(620)
        field.edit.setText(self._owner_query)
        self._owner_search = QTimer(field)
        self._owner_search.setSingleShot(True)
        self._owner_search.setInterval(350)
        self._owner_search.timeout.connect(self._owner_load_users)

        def typed(text):
            self._owner_query = text
            self._owner_search.start()
        field.edit.textEdited.connect(typed)
        self._owner_col.addWidget(field)
        self._owner_chips = QHBoxLayout()
        self._owner_chips.setSpacing(8)
        self._owner_col.addLayout(self._owner_chips)
        self._owner_rows = QWidget()
        self._owner_rows_col = QVBoxLayout(self._owner_rows)
        self._owner_rows_col.setContentsMargins(0, 0, 0, 0)
        self._owner_rows_col.setSpacing(10)
        self._owner_col.addWidget(self._owner_rows)
        self._owner_col.addWidget(_t("A song shows only for people who share their listening with friends.", 11.5))
        self._owner_fill_users()

    def _owner_load_users(self):
        headers, q = self._owner_headers(), quote(self._owner_query.strip())

        def ok(r):
            if self._owner_alive():
                users = r.get("users") or []
                changed = users != self._owner_users
                self._owner_users = users
                if changed and self._owner_tab == "users":
                    self._owner_fill_users()

        def fail(e):
            logger.debug("Couldn't read the accounts: %s", type(e).__name__)
        self.hub.run(lambda: social.call("GET", "/v1/owner/users?q=" + q, headers=headers), ok, fail)

    def _owner_choose(self, only=None, sort=None):
        self._owner_limit = ROWS_AT_ONCE
        if only is not None:
            self._owner_only = only
        if sort is not None:
            self._owner_sort = sort
        self._owner_fill_users()

    def _owner_fill_users(self):
        if not _alive(getattr(self, "_owner_rows", None)):
            return
        while self._owner_chips.count():
            w = self._owner_chips.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        everyone = self._owner_users
        all_ = everyone or []
        b = Pill("Online now (%d)" % sum(1 for u in all_ if u.get("online")), style="tab_on" if self._owner_only else "glass")
        b.clicked.connect(lambda: self._owner_choose(only=not self._owner_only))
        self._owner_chips.addWidget(b)
        for key, name in (("seen", "Last seen"), ("today", "Today"), ("week", "7 days"), ("total", "All time"), ("joined", "Newest")):
            b = Pill(name, style="tab_on" if self._owner_sort == key else "glass")
            b.clicked.connect(lambda _c=False, k=key: self._owner_choose(sort=k))
            self._owner_chips.addWidget(b)
        self._owner_chips.addStretch(1)
        self._clear_col(self._owner_rows_col)
        shown = sorted((u for u in all_ if u.get("online") or not self._owner_only),
                       key=lambda u: u.get(self._owner_sort) or 0, reverse=True)
        if everyone is None:
            self._owner_rows_col.addWidget(_t("Loading…"))
        elif not shown:
            self._owner_rows_col.addWidget(_t("Nobody online right now." if self._owner_only else "No one found."))
        for u in shown[:self._owner_limit]:          # a few at a time: each row is a dozen widgets
            self._owner_rows_col.addWidget(self._owner_user_row(u))
        if len(shown) > self._owner_limit:
            more = Pill("Show more (%d left)" % (len(shown) - self._owner_limit), style="glass")
            more.clicked.connect(self._owner_more)
            self._owner_rows_col.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)

    def _owner_more(self):
        self._owner_limit += ROWS_AT_ONCE
        self._owner_fill_users()

    def _owner_user_row(self, u):
        f, col = _glass()
        handle, name = u.get("handle") or "", u.get("name") or ""
        online = bool(u.get("online"))
        title = (name or ("@" + handle if handle else "No username yet")) + ("  🔒" if u.get("private") else "")
        head = QHBoxLayout()
        head.addWidget(_t("●", 10, color=GREEN if online else "rgba(255,255,255,56)"))
        head.addWidget(_t(title, 14.5, QFont.Weight.Bold, "white"), 1)
        col.addLayout(head)
        bits = ["@" + handle if handle and name else "", "online" if online else "seen " + ago(u.get("seen")),
                "%d active %s" % (u.get("days") or 0, "day" if u.get("days") == 1 else "days"), u.get("country") or "",
                "v" + u["version"] if u.get("version") else ""]
        col.addWidget(_t("  ·  ".join(b for b in bits if b), 11.5))
        playing = u.get("playing")
        if playing:
            col.addWidget(_t("▶  %s  ·  %s" % (playing.get("title"), playing.get("artist")), 12.5, QFont.Weight.DemiBold, GREEN))
        row = QHBoxLayout()
        for label, value in (("Today", span(u.get("today"))), ("7 days", span(u.get("week"))),
                             ("All time", span(u.get("total"))), ("Followers", u.get("followers") or 0)):
            box = QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(_t(value, 15, QFont.Weight.Bold, "white"))
            box.addWidget(_t(label, 11))
            row.addLayout(box, 1)
        col.addLayout(row)
        joined = time.strftime("%d %b %Y", time.localtime((u.get("joined") or 0) / 1000))
        col.addWidget(_t("Joined %s  ·  %s per active day  ·  following %d" % (joined, span(u.get("perDay")), u.get("following") or 0), 11))
        return f

    # ---- music ----
    def _owner_ranked(self, title, rows, round_):
        f, col = _glass()
        col.addWidget(_t(title, 15, QFont.Weight.Bold, "white"))
        if not rows:
            col.addWidget(_t("Nothing played yet."))
        top = max([r.get("plays") or 0 for r in rows] + [1])
        for i, r in enumerate(rows):
            row = QHBoxLayout()
            row.setSpacing(12)
            lab = _t(i + 1, 14, QFont.Weight.Bold, AMBER if i == 0 else "rgba(255,255,255,180)", wrap=False)
            lab.setFixedWidth(22)
            row.addWidget(lab)
            pic = QLabel()
            pic.setFixedSize(42, 42)
            pic.setStyleSheet("background: rgba(255,255,255,20); border-radius: %dpx;" % (21 if round_ else 8))
            url = r.get("art")
            if url:
                def got(pix, target=pic):
                    if _alive(target):
                        target.setPixmap(pix.scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                    Qt.TransformationMode.SmoothTransformation))
                art().want(url, got)
            row.addWidget(pic)
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(_t(r.get("title") or "", 14, QFont.Weight.DemiBold, "white", wrap=False))
            if not round_:
                words.addWidget(_t(r.get("artist") or "", 12, wrap=False))
            bar = QFrame()
            bar.setFixedHeight(3)
            bar.setFixedWidth(int(320 * (r.get("plays") or 0) / top))
            bar.setStyleSheet("background: %s; border-radius: 1px;" % BLUE)
            words.addWidget(bar)
            row.addLayout(words, 1)
            row.addWidget(_t(r.get("plays") or 0, 13, QFont.Weight.Bold, "white", wrap=False))
            col.addLayout(row)
        self._owner_col.addWidget(f)

    # ---- system ----
    def _owner_system(self, d):
        f, col = _glass()
        col.addWidget(_t("System", 15, QFont.Weight.Bold, "white"))
        note = _t("Database answered in %d ms" % (d.get("dbMs") or 0))
        col.addWidget(note)
        b = Pill("Database health", style="glass")
        headers = self._owner_headers()

        def health():
            base = "Database answered in %d ms · health check: " % (d.get("dbMs") or 0)

            def ok(r):
                if _alive(note):
                    note.setText(base + "ok, %d ms" % (r.get("ms") or 0))

            def fail(e):
                if _alive(note):
                    note.setText(base + str(e))
            self.hub.run(lambda: social.call("GET", "/v1/owner/health", headers=headers), ok, fail)
        b.clicked.connect(health)
        col.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        self._owner_col.addWidget(f)
        f, col = _glass()
        col.addWidget(_t("Recent crashes", 15, QFont.Weight.Bold, "white"))
        crashes = d.get("crashes") or []
        if not crashes:
            col.addWidget(_t("None reported."))
        for c in crashes:
            col.addWidget(_t("%s · %s · %s" % (c.get("version"), c.get("device"), ago(c.get("at"))), 11.5, QFont.Weight.DemiBold, AMBER))
            col.addWidget(_t(c.get("message") or "", 12))
        self._owner_col.addWidget(f)
        f, col = _glass()
        col.addWidget(_t("Send a notification", 15, QFont.Weight.Bold, "white"))
        col.addWidget(_t("Every signed-in phone shows it on its next check (within a few minutes while the app runs, "
                         "otherwise within ~15).", 12))
        self._owner_title = Field("Title")
        self._owner_text = Field("Message")
        col.addWidget(self._owner_title)
        col.addWidget(self._owner_text)
        self._owner_send_btn = Pill("Send to everyone", style="glass")
        self._owner_send_btn.clicked.connect(self._owner_announce)
        col.addWidget(self._owner_send_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._owner_col.addWidget(f)

    def _owner_confirm(self, title):
        return QMessageBox.question(self, "Send to everyone", "Send \"%s\" to every signed-in phone? It can't be taken "
                                    "back." % title) == QMessageBox.StandardButton.Yes

    def _owner_announce(self):
        title, text = self._owner_title.text().strip(), self._owner_text.text().strip()
        if not title or not text or not self._owner_confirm(title):
            return
        headers = self._owner_headers()
        btn, a, b = self._owner_send_btn, self._owner_title, self._owner_text

        def ok(_r):
            if _alive(btn):
                btn.setText("Sent ✓")
                a.edit.clear()
                b.edit.clear()

        def fail(e):
            if _alive(btn):
                btn.setText(str(e))
        self.hub.run(lambda: social.call("POST", "/v1/owner/announce", {"title": title, "body": text}, headers=headers), ok, fail)
