// The poster / thumbnail: the name and the badge beside the real app window.
import React from "react";
import { AbsoluteFill } from "remotion";
import { COPY } from "./config";
import { Backdrop } from "./components/Backdrop";
import { Stage } from "./components/Stage";
import { Badge } from "./components/Text";
import { Wordmark } from "./components/Wordmark";
import { C, FONT } from "./tokens";

export const Poster: React.FC = () => (
  <AbsoluteFill>
    <Backdrop drift={false} />
    <div style={{ position: "absolute", left: 110, top: 300, width: 760 }}>
      <Wordmark size={80} />
      <div style={{ fontFamily: FONT.ui, fontSize: 36, lineHeight: 1.3, color: C.muted, marginTop: 34, textWrap: "balance" } as React.CSSProperties}>{COPY.tagline}</div>
      <div style={{ marginTop: 44 }}>
        <Badge size={1.15} />
      </div>
      <div style={{ fontFamily: FONT.ui, fontSize: 28, color: C.faint, marginTop: 40 }}>Windows 10 &amp; 11 · No ads · No account</div>
    </div>
    <Stage box={{ x: 990, y: 200, w: 1180, h: 664 }} layers={[{ src: "captures/video-fetched.png" }]} />
  </AbsoluteFill>
);
