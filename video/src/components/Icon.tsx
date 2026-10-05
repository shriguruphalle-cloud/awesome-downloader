// Line icons in the app's style (24-unit grid, round caps), for the cards.
import React from "react";

const P: Record<string, React.ReactNode> = {
  video: (<><rect x="3" y="5" width="18" height="14" rx="3" /><path d="M10 9.2v5.6l4.6-2.8z" fill="currentColor" /></>),
  images: (<><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="9" cy="9.5" r="1.8" /><path d="M4 18l5.5-5.5 3.5 3.5 2.5-2.5L21 18" /></>),
  torrent: (<><path d="M12 3v11" /><path d="M7.5 9.5L12 14l4.5-4.5" /><path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16" /></>),
  browser: (<><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3z" /></>),
  history: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" /></>),
  palette: (<><path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.8 1.8-1.7 0-1.4-1.3-1.6-1.3-2.9 0-1 .8-1.6 1.8-1.6H17a4 4 0 0 0 4-4C21 6.4 17 3 12 3z" /><circle cx="7.5" cy="11" r="1.2" fill="currentColor" /><circle cx="10" cy="7.2" r="1.2" fill="currentColor" /><circle cx="14.6" cy="7.4" r="1.2" fill="currentColor" /></>),
  bolt: (<path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z" />),
  leaf: (<><path d="M5 19c0-8 5-13 15-14-1 10-6 15-14 15z" /><path d="M5 19l7-7" /></>),
  gpu: (<><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="15" cy="12" r="3" /><path d="M7 10h3M7 14h3" /></>),
  shield: (<><path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z" /><path d="M8.5 12l2.5 2.5 4.5-5" /></>),
  star: (<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />),
  download: (<><path d="M12 4v11" /><path d="M7.5 10.5L12 15l4.5-4.5" /><path d="M5 19.5h14" /></>),
  github: (<path d="M12 2.8a9.3 9.3 0 0 0-2.9 18.1c.5.1.6-.2.6-.5v-1.7c-2.6.6-3.1-1.2-3.1-1.2-.4-1.1-1-1.4-1-1.4-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .8 1.5 2.3 1 2.8.8.1-.6.3-1 .6-1.2-2-.2-4.2-1-4.2-4.6 0-1 .4-1.8 1-2.5-.1-.2-.4-1.2.1-2.4 0 0 .8-.3 2.5 1a8.6 8.6 0 0 1 4.6 0c1.8-1.3 2.5-1 2.5-1 .5 1.3.2 2.2.1 2.4.6.7 1 1.5 1 2.5 0 3.6-2.2 4.4-4.2 4.6.3.3.6.8.6 1.6v2.4c0 .3.2.6.6.5A9.3 9.3 0 0 0 12 2.8z" fill="currentColor" stroke="none" />),
  coffee: (<><path d="M4 9h12v5.5A4.5 4.5 0 0 1 11.5 19h-3A4.5 4.5 0 0 1 4 14.5z" /><path d="M16 10.5h1a3 3 0 0 1 0 6h-1" /><path d="M7.5 3.5v2.5M10.5 3.5v2.5M13.5 3.5v2.5" /></>),
  windows: (<path d="M3 5.2l7.5-1v7.3H3zM11.5 4.1L21 3v8.5h-9.5zM3 12.5h7.5v7.3L3 18.8zM11.5 12.5H21V21l-9.5-1.2z" fill="currentColor" stroke="none" />),
};

export const Icon: React.FC<{ name: string; size?: number; color?: string; stroke?: number }> = ({ name, size = 40, color = "currentColor", stroke = 1.8 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" style={{ color }}>
    {P[name]}
  </svg>
);
