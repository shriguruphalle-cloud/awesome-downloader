"""A part of a video ("custom range"): when FFmpeg fails cutting it straight
from the stream (it crashed on YouTube's servers: "ffmpeg exited with code
3436169992"), the whole video is fetched into a folder of its own and the
part cut here instead -- the right length, under the usual name, nothing
already saved touched, nothing left behind. The network is stubbed: the
"download" is a test pattern and a tone made with FFmpeg."""
import os
import shutil
import subprocess
import tempfile

import _support
from _support import check

import yt_dlp  # noqa: E402

from app.core import downloader  # noqa: E402

FF = downloader._ffmpeg_exe()
work = tempfile.mkdtemp(prefix="clip-test-")
source = os.path.join(work, "source.mkv")
subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i", "sine=f=440", "-t", "12",
                "-c:v", "libx264", "-g", "50", "-c:a", "libopus", source], check=True)


def length(path):
    probe = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    hms = probe.split("Duration: ")[1].split(",")[0]
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


calls = []


def fake_run(opts, url):
    """The stream cut fails the way it did for real; a whole download "succeeds"."""
    calls.append(dict(opts))
    if opts.get("download_ranges"):
        raise yt_dlp.utils.DownloadError("ERROR: ffmpeg exited with code 3436169992")
    dest = opts["outtmpl"].replace("%(title)s", "Test Video").replace("%(ext)s", "mkv")
    shutil.copyfile(source, dest)
    return {"title": "Test Video", "requested_downloads": [{"filepath": dest}]}, None


downloader.run_with_client_fallback = fake_run
downloader.base_ydl_opts = lambda *a, **k: {}

save = os.path.join(work, "saved")
os.makedirs(save)
already = os.path.join(save, "Test Video.mkv")            # a full download saved before: must stay as it is
shutil.copyfile(source, already)
before = os.path.getsize(already)

info, path, _used = downloader.download_video("https://www.youtube.com/watch?v=test0000000", save, 360,
                                              lambda d: None, time_range=(3, 8))
check(len(calls) == 2 and calls[0].get("download_ranges") and not calls[1].get("download_ranges"),
      "the stream cut wasn't followed by a whole download: %r" % [sorted(c) for c in calls])
check(path and os.path.exists(path) and path != already, "the clip wasn't saved beside the earlier file: %r" % path)
check(abs(length(path) - 5.0) < 0.35, "the clip is %.2f s long, not 5" % length(path))
check(os.path.getsize(already) == before, "a file already saved was changed")
check(sorted(os.listdir(save)) == ["Test Video (2).mkv", "Test Video.mkv"],
      "something was left behind: %r" % os.listdir(save))
print("a video clip FFmpeg couldn't cut from the stream is downloaded whole and cut here, to the second")

calls.clear()
info, mp3, _used = downloader.download_audio("https://www.youtube.com/watch?v=test0000000", save, "192",
                                             lambda d: None, time_range=(1, 4))
check(mp3 and mp3.endswith(".mp3") and abs(length(mp3) - 3.0) < 0.35, "the audio clip is wrong: %r" % mp3)
print("an audio clip is cut the same way, to MP3")


def other_failure(opts, url):
    raise yt_dlp.utils.DownloadError("ERROR: Video unavailable")


downloader.run_with_client_fallback = other_failure
try:
    downloader.download_video("https://www.youtube.com/watch?v=test0000000", save, 360, lambda d: None,
                              time_range=(3, 8))
    check(False, "another error was swallowed")
except yt_dlp.utils.DownloadError as e:
    check("unavailable" in str(e), "another error changed on the way: %s" % e)
print("other errors still reach the user as they are")

shutil.rmtree(work, ignore_errors=True)
print("\nCLIP FALLBACK OK")
