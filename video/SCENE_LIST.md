# Showcase storyboard — Awesome Downloader 2.5.0

1920×1080, 60 fps, 4:52.5 (17,550 frames). Every app screen is the real UI,
captured by `capture/capture.py` from the app itself with made-up content
(see FEATURE_MANIFEST.md §5 for what is deliberately not shown). Timings live
in `src/config.ts`; this table is generated from it.

Persistent: the Sapphire backdrop (field + glows + 68 px grid, slow drift);
the **FREE & OPEN SOURCE · GPL-3.0** corner tag from *Why* until *Open source*;
burned-in captions on every explained point; a whoosh on every scene change.

| # | Time | Scene | On screen | Motion / sound |
|---|------|-------|-----------|----------------|
| 1 | 0:00.0 – 0:09.0 | **Hook** | "A video on one site. / A gallery on another. / A torrent. A playlist. A song." → "Five tools. *Endless ads.*" | Lines rise in turn; glass tool cards pop with red AD tags and jitter, then collapse to the centre. Swish ×3, whoosh. |
| 2 | 0:09.0 – 0:16.5 | **Logo** | Lightning logo, *Awesome Downloader* wordmark, tagline, FREE & OPEN SOURCE · GPL-3.0 badge | Logo springs in with a light ring; wordmark wipes on; badge pops. Chime. |
| 3 | 0:16.5 – 0:31.5 | **Why it exists** | Six cards: Video & audio (1,750+ sites), Images, Torrents, Browser, History, Seven palettes. Caption: "No ads. No account. No tracking." | Staggered rise with blur-to-focus. |
| 4 | 0:31.5 – 0:39.5 | **Tour** | The app window; each tab of the nav island lit in turn while the window changes to that tab | Callout ring steps across the six tabs; cross-fades between real captures. |
| 5 | 0:39.5 – 0:53.5 | **Paste a link** | Video tab: empty → *Fetching…* → fetched card (thumbnail, title, channel, duration); resolution list with sizes | Cursor clicks the field; **Ctrl + V** keycaps; camera to the form, then to the resolution box; the real drop-down opens. Click, keys, pops. |
| 6 | 0:53.5 – 1:06.5 | **Your format** | Format MP4/MKV/MOV → Audio only (MP3 · 128–320 kbps) → clip range 1:20 – 4:05 | Camera on Download options; cursor clicks Audio only, then the clip box; callouts on each. |
| 7 | 1:06.5 – 1:18.5 | **Queue** | Five links stacked as cards; Download all | **Ctrl + V** keycaps ("Paste several links at once"); cards ringed in turn; Download all clicked. |
| 8 | 1:18.5 – 1:30.5 | **Downloads wait their turn** | Download tab: two cards *Waiting for a free slot*, three running with speed / size / ETA | Camera to the waiting cards, then the running ones; callouts. |
| 9 | 1:30.5 – 1:42.5 | **Images** | Eight-image gallery; two images unticked and dimmed; Download Selected | Cursor unticks two tiles (each cross-fades in place), then clicks Download Selected. |
| 10 | 1:42.5 – 1:57.5 | **Torrents** | Three torrents (Ubuntu ISO, Big Buck Bunny, Sintel — legal, openly licensed) with the **live** speed graphs | Real 60 fps capture of the app's own graph painting; camera to the first card; callouts on the line, the upload spikes, the stats. |
| 11 | 1:57.5 – 2:10.5 | **A real browser** | Browser tab home page (from WebView2) with Silk wallpaper, search, engine chip, shortcuts | Camera to the search; callouts on the engine chip and the wallpaper. |
| 12 | 2:10.5 – 2:23.5 | **Press Download** | Our demo page *clips.example* playing a clip; Download button lit (detected by the app), Now Playing pill | Camera to the toolbar; cursor clicks Download; callout on the AdGuard shield. |
| 13 | 2:23.5 – 2:36.5 | **Bookmarks** | Bookmark button, All bookmarks panel, right-click menu, Edit bookmark dialog | **Ctrl + D** keycaps; the real panel, menu and dialog appear where the app opens them; cursor clicks through. |
| 14 | 2:36.5 – 2:46.5 | **Private tabs** | Home page in colour → the private tab in monochrome | **Ctrl + Shift + N** keycaps; slow dissolve; gentle push-in. |
| 15 | 2:46.5 – 2:58.5 | **History** | Eight downloads with thumbnails → three selected, the action row under the header | Cursor ticks three rows; callout on *Remove from History · Delete files*. |
| 16 | 2:58.5 – 3:09.5 | **Settings** | The Settings panel over the blurred window | Panel rises; camera scrolls it; callouts on downloads-at-once, palettes, theme. |
| 17 | 3:09.5 – 3:35.5 | **Themes** | The same screen through all 7 palettes at night, then all 7 by day | Cross-fades every 1.75 s / 1.45 s; the backdrop takes each palette's light; name pill (e.g. *Rose Gold · Day*). |
| 18 | 3:35.5 – 3:51.5 | **Only here** | Four tiles: live torrent graph (video), monochrome private tab, moving wallpaper, the lit Download button | Staggered rise. |
| 19 | 3:51.5 – 4:10.0 | **Light & private** | Four cards (starts from its folder; idle means idle; gentle on the GPU; no telemetry) + tech-stack chips | Staggered rise; chips pop. |
| 20 | 4:10.0 – 4:25.0 | **Free & open source** | Badge, repository URL, **Star on GitHub**, GPL-3.0, four points incl. "Buy the developer a coffee" | Badge pops (chime); star button glows. |
| 21 | 4:25.0 – 4:41.5 | **Get started** | 1 Download the installer · 2 Run it (More info → Run anyway) · 3 Paste a link; website + releases URLs | Steps rise in turn. |
| 22 | 4:41.5 – 4:52.5 | **Outro** | Logo + wordmark, tagline, awesome-downloader.pages.dev, **Download free** / **Star on GitHub**, badge, credit | Centre-out reveal; CTA pops; music resolves and fades. |

## Social cut (1:1, 1080×1080, 2:12.5)

Hook → Logo → Paste a link → Queue → Torrents → Press Download → Private tabs →
Themes → Free & open source → Outro. App scenes fill the square's width;
the headline sits above and the caption below, set large for phones; the
FREE & OPEN SOURCE badge stays at the bottom throughout.
