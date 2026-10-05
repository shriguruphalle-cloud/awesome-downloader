// The app's backdrop (ui_qt/cinema.py, Sapphire night): a navy field lit
// from above in sky and indigo, a faint 68px grid strongest at the top,
// drifting very slowly so the frame never sits dead still.
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { C } from "../tokens";

type Glow = { rgb: string; a: number; cx: number; cy: number; rx: number; ry: number };

export const SAPPHIRE: { base: string; glows: Glow[] } = {
  base: C.base,
  glows: [
    { rgb: "56,189,248", a: 0.24, cx: 0.5, cy: -0.08, rx: 0.95, ry: 0.66 },
    { rgb: "129,140,248", a: 0.21, cx: 0.9, cy: 0.22, rx: 0.72, ry: 0.58 },
    { rgb: "14,165,233", a: 0.16, cx: 0.04, cy: 0.74, rx: 0.82, ry: 0.62 },
    { rgb: "5,10,26", a: 0.5, cx: 0.5, cy: 1.2, rx: 1.2, ry: 0.6 },
  ],
};

export const Backdrop: React.FC<{ base?: string; glows?: Glow[]; grid?: number; drift?: boolean }> = ({
  base = SAPPHIRE.base,
  glows = SAPPHIRE.glows,
  grid = 0.068,
  drift = true,
}) => {
  const f = useCurrentFrame();
  const t = drift ? f / 60 : 0;
  const layers = glows
    .map((g, i) => {
      const dx = Math.sin(t * 0.11 + i * 1.7) * 0.025;
      const dy = Math.cos(t * 0.09 + i * 2.3) * 0.02;
      return `radial-gradient(${g.rx * 100}% ${g.ry * 100}% at ${(g.cx + dx) * 100}% ${(g.cy + dy) * 100}%, rgba(${g.rgb},${g.a}) 0%, rgba(${g.rgb},${g.a * 0.45}) 38%, rgba(${g.rgb},0) 70%)`;
    })
    .join(",");
  const line = `rgba(255,255,255,${grid})`;
  return (
    <AbsoluteFill style={{ background: base }}>
      <AbsoluteFill style={{ background: layers }} />
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(${line} 1px, transparent 1px), linear-gradient(90deg, ${line} 1px, transparent 1px)`,
          backgroundSize: "68px 68px",
          backgroundPosition: `${-((t * 3) % 68)}px ${-((t * 2) % 68)}px`,
          maskImage: "linear-gradient(to bottom, rgba(0,0,0,0.95), rgba(0,0,0,0.25) 70%, rgba(0,0,0,0.12))",
          WebkitMaskImage: "linear-gradient(to bottom, rgba(0,0,0,0.95), rgba(0,0,0,0.25) 70%, rgba(0,0,0,0.12))",
        }}
      />
      <AbsoluteFill
        style={{ background: "radial-gradient(120% 90% at 50% 45%, rgba(0,0,0,0) 55%, rgba(2,5,16,0.55) 100%)" }}
      />
    </AbsoluteFill>
  );
};
