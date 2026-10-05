// The film: the shared backdrop, every scene in order, the quiet corner tag,
// and the soundtrack.
import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame } from "remotion";
import { MUSIC, SCENES, sceneFrames, sceneStart } from "./config";
import { Backdrop } from "./components/Backdrop";
import { SceneShell } from "./components/SceneShell";
import { CornerTag } from "./components/Text";
import { ramp } from "./components/anim";
import { Hook, Logo, Tour, Why } from "./scenes/Opening";
import {
  Bookmarks, BrowserHome, BrowserSite, Downloads, History, Images, Private, Queue, Settings, Torrent, VideoFetch, VideoOptions,
} from "./scenes/Features";
import { OpenSource, Outro, Performance, QuickStart, Themes, Unique } from "./scenes/Closing";

export const SCENE_COMPONENTS: Record<string, React.FC> = {
  hook: Hook, logo: Logo, why: Why, tour: Tour,
  videoFetch: VideoFetch, videoOptions: VideoOptions, queue: Queue, downloads: Downloads, images: Images,
  torrent: Torrent, browserHome: BrowserHome, browserSite: BrowserSite, bookmarks: Bookmarks, private: Private,
  history: History, settings: Settings, themes: Themes, unique: Unique, performance: Performance,
  openSource: OpenSource, quickStart: QuickStart, outro: Outro,
};

const TagLayer: React.FC = () => {
  const f = useCurrentFrame();
  const on = sceneStart("why");
  const off = sceneStart("openSource");
  const v = Math.min(ramp(f, on + 30, 24), 1 - ramp(f, off - 20, 20));
  return <CornerTag visible={v} />;
};

export const Showcase: React.FC = () => (
  <AbsoluteFill style={{ background: "#0d1734" }}>
    <Backdrop />
    {SCENES.map((s) => {
      const C = SCENE_COMPONENTS[s.id];
      return (
        <Sequence key={s.id} from={sceneStart(s.id)} durationInFrames={sceneFrames(s)} name={s.id}>
          <SceneShell>
            <C />
          </SceneShell>
        </Sequence>
      );
    })}
    <TagLayer />
    <Audio src={staticFile(MUSIC ? "audio/soundtrack-music.wav" : "audio/soundtrack.wav")} />
  </AbsoluteFill>
);
