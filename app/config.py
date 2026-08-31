import os
import subprocess
import sys

APP_NAME = "AWESOME DOWNLOADER"
APP_VERSION = "2.4.0"
APP_PUBLISHER = "Shriguru Phalle"  # kept in sync with installer.iss's MyAppPublisher by hand

IS_FROZEN = getattr(sys, "frozen", False)  # True when running as a PyInstaller .exe
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hides console flashes on Windows


def _repo_base_dir():
    if IS_FROZEN:
        return os.path.dirname(sys.executable)
    # app/config.py -> app/ -> repo root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = _repo_base_dir()
_ASSET_DIR = getattr(sys, "_MEIPASS", BASE_DIR) if IS_FROZEN else BASE_DIR
ICON_PATH = os.path.join(_ASSET_DIR, "app_icon.ico")

FFMPEG_BUNDLED_PATH = os.path.join(BASE_DIR, "ffmpeg.exe")
# Tried in order -- gyan.dev is the smaller, original source (and what the
# app's old winget-based installer used), but it's a single small personal
# site that can and does 503 under load (confirmed during development).
# GitHub's release CDN is the reliable fallback so an update/first-install
# doesn't hard-fail just because one host is briefly down.
FFMPEG_DOWNLOAD_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)

DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YT-Downloads")
DEFAULT_TORRENT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "Torrents")

APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Awesome Downloader")
LOG_PATH = os.path.join(APPDATA_DIR, "app.log")
SETTINGS_PATH = os.path.join(APPDATA_DIR, "settings.json")

# Where update_checker.upgrade_yt_dlp() extracts a freshly downloaded yt-dlp
# wheel when running as the packaged .exe -- pip can't install into a
# PyInstaller onefile bundle (there's no real site-packages to write into at
# runtime), so this is the actual update mechanism there: download the wheel
# straight from PyPI (a .whl is just a zip, no pip needed to unpack one),
# and prepend this directory to sys.path *before* anything imports yt_dlp,
# so the newer copy here shadows the one frozen into the .exe. Applied
# unconditionally below, at the very top of the app's own import graph
# (config.py is imported by everything before any of them touch yt_dlp) --
# a no-op if nothing has been downloaded yet.
YT_DLP_SHADOW_DIR = os.path.join(APPDATA_DIR, "yt_dlp_shadow")
if os.path.isdir(os.path.join(YT_DLP_SHADOW_DIR, "yt_dlp")) and YT_DLP_SHADOW_DIR not in sys.path:
    sys.path.insert(0, YT_DLP_SHADOW_DIR)

# Minimum libtorrent Python-wheel-compatible range, confirmed against PyPI at
# plan time (libtorrent 2.0.13's newest wheels top out at cp313).
LIBTORRENT_MIN_PY = (3, 9)
LIBTORRENT_MAX_PY = (3, 13)
