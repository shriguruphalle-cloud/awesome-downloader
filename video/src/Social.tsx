// The 1:1 social cut: a subset of scenes, each 16:9 scene shown full-width in
// the square's middle band, with its headline above and its caption below
// set large for phones.
import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { SOCIAL_SCENES } from "./socialConfig";
import { scene, sceneFrames } from "./config";
import { Backdrop } from "./components/Backdrop";
import { SceneShell } from "./components/SceneShell";
import { Badge, LayoutCtx } from "./components/Text";
import { rise, window01 } from "./components/anim";
import { C, FONT, STAGE } from "./tokens";

// Scenes built around the app window: the window fills the square's width.
const STAGE_SCENES = new Set(["videoFetch", "queue", "torrent", "browserSite", "private", "themes"]);
const BAND = { x: 30, y: 262, w: 1020 };
import { SCENE_COMPONENTS } from "./Showcase";

const Bands: React.FC<{ id: string }> = ({ id }) => {
  const f = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const s = scene(id);
  const t = rise(f, fps, 8);
  const cap = window01(f, 30, durationInFrames - 20, 18);
  return (
    <>
      {s.title ? (
        <div style={{ position: "absolute", left: 60, right: 60, top: 70, textAlign: "center" }}>
          <div style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: 28, letterSpacing: "0.18em", textTransform: "uppercase", color: C.brand, opacity: t }}>{s.eyebrow}</div>
          <div style={{ fontFamily: FONT.serif, fontSize: 74, lineHeight: 1.05, color: C.text, marginTop: 10, opacity: t, transform: `translateY(${(1 - t) * 20}px)` }}>{s.title}</div>
        </div>
      ) : null}
      {s.caption ? (
        <div style={{ position: "absolute", left: 60, right: 60, top: 876, textAlign: "center", fontFamily: FONT.ui, fontWeight: 500, fontSize: 36, lineHeight: 1.3, color: C.text, opacity: cap }}>
          {s.caption}
        </div>
      ) : null}
    </>
  );
};

export const Social: React.FC = () => {
  let from = 0;
  return (
    <AbsoluteFill style={{ background: "#0d1734" }}>
      <Backdrop />
      <LayoutCtx.Provider value="square">
        {SOCIAL_SCENES.map((id) => {
          const s = scene(id);
          const len = sceneFrames(s);
          const start = from;
          from += len;
          const Comp = SCENE_COMPONENTS[id];
          const k = STAGE_SCENES.has(id) ? BAND.w / STAGE.w : 0.5625;
          const left = STAGE_SCENES.has(id) ? BAND.x - STAGE.x * k : 0;
          const top = STAGE_SCENES.has(id) ? BAND.y - STAGE.y * k : 250;
          return (
            <Sequence key={id} from={start} durationInFrames={len} name={id}>
              <SceneShell>
                <div style={{ position: "absolute", left, top, width: 1920, height: 1080, transform: `scale(${k})`, transformOrigin: "0 0" }}>
                  <Comp />
                </div>
                <Bands id={id} />
              </SceneShell>
            </Sequence>
          );
        })}
      </LayoutCtx.Provider>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 22, display: "flex", justifyContent: "center", opacity: 0.9 }}>
        <Badge size={0.9} />
      </div>
      <Audio src={staticFile("audio/soundtrack-social.wav")} />
    </AbsoluteFill>
  );
};
