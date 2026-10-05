// Every scene comes in and goes out the same way: blur-to-focus and a small
// rise in, a soft fade out to the shared backdrop (at least 18 frames each).
import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { EASE, EASE_OUT } from "../tokens";
import { interpolate } from "remotion";

export const SceneShell: React.FC<{ children: React.ReactNode; inLen?: number; outLen?: number }> = ({
  children,
  inLen = 22,
  outLen = 20,
}) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const a = interpolate(f, [0, inLen], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE_OUT });
  const b = interpolate(f, [durationInFrames - outLen, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });
  const o = Math.min(a, b);
  return (
    <AbsoluteFill
      style={{
        opacity: o,
        filter: o < 1 ? `blur(${(1 - o) * 10}px)` : undefined,
        transform: `scale(${1.015 - 0.015 * a + (1 - b) * 0.01})`,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
