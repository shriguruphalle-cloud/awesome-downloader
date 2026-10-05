// DeviceFrame + camera. The app window (a 2560x1440 capture) sits in a
// rounded glass frame; the camera eases between focus rects given in the
// capture's own pixels, and everything passed as children (callouts, the
// cursor) is laid out in those same pixels, so it stays glued to the UI
// while the camera moves.
import React, { createContext, useContext } from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame } from "remotion";
import { C, CAPTURE, EASE, STAGE } from "../tokens";

export type Rect = [number, number, number, number];
export const FULL: Rect = [0, 0, CAPTURE.w, CAPTURE.h];

export type Layer = { src: string; from?: number; fade?: number; video?: boolean; startFrom?: number; clip?: Rect; until?: number };
export type CamKey = { at: number; rect: Rect; len?: number };

type StageCtx = { scale: number };
const Ctx = createContext<StageCtx>({ scale: STAGE.w / CAPTURE.w });
/** Screen pixels per capture pixel right now (for counter-scaling text). */
export const useStageScale = () => useContext(Ctx).scale;

const camAt = (keys: CamKey[], f: number, full: Rect): Rect => {
  let cur: Rect = keys.length ? keys[0].rect : full;
  for (let i = 1; i < keys.length; i++) {
    const k = keys[i];
    const len = k.len ?? 54;
    if (f <= k.at) break;
    const t = interpolate(f, [k.at, k.at + len], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: EASE,
    });
    cur = cur.map((v, j) => v + (k.rect[j] - v) * t) as Rect;
  }
  return cur;
};

/** A focus rect grown to the stage's aspect and kept inside the capture. */
export const fit = (r: Rect, pad = 1.18): Rect => {
  const aspect = CAPTURE.w / CAPTURE.h;
  let w = r[2] * pad;
  let h = r[3] * pad;
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;
  w = Math.min(w, CAPTURE.w);
  h = Math.min(h, CAPTURE.h);
  const cx = r[0] + r[2] / 2;
  const cy = r[1] + r[3] / 2;
  const x = Math.max(0, Math.min(CAPTURE.w - w, cx - w / 2));
  const y = Math.max(0, Math.min(CAPTURE.h - h, cy - h / 2));
  return [x, y, w, h];
};

export const Stage: React.FC<{
  layers: Layer[];
  cam?: CamKey[];
  box?: { x: number; y: number; w: number; h: number };
  size?: { w: number; h: number };
  enter?: number;
  radius?: number;
  children?: React.ReactNode;
}> = ({ layers, cam, box = STAGE, size = CAPTURE, enter = 1, radius = 22, children }) => {
  const f = useCurrentFrame();
  const full: Rect = [0, 0, size.w, size.h];
  const r = camAt(cam ?? [{ at: 0, rect: full }], f, full);
  const zoom = size.w / r[2];
  const scale = (box.w / size.w) * zoom;
  const tx = -r[0] * scale;
  const ty = -r[1] * scale;
  return (
    <div
      style={{
        position: "absolute",
        left: box.x,
        top: box.y,
        width: box.w,
        height: box.h,
        borderRadius: radius,
        transform: `translateY(${(1 - enter) * 40}px) scale(${0.96 + 0.04 * enter})`,
        opacity: enter,
        filter: `blur(${(1 - enter) * 8}px)`,
        boxShadow: `0 40px 120px -30px ${C.shade}, 0 0 0 1px ${C.rim}, 0 0 80px -20px rgba(56,189,248,0.25)`,
      }}
    >
      <div style={{ position: "absolute", inset: 0, borderRadius: radius, overflow: "hidden", background: C.ink }}>
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            width: size.w,
            height: size.h,
            transformOrigin: "0 0",
            transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          }}
        >
          {layers.map((l, i) => {
            const from = l.from ?? 0;
            const fade = l.fade ?? 20;
            const inOp = i === 0 ? 1 : interpolate(f, [from, from + fade], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: EASE,
            });
            const outOp = l.until === undefined ? 1 : interpolate(f, [l.until, l.until + fade], [1, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: EASE,
            });
            const op = Math.min(inOp, outOp);
            if (op <= 0) return null;
            const clip = l.clip
              ? `inset(${l.clip[1]}px ${size.w - l.clip[0] - l.clip[2]}px ${size.h - l.clip[1] - l.clip[3]}px ${l.clip[0]}px round 18px)`
              : undefined;
            const style: React.CSSProperties = { position: "absolute", inset: 0, width: size.w, height: size.h, opacity: op, clipPath: clip };
            return l.video ? (
              <OffthreadVideo key={i} src={staticFile(l.src)} style={style} muted startFrom={l.startFrom ?? 0} />
            ) : (
              <Img key={i} src={staticFile(l.src)} style={style} />
            );
          })}
          <Ctx.Provider value={{ scale }}>
            <AbsoluteFill>{children}</AbsoluteFill>
          </Ctx.Provider>
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: radius,
          pointerEvents: "none",
          boxShadow: `inset 0 1px 0 ${C.rimHi}, inset 0 0 0 1px rgba(255,255,255,0.06)`,
        }}
      />
    </div>
  );
};

/** A loose capture (popup, dialog) placed in capture pixels inside a Stage. */
export const Overlay: React.FC<{ src: string; at: Rect; show: number; shadow?: boolean }> = ({ src, at, show, shadow = true }) => {
  if (show <= 0) return null;
  return (
    <Img
      src={staticFile(src)}
      style={{
        position: "absolute",
        left: at[0],
        top: at[1],
        width: at[2],
        height: at[3],
        opacity: show,
        transform: `translateY(${(1 - show) * 24}px) scale(${0.97 + 0.03 * show})`,
        transformOrigin: "50% 0%",
        filter: shadow ? "drop-shadow(0 30px 60px rgba(0,0,0,0.5))" : undefined,
      }}
    />
  );
};
