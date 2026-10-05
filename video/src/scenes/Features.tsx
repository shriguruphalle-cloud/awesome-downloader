// The feature tour: each scene is the real app (captures/), a camera that
// moves to what matters, callouts on the real widgets, the cursor doing
// what you'd do, and keycaps for the shortcuts.
import React from "react";
import { useCurrentFrame } from "remotion";
import { C, CAPTURE, STAGE } from "../tokens";
import { rise, window01 } from "../components/anim";
import { Callout } from "../components/Callout";
import { Cursor } from "../components/Cursor";
import { FULL, Overlay, Rect, Stage, fit } from "../components/Stage";
import { Keycaps } from "../components/Text";
import { at, centre, rect, size, union } from "../meta";
import { Frame, useScene } from "./common";

const cap = (n: string) => `captures/${n}.png`;
const useEnter = (id: string) => {
  const f = useCurrentFrame();
  const { fps, sec } = useScene(id);
  return rise(f, fps, sec(0.25));
};
const KEYS_AT = { x: STAGE.x + STAGE.w / 2, y: STAGE.y + STAGE.h * 0.62 };

// Measured on the captures (rects meta.json doesn't carry).
const M = {
  downloadAll: [2240, 778, 226, 56] as Rect,
  engineChip: [1538, 825, 272, 62] as Rect,
  homeSearch: [624, 800, 1316, 114] as Rect,
  homeTiles: [764, 975, 1032, 140] as Rect,
  // inside a torrent card, from its top-left (measured on card 0)
  torrentStats: (c: Rect): Rect => [c[0] + 20, c[1] + 144, 2140, 84],
  torrentLine: (c: Rect): Rect => [c[0] + 4, c[1] + 112, c[2] - 8, 150],
  torrentSpikes: (c: Rect): Rect => [c[0] + 4, c[1] + 262, c[2] - 8, 34],
  menuEdit: [32 + 20, 340 + 160, 300, 50] as Rect,
  tileCheck: (r: Rect): [number, number] => [r[0] + 30, r[1] + 30],
};

// ---- Video: paste & fetch ---------------------------------------------------------------------------
export const VideoFetch: React.FC = () => {
  const { b, sec } = useScene("videoFetch");
  const f = useCurrentFrame();
  const enter = useEnter("videoFetch");
  const url = rect("video-empty", "url_entry");
  const res = rect("video-fetched", "res_combo");
  // Where Qt opens the list: over the box, its bottom on the box's bottom.
  // (The recorded position is off: the capture window is wider than the
  // screen, and the popup was pushed back onto it.)
  const pop = size("video-res-popup");
  const popAt: Rect = [res[0], res[1] + res[3] + 2 - pop.h, pop.w, pop.h];
  const popup = window01(f, b("popup"), b("popupOut"), 12);
  const [ux, uy] = centre(url, 0.3);
  const [rx, ry] = centre(res);
  return (
    <Frame id="videoFetch">
      <Stage
        enter={enter}
        layers={[
          { src: cap("video-empty") },
          { src: cap("video-fetching"), from: b("fetching"), fade: 10 },
          { src: cap("video-fetched"), from: b("fetched"), fade: 22 },
        ]}
        cam={[
          { at: 0, rect: FULL },
          { at: b("move"), rect: fit([36, 170, 1500, 540], 1.05), len: 60 },
          { at: b("zoom"), rect: fit(union(res, popAt, [220, 856, 1300, 68]), 1.22), len: 60 },
          { at: b("popupOut"), rect: FULL, len: 60 },
        ]}
      >
        <Overlay src={cap("video-res-popup")} at={popAt} show={popup} />
        <Callout rect={res} label="Every resolution shows its size" at={b("zoom", 0.8)} until={b("popup") - 6} side="right" />
        <Callout rect={popAt} label="Pick one — the size is estimated before you download" at={b("popup", 0.3)} until={b("popupOut") - 4} side="right" />
        <Cursor
          path={[
            { at: 0, x: 1500, y: 900 },
            { at: b("paste") - sec(0.45), x: ux, y: uy, len: 60 },
            { at: b("popup") - sec(0.35), x: rx + 60, y: ry, len: 70 },
            { at: b("popupOut") - sec(0.3), x: rx + 120, y: ry + 260, len: 50 },
          ]}
          clicks={[b("paste") - sec(0.4), b("popup") - sec(0.3)]}
          show={[sec(0.6), b("popupOut") + sec(0.8)]}
        />
      </Stage>
      <Keycaps keys={["Ctrl", "V"]} at={b("paste")} until={b("fetched") + sec(0.5)} x={KEYS_AT.x} y={KEYS_AT.y + 40} label="Paste the link" />
    </Frame>
  );
};

// ---- Video: format, audio, clip ----------------------------------------------------------------------
export const VideoOptions: React.FC = () => {
  const { b, sec, dur } = useScene("videoOptions");
  const enter = useEnter("videoOptions");
  const fmt = rect("video-fetched", "format_combo");
  const audio = rect("video-fetched", "audio_radio");
  const bitrate = rect("video-audio", "bitrate_combo");
  const range = rect("video-fetched", "range_check");
  const times = union(rect("video-clip", "start_entry"), rect("video-clip", "end_entry"));
  const options: Rect = fit([36, 760, 2488, 360], 1.12);
  return (
    <Frame id="videoOptions">
      <Stage
        enter={enter}
        layers={[
          { src: cap("video-fetched") },
          { src: cap("video-audio"), from: b("audio"), fade: 16, until: b("clip") - 8 },
          { src: cap("video-clip"), from: b("clip"), fade: 16 },
        ]}
        cam={[{ at: 0, rect: FULL }, { at: sec(0.5), rect: options, len: 60 }, { at: dur - sec(1.6), rect: FULL, len: 60 }]}
      >
        <Callout rect={fmt} label="MP4 · MKV · MOV" at={b("format")} until={b("audio") - 10} side="right" />
        <Callout rect={[bitrate[0], bitrate[1], 640, bitrate[3]]} label="MP3 · 128 to 320 kbps" at={b("audio", 0.4)} until={b("clip") - 10} side="right" />
        <Callout rect={[range[0], range[1], 1020, range[3]]} label="Just the part you need" at={b("clip", 0.3)} until={b("clip", 2.0)} side="right" />
        <Callout rect={times} label="From 1:20 to 4:05" at={b("clip", 2.0)} until={dur - sec(1.8)} side="right" />
        <Cursor
          path={[
            { at: 0, x: 1500, y: 1150 },
            { at: b("audio") - sec(0.2), x: audio[0] + 20, y: audio[1] + 18, len: 60 },
            { at: b("clip") - sec(0.2), x: range[0] + 18, y: range[1] + 18, len: 60 },
            { at: dur - sec(1.2), x: 1300, y: 1200, len: 60 },
          ]}
          clicks={[b("audio") - sec(0.2), b("clip") - sec(0.2)]}
          show={[sec(0.8), dur - sec(1)]}
        />
      </Stage>
    </Frame>
  );
};

// ---- Queue --------------------------------------------------------------------------------------------
export const Queue: React.FC = () => {
  const { b, sec, dur } = useScene("queue");
  const enter = useEnter("queue");
  const q0 = rect("video-queue", "queue_0");
  return (
    <Frame id="queue">
      <Stage
        enter={enter}
        layers={[{ src: cap("video-queue") }]}
        cam={[{ at: 0, rect: FULL }, { at: b("cards"), rect: fit([36, 760, 2488, 680], 1.02), len: 60 }, { at: dur - sec(1.5), rect: FULL, len: 60 }]}
      >
        {[0, 1, 2, 3].map((i) => (
          <Callout key={i} rect={rect("video-queue", `queue_${i}`)} at={b("cards", 0.5 + i * 0.35)} until={b("downloadAll") - 20} pad={4} radius={20} />
        ))}
        <Callout rect={M.downloadAll} label="Download all" at={b("downloadAll")} until={dur - sec(0.8)} side="bottom" color={C.ember} />
        <Cursor
          path={[{ at: 0, x: 1400, y: 1300 }, { at: b("downloadAll") - sec(0.2), x: M.downloadAll[0] + 110, y: M.downloadAll[1] + 30, len: 70 }]}
          clicks={[b("downloadAll") - sec(0.2)]}
          show={[b("cards"), dur - sec(0.6)]}
        />
        <Callout rect={[q0[0], q0[1] - 70, 600, 56]} label="One card per link" at={b("cards", 0.4)} until={b("downloadAll") - 20} side="right" color="rgba(0,0,0,0)" />
      </Stage>
      <Keycaps keys={["Ctrl", "V"]} at={b("keys")} until={b("cards", 0.6)} x={KEYS_AT.x} y={KEYS_AT.y} label="Paste several links at once" />
    </Frame>
  );
};

// ---- Download tab ------------------------------------------------------------------------------------------
export const Downloads: React.FC = () => {
  const { b, sec, dur } = useScene("downloads");
  const enter = useEnter("downloads");
  const top = rect("download", "card_4");
  const second = rect("download", "card_3");
  const active = union(rect("download", "card_0"), rect("download", "card_2"));
  return (
    <Frame id="downloads">
      <Stage
        enter={enter}
        layers={[{ src: cap("download") }]}
        cam={[
          { at: 0, rect: FULL },
          { at: b("waiting"), rect: fit([top[0], top[1], 1500, second[1] + second[3] - top[1]], 1.08), len: 60 },
          { at: b("progress"), rect: fit([active[0], active[1], 1500, active[3]], 1.04), len: 70 },
          { at: dur - sec(1.6), rect: FULL, len: 60 },
        ]}
      >
        <Callout rect={[top[0] + 260, top[1] + 100, 560, 44]} label="Waiting for a free slot" at={b("waiting", 0.5)} until={b("progress") - 10} side="right" />
        <Callout rect={[active[0] + 260, active[1] + 98, 760, 44]} label="Speed, size and time left — live" at={b("progress", 0.7)} until={dur - sec(1.8)} side="right" />
        <Callout rect={[active[0] + 14, active[1] + 14, 300, 200]} label="Real thumbnails, real titles" at={b("progress", 2.4)} until={dur - sec(1.8)} side="right" />
      </Stage>
    </Frame>
  );
};

// ---- Images ----------------------------------------------------------------------------------------------------
export const Images: React.FC = () => {
  const { b, sec, dur } = useScene("images");
  const enter = useEnter("images");
  const t2 = rect("images", "tile_2");
  const t5 = rect("images", "tile_5");
  const dl = rect("images", "download_btn");
  const [c2x, c2y] = M.tileCheck(t2);
  const [c5x, c5y] = M.tileCheck(t5);
  return (
    <Frame id="images">
      <Stage
        enter={enter}
        layers={[
          { src: cap("images-all") },
          { src: cap("images"), from: b("click1"), fade: 10, clip: t2 },
          { src: cap("images"), from: b("click2"), fade: 10 },
        ]}
        cam={[
          { at: 0, rect: FULL },
          { at: sec(2.2), rect: fit([60, 500, 2440, 540], 1.05), len: 70 },
          { at: b("save") - sec(0.9), rect: FULL, len: 60 },
        ]}
      >
        <Callout rect={[t2[0], t2[1], t2[2], t2[3]]} label="Leave out what you don't want" at={b("click1", 0.3)} until={b("save") - 30} side="bottom" />
        <Callout rect={dl} label="Saved at original quality" at={b("save", 0.2)} until={dur - sec(0.8)} side="top" color={C.ember} />
        <Cursor
          path={[
            { at: 0, x: 1300, y: 1200 },
            { at: b("click1"), x: c2x, y: c2y, len: 60 },
            { at: b("click2"), x: c5x, y: c5y, len: 50 },
            { at: b("save") - sec(0.2), x: dl[0] + 170, y: dl[1] + 32, len: 60 },
          ]}
          clicks={[b("click1"), b("click2"), b("save") - sec(0.2)]}
          show={[sec(1.0), dur - sec(0.6)]}
        />
      </Stage>
    </Frame>
  );
};

// ---- Torrents: the live graph ------------------------------------------------------------------------------------
export const Torrent: React.FC = () => {
  const { b, sec, dur } = useScene("torrent");
  const enter = useEnter("torrent");
  const card = rect("torrent-live", "card_0");   // the clip is scrolled a little further than the still
  return (
    <Frame id="torrent">
      <Stage
        enter={enter}
        layers={[{ src: "captures/torrent-live.mp4", video: true }]}
        cam={[
          { at: 0, rect: FULL },
          { at: b("zoom"), rect: fit([card[0], card[1] - 20, 1460, card[3] + 40], 1.08), len: 80 },
          { at: dur - sec(1.8), rect: FULL, len: 70 },
        ]}
      >
        <Callout rect={M.torrentLine(card)} label="Download speed, the last minute" at={b("line")} until={b("spikes") - 8} side="top" />
        <Callout rect={M.torrentSpikes(rect("torrent-live", "card_1"))} label="Upload: a spike every second" at={b("spikes")} until={b("stats") - 8} side="bottom" color={C.success} />
        <Callout rect={M.torrentStats(card)} label="Speeds, time left, peers, ratio" at={b("stats")} until={dur - sec(2)} side="top" />
      </Stage>
    </Frame>
  );
};

// ---- Browser: home ----------------------------------------------------------------------------------------------------
export const BrowserHome: React.FC = () => {
  const { b, sec, dur } = useScene("browserHome");
  const enter = useEnter("browserHome");
  return (
    <Frame id="browserHome">
      <Stage
        enter={enter}
        layers={[{ src: cap("browser-home") }]}
        cam={[
          { at: 0, rect: FULL },
          { at: b("zoom"), rect: fit(union(M.homeSearch, M.homeTiles, [960, 620, 640, 140]), 1.2), len: 80 },
          { at: b("wall"), rect: FULL, len: 80 },
        ]}
      >
        <Callout rect={M.engineChip} label="Google, DuckDuckGo, Bing, Brave or Yandex" at={b("engines")} until={b("wall") - 8} side="bottom" />
        <Callout rect={[0, 350, CAPTURE.w, 1090]} label="Live wallpaper: Silk" at={b("wall", 1.0)} until={dur - sec(1)} side="top" pad={-6} radius={4} />
      </Stage>
    </Frame>
  );
};

// ---- Browser: a page with video ---------------------------------------------------------------------------------------
export const BrowserSite: React.FC = () => {
  const { b, sec, dur } = useScene("browserSite");
  const enter = useEnter("browserSite");
  const dl = rect("browser-site", "download_btn");
  const shield = rect("browser-site", "shield_btn");
  const toolbar: Rect = fit([1500, 160, 1060, 480], 1.05);
  const [dx, dy] = centre(dl);
  return (
    <Frame id="browserSite">
      <Stage
        enter={enter}
        layers={[{ src: cap("browser-site") }]}
        cam={[{ at: 0, rect: FULL }, { at: b("zoom"), rect: toolbar, len: 70 }, { at: dur - sec(1.7), rect: FULL, len: 70 }]}
      >
        <Callout rect={dl} label="Lights up when a video plays" at={b("lit")} until={b("click") + sec(1.8)} side="bottom" color={C.ember} />
        <Callout rect={dl} label="Opens it in the Video tab" at={b("click", 1.9)} until={b("shield") - 4} side="bottom" color={C.ember} />
        <Callout rect={shield} label="AdGuard, built in" at={b("shield")} until={dur - sec(1.9)} side="bottom" color={C.success} />
        <Cursor
          path={[{ at: 0, x: 1900, y: 900 }, { at: b("click"), x: dx, y: dy, len: 70 }, { at: b("shield") - sec(0.2), x: dx + 40, y: dy + 220, len: 60 }]}
          clicks={[b("click")]}
          show={[b("zoom"), b("shield") + sec(0.4)]}
        />
      </Stage>
    </Frame>
  );
};

// ---- Bookmarks ------------------------------------------------------------------------------------------------------------
export const Bookmarks: React.FC = () => {
  const f = useCurrentFrame();
  const { b, sec, dur } = useScene("bookmarks");
  const enter = useEnter("bookmarks");
  const star = rect("browser-site", "bookmarks_btn");
  const all = rect("browser-site", "all_bookmarks");
  // Right-aligned under the button, as the app opens it (GlassPopup.open_under;
  // the recorded spot was pushed onto the screen, see VideoFetch).
  const pnl = size("bookmarks-panel");
  const panelAt: Rect = [all[0] + all[2] - pnl.w + 28, all[1] + all[3] - 16, pnl.w, pnl.h];
  const menuAt = at("bookmark-menu");
  const chip = at("bookmark-menu", "chip");
  const edit = size("bookmark-edit");
  const editAt: Rect = [(CAPTURE.w - edit.w * 1.25) / 2, (CAPTURE.h - edit.h * 1.25) / 2 + 60, edit.w * 1.25, edit.h * 1.25];
  const panel = window01(f, b("panel"), b("panelOut"), 12);
  const menu = window01(f, b("menu"), b("edit") + 4, 10);
  const dialog = window01(f, b("edit"), dur - sec(0.6), 14);
  const [ax, ay] = centre(all);
  return (
    <Frame id="bookmarks">
      <Stage
        enter={enter}
        layers={[{ src: cap("browser-site") }]}
        cam={[
          { at: 0, rect: FULL },
          { at: b("menu") - sec(0.6), rect: fit([0, 150, 1100, 560], 1.0), len: 60 },
          { at: b("edit") + sec(0.2), rect: FULL, len: 60 },
        ]}
      >
        <Callout rect={star} label="Bookmark this page" at={b("keys", 0.4)} until={b("panel") - sec(0.9)} side="left" />
        <Callout rect={all} label="All bookmarks" at={b("panel") - sec(0.8)} until={b("panel") + 4} side="bottom" />
        <Overlay src={cap("bookmarks-panel")} at={panelAt} show={panel} />
        <Overlay src={cap("bookmark-menu")} at={menuAt} show={menu} />
        <Callout rect={M.menuEdit} label="Edit… name and address" at={b("menu", 0.6)} until={b("edit") - 2} side="right" />
        <Overlay src={cap("bookmark-edit")} at={editAt} show={dialog} />
        <Cursor
          path={[
            { at: 0, x: 1800, y: 900 },
            { at: b("panel") - sec(0.2), x: ax, y: ay, len: 70 },
            { at: b("menu") - sec(0.2), x: chip[0] + chip[2] / 2, y: chip[1] + chip[3] / 2, len: 60 },
            { at: b("edit") - sec(0.2), x: M.menuEdit[0] + 60, y: M.menuEdit[1] + 25, len: 40 },
            { at: b("edit") + sec(1.2), x: editAt[0] + editAt[2] * 0.6, y: editAt[1] + editAt[3] * 0.62, len: 60 },
          ]}
          clicks={[b("panel") - sec(0.2), b("menu") - sec(0.2), b("edit") - sec(0.2)]}
          show={[sec(2.0), dur - sec(0.8)]}
        />
      </Stage>
      <Keycaps keys={["Ctrl", "D"]} at={b("keys")} until={b("keys", 2.0)} x={KEYS_AT.x} y={KEYS_AT.y} label="Bookmark the page" />
    </Frame>
  );
};

// ---- Private tabs -------------------------------------------------------------------------------------------------------------
export const Private: React.FC = () => {
  const { b, sec, dur } = useScene("private");
  const enter = useEnter("private");
  return (
    <Frame id="private">
      <Stage
        enter={enter}
        layers={[{ src: cap("browser-home") }, { src: cap("browser-private"), from: b("flip"), fade: 40 }]}
        cam={[{ at: 0, rect: FULL }, { at: b("flip", 1.2), rect: fit([300, 0, 1960, 1440], 1.0), len: 120 }, { at: dur - sec(1.4), rect: FULL, len: 60 }]}
      />
      <Keycaps keys={["Ctrl", "Shift", "N"]} at={b("keys")} until={b("flip", 0.6)} x={KEYS_AT.x} y={KEYS_AT.y} label="New private tab" />
    </Frame>
  );
};

// ---- History ------------------------------------------------------------------------------------------------------------------
export const History: React.FC = () => {
  const { b, sec, dur } = useScene("history");
  const enter = useEnter("history");
  const bar = rect("history-selected", "bar");
  const rows = [0, 1, 2].map((i) => rect("history-selected", `row_${i}`));
  return (
    <Frame id="history">
      <Stage
        enter={enter}
        layers={[{ src: cap("history") }, { src: cap("history-selected"), from: b("bar"), fade: 24 }]}
        cam={[{ at: 0, rect: FULL }, { at: b("bar", 0.3), rect: fit([36, 180, 2488, 720], 1.0), len: 60 }, { at: dur - sec(1.5), rect: FULL, len: 60 }]}
      >
        <Callout rect={bar} label="Remove from History · Delete files" at={b("bar", 0.6)} until={dur - sec(1.7)} side="bottom" />
        <Cursor
          path={[
            { at: 0, x: 1400, y: 1300 },
            ...rows.map((r, i) => ({ at: b("select") + i * 30 - 6, x: r[0] + 36, y: r[1] - 64 + r[3] / 2, len: i === 0 ? 60 : 24 })),
          ]}
          clicks={rows.map((_, i) => b("select") + i * 30 - 6)}
          show={[sec(1.5), b("bar") + sec(0.4)]}
        />
      </Stage>
    </Frame>
  );
};

// ---- Settings --------------------------------------------------------------------------------------------------------------------
export const Settings: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, sec, dur } = useScene("settings");
  const enter = useEnter("settings");
  const S = size("settings");
  const h = 690;
  const w = Math.round((h * S.w) / S.h);
  const panelIn = rise(f, fps, b("panel"));
  const box = { x: 960 - w / 2, y: 220 + (1 - panelIn) * 30, w, h };
  return (
    <Frame id="settings">
      <div style={{ opacity: 0.55 * enter, filter: "blur(6px)" }}>
        <Stage layers={[{ src: cap("video-fetched") }]} />
      </div>
      <div style={{ opacity: panelIn }}>
        <Stage
          box={box}
          size={S}
          radius={18}
          layers={[{ src: cap("settings") }]}
          cam={[{ at: 0, rect: [0, 0, S.w, S.h] }, { at: b("c1") - sec(0.4), rect: [0, 200, S.w, S.h - 200], len: 60 }, { at: b("c3") - sec(0.3), rect: [0, 560, S.w, S.h - 560], len: 60 }]}
        >
          <Callout rect={rect("settings", "concurrent")} label="Downloads at once: 1 to 6" at={b("c1")} until={b("c2") - 6} side="bottom" />
          <Callout rect={rect("settings", "palette")} label="Seven palettes" at={b("c2")} until={b("c3") - 6} side="bottom" />
          <Callout rect={rect("settings", "theme")} label="Night or day, one click" at={b("c3")} until={dur - sec(0.9)} side="bottom" />
        </Stage>
      </div>
    </Frame>
  );
};

