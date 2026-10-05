"""Captures the real Awesome Downloader UI for the showcase video.

The app is built exactly as main_qt.py builds it, in a throwaway test profile
(tests/_support.py points LOCALAPPDATA and USERPROFILE at a temp folder, so
nothing of yours is read or written), filled with made-up content (synth.py)
and driven through each state the video shows. Every capture is the app's
own pixels at 2x (a 1280x720 window -> 2560x1440 PNG); the Browser tab's
page is captured from WebView2 itself and set into place.

Writes into video/public/captures/:
  <name>.png       one per state
  torrent-live.mp4 the Torrent tab's live graphs, 60 fps
  meta.json        each capture's size and the pixel rect of every widget the
                   video points at (callouts, cursor targets, zooms)

Run:  npm run capture   (from video/)
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
from ctypes import wintypes

os.environ["QT_SCALE_FACTOR"] = "2"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "video", "public", "captures")
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import _support  # noqa: E402  -- isolates every state file in a temp folder
from _support import STATE_DIR, build_window, qapp, stub_network  # noqa: E402

app = qapp()

from PySide6.QtCore import QAbstractNativeEventFilter, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget  # noqa: E402

import synth  # noqa: E402

FFMPEG = shutil.which("ffmpeg") or os.path.join(ROOT, "vendor", "ffmpeg.exe")
NEUTRAL_HOME = r"C:\Users\You"
META = {}
LOG = []


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def settle(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.008)


# ---- a capture window may be bigger than the screen ------------------------------------
class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


LOCK = {"hwnd": None, "size": None}


class Unclamp(QAbstractNativeEventFilter):
    """Lets the capture window be bigger than the screen, and keeps it at its
    capture size: Windows fits an oversized window back onto the work area
    on some changes (it came out 960x516 mid-run)."""
    def nativeEventFilter(self, et, msg):
        m = wintypes.MSG.from_address(int(msg))
        if m.message == 0x0024:      # WM_GETMINMAXINFO
            info = MINMAXINFO.from_address(m.lParam)
            info.ptMaxTrackSize.x = info.ptMaxTrackSize.y = 8000
            info.ptMaxSize.x = info.ptMaxSize.y = 8000
            return True, 0
        if m.message == 0x0046 and LOCK["hwnd"] and m.hWnd == LOCK["hwnd"]:      # WM_WINDOWPOSCHANGING
            pos = WINDOWPOS.from_address(m.lParam)
            if not pos.flags & 0x0001:       # SWP_NOSIZE
                pos.cx, pos.cy = LOCK["size"]
        return False, 0


_unclamp = Unclamp()
app.installNativeEventFilter(_unclamp)


# ---- no private path in any picture ------------------------------------------------------
def scrub():
    """Every path shown on screen points into the temp test profile, whose
    name includes this PC's user folder; shown as a neutral one for the
    picture only. Returns what to put back (the app still uses the real one)."""
    pat = re.compile(re.escape(STATE_DIR), re.IGNORECASE)
    changed = []
    for top in QApplication.topLevelWidgets():
        for w in [top] + top.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QLabel)) and STATE_DIR.lower() in (w.text() or "").lower():
                changed.append((w, w.text()))
                blocked = w.blockSignals(True)
                w.setText(pat.sub(lambda _m: NEUTRAL_HOME, w.text()))
                w.blockSignals(blocked)
    return changed


def unscrub(changed):
    for w, text in changed:
        blocked = w.blockSignals(True)
        w.setText(text)
        w.blockSignals(blocked)


def ensure_size():
    if win.size().toTuple() != (1280, 720):
        log("  (window was %s -- restored)" % (win.size().toTuple(),))
        win.resize(1280, 720)
        settle(0.6)


# ---- grabbing and bookkeeping ---------------------------------------------------------
def grab(widget):
    img = widget.grab().toImage()
    img.setDevicePixelRatio(1.0)
    return img


def px_rect(widget, root):
    """`widget`'s rect in `root`'s capture, in pixels."""
    dpr = root.devicePixelRatioF()
    if widget.window() is root.window() and root.isAncestorOf(widget) or widget is root:
        o = widget.mapTo(root, QPoint(0, 0))
    else:
        o = widget.mapToGlobal(QPoint(0, 0)) - root.mapToGlobal(QPoint(0, 0))
    return [round(o.x() * dpr), round(o.y() * dpr), round(widget.width() * dpr), round(widget.height() * dpr)]


def save(name, img, rects=None, extra=None):
    path = os.path.join(OUT, name + ".png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    META[name] = {"w": img.width(), "h": img.height(), "rects": rects or {}}
    if extra:
        META[name].update(extra)
    log("  saved %s (%dx%d)" % (name, img.width(), img.height()))


def shot(name, rect_widgets=None, extra=None):
    ensure_size()
    changed = scrub()
    settle(0.25)
    img = grab(win)
    unscrub(changed)
    rects = {k: px_rect(w, win) for k, w in (rect_widgets or {}).items() if w is not None and w.isVisible()}
    save(name, img, rects, extra)
    return img


def pil_image(name, w=1280, h=720):
    return synth.scene(name, w, h)


# ---- made-up content ----------------------------------------------------------------------
VIDEOS = [
    # (scene, title, channel, seconds, sizes by height)
    ("aurora", "Aurora over the fjord — 4K timelapse", "Northern Frames", 754,
     {2160: 1_740_000_000, 1440: 820_000_000, 1080: 412_000_000, 720: 198_000_000, 480: 96_000_000}),
    ("city", "City lights at night — drone flight", "Skyline Studio", 512,
     {1080: 286_000_000, 720: 141_000_000, 480: 70_000_000}),
    ("coffee", "How to brew pour-over coffee", "Slow Mornings", 437,
     {1080: 233_000_000, 720: 118_000_000, 480: 61_000_000}),
    ("dunes", "Desert dunes at golden hour", "Wide Open", 368,
     {1440: 402_000_000, 1080: 214_000_000, 720: 109_000_000}),
    ("ocean", "Ocean waves — slow motion", "Blue Hours", 1260,
     {1080: 655_000_000, 720: 322_000_000, 480: 160_000_000}),
    ("forest", "Morning walk through a pine forest", "Quiet Trails", 903,
     {1080: 470_000_000, 720: 231_000_000}),
    ("alpine", "Alpine lakes from above", "Northern Frames", 645,
     {2160: 1_210_000_000, 1080: 360_000_000, 720: 170_000_000}),
    ("night", "Moonrise over the ridge", "Northern Frames", 299,
     {1080: 160_000_000, 720: 80_000_000}),
]
SLUG = lambda title: re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32]  # noqa: E731


def video_url(title):
    return "https://clips.example/watch/" + SLUG(title)


# ============================================================================================
log("state folder: %s" % STATE_DIR)
os.makedirs(OUT, exist_ok=True)

# Browser profile: made-up shortcuts, bookmarks and history (no real sites).
from app.utils import browser_data, download_history  # noqa: E402

for s in browser_data.load_shortcuts():
    browser_data.remove_shortcut(s.get("url"))
SITES = [("Clips", "https://clips.example/"), ("Docs", "https://docs.example/"), ("News", "https://news.example/"),
         ("Recipes", "https://recipes.example/"), ("Weather", "https://weather.example/")]
for title, url in SITES[:4]:
    browser_data.add_shortcut(url, title)
for title, url in [("Clips", "https://clips.example/"), ("Docs", "https://docs.example/"),
                   ("News", "https://news.example/"), ("Recipes", "https://recipes.example/"),
                   ("Weather", "https://weather.example/"), ("Music", "https://music.example/"),
                   ("Maps", "https://maps.example/"), ("Photos", "https://photos.example/")]:
    browser_data.add_bookmark(url, title)
for _ in range(3):
    for title, url in SITES:
        browser_data.add_history_entry(url, title)

# The demo site the browser opens: our own page and clip, served by WebView2
# from a local folder under the made-up host clips.example.
SITE_DIR = os.path.join(STATE_DIR, "clips-site")
os.makedirs(SITE_DIR, exist_ok=True)
for i, (scene_name, *_rest) in enumerate(VIDEOS):
    pil_image(scene_name, 640, 360).save(os.path.join(SITE_DIR, scene_name + ".jpg"), quality=90)
pil_image("aurora", 1920, 1080).save(os.path.join(SITE_DIR, "poster.jpg"), quality=92)
clip = os.path.join(SITE_DIR, "aurora.mp4")
subprocess.run([FFMPEG, "-v", "error", "-y", "-loop", "1", "-i", os.path.join(SITE_DIR, "poster.jpg"),
                "-vf", "zoompan=z='1.0+0.0006*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=30",
                "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", clip], check=True)
shutil.copy(os.path.join(HERE, "clips-site", "watch.html"), os.path.join(SITE_DIR, "watch.html"))

win, tabs = build_window(size=(1280, 720))
win.move(16, 16)
win.resize(1280, 720)
settle(0.5)
LOCK["hwnd"] = int(win.winId())
LOCK["size"] = (round(1280 * win.devicePixelRatioF()), round(720 * win.devicePixelRatioF()))
vt, tt, it, bt, dt, ht = (tabs[k] for k in ("video", "torrent", "images", "browser", "download", "history"))
calls = stub_network(vt)
settle(3)
log("window %s at dpr %.1f" % (win.size().toTuple(), win.devicePixelRatioF()))


def show_tab(tab):
    win.tabs.setCurrentWidget(tab)
    settle(0.9)


def to_top(tab):
    from PySide6.QtWidgets import QScrollArea
    for area in tab.findChildren(QScrollArea):
        area.verticalScrollBar().setValue(0)
    settle(0.2)


def clean_video_tab():
    """No leftover queue, scrolled to the top, the first video in the form."""
    if vt._queue_strips:
        try:
            vt.clear_queue()
        except Exception as exc:  # noqa: BLE001
            log("  clear queue: %s" % exc)
        settle(0.8)
    fill_form(0)
    to_top(vt)


def nav_rects():
    return {"tab_%s" % b.text().lower(): b for b in win._tab_buttons} | {
        "wordmark": win._wordmark, "island": win._island, "tray": win._action_tray,
        "settings_btn": win.settings_btn, "theme_btn": win.theme_btn, "update_btn": win.update_btn,
        "about_btn": win.about_btn, "donate_btn": win.donate_btn}


def fill_form(idx, url=None):
    scene_name, title, channel, secs, sizes = VIDEOS[idx]
    url = url or video_url(title)
    vt._last_fetch_url = url
    vt._suppress_url_change = True
    try:
        vt.url_entry.setText(url)
    finally:
        vt._suppress_url_change = False
    vt._on_fetch_done(title, channel, secs, dict(sizes), pil_image(scene_name), url + ".jpg", False)
    settle(0.4)


def video_rects():
    r = nav_rects()
    r.update({"url_entry": vt.url_entry, "fetch_btn": vt.fetch_btn, "info_card": vt.info_card,
              "res_combo": vt.res_combo, "format_combo": vt.format_combo, "video_radio": vt.video_radio,
              "audio_radio": vt.audio_radio, "range_check": vt.range_check, "download_btn": vt.download_btn,
              "queue_btn": vt.queue_btn, "thumb": vt.thumb_label, "title": vt.info_title_label,
              "bitrate_combo": getattr(vt, "bitrate_combo", None), "start_entry": vt.start_entry,
              "end_entry": vt.end_entry})
    return r


# ---- 1. Video tab -----------------------------------------------------------------------------
log("video tab")
show_tab(vt)
shot("video-empty", video_rects())

vt._suppress_url_change = True
vt.url_entry.setText(video_url(VIDEOS[0][1]))
vt._suppress_url_change = False
vt.on_fetch()
settle(0.6)
shot("video-fetching", video_rects())

fill_form(0)
shot("video-fetched", video_rects())

vt.res_combo.showPopup()
settle(0.6)
popup = vt.res_combo.view().window()
save("video-res-popup", grab(popup), {}, {"at": px_rect(popup, win)})
vt.res_combo.hidePopup()
settle(0.3)

vt.audio_radio.setChecked(True)
settle(0.5)
shot("video-audio", video_rects())
vt.video_radio.setChecked(True)
settle(0.4)

vt.range_check.setChecked(True)
vt.start_entry.setText("1:20")
vt.end_entry.setText("4:05")
settle(0.4)
shot("video-clip", video_rects())
vt.range_check.setChecked(False)
settle(0.3)

# The queue: several links pasted at once stack as cards.
for i in (1, 2, 3, 4, 5):
    fill_form(i)
    vt.on_queue()
    settle(0.5)
settle(1.0)
shot("video-queue", video_rects() | {"queue_%d" % i: s for i, s in enumerate(vt._queue_strips[:5])})

# ---- 2. Download tab: real jobs from the Video tab (the download itself is stubbed) -------------
log("download tab")
for strip in list(vt._queue_strips):
    vt._remove_queue_strip(strip) if hasattr(vt, "_remove_queue_strip") else None
settle(0.4)
jobs = []
for i in (0, 1, 3, 4, 6):
    fill_form(i)
    if i == 1:
        vt.audio_radio.setChecked(True)
        settle(0.2)
    vt.on_download()
    settle(0.5)
    vt.video_radio.setChecked(True)
jobs = sorted(dt._cards)
log("  jobs %s, calls %d downloads" % (jobs, len(calls["downloads"])))
progress = {jobs[0]: (64, "1.1 GB / 1.7 GB  •  18.4 MB/s  •  ETA 0:34"),
            jobs[1]: (38, "52.3 MB / 138 MB  •  6.2 MB/s  •  ETA 0:14"),
            jobs[2]: (87, "349 MB / 402 MB  •  11.9 MB/s  •  ETA 0:04")}
for job, (pct, detail) in progress.items():
    dt.set_started(job)
    dt.update_progress(job, pct, detail)
settle(1.6)
show_tab(dt)
settle(1.2)
shot("download", nav_rects() | {"card_%d" % n: dt._cards[j] for n, j in enumerate(jobs) if j in dt._cards})

# ---- 3. Images tab ---------------------------------------------------------------------------------
log("images tab")
from ui_qt import images_tab as images_mod  # noqa: E402

GALLERY = ["dunes", "sunset", "ocean", "alpine", "forest", "city", "night", "aurora"]
_gallery_imgs = {"https://photos.example/p/golden-hour/%d.jpg" % i: synth.scene(n, 1080, 1350)
                 for i, n in enumerate(GALLERY)}
images_mod.downloader.fetch_thumbnail_image = lambda url, size=None: (
    _gallery_imgs[url].copy().resize(size) if size else _gallery_imgs[url].copy())
show_tab(it)
it.url_entry.setText("https://photos.example/p/golden-hour") if hasattr(it, "url_entry") else None
it._on_fetch_done("Golden hour — eight frames", [{"url": u} for u in _gallery_imgs], 0)
settle(2.5)
shot("images-all", nav_rects() | {"tile_%d" % i: t for i, t in enumerate(it.tiles)} |
     {"download_btn": getattr(it, "download_btn", None)})
for i in (2, 5):
    it.tiles[i].setChecked(False)
settle(0.8)
shot("images", nav_rects() | {"tile_%d" % i: t for i, t in enumerate(it.tiles)} |
     {"download_btn": getattr(it, "download_btn", None)})

# ---- 4. History tab ------------------------------------------------------------------------------
log("history tab")
from ui_qt import history_tab as history_mod  # noqa: E402

hist_dir = os.path.join(STATE_DIR, "Downloads")
os.makedirs(hist_dir, exist_ok=True)
_hist_thumbs = {}
HISTORY = [("video", 0, 412_000_000), ("audio", 1, 9_800_000), ("video", 2, 233_000_000), ("video", 3, 214_000_000),
           ("torrent", None, 2_800_000_000), ("video", 4, 655_000_000), ("audio", 5, 21_300_000), ("video", 6, 360_000_000)]
for kind, idx, size in reversed(HISTORY):
    if idx is None:
        title, scene_name, ext = "Sintel (2010) — Blender open movie, 4K", "night", ".mkv"
    else:
        scene_name, title = VIDEOS[idx][0], VIDEOS[idx][1]
        ext = ".mp3" if kind == "audio" else ".mp4"
    path = os.path.join(hist_dir, SLUG(title) + ext)
    with open(path, "wb") as f:
        f.write(b"\0" * 1024)
    _hist_thumbs[path] = synth.scene(scene_name, 640, 360)
    download_history.add_entry(kind, title, path, hist_dir, size)
history_mod.thumbnails.get_preview_image = lambda path, kind, size=None: (
    _hist_thumbs.get(path).copy().resize(size) if size and path in _hist_thumbs else _hist_thumbs.get(path))
show_tab(ht)
if hasattr(ht, "refresh"):
    ht.refresh()
settle(2.0)
shot("history", nav_rects())
rows = [r for r in ht.findChildren(QWidget) if type(r).__name__ == "_Row" and r.isVisible()]
log("  history rows: %d" % len(rows))
for r in rows[:3]:
    r.check.click()
    settle(0.2)
settle(1.2)
shot("history-selected", nav_rects() | {"bar": getattr(ht, "bar", None)} |
     {"row_%d" % i: r for i, r in enumerate(rows[:6])})

# ---- 5. Torrent tab ---------------------------------------------------------------------------------
log("torrent tab")
from ui_qt import torrent_tab as torrent_mod  # noqa: E402
from ui_qt.widgets import speed_graph  # noqa: E402

KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3
clock = {"now": 1000.0}
fake_time = types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time, sleep=time.sleep)
speed_graph.time = fake_time
torrent_mod.time = fake_time
tt._reschedule_poll = lambda *a, **k: None
for timer_name in ("_poll_timer", "poll_timer", "_timer"):
    t = getattr(tt, timer_name, None)
    if t is not None and hasattr(t, "stop"):
        t.stop()
TORRENTS = [
    ("ubuntu-24.04.1-desktop-amd64.iso", 6.1 * GB, 0.46, "magnet"),
    ("Big Buck Bunny (2008) 1080p — Blender open movie", 0.9 * GB, 0.81, "torrent"),
    ("Sintel (2010) 4K — Blender open movie", 2.8 * GB, 1.0, "torrent"),
]
trows = []
for name, size, done, kind in TORRENTS:
    tt._add_row(name, kind, "magnet:?xt=urn:btih:" + "0" * 40, os.path.join(STATE_DIR, "Downloads"), None, None,
                initial_progress=done)
trows = [tt.rows[k] for k in sorted(tt.rows)]
owner = tt


def speeds(i, t):
    import math as m
    if i == 0:
        return (14.5 + 4.0 * m.sin(t / 7.0) + 2.2 * m.sin(t * 1.3)) * MB, (0.35 + 0.25 * max(0, m.sin(t * 2.1))) * MB
    if i == 1:
        return (5.2 + 1.6 * m.sin(t / 5.0 + 1) + 0.8 * m.sin(t * 2.0)) * MB, (0.9 + 0.6 * max(0, m.sin(t * 1.7 + 2))) * MB
    return 0.0, (1.6 + 1.1 * max(0, m.sin(t * 0.9)) + 0.5 * max(0, m.sin(t * 3.1))) * MB


def feed(t):
    for i, ((name, size, done, kind), row) in enumerate(zip(TORRENTS, trows)):
        down, up = speeds(i, t)
        complete = done >= 1.0
        frac = min(1.0, done + (0 if complete else t * down / size * 0.02))
        st = types.SimpleNamespace(paused=False, has_metadata=True, total_wanted=int(size),
                                   total_wanted_done=int(size * frac), total_payload_download=int(size * frac),
                                   total_upload=int(up * (t + 600)), download_rate=down, upload_rate=up,
                                   num_peers=[38, 21, 12][i], num_seeds=[112, 46, 0][i])
        row["last_poll"] = clock["now"] - 1
        if complete:
            row["took_s"] = row.get("took_s") or 1140
            row["finished_at"] = row.get("finished_at") or (time.time() - 3 * 3600)
        else:
            row["active_s"] = 60 + t
        tt._update_stats(row, st, frac, complete, False)
        if not complete:
            row["progress_bar"].setValue(int(frac * 100))
            row["pct_label"].setText("%.0f%%" % (frac * 100))
            row["status_label"].setText("Downloading")
        else:
            row["status_label"].setText("Seeding")


# a minute of history first, so the graphs are full from the first frame
for s in range(-62, 1):
    clock["now"] = 1000.0 + s
    feed(s + 62)
show_tab(tt)
settle(1.0)
for row in trows:
    row["graph"].trace.snap_scale(clock["now"])
shot("torrent", nav_rects() | {"card_%d" % i: r["card"] for i, r in enumerate(trows)})

# The live clip: the app's own paint of the graph, frame by frame at 60 fps.
FPS, SECONDS = 60, 16
ensure_size()
clip_scrub = scrub()      # every frame of the clip shows the neutral path
settle(0.3)
first = grab(win)
w, h = first.width(), first.height()
live = os.path.join(OUT, "torrent-live.mp4")
proc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "%dx%d" % (w, h),
                         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "12",
                         "-pix_fmt", "yuv420p", "-movflags", "+faststart", live], stdin=subprocess.PIPE)
from PySide6.QtWidgets import QScrollArea  # noqa: E402
t_areas = tt.findChildren(QScrollArea)
t0 = clock["now"]
next_feed = 1
for f in range(FPS * SECONDS):
    clock["now"] = t0 + f / FPS
    if f / FPS >= next_feed:
        feed(62 + next_feed)
        next_feed += 1
    for row in trows:
        row["graph"].frame()
    app.processEvents()
    for area in t_areas:      # the list kept scrolling down as rows changed; held at the top
        area.verticalScrollBar().setValue(0)
    img = grab(win).convertToFormat(QImage.Format.Format_ARGB32)
    proc.stdin.write(bytes(img.constBits())[: w * h * 4])
proc.stdin.close()
proc.wait()
unscrub(clip_scrub)
META["torrent-live"] = {"w": w, "h": h, "fps": FPS, "seconds": SECONDS,
                        "rects": {"card_%d" % i: px_rect(r["card"], win) for i, r in enumerate(trows)}}
log("  torrent-live.mp4 %dx%d, %d frames" % (w, h, FPS * SECONDS))

# ---- 6. Settings and donate panels -------------------------------------------------------------------
log("panels")
show_tab(vt)
fill_form(0)
from ui_qt.dialogs.settings_dialog import SettingsDialog  # noqa: E402

dlg = SettingsDialog(win)
dlg.resize(640, 700)
dlg.show()
settle(1.5)
changed = scrub()
settle(0.3)
img = grab(dlg)
unscrub(changed)
save("settings", img, {k: px_rect(wd, dlg) for k, wd in {
    "concurrent": dlg.concurrent, "quality": dlg.quality, "format": dlg.format, "palette": dlg.palette,
    "theme": dlg.theme, "motion": dlg.motion, "auto_check": dlg.auto_check}.items()})
dlg.close()
settle(0.4)

panel = win.open_donate()
settle(1.0)
save("donate", grab(panel), {}, {"at": px_rect(panel, win)})
panel.close()
settle(0.4)

# ---- 7. Every palette, night and day, on the same screen ---------------------------------------------
log("palettes")
from ui_qt import palettes  # noqa: E402

show_tab(vt)
from _support import no_modal_dialogs  # noqa: E402
no_modal_dialogs()
clean_video_tab()
for name in palettes.ORDER:
    for mode in ("night", "day"):
        win.settings["palette"] = name
        want_dark = mode == "night"
        if win.dark_mode != want_dark:
            win.toggle_theme()
            settle(1.4)
        win.apply_settings({"palette"})
        settle(1.2)
        to_top(vt)
        shot("palette-%s-%s" % (name, mode), {}, {"label": palettes.label(name), "mode": mode})
if not win.dark_mode:
    win.toggle_theme()
    settle(1.2)
win.settings["palette"] = palettes.DEFAULT
win.apply_settings({"palette"})
settle(1.0)

# ---- 8. Browser tab -------------------------------------------------------------------------------------
log("browser tab")
from ui_qt import webview2 as wv2  # noqa: E402

log("  webview2 load: %s" % (wv2.load(),))
import clr  # noqa: E402,F401
from System.IO import MemoryStream  # noqa: E402
from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat as Fmt  # noqa: E402
from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind as Access  # noqa: E402


def sharpen(view):
    """WebView2 draws at the monitor's scale; the capture is 2x."""
    try:
        view.controller.ShouldDetectMonitorScaleChanges = False
        view.controller.RasterizationScale = 2.0
    except Exception as exc:  # noqa: BLE001
        log("  scale: %s" % exc)


def map_host(view):
    try:
        view.core.SetVirtualHostNameToFolderMapping("clips.example", SITE_DIR, Access.Allow)
    except Exception as exc:  # noqa: BLE001
        log("  map host: %s" % exc)


def page_image(view):
    stream = MemoryStream()
    task = view.core.CapturePreviewAsync(Fmt.Png, stream)
    t0 = time.time()
    while not task.IsCompleted and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.01)
    return QImage.fromData(bytes(stream.ToArray()))


def browser_shot(name, view, extra_rects=None):
    ensure_size()
    changed = scrub()
    settle(0.3)
    page = page_image(view)
    img = grab(win)
    unscrub(changed)
    r = px_rect(view, win)
    p = QPainter(img)
    p.drawImage(QRect(*r), page)
    p.end()
    rects = {"page": view} | {
        "address": bt.address, "download_btn": bt.download_btn, "bookmarks_btn": bt.bookmarks_btn,
        "shield_btn": bt.shield_btn, "menu_btn": bt.menu_btn, "bookmarks_bar": bt.bookmarks_bar,
        "all_bookmarks": bt.bookmarks_bar.all_btn, "strip": bt.strip} | (extra_rects or {})
    save(name, img, {k: px_rect(wd, win) for k, wd in rects.items() if wd is not None and wd.isVisible()})


show_tab(bt)
settle(8)
home_view = bt.home.view
sharpen(home_view)
settle(3)
browser_shot("browser-home", home_view)

# A page of the demo site, playing its clip: the Download button lights up.
bt.navigate("https://clips.example/watch.html")
settle(2)
site_view = bt.current_view()
map_host(site_view)
sharpen(site_view)
site_view.core.Reload()
settle(7)
cur = bt._current()
log("  site tab: url=%s has_video=%s" % (getattr(cur, "url", None), getattr(cur, "has_video", None)))
if not getattr(cur, "has_video", False):
    bt.download_btn.set_lit(True)
settle(0.6)
browser_shot("browser-site", site_view)

# All bookmarks, the right-click menu, and Edit...
panel = bt._show_bookmarks_panel(bt.bookmarks_bar.all_btn)
settle(1.2)
save("bookmarks-panel", grab(panel), {}, {"at": px_rect(panel, win)})
panel.close()
settle(0.5)

from ui_qt import browser_chrome  # noqa: E402

menus = []


class RecordedMenu(browser_chrome.QMenu):
    def exec(self, *a, **k):
        menus.append(self)


browser_chrome.QMenu = RecordedMenu
chip = bt.bookmarks_bar._chips[0]
bt.bookmarks_bar._chip_menu(chip.url, chip.mapToGlobal(QPoint(chip.width() // 2, chip.height())))
menu = menus[-1]
menu.adjustSize()
menu.popup(chip.mapToGlobal(QPoint(8, chip.height() + 4)))
settle(0.8)
save("bookmark-menu", grab(menu), {}, {"at": px_rect(menu, win), "chip": px_rect(chip, win)})
menu.close()
from ui_qt.browser_tab import bookmark_address  # noqa: E402
from ui_qt.dialogs.bookmark_dialog import EditBookmarkDialog  # noqa: E402

edit = EditBookmarkDialog(win, chip.title, chip.url, dark_mode=True, normalize=bookmark_address)
edit.show()
settle(1.0)
save("bookmark-edit", grab(edit))
edit.close()
settle(0.4)

# A private tab: the whole window turns black and white.
bt._create_tab(activate=True, private=True)
settle(5)
priv_view = bt.home.view if bt._current().on_home else bt.current_view()
sharpen(priv_view)
settle(2.5)
browser_shot("browser-private", priv_view)

# ---- done ------------------------------------------------------------------------------------------------
with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(META, f, indent=1)
log("wrote meta.json with %d captures" % len(META))
win.close()
settle(0.5)
