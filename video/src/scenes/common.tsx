// What every scene needs: its config, its beats in frames, and the title +
// caption that frame it.
import React from "react";
import { useVideoConfig } from "remotion";
import { scene } from "../config";
import { Caption, TitleBlock } from "../components/Text";

export const useScene = (id: string) => {
  const s = scene(id);
  const { fps, durationInFrames } = useVideoConfig();
  const b = (key: string, plus = 0) => {
    const v = s.beats?.[key];
    if (v === undefined) throw new Error(`scene ${id} has no beat ${key}`);
    return Math.round((v + plus) * fps);
  };
  return { s, b, fps, dur: durationInFrames, sec: (x: number) => Math.round(x * fps) };
};

export const Frame: React.FC<{ id: string; children: React.ReactNode; captionAt?: number }> = ({ id, children, captionAt }) => {
  const { s, dur, sec } = useScene(id);
  return (
    <>
      {children}
      <TitleBlock eyebrow={s.eyebrow} title={s.title} />
      <Caption text={s.caption} at={captionAt ?? sec(0.6)} until={dur - sec(0.35)} />
    </>
  );
};
