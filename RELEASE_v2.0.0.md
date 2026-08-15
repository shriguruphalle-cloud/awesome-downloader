# Awesome Downloader 2.0.0

A Windows desktop app for downloading video, audio, images and torrents —
one window, four tools, no ads and no sign-up.

## Download

**[AwesomeVideoDownloaderSetup.exe](../../releases/download/v2.0.0/AwesomeVideoDownloaderSetup.exe)** — 289 MB

Everything is bundled. FFmpeg and the torrent engine are included, so there
is nothing else to install.

**Requirements:** Windows 10 or 11, 64-bit. ARM64 works through Windows'
x64 emulation. 32-bit Windows is not supported.

### Verify your download

```
certutil -hashfile AwesomeVideoDownloaderSetup.exe SHA256
```

```
b2dc1ecb802a6fc31d5376d4d94f078fa7e2ad0a2f64b3fe2cc4b4ccbe18c148
```

If the value differs, the download was cut short or altered — delete it and
download again.

### "Windows protected your PC"

Expected. The app isn't code-signed yet, so SmartScreen warns about any
publisher it hasn't seen before. It is not a virus warning. Click
**More info** → **Run anyway**. Verify the SHA-256 above first if you want
to be certain the file is intact.

---

## What's in this release

**Batch downloads** — paste a link, hit Download, and the form clears
immediately so you can queue the next one. Downloads run side by side, each
with its own card showing a thumbnail, title and live progress. Finished
items move to History automatically.

**New app icon** throughout — window, taskbar, installer and shortcuts.

### Fixes

- **Format conversion failing** when a video was already in the requested
  container, or when the same video was downloaded twice. Both cases now
  work; a second download no longer overwrites the first.
- **Torrent progress resetting to 0%** after restarting the app. Progress is
  saved and restored, and the bar no longer counts backwards during the
  file re-check on resume.
- **Drag-to-top-edge maximise** on Windows 11 now works.
- **Higher-contrast light mode**, and cards are properly visible when the
  window is maximised.

---

## Licence

GPL-3.0. The installer bundles an FFmpeg build compiled with `--enable-gpl
--enable-version3`, whose terms apply to the combined work. Every bundled
component and its licence is listed in [NOTICE.md](NOTICE.md).

## Fair use

Downloading content may breach a site's Terms of Service unless you own it,
have permission, or it is in the public domain. You are responsible for what
you download. This app does not support DRM-protected content.
