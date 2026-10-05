// Where each widget is in each capture (written by capture/capture.py).
import META from "../public/captures/meta.json";
import type { Rect } from "./components/Stage";

type Entry = { w: number; h: number; rects: Record<string, number[]>; at?: number[]; chip?: number[] };
const M = META as unknown as Record<string, Entry>;

export const rect = (capture: string, key: string): Rect => {
  const r = M[capture]?.rects?.[key];
  if (!r) throw new Error(`no rect ${key} in ${capture}`);
  return r as Rect;
};

export const at = (capture: string, key: "at" | "chip" = "at"): Rect => {
  const r = M[capture]?.[key];
  if (!r) throw new Error(`no ${key} for ${capture}`);
  return r as Rect;
};

export const size = (capture: string) => ({ w: M[capture].w, h: M[capture].h });

export const centre = (r: Rect, dx = 0.5, dy = 0.5): [number, number] => [r[0] + r[2] * dx, r[1] + r[3] * dy];

/** A rect grown on every side. */
export const grow = (r: Rect, by: number): Rect => [r[0] - by, r[1] - by, r[2] + 2 * by, r[3] + 2 * by];

/** The union of rects. */
export const union = (...rs: Rect[]): Rect => {
  const x = Math.min(...rs.map((r) => r[0]));
  const y = Math.min(...rs.map((r) => r[1]));
  const r2 = Math.max(...rs.map((r) => r[0] + r[2]));
  const b = Math.max(...rs.map((r) => r[1] + r[3]));
  return [x, y, r2 - x, b - y];
};
