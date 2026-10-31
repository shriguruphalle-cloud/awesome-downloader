"""Awesome Music's people, on the PC: the same account as the phone app.

  Hub        -- you (signed in or not), who you follow and who follows you with
                what they're playing, your chats (unread marked), jams going on
                in them; refreshed every half-minute while signed in; tells
                friends what you're playing (when you share it).
  ChatLive   -- a chat open on screen, live: new messages arrive as they're sent.
  JamClient  -- listening together. Each PC or phone plays the song itself; the
                jam holds what's playing and where (a position at a moment of the
                server's clock). The server's clock is learnt from a few pings;
                twice a second the player is put into step (MusicTab.jam_follow);
                your play / pause / next / seek / add go to the jam instead.
  pages      -- the Friends page (signing in, search, friends, jams), someone's
                profile, Chats, one chat, and your account -- built from the
                Music tab's own parts (Hero, glass, pills, rows) so they look the
                rest of it.

The talking to the server is app.core.social; everything here that waits on it
runs on a worker thread and comes back to the UI thread through a signal.
"""
import json
import threading
import time

from PySide6.QtCore import QObject, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QAbstractSocket, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QSizePolicy, QVBoxLayout, QWidget)

from app.core import social
from app.logging_setup import get_logger

from . import music_look as look
from .browser_chrome import draw_icon
from .music_widgets import Pill, art, icon_button, thumb_url

logger = get_logger("music_social")

REFRESH_MS = 30_000


def _alive(obj):
    try:
        return obj is not None and obj.objectName() is not None
    except RuntimeError:
        return False


def when(ms):
    """'now', '5 min', '3 h', 'Mon', '12 Oct' -- how long ago, briefly."""
    if not ms:
        return ""
    s = max(0, time.time() - ms / 1000.0)
    if s < 60:
        return "now"
    if s < 3600:
        return "%d min" % (s // 60)
    if s < 86400:
        return "%d h" % (s // 3600)
    if s < 6 * 86400:
        return time.strftime("%a", time.localtime(ms / 1000.0))
    return time.strftime("%d %b", time.localtime(ms / 1000.0)).lstrip("0")


def label_of(person):
    return (person or {}).get("name") or ("@" + person["handle"] if (person or {}).get("handle") else "Someone")


# ====================================================================== the hub
class Hub(QObject):
    """You and your people, kept up to date while you're signed in."""
    changed = Signal()               # people / chats / jams
    account_changed = Signal()       # signed in or out, or your profile changed
    toast = Signal(str)
    _back = Signal(object, object, object, object)      # callback, result, error-callback, error
    _gone = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.people, self.chats, self.live = [], [], []
        self.error = None
        self._presence = None
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._back.connect(self._deliver)
        self._gone.connect(self._signed_out)
        social.on_signed_out(self._gone.emit)       # (may come from a worker thread)
        self._busy = False

    # ---- running things off the UI thread ----
    def run(self, fn, ok=None, fail=None):
        """fn() on a worker thread; ok(result) or fail(SocialError) back on this one."""
        def work():
            try:
                res = fn()
            except social.SocialError as e:
                self._back.emit(None, None, fail, e)
                return
            except Exception as e:   # noqa: BLE001
                logger.exception("A social call failed")
                self._back.emit(None, None, fail, social.SocialError(0, "Something went wrong: %s" % e))
                return
            self._back.emit(ok, res, None, None)
        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, ok, res, fail, err):
        if err is not None:
            if fail is not None:
                fail(err)
            else:
                self.toast.emit(str(err))
        elif ok is not None:
            ok(res)

    # ---- you ----
    @property
    def me(self):
        return social.me()

    def signed_in(self):
        return social.signed_in()

    def start(self):
        """The Music tab is up: if you're signed in, catch up and keep up."""
        if not social.signed_in():
            return
        self.run(social.refresh_me, lambda _u: self.account_changed.emit(), lambda _e: None)
        self.refresh()
        self._timer.start()

    def signed_in_now(self):
        self.account_changed.emit()
        self.refresh()
        self._timer.start()

    def _signed_out(self):
        self._timer.stop()
        self.people, self.chats, self.live = [], [], []
        self._presence = None
        self.account_changed.emit()
        self.changed.emit()

    def refresh(self):
        if not social.signed_in() or self._busy:
            return
        self._busy = True

        def fetch():
            chats = social.chats()
            people = social.friends()
            try:
                live = social.live_jams()
            except social.SocialError:
                live = None
            return chats, people, live

        def ok(res):
            self._busy = False
            self.chats, self.people = res[0], res[1]
            if res[2] is not None:
                self.live = res[2]
            self.error = None
            self.changed.emit()

        def fail(e):
            self._busy = False
            self.error = str(e)
            self.changed.emit()
        self.run(fetch, ok, fail)

    def unread(self):
        return sum(1 for c in self.chats if c.get("unread"))

    def playing(self, track):
        """A song started (or None: stopped) -- friends see it, when you share what you play."""
        me = social.me()
        if not me or not me.get("share_activity", True):
            return
        key = (track or {}).get("id") or ""
        if key == self._presence:
            return
        self._presence = key
        item = social.shareable(track) if track else None
        self.run(lambda: social.presence(item), lambda _r: None, lambda _e: None)


# ====================================================================== live connections
def _socket_request(path):
    req = QNetworkRequest(QUrl(social.socket_url(path)))
    tok = social.token()
    if tok:
        req.setRawHeader(b"Authorization", ("Bearer " + tok).encode("ascii"))
    return req


class ChatLive(QObject):
    """One chat, live while it's on screen."""
    message = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chat_id = None
        self._ws = None
        self._retries = 0
        self._ping = QTimer(self)
        self._ping.setInterval(25_000)
        self._ping.timeout.connect(lambda: self._ws is not None and self._ws.sendTextMessage("ping"))

    def open(self, chat_id):
        if chat_id == self.chat_id and self._ws is not None:
            return
        self.close()
        self.chat_id = chat_id
        self._connect()

    def _connect(self):
        ws = QWebSocket()
        ws.setParent(self)
        ws.textMessageReceived.connect(self._got)
        ws.connected.connect(lambda: setattr(self, "_retries", 0))
        ws.disconnected.connect(lambda w=ws: self._lost(w))
        self._ws = ws
        ws.open(_socket_request("/v1/chats/%s/live" % self.chat_id))
        self._ping.start()

    def _got(self, text):
        if text == "pong":
            return
        try:
            m = json.loads(text)
        except ValueError:
            return
        if m.get("t") == "message" and isinstance(m.get("message"), dict):
            self.message.emit(m["message"])

    def _lost(self, ws):
        if ws is not self._ws or self.chat_id is None:
            return
        wait = 1000 * (2 ** min(self._retries, 4))
        self._retries += 1
        cid = self.chat_id
        QTimer.singleShot(wait, lambda: self.chat_id == cid and self._ws is ws and self._connect())

    def close(self):
        self.chat_id = None
        self._ping.stop()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except RuntimeError:
                pass
            ws.deleteLater()


class JamClient(QObject):
    """Listening together: the jam's state, the server's clock, and the player kept in step with it."""
    state_changed = Signal(object)         # the jam's state (dict), or None when out of one
    status_changed = Signal(str)           # "" | connecting | live | reconnecting
    said = Signal(str)                     # words for the status line
    LEAD_IN = 500                          # ms between steps

    def __init__(self, hub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.state = None
        self.status = ""
        self.code = None
        self.follow_fn = None              # (queue, index, playing, target_ms) -> None : MusicTab.jam_follow
        self.here_fn = None                # () -> (queue, index, pos_ms, playing): what's playing here
        self._ws = None
        self._wanted = False
        self._offset = 0
        self._best = 10 ** 9
        self._synced = 0.0
        self._retries = 0
        self._load_from_here = False
        self._tick = QTimer(self)
        self._tick.setInterval(self.LEAD_IN)
        self._tick.timeout.connect(self._step)

    @property
    def active(self):
        return self._wanted and self.state is not None

    def is_host(self):
        me = social.me() or {}
        return bool(self.state) and self.state.get("host") == me.get("id")

    def server_now(self):
        return int(time.time() * 1000) + self._offset

    # ---- in and out ----
    def start(self, chat_id=None):
        def ok(jam):
            self._connect(jam["code"], load_from_here=True)
            if chat_id:
                self.hub.run(lambda: social.send_jam(chat_id, jam["code"]), lambda _m: self.hub.refresh(),
                             lambda _e: None)
            self.said.emit("Jam started — code %s. Friends can join it from the chat, or with the code." % jam["code"])
        self.hub.run(lambda: social.new_jam(chat_id), ok)

    def join(self, code):
        code = (code or "").strip().upper()

        def ok(_jam):
            self._connect(code, load_from_here=False)
            self.said.emit("You're in the jam (%s)." % code)
        self.hub.run(lambda: social.jam(code), ok)

    def leave(self):
        was = self._wanted
        self._wanted = False
        self.code = None
        self._tick.stop()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except RuntimeError:
                pass
            ws.deleteLater()
        self.state = None
        self._set_status("")
        self.state_changed.emit(None)
        if was:
            self.said.emit("You left the jam.")

    def _connect(self, code, load_from_here):
        if self._ws is not None:
            self.leave()
        self.code = code
        self._wanted = True
        self._best = 10 ** 9
        self._load_from_here = load_from_here
        self._open()
        self._tick.start()

    def _open(self):
        self._set_status("connecting" if not self._retries else "reconnecting")
        ws = QWebSocket()
        ws.setParent(self)
        ws.connected.connect(lambda w=ws: self._opened(w))
        ws.textMessageReceived.connect(self._got)
        ws.disconnected.connect(lambda w=ws: self._lost(w))
        self._ws = ws
        ws.open(_socket_request("/v1/jams/%s/live" % self.code))

    def _opened(self, ws):
        if ws is not self._ws:
            return
        self._retries = 0
        self._set_status("live")
        ws.sendTextMessage('{"t":"hello"}')
        self._pings()

    def _pings(self):
        for i in range(5):
            QTimer.singleShot(i * 160, lambda: self._ws is not None and self._ws.sendTextMessage(
                json.dumps({"t": "ping", "c": int(time.time() * 1000)})))

    def _lost(self, ws):
        if ws is not self._ws or not self._wanted:
            return
        err = ws.error()
        # the jam ended (or isn't yours): the upgrade was refused outright
        if self.state is None and err == QAbstractSocket.SocketError.ConnectionRefusedError and self._retries > 2:
            self.said.emit("That jam has ended.")
            self.leave()
            return
        self._set_status("reconnecting")
        wait = 1000 * (2 ** min(self._retries, 4))
        self._retries += 1
        code = self.code
        QTimer.singleShot(wait, lambda: self._wanted and self.code == code and self._ws is ws and self._open())

    def _got(self, text):
        try:
            m = json.loads(text)
        except ValueError:
            return
        t = m.get("t")
        if t == "pong":
            now = int(time.time() * 1000)
            c, s = int(m.get("c") or 0), int(m.get("s") or 0)
            rtt = now - c
            if 0 <= rtt <= self._best or time.monotonic() - self._synced > 60:
                self._best = rtt
                self._offset = s - (c + rtt // 2)
                self._synced = time.monotonic()
        elif t == "state" and isinstance(m.get("state"), dict):
            first = self.state is None
            self.state = m["state"]
            if first and self._load_from_here and self.is_host() and not self.state.get("queue"):
                self._load_from_here = False
                self._load_here()
            self.state_changed.emit(self.state)
            self._follow()
        elif t == "error":
            self.said.emit(m.get("message") or "The jam said no.")

    def _set_status(self, s):
        if s != self.status:
            self.status = s
            self.status_changed.emit(s)

    def _step(self):
        if not self._wanted:
            self._tick.stop()
            return
        self._follow()
        if time.monotonic() - self._synced > 30 and self._ws is not None:
            self._pings()

    def target(self):
        """Where the jam's song is now (ms), by the server's clock."""
        st = self.state
        if not st:
            return 0
        return int(st.get("pos") or 0) + (self.server_now() - int(st.get("at") or 0) if st.get("playing") else 0)

    def _follow(self):
        st = self.state
        if not st or not self._wanted or self.follow_fn is None:
            return
        self.follow_fn(st.get("queue") or [], int(st.get("index") or 0), bool(st.get("playing")), self.target())

    # ---- changes, sent to the jam ----
    def op(self, name, **kw):
        if self._ws is None:
            return
        msg = {"t": "op", "op": name}
        msg.update(kw)
        self._ws.sendTextMessage(json.dumps(msg))

    def _load_here(self):
        if self.here_fn is None:
            return
        queue, index, pos, playing = self.here_fn()
        if queue:
            self.op("load", queue=[social.shareable(t) for t in queue[:200]], index=max(0, index), pos=int(pos),
                    playing=bool(playing))

    def add(self, track, next_=False):
        self.op("add", item=social.shareable(track), next=bool(next_))
        self.said.emit(("Plays next in the jam: %s" if next_ else "Added to the jam: %s") % track.get("title", ""))

    def play_list(self, tracks, start):
        songs = [social.shareable(t) for t in tracks if t.get("kind", "track") == "track"][:200]
        if not songs:
            return
        start = max(0, min(start, len(songs) - 1))
        if self.is_host():
            self.op("load", queue=songs, index=start, pos=0, playing=True)
        else:
            self.op("add", items=songs[start:start + 50], next=True)
            self.op("next")
        self.said.emit("Playing %s in the jam." % (songs[start]["title"] if len(songs) == 1
                                                     else "%d songs" % len(songs[start:start + 50])))


# ====================================================================== little parts
class Avatar(QWidget):
    """Someone's picture in a circle -- or their initial on a soft colour of its own."""

    def __init__(self, person, size=44, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pix = None
        self.set_person(person)

    def set_person(self, person):
        self.person = person or {}
        self._pix = None
        url = social.avatar_url(self.person)
        if url:
            art().want(url, self._got)
        self.update()

    def _got(self, pm):
        if _alive(self):
            self._pix = pm
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addEllipse(r)
        p.setClipPath(clip)
        if self._pix is not None and not self._pix.isNull():
            pm = self._pix
            s = min(pm.width(), pm.height())
            p.drawPixmap(r, pm, QRectF((pm.width() - s) / 2, (pm.height() - s) / 2, s, s))
        else:
            seed = sum(ord(c) for c in (self.person.get("id") or "x"))
            hue = (seed * 47) % 360
            p.fillRect(r, QColor.fromHsl(hue, 120, 92))
            p.fillRect(r, QColor(0, 0, 0, 40))
            p.setPen(QColor(255, 255, 255, 235))
            p.setFont(look.font(self.width() * 0.42, QFont.Weight.Bold))
            name = label_of(self.person).lstrip("@")
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), (name[:1] or "?").upper())
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.end()


class Glass(QWidget):
    """A pane of glass to hold a group of things (the rail's cards, on the page)."""

    def __init__(self, radius=18, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(18, 16, 18, 16)
        self.col.setSpacing(10)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g_top, g_bot = QColor(255, 255, 255, 20), QColor(255, 255, 255, 9)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, g_top)
        g.setColorAt(1, g_bot)
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawRoundedRect(r, self.radius, self.radius)
        p.end()


class Field(QWidget):
    """A line to type in, on glass, its edge lit while you type (as the rail's search)."""

    def __init__(self, placeholder="", height=42, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMinimumWidth(200)
        self.edit = QLineEdit(self)
        self.edit.setObjectName("socialField")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFont(look.font(13.5))
        self.edit.setFrame(False)
        self.edit.setStyleSheet("QLineEdit#socialField { background: transparent; border: none; color: #ffffff;"
                                " selection-background-color: rgba(125,211,252,110); }")
        self.edit.installEventFilter(self)
        self._focus = False

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._focus = ev.type() == QEvent.Type.FocusIn
            self.update()
        return False

    def resizeEvent(self, e):
        self.edit.setGeometry(16, 0, self.width() - 32, self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(255, 255, 255, 22 if self._focus else 14))
        p.setPen(QPen(QColor(125, 211, 252, 150) if self._focus else QColor(255, 255, 255, 34), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()

    def text(self):
        return self.edit.text()


def _label(text, px=13.5, weight=QFont.Weight.Normal, color=None, wrap=False):
    lb = QLabel(text)
    lb.setFont(look.font(px, weight))
    c = color or look.TEXT_2
    lb.setStyleSheet("color: rgba(%d,%d,%d,%d); background: transparent;" % (c.red(), c.green(), c.blue(), c.alpha()))
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return lb


class _Clickable(QAbstractButton):
    """A row that answers a click, lit under the cursor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paint_back(self, p):
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 16))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)


class PersonRow(_Clickable):
    """Someone: picture, name over @handle (or what they're playing), and a button."""
    play = Signal(object)

    def __init__(self, person, button=None, parent=None):
        super().__init__(parent)
        self.person = person
        self.setFixedHeight(64)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(12)
        row.addWidget(Avatar(person, 44))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = _label(label_of(person), 14, QFont.Weight.DemiBold, look.TEXT)
        col.addWidget(name)
        now = (person.get("now") or {}).get("track")
        if now:
            line = NowLine(now)
            line.clicked.connect(lambda: self.play.emit(now))
            col.addWidget(line)
        else:
            rel = "Friends" if person.get("following") and person.get("follows_you") else (
                "Follows you" if person.get("follows_you") else ("Following" if person.get("following") else ""))
            sub = "@%s" % person.get("handle") if person.get("handle") else ""
            col.addWidget(_label(" · ".join(x for x in (sub, rel) if x), 12, color=look.TEXT_3))
        row.addLayout(col, 1)
        if button is not None:
            row.addWidget(button)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_back(p)
        p.end()


class NowLine(_Clickable):
    """▶ the song they're playing -- click to play it too."""

    def __init__(self, track, parent=None):
        super().__init__(parent)
        self.track = track
        self.setFixedHeight(18)
        self.setToolTip("Play it too")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_icon(p, "note", QRectF(0, 2, 14, 14), QColor(125, 211, 252, 230))
        f = look.font(12, QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(look.TEXT if self._hover else look.TEXT_2)
        text = self.track.get("title", "") + ("  —  " + self.track["artist"] if self.track.get("artist") else "")
        text = QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, self.width() - 20)
        p.drawText(QRectF(19, 0, self.width() - 19, self.height()), int(Qt.AlignmentFlag.AlignVCenter), text)
        p.end()


class ChatRow(_Clickable):
    """A conversation: picture(s), its name, the last thing said, when; a dot while unread."""

    def __init__(self, chat, me_id, parent=None):
        super().__init__(parent)
        self.chat = chat
        self.setFixedHeight(66)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 14, 8)
        row.setSpacing(12)
        others = [m for m in chat.get("members") or [] if m.get("id") != me_id]
        row.addWidget(Avatar(others[0] if others else {"id": chat.get("id"), "name": chat.get("title")}, 46))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(chat.get("title") or "Chat", 14, QFont.Weight.Bold if chat.get("unread")
                             else QFont.Weight.DemiBold, look.TEXT))
        last = chat.get("last_text") or ("A group of %d" % len(chat.get("members") or []) if chat.get("kind") == "group"
                                         else "Say hello")
        if chat.get("last_by") == me_id and chat.get("last_text"):
            last = "You: " + last
        ll = _label(last, 12.5, QFont.Weight.Medium if chat.get("unread") else QFont.Weight.Normal,
                    look.TEXT if chat.get("unread") else look.TEXT_3)
        ll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(ll)
        row.addLayout(col, 1)
        right = QVBoxLayout()
        right.addWidget(_label(when(chat.get("last_at")), 11.5, color=look.TEXT_3), 0, Qt.AlignmentFlag.AlignRight)
        self._dot = bool(chat.get("unread"))
        right.addStretch(1)
        row.addLayout(right)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_back(p)
        if self._dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(125, 211, 252))
            p.drawEllipse(QRectF(self.width() - 22, self.height() - 24, 9, 9))
        p.end()


class Bubble(QWidget):
    """One message: words in a bubble (yours on the right, in the page's colour), or a song as a card
    that plays when clicked, or an invitation to a jam."""
    play = Signal(object)
    join = Signal(str)

    def __init__(self, msg, mine, sender=None, accent=None, parent=None):
        super().__init__(parent)
        self.msg, self.mine = msg, mine
        self.accent = QColor(accent or QColor(125, 211, 252))
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        self.card = QWidget()
        self.card.installEventFilter(self)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(14, 9, 14, 10)
        col.setSpacing(4)
        if sender and not mine:
            col.addWidget(_label(sender, 11.5, QFont.Weight.DemiBold, QColor(125, 211, 252, 230)))
        kind = msg.get("kind")
        item = msg.get("item") or {}
        if kind == "item" and item:
            col.addWidget(SongCard(item, self))
            if msg.get("text"):
                col.addWidget(_label(msg["text"], 13.5, color=look.TEXT, wrap=True))
        elif kind == "jam":
            col.addWidget(_label("Started a jam — listen together", 13.5, QFont.Weight.DemiBold, look.TEXT))
            code = item.get("id", "")
            jb = Pill("Join the jam" if not mine else "Code %s" % code, style="glass")
            jb.setEnabled(not mine)
            jb.clicked.connect(lambda: self.join.emit(code))
            col.addWidget(jb, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            t = _label(msg.get("text", ""), 13.5, color=look.TEXT, wrap=True)
            t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            t.setMaximumWidth(460)
            col.addWidget(t)
        tm = _label(time.strftime("%H:%M", time.localtime((msg.get("at") or 0) / 1000.0)), 10.5, color=look.TEXT_3)
        col.addWidget(tm, 0, Qt.AlignmentFlag.AlignRight)
        self.card.setMaximumWidth(500)
        if mine:
            outer.addStretch(1)
            outer.addWidget(self.card)
        else:
            outer.addWidget(self.card)
            outer.addStretch(1)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.card and ev.type() == QEvent.Type.Paint:
            p = QPainter(self.card)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.card.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            if self.mine:
                c = QColor(self.accent)
                c.setAlpha(70)
                p.setBrush(c)
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            else:
                p.setBrush(QColor(255, 255, 255, 20))
                p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.drawRoundedRect(r, 16, 16)
            p.end()
        return False


class SongCard(_Clickable):
    """A shared song (or album, artist, playlist): its cover, title over artist, a play mark."""

    def __init__(self, item, bubble, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedHeight(64)
        self.setMinimumWidth(280)
        self._pix = None
        if item.get("artwork"):
            art().want(thumb_url(item["artwork"], 120), self._got)
        self.clicked.connect(lambda: bubble.play.emit(item))
        self.setToolTip("Play" if item.get("kind", "track") == "track" else "Open")

    def _got(self, pm):
        if _alive(self):
            self._pix = pm
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.paint_back(p)
        cover = QRectF(6, 6, 52, 52)
        path = QPainterPath()
        path.addRoundedRect(cover, 8, 8)
        p.save()
        p.setClipPath(path)
        if self._pix is not None and not self._pix.isNull():
            pm = self._pix
            s = min(pm.width(), pm.height())
            p.drawPixmap(cover, pm, QRectF((pm.width() - s) / 2, (pm.height() - s) / 2, s, s))
        else:
            p.fillRect(cover, QColor(255, 255, 255, 30))
            draw_icon(p, "note", cover.adjusted(14, 14, -14, -14), look.TEXT_2)
        p.restore()
        x = cover.right() + 12
        w = self.width() - x - 40
        f1, f2 = look.font(13.5, QFont.Weight.DemiBold), look.font(12)
        p.setFont(f1)
        p.setPen(look.TEXT)
        p.drawText(QRectF(x, 10, w, 22), int(Qt.AlignmentFlag.AlignVCenter),
                   QFontMetricsF(f1).elidedText(self.item.get("title", ""), Qt.TextElideMode.ElideRight, w))
        kind = self.item.get("kind", "track")
        sub = self.item.get("artist") or {"album": "Album", "artist": "Artist", "playlist": "Playlist",
                                          "mix": "Playlist", "ytplaylist": "Playlist"}.get(kind, "")
        p.setFont(f2)
        p.setPen(look.TEXT_2)
        p.drawText(QRectF(x, 32, w, 20), int(Qt.AlignmentFlag.AlignVCenter),
                   QFontMetricsF(f2).elidedText(sub, Qt.TextElideMode.ElideRight, w))
        draw_icon(p, "play", QRectF(self.width() - 32, 22, 20, 20), look.TEXT if self._hover else look.TEXT_2)
        p.end()


class JamChip(_Clickable):
    """In the corner while you're in a jam: its code, how many are in it, live or reconnecting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.text = ""
        self.live = True
        self.hide()

    def set_state(self, state, status):
        if not state:
            self.hide()
            return
        n = len(state.get("members") or [])
        self.live = status == "live"
        self.text = "Jam · %s · %d %s" % (state.get("code", ""), n, "person" if n == 1 else "people")
        if not self.live:
            self.text += " · reconnecting"
        f = look.font(12.5, QFont.Weight.DemiBold)
        self.setFixedWidth(int(QFontMetricsF(f).horizontalAdvance(self.text) + 52))
        self.show()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(0, 0, 0, 110 if not self._hover else 140))
        p.setPen(QPen(QColor(125, 211, 252, 170) if self.live else QColor(255, 255, 255, 60), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        draw_icon(p, "jam", QRectF(12, 8, 18, 18), QColor(125, 211, 252) if self.live else look.TEXT_2)
        p.setFont(look.font(12.5, QFont.Weight.DemiBold))
        p.setPen(look.TEXT)
        p.drawText(QRectF(36, 0, self.width() - 40, self.height()), int(Qt.AlignmentFlag.AlignVCenter), self.text)
        p.end()


# ====================================================================== pages
class SignInCard(Glass):
    """Signing in: your email, then the 4-digit code sent to it; a new account picks a username."""
    done = Signal()

    def __init__(self, hub, parent=None):
        super().__init__(22, parent)
        self.hub = hub
        self.email = ""
        self.setMaximumWidth(560)
        self.col.setContentsMargins(28, 26, 28, 26)
        self.col.setSpacing(12)
        self.title = _label("", 22, QFont.Weight.Bold, look.TEXT)
        self.sub = _label("", 13.5, wrap=True)
        self.field = Field()
        self.field.edit.returnPressed.connect(self._go)
        self.msg = _label("", 12.5, color=QColor(252, 165, 165), wrap=True)
        self.msg.hide()
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.go_btn = Pill("Send code", style="solid")
        self.go_btn.clicked.connect(self._go)
        self.alt_btn = Pill("Use another email", style="glass")
        self.alt_btn.clicked.connect(self._step_email)
        self.again_btn = Pill("Send again", style="glass")
        self.again_btn.clicked.connect(self._resend)
        buttons.addWidget(self.go_btn)
        buttons.addWidget(self.again_btn)
        buttons.addWidget(self.alt_btn)
        buttons.addStretch(1)
        self.name_field = Field("Your name (as friends see it)")
        self.name_field.hide()
        for w in (self.title, self.sub, self.field, self.name_field, self.msg):
            self.col.addWidget(w)
        self.col.addLayout(buttons)
        self._step = "email"
        self._wait = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._count)
        self._step_email()

    def _say(self, text):
        self.msg.setText(text)
        self.msg.setVisible(bool(text))

    def _step_email(self):
        self._step = "email"
        self.title.setText("Sign in to Awesome Music")
        self.sub.setText("One account on your phone and this PC: follow friends, see what they're playing, "
                         "share songs in chats and listen together in jams. We'll email you a 4-digit code — "
                         "no password.")
        self.field.edit.setPlaceholderText("Your email")
        self.field.edit.setMaxLength(254)
        self.field.edit.setText(self.email)
        self.go_btn.setText("Send code")
        self.alt_btn.hide()
        self.again_btn.hide()
        self.name_field.hide()
        self._say("")
        self.field.edit.setFocus()

    def _step_code(self):
        self._step = "code"
        self.title.setText("Check your email")
        self.sub.setText("We sent a 4-digit code to %s. It works for 10 minutes." % self.email)
        self.field.edit.clear()
        self.field.edit.setPlaceholderText("The 4-digit code")
        self.field.edit.setMaxLength(4)
        self.go_btn.setText("Sign in")
        self.alt_btn.show()
        self.again_btn.show()
        self._wait = 60
        self._count()
        self._timer.start()
        self.field.edit.setFocus()

    def _step_name(self):
        self._step = "name"
        self.title.setText("Pick a username")
        self.sub.setText("It's how friends find you: 3–20 letters, digits, dots or underscores.")
        self.field.edit.clear()
        self.field.edit.setMaxLength(21)
        self.field.edit.setPlaceholderText("@username")
        self.name_field.show()
        self.go_btn.setText("Done")
        self.alt_btn.hide()
        self.again_btn.hide()
        self._timer.stop()
        self.field.edit.setFocus()

    def _count(self):
        self._wait -= 1
        if self._wait > 0:
            self.again_btn.setText("Send again (%d s)" % self._wait)
            self.again_btn.setEnabled(False)
        else:
            self.again_btn.setText("Send again")
            self.again_btn.setEnabled(True)
            self._timer.stop()
        self.again_btn.adjustSize()

    def _busy(self, on):
        self.go_btn.setEnabled(not on)
        self.field.edit.setEnabled(not on)

    def _go(self):
        text = self.field.text().strip()
        if self._step == "email":
            if not text:
                return
            self.email = text
            self._busy(True)
            self._say("")

            def ok(_r):
                self._busy(False)
                self._step_code()

            def fail(e):
                self._busy(False)
                if e.extra.get("retry_in"):      # a code went out just now: type that one
                    self._step_code()
                    self._wait = int(e.extra["retry_in"])
                self._say(str(e))
            self.hub.run(lambda: social.start(self.email), ok, fail)
        elif self._step == "code":
            if len(text) != 4 or not text.isdigit():
                self._say("The code is the 4 digits in the email.")
                return
            self._busy(True)

            def ok(new):
                self._busy(False)
                if new or not (social.me() or {}).get("handle"):
                    # a new account picks its username first; the app hears of the sign-in after that (it
                    # rebuilds the page it's on, which would take this card away mid-step)
                    self._step_name()
                else:
                    self.hub.signed_in_now()
                    self.done.emit()

            def fail(e):
                self._busy(False)
                self.field.edit.selectAll()
                self._say(str(e))
            self.hub.run(lambda: social.verify(self.email, text), ok, fail)
        else:
            handle = text.lstrip("@")
            name = self.name_field.text().strip()
            self._busy(True)

            def ok(_u):
                self._busy(False)
                self.done.emit()
                self.hub.signed_in_now()

            def fail(e):
                self._busy(False)
                self._say(str(e))
            self.hub.run(lambda: social.update_me(handle=handle, name=name), ok, fail)

    def _resend(self):
        self._busy(True)

        def ok(_r):
            self._busy(False)
            self._say("A new code is on its way.")
            self._wait = 60
            self._timer.start()

        def fail(e):
            self._busy(False)
            self._say(str(e))
        self.hub.run(lambda: social.start(self.email), ok, fail)


class Compose(Glass):
    """Writing in a chat: words, the song that's playing, a jam."""
    send = Signal(str)
    share_now = Signal()
    start_jam = Signal()

    def __init__(self, parent=None):
        super().__init__(24, parent)
        self.col.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.share_btn = icon_button("note", "Share the song that's playing", 38, 18)
        self.share_btn.clicked.connect(self.share_now.emit)
        self.jam_btn = icon_button("jam", "Start a jam here — listen together", 38, 18)
        self.jam_btn.clicked.connect(self.start_jam.emit)
        self.field = Field("Message", 40)
        self.field.edit.setMaxLength(2000)
        self.field.edit.returnPressed.connect(self._send)
        self.send_btn = icon_button("send", "Send (Enter)", 38, 18)
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.share_btn)
        row.addWidget(self.jam_btn)
        row.addWidget(self.field, 1)
        row.addWidget(self.send_btn)
        self.col.addLayout(row)

    def _send(self):
        t = self.field.text().strip()
        if t:
            self.field.edit.clear()
            self.send.emit(t)


def jpeg_avatar(path):
    """A picture file as the profile picture the server takes: square, at most 256 px, a JPEG under 200 KB."""
    img = QImage(path)
    if img.isNull():
        return None
    s = min(img.width(), img.height())
    img = img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s).scaled(
        QSize(256, 256), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    for q in (88, 75, 60, 45):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", q)
        buf.close()
        if data.size() <= 200 * 1024:
            return bytes(data)
    return None


def pick_avatar(parent, hub, done):
    path, _ = QFileDialog.getOpenFileName(parent, "Choose a profile picture", "",
                                          "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)")
    if not path:
        return
    data = jpeg_avatar(path)
    if data is None:
        QMessageBox.warning(parent, "Profile picture", "That picture couldn't be read.")
        return
    hub.run(lambda: social.set_picture(data), lambda _u: (hub.account_changed.emit(), done()))
