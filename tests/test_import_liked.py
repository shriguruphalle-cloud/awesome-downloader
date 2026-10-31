"""Import liked songs: signing in happens on a page of the service's, the reader runs inside that page, and only the
songs come back. Everything here runs against a made-up service on 127.0.0.1 (no network, no real account, made-up
cookies and tokens): its pages, its library, and what it was sent are checked. The secrets must reach the service and
nowhere else: not the code's messages, not the log, not the disk after the sheet closes."""
import _support
from _support import build_window, check, settle

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ui_qt import music_import_liked as liked
from ui_qt import webview2

if not webview2.available():
    print("no WebView2 here: skipped")
    raise SystemExit(0)

from app.core import music_brain as brain  # noqa: E402
from app.core import playlist_import  # noqa: E402
from app.utils import music_library  # noqa: E402

SID = "made-up-sapisid-9f3a71"
TOKEN = "Bearer made-up-token-55c2e0"
CLIENT = "made-up-client-token-77ab"
seen = {"ytm": [], "spotify": []}


def yt_row(n):
    return {"musicResponsiveListItemRenderer": {
        "playlistItemData": {"videoId": "vid%08d" % n},
        "flexColumns": [{"c": {"text": {"runs": [{"text": "Synthetic %d" % n}]}}},
                        {"c": {"text": {"runs": [{"text": "Nobody"}]}}}],
        "fixedColumns": [{"c": {"text": {"runs": [{"text": "3:0%d" % (n % 10)}]}}}],
        "x": {"musicVideoType": "MUSIC_VIDEO_TYPE_ATV"}}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, kind="application/json"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/ytm"):            # YouTube Music, signed in
            self._send("<!doctype html><title>Music</title><script>document.cookie='SAPISID=%s; path=/'</script>" % SID,
                       "text/html")
        elif self.path.startswith("/player"):       # the web player: its own query, carrying its own headers
            self._send("""<!doctype html><title>Player</title><script>
fetch('%s',{method:'POST',headers:{'authorization':'%s','client-token':'%s','app-platform':'WebPlayer'},body:'{}'});
</script>""" % (QUERY, TOKEN, CLIENT), "text/html")
        else:
            self._send("<!doctype html><title>Sign in</title><p>Sign in here", "text/html")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/youtubei"):
            seen["ytm"].append((self.headers.get("Authorization") or "", self.headers.get("Cookie") or "", body))
            if "continuation" in body:
                page = {"contents": [yt_row(3)]}
            else:
                page = {"contents": [yt_row(1), yt_row(2)],
                        "next": {"continuationCommand": {"token": "page-two"}}}
            self._send(json.dumps(page))
        else:
            seen["spotify"].append((self.headers.get("authorization") or "", self.headers.get("client-token") or "", body))
            off = (body.get("variables") or {}).get("offset", 0)
            lim = (body.get("variables") or {}).get("limit", 1)
            all_songs = [{"track": {"data": {
                "name": "Spotty %d" % i, "artists": {"items": [{"profile": {"name": "Nobody"}}]},
                "albumOfTrack": {"name": "Album"}, "duration": {"totalMilliseconds": 180000 + i}}}} for i in range(120)]
            self._send(json.dumps({"data": {"me": {"library": {"tracks": {"totalCount": 120,
                                                                          "items": all_songs[off:off + lim]}}}}}))


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_port
QUERY = BASE + "/query"

# ---- the service's addresses, for the made-up one ----
liked.YTM_HOST = liked.SPOTIFY_HOST = "127.0.0.1"
liked.GOOGLE_HOST = "google.example.test"
liked.YTM_SIGN_IN = BASE + "/ytm"
liked.SPOTIFY_LOGIN = BASE + "/login"
liked.SPOTIFY_QUERY = QUERY

win, tabs = build_window(tabs=("music", "download"), size=(1300, 860))
win.show()
settle(500)
mt = tabs["music"]
messages = []


def wait_for(cond, ms=25000):
    for _ in range(ms // 100):
        settle(100)
        if cond():
            return True
    return False


# stand-ins for the song matching: what comes in is liked and kept, with no network
playlist_import.from_videos = lambda videos: ([{"kind": "track", "id": "yt:" + v["video_id"], "title": v["title"],
                                                "artist": v["channel"], "source": "youtube",
                                                "page_url": "https://music.youtube.test/" + v["video_id"],
                                                "duration": v["duration"]} for v in videos], {})
playlist_import.prefer_songs = lambda tracks, songs, progress, workers=8: tracks


def fake_match(want, progress, workers=8):
    progress(len(want), len(want))
    return [{"kind": "track", "id": "yt:%s" % w["title"].replace(" ", ""), "title": w["title"], "artist": w["artist"],
             "source": "youtube", "page_url": "https://music.youtube.test/" + w["title"].replace(" ", ""),
             "duration": w["duration"]} for w in want]


playlist_import.match = fake_match

sheet = mt.liked_import
check(sheet is not None, "the tab has no liked-songs import")
sheet.open()
settle(400)
check(sheet.isVisible() and sheet.stack.currentIndex() == 0, "the chooser didn't open")

# ---- YouTube Music ----
sheet.start("youtube")
view = sheet.view
view.webMessage.connect(lambda m: messages.append(m))
check(view.private, "the sign-in should be on a private page")
check(view.engine is not mt.player and sheet.engine.user_data_dir.startswith(liked.PROFILE_DIR + os.sep), "its own profile")
check(liked.PROFILE_DIR != os.path.join(os.path.dirname(liked.PROFILE_DIR), "wv2"), "not the Browser tab's profile")
check(wait_for(lambda: sheet.phase in ("matching", "done", "failed")), "the read never finished: %s" % sheet.phase)
check(wait_for(lambda: sheet.phase in ("done", "failed")), "the matching never finished: %s" % sheet.phase)
check(sheet.phase == "done", "it failed: %s" % sheet.prog_note.text())
print("YouTube Music: %d pages asked for, in the page" % len(seen["ytm"]))
check(len(seen["ytm"]) == 2, "both pages should be read: %d" % len(seen["ytm"]))
auth, cookie, body = seen["ytm"][0]
check(auth.startswith("SAPISIDHASH ") and "_" in auth and SID not in auth, "the sign-in hash: %s" % auth[:14])
check(SID in cookie, "the page should send its own cookie to the service")
check(body.get("browseId") == "VLLM", "not the Liked music list")
check(seen["ytm"][1][2].get("continuation") == "page-two", "the second page wasn't asked for by its token")
ids = {t["id"] for t in music_library.saved("songs")}
check({"yt:vid00000001", "yt:vid00000002", "yt:vid00000003"} <= ids, "the songs weren't liked: %s" % sorted(ids)[:4])
pl = [p for p in music_library.playlists() if p.get("title") == "Imported likes"]
check(pl and len(pl[0]["tracks"]) == 3, "not kept as a playlist")
print("three songs liked and kept as a playlist; the note says: %s" % sheet.prog_note.text())

# wiped: the page is gone and so is its folder
check(sheet.view is None and sheet.engine is None, "the sign-in page is still open")
check(wait_for(lambda: not os.path.exists(liked.PROFILE_DIR), 30000), "the sign-in's folder wasn't removed")
print("the sign-in's page and folder are gone")

# ---- Spotify, signing in on another page first ----
sheet.close_sheet()
sheet.open()
settle(300)
liked.SPOTIFY_LOGIN = BASE + "/login"
sheet.start("spotify")
view = sheet.view
view.webMessage.connect(lambda m: messages.append(m))
check(wait_for(lambda: view.url().endswith("/login")), "the sign-in page didn't load")
settle(800)
check(sheet.phase == "signin" or sheet.which == "spotify", "phase %s" % sheet.phase)
# the made-up service uses one host for sign-in and player; signing in is going to the player
view.load(BASE + "/player")
check(wait_for(lambda: sheet.phase in ("done", "failed"), 40000), "Spotify never finished: %s" % sheet.phase)
check(sheet.phase == "done", "it failed: %s" % sheet.prog_note.text())
reads = [s for s in seen["spotify"] if s[2].get("operationName") == "fetchLibraryTracks"]
check(reads and all(a == TOKEN and c == CLIENT for a, c, _b in reads), "the player's headers weren't sent with the read")
check(max(b["variables"]["offset"] for _a, _c, b in reads) == 100, "not read 50 at a time")
ids = {t["id"] for t in music_library.saved("songs")}
check("yt:Spotty0" in ids and "yt:Spotty119" in ids, "not every song came across")
print("Spotify: %d asks; 120 songs across" % len(reads))

# ---- Google's dead end, an app link, a failure ----
sheet.close_sheet()
sheet.open()
settle(300)
sheet.start("spotify")
check(sheet._guard("https://google.example.test/signin") == "google", "Google's page should be stopped in Spotify's")
check(sheet.stack.currentIndex() == 3, "what to do instead should be shown")
check(sheet._guard("spotify:track:0") == "scheme", "no app links out of the sign-in")
check(sheet._guard("https://accounts.spotify.test/") is None, "the service's own pages load")
sheet.show_page(2)
print("Google's dead end is explained; no app links out")
sheet.phase = "reading"
sheet.view._url = BASE + "/player"
sheet._on_message({"liked": "failed", "code": 500})
check(sheet.phase == "failed" and sheet.view is None, "a failed read should wipe and say so")
check("Spotify didn't share" in sheet.prog_note.text(), "what went wrong isn't said")
sheet._progress_action()
check(sheet.phase == "choose" and sheet.stack.currentIndex() == 0, "Try again goes back to the choice")
sheet.close_sheet()

# nothing of the secrets reached the code, the log or the disk
said = json.dumps(messages)
for secret in (SID, TOKEN, CLIENT, "made-up"):
    check(secret not in said, "a secret reached the code: %s" % secret[:8])
print("%d messages came back from the page; none carries a cookie or a token" % len(messages))
check(wait_for(lambda: not os.path.exists(liked.PROFILE_DIR), 30000), "the sign-in's folder wasn't removed")
for root, _dirs, files in os.walk(_support.STATE_DIR):
    for name in files:
        path = os.path.join(root, name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        for secret in (SID, TOKEN, CLIENT):
            check(secret.encode() not in data, "a secret was stored in %s" % name)
print("none of them is anywhere in the app's folder, the log included")

# the playlist link needs no sign-in
sheet.open()
sheet.show_page(1)
playlist_import.read = lambda link: {"tracks": [], "songs": None, "items": [{"title": "Linked one", "artist": "Nobody"}]}
sheet.link.edit.setText("https://music.youtube.test/playlist?list=madeup")
sheet.link_go.click()
check(wait_for(lambda: sheet.phase in ("done", "failed")), "the link never finished")
check(sheet.phase == "done", "the link failed: %s" % sheet.prog_note.text())
check("yt:Linkedone" in {t["id"] for t in music_library.saved("songs")}, "the link's song wasn't liked")
print("a playlist's link needs no sign-in")
sheet.close_sheet()
server.shutdown()
win.close()
settle(500)
print("IMPORT LIKED OK")
