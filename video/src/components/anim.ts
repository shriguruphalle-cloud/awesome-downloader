// Small animation helpers shared by every scene. All motion eases.
import { interpolate, spring } from "remotion";
import { EASE, EASE_OUT, SPRING, SPRING_POP } from "../tokens";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** 0 -> 1 over `len` frames from `start`, eased in and out. */
export const ramp = (f: number, start: number, len = 18, ease = EASE) =>
  interpolate(f, [start, start + len], [0, 1], { ...clamp, easing: ease });

/** 1 while inside [start, end], easing in and out at both ends. */
export const window01 = (f: number, start: number, end: number, len = 16) =>
  Math.min(ramp(f, start, len, EASE_OUT), 1 - ramp(f, end - len, len, EASE));

export const rise = (f: number, fps: number, start: number, pop = false) =>
  spring({ frame: f - start, fps, config: pop ? SPRING_POP : SPRING });

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export const sec = (s: number, fps = 60) => Math.round(s * fps);
