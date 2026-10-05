// Generates the soundtrack from scratch -- no samples, nothing copyrighted:
// a calm ambient bed (slow chords, a soft pulse) and UI sounds (clicks, key
// taps, pops, whooshes, a chime), placed by the same timings the video uses
// (src/config.ts), then loudness-normalised with ffmpeg to -14 LUFS with
// true peaks at or under -2 dBTP (AAC encoding adds some back).
//
//   node scripts/soundtrack.mjs        -> public/audio/soundtrack.wav, soundtrack-social.wav
// With MUSIC set in config.ts, a version with your track under the effects
// is written too (public/audio/soundtrack-music.wav).
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FPS, MUSIC, SCENES } from "../src/config.ts";
import { SOCIAL_SCENES } from "../src/socialConfig.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "..", "public", "audio");
mkdirSync(OUT, { recursive: true });
const SR = 48000;

// ---- a tiny synth -----------------------------------------------------------------------------
let seed = 12345;
const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296) * 2 - 1;
const noteHz = (n) => 440 * Math.pow(2, (n - 69) / 12);

class Buf {
  constructor(seconds) {
    this.n = Math.ceil(seconds * SR);
    this.L = new Float32Array(this.n);
    this.R = new Float32Array(this.n);
  }
  add(i, l, r = l) {
    if (i >= 0 && i < this.n) {
      this.L[i] += l;
      this.R[i] += r;
    }
  }
}

/** Sustained pad voice: a few detuned partials with a slow attack/release. */
function pad(buf, t0, dur, notes, gain) {
  const a = 2.6, rl = 3.2;
  const i0 = Math.floor(t0 * SR), i1 = Math.min(buf.n, Math.floor((t0 + dur + rl) * SR));
  for (const [k, n] of notes.entries()) {
    const f = noteHz(n);
    const pan = (k / Math.max(1, notes.length - 1)) * 1.2 - 0.6;
    const voices = [-0.004, 0.0, 0.0037];
    const ph = voices.map(() => Math.random() * 6.28);
    for (let i = i0; i < i1; i++) {
      const t = (i - i0) / SR;
      const env = Math.min(1, t / a) * (t > dur ? Math.max(0, 1 - (t - dur) / rl) : 1);
      const trem = 0.85 + 0.15 * Math.sin(6.28 * 0.11 * (i / SR) + k);
      let s = 0;
      for (let v = 0; v < voices.length; v++) {
        const w = 6.283185 * f * (1 + voices[v]) * t + ph[v];
        s += Math.sin(w) * 0.7 + Math.sin(2 * w) * 0.12 + Math.sin(3 * w) * 0.04;
      }
      s *= (gain * env * trem) / voices.length;
      buf.add(i, s * (1 - pan) * 0.7, s * (1 + pan) * 0.7);
    }
  }
}

/** A soft bell pluck. */
function pluck(buf, t0, n, gain, pan = 0) {
  const f = noteHz(n);
  const i0 = Math.floor(t0 * SR), len = Math.floor(1.6 * SR);
  for (let j = 0; j < len; j++) {
    const t = j / SR;
    const env = Math.min(1, t / 0.006) * Math.exp(-t * 3.2);
    const s = (Math.sin(6.283 * f * t) + 0.3 * Math.sin(6.283 * f * 2.01 * t) * Math.exp(-t * 6)) * env * gain;
    buf.add(i0 + j, s * (1 - pan), s * (1 + pan));
  }
}

function noiseBurst(buf, t0, dur, gain, lowHz, highHz, shape = (x) => Math.sin(Math.PI * x)) {
  // Band-limited noise with a moving band (a state-variable filter swept from low to high).
  const i0 = Math.floor(t0 * SR), len = Math.floor(dur * SR);
  let lp = 0, bp = 0;
  for (let j = 0; j < len; j++) {
    const x = j / len;
    const fc = lowHz * Math.pow(highHz / lowHz, x);
    const fq = 2 * Math.sin((Math.PI * fc) / SR);
    const inp = rand();
    lp += fq * bp;
    const hp = inp - lp - 0.7 * bp;
    bp += fq * hp;
    const s = bp * shape(x) * gain;
    const pan = Math.sin(x * Math.PI - Math.PI / 2) * 0.5;
    buf.add(i0 + j, s * (1 - pan), s * (1 + pan));
  }
}

const SFX = {
  click(buf, t) {
    noiseBurst(buf, t, 0.012, 0.35, 2500, 6000, (x) => 1 - x);
    const i0 = Math.floor(t * SR);
    for (let j = 0; j < 0.05 * SR; j++) {
      const tt = j / SR;
      buf.add(i0 + j, Math.sin(6.283 * 1650 * tt) * Math.exp(-tt * 90) * 0.16);
    }
  },
  key(buf, t) {
    noiseBurst(buf, t, 0.02, 0.32, 900, 3200, (x) => Math.exp(-x * 5));
    const i0 = Math.floor(t * SR);
    for (let j = 0; j < 0.07 * SR; j++) {
      const tt = j / SR;
      buf.add(i0 + j, Math.sin(6.283 * 170 * tt) * Math.exp(-tt * 55) * 0.22);
    }
  },
  pop(buf, t) {
    const i0 = Math.floor(t * SR);
    let ph = 0;
    for (let j = 0; j < 0.16 * SR; j++) {
      const tt = j / SR;
      const f = 520 + 520 * Math.min(1, tt / 0.05);
      ph += (6.283 * f) / SR;
      const env = Math.min(1, tt / 0.004) * Math.exp(-tt * 24);
      buf.add(i0 + j, Math.sin(ph) * env * 0.16);
    }
  },
  whoosh(buf, t) {
    noiseBurst(buf, t - 0.25, 0.75, 0.42, 250, 3800);
  },
  swish(buf, t) {
    noiseBurst(buf, t - 0.12, 0.38, 0.22, 600, 5200);
  },
  chime(buf, t) {
    for (const [mult, g, d] of [[1, 0.16, 2.2], [2.76, 0.07, 3.5], [5.4, 0.035, 5]]) {
      const i0 = Math.floor(t * SR);
      for (let j = 0; j < 2.4 * SR; j++) {
        const tt = j / SR;
        const env = Math.min(1, tt / 0.004) * Math.exp(-tt * d);
        const s = Math.sin(6.283 * 1046.5 * mult * tt) * env * g;
        buf.add(i0 + j, s * 0.9, s);
      }
    }
  },
};

/** A small stereo reverb (four combs + two all-passes per side), mixed in. */
function reverb(buf, wet) {
  const out = (src, delays) => {
    const y = new Float32Array(src.length);
    for (const d of delays) {
      const line = new Float32Array(d);
      let p = 0, lpf = 0;
      for (let i = 0; i < src.length; i++) {
        const o = line[p];
        lpf = o * 0.6 + lpf * 0.4;
        line[p] = src[i] + lpf * 0.78;
        y[i] += o * 0.25;
        p = (p + 1) % d;
      }
    }
    for (const d of [556, 441]) {
      const line = new Float32Array(d);
      let p = 0;
      for (let i = 0; i < y.length; i++) {
        const o = line[p];
        const v = y[i] + o * 0.5;
        line[p] = v;
        y[i] = o - v * 0.5;
        p = (p + 1) % d;
      }
    }
    return y;
  };
  const l = out(buf.L, [1557, 1617, 1491, 1422].map((d) => Math.round(d * 1.6)));
  const r = out(buf.R, [1277, 1356, 1188, 1116].map((d) => Math.round(d * 1.6)));
  for (let i = 0; i < buf.n; i++) {
    buf.L[i] = buf.L[i] * (1 - wet * 0.3) + l[i] * wet;
    buf.R[i] = buf.R[i] * (1 - wet * 0.3) + r[i] * wet;
  }
}

// ---- the arrangement ----------------------------------------------------------------------------------
// D major, slow: Dmaj9 - Bm9 - Gmaj9 - A6sus (MIDI notes), eight seconds a chord.
const CHORDS = [
  [50, 57, 61, 64, 66, 69], // D  A  C# E  F# A
  [47, 54, 57, 61, 62, 66], // B  F# A  C# D  F#
  [43, 50, 54, 57, 59, 62], // G  D  F# A  B  D
  [45, 52, 57, 59, 62, 66], // A  E  A  B  D  F#
];
const BAR = 8;
const ARP = [0, 2, 4, 5, 3, 4, 2, 1];

function bed(seconds, swellAt) {
  const buf = new Buf(seconds + 4);
  for (let c = 0, t = 0; t < seconds; c++, t += BAR) {
    const chord = CHORDS[c % CHORDS.length];
    pad(buf, t, BAR, chord.slice(1).map((n) => n + 12), 0.05);
    pad(buf, t, BAR, [chord[0] - 12], 0.075);
    // a soft pluck pattern, quarter notes at 60 bpm, two octaves up
    for (let q = 0; q < BAR; q++) {
      const tt = t + q;
      if (tt < 3 || tt > seconds - 5) continue;
      const n = chord[1 + ARP[(c * BAR + q) % ARP.length] % (chord.length - 1)] + 24;
      pluck(buf, tt, n, 0.035, (q % 2 ? 0.35 : -0.35));
    }
  }
  // fade in, a lift at the logo, fade out
  for (let i = 0; i < buf.n; i++) {
    const t = i / SR;
    let g = Math.min(1, t / 2.5) * (t > seconds - 4 ? Math.max(0, (seconds - t) / 4) : 1);
    if (swellAt !== null) g *= 1 + 0.35 * Math.exp(-Math.pow((t - swellAt - 1.2) / 1.6, 2));
    buf.L[i] *= g;
    buf.R[i] *= g;
  }
  return buf;
}

function cues(sceneIds) {
  const list = [];
  let t = 0;
  for (const id of sceneIds) {
    const s = SCENES.find((x) => x.id === id);
    if (t > 0) list.push({ t: t + 0.02, k: "swish" });
    for (const e of s.sfx || []) list.push({ t: t + e.t, k: e.k });
    t += Math.round(s.seconds * FPS) / FPS;
  }
  return { list, seconds: t };
}

function render(sceneIds, name, withBed = true) {
  const { list, seconds } = cues(sceneIds);
  const logoAt = (() => {
    let t = 0;
    for (const id of sceneIds) {
      if (id === "logo") return t;
      t += Math.round(SCENES.find((x) => x.id === id).seconds * FPS) / FPS;
    }
    return null;
  })();
  const mix = withBed ? bed(seconds, logoAt) : new Buf(seconds + 4);
  const fx = new Buf(seconds + 4);
  for (const c of list) SFX[c.k](fx, c.t);
  reverb(fx, 0.22);
  if (withBed) reverb(mix, 0.12);
  for (let i = 0; i < mix.n; i++) {
    mix.L[i] += fx.L[i];
    mix.R[i] += fx.R[i];
  }
  // write 32-bit float WAV, trimmed to the film's length
  const n = Math.round(seconds * SR);
  const data = Buffer.alloc(n * 8);
  for (let i = 0; i < n; i++) {
    data.writeFloatLE(mix.L[i], i * 8);
    data.writeFloatLE(mix.R[i], i * 8 + 4);
  }
  const head = Buffer.alloc(44);
  head.write("RIFF", 0); head.writeUInt32LE(36 + data.length, 4); head.write("WAVE", 8);
  head.write("fmt ", 12); head.writeUInt32LE(16, 16); head.writeUInt16LE(3, 20); head.writeUInt16LE(2, 22);
  head.writeUInt32LE(SR, 24); head.writeUInt32LE(SR * 8, 28); head.writeUInt16LE(8, 32); head.writeUInt16LE(32, 34);
  head.write("data", 36); head.writeUInt32LE(data.length, 40);
  const raw = join(OUT, name + ".raw.wav");
  writeFileSync(raw, Buffer.concat([head, data]));
  return { raw, seconds, cueCount: list.length };
}

function loudnorm(raw, out, music) {
  const ff = "ffmpeg";
  const inputs = music ? ["-i", raw, "-stream_loop", "-1", "-i", music] : ["-i", raw];
  const pre = music
    ? "[1:a]aresample=48000,volume=0.8,afade=t=in:d=2[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a];[a]"
    : "[0:a]";
  const run = spawnSync(ff, ["-hide_banner", ...inputs, "-filter_complex", pre + "loudnorm=I=-14:TP=-2:LRA=11:print_format=json", "-f", "null", "-"], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const text = run.stderr || "";
  const stats = JSON.parse(text.slice(text.lastIndexOf("{"), text.lastIndexOf("}") + 1));
  const second = `${pre}loudnorm=I=-14:TP=-2:LRA=11:measured_I=${stats.input_i}:measured_TP=${stats.input_tp}:measured_LRA=${stats.input_lra}:measured_thresh=${stats.input_thresh}:offset=${stats.target_offset}:linear=true,aresample=48000`;
  execFileSync(ff, ["-hide_banner", "-y", "-v", "error", ...inputs, "-filter_complex", second, "-c:a", "pcm_s24le", "-ar", "48000", out]);
  return stats;
}

const wide = SCENES.map((s) => s.id);
for (const [ids, name] of [[wide, "soundtrack"], [SOCIAL_SCENES, "soundtrack-social"]]) {
  const r = render(ids, name);
  const st = loudnorm(r.raw, join(OUT, name + ".wav"), null);
  unlinkSync(r.raw);
  console.log(`${name}.wav  ${r.seconds.toFixed(2)} s, ${r.cueCount} cues, measured ${st.input_i} LUFS -> -14`);
}
if (MUSIC) {
  const music = join(HERE, "..", "public", MUSIC);
  if (!existsSync(music)) throw new Error("MUSIC is set but public/" + MUSIC + " is missing");
  const r = render(wide, "soundtrack-music", false);
  loudnorm(r.raw, join(OUT, "soundtrack-music.wav"), music);
  unlinkSync(r.raw);
  console.log("soundtrack-music.wav with " + MUSIC);
}
