// Everything you might want to change without touching animation code:
// the copy, how long each scene lasts, when things happen inside it, the
// links, and the music. Times are in seconds from the start of the scene.
// After editing, re-run `npm run audio` (the sound effects follow these
// times) and `npm run render`.

export const FPS = 60;

export const LINKS = {
  website: "awesome-downloader.pages.dev",
  repo: "github.com/shriguruphalle-cloud/awesome-downloader",
  releases: "github.com/shriguruphalle-cloud/awesome-downloader/releases",
  installer: "AwesomeVideoDownloaderSetup.exe",
  license: "GPL-3.0",
};

// Drop a track at public/music.mp3 and set this to "music.mp3" to use it
// instead of the generated ambient bed. Sound effects stay either way.
export const MUSIC: string | null = null;

export type Sfx = { t: number; k: "click" | "key" | "pop" | "whoosh" | "chime" | "swish" };

export type Scene = {
  id: string;
  seconds: number;
  eyebrow?: string;
  title?: string;
  caption?: string;
  beats?: Record<string, number>;
  sfx?: Sfx[];
};

export const SCENES: Scene[] = [
  {
    id: "hook",
    seconds: 9,
    beats: { l1: 0.4, l2: 1.9, l3: 3.4, punch: 5.3 },
    sfx: [{ t: 0.4, k: "swish" }, { t: 1.9, k: "swish" }, { t: 3.4, k: "swish" }, { t: 5.3, k: "whoosh" }],
  },
  {
    id: "logo",
    seconds: 7.5,
    beats: { logo: 0.3, word: 1.0, tagline: 2.2, badge: 3.2 },
    sfx: [{ t: 0.3, k: "chime" }, { t: 3.2, k: "pop" }],
  },
  {
    id: "why",
    seconds: 15,
    eyebrow: "Why it exists",
    title: "One app for everything you download",
    caption: "No ads. No account. No tracking.",
    beats: { cards: 1.0, stagger: 0.32, footer: 4.2 },
  },
  {
    id: "tour",
    seconds: 8,
    eyebrow: "A quick tour",
    title: "Six tabs. One window.",
    caption: "Video · Torrent · Images · Browser · Download · History",
    beats: { sweep: 2.0, step: 0.75 },
  },
  {
    id: "videoFetch",
    seconds: 14,
    eyebrow: "Video & audio",
    title: "Paste a link. That's it.",
    caption: "Copy a link and press Ctrl + V — the app reads it right away. Every resolution shows its size.",
    beats: { move: 0.8, paste: 2.4, fetching: 2.7, fetched: 4.6, zoom: 5.6, popup: 8.0, popupOut: 11.6 },
    sfx: [{ t: 2.0, k: "click" }, { t: 2.4, k: "key" }, { t: 2.55, k: "key" }, { t: 4.6, k: "pop" }, { t: 7.7, k: "click" }, { t: 8.0, k: "pop" }],
  },
  {
    id: "videoOptions",
    seconds: 13,
    eyebrow: "Your format",
    title: "Video, audio, or just a clip",
    caption: "MP4, MKV or MOV. MP3 at up to 320 kbps. Or download only the part you need.",
    beats: { format: 0.8, audio: 3.6, clip: 7.6 },
    sfx: [{ t: 3.4, k: "click" }, { t: 3.6, k: "pop" }, { t: 7.4, k: "click" }, { t: 7.6, k: "pop" }],
  },
  {
    id: "queue",
    seconds: 12,
    eyebrow: "Queue",
    title: "Many links at once",
    caption: "Paste several links — or a whole playlist, up to 300 videos. Then press Download all.",
    beats: { keys: 1.0, cards: 2.0, downloadAll: 7.2 },
    sfx: [{ t: 1.0, k: "key" }, { t: 1.15, k: "key" }, { t: 2.0, k: "pop" }, { t: 7.0, k: "click" }],
  },
  {
    id: "downloads",
    seconds: 12,
    eyebrow: "Download tab",
    title: "Downloads wait their turn",
    caption: "Three run at once by default (1–6 in Settings). The rest wait politely — pause, resume or cancel any time.",
    beats: { waiting: 1.6, progress: 5.8 },
    sfx: [{ t: 1.6, k: "pop" }, { t: 5.8, k: "pop" }],
  },
  {
    id: "images",
    seconds: 12,
    eyebrow: "Images",
    title: "The whole gallery, not just the first image",
    caption: "Paste a post link and pick what you want. Images are saved byte for byte, at original quality.",
    beats: { click1: 4.2, click2: 5.6, save: 8.4 },
    sfx: [{ t: 4.2, k: "click" }, { t: 5.6, k: "click" }, { t: 8.2, k: "click" }],
  },
  {
    id: "torrent",
    seconds: 15,
    eyebrow: "Torrents",
    title: "Every torrent draws its own graph",
    caption: "Magnet links and .torrent files. Download as a line, upload as little spikes — live, in each card.",
    beats: { zoom: 2.4, line: 4.6, spikes: 7.4, stats: 10.4 },
    sfx: [{ t: 4.6, k: "pop" }, { t: 7.4, k: "pop" }, { t: 10.4, k: "pop" }],
  },
  {
    id: "browserHome",
    seconds: 13,
    eyebrow: "Browser",
    title: "A real browser, built in",
    caption: "Microsoft Edge WebView2 inside the app, with a home page that moves: Silk, Borealis or Liquid.",
    beats: { zoom: 3.2, engines: 6.0, wall: 8.8 },
    sfx: [{ t: 6.0, k: "pop" }, { t: 8.8, k: "pop" }],
  },
  {
    id: "browserSite",
    seconds: 13,
    eyebrow: "Browser",
    title: "See a video? Press Download.",
    caption: "The Download button lights up when a page plays video. AdGuard is built in to block ads and trackers.",
    beats: { zoom: 2.0, lit: 3.4, click: 6.4, shield: 8.6 },
    sfx: [{ t: 3.4, k: "pop" }, { t: 6.4, k: "click" }, { t: 8.6, k: "pop" }],
  },
  {
    id: "bookmarks",
    seconds: 13,
    eyebrow: "Bookmarks",
    title: "Bookmarks, done properly",
    caption: "Bookmark a page with Ctrl + D. All bookmarks is one click away — right-click any of them to edit it.",
    beats: { keys: 0.9, panel: 3.4, panelOut: 6.6, menu: 7.4, edit: 9.4 },
    sfx: [{ t: 0.9, k: "key" }, { t: 1.05, k: "key" }, { t: 3.2, k: "click" }, { t: 3.4, k: "pop" }, { t: 7.2, k: "click" }, { t: 9.2, k: "click" }, { t: 9.4, k: "pop" }],
  },
  {
    id: "private",
    seconds: 10,
    eyebrow: "Private tabs",
    title: "Private looks private",
    caption: "Press Ctrl + Shift + N: while you're on a private tab, the whole window turns black and white.",
    beats: { keys: 1.2, flip: 2.6 },
    sfx: [{ t: 1.2, k: "key" }, { t: 1.3, k: "key" }, { t: 1.4, k: "key" }, { t: 2.6, k: "whoosh" }],
  },
  {
    id: "history",
    seconds: 12,
    eyebrow: "History",
    title: "Everything you've downloaded",
    caption: "Thumbnails, filters and search. Select a few to remove them — deleted files go to the Recycle Bin.",
    beats: { select: 4.0, bar: 5.4 },
    sfx: [{ t: 3.8, k: "click" }, { t: 4.3, k: "click" }, { t: 4.8, k: "click" }, { t: 5.4, k: "pop" }],
  },
  {
    id: "settings",
    seconds: 11,
    eyebrow: "Settings",
    title: "Your choices, applied instantly",
    caption: "Save folder, downloads at once, quality, format, theme, colour, Reduce motion — all in one panel.",
    beats: { panel: 0.8, c1: 3.0, c2: 5.0, c3: 7.0 },
    sfx: [{ t: 0.8, k: "swish" }, { t: 3.0, k: "pop" }, { t: 5.0, k: "pop" }, { t: 7.0, k: "pop" }],
  },
  {
    id: "themes",
    seconds: 26,
    eyebrow: "Make it yours",
    title: "Seven palettes. Night and day.",
    caption: "Each palette has a night and a day — carried through every button and panel.",
    beats: { start: 1.6, night: 1.75, day: 1.45 },
  },
  {
    id: "unique",
    seconds: 16,
    eyebrow: "Only here",
    title: "Details you won't find elsewhere",
    caption: "Small things, done with care — all of it in the free version, because there's only one.",
    beats: { tiles: 1.0, stagger: 0.4 },
  },
  {
    id: "performance",
    seconds: 18.5,
    eyebrow: "Light & private",
    title: "Quiet when you're not using it",
    caption: "Light on your PC, and private by design.",
    beats: { cards: 1.0, stagger: 0.55, stack: 8.5 },
  },
  {
    id: "openSource",
    seconds: 15,
    eyebrow: "Free & open source",
    title: "Free forever. Open to everyone.",
    caption: "Every line is on GitHub under GPL-3.0. Star it, fork it, report a bug, send a pull request.",
    beats: { badge: 0.8, repo: 2.4, star: 4.4, points: 6.4 },
    sfx: [{ t: 0.8, k: "chime" }, { t: 4.4, k: "pop" }],
  },
  {
    id: "quickStart",
    seconds: 16.5,
    eyebrow: "Get started",
    title: "Up and running in three steps",
    caption: "Windows 10 and 11, 64-bit. FFmpeg, the torrent engine and AdGuard are all included.",
    beats: { s1: 1.0, s2: 4.6, s3: 8.2, req: 11.0 },
    sfx: [{ t: 1.0, k: "pop" }, { t: 4.6, k: "pop" }, { t: 8.2, k: "pop" }],
  },
  {
    id: "outro",
    seconds: 11,
    beats: { logo: 0.4, url: 1.8, cta: 2.8 },
    sfx: [{ t: 0.4, k: "chime" }],
  },
];

// ---- copy used inside scenes ----------------------------------------------------------------
export const COPY = {
  hook: ["A video on one site.", "A gallery on another.", "A torrent. A playlist. A song."],
  hookPunch: ["Five tools.", "Endless ads."],
  tagline: "Video, audio, images and torrents — in one window.",
  badge: "Free & open source",
  why: [
    { icon: "video", title: "Video & audio", line: "1,750+ sites · MP4, MKV, MOV, MP3" },
    { icon: "images", title: "Images", line: "Whole galleries, original quality" },
    { icon: "torrent", title: "Torrents", line: "Magnet links & .torrent files" },
    { icon: "browser", title: "Browser", line: "Edge engine, AdGuard built in" },
    { icon: "history", title: "History", line: "Every download, one click away" },
    { icon: "palette", title: "Seven palettes", line: "Night and day, every one" },
  ],
  tabs: ["Video", "Torrent", "Images", "Browser", "Download", "History"],
  unique: [
    { title: "Live torrent graphs", line: "Download line, upload spikes" },
    { title: "Monochrome private tabs", line: "Impossible to mistake" },
    { title: "Moving wallpapers", line: "Drawn by the GPU, paused when hidden" },
    { title: "A Download button that knows", line: "Lights up when video plays" },
  ],
  performance: [
    { title: "Starts straight from its folder", line: "Nothing to unpack on launch" },
    { title: "Idle means idle", line: "Background tabs sleep while you're away" },
    { title: "Gentle on the GPU", line: "Wallpapers ≤ 30 fps, paused when hidden" },
    { title: "No telemetry", line: "The only call home: an optional update check" },
  ],
  stack: ["Python 3.12", "Qt 6 · PySide6", "yt-dlp", "FFmpeg", "libtorrent", "WebView2", "AdGuard"],
  openSource: [
    "No paywall, no account, no ads",
    "No telemetry or tracking",
    "Report issues · send pull requests",
    "Buy the developer a coffee, if you like",
  ],
  steps: [
    { title: "Download the installer", detail: "AwesomeVideoDownloaderSetup.exe", sub: "from GitHub Releases or the website" },
    { title: "Run it", detail: "More info → Run anyway", sub: "Windows asks once: the installer isn't code-signed yet" },
    { title: "Paste a link", detail: "Ctrl + V", sub: "That's it — everything is bundled" },
  ],
  outroCta: ["Download free", "Star on GitHub"],
  credit: "Built by Shriguru Phalle",
};

export const PALETTE_ORDER = ["sapphire", "ruby", "gold", "emerald", "obsidian", "amethyst", "rose"] as const;
export const PALETTE_LABEL: Record<string, string> = {
  sapphire: "Sapphire", ruby: "Ruby", gold: "Gold", emerald: "Emerald",
  obsidian: "Obsidian", amethyst: "Amethyst", rose: "Rose Gold",
};

// ---- derived ------------------------------------------------------------------------------------
export const sceneFrames = (s: Scene) => Math.round(s.seconds * FPS);
export const TOTAL_FRAMES = SCENES.reduce((n, s) => n + sceneFrames(s), 0);
export const sceneStart = (id: string) => {
  let n = 0;
  for (const s of SCENES) {
    if (s.id === id) return n;
    n += sceneFrames(s);
  }
  throw new Error("no scene " + id);
};
export const scene = (id: string): Scene => {
  const s = SCENES.find((x) => x.id === id);
  if (!s) throw new Error("no scene " + id);
  return s;
};
