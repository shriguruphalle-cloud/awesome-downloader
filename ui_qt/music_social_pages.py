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
                           PersonRow, SignInCard, SongCard, _alive, _label, label_of, pick_avatar)
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

    SOCIAL_PAGES = ("friends", "profile", "chats", "chat", "account")

    def _init_social(self, corner_row):
        self.hub = Hub(self)
        self.hub.toast.connect(self.status.setText)
        self.hub.changed.connect(self._social_changed)
        self.hub.account_changed.connect(self._social_account)
        self.chat_live = ChatLive(self)
        self.chat_live.message.connect(self._chat_message)
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
        self.hub.start()

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
        here = self._here[0] if self._here else None
        if here == "friends":
            self._fill_friends()
        elif here == "chats":
            self._fill_chats()

    def _social_account(self):
        here = self._here[0] if self._here else None
        if here in ("friends", "chats", "account"):
            self.go(here, None, push=False)

    def _jam_shown(self, st):
        self.jam_chip.set_state(st, self.jam.status)
        self._corner.resize(self._corner.sizeHint())

    def _need_sign_in(self, page, what):
        """Signed out: the page is the sign-in card."""
        page.hero.set_text("Awesome Music", what, "Sign in with your email — the same account as on your phone.")
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
        find.setMaximumWidth(520)
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
        if me and not me.get("handle"):
            self.status.setText("Pick a username in Your profile so friends can find you.")

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
                               "%d followers · following %d" % (u.get("followers") or 0, u.get("following") or 0))
            if not u.get("self"):
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
                card.setMaximumWidth(620)
                card.col.addWidget(SongCard(now, relay))
                page.add(card, "Playing now")
            for title, items, kind in (("Artists they like", r.get("artists") or [], None),
                                       ("Playlists", r.get("playlists") or [], "mix")):
                if not items:
                    continue
                box = QWidget()
                bl = QVBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                bl.setSpacing(2)
                for it in items[:30]:
                    bl.addWidget(SongCard(dict(it, kind=kind) if kind else it, relay))
                box.setMaximumWidth(620)
                page.add(box, title)
            if not now and not r.get("artists") and not r.get("playlists"):
                page.add(_label("Nothing to show yet.", 13, color=look.TEXT_3))

        def fail(e):
            if _alive(page):
                page.hero.set_text("Profile", "@" + handle, str(e))
        self.hub.run(lambda: social.profile(handle), ok, fail)

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
        box.setMaximumWidth(760)
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
        page.add(box)
        compose = Compose()
        compose.setMaximumWidth(820)
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
        self.hub.run(lambda: social.messages(cid), ok)

    def _chat_message(self, m, scroll=True):
        if self._chat_box is None or not _alive(self._chat_box.parentWidget()):
            return
        if not self._here or self._here[0] != "chat" or m.get("id") in self._chat_seen:
            return
        self._chat_seen.add(m.get("id"))
        me_id = (self.hub.me or {}).get("id")
        who = self._chat_members.get(m.get("user")) if len(self._chat_members) > 2 else None
        b = Bubble(m, m.get("user") == me_id, label_of(who) if who else None, QColor(125, 211, 252))
        b.play.connect(self._play_shared)
        b.join.connect(self.jam.join)
        self._chat_box.addWidget(b)
        if scroll:
            page = self._pages["chat"]
            QTimer.singleShot(40, lambda: page.verticalScrollBar().setValue(page.verticalScrollBar().maximum()))
            if m.get("user") != me_id and self._chat_open:
                cid = self._chat_open
                self.hub.run(lambda: social.mark_read(cid), lambda _r: None, lambda _e: None)

    def _chat_send(self, cid, text):
        self.hub.run(lambda: social.send_text(cid, text), lambda m: (self._chat_message(m), self.hub.refresh()))

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
            self._need_sign_in(page, "Your profile")
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
                               ("show_artists", "Show the artists I like on my profile", "")):
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.addWidget(_label(text, 13.5, QFont.Weight.DemiBold, look.TEXT))
            if sub:
                col.addWidget(_label(sub, 12, color=look.TEXT_3))
            row.addLayout(col, 1)
            on = me.get(key, True)
            pill = Pill("On" if on else "Off", style="tab_on" if on else "glass")
            pill.clicked.connect(lambda _c=False, k=key, v=not on: self.hub.run(
                lambda: social.update_me(**{k: v}), lambda _u: self.hub.account_changed.emit()))
            row.addWidget(pill)
            priv.col.addLayout(row)
        page.add(priv, "Privacy")
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

