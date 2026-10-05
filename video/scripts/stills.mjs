// QA stills: renders each scene at its start (+0.6 s), middle and end
// (-0.6 s) into out/stills/, plus a contact sheet per scene, so every frame
// that matters can be checked for overflow, clipping, overlaps and typos.
//
//   node scripts/stills.mjs                 every scene
//   node scripts/stills.mjs videoFetch tour  just these
//   node scripts/stills.mjs --at=hook:3.5    one moment (scene:seconds)
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FPS, SCENES, sceneFrames, sceneStart } from "../src/config.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "..", "out", "stills");
mkdirSync(OUT, { recursive: true });

const args = process.argv.slice(2);
const only = args.filter((a) => !a.startsWith("--"));
const at = args.filter((a) => a.startsWith("--at=")).map((a) => a.slice(5).split(":"));

const serveUrl = await bundle({ entryPoint: join(HERE, "..", "src", "index.ts") });
const composition = await selectComposition({ serveUrl, id: "Showcase" });

const jobs = [];
if (at.length) {
  for (const [id, s] of at) jobs.push({ name: `${id}-${s}s`, frame: sceneStart(id) + Math.round(Number(s) * FPS) });
} else {
  for (const s of SCENES) {
    if (only.length && !only.includes(s.id)) continue;
    const start = sceneStart(s.id);
    const len = sceneFrames(s);
    jobs.push({ name: `${s.id}-1-start`, frame: start + Math.round(0.6 * FPS) });
    jobs.push({ name: `${s.id}-2-mid`, frame: start + Math.round(len / 2) });
    jobs.push({ name: `${s.id}-3-end`, frame: start + len - Math.round(0.6 * FPS) });
  }
}
for (const j of jobs) {
  await renderStill({ composition, serveUrl, frame: j.frame, output: join(OUT, j.name + ".png"), imageFormat: "png", chromiumOptions: { gl: "angle" } });
  console.log(`${j.name}  (frame ${j.frame})`);
}
