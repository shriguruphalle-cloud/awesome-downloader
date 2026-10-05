// The name as the app and the website set it (ui_qt/widgets/wordmark.py):
// the lightning logo as tall as the letters, "Awesome" upright, "Downloader"
// italic and lit with the palette's gradient.
import React from "react";
import { Img, staticFile } from "remotion";
import { C, FONT } from "../tokens";

export const Wordmark: React.FC<{ size: number; logo?: boolean; reveal?: number; glow?: boolean }> = ({
  size,
  logo = true,
  reveal = 1,
  glow = true,
}) => {
  const capH = size * 0.74; // Instrument Serif: ascender to baseline
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: size * 0.24, whiteSpace: "nowrap" }}>
      {logo ? (
        <Img
          src={staticFile("brand/logo.png")}
          style={{ width: capH, height: capH, alignSelf: "baseline", transform: `translateY(${size * 0.02}px)` }}
        />
      ) : null}
      <div
        style={{
          fontFamily: FONT.serif,
          fontSize: size,
          lineHeight: 1,
          color: C.text,
          letterSpacing: "-0.01em",
          clipPath: `inset(-20% ${(1 - reveal) * 100}% -30% -5%)`,
        }}
      >
        Awesome{" "}
        <span
          style={{
            fontStyle: "italic",
            backgroundImage: `linear-gradient(100deg, ${C.brandHi} 0%, ${C.brand} 45%, ${C.indigo} 100%)`,
            WebkitBackgroundClip: "text",
            backgroundClip: "text",
            color: "transparent",
            filter: glow ? `drop-shadow(0 0 ${size * 0.18}px rgba(56,189,248,0.35))` : undefined,
            paddingRight: size * 0.08,
          }}
        >
          Downloader
        </span>
      </div>
    </div>
  );
};
