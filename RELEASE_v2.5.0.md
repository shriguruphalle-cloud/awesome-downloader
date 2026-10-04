# Awesome Downloader 2.5.0

A Windows desktop app for downloading video, audio, images and torrents —
one window, no ads and no sign-up.

## Download

**[AwesomeVideoDownloaderSetup.exe](../../releases/download/v2.5.0/AwesomeVideoDownloaderSetup.exe)** — SIZE_TBD

Everything is bundled. FFmpeg, the torrent engine and the AdGuard ad blocker
are included, so there is nothing else to install.

**Requirements:** Windows 10 or 11, 64-bit, with Microsoft Edge WebView2 —
part of Windows 10 and 11 already (the Browser tab uses it; if it's ever
missing, the tab says so and links to it). ARM64 works through Windows' x64
emulation. 32-bit Windows is not supported.

### Verify your download

```
certutil -hashfile AwesomeVideoDownloaderSetup.exe SHA256
```

```
SHA256_TBD
```

If the value differs, the download was cut short or altered — delete it and
download again.

### "Windows protected your PC"

The app is not code-signed, so SmartScreen warns the first time you run the
installer. Click **More info**, then **Run anyway**. A signing certificate is
the only thing that removes this, and it is not something this project has.

---

## What's new in 2.5.0

Awesome Downloader is free and open source (GPL-3.0): every line is on
[GitHub](https://github.com/shriguruphalle-cloud/awesome-downloader). No ads,
no tracking, no account.

### A real browser: H.264 video, AdGuard built in

The Browser tab now runs on Microsoft Edge WebView2, the engine Windows
already has, instead of the Chromium build inside Qt. That one couldn't play
H.264 or AAC at all — on many sites video stayed black. Now it plays H.264,
AAC, VP9 and AV1 like Edge does.

**AdGuard AdBlocker is built in** (the official release, GPL-3.0). With its
tracking, social, annoyance, cookie-notice and pop-up filters switched on,
it stopped every ad and tracker script in testing, and YouTube's player
data arrives with no ad placements. The shield in the toolbar shows how
many requests it blocked on the page; click it to turn AdGuard off for one
site or pause it everywhere. Private tabs, where extensions can't run, use
the app's own ad and tracker block list instead.

And the browser around it:

- **Download** in the toolbar (it lights up when a page is playing video),
  and a floating download button that appears on pages with a video.
- Files a site downloads carry the page's sign-in and show up in the
  **Download** tab with pause, resume and cancel. A downloaded **.torrent**
  offers itself to the Torrent tab; **magnet links** go straight there.
- A **bookmark button** beside Download bookmarks the page you're on and
  puts it on the **bookmarks bar** (now shown by default), then lets you
  rename it, take it back out, or open **All bookmarks** — a panel of every
  bookmark you can search, also one click away at the bar's end. A bookmark opens in a **new tab** (the page you're
  on stays put), switches to its tab if it's already open, and uses a blank
  New Tab rather than leaving one behind. Middle-click opens one behind.
- A **close all tabs** button at the left of the tab strip (Ctrl+Shift+W),
  undone with one click — or one Ctrl+Shift+T.
- Tabs you can drag to reorder, each its own card, a spinner while loading,
  a load bar that follows the page, and a **Now Playing** control for whatever is playing
  in any tab. Middle-click or Ctrl+click opens a link in a background tab
  that doesn't load until you look at it; tabs left in the background ten
  minutes go to sleep.
- A **private tab** turns the whole window black and white while it's in
  view — wallpaper, glass and colours; site icons keep theirs — so there's
  no mistaking it, and your colours come back the moment you leave it.
- Pop-ups a page opens by itself are blocked (with a one-click "Open"); ones
  you click still work, including sign-in windows.
- The tabs you had open come back next time (switchable off), unloaded
  until you open them; **Ctrl+Shift+T** reopens closed tabs even after a
  restart — and, with reopening at start switched off, brings back the
  whole last session. Bookmarks bar, history, zoom, find on page, print,
  save page, a choice of search engine, clear browsing data, developer
  tools.
- In a fullscreen video, touch the top of the screen and a round button
  drops down to leave full screen, as in Chrome.
- In-player video ads that get past AdGuard (a site's own pre-roll) are
  skipped the moment the player's own "Skip Ad" button appears.

**A new home page**, in the website's own type: *Awesome Downloader* in
Instrument Serif over one large search field (with suggestions from your
history and bookmarks), then your shortcuts and the sites you visit most —
picked from this browser's history, so the page fills itself in as you use
it — and the last few pages you had open. The search engine is one click
away on the search field itself, each shown with its own logo — Google,
DuckDuckGo, Bing, Brave Search or Yandex. Behind it, a
wallpaper: three that move — **Silk** (satin folding slowly in your
palette's light; the wallpaper from now on, for an existing install too,
unless you'd set a picture of your own), **Borealis** (the northern lights over dark hills) and
**Liquid** (your palette's colours flowing like ink in water) — or Glow,
Aurora, Peaks, Dunes, Ocean, Nebula, or any number of pictures of your own,
each removable, with the page taking its colours from the one shown. The
moving ones are drawn by the graphics card at half size and at most 30
frames a second, and stop whenever the page isn't in view or the window
isn't the one in use. The wallpaper runs on up behind the tabs and the
title bar, frosted, and its layers drift with the pointer (Customize turns
the motion off; so does Reduce motion). In the corner, a link to the
project's website and one to its source.

**Sign-ins made in the old Browser tab don't carry over** — the engine is
new. Sign in again once; downloads then use that session as before.

### A new look

The app now looks like its website: a deep navy backdrop with a faint grid,
lit in sky blue and indigo, and panels of frosted blue glass that let that
light through. Every tab, panel and dialog was redesigned to match — a
sliding tab indicator, clear section labels, rounded thumbnails, calm empty
states instead of blank tabs, and a light theme in soft periwinkle, with
frosted panels that lift off it, rather than white on white. Night is a
lit ocean-blue rather than near-black.

Buttons ease under the pointer and sink when pressed; the ember one catches a
band of light. Pages cross-fade as you switch tabs, new downloads and queued
links open into place and finished ones fold away, progress bars glide, a
finished download glows once, and light and dark dissolve into each other.
All of it follows Settings > Appearance > Reduce motion. Messages and
confirmations are the app's own glass panels now, not Windows' grey box, and
say what they do ("Delete", not "Yes").

**Seven colour palettes** (Settings > Appearance > Colour): Sapphire, the
original; Ruby and gold; Gold on espresso; Emerald and gold; Obsidian and
champagne; Amethyst; and Rose Gold — each with a night and a day version,
and each carried through the buttons, glass, text and title bar, not just
the background.

The name is set as the website sets it — *Awesome Downloader* in Instrument
Serif, the second word in italic and lit in your palette — in the title bar
and the About panel. The title bar is its own band of dark glass, set off
from the page by a fine rim of light and a soft shadow. The window controls are larger traffic-light
dots in a glass capsule, centred on the title row and lined up with the
pages' edge; hover them and they show what they do.

The window keeps Windows 11's rounded corners while it floats and goes
square when it's maximized or snapped to a screen edge. Pages widen with
the window instead of staying a narrow column on a big screen.

The painted backdrop replaces the Windows acrylic blur the app used before,
which is what caused the ghosting between tabs and the washed-out look when
maximized. The see-through acrylic style is still available in Settings.

### Opens in about 1.5 seconds

2.4.0 took over 8 seconds to show its window, because the single .exe
unpacked the whole program — Qt and the browser engine included — into a
temporary folder on every launch. 2.5.0 installs as a normal program folder
and starts straight from it. The installer is also about half the size.

### Settings

A new Settings panel (the gear in the top bar) collects everything in one
place, and changes apply immediately:

- where videos are saved
- how many downloads run at once
- the quality new links start on, and the default video format
- which sign-in to use for posts that need an account
- theme, colour palette, backdrop style, and a **Reduce motion** option
- whether to check for a new version at startup

### The History tab

Select downloads — the box on each row, a click anywhere on it, Shift+click
for a run, Ctrl+A for all — and a row opens under the header with what to
do with them (the filters and the search stay where they are):
**Remove from History**, or **Delete files** in one go (to the Recycle Bin,
so a slip is one Restore away). Filter by kind and search by name.

### The Images tab

The images fill the width of the window in as many columns as fit, each
with a check in its corner; ones you leave out dim back.

### The Video tab

Pasting a link now does exactly what Fetch does: it opens with its download
options. Pasting several at once stacks them in the queue, which has
**Download all** and **Clear queue**; a link leaves the queue the moment its
download starts (it's in the Download tab from then on). Clicking a queued
link opens it in the form instantly, without reading it from the site again.
New links start on the highest resolution they have, and audio on 320 kbps MP3.

### Downloads wait their turn

At most three downloads run at once by default (1–6 in Settings). The rest
show *Waiting for a free slot · #2 in line* and start as others finish.
**Start all** on thirty links used to open hundreds of connections at once —
slower overall, and the quickest way to get rate-limited.

### Playlists and channels

Paste a playlist or channel link and every video in it (up to 300) stacks as
its own card within seconds, instead of the app reading every video's
formats first. Each card can be set to **Best** or a specific resolution;
the nearest one the video actually has is used.

### Know when there's an update

The app asks GitHub once at startup whether a newer version exists, and if
so an **Update** button appears in the top bar that opens the release page.
It never downloads or runs anything on its own. The check can be turned off
in Settings, and the Updates panel now shows the app's own version status
alongside yt-dlp's.

### Torrent stats

Each torrent's card carries its **speed over the last minute** in its glass,
behind everything on it: download as a faint line with a soft fill, upload
as a faint dotted line, sliding by continuously — whether it's speeding up,
stalling or done is a shape you read at a glance. The scale only ever tops
out at a round number, so a trickle draws as a trickle. On top, its speeds,
how much is done, time left, time spent, peers and seeds, and share ratio;
a finished torrent shows how long it took, its average speed, what it has
uploaded and when it finished.

Torrents now keep libtorrent's **fast-resume data**: a finished or part-done
torrent comes back instantly at launch, with no "Checking Files" pass over
everything already downloaded and no wait for a magnet link's metadata —
and a torrent finished days ago no longer reads "finished today, took 4 s".
Magnet links open in this app by default (untick it on the Torrent tab).
The totals line counts what's downloading, complete and paused.

### Clearer errors

Failures now say what happened in plain words — *this video is private*,
*the site is rate-limiting you*, *not enough disk space* — with what to do
next, instead of yt-dlp's raw output.

---

### Lighter when idle

The app now does next to nothing while you aren't using it. The Browser tab
no longer checks on the browser engine fifty times a second (it's told when
work finishes instead); the ad blocker's count is asked for every few
seconds once a page has settled; pages sleep a few minutes after you leave
the Browser tab, and stop drawing while the window is minimized. The
torrent engine starts with your first torrent rather than with the app, and
an empty Torrent tab uses no time at all.

## Fixed

- **A page could be drawn off to the side after switching browser tabs**,
  with the app's backdrop in the gap. Each page now checks where its window
  really is whenever it's shown, resized or the window moves, and puts it
  back.
- **The search-engine list on the home page was drawn under the shortcuts**
  and could be read through. It's above them, and solid.
- **All bookmarks could open and vanish in a blink** when the page had the
  keyboard: the window handed it back to the page on the click, and the
  panel closed as the app lost focus. A click on the browser's own bars
  now leaves the keyboard where it is.
- **Panels kept a navy title bar whatever the colour palette.** Settings,
  About, Updates and every other panel now take the palette's colour for
  their title bar, its text and their edge.

- **Downloads could come out pixelated**, the picture smearing into blocks:
  when a site refused a piece of a video, yt-dlp skipped it and finished the
  file with a hole in it. A piece that keeps failing now fails the download
  instead (it can be retried, and resumes), retries back off rather than
  hammering the site, and four pieces are fetched at a time, not eight. At
  the same resolution, H.264 and AAC are now preferred — they play in every
  player.
- **In the Windows acrylic style, the window changed character on every
  minimize and maximize** — clear glass while floating, the painted backdrop
  when maximized. Both are now the same palette, with the desktop faintly
  through it while floating.

- **A sign-in borrowed from Chrome or Edge broke every fetch** — "Failed to
  decrypt with DPAPI" or "Could not copy Chrome cookie database", even for
  public videos. Those browsers lock their cookies against other apps now;
  the app carries on without them, and Sign-in moves to its own Browser tab.
- **Closing the Updates panel could, rarely, close the whole app** when its
  check finished at the same moment.
- **Save Thumbnail** left its button disabled for the rest of the session
  after one use (a 2.4.0 regression).
- **A sign-in chosen for a link wasn't used for its download** — the fetch
  succeeded, then the download failed with the same login error.
- **Audio downloads could report the wrong file** when another download
  finished in the same folder at the same moment.
- **Signing in through the Browser tab now shares only that site's cookies**
  with the downloader, in a temporary file removed afterwards. Before, every
  cookie from the Browser tab was written to one file that stayed on disk.
- **History thumbnails were squashed** into squares; they keep their shape now.

---

## Notes

- Your settings, queue, history and bookmarks carry over from 2.4.0.
  Browser sign-ins don't (see above).
- Installing over 2.4.0 replaces the old single .exe with the new program
  folder; nothing needs uninstalling first.
- Dependencies are pinned for the release build (`requirements-lock.txt`), and
  the test suite now lives in the repository (`tests/run_all.py`).
