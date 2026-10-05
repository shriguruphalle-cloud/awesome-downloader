# Awesome Downloader 2.5.0 — Feature manifest

Audited from the source in this repository (`app/`, `ui_qt/`, `installer.iss`,
`README.md`, `RELEASE_v2.5.0.md`, `NOTICE.md`, `tests/`). Every claim the
showcase video makes must trace back to a line here. Anything in section 5
must **not** appear in the video.

---

## 1. User-facing features (by category)

### Video & audio — Video tab (`ui_qt/video_tab.py`, `app/core/downloader.py`)
| Feature | What it does | Where |
|---|---|---|
| Paste-to-fetch | Pasting a link opens it with its download options (same as **Fetch**) | "Paste a link" card, URL field |
| 1,750+ sites | yt-dlp 2026.08.19 ships 1,751 extractors (YouTube, Instagram, Facebook, TikTok, X, Reddit, Vimeo, Twitch, SoundCloud …) | — |
| Resolution with size | Each resolution lists its estimated file size; new links start on the highest | Download options › Resolution |
| Container choice | MP4, MKV or MOV | Download options › Format |
| Audio only | MP3 at 128 / 192 / 256 / 320 kbps (320 default) | Download options › Audio only (MP3) |
| Clip range | Download only part of a video | Download options › "Download only part of this video" |
| Multi-link queue | Paste several links at once → each stacks as a card; **Download all**, **Clear queue** | Queue under the form |
| Playlists & channels | A playlist/channel link stacks every video (up to 300, `PLAYLIST_LIMIT`) as its own card; each card can be Best or a set resolution | Queue |
| Save folder | Per-tab save folder, remembered | "Save to" card (Browse / Open Folder) |

### Downloads — Download tab (`ui_qt/download_tab.py`, `widgets/download_card.py`)
| Feature | What it does |
|---|---|
| Progress cards | One card per download: thumbnail, title, speed/ETA, gliding progress bar |
| Pause / Resume / Cancel / Retry | Retry on a failed card resumes |
| Waiting its turn | At most 3 at once by default (1–6 in Settings); the rest read "Waiting for a free slot · #2 in line" |
| Plain-language errors | "this video is private", "the site is rate-limiting you", "not enough disk space" (`app/core/errors.py`) |

### Images — Images tab (`ui_qt/images_tab.py`)
| Feature | What it does |
|---|---|
| Whole galleries | Paste a post link → the whole carousel/gallery, not just the first image |
| Grid with checks | Fills the window in as many columns as fit; each image has a check; unchecked ones dim |
| Original quality | Saved byte-for-byte, no re-compression, real format kept (PNG transparency survives) |

### Torrents — Torrent tab (`ui_qt/torrent_tab.py`, `app/core/torrent_manager.py`, libtorrent)
| Feature | What it does |
|---|---|
| Magnet links & .torrent files | Add either; choose which files inside to fetch (`add_torrent_dialog.py`) |
| Live per-torrent speed graph | In each card's glass: download as a line with a soft fill, upload as little spikes one a second; round-number scale |
| Stats | Speeds, done, time left, time spent, peers & seeds, share ratio; finished torrents show duration, average speed, uploaded, finish time |
| Fast resume | Part-done/finished torrents come back instantly, no "Checking files" pass |
| Magnet handler | Magnet links open in the app by default (toggle on the Torrent tab) |

### Browser — Browser tab (`ui_qt/browser_tab.py`, `browser_chrome.py`, `browser_home.py`, `webview2.py`)
| Feature | What it does |
|---|---|
| Microsoft Edge WebView2 engine | Plays H.264, AAC, VP9, AV1 |
| AdGuard built in | Official AdGuard (GPL-3.0); the shield shows how many requests were blocked; per-site off / pause |
| Download button | Lights up when the page is playing video; sends it to the Video tab |
| Home page | Serif wordmark over one search field (suggestions from history & bookmarks); engine picker with logos: Google, DuckDuckGo, Bing, Brave Search, Yandex; shortcuts & most-visited tiles |
| Wallpapers | Live: **Silk** (default), **Borealis**, **Liquid**; still: Glow, Aurora, Peaks, Dunes, Ocean, Nebula; plus any number of your own pictures |
| Bookmarks | Bookmark button (Ctrl+D), bookmarks bar (Ctrl+Shift+B), **All bookmarks** searchable panel, right-click **Edit…** (name & address) |
| Tabs | Drag to reorder, close-all with one-click undo, reopen closed tabs (Ctrl+Shift+T), background tabs sleep |
| Private tabs | Ctrl+Shift+N — the whole window turns black & white while a private tab is in view (site icons keep colour) |
| Pop-up blocking | Unrequested pop-ups blocked with a one-click "Open" |
| Shortcuts | Ctrl+T, Ctrl+W, Ctrl+Shift+T, Ctrl+Shift+W, Ctrl+L / Alt+D, Ctrl+D, Ctrl+F, Ctrl+R / F5, Alt+←/→, Alt+Home, Ctrl+Shift+N, Ctrl+Shift+B, Ctrl+Tab |

### History — History tab (`ui_qt/history_tab.py`)
| Feature | What it does |
|---|---|
| Everything downloaded | Rows with thumbnails (keep their shape) |
| Filter & search | Filter by kind, search by name |
| Select & act | Checkbox / click / Shift+click / Ctrl+A → a row opens under the header: **Remove from History** or **Delete files** (to the Recycle Bin) |
| Per row | Play, open folder |

### App shell (`ui_qt/main_window.py`, `dialogs/`)
| Feature | What it does | Where |
|---|---|---|
| Settings panel | Save folder, simultaneous downloads, quality for new links, video format, sign-in source, theme, colour, backdrop, Reduce motion, update check, data folder, Uninstall | Gear in the title bar |
| Update check | Asks GitHub once at startup; an **Update** pill appears; never downloads/runs anything itself | Title bar, Settings |
| yt-dlp updates | Updates panel compares and updates yt-dlp in one click | ↻ in the title bar |
| Theme switch | Night / day | Moon in the title bar |
| Donate | Coffee cup → PayPal or UPI QR | Title bar |
| Single instance | A second launch (or a magnet link) goes to the open window | — |
| Installer / uninstaller | Start menu, Settings › Apps, or Settings › Your data › Uninstall; asks whether to keep data | `installer.iss` |

---

## 2. Themes & appearance (exact values from the code)

**Fonts** (`ui_qt/fonts/`, OFL): **Instrument Serif** (regular + italic) for the wordmark;
**Inter** (variable) for all UI text. (Home page also uses Archivo and IBM Plex Mono.)

**Wordmark:** "Awesome" upright in the text colour, *Downloader* italic, filled with a
gradient brand-light → brand → the palette's second light; lightning logo
(`app_icon.png`) as tall as the letters, spaced like the words.

**Night tokens (Sapphire, `theme.DARK`)**: text `#eaf2ff`, muted `#a9b4c9`, faint `#7b86a0`,
brand/eyebrow `#38bdf8`, brand hover `#7dd3fc`, accent (ember) `#ff6a13` (top `#ff8b47`,
text on ember `#170b04`), success `#34d399`, danger `#ff6b6b`, warning `#fcd34d`,
ink `#101c3e`, glass `rgba(255,255,255,0.05)` with border `rgba(255,255,255,0.11)`,
recessed fields `rgba(4,10,30,0.43)`.

**Backdrop (`cinema.py`)**: field + soft elliptical glows + a faint white grid at 68 px.

**Seven palettes, each night & day** (`ui_qt/palettes.py`, base · brand · accent · glows):

| Palette | Night | Day |
|---|---|---|
| Sapphire | `#0d1734` · `#38bdf8` · `#ff6a13` · sky/indigo/cyan | `#b8c5e0` · `#0284c7` · `#d0460e` |
| Ruby | `#18080d` · `#e296a0` · `#d6b070` · satin sheen | `#e2d4d8` · `#8c1e3a` · `#96203e` |
| Gold | `#130f0a` · `#dec284` · `#d6a84c` · sheen | `#e2daca` · `#804816` · `#965c1c` |
| Emerald | `#061512` · `#7cceac` · `#d4ac5c` | `#ceded6` · `#0c684e` · `#0a684e` |
| Obsidian | `#0b0b0d` · `#d6c098` · `#cea65e` · sheen | `#d6d7dc` · `#806228` · `#18181b` |
| Amethyst | `#120c20` · `#beb2f5` · `#b0a2ee` | `#dcd6ea` · `#5c30b2` · `#6842c4` |
| Rose Gold | `#1a1113` · `#e8b8a8` · `#da9680` · sheen | `#e8dcd8` · `#924e3c` · `#a0523e` |

Hidden **Private** (monochrome) palette is applied only while a private tab is in view.

**Backdrop styles:** Cinematic (default, painted), Solid, Desktop (acrylic). **Reduce motion** honoured everywhere.

**Design language:** deep field with a faint grid, lit from above; frosted glass cards
with a 1 px rim; sliding tab indicator in a glass "island"; ember primary button with a
light sheen; traffic-light window dots in a glass capsule; eyebrow section labels in brand
colour, uppercase, letter-spaced.

---

## 3. Differentiators

1. Video, audio, images, torrents **and** a real browser in one window (most tools do one).
2. A built-in browser with **AdGuard** and a **Download** button that lights up when a page plays video.
3. **Per-torrent live speed graphs** drawn into each card (download line + upload spikes).
4. **Private tabs turn the whole window monochrome** — impossible to mistake.
5. **Live wallpapers** on the browser's home page drawn by the GPU, paused when not in view.
6. **Seven palettes × night/day**, carried through every button, panel and title bar.
7. Plain-language errors; downloads queue politely (max 3 at once by default).
8. Gallery-complete image downloads at original bytes.
9. No ads, no account, no telemetry — free and open source (GPL-3.0).

---

## 4. Tech, platforms, install, licence

- **Stack:** Python 3.12 · PySide6 / Qt 6 · yt-dlp · FFmpeg · libtorrent · Microsoft Edge WebView2 · AdGuard (MV3) · PyInstaller · Inno Setup.
- **Platforms:** Windows 10 and 11, 64-bit (ARM64 via x64 emulation). Not 32-bit.
- **Install:** download `AwesomeVideoDownloaderSetup.exe` from GitHub Releases
  (`https://github.com/shriguruphalle-cloud/awesome-downloader/releases/latest`) or the website
  `https://awesome-downloader.pages.dev`, run it (SmartScreen: **More info → Run anyway** — unsigned),
  paste a link. Everything (FFmpeg, torrent engine, AdGuard) is bundled.
- **From source:** `git clone https://github.com/shriguruphalle-cloud/awesome-downloader` ·
  `py -3.12 -m venv .venv312` · `.venv312\Scripts\python.exe -m pip install -r requirements.txt` ·
  `.venv312\Scripts\python.exe main_qt.py`.
- **Licence:** GPL-3.0 (bundled FFmpeg GPL build, AdGuard GPL-3.0; fonts OFL — see `NOTICE.md`).
- **Privacy (verified in code):** no analytics/telemetry; the app's own network calls are the
  optional GitHub update check, PyPI when you check yt-dlp, and each site's own `favicon.ico`.
- **Performance (verified in code):** folder install, no unpack on launch; idle tabs, the
  browser and the torrent engine do no work when not in use; live wallpapers ≤ 30 fps at half
  size and stop when hidden.

---

## 5. Do NOT show in the video

| Item | Why |
|---|---|
| Real third-party sites, videos, logos of content, or anyone's personal data | Copyright / privacy — demo uses synthetic content only |
| Sign-in borrowed from Chrome/Edge cookies | Broken by those browsers' cookie locks (release notes) |
| DRM content | Not supported, by design |
| Dragging the window to maximize | Known open glitch (frameless maximize) |
| Desktop (acrylic) backdrop style | Legacy option; not the showcase look |
| Full-screen-exit button, in-player ad skip | Need real sites to demonstrate honestly |
| A specific startup time in seconds | Not re-measured on this build — say "starts straight from its folder" instead |
| "Code-signed" / "SmartScreen-free" | The installer is unsigned |
| macOS / Linux | Windows only |
