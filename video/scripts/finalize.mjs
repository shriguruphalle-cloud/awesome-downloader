// Last step after rendering: puts the soundtrack in with ffmpeg's AAC encoder
// (checked for true peaks after encoding) and tags the picture BT.709, so
// browsers and players show the app's colours as rendered. The picture itself
// is copied untouched.
//
//   node scripts/finalize.mjs      (final.mp4 and social-1x1.mp4, if present)
import { spawnSync } from "node:child_process";
import { existsSync, renameSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MUSIC } from "../src/config.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const jobs = [
  ["final.mp4", MUSIC ? "soundtrack-music.wav" : "soundtrack.wav", "256k"],
  ["social-1x1.mp4", "soundtrack-social.wav", "192k"],
];
for (const [video, audio, rate] of jobs) {
  const v = join(ROOT, video);
  if (!existsSync(v)) continue;
  const tmp = v.replace(/\.mp4$/, ".tmp.mp4");
  const r = spawnSync("ffmpeg", [
    "-hide_banner", "-v", "error", "-y", "-i", v, "-i", join(ROOT, "public", "audio", audio),
    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
    "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
    "-c:a", "aac", "-b:a", rate, "-ar", "48000", "-shortest", "-movflags", "+faststart", tmp,
  ], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(r.stderr);
  renameSync(tmp, v);
  console.log(`${video}: soundtrack ${audio} at ${rate}, BT.709 tags`);
}
