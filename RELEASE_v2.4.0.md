# Awesome Downloader 2.4.0

A Windows desktop app for downloading video, audio, images and torrents —
one window, no ads and no sign-up.

## Download

**[AwesomeVideoDownloaderSetup.exe](../../releases/download/v2.4.0/AwesomeVideoDownloaderSetup.exe)** — 297 MB

Everything is bundled. FFmpeg and the torrent engine are included, so there
is nothing else to install.

**Requirements:** Windows 10 or 11, 64-bit. ARM64 works through Windows'
x64 emulation. 32-bit Windows is not supported.

### Verify your download

```
certutil -hashfile AwesomeVideoDownloaderSetup.exe SHA256
```

```
aca8ea06da25f7d70694eb5206cfc3a83ff8c223ad0e569089f511193dacc76d
```

If the value differs, the download was cut short or altered — delete it and
download again.

### "Windows protected your PC"

The app is not code-signed, so SmartScreen warns the first time you run the
installer. Click **More info**, then **Run anyway**. A signing certificate is
the only thing that removes this, and it is not something this project has.

---

## What's new in 2.4.0

### Paste a whole batch of links

Drop any number of links into the Video tab and they stack up as cards —
title, channel, duration and a thumbnail each — then **Start all** runs the
lot. One link at a time works the same way.

Each card carries its own resolution picker, because every link offers a
different ladder of qualities. Click a card and its link goes back into the
form, so you can give that one a clip range, a different container, or
audio-only before starting it.

**The queue survives closing the app.** Links you lined up are still there
next time, along with the resolution you picked and any clip range. Cancel a
download and it returns to the queue rather than disappearing.

### Links that need a sign-in

Instagram posts, age-gated videos and members-only content return nothing to
an anonymous request. Instead of showing yt-dlp's command-line advice, the
app now offers two ways to get a session: sign in inside its own Browser tab,
or borrow the cookies from a browser you already use. Nothing is uploaded
anywhere — the session is passed to the downloader on your machine.

### Background tabs stay quiet

Opening a video in a new browser tab no longer starts playing it. Playback
waits until you actually switch to the tab.

### A redesigned top bar

The window's title bar and the navigation row are one strip now instead of
two, which gives every tab more room — the browser gained about 40px of page
height and runs edge to edge. Theme, Updates and About became icons in their
own tray, so tab names are never cropped.

---

## Fixed

- **Closing the Updates or About panel closed the whole app.** The update
  check ran on a thread owned by the dialog, so closing it destroyed a thread
  that was still working, and Qt terminates the process for that.
- **Window snapping.** Dragging the window to a screen edge did nothing,
  because the new top bar covered the area that starts a window drag.
- **Maximized window corners.** The window kept rounded corners while
  maximized, leaving cut-outs in the screen's corners with the desktop
  showing through, plus a thin border that broke at each corner.
- **Downloads resume after a restart** instead of starting over, and a failed
  one can be retried from its card.
- **No way to escape fullscreen video** in the Browser tab.
- **Torrent row buttons** were rendering unstyled, and the overflow menu
  showed as a single faint dot. The same styling bug affected the Download,
  History and Images tabs.
- **The Video tab could not scroll**, so a long queue drew cards on top of
  each other. It also now fits smaller screens: the window is clamped to the
  display it opens on.

---

## Notes

- A fresh install starts with one browser shortcut (YouTube) and no
  bookmarks.
- Uninstalling now offers to remove your settings, history and saved logins.
  It keeps them by default, since an uninstall is often really a reinstall.
- Magnet links are registered for all accounts on the machine rather than
  only the one that ran the installer.
