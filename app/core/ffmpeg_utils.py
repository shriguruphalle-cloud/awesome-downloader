"""ffmpeg invocation + per-container conversion logic.

ffmpeg is bundled directly into the app's own install folder (see
download_ffmpeg() / config.FFMPEG_BUNDLED_PATH) rather than requiring a
separate system install -- this is what a real friend-hit-this bug report
was about earlier (a user's laptop was missing ffmpeg and downloads that
needed muxing just failed with no clear next step). A system-PATH ffmpeg is
still honored if present (e.g. a user who already has one via a different
app), so nothing breaks for anyone who had the old winget-based setup.
"""
import io
import os
import shutil
import subprocess
import urllib.request
import zipfile

from .. import config
from ..logging_setup import get_logger

logger = get_logger("ffmpeg")


def ffmpeg_path():
    """Resolves the ffmpeg executable to actually invoke: prefer the app's
    own bundled copy (predictable, doesn't depend on the user's PATH being
    set up right), fall back to whatever 'ffmpeg' resolves to on PATH."""
    if os.path.exists(config.FFMPEG_BUNDLED_PATH):
        return config.FFMPEG_BUNDLED_PATH
    return "ffmpeg"


def ffmpeg_available():
    return os.path.exists(config.FFMPEG_BUNDLED_PATH) or shutil.which("ffmpeg") is not None


def installed_ffmpeg_version():
    """Best-effort version string (e.g. '7.1.1') from `ffmpeg -version`'s
    first line, for display in the Check for Updates panel. None if ffmpeg
    isn't available or the output couldn't be parsed."""
    if not ffmpeg_available():
        return None
    try:
        result = ffmpeg_run([ffmpeg_path(), "-version"])
        first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
        # e.g. "ffmpeg version 7.1.1-essentials_build-www.gyan.dev Copyright ..."
        parts = first_line.split()
        idx = parts.index("version") if "version" in parts else -1
        return parts[idx + 1] if 0 <= idx < len(parts) - 1 else None
    except Exception:
        logger.exception("Failed to parse ffmpeg -version output")
        return None


def _download_zip_bytes(url, report):
    req = urllib.request.Request(url, headers={"User-Agent": "AwesomeDownloader-FFmpegInstaller"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        downloaded = 0
        chunk_size = 1 << 20
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            buf.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                report(f"Downloading ffmpeg... {pct:.0f}% ({downloaded // (1 << 20)} MB / {total // (1 << 20)} MB)")
    return buf


def download_ffmpeg(progress_callback=None):
    """Blocking. Downloads a current static ffmpeg build and drops just
    ffmpeg.exe into the app's own folder (config.FFMPEG_BUNDLED_PATH) -- no
    winget, no PATH mutation, no admin prompt. Used for both a first install
    and an in-place update (same function, it just overwrites).

    Tries each URL in config.FFMPEG_DOWNLOAD_URLS in order (falls through to
    the next on any failure -- a single host being briefly down, e.g. a real
    503 hit from gyan.dev during development, shouldn't fail the whole
    operation when a working mirror exists). Raises RuntimeError with a
    human-readable message only if every source failed.

    Written to a temp file first and atomically replaced so a failed/
    interrupted download can never leave a half-written, broken ffmpeg.exe
    behind.
    """
    def report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    report("Downloading ffmpeg...")
    buf = None
    errors = []
    for url in config.FFMPEG_DOWNLOAD_URLS:
        try:
            buf = _download_zip_bytes(url, report)
            break
        except Exception as e:
            logger.warning("ffmpeg download failed from %s: %s", url, e)
            errors.append(f"{url}: {e}")
    if buf is None:
        raise RuntimeError("Couldn't download ffmpeg from any source:\n" + "\n".join(errors))

    report("Extracting...")
    try:
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            exe_entry = next(
                (n for n in zf.namelist() if n.replace("\\", "/").endswith("/bin/ffmpeg.exe")), None
            )
            if not exe_entry:
                raise RuntimeError("Downloaded archive didn't contain ffmpeg.exe -- the build layout may have changed.")
            os.makedirs(os.path.dirname(config.FFMPEG_BUNDLED_PATH), exist_ok=True)
            tmp_path = config.FFMPEG_BUNDLED_PATH + ".tmp"
            with zf.open(exe_entry) as src, open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp_path, config.FFMPEG_BUNDLED_PATH)
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Downloaded file wasn't a valid archive: {e}") from e
    finally:
        for p in (config.FFMPEG_BUNDLED_PATH + ".tmp",):
            if os.path.exists(p) and not os.path.exists(config.FFMPEG_BUNDLED_PATH):
                try:
                    os.remove(p)
                except OSError:
                    pass

    report("Done.")


def install_ffmpeg(progress_callback=None):
    """Kept as a thin alias -- update_panel.py and any external callers
    written against the old name keep working; download_ffmpeg() is the
    real implementation now (see its docstring for why it replaced the
    winget-based approach)."""
    download_ffmpeg(progress_callback=progress_callback)


def ffmpeg_run(args):
    if args and args[0] == "ffmpeg":
        args = [ffmpeg_path(), *args[1:]]
    kwargs = {"capture_output": True, "text": True}
    if config.CREATE_NO_WINDOW:
        kwargs["creationflags"] = config.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        logger.exception("Failed to remove intermediate file %s", path)


def _unique_path(preferred, ignore_path=None):
    """`preferred` if free, otherwise 'name (2).ext', 'name (3).ext', ...
    `ignore_path` is treated as free -- it's the source file this conversion
    is about to replace, so it isn't a real collision."""
    if not os.path.exists(preferred) or os.path.abspath(preferred) == os.path.abspath(ignore_path or ""):
        return preferred
    base, ext = os.path.splitext(preferred)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def _finish_conversion(tmp_path, dst_path, src_path):
    """Moves the finished temp file to its real name and drops the source."""
    try:
        if os.path.exists(dst_path):
            os.remove(dst_path)
        os.replace(tmp_path, dst_path)
    except OSError:
        logger.exception("Failed to move converted file into place")
        return tmp_path, None
    _remove_quietly(src_path)
    return dst_path, None


def convert_container(src_path, target_ext):
    """Turn the merged .mkv into the user's requested container.

    mp4  -> fast stream-copy remux (works for the vast majority of sources)
    mov  -> full re-encode, since MOV is the strictest container and some
            codec combos (VP9/AV1 + Opus) don't remux into it cleanly.
    Falls back to keeping the .mkv if conversion fails for any reason.
    """
    base, src_ext = os.path.splitext(src_path)

    # Already the requested container -- nothing to convert. Without this,
    # dst_path below resolves to src_path itself and ffmpeg refuses with
    # "Output ... same as Input #0 - exiting / FFmpeg cannot edit existing
    # files in-place", which surfaced as a bogus "conversion failed, kept as
    # .mkv" warning on a file that was already a perfectly good .mp4
    # (reported directly, with the ffmpeg error). yt-dlp hands back an .mp4
    # directly whenever the chosen streams need no remux, so this is the
    # common case, not an edge case.
    if src_ext.lstrip(".").lower() == target_ext.lower():
        return src_path, None

    # Written to a distinct temp name and renamed into place afterwards.
    # Converting straight to "<base>.<ext>" collides whenever a file of that
    # name already exists -- downloading the same video twice, which is
    # exactly when this was reported -- and ffmpeg would either clobber the
    # earlier download or fail outright depending on timing.
    tmp_path = f"{base}.converting.{target_ext}"
    dst_path = _unique_path(f"{base}.{target_ext}", src_path)

    if target_ext == "mp4":
        cmd = ["ffmpeg", "-y", "-i", src_path, "-map", "0", "-dn",
               "-c", "copy", "-c:s", "mov_text", tmp_path]
    else:  # mov: safest is a real re-encode
        cmd = ["ffmpeg", "-y", "-i", src_path, "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-c:a", "aac", "-b:a", "192k", tmp_path]

    result = ffmpeg_run(cmd)
    if result.returncode == 0 and os.path.exists(tmp_path):
        return _finish_conversion(tmp_path, dst_path, src_path)

    # Remux failed (rare) - if we were doing a fast remux to mp4, try a full
    # re-encode before giving up, since that handles almost any codec.
    if target_ext == "mp4":
        _remove_quietly(tmp_path)
        cmd2 = ["ffmpeg", "-y", "-i", src_path, "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", tmp_path]
        result2 = ffmpeg_run(cmd2)
        if result2.returncode == 0 and os.path.exists(tmp_path):
            return _finish_conversion(tmp_path, dst_path, src_path)
    _remove_quietly(tmp_path)

    # Give up on conversion, keep the working .mkv instead of failing outright
    logger.error("ffmpeg conversion to .%s failed: %s", target_ext, (result.stderr or "")[-800:])
    return src_path, (result.stderr or "")[-400:]
