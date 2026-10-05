// Automated QA for the showcase:
//  1. pacing  -- every scene leaves time to read what it says
//                (at least 0.25 s per word + 1 s, title and caption together)
//  2. copy    -- every string that appears on screen, for a proofread, plus a
//                check for doubled words and stray spaces
//  3. links   -- the website, repository and releases page answer
//  4. output  -- if final.mp4 / social-1x1.mp4 exist: 60 fps, frame count,
//                audio and video the same length, loudness ~ -14 LUFS, no clipping
//
//   node scripts/qa.mjs            (writes out/qa.json, prints a summary)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { COPY, FPS, LINKS, SCENES, TOTAL_FRAMES } from "../src/config.ts";
import { SOCIAL_SCENES } from "../src/socialConfig.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const report = { pacing: [], copy: [], copyIssues: [], links: [], output: {} };
const words = (s) => (s || "").split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length;

// ---- 1. pacing ---------------------------------------------------------------------------------
const extraText = {
  hook: [...COPY.hook, ...COPY.hookPunch].join(" "),
  logo: COPY.tagline + " " + COPY.badge,
  why: COPY.why.map((c) => c.title + " " + c.line).join(" "),
  unique: COPY.unique.map((c) => c.title + " " + c.line).join(" "),
  performance: COPY.performance.map((c) => c.title + " " + c.line).join(" ") + " " + COPY.stack.join(" "),
  openSource: COPY.openSource.join(" ") + " " + LINKS.repo,
  quickStart: COPY.steps.map((s) => s.title + " " + s.detail + " " + s.sub).join(" "),
  outro: COPY.tagline + " " + LINKS.website + " " + COPY.outroCta.join(" ") + " " + COPY.credit,
};
for (const s of SCENES) {
  // The caption is read once; the title alongside it. Card text (extraText) is
  // read while the scene holds, after its reveal.
  const text = [s.title, s.caption].filter(Boolean).join(" ");
  const n = words(text) + words(extraText[s.id] || "");
  const visible = s.seconds - 0.6 - 0.35 - 0.6; // caption in at 0.6 s, out 0.35 s early, fades
  const need = 0.25 * n + 1;
  report.pacing.push({ id: s.id, seconds: s.seconds, words: n, need: +need.toFixed(2), visible: +visible.toFixed(2), ok: visible >= need });
}

// ---- 2. copy ------------------------------------------------------------------------------------
const strings = [];
const walk = (v) => {
  if (typeof v === "string") strings.push(v);
  else if (Array.isArray(v)) v.forEach(walk);
  else if (v && typeof v === "object") Object.values(v).forEach(walk);
};
SCENES.forEach((s) => [s.eyebrow, s.title, s.caption].forEach(walk));
walk(COPY);
walk(LINKS);
report.copy = [...new Set(strings.filter(Boolean))];
for (const s of report.copy) {
  if (/\b(\w+) \1\b/i.test(s)) report.copyIssues.push(`doubled word: "${s}"`);
  if (/ {2,}|\s[,;:]|\s\.(?!torrent)/.test(s)) report.copyIssues.push(`stray space: "${s}"`);
  if (/\bteh\b|\brecieve|\bseperat|\boccured|\buntill\b|\bpallet/i.test(s)) report.copyIssues.push(`spelling: "${s}"`);
}

// ---- 3. links --------------------------------------------------------------------------------------
for (const url of [LINKS.website, LINKS.repo, LINKS.releases]) {
  try {
    const r = await fetch("https://" + url, { method: "GET", redirect: "follow" });
    report.links.push({ url, status: r.status, ok: r.ok });
  } catch (e) {
    report.links.push({ url, status: String(e), ok: false });
  }
}

// ---- 4. output -------------------------------------------------------------------------------------
const probe = (file) => {
  const p = spawnSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_type,codec_name,avg_frame_rate,nb_frames,duration,width,height,pix_fmt,sample_rate,channels", "-of", "json", file], { encoding: "utf8" });
  return JSON.parse(p.stdout || "{}").streams || [];
};
const loud = (file) => {
  const p = spawnSync("ffmpeg", ["-hide_banner", "-nostats", "-i", file, "-map", "0:a", "-af", "ebur128=peak=true", "-f", "null", "-"], { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  const t = p.stderr || "";
  const summary = t.slice(t.lastIndexOf("Summary:"));
  const num = (re) => { const m = summary.match(re); return m ? Number(m[1]) : null; };
  return { lufs: num(/I:\s+(-?[\d.]+) LUFS/), lra: num(/LRA:\s+(-?[\d.]+) LU/), truePeak: num(/Peak:\s+(-?[\d.]+) dBFS/) };
};
for (const [name, frames] of [["final.mp4", TOTAL_FRAMES], ["social-1x1.mp4", null]]) {
  const file = join(ROOT, name);
  if (!existsSync(file)) continue;
  const streams = probe(file);
  const v = streams.find((s) => s.codec_type === "video");
  const a = streams.find((s) => s.codec_type === "audio");
  const l = a ? loud(file) : {};
  report.output[name] = {
    video: v, audio: a, loudness: l,
    checks: {
      fps60: v?.avg_frame_rate === "60/1",
      frames: frames === null ? "n/a" : Number(v?.nb_frames) === frames,
      avSync: a && v ? Math.abs(Number(a.duration) - Number(v.duration)) < 0.05 : false,
      loudness: l.lufs !== null && Math.abs(l.lufs + 14) <= 1,
      noClipping: l.truePeak !== null && l.truePeak <= -1.0,
    },
  };
}
mkdirSync(join(ROOT, "out"), { recursive: true });
writeFileSync(join(ROOT, "out", "qa.json"), JSON.stringify(report, null, 2));

const bad = report.pacing.filter((p) => !p.ok);
console.log(`pacing: ${report.pacing.length - bad.length}/${report.pacing.length} scenes leave enough reading time` + (bad.length ? " -- short: " + bad.map((b) => `${b.id} (${b.visible}s < ${b.need}s)`).join(", ") : ""));
console.log(`copy: ${report.copy.length} strings, ${report.copyIssues.length} issues` + (report.copyIssues.length ? "\n  " + report.copyIssues.join("\n  ") : ""));
console.log("links: " + report.links.map((l) => `${l.url} ${l.status}`).join(" | "));
for (const [n, o] of Object.entries(report.output)) console.log(`${n}: ` + JSON.stringify(o.checks) + " " + JSON.stringify(o.loudness));
console.log(`film: ${(TOTAL_FRAMES / FPS).toFixed(2)} s, social: ${SOCIAL_SCENES.length} scenes`);
