import { Config } from "@remotion/cli/config";

// Deterministic, high-quality output: PNG frames (no JPEG artefacts in the
// UI's fine text), H.264 encode at the end.
Config.setVideoImageFormat("png");
Config.setConcurrency(12);
Config.setChromiumOpenGlRenderer("angle");
Config.setOverwriteOutput(true);
