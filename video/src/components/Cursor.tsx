// A pointer that glides between points (capture pixels) and ripples when it
// clicks. Counter-scaled, so it stays pointer-sized at any zoom.
import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { EASE } from "../tokens";
import { useStageScale } from "./Stage";

export type CursorKey = { at: number; x: number; y: number; len?: number };

export const Cursor: React.FC<{ path: CursorKey[]; clicks?: number[]; show?: [number, number] }> = ({
  path,
  clicks = [],
  show = [0, 1e9],
}) => {
  const f = useCurrentFrame();
  const scale = useStageScale();
  const k = 1 / scale;
  if (f < show[0] - 12 || f > show[1] + 12 || !path.length) return null;
  const vis = Math.min(
    interpolate(f, [show[0] - 12, show[0]], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
    interpolate(f, [show[1], show[1] + 12], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
  );
  let x = path[0].x;
  let y = path[0].y;
  for (let i = 1; i < path.length; i++) {
    const p = path[i];
    const len = p.len ?? 40;
    const t = interpolate(f, [p.at - len, p.at], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
    x += (p.x - x) * t;
    y += (p.y - y) * t;
  }
  const pressing = clicks.some((c) => f >= c && f < c + 8);
  const size = 46 * k;
  return (
    <>
      {clicks.map((c) => {
        const t = interpolate(f, [c, c + 28], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
        if (f < c || t >= 1) return null;
        return (
          <div
            key={c}
            style={{
              position: "absolute",
              left: x - (20 + 70 * t) * k,
              top: y - (20 + 70 * t) * k,
              width: (40 + 140 * t) * k,
              height: (40 + 140 * t) * k,
              borderRadius: "50%",
              border: `${3 * k}px solid rgba(125,211,252,${0.85 * (1 - t)})`,
              background: `rgba(56,189,248,${0.18 * (1 - t)})`,
            }}
          />
        );
      })}
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        style={{
          position: "absolute",
          left: x - 3 * k,
          top: y - 2 * k,
          opacity: vis,
          transform: `scale(${pressing ? 0.86 : 1})`,
          transformOrigin: "10% 10%",
          filter: `drop-shadow(0 ${4 * k}px ${8 * k}px rgba(0,0,0,0.55))`,
          overflow: "visible",
        }}
      >
        <path d="M3 2 L3 19.5 L7.6 15.4 L10.6 22 L13.6 20.7 L10.7 14.2 L17 14.2 Z" fill="#ffffff" stroke="#0b1530" strokeWidth={1.4} strokeLinejoin="round" />
      </svg>
    </>
  );
};
