// The app's own fonts (ui_qt/fonts, SIL OFL): Instrument Serif for the name
// and headlines, Inter for everything else. Loaded before the first frame.
import { continueRender, delayRender, staticFile } from "remotion";

const faces: [string, string, FontFaceDescriptors][] = [
  ["Instrument Serif", "fonts/InstrumentSerif-Regular.ttf", { style: "normal", weight: "400" }],
  ["Instrument Serif", "fonts/InstrumentSerif-Italic.ttf", { style: "italic", weight: "400" }],
  ["Inter", "fonts/InterVariable.ttf", { style: "normal", weight: "100 900" }],
];

let started = false;

export const loadFonts = () => {
  if (started || typeof document === "undefined") return;
  started = true;
  const handle = delayRender("Loading fonts");
  Promise.all(
    faces.map(async ([family, file, desc]) => {
      const face = new FontFace(family, `url(${staticFile(file)})`, desc);
      await face.load();
      (document.fonts as unknown as { add: (f: FontFace) => void }).add(face);
    }),
  )
    .then(() => continueRender(handle))
    .catch((err) => {
      console.error(err);
      continueRender(handle);
    });
};
