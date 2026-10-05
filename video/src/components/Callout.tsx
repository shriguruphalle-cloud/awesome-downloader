// A highlight ring on a real widget with a label pill beside it. Lives in a
// Stage's capture pixels; the label is counter-scaled so it reads the same
// size whatever the camera's zoom.
import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT } from "../tokens";
import { rise, window01 } from "./anim";
import { Rect, useStageScale } from "./Stage";

export const Callout: React.FC<{
  rect: Rect;
  label?: string;
  at: number;
  until: number;
  side?: "top" | "bottom" | "left" | "right";
  pad?: number;
  radius?: number;
  color?: string;
}> = ({ rect, label, at, until, side = "bottom", pad = 10, radius = 18, color = C.brand }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = useStageScale();
  const on = window01(f, at, until, 14);
  if (on <= 0) return null;
  const pop = rise(f, fps, at, true);
  const k = 1 / scale; // screen px -> capture px
  const [x, y, w, h] = rect;
  const p = pad * k * 0.6 + 6;
  const pulse = 0.5 + 0.5 * Math.sin((f - at) / 9);
  const labelSize = 30 * k;
  const gap = 22 * k;
  const pill: React.CSSProperties = {
    position: "absolute",
    whiteSpace: "nowrap",
    fontFamily: FONT.ui,
    fontWeight: 600,
    fontSize: labelSize,
    color: C.text,
    padding: `${12 * k}px ${22 * k}px`,
    borderRadius: 999,
    background: "rgba(10,20,48,0.86)",
    border: `${1.5 * k}px solid ${color}`,
    boxShadow: `0 ${12 * k}px ${36 * k}px rgba(0,0,0,0.45), 0 0 ${24 * k}px rgba(56,189,248,0.25)`,
    opacity: on,
  };
  const cx = x + w / 2;
  const cy = y + h / 2;
  let pos: React.CSSProperties = {};
  if (side === "bottom") pos = { left: cx, top: y + h + p + gap, transform: `translateX(-50%) translateY(${(1 - pop) * 14 * k}px)` };
  if (side === "top") pos = { left: cx, top: y - p - gap, transform: `translate(-50%, -100%) translateY(${(1 - pop) * -14 * k}px)` };
  if (side === "right") pos = { left: x + w + p + gap, top: cy, transform: `translateY(-50%) translateX(${(1 - pop) * 14 * k}px)` };
  if (side === "left") pos = { left: x - p - gap, top: cy, transform: `translate(-100%, -50%) translateX(${(1 - pop) * -14 * k}px)` };
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: x - p,
          top: y - p,
          width: w + 2 * p,
          height: h + 2 * p,
          borderRadius: radius * k * 0.6 + 10,
          border: `${3 * k}px solid ${color}`,
          boxShadow: `0 0 ${(16 + 14 * pulse) * k}px ${color}, inset 0 0 ${18 * k}px rgba(56,189,248,0.18)`,
          opacity: on,
          transform: `scale(${0.92 + 0.08 * pop})`,
        }}
      />
      {label ? <div style={{ ...pill, ...pos }}>{label}</div> : null}
    </>
  );
};
