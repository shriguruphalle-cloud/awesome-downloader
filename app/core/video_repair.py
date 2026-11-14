"""Videos whose own timing is broken, put right after they're downloaded.

Some sites serve MP4s where stretches of frames carry no proper time: dozens of frames stamped with one and the same
moment (a file seen: 91 frames all at 0:41 and 174 at 8:27). The picture data is fine, so FFmpeg reads it without an
error, but players stumble on it: they drop and misplace frames there and the picture smears into blocks until the
next keyframe -- the "pixelated" look, which isn't the bitrate at all.

Checking a file is fast (only its packet table is read, nothing decoded). A broken one is rebuilt at a steady frame
rate -- the stuck frames come out, nothing is left as a gap -- on the graphics card where there is one, at about
the bitrate it had, and only replaces the download once the result is checked to be whole and as long."""
import os
import re
import subprocess

from app import config
from app.logging_setup import get_logger

from . import ffmpeg_utils

logger = get_logger("video_repair")

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".m4v", ".webm")
# duplicate or backward timestamps a healthy file never has; a few spare for odd but playable files
_TOLERANCE = 3
_STUCK = 15


def _run(args, text=True):
    kw = {"capture_output": True, "text": text, "errors": "replace"} if text else {"capture_output": True}
    if config.CREATE_NO_WINDOW:
        kw["creationflags"] = config.CREATE_NO_WINDOW
    return subprocess.run([ffmpeg_utils.ffmpeg_path(), *args], **kw)


def timing(path):
    """{"frames", "stuck", "backward"} of the first video stream: stuck = frames sharing a presentation time with
    another, backward = decode times that don't move forward. None when it can't be read (or has no video)."""
    r = _run(["-v", "error", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-"])
    pts, dts = [], []
    for line in (r.stdout or "").splitlines():
        if line.startswith("0,"):
            parts = line.split(",")
            try:
                dts.append(int(parts[1]))
                pts.append(int(parts[2]))
            except (IndexError, ValueError):
                continue
    if not pts:
        return None
    backward = sum(1 for i in range(1, len(dts)) if dts[i] <= dts[i - 1])
    # stuck: frames hardly any time after the one before -- the same moment, or a tick apart where a muxer
    # nudged equal times apart -- against the usual step between frames
    order = sorted(pts)
    steps = sorted(b - a for a, b in zip(order, order[1:]))
    usual = steps[len(steps) // 2] if steps else 0
    stuck = sum(1 for s in steps if s < usual * 0.25) if usual > 0 else 0
    return {"frames": len(pts), "stuck": stuck, "backward": backward}


def broken(path):
    if not path or os.path.splitext(path)[1].lower() not in VIDEO_EXTS or not os.path.isfile(path):
        return False
    t = timing(path)
    # half a second's worth at least: a phone's variable frame rate has the odd short step, never dozens
    return bool(t) and (t["stuck"] > _STUCK or t["backward"] > _TOLERANCE)


def _probe(path):
    """(frame rate as FFmpeg takes it, video kb/s or None, length in seconds or None)."""
    err = _run(["-hide_banner", "-i", path]).stderr or ""
    video = next((l for l in err.splitlines() if "Video:" in l), "")
    fps = "30"
    m = re.search(r"([\d.]+) fps", video)
    if m:
        f = float(m.group(1))
        fps = {29.97: "30000/1001", 23.98: "24000/1001", 59.94: "60000/1001", 47.95: "48000/1001"}.get(
            round(f, 2), str(round(f)) if abs(f - round(f)) < 0.01 else m.group(1))
    kbps = re.search(r"(\d+) kb/s", video) or re.search(r"bitrate: (\d+) kb/s", err)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    secs = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return fps, int(kbps.group(1)) if kbps else None, secs


_encoders = None


def _gpu_encoders():
    global _encoders
    if _encoders is None:
        out = _run(["-hide_banner", "-encoders"]).stdout or ""
        _encoders = [e for e in ("h264_nvenc", "h264_qsv", "h264_amf") if e in out]
    return _encoders


def _video_args(encoder, kbps):
    rate = ["-b:v", "%dk" % kbps, "-maxrate", "%dk" % int(kbps * 1.5), "-bufsize", "%dk" % (kbps * 2)] if kbps else []
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", "21"] + (rate or ["-b:v", "0"])
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "medium", "-global_quality", "21"] + rate
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "quality", "-rc", "vbr_peak"] + rate
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"] + (["-maxrate", rate[3], "-bufsize", rate[5]]
                                                                      if rate else [])


def repair(path):
    """The video rebuilt with sound timing, in place of `path`; returns the file's path (a .webm comes back as
    .mp4, which H.264 needs). On any failure the download is left as it was and None is returned."""
    fps, kbps, secs = _probe(path)
    base, ext = os.path.splitext(path)
    out_ext = ".mp4" if ext.lower() in (".webm", ".m4v") else ext
    tmp = base + ".fixing" + out_ext
    for encoder in _gpu_encoders() + ["libx264"]:
        args = ["-y", "-v", "error", "-i", path, "-map", "0:v:0", "-map", "0:a?", "-fps_mode", "cfr", "-r", fps,
                *_video_args(encoder, kbps), "-pix_fmt", "yuv420p", "-c:a",
                "copy" if out_ext == ext else "aac"]
        if out_ext in (".mp4", ".mov"):
            args += ["-movflags", "+faststart"]
        r = _run(args + [tmp])
        if r.returncode == 0 and os.path.isfile(tmp):
            break
        logger.info("Rebuilding %s with %s didn't work: %s", os.path.basename(path), encoder, (r.stderr or "")[-300:])
        _drop(tmp)
    else:
        return None
    t = timing(tmp)
    _fps, _kb, new_secs = _probe(tmp)
    if not t or t["stuck"] > _TOLERANCE or t["backward"] > _TOLERANCE or (secs and new_secs and abs(new_secs - secs) > max(2, secs * 0.02)):
        logger.warning("The rebuilt %s didn't check out (%s, %ss of %ss); keeping the download as it was",
                       os.path.basename(path), t, new_secs, secs)
        _drop(tmp)
        return None
    final = base + out_ext
    try:
        os.replace(tmp, final)
        if final != path:
            _drop(path)
    except OSError:
        logger.exception("Couldn't put the rebuilt %s in place", os.path.basename(path))
        _drop(tmp)
        return None
    logger.info("Rebuilt %s: its timing was broken", os.path.basename(final))
    return final


def check_and_repair(path):
    """(the path to use, True if it was rebuilt). Never raises: a check that fails leaves the download alone."""
    try:
        if broken(path):
            fixed = repair(path)
            if fixed:
                return fixed, True
    except Exception:   # noqa: BLE001
        logger.exception("Couldn't check %s", path)
    return path, False


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass
