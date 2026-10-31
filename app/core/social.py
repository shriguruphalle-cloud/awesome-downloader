"""Awesome Music's own backend, from the PC: the same account as on the phone app.

Signing in is an email and the 4-digit code sent to it; after that the session
token is kept in the app's encrypted store (secure_store) and sent as
"Authorization: Bearer ..." with every request. Through it: you (your
@username, name, picture, whether friends see what you play), people (search,
profiles, follow), what friends are playing, chats with songs in them, and
jams -- listening together. The backend is a Cloudflare Worker (the Android
project's server/); the phone app talks to the same one, so one account works
on both.

Everything here blocks: call it from a worker thread, never the UI thread.
Errors come back as SocialError with words to show as they are.
"""
import json
import os
import platform
import threading
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from ..logging_setup import get_logger
from ..utils import secure_store

logger = get_logger("social")

LIVE_BASE = "https://awesome-music-api.shriguruphalle.workers.dev"
# AWESOME_MUSIC_API points the app at another backend -- the tests' local one (wrangler dev on :8787)
BASE = (os.environ.get("AWESOME_MUSIC_API") or LIVE_BASE).rstrip("/")
ACCOUNT_PATH = os.path.join(config.APPDATA_DIR, "music_account.json")
TIMEOUT = 20


class SocialError(Exception):
    """The backend said no, or couldn't be reached. `status` 0 = no connection."""

    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


# ------------------------------------------------------------------ the account, kept
_lock = threading.Lock()
_state = None                 # {"token", "me"} once read
_on_signed_out = []           # callbacks: the server forgot the session (signed out elsewhere, deleted)


def _load():
    global _state
    if _state is None:
        try:
            _state = secure_store.read_json(ACCOUNT_PATH) if os.path.exists(ACCOUNT_PATH) else {}
        except Exception:   # noqa: BLE001 -- unreadable (another Windows account): signed out
            logger.warning("Couldn't read the music account; signed out", exc_info=True)
            _state = {}
    return _state


def _save():
    try:
        if _state and _state.get("token"):
            secure_store.write_json(ACCOUNT_PATH, _state)
        elif os.path.exists(ACCOUNT_PATH):
            os.remove(ACCOUNT_PATH)
    except OSError:
        logger.exception("Couldn't save the music account")


def token():
    with _lock:
        return _load().get("token")


def signed_in():
    return bool(token())


def me():
    """You, as last heard from the server (None when signed out)."""
    with _lock:
        return dict(_load().get("me") or {}) or None


def on_signed_out(fn):
    _on_signed_out.append(fn)


def _keep_me(user):
    with _lock:
        if user is not None and _load().get("token"):
            _state["me"] = user
            _save()


def _clear():
    global _state
    with _lock:
        _state = {}
        _save()
    for fn in list(_on_signed_out):
        try:
            fn()
        except Exception:   # noqa: BLE001
            logger.debug("signed-out listener failed", exc_info=True)


# ------------------------------------------------------------------ requests
APP_VERSION_TAG = "pc-%s" % config.APP_VERSION     # told apart from the phone's on the owner's dashboard
foreground = False                                  # the window's in use (the tab keeps this up to date)


def call(method, path, body=None, auth=True, headers=None):
    """One request; the answer's JSON (a dict), or SocialError. `headers`: more of them, never logged."""
    headers = dict(headers or {})
    data = None
    headers.update({"Accept": "application/json", "User-Agent": "AwesomeDownloader/%s" % config.APP_VERSION,
                    "X-App-Version": APP_VERSION_TAG, "X-App-State": "foreground" if foreground else "background"})
    if body is not None or method not in ("GET", "DELETE"):
        data = json.dumps(body if body is not None else {}).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    tok = token() if auth else None
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    return _send(req, auth and bool(tok))


def put_bytes(path, data, content_type):
    headers = {"Content-Type": content_type, "User-Agent": "AwesomeDownloader/%s" % config.APP_VERSION}
    tok = token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    return _send(urllib.request.Request(BASE + path, data=data, headers=headers, method="PUT"), bool(tok))


def _send(req, authed):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            obj = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            obj = {}
        if e.code == 401 and authed:
            _clear()
        extra = {k: v for k, v in obj.items() if k != "error"} if isinstance(obj, dict) else {}
        raise SocialError(e.code, (obj.get("error") if isinstance(obj, dict) else None)
                          or "The server answered %d." % e.code, extra) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SocialError(0, "Couldn't reach Awesome Music's server. Check your connection.") from None
    try:
        obj = json.loads(text or "{}")
    except ValueError:
        raise SocialError(502, "The server's answer wasn't understood.") from None
    return obj if isinstance(obj, dict) else {}


def socket_url(path):
    """The address of a live connection (a chat open on screen, a jam)."""
    return BASE.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + path


def avatar_url(user):
    """A person's picture (None when they have none); the address changes when the picture does."""
    if not user or not user.get("avatar_at") or not user.get("id"):
        return None
    return "%s/v1/avatars/%s?v=%s" % (BASE, user["id"], user["avatar_at"])


# ------------------------------------------------------------------ signing in
def start(email):
    """Sends the code. SocialError says why not ("A code was just sent...", not an email...)."""
    return call("POST", "/v1/auth/start", {"email": (email or "").strip()}, auth=False)


def verify(email, code):
    """The code typed in: signed in. Returns True when it's a new account (it still needs a username)."""
    r = call("POST", "/v1/auth/verify", {"email": (email or "").strip(), "code": (code or "").strip(),
                                         "device": "Windows PC (%s)" % platform.node()[:40]}, auth=False)
    tok = r.get("token")
    if not tok:
        raise SocialError(500, "Sign-in didn't finish. Try again.")
    global _state
    with _lock:
        _state = {"token": tok, "me": r.get("user") or {}}
        _save()
    return bool(r.get("new_account"))


def refresh_me():
    u = call("GET", "/v1/me").get("user")
    _keep_me(u)
    return u


def update_me(handle=None, name=None, share_activity=None, show_artists=None, show_likes=None, private=None):
    b = {}
    if show_likes is not None:
        b["show_likes"] = bool(show_likes)
    if private is not None:
        b["private"] = bool(private)
    if handle is not None:
        b["handle"] = handle.strip().lstrip("@").lower()
    if name is not None:
        b["name"] = name.strip()
    if share_activity is not None:
        b["share_activity"] = bool(share_activity)
    if show_artists is not None:
        b["show_artists"] = bool(show_artists)
    u = call("PATCH", "/v1/me", b).get("user")
    _keep_me(u)
    return u


def set_picture(jpeg):
    u = put_bytes("/v1/me/avatar", jpeg, "image/jpeg").get("user")
    _keep_me(u)
    return u


def remove_picture():
    u = call("DELETE", "/v1/me/avatar").get("user")
    _keep_me(u)
    return u


def sign_out():
    try:
        call("POST", "/v1/auth/logout")
    except SocialError:
        pass                    # signed out here either way
    _clear()


def delete_account():
    call("DELETE", "/v1/me")
    _clear()


# ------------------------------------------------------------------ people
def presence(track):
    """What you're playing now, for friends (when you share it). None: nothing."""
    return call("POST", "/v1/presence", {"track": track})


def friends():
    """Who you follow and who follows you, each with what they're playing now (when they share it)."""
    return call("GET", "/v1/friends").get("people") or []


def search_people(q):
    q = (q or "").strip()
    if len(q.lstrip("@")) < 2:
        return []
    return call("GET", "/v1/users/search?q=" + urllib.parse.quote(q)).get("users") or []


def profile(handle):
    """{"user": {..., followers, following, now}, "playlists": [...], "artists": [...]}"""
    return call("GET", "/v1/users/" + urllib.parse.quote((handle or "").lstrip("@")))


def follow(user_id, on=True):
    return call("POST" if on else "DELETE", "/v1/follow/" + user_id)


def remove_follower(user_id):
    """Someone who follows you, made not to (they aren't told)."""
    return call("DELETE", "/v1/followers/" + user_id)


def suggest():
    """People you may know, each with "why": follows_you, followed_by:handle[:n], popular:n or new."""
    return call("GET", "/v1/users/suggest").get("people") or []


def follow_list(handle, which):
    """Who follows `handle` ("followers"), or whom they follow ("following")."""
    return call("GET", "/v1/users/%s/%s" % (urllib.parse.quote(handle), which)).get("people") or []


def playlist_of(handle, playlist_id):
    """One of someone's playlists, songs and all."""
    return call("GET", "/v1/users/%s/playlists/%s" % (urllib.parse.quote(handle),
                                                      urllib.parse.quote(playlist_id, safe=""))).get("playlist")


def new_followers(since):
    """{"people": [...], "now": ms}: who started following you since `since`."""
    return call("GET", "/v1/followers/new?since=%d" % int(since or 0))


def top_listener():
    """{"last_month", "champion"}: last month's top listener among you and the people you follow."""
    return call("GET", "/v1/listeners/top")


def add_listening(seconds):
    """Seconds of music heard, for your profile and the month's top listener."""
    return call("POST", "/v1/listen", {"seconds": int(seconds)})


def latest_announcement(since):
    return call("GET", "/v1/announcements/latest?since=%d" % int(since or 0))


# ------------------------------------------------------------------ the library, in step with the phone
def sync_library(taste):
    """One sync of likes, playlists, history and taste picks (library_sync does the comparing; the later change
    of an item wins, as between two phones). True when something here changed."""
    from . import library_sync
    changed = False
    for _round in range(6):
        prep = library_sync.prepare(taste, {})
        res = call("POST", "/v1/sync", {"since": prep["since"], "changes": prep["changes"]})
        got = library_sync.apply(taste, res.get("docs") or [], res.get("rev") or 0)
        changed = changed or bool(got.get("changed"))
        # the first time the server's library comes in before this PC's goes up; a long one comes in pages
        if not prep.get("first") and not res.get("more"):
            break
    return changed


def forget_sync():
    from . import library_sync
    library_sync.forget()


# ------------------------------------------------------------------ chats
def chats():
    return call("GET", "/v1/chats").get("chats") or []


def chat(chat_id):
    return call("GET", "/v1/chats/" + chat_id).get("chat")


def dm(user_id):
    return call("POST", "/v1/chats", {"kind": "dm", "with": user_id}).get("chat")


def new_group(title, member_ids):
    return call("POST", "/v1/chats", {"kind": "group", "title": title, "members": list(member_ids)}).get("chat")


def rename_group(chat_id, title):
    return call("PATCH", "/v1/chats/" + chat_id, {"title": title}).get("chat")


def add_members(chat_id, member_ids):
    return call("POST", "/v1/chats/%s/members" % chat_id, {"add": list(member_ids)}).get("chat")


def leave_chat(chat_id):
    return call("POST", "/v1/chats/%s/leave" % chat_id)


def messages(chat_id, before=0, after=0):
    q = "?before=%d" % before if before else ("?after=%d" % after if after else "")
    return call("GET", "/v1/chats/%s/messages%s" % (chat_id, q)).get("messages") or []


def send_text(chat_id, text, reply_to=None):
    body = {"kind": "text", "text": text}
    if reply_to:
        body["reply_to"] = reply_to
    return call("POST", "/v1/chats/%s/messages" % chat_id, body).get("message")


def edit_text(chat_id, message_id, text):
    return call("POST", "/v1/chats/%s/edit" % chat_id, {"id": message_id, "text": text})


def unsend(chat_id, message_id):
    return call("POST", "/v1/chats/%s/unsend" % chat_id, {"id": message_id})


def send_item(chat_id, item, note=""):
    """A song, album, artist or playlist, as a card that plays when clicked."""
    return call("POST", "/v1/chats/%s/messages" % chat_id,
                {"kind": "item", "item": shareable(item), "text": note}).get("message")


def send_jam(chat_id, code):
    return call("POST", "/v1/chats/%s/messages" % chat_id, {"kind": "jam", "item": {"id": code}}).get("message")


def mark_read(chat_id):
    return call("POST", "/v1/chats/%s/read" % chat_id)


# ------------------------------------------------------------------ jams
def new_jam(chat_id=None):
    return call("POST", "/v1/jams", {"chat": chat_id} if chat_id else {}).get("jam")


def jam(code):
    return call("GET", "/v1/jams/" + code.strip().upper()).get("jam")


def live_jams():
    return call("GET", "/v1/jams/live").get("jams") or []


# ------------------------------------------------------------------ what's shared
_ITEM_KEYS = ("kind", "id", "title", "artist", "duration", "artwork", "source", "page_url", "ext", "album",
              "album_browse", "artist_browse", "browse_id", "year", "subtitle", "type", "stream", "license",
              "album_id")


def shareable(item):
    """A song (or album, artist, playlist) as it travels: the fields the server keeps, nothing of this PC's
    (a song from a folder on this PC can't be played anywhere else, so it goes without its file)."""
    out = {}
    for k in _ITEM_KEYS:
        v = (item or {}).get(k)
        if v not in (None, ""):
            out[k] = v
    if out.get("source") == "local":
        out.pop("stream", None)
        out.pop("page_url", None)
    if isinstance((item or {}).get("tracks"), list):
        out["tracks"] = [shareable(t) for t in item["tracks"][:300]]
    out.setdefault("kind", "track")
    return out
