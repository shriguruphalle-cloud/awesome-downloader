# Awesome Downloader 3.0.0

A Windows desktop app for downloading video, audio, images and torrents —
one window, no ads and no sign-up. Now with a music player.

## Download

**[AwesomeVideoDownloaderSetup.exe](../../releases/download/v3.0.0/AwesomeVideoDownloaderSetup.exe)** — 111 MB

Everything is bundled: FFmpeg, the torrent engine and the AdGuard ad blocker.
**Requirements:** Windows 10 or 11, 64-bit.

Already on 2.5? Click **Update available** in the title bar — the update is
signed, checked and installed from inside the app.

### Verify your download

```
certutil -hashfile AwesomeVideoDownloaderSetup.exe SHA256
```

```
70DE51C16E0B008882AE5DC159A1D8D41EF9196567C4C033E5CEF8C7EAE3FAF5
```

## What's new in 3.0.0

### Music
- A new Music tab: search YouTube Music plus free music from Openverse and the
  Internet Archive, with suggestions as you type, and an Explore page of
  charts and trending songs by genre.
- Songs start at once and play through without skipping; one song can blend
  into the next, and every song plays at the same loudness.
- Up next learns as you listen: it keeps to the language and mood you're
  playing, and never repeats a song.
- Lyrics that follow the song line by line, in every script available, with a
  translation under each line.
- Full-screen player on a living background in the cover's colours that moves
  with the beat (or a still cover); the queue, equalizer and song details pop
  in around it, and it fits any window size.
- A 10-band equalizer with presets, bass boost and open-source spatial audio
  for headphones.
- Each song's original album cover instead of a video frame, and its quality
  (Opus, Lossless, Hi-Res, Dolby Atmos) where it's known.
- A mini player floats over other windows while the app is minimised; its
  controls appear under the mouse, and a corner drag resizes it.
- Import playlists from Spotify, Apple Music or YouTube, or paste a list.
- The last song is waiting (paused) when you open the app, and the app opens
  on the tab you left.
- Save any song as a tagged 320 kbps MP3 with its cover.

### Downloads
- Clips (a part of a video) no longer fail with "ffmpeg exited with code":
  when cutting from the stream fails, the video is fetched whole and cut on
  your PC, to the second.
- YouTube: downloads that stuck at "Starting…" or found "only images" work
  again.
- Video links no longer end up in the Images tab.
- Instagram reels, posts and public accounts download without signing in;
  Reddit videos download from the Browser tab.
- Paste anything into Video or Images: channels and accounts queue all their
  posts, and links go to the right tab by themselves.

### Everything else
- F11 (or the button beside the window dots) puts the whole app in full screen.
- Browser: bookmark star, picture-in-picture, dangerous-site blocking.
- Your history, bookmarks and queues are encrypted on this PC (Settings).
- Closing the window closes the app, music included — it only plays on while
  minimised.
- Installers downloaded for updates are cleared once the update is done.
- The installer shows FFmpeg's progress instead of seeming frozen.
