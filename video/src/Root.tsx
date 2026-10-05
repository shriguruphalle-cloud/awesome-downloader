import React from "react";
import { Composition } from "remotion";
import { FPS, TOTAL_FRAMES, scene, sceneFrames } from "./config";
import { SOCIAL_SCENES } from "./socialConfig";
import { Showcase } from "./Showcase";
import { Social } from "./Social";
import { Poster } from "./Poster";
import { loadFonts } from "./fonts";

loadFonts();

const socialFrames = SOCIAL_SCENES.reduce((n, id) => n + sceneFrames(scene(id)), 0);

export const Root: React.FC = () => (
  <>
    <Composition id="Showcase" component={Showcase} durationInFrames={TOTAL_FRAMES} fps={FPS} width={1920} height={1080} />
    <Composition id="Social" component={Social} durationInFrames={socialFrames} fps={FPS} width={1080} height={1080} />
    <Composition id="Poster" component={Poster} durationInFrames={1} fps={FPS} width={1920} height={1080} />
  </>
);
