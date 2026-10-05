# QA report — showcase video

Every check below was run on the build in this folder; fixes were made before
the final render. Re-run any of it with `npm run stills`, `npm run qa`.

## 1. Frames: start, middle and end of every scene

66 stills (22 scenes × start/middle/end) plus 18 in-between moments
(keycaps, menus, dialogs, cursor clicks) were rendered and inspected on
contact sheets (`out/sheet-*.png`).

| Found | Fixed |
|---|---|
| **Privacy:** the torrent clip showed the capture profile's temp path, which contains this PC's user name | The clip is now scrubbed like the stills (`C:\Users\You\…`); every path field verified |
| Camera "zooms" that didn't zoom (full-width targets: Video form, Downloads, Torrent) | Narrower targets: real 1.5–2× push-ins |
| Torrent list scrolled by itself during the 16 s clip, callouts drifted off the cards | List held at the top while recording; callouts positioned from the clip's own card rects |
| All bookmarks panel and the resolution list drawn in the wrong place (the capture window is wider than the screen, so the popups were pushed onto it when they opened) | Placed where the app opens them, computed from their anchor widgets |
| Callout labels clipped at the frame edge ("Bookmark this page", Settings) | Moved to the open side |
| "Reduce motion" callout circled empty space (the checkbox is below the panel's visible part) | Now points at Theme |
| Captions wrapping to two lines over tall content (Themes, Only here) | Shortened |
| "Only here" tiles cropped off-subject (torrent graph, Download button) | Re-cropped |
| Performance / Open source top-heavy, repo URL wrapping | Re-spaced, URL on one line |
| Outro: logo alone before the name revealed | Centre-out reveal of both |
| Poster: wordmark touching the window, orphaned word | Resized, balanced |

## 2. Claims vs. FEATURE_MANIFEST.md and the code

Every caption and card was checked against the manifest (itself audited from
the code): 1,750+ sites (yt-dlp 2026.08.19: 1,751 extractors); playlists up to
300 (`PLAYLIST_LIMIT`); MP3 128–320 kbps; MP4/MKV/MOV; 3 downloads at once,
1–6 (`settings_dialog`); Recycle Bin deletes; WebView2 engine; AdGuard
bundled; Silk/Borealis/Liquid; Ctrl+D, Ctrl+Shift+N, Ctrl+V behaviours;
monochrome private tabs ("while you're **on** a private tab" — corrected from
"while one is open"); no telemetry (the app's own network calls are the
optional GitHub check, PyPI for yt-dlp, and sites' own favicons); wallpapers
≤ 30 fps and paused when hidden; GPL-3.0. Not shown, per the manifest:
real sites or content, Chrome/Edge cookie sign-in, window dragging/maximize,
the acrylic style, a startup time in seconds, "signed". No AdGuard block
count is shown (the capture had no real blocking to count, and a staged
number was removed).

All content is synthetic: thumbnails and gallery images drawn by
`capture/synth.py`; the browser's page is our own (`clips.example`, served
locally by WebView2); torrent names are openly licensed releases (Ubuntu,
Blender's Big Buck Bunny and Sintel).

## 3. Pacing

`npm run qa` requires every scene to leave at least 0.25 s per word + 1 s for
its title, caption and card text. First run: 3 scenes short (Performance
11.5 s for 24.5 s of text, Only here, Quick start). Fixed by trimming the
Performance copy and lengthening the three scenes. Now **22/22 pass**. Every
scene enters and leaves over 20–22 frames (minimum transition 12 frames);
motion is eased or sprung throughout — no linear moves.

## 4. Copy, links

121 on-screen strings proofread by hand; automated checks for doubled words,
stray spaces and common misspellings: 0 issues. Links checked live:
awesome-downloader.pages.dev → 200; github.com/shriguruphalle-cloud/awesome-downloader
→ 200; …/releases → 200. Installer name matches the release
(`AwesomeVideoDownloaderSetup.exe`).

## 5. The rendered files

Measured with ffprobe / ffmpeg `ebur128` (`npm run qa`):

| | final.mp4 | social-1x1.mp4 |
|---|---|---|
| Size | 1920×1080, 94 MB | 1080×1080, 25 MB |
| Video | H.264 High, yuv420p, BT.709 tagged | same |
| Frame rate | 60/1 constant | 60/1 constant |
| Frames | 17,550 = config (no drops) | 7,950 |
| Duration (video / audio) | 292.50 s / 292.50 s — in sync | 132.50 s / 132.50 s |
| Loudness | −14.1 LUFS integrated, LRA 2.4 | −14.3 LUFS, LRA 2.9 |
| True peak | −1.1 dBTP (no clipping) | −1.2 dBTP |

| Found | Fixed |
|---|---|
| First render: true peak −0.7 dBTP — the soundtrack was normalised to −1.5 dBTP, and AAC encoding added overshoot | Soundtrack normalised to −2 dBTP; `scripts/finalize.mjs` puts it in with ffmpeg's AAC and re-measures; now −1.1 dBTP |
| No colour tags in the H.264 stream (players may guess the wrong matrix) | `finalize.mjs` tags BT.709 without re-encoding the picture |

Sound effects are placed from the same `config.ts` beats the animation uses
(79 cues in the film), so clicks land on cursor clicks, key taps on keycaps,
pops on callouts, whooshes on scene changes. Fine UI text was checked in
frames pulled from the encoded MP4 (crisp at CRF 16).

## Not checked by machine

Spelling was proofread by hand (121 strings) — there's no dictionary in the
toolchain. Sound was checked by measurement, not by ear.
