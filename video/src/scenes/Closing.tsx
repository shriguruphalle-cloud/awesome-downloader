// Themes, the differentiators, performance & privacy, open source, the
// three-step start, and the outro.
import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame } from "remotion";
import { COPY, LINKS, PALETTE_LABEL, PALETTE_ORDER } from "../config";
import { C, CAPTURE, EASE, FONT, STAGE } from "../tokens";
import { rise } from "../components/anim";
import { Icon } from "../components/Icon";
import { Badge, Glass } from "../components/Text";
import { Rect, Stage } from "../components/Stage";
import { Wordmark } from "../components/Wordmark";
import { Frame, useScene } from "./common";

// ---- Themes: every palette, night then day, on the same screen ---------------------------------------
const TINT: Record<string, [string, string]> = {
  // [night glow, day glow] -- the palette's first light (ui_qt/palettes.py)
  sapphire: ["#38bdf8", "#68a8f6"], ruby: ["#be2e4c", "#d6788c"], gold: ["#d4a042", "#e2b864"],
  emerald: ["#189670", "#50be96"], obsidian: ["#d6deec", "#c0c6d4"], amethyst: ["#8c62dc", "#b28cec"],
  rose: ["#de9c88", "#e6a692"],
};

export const Themes: React.FC = () => {
  const f = useCurrentFrame();
  const { b, s, fps, sec } = useScene("themes");
  const enter = rise(f, fps, sec(0.25));
  const night = Math.round((s.beats?.night ?? 1.75) * fps);
  const day = Math.round((s.beats?.day ?? 1.45) * fps);
  const seq: { name: string; mode: "night" | "day"; from: number }[] = [];
  let t = b("start");
  for (const name of PALETTE_ORDER) {
    seq.push({ name, mode: "night", from: t });
    t += night;
  }
  for (const name of PALETTE_ORDER) {
    seq.push({ name, mode: "day", from: t });
    t += day;
  }
  const current = [...seq].reverse().find((x) => f >= x.from) ?? seq[0];
  return (
    <Frame id="themes">
      {seq.map((x, i) => {
        const o = interpolate(f, [x.from, x.from + 18], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
        const next = seq[i + 1];
        const out = next ? interpolate(f, [next.from + 18, next.from + 19], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1;
        const tint = TINT[x.name][x.mode === "night" ? 0 : 1];
        if (o * out <= 0) return null;
        return (
          <AbsoluteFill key={i} style={{ opacity: o * out * 0.5, background: `radial-gradient(70% 60% at 50% 40%, ${tint}55 0%, ${tint}18 45%, transparent 75%)` }} />
        );
      })}
      <Stage
        enter={enter}
        layers={[
          { src: "captures/palette-sapphire-night.png" },
          ...seq.map((x) => ({ src: `captures/palette-${x.name}-${x.mode}.png`, from: x.from, fade: 18 })),
        ]}
      />
      <div style={{ position: "absolute", left: STAGE.x + STAGE.w / 2, top: STAGE.y + STAGE.h - 34, transform: "translate(-50%, -100%)", display: "flex", gap: 12, opacity: enter }}>
        <div style={{ padding: "12px 26px", borderRadius: 999, background: "rgba(6,12,32,0.82)", border: `1px solid ${C.rimHi}`, fontFamily: FONT.ui, fontWeight: 650, fontSize: 30, color: C.text, display: "flex", gap: 14, alignItems: "center" }}>
          <span style={{ width: 16, height: 16, borderRadius: "50%", background: TINT[current.name][current.mode === "night" ? 0 : 1], boxShadow: `0 0 14px ${TINT[current.name][0]}` }} />
          {PALETTE_LABEL[current.name]}
          <span style={{ color: C.muted, fontWeight: 500 }}>· {current.mode === "night" ? "Night" : "Day"}</span>
        </div>
      </div>
    </Frame>
  );
};

// ---- Only here: four details -------------------------------------------------------------------------------
const Crop: React.FC<{ src: string; r: Rect; w: number; h: number; video?: boolean }> = ({ src, r, w, h, video }) => {
  const scale = Math.max(w / r[2], h / r[3]);
  const style: React.CSSProperties = {
    position: "absolute",
    left: -r[0] * scale - (r[2] * scale - w) / 2,
    top: -r[1] * scale - (r[3] * scale - h) / 2,
    width: CAPTURE.w * scale,
    height: CAPTURE.h * scale,
  };
  return (
    <div style={{ position: "relative", width: w, height: h, overflow: "hidden", borderRadius: 18 }}>
      {video ? <OffthreadVideo src={staticFile(src)} style={style} muted /> : <Img src={staticFile(src)} style={style} />}
    </div>
  );
};

export const Unique: React.FC = () => {
  const f = useCurrentFrame();
  const { b, s, fps } = useScene("unique");
  const step = Math.round((s.beats?.stagger ?? 0.4) * fps);
  const tiles: { src: string; r: Rect; video?: boolean }[] = [
    { src: "captures/torrent-live.mp4", r: [36, 524, 1012, 298], video: true },
    { src: "captures/browser-private.png", r: [380, 360, 1800, 900] },
    { src: "captures/browser-home.png", r: [0, 360, 2560, 1080] },
    { src: "captures/browser-site.png", r: [1500, 176, 1060, 312] },
  ];
  return (
    <Frame id="unique">
      <div style={{ position: "absolute", left: 112, right: 112, top: 236, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 30 }}>
        {tiles.map((t, i) => {
          const k = rise(f, fps, b("tiles") + i * step);
          return (
            <Glass key={i} style={{ padding: 18, opacity: k, transform: `translateY(${(1 - k) * 40}px) scale(${0.97 + 0.03 * k})`, filter: `blur(${(1 - k) * 6}px)` }}>
              <Crop src={t.src} r={t.r} w={808} h={238} video={t.video} />
              <div style={{ display: "flex", alignItems: "baseline", gap: 16, padding: "16px 8px 4px" }}>
                <span style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: 32, color: C.text }}>{COPY.unique[i].title}</span>
                <span style={{ fontFamily: FONT.ui, fontSize: 24, color: C.muted }}>{COPY.unique[i].line}</span>
              </div>
            </Glass>
          );
        })}
      </div>
    </Frame>
  );
};

// ---- Light & private ------------------------------------------------------------------------------------------
const PERF_ICONS = ["bolt", "leaf", "gpu", "shield"];

export const Performance: React.FC = () => {
  const f = useCurrentFrame();
  const { b, s, fps } = useScene("performance");
  const step = Math.round((s.beats?.stagger ?? 0.45) * fps);
  return (
    <Frame id="performance">
      <div style={{ position: "absolute", left: 112, right: 112, top: 280, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 30 }}>
        {COPY.performance.map((p, i) => {
          const k = rise(f, fps, b("cards") + i * step);
          return (
            <Glass key={i} style={{ minHeight: 190, padding: "38px 40px", display: "flex", gap: 28, alignItems: "flex-start", opacity: k, transform: `translateY(${(1 - k) * 40}px)` }}>
              <div style={{ flex: "0 0 auto", width: 72, height: 72, borderRadius: 20, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(56,189,248,0.12)", border: "1px solid rgba(56,189,248,0.3)" }}>
                <Icon name={PERF_ICONS[i]} size={40} color={C.brandHi} />
              </div>
              <div>
                <div style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: 34, color: C.text }}>{p.title}</div>
                <div style={{ fontFamily: FONT.ui, fontSize: 25, color: C.muted, marginTop: 6, lineHeight: 1.35 }}>{p.line}</div>
              </div>
            </Glass>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 112, right: 112, top: 760, display: "flex", flexWrap: "wrap", gap: 14, justifyContent: "center" }}>
        {COPY.stack.map((x, i) => {
          const k = rise(f, fps, b("stack") + i * 5, true);
          return (
            <div key={x} style={{ padding: "12px 24px", borderRadius: 999, fontFamily: FONT.ui, fontWeight: 600, fontSize: 26, color: C.text, background: "rgba(255,255,255,0.06)", border: `1px solid ${C.rim}`, opacity: k, transform: `scale(${0.8 + 0.2 * k})` }}>
              {x}
            </div>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 850, textAlign: "center", fontFamily: FONT.ui, fontSize: 24, color: C.faint, opacity: rise(f, fps, b("stack") + 40) }}>
        Built with open-source parts — every one listed with its licence in NOTICE.md
      </div>
    </Frame>
  );
};

// ---- Free & open source ----------------------------------------------------------------------------------------
export const OpenSource: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps } = useScene("openSource");
  const badge = rise(f, fps, b("badge"), true);
  const repo = rise(f, fps, b("repo"));
  const star = rise(f, fps, b("star"), true);
  const glow = 0.5 + 0.5 * Math.sin(f / 14);
  return (
    <Frame id="openSource">
      <div style={{ position: "absolute", left: 112, top: 300, width: 1000 }}>
        <div style={{ transform: `scale(${0.8 + 0.2 * badge})`, transformOrigin: "0 50%", opacity: badge }}>
          <Badge size={1.6} />
        </div>
        <Glass style={{ marginTop: 40, padding: "30px 36px", opacity: repo, transform: `translateY(${(1 - repo) * 30}px)`, display: "flex", alignItems: "center", gap: 24 }}>
          <Icon name="github" size={58} color={C.text} />
          <div>
            <div style={{ fontFamily: FONT.ui, fontSize: 22, letterSpacing: "0.14em", textTransform: "uppercase", color: C.brand, fontWeight: 650 }}>Source code</div>
            <div style={{ fontFamily: FONT.ui, fontSize: 30, color: C.text, fontWeight: 600, marginTop: 6, whiteSpace: "nowrap" }}>{LINKS.repo}</div>
          </div>
        </Glass>
        <div style={{ marginTop: 34, display: "flex", gap: 20, alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "20px 36px", borderRadius: 18, background: `linear-gradient(${C.emberTop}, ${C.ember})`, color: C.emberText, fontFamily: FONT.ui, fontWeight: 750, fontSize: 34, transform: `scale(${star})`, boxShadow: `0 0 ${30 + 30 * glow}px rgba(255,106,19,0.45)` }}>
            <Icon name="star" size={38} color={C.emberText} /> Star on GitHub
          </div>
          <div style={{ fontFamily: FONT.ui, fontSize: 28, color: C.muted, opacity: star }}>Licence: {LINKS.license}</div>
        </div>
      </div>
      <div style={{ position: "absolute", left: 1170, top: 300, width: 640, display: "flex", flexDirection: "column", gap: 22 }}>
        {COPY.openSource.map((p, i) => {
          const k = rise(f, fps, b("points") + i * 14);
          return (
            <Glass key={i} style={{ padding: "28px 30px", display: "flex", alignItems: "center", gap: 18, opacity: k, transform: `translateX(${(1 - k) * 40}px)` }}>
              <Icon name={i === 3 ? "coffee" : "shield"} size={36} color={i === 3 ? C.emberTop : C.success} />
              <span style={{ fontFamily: FONT.ui, fontSize: 29, color: C.text, fontWeight: 550 }}>{p}</span>
            </Glass>
          );
        })}
      </div>
    </Frame>
  );
};

// ---- Quick start ---------------------------------------------------------------------------------------------------
export const QuickStart: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps } = useScene("quickStart");
  const starts = [b("s1"), b("s2"), b("s3")];
  const req = rise(f, fps, b("req"));
  return (
    <Frame id="quickStart">
      <div style={{ position: "absolute", left: 112, right: 112, top: 250, display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 30 }}>
        {COPY.steps.map((st, i) => {
          const k = rise(f, fps, starts[i]);
          return (
            <Glass key={i} style={{ height: 470, padding: "40px 38px", opacity: k, transform: `translateY(${(1 - k) * 50}px)`, display: "flex", flexDirection: "column" }}>
              <div style={{ width: 84, height: 84, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: FONT.serif, fontSize: 56, color: C.emberText, background: `linear-gradient(${C.emberTop}, ${C.ember})`, boxShadow: "0 0 30px rgba(255,106,19,0.4)" }}>
                {i + 1}
              </div>
              <div style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: 40, color: C.text, marginTop: 30 }}>{st.title}</div>
              <div style={{ marginTop: 22, padding: "14px 18px", borderRadius: 14, background: "rgba(4,10,30,0.55)", border: `1px solid ${C.rim}`, fontFamily: FONT.ui, fontWeight: 600, fontSize: i === 0 ? 24 : 30, color: C.brandHi, alignSelf: "flex-start" }}>
                {st.detail}
              </div>
              <div style={{ fontFamily: FONT.ui, fontSize: 25, color: C.muted, marginTop: "auto", lineHeight: 1.35 }}>{st.sub}</div>
            </Glass>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 760, display: "flex", justifyContent: "center", gap: 18, alignItems: "center", opacity: req, fontFamily: FONT.ui, fontSize: 28, color: C.text }}>
        <Icon name="windows" size={30} color={C.brandHi} />
        Get it at <b style={{ color: C.brandHi }}>{LINKS.website}</b> or <b style={{ color: C.brandHi }}>{LINKS.releases}</b>
      </div>
    </Frame>
  );
};

// ---- Outro -------------------------------------------------------------------------------------------------------------
export const Outro: React.FC = () => {
  const f = useCurrentFrame();
  const { b, fps, sec } = useScene("outro");
  const logo = rise(f, fps, b("logo"), true);
  const word = interpolate(f, [b("logo") + 10, b("logo") + sec(1.2)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE });
  const url = rise(f, fps, b("url"));
  const cta = rise(f, fps, b("cta"), true);
  const pulse = 0.5 + 0.5 * Math.sin(f / 16);
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div style={{ position: "absolute", top: 250, transform: `scale(${0.9 + 0.1 * logo})`, opacity: logo, clipPath: `inset(-30% ${(1 - word) * 50}% -30% ${(1 - word) * 50}%)` }}>
        <Wordmark size={132} />
      </div>
      <div style={{ position: "absolute", top: 450, fontFamily: FONT.ui, fontSize: 36, color: C.muted, opacity: url }}>{COPY.tagline}</div>
      <div style={{ position: "absolute", top: 530, fontFamily: FONT.ui, fontSize: 52, fontWeight: 650, color: C.text, opacity: url, transform: `translateY(${(1 - url) * 20}px)` }}>
        {LINKS.website}
      </div>
      <div style={{ position: "absolute", top: 650, display: "flex", gap: 24, transform: `scale(${cta})` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "22px 40px", borderRadius: 18, background: `linear-gradient(${C.emberTop}, ${C.ember})`, color: C.emberText, fontFamily: FONT.ui, fontWeight: 750, fontSize: 34, boxShadow: `0 0 ${30 + 30 * pulse}px rgba(255,106,19,0.45)` }}>
          <Icon name="download" size={36} color={C.emberText} stroke={2.4} /> {COPY.outroCta[0]}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "22px 40px", borderRadius: 18, background: "rgba(255,255,255,0.07)", border: `1.5px solid ${C.rimHi}`, color: C.text, fontFamily: FONT.ui, fontWeight: 700, fontSize: 34 }}>
          <Icon name="star" size={34} color={C.text} /> {COPY.outroCta[1]}
        </div>
      </div>
      <div style={{ position: "absolute", top: 800, opacity: cta }}>
        <Badge size={1.1} />
      </div>
      <div style={{ position: "absolute", top: 900, fontFamily: FONT.ui, fontSize: 24, color: C.faint, opacity: cta }}>
        {COPY.credit} · {LINKS.repo}
      </div>
    </AbsoluteFill>
  );
};

