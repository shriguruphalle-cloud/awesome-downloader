"""Three bugs that shipped in 2.4.0, each pinned so it cannot come back.

1. Save Thumbnail: the worker emitted on two signals that had been lost in
   a merge, died on an AttributeError, and left the button disabled.
2. Sign-in: the fetch honoured Settings > Sign-in but the download ignored
   it, so a link that only read successfully with a chosen browser's cookies
   then failed to download.
3. (covered in test_download_paths.py) the audio file lookup.
"""
import os

import _support
from _support import check, pump, qapp, settings

qapp()
from app.core import downloader
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

st = settings()
vt = VideoTab(settings=st, download_tab=DownloadTab(settings=st))

# ---- 1. Save Thumbnail completes and re-enables its button -----------------
dest_base = os.path.join(_support.STATE_DIR, "thumb")


def fake_save(url, path_no_ext):
    path = path_no_ext + ".jpg"
    with open(path, "wb") as f:
        f.write(b"\xff\xd8fake")
    return path


downloader.save_thumbnail = fake_save
check(hasattr(vt, "_thumb_save_done_sig") and hasattr(vt, "_thumb_save_error_sig"),
      "thumbnail save signals are missing again")
vt.save_thumb_btn.setEnabled(False)       # what on_save_thumbnail does before starting
vt._save_thumbnail_thread("https://t/x.jpg", dest_base + ".jpg")   # synchronous here
pump()
print("after a save: enabled=%s text=%r" % (vt.save_thumb_btn.isEnabled(), vt.save_thumb_btn.text()))
check(vt.save_thumb_btn.isEnabled(), "Save Thumbnail stayed disabled")
check("Saved" in vt.save_thumb_btn.text(), vt.save_thumb_btn.text())
check(os.path.exists(dest_base + ".jpg"), "file not written")


def boom(url, path_no_ext):
    raise OSError("disk on fire")


shown = _support.no_modal_dialogs()
downloader.save_thumbnail = boom
vt.save_thumb_btn.setEnabled(False)
vt._save_thumbnail_thread("https://t/x.jpg", dest_base + "2.jpg")
pump()
check(vt.save_thumb_btn.isEnabled(), "a failed save left the button disabled")
check(shown and shown[-1][0] == "critical", "failure not reported: %r" % shown)
print("failed save reported and button re-enabled: ok")

# ---- 2. the download uses the same sign-in as the fetch ---------------------
seen = []


def fake_video(url, save_dir, height, hook, time_range=None, cookies_from_browser=None):
    seen.append(cookies_from_browser)
    return {"height": height}, None, None


def fake_audio(url, save_dir, bitrate, hook, time_range=None, cookies_from_browser=None):
    seen.append(cookies_from_browser)
    return {}, None, None


downloader.download_video = fake_video
downloader.download_audio = fake_audio
vt._jobs[99] = {"cancel": False, "pause_event": None, "path": None, "title": "t",
                "save_dir": _support.STATE_DIR, "finished": False}

st["cookies_from_browser"] = "edge"
vt._download_thread(99, "https://x.test/v", _support.STATE_DIR, "video", 720, "mkv")
vt._download_thread(99, "https://x.test/a", _support.STATE_DIR, "audio", None, "mp4")
st["cookies_from_browser"] = None
vt._download_thread(99, "https://x.test/v", _support.STATE_DIR, "video", 720, "mkv")
print("\ncookie source reaching the downloads:", seen)
check(seen == ["edge", "edge", None], "download ignored Settings > Sign-in: %r" % seen)

print("\nREGRESSIONS OK")
