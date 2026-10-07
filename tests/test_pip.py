"""Picture in picture, built into the Browser tab: the toolbar button (and
Alt+P / the Now Playing menu, which call the same toggle), the round button
beside the in-page Download pill, and the hover button on a video inside a
frame. Each must open Chromium's real "Picture in picture" window.

A local page and clip served by WebView2's own host mapping (no network),
the test profile, and clicks sent into the page through DevTools -- nothing
outside this test's own windows."""
import ctypes
import json
import os
import shutil
import tempfile
from ctypes import wintypes

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from ui_qt import webview2  # noqa: E402

CLIP = os.path.join(_support.REPO, "ui_qt", "browser_assets", "test_clip.mp4")
site = tempfile.mkdtemp(prefix="awd-pip-")
clip_src = CLIP if os.path.exists(CLIP) else None
if clip_src is None:
    # a two-second clip made on the spot with the bundled ffmpeg
    import subprocess
    ff = os.path.join(_support.REPO, "vendor", "ffmpeg.exe")
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "4",
                    "-pix_fmt", "yuv420p", os.path.join(site, "clip.mp4")], check=True)
else:
    shutil.copy(clip_src, os.path.join(site, "clip.mp4"))
VIDEO = "<video src='clip.mp4' autoplay muted loop playsinline style='width:640px;height:360px;display:block'></video>"
with open(os.path.join(site, "index.html"), "w") as f:
    f.write("<html><body style='background:#111;margin:40px'>" + VIDEO + "</body></html>")
with open(os.path.join(site, "frame.html"), "w") as f:
    f.write("<html><body style='background:#222;margin:0'>" + VIDEO + "</body></html>")
with open(os.path.join(site, "outer.html"), "w") as f:
    f.write("<html><body style='background:#111;margin:40px'>"
            "<iframe src='https://pip-frame.test/frame.html' style='width:660px;height:380px;border:0'></iframe>"
            "</body></html>")

user32 = ctypes.windll.user32


def pip_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(h, _l):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if buf.value == "Picture in picture":
                found.append(h)
        return True
    user32.EnumWindows(each, 0)
    return found


win, tabs = build_window(tabs=("video", "browser", "download"), size=(1280, 820))
bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
win.show()
settle(4000)
bt._create_tab(url="https://example.invalid/", activate=True)
settle(2500)
tab = bt._current()
view = tab.view
view.map_host("pip.test", site)
view.map_host("pip-frame.test", site)
settle(300)
view.core.Navigate("https://pip.test/index.html")
settle(4000)

out = {}


def cdp(method, params, key=None):
    def ok(raw):
        if key:
            out[key] = json.loads(raw) if raw else None
    webview2.then(view.core.CallDevToolsProtocolMethodAsync(method, json.dumps(params)), ok,
                  lambda m: out.__setitem__(key or "_", "failed: %s" % m))


def page_click(x, y):
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        cdp("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})
        settle(60)


def wait_for(cond, ms=4000):
    for _ in range(ms // 100):
        if cond():
            return True
        settle(100)
    return cond()


# 1. The toolbar button
check(tab.has_video, "the page's video wasn't noticed")
bt._sync_toolbar()
check(bt.pip_btn.isEnabled(), "the picture-in-picture button is off on a page with a video")
bt.pip_btn.click()
check(wait_for(lambda: pip_windows()), "the toolbar button opened no picture-in-picture window")
check(wait_for(lambda: tab.pip), "the toolbar didn't learn the video went picture-in-picture")
check(bt.pip_btn.tint is not None, "the button doesn't show that picture in picture is on")
bt.toggle_pip()                       # Alt+P and the Now Playing menu call this too
check(wait_for(lambda: not pip_windows()), "the toolbar button didn't close picture in picture")
check(wait_for(lambda: not tab.pip), "the toolbar still thinks picture in picture is on")
print("the toolbar button opens and closes picture in picture")

# 2. The round button beside the in-page Download pill (a real click in the page)
cdp("Runtime.evaluate", {"expression": "(() => { const b = [...document.querySelectorAll('button')]"
                         ".find(b => b.title.startsWith('Picture in picture')); if (!b) return null;"
                         " const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2,"
                         " getComputedStyle(b).opacity]; })()", "returnByValue": True}, "btn")
settle(600)
pos = (out.get("btn") or {}).get("result", {}).get("value")
check(pos and float(pos[2]) > 0.9, "no picture-in-picture button beside the video: %r" % (pos,))
page_click(pos[0], pos[1])
check(wait_for(lambda: pip_windows()), "the in-page button opened no picture-in-picture window")
page_click(pos[0], pos[1])
check(wait_for(lambda: not pip_windows()), "the in-page button didn't close picture in picture")
print("the button on the video opens and closes picture in picture")

# 3. A video inside a frame: hover shows the button, a click opens PiP
view.core.Navigate("https://pip.test/outer.html")
settle(4500)
for x, y in ((300, 200), (320, 210)):
    cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    settle(300)
# the frame's button: 44 px in from the video's right edge, 12 px down
fx, fy = 40 + 640 - 44 + 16, 40 + 12 + 16
page_click(fx, fy)
check(wait_for(lambda: pip_windows()), "the button on a framed video opened no picture-in-picture window")
page_click(fx, fy)
wait_for(lambda: not pip_windows())
print("a video inside a frame gets its own picture-in-picture button")

win.close()
settle(1500)
shutil.rmtree(site, True)
print("\nPICTURE IN PICTURE OK")
