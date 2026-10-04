# Awesome Downloader

A Windows desktop app for downloading video, audio, images and torrents —
one window, four tools, no ads and no sign-up.

![Version](https://img.shields.io/badge/version-2.5.0-35b6ff)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011%20(64--bit)-lightgrey)
![Licence](https://img.shields.io/badge/licence-GPL--3.0-blue)

---

## ⚠️ Before you use this

Downloading content may breach a site's Terms of Service unless you own it,
have permission, or it is in the public domain. You are responsible for what
you download and what you do with it.

This app does not, and will not, support downloading DRM-protected content.

---

## What it does

**Video & audio** — paste a link from any of 1,750+ sites supported by
yt-dlp (YouTube, Instagram, Facebook, TikTok, X, Reddit, Vimeo, Twitch,
SoundCloud and more). Pick a resolution with the estimated file size shown
next to it, choose MP4/MKV/MOV, or extract MP3 at 128–320 kbps. Download a
clip range instead of the whole video. Queue several links and they download
side by side, each with its own progress card.

**Torrents** — magnet links and `.torrent` files via libtorrent. Choose
which files inside a torrent to fetch, pause and resume, and watch live
speed, peer count and ETA. Progress survives closing the app or rebooting.
Can register itself as your system's magnet-link handler.

**Images** — paste a post link and get the whole carousel or gallery, not
just the first image. Preview the grid, tick what you want. Files are saved
byte-for-byte at original quality — no re-compression — and keep their real
format, so PNG transparency survives.

**History** — everything downloaded, with thumbnails. Play a file, open its
folder, remove the entry, or delete the file itself.

Plus: dark and light themes, a per-tab save folder that's remembered between
runs, and a built-in update check for yt-dlp (the component that breaks when
sites change).

---

## Install

Download the installer from the
[latest release](../../releases/latest) and run it. Everything is bundled —
FFmpeg and the torrent engine are included, so there is nothing else to set
up.

Windows will show **"Windows protected your PC"** the first time, because
the app isn't code-signed yet. Click **More info** → **Run anyway**. Verify
the SHA-256 published with the release first if you want to be sure the file
is intact.

**Requirements:** Windows 10 or 11, 64-bit. ARM64 works through Windows'
x64 emulation. 32-bit Windows is not supported.

---

## Running from source

```bat
git clone <this repo>
cd "Awesome downloader claude code"
py -3.12 -m venv .venv312
.venv312\Scripts\python.exe -m pip install -r requirements.txt
.venv312\Scripts\python.exe main_qt.py
```

**Python 3.12 specifically** — libtorrent doesn't publish wheels for 3.14
yet (the supported range lives in `LIBTORRENT_MIN_PY` / `LIBTORRENT_MAX_PY`
in `app/config.py`). Without libtorrent the app still runs; the Torrent tab
shows an install prompt instead.

FFmpeg is needed for merging video with audio and for format conversion. The
packaged build bundles it; from source, either put `ffmpeg.exe` next to the
app or install it so it's on your `PATH`.

To build the `.exe` and installer yourself, see **[BUILD.md](BUILD.md)**.

---

## How it's put together

```
app/
  config.py            paths, version, constants
  core/                framework-agnostic logic -- no UI imports
    downloader.py        yt-dlp wrapper: info, download, image galleries
    ffmpeg_utils.py      container conversion, bundled-ffmpeg resolution
    torrent_manager.py   libtorrent session wrapper
    size_estimate.py     pre-download size estimates
    update_checker.py    installed vs. latest component versions
  utils/               settings, history, single-instance, protocol handler
ui_qt/                 PySide6 UI
  main_window.py         frameless window, tab island, theming
  *_tab.py               one file per tab
  widgets/               shared painted widgets (cards, chips, progress)
main_qt.py             entry point
```

`app/core/` and `app/utils/` never import Qt. UI code runs those blocking
calls on background threads and marshals results back through signals, so
the logic stays testable and the window never freezes.

---

## Troubleshooting

**Downloads suddenly failing / "Unable to extract"** — sites change how they
serve video, sometimes weekly, and yt-dlp ships fixes constantly. Open
**Check for Updates** in the app; it compares your yt-dlp against the latest
release and updates it in one click. This fixes most cases.

**"HTTP Error 403: Forbidden"** — usually the same cause. If updating
yt-dlp doesn't help, the site may require a login; cookie support is exposed
through `cookies_from_browser` in the settings file.

**"This app can't run on your PC"** — either the download was truncated
(check the SHA-256 from the release) or you're on 32-bit Windows, which
isn't supported. Check with `echo %PROCESSOR_ARCHITECTURE%` — `AMD64` or
`ARM64` is fine, `x86` is not.

**Conversion to MP4/MOV failed** — the file is kept as `.mkv`, which plays
in VLC and most modern players. MOV requires a full re-encode, so it is
slow and lossier by nature; MP4 is a fast stream copy where possible.

**Torrent tab says the engine isn't installed** — you're on a Python
version with no libtorrent wheel. Use Python 3.12.

---

## Licence

[GPL-3.0](LICENSE). The released installer bundles an FFmpeg build compiled
with `--enable-gpl --enable-version3`, whose terms apply to the combined
work. Every third-party component and its licence is listed in
**[NOTICE.md](NOTICE.md)**.

Built by Shriguru Phalle — by an editor, for editors.
