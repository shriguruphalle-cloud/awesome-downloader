"""A video whose own timing is broken (frames stuck on one moment, as some sites serve them) is found and rebuilt so
it plays; a healthy one is left exactly as it was. Made-up clips, made here with FFmpeg; nothing from the internet."""
import os
import shutil
import tempfile

import _support  # noqa: F401
from _support import check

from app.core import video_repair as vr
from app.core.ffmpeg_utils import ffmpeg_path

work = tempfile.mkdtemp(prefix="awd-repair-")
ff = ffmpeg_path()


def make(name, broken, ext=".mp4"):
    clean = os.path.join(work, "clean_" + name + ext)
    vr._run(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-c:v", "libx264", "-bf", "0", "-g", "60",
             "-pix_fmt", "yuv420p", "-c:a", "aac" if ext != ".webm" else "libopus", "-shortest", clean])
    if not broken:
        return clean
    out = os.path.join(work, name + ext)
    # frames 60-120 all stamped with the time of frame 60: what the broken downloads had
    r = vr._run(["-y", "-v", "error", "-i", clean, "-map", "0", "-c", "copy", "-bsf:v",
                 "setts=ts='if(between(N,61,120),PREV_OUTPTS,TS)'", out])
    check(r.returncode == 0, "couldn't make the broken clip: %s" % r.stderr[-300:])
    return out


good = make("good", False)
before = os.path.getsize(good), os.path.getmtime(good)
t = vr.timing(good)
check(t and t["stuck"] == 0 and t["backward"] == 0, "a healthy clip read as broken: %s" % t)
path, fixed = vr.check_and_repair(good)
check(path == good and not fixed and (os.path.getsize(good), os.path.getmtime(good)) == before,
      "a healthy clip was touched")
print("a healthy video is left alone")

bad = make("bad", True)
t = vr.timing(bad)
check(t and t["stuck"] >= 50, "the broken clip doesn't show as broken: %s" % t)
path, fixed = vr.check_and_repair(bad)
check(fixed and path == bad and os.path.isfile(bad), "it wasn't rebuilt: %s %s" % (path, fixed))
t = vr.timing(bad)
check(t["stuck"] == 0 and t["backward"] == 0, "still broken after the rebuild: %s" % t)
_f, _k, secs = vr._probe(bad)
check(secs and abs(secs - 10) < 0.5, "its length changed: %s" % secs)
err = vr._run(["-v", "error", "-i", bad, "-f", "null", "-"]).stderr.strip()
check(not err, "the rebuilt clip doesn't decode cleanly: %s" % err[:300])
check("Audio:" in vr._run(["-hide_banner", "-i", bad]).stderr, "the sound was lost")
check(not any(n.endswith((".fixing.mp4",)) for n in os.listdir(work)), "a half-made file was left behind")
print("a broken video is rebuilt: whole, the same length, with its sound")

mkv = make("bad", True, ".mkv")
path, fixed = vr.check_and_repair(mkv)
check(fixed and path == mkv and vr.timing(mkv)["stuck"] == 0, "the .mkv wasn't rebuilt")
print("an .mkv too")

# a rebuild that fails leaves the download as it was
bad2 = make("bad2", True)
size = os.path.getsize(bad2)
real = vr._run
vr._run = lambda args, text=True: real(args[:-1] + [os.path.join(work, "nope", "x.mp4")], text) \
    if args and args[0] == "-y" and "-fps_mode" in args else real(args, text)
path, fixed = vr.check_and_repair(bad2)
vr._run = real
check(not fixed and path == bad2 and os.path.getsize(bad2) == size, "a failed rebuild changed the download")
print("a failed rebuild leaves the download alone")

shutil.rmtree(work, ignore_errors=True)
print("\nVIDEO REPAIR OK")
