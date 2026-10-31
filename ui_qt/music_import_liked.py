"""Every liked song from another app at once (the phone app's ImportLiked.kt).

You sign in on the service's own page, in a WebView2 of its own: a private page
in a throwaway profile, with nothing in common with the Browser tab's. Your songs
are then read from inside that page, as the service's own player reads them. So
the cookies and tokens never leave the page: this code never asks for them, and
all that comes back over the page's message channel is the songs and a count.
Everything of the sign-in is wiped as soon as the songs are read, or when this
closes; what a killed app left behind goes the next time this opens. Nothing
here logs an address, because a sign-in's addresses can carry secrets.

The songs then go to music_brain.import_liked, which matches them, likes them and
keeps them as a playlist, and can carry on with this sheet hidden.
"""
import json
import os
import shutil
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedLayout, QVBoxLayout, QWidget

from app import config
from app.core import music_brain as brain
from app.logging_setup import get_logger

from . import music_look as look
from . import webview2
from .music_panels import GlassSheet
from .music_social import Field
from .music_widgets import LinkLabel, Pill, ThinSlider

logger = get_logger("music_import_liked")

# Kept short on purpose (see webview2.py), and never the Browser tab's "wv2".
PROFILE_DIR = os.path.join(config.APPDATA_DIR, "wvi")

SPOTIFY_LOGIN = "https://accounts.spotify.com/en/login?continue=https%3A%2F%2Fopen.spotify.com%2Fcollection%2Ftracks"
SPOTIFY_HOST = "open.spotify.com"
SPOTIFY_QUERY = "https://api-partner.spotify.com/pathfinder/v2/query"
SPOTIFY_RESET = "https://accounts.spotify.com/password-reset"
LIBRARY_HASH = "087278b20b743578a6262c2b0b4bcd20d879c503cc359a2285baf083ef944240"
# Google's own sign-in, which comes back to YouTube Music
YTM_SIGN_IN = ("https://accounts.google.com/ServiceLogin?ltmpl=music&service=youtube&passive=true&continue="
               "https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26next%3D"
               "https%253A%252F%252Fmusic.youtube.com%252F")
YTM_HOST = "music.youtube.com"
GOOGLE_HOST = "accounts.google.com"

# Answers only whether a sign-in's cookie is there: true or false, never the cookie.
YTM_SIGNED_IN = "/SAPISID=/.test(document.cookie)"

# Your Liked music, page by page, as the site reads it. In the page: the sign-in hash is made here and sent from here.
YTM_READ = r"""(async function(){if(window.__likedReading)return;window.__likedReading=true;
var post=function(m){window.chrome.webview.postMessage(m);};
function pick(n){var c=document.cookie.split('; ');for(var i=0;i<c.length;i++)if(c[i].indexOf(n+'=')===0)return c[i].slice(n.length+1);return null;}
function wait(ms){return new Promise(function(r){setTimeout(r,ms);});}
async function hash(text){var b=await crypto.subtle.digest('SHA-1',new TextEncoder().encode(text));
 return Array.from(new Uint8Array(b)).map(function(x){return ('0'+x.toString(16)).slice(-2);}).join('');}
async function ask(extra){var sid=pick('__Secure-3PAPISID')||pick('SAPISID');var now=Math.floor(Date.now()/1000);var o=location.origin;
 var h=await hash(now+' '+sid+' '+o);var d=new Date();var day=''+d.getFullYear()+('0'+(d.getMonth()+1)).slice(-2)+('0'+d.getDate()).slice(-2);
 var body=Object.assign({context:{client:{clientName:'WEB_REMIX',clientVersion:'1.'+day+'.01.00',hl:'en'},user:{}}},extra);
 var r=await fetch(o+'/youtubei/v1/browse?prettyPrint=false',{method:'POST',credentials:'include',
  headers:{'Content-Type':'application/json','Authorization':'SAPISIDHASH '+now+'_'+h,'X-Origin':o,'X-Goog-AuthUser':'0'},body:JSON.stringify(body)});
 return {code:r.status,page:r.status===200?await r.json():null};}
function walk(e,visit){if(Array.isArray(e)){e.forEach(function(v){walk(v,visit);});}
 else if(e&&typeof e==='object'){Object.keys(e).forEach(function(k){if(!visit(k,e[k]))walk(e[k],visit);});}}
function first(e,key){var f=null;walk(e,function(k,v){if(f===null&&k===key&&typeof v==='string')f=v;return false;});return f;}
function words(c){try{var o=c[Object.keys(c)[0]];return (o.text.runs||[]).map(function(r){return r.text||'';}).join('').trim();}catch(x){return '';}}
function secs(t){return t.split(':').reduce(function(n,p){return n*60+(parseInt(p,10)||0);},0);}
function row(r){var id=first(r.playlistItemData,'videoId')||first(r,'videoId');if(!id)return null;var f=r.flexColumns||[];
 return {video_id:id,title:words(f[0]),channel:words(f[1]),duration:secs(words((r.fixedColumns||[])[0])),is_song:first(r,'musicVideoType')==='MUSIC_VIDEO_TYPE_ATV'};}
try{var out=[],extra={browseId:'VLLM'},code=0;
 while(true){var got=null;
  for(var a=0;a<3;a++){try{got=await ask(extra);code=got.code;}catch(x){got=null;}if(got&&got.page)break;await wait(1500*(a+1));}
  if(!got||!got.page){if(out.length)break;post({liked:'failed',code:code});return;}
  var next=null,before=out.length;
  walk(got.page,function(k,v){if(k==='musicResponsiveListItemRenderer'){var s=row(v);if(s)out.push(s);return true;}
   if(k==='continuationCommand'){if(next===null&&v&&v.token)next=v.token;return true;}return false;});
  post({liked:'count',n:out.length});
  if(!next||out.length===before)break;extra={continuation:next};}
 post({liked:'songs',songs:out});
}catch(x){post({liked:'failed',code:0});}
})();"""

# Spotify's public API refuses the web player's sign-in, so the songs are read the way the player reads them: its
# library query, with the headers it sends. Those are noted here, inside the page, as it sends them.
SPOTIFY_WATCH = r"""(function(){if(location.hostname!=='%(host)s'||window.__likedSeen)return;window.__likedSeen={};
var Q='%(query)s',SENT=['authorization','client-token','app-platform','spotify-app-version'],real=window.fetch;
window.fetch=function(a,b){try{var u=typeof a==='string'?a:(a&&a.url)||String(a);
 if(u.indexOf(Q)===0){var h=new Headers((b&&b.headers)||(a&&a.headers)||{});
  if(h.get('authorization')){SENT.forEach(function(n){var v=h.get(n);if(v)window.__likedSeen[n]=v;});}}}catch(x){}
 return real.apply(this,arguments);};})();"""

# Query ids change with the player's versions: when the one known here stops working, the current one is found in its
# own scripts. Every liked song then comes back 50 at a time.
SPOTIFY_READ = r"""(async function(){if(window.__likedReading)return;window.__likedReading=true;
var post=function(m){window.chrome.webview.postMessage(m);};var Q='%(query)s',hash='%(hash)s';
function wait(ms){return new Promise(function(r){setTimeout(r,ms);});}
async function page(off,lim){try{var h=Object.assign({'Accept':'application/json','Content-Type':'application/json'},window.__likedSeen);
 var r=await fetch(Q,{method:'POST',headers:h,body:JSON.stringify({operationName:'fetchLibraryTracks',variables:{offset:off,limit:lim},
  extensions:{persistedQuery:{version:1,sha256Hash:hash}}})});
 if(r.status!==200)return {code:r.status,tracks:null};var j=await r.json();
 return {code:200,tracks:(((j.data||{}).me||{}).library||{}).tracks||null};}catch(x){return {code:0,tracks:null};}}
async function current(){var urls=performance.getEntriesByType('resource').map(function(e){return e.name;})
  .filter(function(u){return u.indexOf('.js')>0&&(u.indexOf('spotifycdn')>0||u.indexOf('scdn')>0);});
 for(var i=0;i<urls.length;i++){try{var t=await (await fetch(urls[i].split('?')[0])).text();
  var m=/fetchLibraryTracks[^0-9a-f]{1,30}([0-9a-f]{64})/.exec(t);if(m)return m[1];}catch(x){}}return null;}
try{var waited=0,looked=false;
 while(true){await wait(%(pace)d);waited++;if(waited>40){post({liked:'failed',code:0});return;}
  if(!window.__likedSeen||!window.__likedSeen.authorization)continue;
  var f=await page(0,1);if(f.tracks)break;
  if(!looked&&waited>6){looked=true;var c=await current();if(c)hash=c;}}
 var out=[],total=0,misses=0;
 while(true){var g=await page(out.length,50);
  if(!g.tracks){if(++misses>8)break;await wait(g.code===429?5000:2000);continue;}
  misses=0;total=g.tracks.totalCount||total;var items=g.tracks.items||[];if(!items.length)break;
  items.forEach(function(it){var t=(it&&it.track&&it.track.data)||{};
   out.push({title:t.name||'',artist:((t.artists||{}).items||[]).map(function(a){return (a.profile||{}).name||'';}).filter(Boolean).join(', '),
    album:(t.albumOfTrack||{}).name||'',duration:Math.floor(((t.duration||{}).totalMilliseconds||0)/1000)});});
  post({liked:'count',n:out.length,total:total});if(out.length>=total)break;}
 post({liked:'songs',songs:out});
}catch(x){post({liked:'failed',code:0});}
})();"""


def forget_profile(keep=None):
    """Nothing of a sign-in stays behind, but the one in use (`keep`). WebView2 may hold a folder a moment after its
    page closes: False then."""
    try:
        names = os.listdir(PROFILE_DIR)
    except OSError:
        return True
    left = False
    for name in names:
        path = os.path.join(PROFILE_DIR, name)
        if path != keep:
            shutil.rmtree(path, ignore_errors=True)
            left = left or os.path.exists(path)
    if keep is None and not left:
        try:
            os.rmdir(PROFILE_DIR)
        except OSError:
            left = True
    return not left


def _text(text, px, weight=QFont.Weight.Medium, alpha=255):
    lab = QLabel(text)
    lab.setFont(look.font(px, weight))
    lab.setStyleSheet("color: rgba(255,255,255,%d); background: transparent;" % alpha)
    lab.setWordWrap(True)
    return lab


class LikedImportSheet(GlassSheet):
    """The chooser, the sign-in, and how it's going."""
    SORRY_SPOTIFY = "Spotify didn't share your liked songs. Try again — or in Spotify, add them to a playlist and " \
                    "import its link."

    def __init__(self, tab):
        super().__init__(tab, max_w=1040, max_h=860)
        self.tab = tab
        self.which = None            # "spotify", "youtube"
        self.phase = "choose"        # "signin", "reading", "matching", "done", "failed"
        self.view = None
        self.engine = None
        self._sweeps = 0
        self._serial = 0
        self._closing = []           # [engine, page, ticks since it settled]: closed sign-ins, until they've let go
        self._sweep = QTimer(self)
        self._sweep.setInterval(700)
        self._sweep.timeout.connect(self._sweep_step)
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_status)
        self._reader = ""
        lay = self.card_lay
        top = QHBoxLayout()
        self.over = QLabel("YOUR MUSIC  ·  LIKED SONGS")
        self.over.setFont(look.label_font(11))
        self.over.setStyleSheet("color: rgba(255,255,255,190); background: transparent;")
        top.addWidget(self.over)
        top.addStretch(1)
        self.back_link = LinkLabel("Close")
        self.back_link.clicked.connect(self.back)
        top.addWidget(self.back_link)
        lay.addLayout(top)
        self.stack = QStackedLayout()
        lay.addLayout(self.stack, 1)
        self.stack.addWidget(self._build_choose())
        self.stack.addWidget(self._build_youtube())
        self.stack.addWidget(self._build_signin())
        self.stack.addWidget(self._build_blocked())
        self.stack.addWidget(self._build_progress())
        forget_profile()             # a sign-in the app was killed during

    # ---- the pages ----
    def _page(self):
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        return w, col

    def _choice(self, col, name, note, slot):
        row = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(_text(name, 16, QFont.Weight.DemiBold))
        words.addWidget(_text(note, 13, QFont.Weight.Normal, 170))
        row.addLayout(words, 1)
        b = Pill("Sign in", style="glass")
        b.clicked.connect(slot)
        row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row)
        col.addSpacing(8)

    def _build_choose(self):
        w, col = self._page()
        title = QLabel("BRING ALL YOUR LIKES")
        title.setFont(look.display_font(40))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        col.addWidget(title)
        col.addWidget(_text("Added to your Liked songs here, and kept as a playlist too.", 14, alpha=190))
        col.addSpacing(10)
        self._choice(col, "Spotify", "Sign in to Spotify here; your Liked Songs are read.", lambda: self.start("spotify"))
        self._choice(col, "YouTube Music", "Sign in here; your Liked music is read.", lambda: self.show_page(1))
        col.addWidget(_text("You sign in on the service's own page. Awesome Downloader never sees your password, and "
                            "the sign-in is wiped as soon as the songs are read.", 12.5, QFont.Weight.Normal, 150))
        col.addStretch(1)
        return w

    def _build_youtube(self):
        w, col = self._page()
        title = QLabel("FROM YOUTUBE MUSIC")
        title.setFont(look.display_font(40))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        col.addWidget(title)
        col.addWidget(_text("Sign in to bring in all the music you've liked. You sign in on Google's own page, your "
                            "password never reaches this app, and the sign-in is wiped as soon as the songs are read.",
                            14, alpha=190))
        go = Pill("Sign in to YouTube Music")
        go.clicked.connect(lambda: self.start("youtube"))
        col.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(16)
        col.addWidget(_text("OR A PLAYLIST", 11, QFont.Weight.DemiBold, 150))
        col.addWidget(_text("Paste the link of an Unlisted or Public playlist.", 13, alpha=170))
        self.link = Field("https://music.youtube.com/playlist?list=…")
        col.addWidget(self.link)
        self.link_go = Pill("Import", style="glass")
        self.link_go.clicked.connect(self._import_link)
        col.addWidget(self.link_go, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        return w

    def _build_signin(self):
        w, col = self._page()
        row = QHBoxLayout()
        self.signin_note = _text("", 13, alpha=190)
        row.addWidget(self.signin_note, 1)
        reload_btn = LinkLabel("Reload")
        reload_btn.clicked.connect(lambda: self.view.reload() if self.view is not None else None)
        row.addWidget(reload_btn)
        col.addLayout(row)
        self.web_box = QVBoxLayout()
        self.web_box.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self.web_box, 1)
        return w

    def _build_blocked(self):
        w, col = self._page()
        title = QLabel("GOOGLE CAN'T SIGN IN HERE")
        title.setFont(look.display_font(36))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        col.addWidget(title)
        col.addWidget(_text("Google blocks sign-in inside apps. Add a password to your Spotify account instead — your "
                            "account and music stay the same.", 14, alpha=190))
        for i, step in enumerate(("Press below and enter your Gmail address.",
                                  "Open Spotify's email and choose a new password.",
                                  "Come back and sign in with your email and that password."), 1):
            col.addWidget(_text("%d.  %s" % (i, step), 14))
        reset = Pill("Set a Spotify password")
        reset.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SPOTIFY_RESET)))
        col.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        again = Pill("Back to sign in", style="outline")
        again.clicked.connect(lambda: self.show_page(2))
        col.addWidget(again, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        return w

    def _build_progress(self):
        w, col = self._page()
        col.addStretch(1)
        self.prog_title = _text("Importing your likes", 26, QFont.Weight.Bold)
        col.addWidget(self.prog_title)
        self.prog_note = _text("", 14, alpha=190)
        col.addWidget(self.prog_note)
        self.bar = ThinSlider()
        self.bar.setRange(0, 1000)
        self.bar.setEnabled(False)
        col.addWidget(self.bar)
        self.prog_hint = _text("You can keep using the app — this carries on.", 12.5, QFont.Weight.Normal, 150)
        col.addWidget(self.prog_hint)
        row = QHBoxLayout()
        self.prog_btn = Pill("Hide", style="outline")
        self.prog_btn.clicked.connect(self._progress_action)
        row.addWidget(self.prog_btn)
        row.addStretch(1)
        col.addLayout(row)
        col.addStretch(2)
        return w

    # ---- going through it ----
    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        self.back_link.setText("Close" if i == 0 or self.phase in ("done", "matching") else "Back")

    def open(self):
        if self.phase == "matching":         # still going in the background: back to its progress
            self.show_page(4)
        elif self.phase == "done" or self.view is None:
            self.phase = "choose"
            self.show_page(0)
        self.open_sheet()

    def back(self):
        """One step back: from a service to the choice of services; otherwise out (the matching carries on, hidden)."""
        if self.phase == "matching":
            self.hide()
        elif self.stack.currentIndex() == 3:
            self.show_page(2)
        elif self.stack.currentIndex() == 0 or self.phase == "done":
            self.close_sheet()
        else:
            self._wipe()
            self.phase = "choose"
            self.show_page(0)

    def close_sheet(self):
        if self.phase != "matching":
            self._wipe()
            self.phase = "choose"
        self.hide()

    def start(self, which):
        """A service's own sign-in, on a page of its own."""
        self._wipe()
        self.which, self.phase = which, "signin"
        self.signin_note.setText(
            "Sign in with your email. Google doesn't allow sign-in inside apps — signed up with it? Press it to see "
            "what to do." if which == "spotify" else
            "Sign in with the Google account you use in YouTube Music. Your liked music is read as soon as you're in.")
        self.show_page(2)
        self._serial += 1              # a folder of its own: the last sign-in's may still be on its way out
        self.engine = webview2.Engine(os.path.join(PROFILE_DIR, "%d" % self._serial), self)
        self.engine.failed.connect(lambda _m: self._fail("This needs Microsoft Edge WebView2, which didn't start."))
        self.engine.start()
        self.view = view = webview2.WebView2Widget(self.engine, private=True)
        self.web_box.addWidget(view, 1)
        view.configure(IsPasswordAutosaveEnabled=False, IsGeneralAutofillEnabled=False, AreDevToolsEnabled=False)
        view.nav_guard = self._guard
        view.webMessage.connect(self._on_message)
        view.loadFinished.connect(lambda _ok: self._page_done())
        view.urlChanged.connect(lambda _u: self._page_done())
        view.newWindowRequested.connect(self._new_window)
        view.processFailed.connect(lambda _m: self._fail("The sign-in page stopped working. Try again."))
        if which == "spotify":
            view.add_startup_script(SPOTIFY_WATCH % {"host": SPOTIFY_HOST, "query": SPOTIFY_QUERY})
        view.load(SPOTIFY_LOGIN if which == "spotify" else YTM_SIGN_IN)
        view.show()

    def _guard(self, url):
        """No app links out of the sign-in, and Google's dead end in Spotify's said plainly."""
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http", "about", "data", "blob"):
            return "scheme"
        if self.which == "spotify" and parts.hostname == GOOGLE_HOST:
            self.show_page(3)
            return "google"
        return None

    def _new_window(self, args):
        """A sign-in that opens its own window stays in this one."""
        try:
            url = str(args.Uri or "")
            args.Handled = True
        except Exception:   # noqa: BLE001
            return
        if url and self.view is not None and not self._guard(url):
            self.view.load(url)

    def _host(self):
        try:
            return urlsplit(self.view.url()).hostname if self.view is not None else None
        except ValueError:
            return None

    def _page_done(self):
        if self.view is None:
            return
        host = self._host()
        if self.phase == "reading":
            # the page moved on under the reader (a redirect after signing in): it starts again in the new one
            if host == (SPOTIFY_HOST if self.which == "spotify" else YTM_HOST):
                self.view.run_js(self._reader)
            return
        if self.phase != "signin":
            return
        if self.which == "spotify" and host == SPOTIFY_HOST:
            self._begin_read(SPOTIFY_READ % {"query": SPOTIFY_QUERY, "hash": LIBRARY_HASH, "pace": 1500},
                             "Signed in. Getting your songs...")
        elif self.which == "youtube" and host == YTM_HOST:
            def signed(yes):
                if yes is True and self.phase == "signin" and self._host() == YTM_HOST:
                    self._begin_read(YTM_READ, "Reading your liked music...")
            self.view.evaluate(YTM_SIGNED_IN, signed)

    def _begin_read(self, script, note):
        # the page stays on show: a hidden one is throttled, and on Spotify it's the page's own first requests that
        # bring what's needed
        self.phase = "reading"
        self._reader = script
        self.signin_note.setText(note)
        self.view.run_js(script)

    def _on_message(self, msg):
        """All a sign-in page can tell this code: a count, the songs, or that it failed."""
        if not isinstance(msg, dict) or self.phase != "reading" or self._host() not in (
                SPOTIFY_HOST if self.which == "spotify" else YTM_HOST, ):
            return
        kind = msg.get("liked")
        if kind == "count":
            n, total = int(msg.get("n") or 0), int(msg.get("total") or 0)
            self.signin_note.setText("Reading your liked songs... %d of %d" % (n, total) if total else
                                     "Reading your liked songs... %d" % n)
        elif kind == "songs":
            songs = msg.get("songs") or []
            if self.which == "spotify":
                if not any(s.get("title") for s in songs):
                    self._fail(self.SORRY_SPOTIFY)
                    return
            elif not songs:
                self._fail("No liked music was found. Sign in with the account you use in YouTube Music.")
                return
            self._import(songs, "Spotify" if self.which == "spotify" else "YouTube Music")
        elif kind == "failed":
            code = msg.get("code")
            self._fail(self.SORRY_SPOTIFY if self.which == "spotify" else
                       "YouTube Music didn't share your liked music%s. Try again, or paste a playlist link instead." % (
                           " (%s)" % code if code else ""))

    def _import_link(self):
        link = self.link.text().strip()
        if "list=" not in link:
            self.tab.status.setText("That doesn't look like a playlist link.")
            return
        self._import([], "YouTube Music", link)

    def _import(self, songs, source, link=""):
        """The songs read: the sign-in is wiped, and they're matched, liked here and kept as "Imported likes"."""
        self._wipe()
        self.phase = "matching"
        self._show_progress("Importing your likes", "Found %d songs. Finding them..." % len(songs) if songs else
                            "Reading the playlist...")
        self.bar.setValue(0)
        brain.value(brain.import_liked, songs, source, link)
        self._poll.start()

    def _poll_status(self):
        s = brain.value(brain.import_status) or {}
        self.prog_note.setText(s.get("note") or "")
        total = int(s.get("total") or 0)
        self.bar.setValue(int(1000 * int(s.get("done") or 0) / total) if total else 0)
        state = s.get("state")
        if state == "done":
            self._poll.stop()
            self.phase = "done"
            self._show_progress("Done", s.get("note") or "")
            self.tab.status.setText(s.get("note") or "")
            self.tab._sync_rail()
        elif state == "failed":
            self._poll.stop()
            self._fail(s.get("note") or "That didn't work.")

    def _show_progress(self, title, note):
        self.prog_title.setText(title)
        self.prog_note.setText(note)
        matching = self.phase == "matching"
        self.bar.setVisible(matching)
        self.prog_hint.setVisible(matching)
        self.prog_btn.setText({"done": "Open Liked songs", "failed": "Try again"}.get(self.phase, "Hide"))
        self.show_page(4)

    def _progress_action(self):
        if self.phase == "done":
            self.close_sheet()
            self.tab.go("songs")
        elif self.phase == "failed":
            self.phase = "choose"
            self.show_page(0)
        else:
            self.hide()

    def _fail(self, why):
        self._wipe()
        self.phase = "failed"
        self._show_progress("That didn't work", why)

    def _wipe(self):
        """Nothing of the sign-in stays behind: the page's data cleared, the page closed, the profile folder gone."""
        view, self.view = self.view, None
        engine, self.engine = self.engine, None
        if view is not None:
            try:
                view.webMessage.disconnect(self._on_message)
            except (RuntimeError, TypeError):
                pass
            core = view.core
            if core is not None:
                try:
                    core.Profile.ClearBrowsingDataAsync()
                except Exception:   # noqa: BLE001 -- older runtimes: the folder's removal below does it
                    logger.debug("Couldn't clear the sign-in's data directly")
            view.nav_guard = None
            view.close_page()
            view.setParent(None)
            view.hide()
        if engine is not None:
            self._closing.append([engine, view, 0])
        self._sweeps = 0
        self._sweep.start()

    def _sweep_step(self):
        """A closed sign-in's environment may only arrive after it closed: it's let go once it has, and WebView2's
        process (which holds the folder) ends when .NET collects it."""
        self._sweeps += 1
        for entry in list(self._closing):
            engine, view, ticks = entry
            if engine.env is None and engine.error is None and self._sweeps < 30:
                continue
            engine.env = None
            entry[2] = ticks + 1
            if ticks >= 2:                     # a page's late callback has come and gone
                self._closing.remove(entry)
                if view is not None:
                    view.deleteLater()
                engine.deleteLater()
        try:
            import System
            System.GC.Collect()
            System.GC.WaitForPendingFinalizers()
        except Exception:   # noqa: BLE001 -- the next sweep, or the next time this opens
            logger.debug("Couldn't collect the sign-in's environment")
        keep = self.engine.user_data_dir if self.engine is not None else None
        if (forget_profile(keep) and not self._closing) or self._sweeps > 60:
            self._sweep.stop()
