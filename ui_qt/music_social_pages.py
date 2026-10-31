"""The Music tab's side of Awesome Music's people (mixed into MusicTab): the
Friends page (signing in, finding people, jams going on), someone's profile,
Chats and one chat, your account -- and the player kept in step with a jam.

The parts and the talking to the server are in music_social; this puts them on
the tab's pages, which look the rest of the tab (Hero, glass, pills, rows).
"""
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QVBoxLayout, QWidget

from app.core import social

from . import music_look as look
from . import theme
from .browser_chrome import style_menu
from .music_social import (Avatar, Bubble, ChatLive, ChatRow, Compose, Field, Glass, Hub, JamChip, JamClient,
                           PersonRow, SignInCard, SongCard, _alive, _label, label_of, pick_avatar, when)
from .music_widgets import Pill


def _empty(col):
    while col.count():
        w = col.takeAt(0).widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


class _Relay(QObject):
    """A song card's click, passed on (cards on a page outside a chat bubble)."""
    play = Signal(object)


class SocialPages:
    """Mixed into MusicTab. Uses the tab's _pages, _here, go(), play_from(), current(), queue / index /
    player / _started, status, rail and the corner row."""

    SOCIAL_PAGES = ("friends", "profile", "people", "chats", "chat", "account")

    def _init_social(self, corner_row):
        self.hub = Hub(self)
        self.hub.toast.connect(self.status.setText)
        self.hub.changed.connect(self._social_changed)
        self.hub.account_changed.connect(self._social_account)
        self.hub.library_changed.connect(self._library_arrived)
        self.hub.notify.connect(self._tell)
        self.hub.taste = self.taste
        self.hub.is_playing = self._playing
        self.chat_live = ChatLive(self)
        self.chat_live.message.connect(self._chat_message)
        self.chat_live.edited.connect(self._chat_edited)
        self.chat_live.unsent.connect(self._chat_unsent)
        self._bubbles = {}
        self._compose = None
        self._seen_label = None
        self._tray = None
        self._tray_open = None
        QApplication.instance().aboutToQuit.connect(self.hub.flush_listening)
        self.jam = JamClient(self.hub, self)
        self.jam.follow_fn = self.jam_follow
        self.jam.here_fn = lambda: (list(self.queue), self.index,
                                    self.player.position() if self._started else 0, self._playing())
        self.jam.said.connect(self.status.setText)
        self.jam_chip = JamChip()
        self.jam_chip.setToolTip("You're in a jam — click for its code, the queue lock, or to leave")
        self.jam_chip.clicked.connect(self._jam_menu)
        corner_row.addWidget(self.jam_chip)
        self.jam.state_changed.connect(lambda st: self._jam_shown(st))
        self.jam.status_changed.connect(lambda _s: self._jam_shown(self.jam.state))
        self._chat_open = None
        self._chat_box = None
        self._chat_seen = set()
        self._chat_members = {}
        self._social_lists = {}
        self.hub.synced.connect(self._show_account_row)
        self._show_account_row()
        # "Synced 3 min" stays true without a sync to say so
        self._account_timer = QTimer(self)
        self._account_timer.setInterval(60_000)
        self._account_timer.timeout.connect(self._show_account_row)
        self._account_timer.start()
        self.hub.start()

    # ---- the phone's library arrived ----
    def _library_arrived(self):
        self.status.setText("Your library is in step with your phone.")
        self._sync_saved()
        self._sync_rail()
        here = self._here[0] if self._here else None
        if here in ("songs", "artists", "albums", "playlist"):
            self.go(here, self._here[1], push=False)

    # ---- telling you: a banner while you're here, a Windows notification while you're away ----
    def _tell(self, title, text, target):
        if not bool(self.settings.get("music_notifications", True)) if self.settings is not None else False:
            return
        win = self.window()
        here = win.isActiveWindow() and self.isVisible()
        if here:
            if target and target[0] == "chat" and self._chat_open == target[1].get("id"):
                return
            self.status.setText("%s — %s" % (title, text))
            return
        self._tray_message(title, text, target)

    def _tray_message(self, title, text, target):
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._tray is None:
            self._tray = QSystemTrayIcon(self.window().windowIcon(), self)
            self._tray.setToolTip("Awesome Music")
            self._tray.messageClicked.connect(self._tray_clicked)
            self._tray.show()
        self._tray_open = target
        self._tray.showMessage(title, text or title, QSystemTrayIcon.MessageIcon.NoIcon, 7000)

    def _tray_clicked(self):
        win = self.window()
        win.showNormal() if win.isMinimized() else win.show()
        win.raise_()
        win.activateWindow()
        target, self._tray_open = self._tray_open, None
        stack = getattr(win, "tabs", None)
        if stack is not None and stack.indexOf(self) >= 0:
            stack.setCurrentWidget(self)
        if target and target[0] == "chat":
            self.go("chat", {"id": target[1]["id"], "title": target[1].get("title")})
        elif target and target[0] == "profile" and target[1]:
            self.go("profile", {"handle": target[1]})

    def _social_page_left(self, key):
        """go() is opening `key`: a chat that's no longer on screen stops being live."""
        if key != "chat" and getattr(self, "chat_live", None) is not None:      # (the first page opens before it's made)
            self._chat_open = None
            self.chat_live.close()

    # ---- refreshed ----
    def _social_changed(self):
        n = self.hub.unread()
        if "chats" in self.rail.items:
            self.rail.items["chats"].set_count(n or "")
            self.rail.items["social"].set_count(n or "")
        here = self._here[0] if self._here else None
        if here == "friends":
            self._fill_friends()
        elif here == "chats":
            self._fill_chats()
        elif here == "chat":
            self._show_seen()

    def _show_account_row(self):
        """The rail's account row: an invitation to sign in, or who you are and when the library last synced."""
        hb = self.hub
        me = hb.me if hb.signed_in() else None
        if not me:
            self.rail.account_item.set_lines("Sign in", "Sync with your phone")
            return
        if hb.sync_error:
            sub = "Couldn't sync — open to retry"
        elif hb.synced_at:
            sub = "Synced %s" % when(int(hb.synced_at * 1000))
        else:
            sub = "Syncing with your phone…"
        self.rail.account_item.set_lines(label_of(me) if (me.get("name") or me.get("handle")) else me.get("email", "You"),
                                         sub)

    def _social_account(self):
        self._show_account_row()
        here = self._here[0] if self._here else None
        if here in ("friends", "chats", "account"):
            self.go(here, None, push=False)

    def _jam_shown(self, st):
        self.jam_chip.set_state(st, self.jam.status)
        self._corner.resize(self._corner.sizeHint())

    def _need_sign_in(self, page, what):
        """Signed out: the page is the sign-in card."""
        page.hero.set_text("Awesome Music", what, "Sign in with the email you use on your phone: your likes, playlists "
                                                  "and history come over, and stay the same on both.")
        card = SignInCard(self.hub)
        card.done.connect(lambda: self.go(self._here[0] if self._here else "friends", None, push=False))
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(card)
        lay.addStretch(1)
        page.add(holder)
        QTimer.singleShot(0, card.field.edit.setFocus)

    # ---- Friends ----
    def _build_friends(self):
        page = self._pages["friends"]
        page.clear()
        page.art_url = None
        if not self.hub.signed_in():
            self._need_sign_in(page, "Friends")
            return
        me = self.hub.me or {}
        page.hero.set_text("Together", "Friends",
                           "Follow people to see what they're playing, share songs and listen together.")
        prof = Pill("Your profile", style="glass")
        prof.clicked.connect(lambda: self.go("account"))
        page.hero.add_action(prof)
        # find people
        find = Field("Find people by @username or name")
        page.add(find, "Find people")
        results = QWidget()
        rcol = QVBoxLayout(results)
        rcol.setContentsMargins(0, 0, 0, 0)
        rcol.setSpacing(2)
        page.add(results, gap=4)
        wait = QTimer(find)
        wait.setSingleShot(True)
        wait.setInterval(350)

        def search():
            q = find.text().strip()
            if len(q.lstrip("@")) < 2:
                self._fill_people(rcol, [], "")
                return
            self.hub.run(lambda: social.search_people(q),
                         lambda found, q=q: _alive(results) and find.text().strip() == q
                         and self._fill_people(rcol, found, "No one by that name yet."))
        wait.timeout.connect(search)
        find.edit.textChanged.connect(lambda _t: wait.start())
        # listening together
        jam_row = QWidget()
        jl = QHBoxLayout(jam_row)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.setSpacing(10)
        code = Field("A jam's 6-letter code")
        code.setMaximumWidth(300)
        code.edit.setMaxLength(6)
        join = Pill("Join", style="glass")
        join.clicked.connect(lambda: len(code.text().strip()) == 6 and self.jam.join(code.text()))
        code.edit.returnPressed.connect(join.click)
        start = Pill("Start a jam", style="glass")
        start.setToolTip("Listen together: everyone in the jam hears the same song at the same moment")
        start.clicked.connect(lambda: self.jam.start())
        jl.addWidget(code)
        jl.addWidget(join)
        jl.addWidget(start)
        jl.addStretch(1)
        page.add(jam_row, "Listen together")
        live = QWidget()
        self._social_lists["jams"] = QVBoxLayout(live)
        self._social_lists["jams"].setContentsMargins(0, 0, 0, 0)
        page.add(live, gap=4)
        people = QWidget()
        self._social_lists["people"] = QVBoxLayout(people)
        self._social_lists["people"].setContentsMargins(0, 0, 0, 0)
        self._social_lists["people"].setSpacing(2)
        page.add(people, "Your people")
        self._fill_friends()
        self.hub.refresh()
        self._add_top_listener(page)
        self._add_suggestions(page)
        if me and not me.get("handle"):
            self.status.setText("Pick a username in Your profile so friends can find you.")

    @staticmethod
    def _why(why):
        """The words for why someone is suggested."""
        why = why or ""
        if why == "follows_you":
            return "Follows you"
        if why.startswith("followed_by:"):
            parts = why.split(":")
            more = " and %s more" % parts[2] if len(parts) > 2 else ""
            return "Followed by @%s%s" % (parts[1], more)
        if why.startswith("popular:"):
            return "%s followers" % why.split(":")[1]
        return "New on Awesome Music" if why == "new" else ""

    def _add_suggestions(self, page):
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        page.add(box, "People you may know")

        def ok(people):
            if not _alive(box):
                return
            box.setVisible(bool(people))
            for p in people[:8]:
                btn = Pill("Follow back" if p.get("follows_you") else "Follow", style="solid")
                btn.clicked.connect(lambda _c=False, p=p: self._follow(p, True))
                row = PersonRow(dict(p, why_text=self._why(p.get("why"))), btn)
                row.clicked.connect(lambda _c=False, p=p: p.get("handle") and self.go("profile", {"handle": p["handle"]}))
                col.addWidget(row)
        self.hub.run(social.suggest, ok, lambda _e: None)

    def _add_top_listener(self, page):
        def ok(r):
            who = r.get("champion")
            if not who or not _alive(page) or not self._here or self._here[0] != "friends":
                return
            if who.get("self"):
                text = "You were last month's top listener, with %d hours." % ((who.get("seconds") or 0) // 3600)
            else:
                text = "%s was your circle's top listener last month." % label_of(who)
            card = Glass()
            card.col.addWidget(_label(text, 13.5, QFont.Weight.DemiBold, look.TEXT, wrap=True))
            page.add(card, "Top listener")
        self.hub.run(social.top_listener, ok, lambda _e: None)

    # ---- who follows whom ----
    def _build_people(self, what):
        page = self._pages["people"]
        page.clear()
        page.art_url = None
        handle, which = what["handle"], what["which"]
        page.hero.set_text("@" + handle, which.title(), "")
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        page.add(box)
        mine = (self.hub.me or {}).get("handle") == handle

        def ok(people):
            if not _alive(box):
                return
            self._fill_people(col, [p for p in people if not p.get("self")], "No one yet.")
            if mine and which == "followers":
                for i in range(col.count()):
                    row = col.itemAt(i).widget()
                    if isinstance(row, PersonRow):
                        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                        row.setToolTip("Right-click to remove this follower")
                        row.customContextMenuRequested.connect(lambda _p, r=row: self._follower_menu(r.person, what))

        def fail(e):
            if _alive(page):
                page.hero.set_text("@" + handle, which.title(), str(e))
        self.hub.run(lambda: social.follow_list(handle, which), ok, fail)

    def _follower_menu(self, person, what):
        menu = style_menu(QMenu(self), theme.tokens(True))
        menu.addAction("Remove this follower", lambda: self.hub.run(
            lambda: social.remove_follower(person["id"]),
            lambda _r: (self.status.setText("%s no longer follows you." % label_of(person)),
                        self.go("people", what, push=False), self.hub.refresh())))
        menu.exec(QCursor.pos())

    def _fill_people(self, col, people, empty):
        _empty(col)
        if not people and empty:
            col.addWidget(_label(empty, 13, color=look.TEXT_3, wrap=True))
        for person in people:
            col.addWidget(self._person_row(person))

    def _person_row(self, person):
        if person.get("following"):
            btn = Pill("Message", style="glass")
            btn.clicked.connect(lambda _c=False, p=person: self._open_dm(p))
        else:
            btn = Pill("Follow back" if person.get("follows_you") else "Follow", style="solid")
            btn.clicked.connect(lambda _c=False, p=person: self._follow(p, True))
        row = PersonRow(person, btn)
        row.clicked.connect(lambda _c=False, p=person: p.get("handle") and self.go(
            "profile", {"handle": p["handle"], "id": p["id"]}))
        row.play.connect(lambda t: self.play_from(t, [t], context="single"))
        return row

    def _fill_friends(self):
        col = self._social_lists.get("people")
        if col is None or not _alive(col.parentWidget()):
            return
        people = list(self.hub.people)
        people.sort(key=lambda p: (not (p.get("now") or {}).get("track"),
                                   not (p.get("following") and p.get("follows_you")), label_of(p).lower()))
        self._fill_people(col, people, "No one yet — find friends by their username above, and follow them.")
        jams = self._social_lists.get("jams")
        if jams is None or not _alive(jams.parentWidget()):
            return
        _empty(jams)
        for j in self.hub.live:
            if self.jam.code == j.get("code"):
                continue
            song = j.get("song") or {}
            n = len(j.get("members") or [])
            btn = Pill("Join", style="solid")
            btn.clicked.connect(lambda _c=False, c=j["code"]: self.jam.join(c))
            row = PersonRow({"id": j.get("chat") or j.get("code"), "name": "Jam in %s" % j.get("chat_title", "a chat"),
                             "handle": "%d listening%s" % (n, " · " + song["title"] if song.get("title") else "")},
                            btn)
            jams.addWidget(row)

    def _follow(self, person, on):
        def ok(_r):
            self.status.setText(("You follow %s now." if on else "You don't follow %s now.") % label_of(person))
            self.hub.refresh()
            if self._here and self._here[0] == "profile":
                self.go("profile", self._here[1], push=False)
        self.hub.run(lambda: social.follow(person["id"], on), ok)

    # ---- someone's page ----
    def _build_profile(self, who):
        page = self._pages["profile"]
        page.clear()
        page.art_url = None
        if not self.hub.signed_in():
            self._need_sign_in(page, "Profiles")
            return
        handle = (who or {}).get("handle", "")
        page.hero.set_text("Profile", "@" + handle, "Opening…")

        def ok(r):
            if not _alive(page) or not self._here or self._here[0] != "profile":
                return
            u = r.get("user") or {}
            page.hero.set_text("@" + (u.get("handle") or ""), label_of(u),
                               "%d followers · following %d" % (u.get("followers") or 0, u.get("following_count") or 0))
            if not u.get("locked"):
                for which in ("followers", "following"):
                    b = Pill(which.title(), style="glass")
                    b.clicked.connect(lambda _c=False, w=which: self.go("people", {"handle": u.get("handle"), "which": w}))
                    page.hero.add_action(b)
            if u.get("self") and u.get("listen_total"):
                page.add(_label("You've listened for %d hours this month, %d in all." % (
                    (u.get("listen_month") or 0) // 3600, (u.get("listen_total") or 0) // 3600), 13, color=look.TEXT_2))
            who = QWidget()
            wl = QHBoxLayout(who)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(14)
            wl.addWidget(Avatar(u, 84))
            wl.addStretch(1)
            page.add(who)
            if u.get("self"):
                ed = Pill("Edit profile", style="solid")
                ed.clicked.connect(lambda: self.go("account"))
                page.hero.add_action(ed)
                sh = Pill("Share profile", style="glass")
                sh.setToolTip("Copy your @username")
                sh.clicked.connect(lambda: (QApplication.clipboard().setText("@" + (u.get("handle") or "")),
                                            self.status.setText("Your @username is copied.")))
                page.hero.add_action(sh)
            else:
                if u.get("following"):
                    msg = Pill("Message", style="solid")
                    msg.clicked.connect(lambda: self._open_dm(u))
                    page.hero.add_action(msg)
                    unf = Pill("Following", style="glass")
                    unf.setToolTip("Stop following")
                    unf.clicked.connect(lambda: self._follow(u, False))
                    page.hero.add_action(unf)
                else:
                    fol = Pill("Follow back" if u.get("follows_you") else "Follow", style="solid")
                    fol.clicked.connect(lambda: self._follow(u, True))
                    page.hero.add_action(fol)
            relay = _Relay(page)
            relay.play.connect(self._play_shared)
            now = (u.get("now") or {}).get("track")
            if now:
                card = Glass()
                card.col.addWidget(SongCard(now, relay))
                page.add(card, "Playing now")
            if u.get("locked"):
                page.add(_label("This profile is private. It opens to the people it follows.", 13, color=look.TEXT_2))
                return
            pl_relay = _Relay(page)
            pl_relay.play.connect(lambda it, h=u.get("handle"): self._open_their_playlist(h, it))
            for title, items, rl in (("Artists they like", r.get("artists") or [], relay),
                                     ("Liked songs", r.get("liked") or [], relay),
                                     ("Playlists", r.get("playlists") or [], pl_relay)):
                if not items:
                    continue
                box = QWidget()
                bl = QVBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.setSpacing(2)
                for it in items[:30]:
                    bl.addWidget(SongCard(it, rl))
                page.add(box, title)
            if not now and not r.get("artists") and not r.get("playlists") and not r.get("liked"):
                page.add(_label("Nothing to show yet.", 13, color=look.TEXT_3))

        def fail(e):
            if _alive(page):
                page.hero.set_text("Profile", "@" + handle, str(e))
        self.hub.run(lambda: social.profile(handle), ok, fail)

    def _open_their_playlist(self, handle, item):
        def ok(pl):
            songs = (pl or {}).get("tracks") or []
            if songs:
                self.play_from(songs[0], songs)
            else:
                self.status.setText("That playlist is empty.")
        self.hub.run(lambda: social.playlist_of(handle, item.get("id")), ok)

    # ---- Chats ----
    def _build_chats(self):
        page = self._pages["chats"]
        page.clear()
        page.art_url = None
        if not self.hub.signed_in():
            self._need_sign_in(page, "Chats")
            return
        page.hero.set_text("Together", "Chats", "Share songs with friends, talk about them, start a jam.")
        new = Pill("New group", style="glass")
        new.clicked.connect(self._new_group)
        page.hero.add_action(new)
        box = QWidget()
        self._social_lists["chats"] = QVBoxLayout(box)
        self._social_lists["chats"].setContentsMargins(0, 0, 0, 0)
        self._social_lists["chats"].setSpacing(2)
        page.add(box)
        self._fill_chats()
        self.hub.refresh()

    def _fill_chats(self):
        col = self._social_lists.get("chats")
        if col is None or not _alive(col.parentWidget()):
            return
        _empty(col)
        me_id = (self.hub.me or {}).get("id")
        if not self.hub.chats:
            col.addWidget(_label("No chats yet. Open a friend's page and send them a message — or share a song from "
                                 "its menu.", 13, color=look.TEXT_3, wrap=True))
        for c in self.hub.chats:
            row = ChatRow(c, me_id)
            row.clicked.connect(lambda _c=False, c=c: self.go("chat", {"id": c["id"], "title": c.get("title")}))
            col.addWidget(row)

    def _open_dm(self, person):
        self.hub.run(lambda: social.dm(person["id"]),
                     lambda c: (self.hub.refresh(), self.go("chat", {"id": c["id"], "title": c.get("title")})))

    def _new_group(self):
        known = [p for p in self.hub.people if p.get("following") or p.get("follows_you")]
        if not known:
            self.status.setText("Follow some people first — a group is made of people you know.")
            return
        title, ok = QInputDialog.getText(self, "New group", "The group's name:")
        if not ok or not title.strip():
            return
        names = [label_of(p) for p in known]
        picked, ok = QInputDialog.getItem(self, "New group", "Start it with:", names, 0, False)
        if not ok:
            return
        who = [p["id"] for p in known if label_of(p) == picked]
        self.hub.run(lambda: social.new_group(title.strip(), who),
                     lambda c: (self.hub.refresh(), self.go("chat", {"id": c["id"], "title": c.get("title")})))

    # ---- one chat ----
    def _build_chat(self, chat):
        page = self._pages["chat"]
        page.clear()
        page.art_url = None
        if not self.hub.signed_in():
            self._need_sign_in(page, "Chats")
            return
        cid = chat["id"]
        self._chat_open = cid
        full = next((c for c in self.hub.chats if c["id"] == cid), None) or {}
        self._chat_members = {m["id"]: m for m in full.get("members") or []}
        if full.get("kind") == "group":
            page.hero.set_text("Group", full.get("title") or "Group",
                               ", ".join(label_of(m) for m in full.get("members") or []))
            leave = Pill("Leave", style="glass")
            leave.clicked.connect(lambda: self._leave_chat(cid))
            page.hero.add_action(leave)
        else:
            page.hero.set_text("Chat", chat.get("title") or full.get("title") or "Chat", "")
        jam = Pill("Start a jam", style="solid")
        jam.setToolTip("Listen together: everyone here hears the same song at the same moment")
        jam.clicked.connect(lambda: self.jam.start(cid))
        page.hero.add_action(jam)
        box = QWidget()
        box.setMaximumWidth(820)
        self._chat_box = QVBoxLayout(box)
        self._chat_box.setContentsMargins(0, 0, 0, 0)
        self._chat_box.setSpacing(4)
        self._chat_seen = set()
        self._bubbles = {}
        page.add(box)
        self._seen_label = _label("", 11.5, color=look.TEXT_3)
        self._seen_label.setMaximumWidth(820)
        self._seen_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._seen_label.hide()
        page.add(self._seen_label, gap=0)
        compose = Compose()
        compose.setMaximumWidth(820)
        self._compose = compose
        compose.send.connect(lambda t: self._chat_send(cid, t))
        compose.share_now.connect(lambda: self._share_current_to(cid))
        compose.start_jam.connect(lambda: self.jam.start(cid))
        page.add(compose, gap=12)
        self.chat_live.open(cid)

        def ok(msgs):
            if self._chat_open != cid:
                return
            for m in msgs:
                self._chat_message(m, scroll=False)
            QTimer.singleShot(60, lambda: page.verticalScrollBar().setValue(page.verticalScrollBar().maximum()))
            self.hub.run(lambda: social.mark_read(cid), lambda _r: self.hub.refresh(), lambda _e: None)
            QTimer.singleShot(80, compose.field.edit.setFocus)
            self._show_seen()
        self.hub.run(lambda: social.messages(cid), ok)

    def _chat_message(self, m, scroll=True):
        if self._chat_box is None or not _alive(self._chat_box.parentWidget()):
            return
        if not self._here or self._here[0] != "chat" or m.get("id") in self._chat_seen:
            return
        self._chat_seen.add(m.get("id"))
        me_id = (self.hub.me or {}).get("id")
        who = self._chat_members.get(m.get("user")) if len(self._chat_members) > 2 else None
        quoted = None
        if m.get("reply"):
            ru = (m["reply"] or {}).get("user")
            quoted = "You" if ru == me_id else label_of(self._chat_members.get(ru) or {})
        b = Bubble(m, m.get("user") == me_id, label_of(who) if who else None, QColor(125, 211, 252), quoted=quoted)
        b.play.connect(self._play_shared)
        b.join.connect(self.jam.join)
        b.menu_asked.connect(self._bubble_menu)
        self._bubbles[m.get("id")] = b
        self._chat_box.addWidget(b)
        self._show_seen()
        if scroll:
            page = self._pages["chat"]
            QTimer.singleShot(40, lambda: page.verticalScrollBar().setValue(page.verticalScrollBar().maximum()))
            if m.get("user") != me_id and self._chat_open:
                cid = self._chat_open
                self.hub.run(lambda: social.mark_read(cid), lambda _r: None, lambda _e: None)

    def _chat_send(self, cid, text):
        c = self._compose
        editing = c.editing if c is not None and _alive(c) else None
        reply = c.reply_to if c is not None and _alive(c) else None
        if c is not None and _alive(c):
            c.reply_to = c.editing = None
            c.clear_note()
        if editing is not None:
            mid = editing.get("id")
            self.hub.run(lambda: social.edit_text(cid, mid, text),
                         lambda _r: self._chat_edited({"id": mid, "text": text}))
            return
        rid = (reply or {}).get("id")
        self.hub.run(lambda: social.send_text(cid, text, rid), lambda m: (self._chat_message(m), self.hub.refresh()))

    def _bubble_menu(self, bubble, pos):
        m = bubble.msg
        cid = self._chat_open
        menu = style_menu(QMenu(self), theme.tokens(True))
        who = self._chat_members.get(m.get("user")) or {}
        menu.addAction("Reply", lambda: self._compose.answer(m, "yourself" if bubble.mine else label_of(who)))
        if m.get("kind") == "text":
            menu.addAction("Copy", lambda: QApplication.clipboard().setText(m.get("text") or ""))
        if bubble.mine:
            if m.get("kind") == "text":
                menu.addAction("Edit", lambda: self._compose.edit(m))
            menu.addAction("Unsend", lambda: self.hub.run(lambda: social.unsend(cid, m.get("id")),
                                                          lambda _r: self._chat_unsent(m.get("id"))))
        menu.addSeparator()
        for line in self._seen_lines(m):
            menu.addAction(line).setEnabled(False)
        menu.exec(pos)

    def _chat_edited(self, e):
        b = self._bubbles.get(e.get("id"))
        if b is not None and _alive(b):
            b.set_text(e.get("text") or "")

    def _chat_unsent(self, mid):
        b = self._bubbles.pop(mid, None)
        if b is not None and _alive(b):
            b.setParent(None)
            b.deleteLater()
        self._show_seen()
        self.hub.refresh()

    def _chat_view(self):
        return next((c for c in self.hub.chats if c["id"] == self._chat_open), None) or {}

    def _seen_lines(self, m):
        """In a group, who has seen a message and who hasn't yet."""
        me_id = (self.hub.me or {}).get("id")
        others = [x for x in self._chat_view().get("members") or [] if x.get("id") != me_id]
        if m.get("user") != me_id or not others:
            return []
        seen = [label_of(x) for x in others if (x.get("last_read") or 0) >= (m.get("at") or 0)]
        wait = [label_of(x) for x in others if (x.get("last_read") or 0) < (m.get("at") or 0)]
        out = []
        if seen:
            out.append("Seen by " + ", ".join(seen))
        if wait:
            out.append("Not yet: " + ", ".join(wait))
        return out

    def _show_seen(self):
        """Under your last message: Sent, Seen; in a group, Seen by N or Seen by everyone."""
        lab = self._seen_label
        if lab is None or not _alive(lab):
            return
        me_id = (self.hub.me or {}).get("id")
        ids = sorted(self._bubbles)
        last = self._bubbles[ids[-1]].msg if ids else None
        if not last or last.get("user") != me_id:
            lab.hide()
            return
        chat = self._chat_view()
        others = [x for x in chat.get("members") or [] if x.get("id") != me_id]
        n = sum(1 for x in others if (x.get("last_read") or 0) >= (last.get("at") or 0))
        if not n:
            text = "Sent"
        elif chat.get("kind") == "group":
            text = "Seen by everyone" if n == len(others) else "Seen by %d" % n
        else:
            text = "Seen"
        lab.setText(text)
        lab.show()

    def _leave_chat(self, cid):
        if QMessageBox.question(self, "Leave the group",
                                "Leave this group? You'll stop seeing its messages.") != QMessageBox.StandardButton.Yes:
            return
        self.hub.run(lambda: social.leave_chat(cid), lambda _r: (self.hub.refresh(), self.go("chats")))

    def _play_shared(self, item):
        kind = item.get("kind", "track")
        if kind == "track":
            self.play_from(item, [item], context="single")
        elif kind == "artist":
            self.go("artist", item)
        elif kind == "album":
            self.go("album", item)
        elif item.get("tracks"):
            songs = item["tracks"]
            self.play_from(songs[0], songs)
        else:
            self.status.setText("That can't be opened here.")

    # ---- sharing ----
    def _share_current_to(self, cid):
        t = self.current()
        if not t:
            self.status.setText("Play a song first, then share it.")
            return
        self.share_to(cid, t)

    def share_to(self, cid, item):
        self.hub.run(lambda: social.send_item(cid, item),
                     lambda m: (self._chat_message(m), self.hub.refresh(),
                                self.status.setText("Shared %s." % item.get("title", ""))))

    def share_menu(self, item, pos=None):
        """A song's "Share to a chat…": your chats, then friends you don't have a chat with yet."""
        if not self.hub.signed_in():
            self.status.setText("Sign in on the Friends page to share songs with friends.")
            self.go("friends")
            return
        menu = style_menu(QMenu(self), theme.tokens(True))
        head = menu.addAction("SHARE TO")
        head.setEnabled(False)
        have = set()
        me_id = (self.hub.me or {}).get("id")
        for c in self.hub.chats[:12]:
            menu.addAction(c.get("title") or "Chat", lambda c=c: self.share_to(c["id"], item))
            if c.get("kind") == "dm":
                have.update(m.get("id") for m in c.get("members") or [] if m.get("id") != me_id)
        rest = [p for p in self.hub.people if p.get("following") and p["id"] not in have][:10]
        if rest:
            menu.addSeparator()
            for p in rest:
                menu.addAction(label_of(p), lambda p=p: self.hub.run(lambda: social.dm(p["id"]),
                                                                       lambda c: self.share_to(c["id"], item)))
        if not self.hub.chats and not rest:
            menu.addAction("Follow friends first…", lambda: self.go("friends"))
        menu.exec(pos or QCursor.pos())

    def social_song_actions(self, menu, track):
        """The song menus' share and jam entries."""
        menu.addAction("Share to a chat…", lambda: self.share_menu(track, QCursor.pos()))
        if self.jam.active:
            menu.addAction("Add to the jam", lambda: self.jam.add(track))
            menu.addAction("Play next in the jam", lambda: self.jam.add(track, next_=True))

    # ---- you ----
    def _build_account(self):
        page = self._pages["account"]
        page.clear()
        page.art_url = None
        if not self.hub.signed_in():
            self._need_sign_in(page, "Sign in")
            return
        me = self.hub.me or {}
        page.hero.set_text("Your profile", label_of(me), me.get("email", ""))
        card = Glass()
        card.setMaximumWidth(620)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(Avatar(me, 72))
        pic = QVBoxLayout()
        change = Pill("Change picture", style="glass")
        change.clicked.connect(lambda: pick_avatar(self, self.hub, lambda: None))
        pic.addWidget(change)
        if me.get("avatar_at"):
            drop = Pill("Remove picture", style="glass")
            drop.clicked.connect(lambda: self.hub.run(social.remove_picture,
                                                      lambda _u: self.hub.account_changed.emit()))
            pic.addWidget(drop)
        top.addLayout(pic)
        top.addStretch(1)
        card.col.addLayout(top)
        name = Field("Your name")
        name.edit.setText(me.get("name") or "")
        name.edit.setMaxLength(40)
        handle = Field("@username")
        handle.edit.setText(me.get("handle") or "")
        handle.edit.setMaxLength(20)
        card.col.addWidget(_label("Name", 12, QFont.Weight.DemiBold, look.TEXT_3))
        card.col.addWidget(name)
        card.col.addWidget(_label("Username", 12, QFont.Weight.DemiBold, look.TEXT_3))
        card.col.addWidget(handle)
        msg = _label("", 12.5, color=QColor(252, 165, 165), wrap=True)
        msg.hide()
        card.col.addWidget(msg)
        save = Pill("Save", style="solid")

        def do_save():
            def fail(e):
                msg.setText(str(e))
                msg.show()
            self.hub.run(lambda: social.update_me(handle=handle.text(), name=name.text()),
                         lambda _u: (msg.hide(), self.status.setText("Saved."), self.hub.account_changed.emit()), fail)
        save.clicked.connect(do_save)
        card.col.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        page.add(card)
        priv = Glass()
        priv.setMaximumWidth(620)
        for key, text, sub in (("share_activity", "Show friends what I'm playing",
                                "Friends see the song you're on, and can play it too."),
                               ("show_artists", "Show the artists I like on my profile", ""),
                               ("show_likes", "Show my liked songs on my profile", ""),
                               ("private", "Private profile",
                                "Only people you follow see your playlists, likes and what you play.")):
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.addWidget(_label(text, 13.5, QFont.Weight.DemiBold, look.TEXT))
            if sub:
                col.addWidget(_label(sub, 12, color=look.TEXT_3))
            row.addLayout(col, 1)
            on = me.get(key, key != "private")
            pill = Pill("On" if on else "Off", style="tab_on" if on else "glass")
            pill.clicked.connect(lambda _c=False, k=key, v=not on: self.hub.run(
                lambda: social.update_me(**{k: v}), lambda _u: self.hub.account_changed.emit()))
            row.addWidget(pill)
            priv.col.addLayout(row)
        page.add(priv, "Privacy")
        lib = Glass()
        lib.setMaximumWidth(620)
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(_label("Keep my library the same on my phone and this PC", 13.5, QFont.Weight.DemiBold, look.TEXT))
        hb = self.hub
        state = ("Couldn't sync: %s" % hb.sync_error) if hb.sync_error else (
            "Synced %s" % when(int(hb.synced_at * 1000)) if hb.synced_at else "Likes, playlists, history and your taste")
        col.addWidget(_label(state, 12, color=look.TEXT_3))
        row.addLayout(col, 1)
        now_btn = Pill("Sync now", style="glass")
        now_btn.clicked.connect(lambda: (self.hub.sync(), self.status.setText("Syncing your library…")))
        row.addWidget(now_btn)
        lib.col.addLayout(row)
        row2 = QHBoxLayout()
        col2 = QVBoxLayout()
        col2.addWidget(_label("Notifications", 13.5, QFont.Weight.DemiBold, look.TEXT))
        col2.addWidget(_label("Messages, new followers and news, while the app is in the background.", 12,
                              color=look.TEXT_3))
        row2.addLayout(col2, 1)
        n_on = bool((self.settings or {}).get("music_notifications", True))
        n_btn = Pill("On" if n_on else "Off", style="tab_on" if n_on else "glass")
        n_btn.clicked.connect(lambda: (self.set_pref("music_notifications", not n_on), self.go("account", None, push=False)))
        row2.addWidget(n_btn)
        lib.col.addLayout(row2)
        page.add(lib, "This PC and your phone")
        acct = QWidget()
        al = QHBoxLayout(acct)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(10)
        out = Pill("Sign out", style="glass")
        out.clicked.connect(self._sign_out)
        gone = Pill("Delete account", style="glass")
        gone.clicked.connect(self._delete_account)
        al.addWidget(out)
        al.addWidget(gone)
        al.addStretch(1)
        page.add(acct, "Account")

    def _sign_out(self):
        self.jam.leave()
        self.chat_live.close()
        self.hub.run(social.sign_out, lambda _r: self.status.setText("Signed out."))

    def _delete_account(self):
        if QMessageBox.question(
                self, "Delete your account",
                "Delete your Awesome Music account? Your profile, follows and the copy of your library on the server "
                "go, on every device. The music on this PC stays.") != QMessageBox.StandardButton.Yes:
            return
        self.jam.leave()
        self.chat_live.close()
        self.hub.run(social.delete_account, lambda _r: self.status.setText("Your account was deleted."))

    # ---- jams: the player in step ----
    def jam_follow(self, queue, index, playing, target):
        """The jam's song, playing or paused, and where: this player brought there."""
        if not queue or not (0 <= index < len(queue)):
            if self._playing():
                self.player.pause()
            return
        want = queue[index]
        cur = self.current()
        if cur is None or cur.get("id") != want.get("id"):
            self.queue = [dict(t) for t in queue]
            self.index = index
            self._context = "jam"
            self._render_queue()
            self._play_current()
            return
        if [t.get("id") for t in self.queue] != [t.get("id") for t in queue]:
            self.queue = [dict(t) for t in queue]
            self.index = index
            self._render_queue()
        if not self._started:
            return                      # still loading: in step once it starts
        if target < 0:                  # a song set to start a moment from now, everywhere together
            if self._playing():
                self.player.pause()
            return
        if playing and not self._playing():
            self.player.play()
        elif not playing and self._playing():
            self.player.pause()
        drift = self.player.position() - target
        dur = self.player.duration()
        if abs(drift) > 700 and (not dur or target < dur - 1500):
            self.player.setPosition(int(target))

    def _jam_menu(self):
        st = self.jam.state or {}
        menu = style_menu(QMenu(self), theme.tokens(True))
        who = ", ".join(label_of(m) for m in st.get("members") or [])
        head = menu.addAction(("In the jam: " + who)[:90])
        head.setEnabled(False)
        menu.addAction("Copy the code (%s)" % st.get("code", ""),
                       lambda: QApplication.clipboard().setText(st.get("code", "")))
        if self.jam.is_host():
            menu.addAction("Let everyone change the queue" if st.get("locked") else "Only I change the queue",
                           lambda: self.jam.op("lock", locked=not st.get("locked")))
        menu.addSeparator()
        menu.addAction("Leave the jam", self.jam.leave)
        menu.exec(QCursor.pos())

