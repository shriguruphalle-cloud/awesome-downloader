// Type on the canvas: the scene title (eyebrow + serif headline, words
// rising in turn), burned-in captions, keycaps, badges and the corner tag.
import React, { createContext, useContext } from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MARGIN_X } from "../tokens";
import { ramp, rise, window01 } from "./anim";

/** "wide" for the 16:9 film; "square" when the social cut draws titles and captions itself. */
export const LayoutCtx = createContext<"wide" | "square">("wide");

export const TitleBlock: React.FC<{ eyebrow?: string; title?: string; at?: number }> = ({ eyebrow, title, at = 6 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (useContext(LayoutCtx) !== "wide") return null;
  const words = (title || "").split(" ");
  return (
    <div style={{ position: "absolute", left: MARGIN_X, top: 64, right: 520 }}>
      {eyebrow ? (
        <div
          style={{
            fontFamily: FONT.ui,
            fontWeight: 650,
            fontSize: 22,
            letterSpacing: "0.2em",
            textTransform: "uppercase",
            color: C.brand,
            opacity: ramp(f, at, 18),
            transform: `translateX(${(1 - ramp(f, at, 22)) * -24}px)`,
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <span style={{ width: 28, height: 2, background: C.brand, boxShadow: `0 0 12px ${C.brand}` }} />
          {eyebrow}
        </div>
      ) : null}
      <div style={{ fontFamily: FONT.serif, fontSize: 76, lineHeight: 1.04, color: C.text, marginTop: 10, letterSpacing: "-0.01em" }}>
        {words.map((w, i) => {
          const s = rise(f, fps, at + 6 + i * 4);
          return (
            <span key={i} style={{ display: "inline-block", opacity: s, transform: `translateY(${(1 - s) * 28}px)`, filter: `blur(${(1 - s) * 6}px)`, marginRight: "0.24em" }}>
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export const Caption: React.FC<{ text?: string; at?: number; until: number }> = ({ text, at = 24, until }) => {
  const f = useCurrentFrame();
  if (!text || useContext(LayoutCtx) !== "wide") return null;
  const on = window01(f, at, until, 18);
  if (on <= 0) return null;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 34, display: "flex", justifyContent: "center", opacity: on }}>
      <div
        style={{
          maxWidth: 1600,
          padding: "16px 34px",
          borderRadius: 22,
          background: "rgba(6,12,32,0.78)",
          border: `1px solid ${C.rim}`,
          boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
          fontFamily: FONT.ui,
          fontSize: 32,
          fontWeight: 500,
          lineHeight: 1.32,
          color: C.text,
          textAlign: "center",
          transform: `translateY(${(1 - on) * 14}px)`,
        }}
      >
        {text}
      </div>
    </div>
  );
};

export const Keycaps: React.FC<{ keys: string[]; at: number; until: number; x: number; y: number; label?: string }> = ({
  keys,
  at,
  until,
  x,
  y,
  label,
}) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const on = window01(f, at - 10, until, 14);
  if (on <= 0) return null;
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: "translate(-50%, -50%)", opacity: on, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        {keys.map((k, i) => {
          const press = f >= at + i * 9 && f < at + i * 9 + 14 + (keys.length - 1 - i) * 9;
          const s = rise(f, fps, at - 10 + i * 3, true);
          return (
            <React.Fragment key={i}>
              {i > 0 ? <span style={{ fontFamily: FONT.ui, fontSize: 34, color: C.muted, fontWeight: 600 }}>+</span> : null}
              <div
                style={{
                  minWidth: 96,
                  height: 96,
                  padding: "0 26px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  borderRadius: 18,
                  fontFamily: FONT.ui,
                  fontWeight: 650,
                  fontSize: 36,
                  color: C.text,
                  background: press ? "linear-gradient(#1b2b5c, #14224a)" : "linear-gradient(#22356c, #172755)",
                  border: `1.5px solid ${press ? C.brand : C.rimHi}`,
                  boxShadow: press
                    ? `0 2px 0 #0a1330, 0 0 24px rgba(56,189,248,0.5)`
                    : `0 8px 0 #0a1330, 0 18px 30px rgba(0,0,0,0.45)`,
                  transform: `translateY(${press ? 6 : 0}px) scale(${0.85 + 0.15 * s})`,
                }}
              >
                {k}
              </div>
            </React.Fragment>
          );
        })}
      </div>
      {label ? (
        <div style={{ fontFamily: FONT.ui, fontSize: 26, fontWeight: 600, color: C.text, padding: "8px 18px", borderRadius: 999, background: "rgba(6,12,32,0.8)", border: `1px solid ${C.rim}` }}>
          {label}
        </div>
      ) : null}
    </div>
  );
};

export const Badge: React.FC<{ size?: number; text?: string; sub?: string }> = ({ size = 1, text = "Free & open source", sub = "GPL-3.0" }) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 14 * size,
      padding: `${12 * size}px ${24 * size}px`,
      borderRadius: 999,
      background: "rgba(255,255,255,0.06)",
      border: `${1.5 * size}px solid rgba(56,189,248,0.45)`,
      boxShadow: `inset 0 1px 0 ${C.rimHi}, 0 0 ${30 * size}px rgba(56,189,248,0.18)`,
      fontFamily: FONT.ui,
      fontWeight: 650,
      fontSize: 20 * size,
      letterSpacing: "0.16em",
      textTransform: "uppercase",
      color: C.text,
    }}
  >
    <span style={{ width: 9 * size, height: 9 * size, borderRadius: "50%", background: C.brand, boxShadow: `0 0 ${12 * size}px ${C.brand}` }} />
    {text}
    {sub ? (
      <>
        <span style={{ width: 1, height: 20 * size, background: C.rimHi }} />
        <span style={{ color: C.brandHi }}>{sub}</span>
      </>
    ) : null}
  </div>
);

/** The persistent, quiet "free & open source" tag in the top-right corner. */
export const CornerTag: React.FC<{ visible: number }> = ({ visible }) => {
  if (visible <= 0 || useContext(LayoutCtx) !== "wide") return null;
  return (
    <div style={{ position: "absolute", right: MARGIN_X, top: 72, opacity: 0.85 * visible }}>
      <Badge size={0.82} />
    </div>
  );
};

export const Glass: React.FC<{ style?: React.CSSProperties; children?: React.ReactNode }> = ({ style, children }) => (
  <div
    style={{
      borderRadius: 26,
      background: "linear-gradient(160deg, rgba(255,255,255,0.085), rgba(255,255,255,0.035))",
      border: `1px solid ${C.rim}`,
      boxShadow: `inset 0 1px 0 ${C.rimHi}, 0 30px 80px -30px ${C.shade}`,
      backdropFilter: "blur(18px)",
      ...style,
    }}
  >
    {children}
  </div>
);
