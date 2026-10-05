// The app's own design language (ui_qt/theme.py DARK, ui_qt/palettes.py
// Sapphire night, ui_qt/cinema.py), defined once for every scene.
import { Easing } from "remotion";

export const C = {
  base: "#0d1734",          // Sapphire night field
  ink: "#101c3e",
  text: "#eaf2ff",
  muted: "#a9b4c9",
  faint: "#7b86a0",
  brand: "#38bdf8",         // sky: identity, eyebrows
  brandHi: "#7dd3fc",
  indigo: "#818cf8",
  cyan: "#0ea5e9",
  ember: "#ff6a13",         // action
  emberTop: "#ff8b47",
  emberText: "#170b04",
  success: "#34d399",
  glass: "rgba(255,255,255,0.055)",
  glassHi: "rgba(255,255,255,0.09)",
  rim: "rgba(255,255,255,0.12)",
  rimHi: "rgba(255,255,255,0.22)",
  shade: "rgba(2,6,20,0.55)",
};

export const FONT = {
  serif: "'Instrument Serif', Georgia, serif",
  ui: "'Inter', 'Segoe UI', sans-serif",
};

// Ease-in-out and springs only: no linear motion anywhere.
export const EASE = Easing.bezier(0.65, 0, 0.35, 1);
export const EASE_OUT = Easing.bezier(0.16, 1, 0.3, 1);
export const EASE_IN = Easing.bezier(0.7, 0, 0.84, 0);

export const SPRING = { damping: 200, stiffness: 120, mass: 1 } as const;   // calm, no overshoot
export const SPRING_POP = { damping: 14, stiffness: 160, mass: 0.7 } as const; // a small, friendly bounce

// Layout: 1920x1080 with safe margins.
export const W = 1920;
export const H = 1080;
export const MARGIN_X = 112;
export const STAGE = { x: 336, y: 214, w: 1248, h: 702 };   // where the app window sits in feature scenes
export const CAPTURE = { w: 2560, h: 1440 };               // every capture's pixel size
export const MIN_T = 12;                                   // frames: the shortest transition
