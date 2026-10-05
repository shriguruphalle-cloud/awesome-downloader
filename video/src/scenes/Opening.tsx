// Hook, logo reveal, why it exists, and the six-tab tour.
import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { COPY } from "../config";
import { C, EASE, EASE_OUT, FONT } from "../tokens";
import { ramp, rise, window01 } from "../components/anim";
import { Icon } from "../components/Icon";
import { Badge, Glass } from "../components/Text";
import { Wordmark } from "../components/Wordmark";
import { Callout } from "../components/Callout";
import { Stage } from "../components/Stage";
import { rect } from "../meta";
import { Frame, useScene } from "./common";

// ---- 1. Hook: the problem ----------------------------------------------------------------------
const Clutter: React.FC<{ icon: string; x: number; y: number; at: number; tilt: number; collapse: number }> = ({ icon, x, y, at, tilt, collapse }) => {
  const f = useCurrentFrame();
  const { fps } = useScene("hook");
  const s = rise(f, fps, at, true);
  const ad = rise(f, fps, at + 14, true);
  const shake = Math.sin((f - at) / 2.2) * 2.5 * window01(f, at + 14, at + 60, 10);
  const cx = 960 + (x - 960) * (1 - collapse);
  const cy = 540 + (y - 540) * (1 - collapse);
  return (
    <div style={{ position: "absolute", left: cx, top: cy, transform: `translate(-50%,-50%) rotate(${tilt + shake}deg) scale(${(0.6 + 0.4 * s) * (1 - 0.7 * collapse)})`, opacity: s * (1 - collapse) }}>
      <Glass style={{ width: 230, height: 150, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 22 }}>
        <Icon name={icon} size={64} color={C.muted} />
      </Glass>
      <div style={{ position: "absolute", right: -18, top: -16, padding: "6px 12px", borderRadius: 10, background: "#ff5a5f", color: "#fff", fontFamily: FONT.ui, fontWeight: 800, fontSize: 20, letterSpacing: "0.06em", transform: `scale(${ad})`, boxShadow: "0 8px 20px rgba(255,90,95,0.45)" }}>
        AD
      </div>
    </div>
  );
};

export const Hook: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, sec } = useScene("hook");
  const starts = [b("l1"), b("l2"), b("l3")];
  const punch = b("punch");
  const collapse = interpolate(f, [punch - sec(0.2), punch + sec(0.7)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
  const items = [
    { icon: "video", x: 1350, y: 250, at: starts[0] + 6, tilt: -5 },
    { icon: "images", x: 1610, y: 470, at: starts[1] + 6, tilt: 4 },
    { icon: "torrent", x: 1300, y: 690, at: starts[2] + 6, tilt: -3 },
    { icon: "palette", x: 1640, y: 820, at: starts[2] + 20, tilt: 6 },
    { icon: "browser", x: 1120, y: 470, at: starts[1] + 22, tilt: -7 },
  ];
  return (
    <AbsoluteFill>
      {items.map((it, i) => (
        <Clutter key={i} {...it} collapse={collapse} />
      ))}
      <div style={{ position: "absolute", left: 140, top: 300, opacity: 1 - collapse }}>
        {COPY.hook.map((line, i) => {
          const s = rise(f, fps, starts[i]);
          return (
            <div key={i} style={{ fontFamily: FONT.serif, fontSize: 92, lineHeight: 1.18, color: i === 2 ? C.text : C.muted, opacity: s, transform: `translateY(${(1 - s) * 40}px)`, filter: `blur(${(1 - s) * 8}px)` }}>
              {line}
            </div>
          );
        })}
      </div>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 6 }}>
        {COPY.hookPunch.map((w, i) => {
          const s = rise(f, fps, punch + sec(0.25) + i * 12, true);
          return (
            <div key={i} style={{ fontFamily: FONT.serif, fontStyle: i === 1 ? "italic" : "normal", fontSize: 150, lineHeight: 1.02, color: i === 1 ? "#ff8a80" : C.text, opacity: s, transform: `scale(${0.8 + 0.2 * s})`, textShadow: i === 1 ? "0 0 60px rgba(255,90,95,0.35)" : undefined }}>
              {w}
            </div>
          );
        })}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---- 2. Logo reveal ------------------------------------------------------------------------------
export const Logo: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, sec } = useScene("logo");
  const logoIn = rise(f, fps, b("logo"), true);
  const ring = interpolate(f, [b("logo"), b("logo") + sec(1.1)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE_OUT });
  const word = interpolate(f, [b("word"), b("word") + sec(1.1)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
  const tag = rise(f, fps, b("tagline"));
  const badge = rise(f, fps, b("badge"), true);
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ position: "absolute", left: 960, top: 330, width: 0, height: 0 }}>
        <div style={{ position: "absolute", left: -260 * ring, top: -260 * ring, width: 520 * ring, height: 520 * ring, borderRadius: "50%", border: `3px solid rgba(56,189,248,${0.7 * (1 - ring)})`, boxShadow: `0 0 60px rgba(56,189,248,${0.5 * (1 - ring)})` }} />
        <Img src={staticFile("brand/logo.png")} style={{ position: "absolute", left: -95, top: -95, width: 190, height: 190, transform: `scale(${logoIn}) rotate(${(1 - logoIn) * -40}deg)`, filter: `drop-shadow(0 0 ${40 * logoIn}px rgba(56,189,248,0.55))` }} />
      </div>
      <div style={{ position: "absolute", top: 470 }}>
        <Wordmark size={150} logo={false} reveal={word} />
      </div>
      <div style={{ position: "absolute", top: 670, fontFamily: FONT.ui, fontSize: 38, fontWeight: 500, color: C.muted, opacity: tag, transform: `translateY(${(1 - tag) * 20}px)` }}>
        {COPY.tagline}
      </div>
      <div style={{ position: "absolute", top: 770, transform: `scale(${0.7 + 0.3 * badge})`, opacity: badge }}>
        <Badge size={1.25} />
      </div>
    </AbsoluteFill>
  );
};

// ---- 3. Why it exists --------------------------------------------------------------------------------
export const Why: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, s } = useScene("why");
  const step = Math.round((s.beats?.stagger ?? 0.3) * fps);
  return (
    <Frame id="why">
      <div style={{ position: "absolute", left: 112, top: 270, right: 112, display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 34 }}>
        {COPY.why.map((c, i) => {
          const k = rise(f, fps, b("cards") + i * step);
          return (
            <Glass key={i} style={{ height: 270, padding: "38px 40px", opacity: k, transform: `translateY(${(1 - k) * 50}px) scale(${0.96 + 0.04 * k})`, filter: `blur(${(1 - k) * 6}px)` }}>
              <div style={{ width: 76, height: 76, borderRadius: 20, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(56,189,248,0.12)", border: "1px solid rgba(56,189,248,0.3)" }}>
                <Icon name={c.icon} size={44} color={C.brandHi} />
              </div>
              <div style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: 40, color: C.text, marginTop: 28 }}>{c.title}</div>
              <div style={{ fontFamily: FONT.ui, fontSize: 28, color: C.muted, marginTop: 8 }}>{c.line}</div>
            </Glass>
          );
        })}
      </div>
    </Frame>
  );
};

// ---- 4. The tour: one window, six tabs -----------------------------------------------------------------
const TOUR = ["video-fetched", "torrent", "images", "browser-home", "download", "history"];
const TAB_KEYS = ["tab_video", "tab_torrent", "tab_images", "tab_browser", "tab_download", "tab_history"];

export const Tour: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, s, sec } = useScene("tour");
  const step = Math.round((s.beats?.step ?? 0.75) * fps);
  const enter = rise(f, fps, sec(0.3));
  return (
    <Frame id="tour">
      <Stage
        enter={enter}
        layers={TOUR.map((src, i) => ({ src: `captures/${src}.png`, from: i === 0 ? 0 : b("sweep") + i * step, fade: 14 }))}
      >
        {TAB_KEYS.map((k, i) => (
          <Callout key={k} rect={rect("video-empty", k)} at={b("sweep") + i * step - 4} until={b("sweep") + (i + 1) * step + (i === 5 ? sec(1.4) : 0)} pad={8} radius={30} />
        ))}
      </Stage>
    </Frame>
  );
};

