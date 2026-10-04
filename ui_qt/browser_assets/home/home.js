/* The Browser's home page. Everything it shows comes from the app as one
   "state" message (palette, shortcuts, suggestions, recent pages, options);
   everything it does goes back as a message ({type: 'go' | 'pin' | ...}).
   Wallpapers are painted here, on canvases in depth layers, and the
   parallax moves those layers with GPU transforms only -- nothing is
   repainted while the pointer moves, which is what keeps it at the
   display's full frame rate. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const post = (msg) => { try { window.chrome.webview.postMessage(msg); } catch (e) { /* not in the app */ } };
  const SLACK = 26;                 // px each layer may travel (matches --slack)
  const body = document.body;

  let state = null;
  let layers = [];                  // [{el, depth}]
  let wallKey = '';
  let still = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------------------------------------------------------------- utils
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const svg = (markup) => {
    const t = document.createElement('template');
    t.innerHTML = markup.trim();
    return t.content.firstChild;
  };
  const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  const ICON_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
  const ICON_GLOBE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.9 5.2 3.9 8.5s-1.3 6.1-3.9 8.5c-2.6-2.4-3.9-5.2-3.9-8.5s1.3-6.1 3.9-8.5z"/></svg>';
  const ICON_SEARCH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>';
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const mix = (a, b, t) => [0, 1, 2].map(i => Math.round(a[i] + (b[i] - a[i]) * t));

  // Where a click goes: a plain click in this tab, Ctrl or the middle button
  // behind in a new tab, Shift in a new tab in front.
  const where = (e) => (e.button === 1 || e.ctrlKey || e.metaKey) ? 'background' : e.shiftKey ? 'tab' : 'current';
  const go = (text, how) => { if (text) post({ type: 'go', text, where: how || 'current' }); };

  function linkify(node, url, onContext) {
    node.addEventListener('mousedown', (e) => { if (e.button === 1) e.preventDefault(); });   // no autoscroll
    node.addEventListener('click', (e) => { if (e.button === 0) { e.preventDefault(); go(url, where(e)); } });
    node.addEventListener('auxclick', (e) => { if (e.button === 1) { e.preventDefault(); go(url, 'background'); } });
    node.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(url, where(e)); });
    if (onContext) node.addEventListener('contextmenu', (e) => { e.preventDefault(); onContext(e); });
  }

  // ------------------------------------------------------------- painting
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function canvas(w, h, dpr) {
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w * dpr));
    c.height = Math.max(1, Math.round(h * dpr));
    const x = c.getContext('2d');
    x.scale(dpr, dpr);
    return [c, x];
  }
  function vgrad(x, y0, y1, w, stops) {
    const g = x.createLinearGradient(0, y0, 0, y1);
    for (const [t, c] of stops) g.addColorStop(t, rgba(c, 1));
    x.fillStyle = g;
    x.fillRect(0, y0, w, y1 - y0);
  }
  function glow(x, cx, cy, r, c, a) {
    const g = x.createRadialGradient(cx, cy, 0, cx, cy, r);
    for (const [t, k] of [[0, 1], [.25, .62], [.5, .28], [.75, .08], [1, 0]]) g.addColorStop(t, rgba(c, a * k));
    x.fillStyle = g;
    x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill();
  }
  // The app backdrop's own light: an ellipse fading out by `fadeAt`.
  function rigGlow(x, w, h, c, a, centre, radii, fadeAt) {
    const cx = w * centre[0], cy = h * centre[1];
    const rx = Math.max(1, w * radii[0]), ry = Math.max(1, h * radii[1]);
    x.save();
    x.translate(cx, cy); x.scale(rx, ry);
    const g = x.createRadialGradient(0, 0, 0, 0, 0, 1);
    for (let i = 0; i <= 8; i++) {
      const t = i / 8;
      const k = t < fadeAt ? (1 - (t / fadeAt) * (t / fadeAt) * (3 - 2 * (t / fadeAt))) : 0;
      g.addColorStop(t, rgba(c, a * k));
    }
    x.fillStyle = g;
    x.fillRect(-cx / rx, -cy / ry, w / rx, h / ry);
    x.restore();
  }
  function ridge(x, w, h, base, amp, rand, rough = 1, smooth = false) {
    const oct = smooth ? [[380, 1], [150, .35 * rough]] : [[220, 1], [90, .45 * rough], [34, .18 * rough]];
    const tables = oct.map(([span, wt]) => [Array.from({ length: Math.ceil(w / span) + 4 }, rand), span, wt]);
    const total = tables.reduce((s, t) => s + t[2], 0);
    const height = (px) => {
      let y = 0;
      for (const [tab, span, wt] of tables) {
        const f = px / span, i = Math.floor(f);
        let t = f - i; t = t * t * (3 - 2 * t);
        y += (tab[i] * (1 - t) + tab[i + 1] * t) * wt;
      }
      return y / total;
    };
    x.beginPath(); x.moveTo(0, h);
    for (let px = 0; px <= w + 6; px += 6) x.lineTo(px, base - amp * height(px));
    x.lineTo(w + 6, h); x.closePath(); x.fill();
  }
  function stars(x, w, h, rand, count, maxY, size = [.5, 1.3], alpha = [.3, .9]) {
    for (let i = 0; i < count; i++) {
      const px = rand() * w, py = Math.pow(rand(), 1.3) * maxY;
      const r = size[0] + rand() * (size[1] - size[0]);
      x.fillStyle = `rgba(255,255,255,${alpha[0] + rand() * (alpha[1] - alpha[0])})`;
      x.beginPath(); x.arc(px, py, r, 0, Math.PI * 2); x.fill();
    }
  }

  // Each painter returns [[canvas, depth], ...], far to near.
  const PAINTERS = {
    glow(w, h, d, pal) {
      const out = [];
      let [c, x] = canvas(w, h, d);
      x.fillStyle = rgba(pal.base, 1); x.fillRect(0, 0, w, h);
      for (const g of pal.glows) rigGlow(x, w, h, g[0], g[1], g[2], g[3], g[4]);
      // The website's grid, strongest along the top and fading out.
      const [gc, ga, step] = pal.grid;
      x.save();
      const mask = x.createRadialGradient(w / 2, 0, 0, w / 2, 0, Math.max(w, h) * .9);
      mask.addColorStop(0, rgba(gc, ga)); mask.addColorStop(.55, rgba(gc, ga * .45)); mask.addColorStop(1, rgba(gc, 0));
      x.fillStyle = mask;
      for (let px = (w / 2) % step; px < w; px += step) x.fillRect(Math.round(px), 0, 1, h);
      for (let py = 0; py < h; py += step) x.fillRect(0, Math.round(py), w, 1);
      x.restore();
      out.push([c, .12]);

      const rand = rng(7);
      [c, x] = canvas(w, h, d);
      const side = () => (rand() < .5 ? .03 + rand() * .27 : .70 + rand() * .27);
      for (let i = 0; i < 4; i++) {
        const g = pal.glows[(i + 1) % 3];
        glow(x, side() * w, (.12 + rand() * .76) * h, (.07 + rand() * .06) * Math.max(w, h), g[0], pal.dark ? .13 : .16);
      }
      out.push([c, .5]);

      [c, x] = canvas(w, h, d);
      const mote = pal.dark ? mix(pal.brand, [255, 255, 255], .55) : mix(pal.brand, [255, 255, 255], .2);
      const n = Math.round(w * h / 15000) + 12;
      for (let i = 0; i < n; i++) {
        const r = 1 + rand() * 1.6;
        glow(x, rand() * w, rand() * h, r * 3.2, mote, (pal.dark ? .22 : .28) + rand() * .28);
      }
      out.push([c, 1]);
      return out;
    },

    aurora(w, h, d) {
      const rand = rng(11);
      let [c, x] = canvas(w, h, d);
      vgrad(x, 0, h, w, [[0, [3, 6, 16]], [.55, [7, 18, 34]], [.85, [12, 36, 50]], [1, [8, 24, 34]]]);
      stars(x, w, h, rand, Math.round(w * h / 4200), h * .75);
      const out = [[c, .1]];
      [c, x] = canvas(w, h, d);
      x.filter = 'blur(26px)';
      for (const [col, a, y0, amp, thick, ph] of [
        [[52, 190, 140], .42, .36, .08, .2, 0], [[45, 180, 170], .3, .3, .06, .15, 1.7], [[150, 130, 230], .2, .24, .05, .12, 3.1]]) {
        const pts = [];
        for (let i = 0; i <= 48; i++) {
          const px = w * i / 48;
          pts.push([px, h * (y0 + amp * Math.sin(i / 48 * Math.PI * 2.2 + ph) + .025 * Math.sin(i / 48 * Math.PI * 7 + ph * 2))]);
        }
        const g = x.createLinearGradient(0, h * (y0 - amp), 0, h * (y0 + amp + thick));
        g.addColorStop(0, rgba(col, 0)); g.addColorStop(.45, rgba(col, a * .35)); g.addColorStop(.8, rgba(col, a)); g.addColorStop(1, rgba(col, 0));
        x.fillStyle = g;
        x.beginPath(); x.moveTo(pts[0][0], pts[0][1]);
        for (const p of pts) x.lineTo(p[0], p[1]);
        for (let i = pts.length - 1; i >= 0; i--) x.lineTo(pts[i][0], pts[i][1] + h * thick);
        x.closePath(); x.fill();
      }
      x.filter = 'none';
      out.push([c, .35]);
      [c, x] = canvas(w, h, d);
      x.fillStyle = 'rgb(11,22,32)'; ridge(x, w, h, h * .8, h * .2, rand, 1.1);
      out.push([c, .65]);
      [c, x] = canvas(w, h, d);
      x.fillStyle = 'rgb(4,9,14)'; ridge(x, w, h, h * .94, h * .16, rand, 1.3);
      out.push([c, 1]);
      return out;
    },

    peaks(w, h, d) {
      const rand = rng(23);
      let [c, x] = canvas(w, h, d);
      vgrad(x, 0, h, w, [[0, [26, 20, 52]], [.38, [92, 56, 104]], [.62, [184, 112, 120]], [.8, [222, 168, 136]], [1, [228, 186, 150]]]);
      glow(x, w * .62, h * .6, h * .42, [255, 210, 168], .45);
      x.fillStyle = 'rgba(255,234,204,.9)';
      x.beginPath(); x.arc(w * .62, h * .6, h * .045, 0, Math.PI * 2); x.fill();
      const out = [[c, .08]];
      for (const [col, base, amp, depth] of [[[150, 96, 128], .62, .22, .3], [[104, 58, 102], .72, .24, .5],
                                             [[60, 32, 72], .84, .22, .75], [[26, 14, 36], .97, .18, 1]]) {
        [c, x] = canvas(w, h, d);
        const g = x.createLinearGradient(0, h * (base - amp), 0, h);
        g.addColorStop(0, rgba(col, 1)); g.addColorStop(1, rgba(mix(col, [0, 0, 0], .3), 1));
        x.fillStyle = g; ridge(x, w, h, h * base, h * amp, rand, 1.2);
        out.push([c, depth]);
      }
      return out;
    },

    dunes(w, h, d) {
      const rand = rng(31);
      let [c, x] = canvas(w, h, d);
      vgrad(x, 0, h, w, [[0, [36, 26, 46]], [.35, [120, 70, 72]], [.62, [204, 130, 88]], [.82, [236, 190, 136]], [1, [240, 206, 156]]]);
      glow(x, w * .3, h * .62, h * .55, [255, 222, 170], .5);
      x.fillStyle = 'rgba(255,240,212,.92)';
      x.beginPath(); x.arc(w * .3, h * .62, h * .06, 0, Math.PI * 2); x.fill();
      const out = [[c, .08]];
      for (const [lit, shade, base, amp, depth] of [[[196, 136, 90], [158, 96, 62], .74, .12, .35],
                                                    [[170, 104, 64], [120, 66, 40], .85, .14, .65],
                                                    [[118, 64, 38], [72, 36, 22], .98, .16, 1]]) {
        [c, x] = canvas(w, h, d);
        const g = x.createLinearGradient(0, 0, w, 0);
        g.addColorStop(0, rgba(lit, 1)); g.addColorStop(1, rgba(shade, 1));
        x.fillStyle = g; ridge(x, w, h, h * base, h * amp, rand, .5, true);
        out.push([c, depth]);
      }
      return out;
    },

    ocean(w, h, d) {
      const rand = rng(41);
      const hz = .6;
      let [c, x] = canvas(w, h, d);
      vgrad(x, 0, h * hz, w, [[0, [4, 7, 20]], [.6, [14, 28, 60]], [1, [44, 70, 112]]]);
      stars(x, w, h, rand, Math.round(w * h / 9000), h * .45);
      glow(x, w * .7, h * .24, h * .3, [200, 220, 255], .3);
      x.fillStyle = 'rgb(236,240,252)';
      x.beginPath(); x.arc(w * .7, h * .24, h * .04, 0, Math.PI * 2); x.fill();
      const out = [[c, .1]];
      [c, x] = canvas(w, h, d);
      vgrad(x, h * hz, h, w, [[0, [22, 42, 78]], [.4, [9, 20, 40]], [1, [3, 8, 18]]]);
      for (let i = 0; i < 70; i++) {
        const t = i / 70, py = h * (hz + .01 + t * (1 - hz - .02));
        const spread = w * (.02 + .1 * t), px = w * .7 + (rand() * 2 - 1) * spread, len = w * (.008 + .03 * t);
        x.fillStyle = `rgba(220,232,255,${.45 * (1 - t * .6) * (.4 + rand() * .6)})`;
        x.fillRect(px - len / 2, py, len, Math.max(1, 1.6 * t + .6));
      }
      out.push([c, .45]);
      [c, x] = canvas(w, h, d);
      [[[9, 20, 40], .88, .035], [[4, 11, 24], .97, .04]].forEach(([col, base, amp], k) => {
        x.fillStyle = rgba(col, 1);
        x.beginPath(); x.moveTo(0, h);
        for (let i = 0; i <= 80; i++) x.lineTo(w * i / 80, h * base - h * amp * (.6 * Math.sin(i * .55 + k) + .4 * Math.sin(i * 1.3 + 2 * k)));
        x.lineTo(w, h); x.closePath(); x.fill();
      });
      out.push([c, 1]);
      return out;
    },

    nebula(w, h, d) {
      const rand = rng(53);
      let [c, x] = canvas(w, h, d);
      vgrad(x, 0, h, w, [[0, [5, 4, 14]], [1, [8, 5, 20]]]);
      x.filter = 'blur(30px)';
      for (const [col, cx, cy, r, a] of [[[190, 70, 210], .3, .38, .42, .34], [[59, 110, 220], .68, .3, .46, .32],
                                         [[45, 180, 170], .58, .72, .36, .22], [[220, 110, 170], .18, .8, .3, .2],
                                         [[120, 130, 230], .86, .66, .3, .24]]) {
        glow(x, w * cx, h * cy, Math.max(w, h) * r, col, a);
      }
      x.filter = 'none';
      const out = [[c, .12]];
      [c, x] = canvas(w, h, d);
      stars(x, w, h, rand, Math.round(w * h / 2600), h, [.4, .9], [.2, .65]);
      out.push([c, .45]);
      [c, x] = canvas(w, h, d);
      for (let i = 0; i < Math.round(w * h / 26000) + 6; i++) {
        const px = rand() * w, py = rand() * h, r = .9 + rand() * .8;
        glow(x, px, py, r * 5, [220, 230, 255], .3);
        x.fillStyle = 'rgba(255,255,255,.9)';
        x.beginPath(); x.arc(px, py, r, 0, Math.PI * 2); x.fill();
      }
      out.push([c, 1]);
      return out;
    },
  };
  const STILLS = ['glow', 'aurora', 'peaks', 'dunes', 'ocean', 'nebula'];
  const NAMES = { silk: 'Silk', borealis: 'Borealis', liquid: 'Liquid', glow: 'Glow', aurora: 'Aurora',
                  peaks: 'Peaks', dunes: 'Dunes', ocean: 'Ocean', nebula: 'Nebula' };

  // ------------------------------------------------------------ live scenes
  // Three wallpapers that move: one fragment shader each, on one canvas.
  // Kept light on purpose -- drawn at half the window's size (they are soft
  // fields; no detail is lost), at most 30 frames a second, on the low-power
  // GPU when there is a choice, and not at all while the page can't be seen
  // (the browser stops animation frames for a hidden page by itself) or
  // when "Animated wallpaper" or Reduce motion is off: then one still frame.
  const GLSL_HEAD = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes; uniform float uTime; uniform float uDark;
uniform vec3 uBase; uniform vec3 uA; uniform vec3 uB; uniform vec3 uC;
float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
// Quieter behind the name and the search, where the text sits.
float hush(vec2 q, float k) { return mix(k, 1.0, smoothstep(0.05, 0.62, length((q - vec2(0.5, 0.56)) * vec2(1.25, 1.9)))); }
`;
  const SHADERS = {
    // Satin, folding slowly in a light from the upper left.
    silk: `
float field(vec2 p, float t) {
  p += 0.45 * vec2(sin(p.y * 1.2 + t * 0.7), sin(p.x * 0.9 - t * 0.5));
  p += 0.22 * vec2(sin(p.y * 2.6 - t * 0.45 + 2.0), sin(p.x * 2.1 + t * 0.35 + 1.0));
  return 0.65 * sin(p.x * 1.7 + p.y * 1.0 + t * 0.6) + 0.35 * sin(p.x * 0.8 - p.y * 2.1 - t * 0.4);
}
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y * 2.4;
  float t = uTime * 0.32;
  float e = 0.02;
  float c = field(p, t);
  vec2 g = vec2(field(p + vec2(e, 0.0), t) - c, field(p + vec2(0.0, e), t) - c) / e;
  vec3 n = normalize(vec3(-g * 0.9, 1.0));
  vec3 L = normalize(vec3(-0.45, 0.55, 0.75));
  float diff = clamp(dot(n, L), 0.0, 1.0);
  float spec = pow(clamp(dot(n, normalize(L + vec3(0.0, 0.0, 1.0))), 0.0, 1.0), 22.0);
  float fold = c * 0.5 + 0.5;
  vec3 col;
  if (uDark > 0.5) {
    col = uBase * (0.5 + 1.25 * diff * diff);
    col += mix(uB, uA, 0.5) * 0.24 * pow(diff, 4.0);
    col = mix(col, col + uB * 0.12, fold);
    col += mix(uA, vec3(1.0), 0.35) * spec * 0.42;
    col *= hush(gl_FragCoord.xy / uRes, 0.62);
  } else {
    col = mix(uBase, vec3(1.0), 0.25) * (0.8 + 0.26 * diff);
    col = mix(col, mix(uB, vec3(1.0), 0.5), 0.2 * fold);
    col += vec3(1.0) * spec * 0.45;
  }
  vec2 q = gl_FragCoord.xy / uRes;
  float vig = smoothstep(1.25, 0.25, length((q - 0.5) * vec2(1.1, 1.3)));
  col *= mix(uDark > 0.5 ? 0.6 : 0.9, 1.0, vig);
  gl_FragColor = vec4(col, 1.0);
}`,
    // The northern lights: curtains of light drifting over dark hills.
    borealis: `
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float t = uTime * 0.05;
  vec3 col = mix(vec3(0.012, 0.02, 0.05), vec3(0.035, 0.08, 0.13), pow(1.0 - uv.y, 1.6));
  vec2 sp = gl_FragCoord.xy / 5.0;
  float h = hash(floor(sp));
  if (h > 0.982) {
    vec2 f = fract(sp) - 0.5;
    float tw = 0.55 + 0.45 * sin(uTime * 1.3 + h * 60.0);
    col += vec3(0.8, 0.88, 1.0) * smoothstep(0.16, 0.0, length(f)) * tw * smoothstep(0.3, 0.7, uv.y) * 0.8;
  }
  float ax = uv.x * uRes.x / uRes.y;
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float x = ax * (1.1 + fi * 0.35) + fi * 2.7;
    float line = 0.56 + fi * 0.07 + 0.10 * sin(x * 1.3 + t * 6.0 + fi) + 0.05 * sin(x * 3.1 - t * 4.0)
               + 0.05 * (noise(vec2(x * 2.0, t * 3.0 + fi)) - 0.5);
    float d = uv.y - line;
    float curtain = smoothstep(-0.02, 0.025, d) * exp(-max(d, 0.0) * (4.5 + fi * 2.0));
    float rays = 0.5 + 0.5 * noise(vec2(x * 26.0, t * 7.0 + fi * 7.0));
    vec3 c = i == 0 ? vec3(0.16, 0.95, 0.62) : (i == 1 ? vec3(0.18, 0.72, 0.95) : vec3(0.55, 0.38, 0.98));
    col += c * curtain * rays * (0.4 - fi * 0.08);
  }
  float ridge = 0.13 + 0.05 * sin(ax * 2.1) + 0.03 * sin(ax * 5.3 + 1.0) + 0.02 * noise(vec2(ax * 12.0, 1.0));
  float under = smoothstep(ridge + 0.004, ridge - 0.004, uv.y);
  col = mix(col, vec3(0.008, 0.016, 0.03) + col * 0.08, under);
  gl_FragColor = vec4(col, 1.0);
}`,
    // Light in the palette's colours, flowing slowly like ink in water.
    liquid: `
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uTime * 0.11;
  vec2 w = p + 0.08 * vec2(noise(p * 2.2 + t), noise(p * 2.2 - t + 4.0));
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    vec2 c = vec2(0.78 * sin(t * (0.9 + fi * 0.13) + fi * 1.7), 0.42 * cos(t * (0.7 + fi * 0.11) + fi * 2.3));
    float r = 0.36 + 0.1 * sin(t * 0.8 + fi);
    vec3 k = i == 0 ? uA : (i == 1 ? uB : (i == 2 ? uC : (i == 3 ? mix(uA, uB, 0.5) : mix(uB, uC, 0.5))));
    float d = length(w - c);
    acc += k * exp(-d * d / (r * r));
  }
  vec3 col;
  if (uDark > 0.5) {
    col = uBase * 0.85 + acc * 0.4;
    col = 1.0 - exp(-col * 1.35);
    vec2 q = gl_FragCoord.xy / uRes;
    col *= mix(0.62, 1.0, smoothstep(1.2, 0.3, length((q - 0.5) * vec2(1.0, 1.25))));
    col *= hush(q, 0.72);
  } else {
    col = mix(uBase, vec3(1.0), 0.45);
    col = mix(col, mix(acc, vec3(1.0), 0.35), 0.42 * clamp(length(acc), 0.0, 1.0));
  }
  col += (hash(gl_FragCoord.xy + fract(uTime)) - 0.5) * 0.016;
  gl_FragColor = vec4(col, 1.0);
}`,
  };
  const LIVE = Object.keys(SHADERS);

  function liveRenderer(name, cw, ch) {
    const c = document.createElement('canvas');
    c.width = Math.max(2, cw); c.height = Math.max(2, ch);
    const gl = c.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false,
                                        preserveDrawingBuffer: true, powerPreference: 'low-power' });
    if (!gl) return null;
    const sh = (type, src) => {
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { console.warn(gl.getShaderInfoLog(s)); return null; }
      return s;
    };
    const vs = sh(gl.VERTEX_SHADER, 'attribute vec2 a; void main() { gl_Position = vec4(a, 0.0, 1.0); }');
    const fs = sh(gl.FRAGMENT_SHADER, GLSL_HEAD + SHADERS[name]);
    if (!vs || !fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'a');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const u = (n) => gl.getUniformLocation(prog, n);
    const U = { res: u('uRes'), time: u('uTime'), dark: u('uDark'), base: u('uBase'), a: u('uA'), b: u('uB'), c: u('uC') };
    return {
      canvas: c,
      colors(rig) {
        const n = (v) => v.map(x => x / 255);
        const g = rig.glows || [];
        gl.uniform1f(U.dark, rig.dark ? 1 : 0);
        gl.uniform3fv(U.base, n(rig.base));
        gl.uniform3fv(U.a, n(rig.brand));
        gl.uniform3fv(U.b, n((g[1] || g[0] || [rig.brand])[0]));
        gl.uniform3fv(U.c, n((g[0] || [rig.brand])[0]));
      },
      draw(t) {
        gl.viewport(0, 0, c.width, c.height);
        gl.uniform2f(U.res, c.width, c.height);
        gl.uniform1f(U.time, t % 3600);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      },
      dispose() { const ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext(); },
    };
  }

  // One live wallpaper at a time.
  let live = null, liveRaf = 0, liveLast = 0, liveClock = 8;   // seconds of motion so far
  let windowActive = true;     // the app tells us: only the window in use moves
  function animateOn() { return !!(state && state.options.animate) && !still && windowActive; }
  function stopLive() {
    if (liveRaf) cancelAnimationFrame(liveRaf);
    liveRaf = 0;
    // Its canvas stays up through the cross-fade; the GPU context goes after.
    if (live) { const old = live; live = null; setTimeout(() => old.dispose(), 1300); }
  }
  function liveTick(now) {
    liveRaf = 0;
    if (!live) return;
    if (!animateOn()) { live.draw(liveClock); return; }
    liveRaf = requestAnimationFrame(liveTick);
    if (now - liveLast < 32) return;          // at most ~30 frames a second
    liveClock += Math.min(0.1, (now - liveLast) / 1000);
    liveLast = now;
    live.draw(liveClock);
  }
  function startLive() {
    if (!live || liveRaf) return;
    liveLast = performance.now();
    live.draw(liveClock);
    if (animateOn()) liveRaf = requestAnimationFrame(liveTick);
  }

  const SCENES = [...LIVE, ...STILLS];
  // Scenes that keep the page's own light or dark colours; every other one
  // (and a picture) is a dark scene the page's text sits on in white.
  const THEMED = ['silk', 'liquid', 'glow'];

  // ------------------------------------------------------------ wallpaper
  function wallpaperName() {
    const w = state && state.wallpaper;
    if (typeof w === 'string' && w.startsWith('pic:') && state.custom) return 'custom';
    return SCENES.includes(w) ? w : 'silk';
  }

  // The page sits under the app's title bar and the browser's bars. The
  // wallpaper is laid out as if it filled the whole window, top to bottom:
  // the page shows its lower part, and the app paints the part above --
  // the strip the page hands it (postStrip) -- frosted, behind the bars.
  function chromeTop() { return Math.max(0, Math.round((state && state.chromeTop) || 0)); }

  function placeBox(node) {
    const top = chromeTop();
    node.style.top = -(top + SLACK) + 'px';
    node.style.height = (innerHeight + top + 2 * SLACK) + 'px';
  }

  function postStrip() {
    const top = chromeTop();
    if (!top || !layers.length) return;
    const scale = 1 / 6;                    // blurred anyway: small is enough
    const sw = Math.max(32, Math.round(innerWidth * scale)), sh = Math.max(4, Math.round(top * scale));
    const c = document.createElement('canvas');
    c.width = sw; c.height = sh;
    const x = c.getContext('2d');
    x.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#000';
    x.fillRect(0, 0, sw, sh);
    x.filter = (state && state.private) ? 'blur(2px) grayscale(1)' : 'blur(2px)';
    const boxW = innerWidth + 2 * SLACK, boxH = innerHeight + top + 2 * SLACK;
    for (const l of layers) {
      const node = l.el;
      if (node instanceof HTMLCanvasElement) {
        const k = node.width / boxW;      // (a live scene's canvas is drawn at its own scale)
        x.drawImage(node, SLACK * k, SLACK * k, innerWidth * k, top * k, 0, 0, sw, sh);
      } else if (node instanceof HTMLImageElement && node.naturalWidth) {
        const f = Math.max(boxW / node.naturalWidth, boxH / node.naturalHeight);
        const ox = (boxW - node.naturalWidth * f) / 2, oy = (boxH - node.naturalHeight * f) / 2;
        x.drawImage(node, (SLACK - ox) / f, (SLACK - oy) / f, innerWidth / f, top / f, 0, 0, sw, sh);
      }
    }
    post({ type: 'strip', url: c.toDataURL('image/jpeg', 0.86), top });
  }

  function paintWall(force) {
    if (!state) return;
    const name = wallpaperName();
    const w = innerWidth + 2 * SLACK, h = innerHeight + chromeTop() + 2 * SLACK;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const key = [name, w, h, dpr, JSON.stringify(state.rig), state.custom || '', !!state.private].join('|');
    if (!force && key === wallKey) return;
    wallKey = key;
    const box = $('#wall');
    const old = [...box.children];
    stopLive();
    let made;
    if (LIVE.includes(name)) {
      const k = 0.5;                       // half size: a soft field loses nothing
      live = liveRenderer(name, Math.round(w * k), Math.round(h * k));
      if (live) {
        live.colors(state.rig);
        made = [[live.canvas, .3]];
      } else {
        made = PAINTERS.glow(w, h, dpr, state.rig);      // no WebGL here: a still one
      }
    } else if (name === 'custom') {
      const img = new Image();
      img.decoding = 'async';
      img.addEventListener('load', postStrip);
      img.src = state.custom;
      made = [[img, .55]];
    } else {
      made = PAINTERS[name](w, h, dpr, state.rig);
    }
    layers = made.map(([node, depth]) => { placeBox(node); box.appendChild(node); return { el: node, depth }; });
    if (live) live.draw(liveClock);
    if (name !== 'custom') postStrip();
    startLive();
    body.classList.toggle('scene', !THEMED.includes(name));
    applyVars();
    // Cross-fade: the new layers fade in over the old ones, then the old go.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      for (const l of layers) l.el.classList.add('on');
      setTimeout(() => old.forEach(n => n.remove()), still ? 0 : 1150);
    }));
    placeLayers(true);
  }

  // --------------------------------------------------------------- parallax
  let tx = 0, ty = 0, cx = 0, cy = 0, last = 0, running = false;
  function parallaxOn() { return !!(state && state.options.parallax) && !still; }

  addEventListener('pointermove', (e) => {
    tx = (e.clientX / innerWidth - .5) * 2;
    ty = (e.clientY / innerHeight - .5) * 2;
    kick();
  }, { passive: true });
  document.documentElement.addEventListener('pointerleave', () => { tx = 0; ty = 0; kick(); });

  function placeLayers(reset) {
    if (reset && !parallaxOn()) { cx = cy = 0; }
    for (const l of layers) {
      const dx = -cx * SLACK * l.depth, dy = -cy * SLACK * .6 * l.depth;
      l.el.style.transform = `translate3d(${dx.toFixed(2)}px, ${dy.toFixed(2)}px, 0)`;
    }
  }
  function kick() {
    if (running || !parallaxOn()) return;
    running = true;
    last = performance.now();
    requestAnimationFrame(frame);
  }
  function frame(now) {
    // Eased toward the pointer at the same pace whatever the frame rate.
    const dt = Math.min(64, now - last); last = now;
    const k = 1 - Math.pow(1 - .085, dt / 16.67);
    cx += (tx - cx) * k; cy += (ty - cy) * k;
    placeLayers(false);
    if (Math.abs(tx - cx) > .0005 || Math.abs(ty - cy) > .0005) {
      requestAnimationFrame(frame);
    } else {
      running = false;
    }
  }

  // ------------------------------------------------------------- theme vars
  function applyVars() {
    if (!state) return;
    const vars = body.classList.contains('scene') ? state.sceneVars : state.vars;
    const root = document.documentElement.style;
    for (const [k, v] of Object.entries(vars)) root.setProperty(k, v);
  }

  // ------------------------------------------------------------------ kicker
  function setKicker() {
    if (state && state.private) { $('#kicker').textContent = 'Private tab'; return; }
    const d = new Date(), hr = d.getHours();
    const part = hr >= 5 && hr < 12 ? 'Good morning' : hr >= 12 && hr < 17 ? 'Good afternoon' : 'Good evening';
    const date = d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
    $('#kicker').textContent = `${part} · ${date}`;
  }
  setInterval(setKicker, 60 * 1000);

  // ------------------------------------------------------------------ tiles
  const TINTS = () => [getComputedStyle(document.documentElement).getPropertyValue('--brand').trim(),
                       getComputedStyle(document.documentElement).getPropertyValue('--brand-2').trim()];
  function iconFor(host) { return (state && state.icons && state.icons[host]) || null; }

  function renderTiles() {
    const box = $('#tiles');
    box.textContent = '';
    if (!state) return;
    const tints = TINTS();
    state.tiles.forEach((s, i) => {
      const t = el('div', 'tile' + (s.kind === 'suggested' ? ' suggested' : ''));
      t.tabIndex = 0;
      t.title = s.kind === 'suggested' ? `${s.title}\nSuggested: you visit it often` : s.title;
      const ico = el('span', 'ico');
      const src = iconFor(s.host);
      if (src) {
        const img = new Image(); img.src = src; img.alt = ''; ico.appendChild(img);
      } else {
        ico.classList.add('letter');
        ico.style.setProperty('--tint', tints[i % 2]);
        ico.textContent = (s.title || s.host || '?').trim().charAt(0).toUpperCase();
      }
      t.appendChild(ico);
      t.appendChild(el('span', 'name', s.title));
      if (s.kind === 'shortcut') {
        const x = el('button', 'x'); x.type = 'button'; x.title = 'Remove'; x.appendChild(svg(ICON_X));
        x.addEventListener('click', (e) => { e.stopPropagation(); post({ type: 'remove', url: s.url }); });
        x.addEventListener('auxclick', (e) => e.stopPropagation());
        t.appendChild(x);
      }
      linkify(t, s.url, (e) => tileMenu(e, s));
      box.appendChild(t);
    });
    if (!state.private) {
      const add = el('div', 'tile add'); add.tabIndex = 0; add.title = 'Add a shortcut';
      const ico = el('span', 'ico'); ico.appendChild(svg(ICON_PLUS)); add.appendChild(ico);
      add.appendChild(el('span', 'name', 'Add shortcut'));
      add.addEventListener('click', openDialog);
      add.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDialog(); });
      box.appendChild(add);
    }
  }

  function tileMenu(e, s) {
    const items = [
      ['Open', () => go(s.url, 'current')],
      ['Open in new tab', () => go(s.url, 'tab')],
      ['Open in background tab', () => go(s.url, 'background')],
      null,
    ];
    if (s.kind === 'shortcut') items.push(['Remove shortcut', () => post({ type: 'remove', url: s.url })]);
    else items.push(['Add to shortcuts', () => post({ type: 'pin', url: s.url, title: s.title })],
                    ["Don't suggest this site", () => post({ type: 'hide', url: s.url })]);
    openMenu(e.clientX, e.clientY, items);
  }

  // ----------------------------------------------------------- recent pages
  function renderRecent() {
    const box = $('#recent'), list = $('#recent-list');
    list.textContent = '';
    const items = (state && !state.private && state.options.recent) ? state.recent : [];
    box.hidden = !items.length;
    body.classList.toggle('has-recent', !!items.length);
    if (!items.length) return;
    list.style.setProperty('--n', Math.max(2, items.length));
    const tints = TINTS();
    items.forEach((r, i) => {
      const card = el('div', 'rc'); card.tabIndex = 0; card.title = `${r.title}\n${r.url}`;
      const sq = el('span', 'sq'); sq.style.setProperty('--tint', tints[i % 2]);
      const src = iconFor(r.host);
      if (src) { const img = new Image(); img.src = src; img.alt = ''; sq.appendChild(img); } else sq.appendChild(svg(ICON_GLOBE));
      const st = el('span', 'st');
      st.appendChild(el('span', 't', r.title));
      st.appendChild(el('span', 'h', r.host));
      card.appendChild(sq); card.appendChild(st);
      linkify(card, r.url, (e) => openMenu(e.clientX, e.clientY, [
        ['Open', () => go(r.url, 'current')], ['Open in new tab', () => go(r.url, 'tab')],
        ['Open in background tab', () => go(r.url, 'background')]]));
      list.appendChild(card);
    });
  }

  // ----------------------------------------------------------------- search
  const q = $('#q'), sug = $('#suggest');
  let picks = [], at = -1;

  function score(item, words) {
    const title = (item.title || '').toLowerCase(), url = item.url.toLowerCase(), host = hostOf(item.url);
    let s = 0;
    for (const w of words) {
      if (host.startsWith(w)) s += 6;
      else if (host.includes(w)) s += 4;
      else if (title.includes(w)) s += 3;
      else if (url.includes(w)) s += 1;
      else return 0;
    }
    return s + (item.bookmark ? 1.5 : 0);
  }
  function mark(text, words) {
    const frag = document.createDocumentFragment();
    const lower = text.toLowerCase();
    let i = 0;
    while (i < text.length) {
      let hit = null;
      for (const w of words) if (w && lower.startsWith(w, i)) { hit = w; break; }
      if (hit) { frag.appendChild(el('mark', null, text.substr(i, hit.length))); i += hit.length; }
      else { frag.appendChild(document.createTextNode(text[i])); i++; }
    }
    return frag;
  }
  function renderSuggest() {
    const text = q.value.trim();
    sug.textContent = '';
    picks = []; at = -1;
    if (!text || !state) { sug.hidden = true; return; }
    const words = text.toLowerCase().split(/\s+/).filter(Boolean);
    const found = state.suggest.map(it => [score(it, words), it]).filter(p => p[0] > 0)
      .sort((a, b) => b[0] - a[0]).slice(0, 6).map(p => p[1]);
    picks.push({ text, search: true });
    for (const it of found) picks.push({ text: it.url, item: it });
    picks.forEach((p, i) => {
      const row = el('div', 'sg'); row.setAttribute('role', 'option');
      const si = el('span', 'si');
      if (p.search) si.appendChild(svg(ICON_SEARCH));
      else {
        const src = iconFor(hostOf(p.item.url));
        if (src) { const img = new Image(); img.src = src; img.alt = ''; si.appendChild(img); } else si.appendChild(svg(ICON_GLOBE));
      }
      const st = el('span', 'st');
      if (p.search) {
        st.appendChild(el('span', 't', text));
        st.appendChild(el('span', 'u', `Search ${state.engine}`));
      } else {
        const t = el('span', 't'); t.appendChild(mark(p.item.title || p.item.url, words));
        const u = el('span', 'u'); u.appendChild(mark(p.item.url.replace(/^https?:\/\/(www\.)?/, ''), words));
        st.appendChild(t); st.appendChild(u);
      }
      row.appendChild(si); row.appendChild(st);
      row.addEventListener('mousedown', (e) => e.preventDefault());
      row.addEventListener('mouseenter', () => select(i));
      row.addEventListener('click', (e) => { closeSuggest(); go(p.text, where(e)); });
      row.addEventListener('auxclick', (e) => { if (e.button === 1) { e.preventDefault(); go(p.text, 'background'); } });
      sug.appendChild(row);
    });
    sug.hidden = picks.length < 2;
    select(picks.length > 1 && found.length && score(found[0], words) >= 6 ? 1 : 0);
  }
  function select(i) {
    at = i;
    [...sug.children].forEach((r, k) => r.classList.toggle('on', k === i));
  }
  function closeSuggest() { sug.hidden = true; picks = []; at = -1; }

  q.addEventListener('input', renderSuggest);
  q.addEventListener('blur', () => setTimeout(closeSuggest, 120));
  q.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && !sug.hidden) { e.preventDefault(); select((at + 1) % picks.length); }
    else if (e.key === 'ArrowUp' && !sug.hidden) { e.preventDefault(); select((at - 1 + picks.length) % picks.length); }
    else if (e.key === 'Escape') { if (!sug.hidden) { closeSuggest(); e.preventDefault(); } else q.value = ''; }
  });
  $('#search').addEventListener('submit', (e) => {
    e.preventDefault();
    const pick = (!sug.hidden && at >= 0) ? picks[at] : null;
    const text = pick ? pick.text : q.value.trim();
    closeSuggest();
    go(text, e.submitter && e.submitter.id === 'go' ? 'current' : 'current');
  });
  q.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.altKey || e.ctrlKey)) {
      e.preventDefault();
      const pick = (!sug.hidden && at >= 0) ? picks[at] : null;
      go(pick ? pick.text : q.value.trim(), e.ctrlKey ? 'background' : 'tab');
      closeSuggest();
    }
  });

  // ------------------------------------------------------------------- menu
  const menu = $('#menu');
  function openMenu(x, y, items) {
    menu.textContent = '';
    for (const it of items) {
      if (!it) { menu.appendChild(el('div', 'mi sep')); continue; }
      const b = el('button', 'mi', it[0]); b.type = 'button';
      b.addEventListener('click', () => { closeMenu(); it[1](); });
      menu.appendChild(b);
    }
    menu.hidden = false;
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
    menu.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  }
  function closeMenu() { menu.hidden = true; }
  addEventListener('mousedown', (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); }, true);
  addEventListener('blur', closeMenu);
  addEventListener('contextmenu', (e) => { if (!e.target.closest('input')) e.preventDefault(); });

  // ----------------------------------------------------------------- dialog
  const wrap = $('#dialog-wrap');
  function openDialog() {
    $('#d-name').value = ''; $('#d-url').value = '';
    wrap.hidden = false;
    setTimeout(() => $('#d-url').focus(), 30);
  }
  function closeDialog() { wrap.hidden = true; }
  $('#d-cancel').addEventListener('click', closeDialog);
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) closeDialog(); });
  $('#dialog').addEventListener('submit', (e) => {
    e.preventDefault();
    const url = $('#d-url').value.trim();
    if (!url) { $('#d-url').focus(); return; }
    post({ type: 'add', url, title: $('#d-name').value.trim() });
    closeDialog();
  });

  // ------------------------------------------------------------ the corner
  const REPO = 'https://github.com/shriguruphalle-cloud/awesome-downloader';
  const SITE = 'https://awesome-downloader.pages.dev';
  linkify($('#oss'), REPO);
  linkify($('#site'), SITE);

  // --------------------------------------------------------- search engines
  const engineBtn = $('#engine'), enginesBox = $('#engines');
  const TICK = '<svg class="tick" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.2 4.2L19 7"/></svg>';
  function engineMark(label, host) {
    const src = iconFor(host);
    if (!src) return el('span', 'mark', label.charAt(0));
    const m = el('span', 'mark logo');
    const img = new Image(); img.src = src; img.alt = '';
    m.appendChild(img);
    return m;
  }
  function renderEngineChip() {
    const cur = ((state && state.engines) || []).find(e => e[0] === state.engineKey);
    const logo = $('#engine-logo');
    const src = cur ? iconFor(cur[2]) : null;
    logo.textContent = '';
    logo.hidden = !src;
    engineBtn.classList.toggle('has-logo', !!src);
    if (src) { const img = new Image(); img.src = src; img.alt = ''; logo.appendChild(img); }
  }
  function renderEngines() {
    enginesBox.textContent = '';
    renderEngineChip();
    for (const [key, label, host] of (state && state.engines) || []) {
      const b = el('button', 'eng' + (key === state.engineKey ? ' on' : ''));
      b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(engineMark(label, host));
      b.appendChild(el('span', null, label));
      b.appendChild(svg(TICK));
      b.addEventListener('mousedown', (e) => e.preventDefault());
      b.addEventListener('click', () => {
        closeEngines();
        if (key !== state.engineKey) post({ type: 'set-engine', key });
        q.focus();
      });
      enginesBox.appendChild(b);
    }
  }
  function closeEngines() { enginesBox.hidden = true; engineBtn.classList.remove('open'); }
  engineBtn.addEventListener('mousedown', (e) => e.preventDefault());
  engineBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!enginesBox.hidden) { closeEngines(); return; }
    closeSuggest();
    renderEngines();
    enginesBox.hidden = false;
    engineBtn.classList.add('open');
  });
  addEventListener('mousedown', (e) => {
    if (!enginesBox.hidden && !enginesBox.contains(e.target) && !engineBtn.contains(e.target)) closeEngines();
  }, true);

  // -------------------------------------------------------------- customize
  const sheet = $('#sheet');
  function liveThumb(name, tw, tht, dpr) {
    const shot = canvas(tw, tht, dpr)[0];
    const r = liveRenderer(name, Math.round(tw * dpr), Math.round(tht * dpr));
    if (!r) return shot;
    r.colors(state.rig);
    r.draw(18);
    shot.getContext('2d').drawImage(r.canvas, 0, 0, tw, tht);
    r.dispose();
    return shot;
  }
  function renderSheet() {
    const box = $('#walls');
    box.textContent = '';
    const cur = state.wallpaper;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const tw = 106, tht = 62;
    for (const name of SCENES) {
      const wp = el('div', 'wp' + (name === wallpaperName() && cur === name ? ' on' : ''));
      const th = el('div', 'th');
      if (LIVE.includes(name)) {
        th.appendChild(liveThumb(name, tw, tht, dpr));
        th.appendChild(el('span', 'live', 'Live'));
      } else {
        const shot = canvas(tw, tht, dpr)[0];
        const sx = shot.getContext('2d');
        for (const [c] of PAINTERS[name](tw, tht, dpr, state.rig)) sx.drawImage(c, 0, 0, tw * dpr, tht * dpr, 0, 0, tw, tht);
        th.appendChild(shot);
      }
      wp.appendChild(th); wp.appendChild(el('span', 'nm', NAMES[name]));
      wp.addEventListener('click', () => post({ type: 'set', key: 'home_wallpaper', value: name }));
      box.appendChild(wp);
    }
    // Your pictures: as many as you like, each with its own remove.
    const pics = $('#pics');
    pics.textContent = '';
    for (const pic of state.pictures || []) {
      const value = 'pic:' + pic.id;
      const wp = el('div', 'wp pic' + (cur === value ? ' on' : ''));
      const th = el('div', 'th');
      const img = new Image(); img.src = pic.url; img.alt = ''; img.loading = 'lazy';
      th.appendChild(img);
      const del = el('button', 'del'); del.type = 'button'; del.title = 'Remove this picture';
      del.appendChild(svg(ICON_X));
      del.addEventListener('click', (e) => { e.stopPropagation(); post({ type: 'remove-picture', id: pic.id }); });
      th.appendChild(del);
      wp.appendChild(th);
      wp.addEventListener('click', () => post({ type: 'set', key: 'home_wallpaper', value }));
      pics.appendChild(wp);
    }
    const add = el('div', 'wp add');
    const th = el('div', 'th');
    const plus = el('div', 'plus'); plus.appendChild(svg(ICON_PLUS)); th.appendChild(plus);
    add.appendChild(th); add.appendChild(el('span', 'nm', 'Add pictures'));
    add.addEventListener('click', () => post({ type: 'pick-picture' }));
    pics.appendChild(add);
    const opt = state.options;
    $('#opt-animate').checked = !!opt.animate;
    $('#opt-animate').closest('label').classList.toggle('off', !!opt.reduceMotion);
    $('#opt-parallax').checked = !!opt.parallax;
    $('#opt-parallax').closest('label').classList.toggle('off', !!opt.reduceMotion);
    $('#opt-suggestions').checked = !!opt.suggestions;
    $('#opt-recent').checked = !!opt.recent;
  }
  for (const [id, key] of [['opt-animate', 'home_animate'], ['opt-parallax', 'home_parallax'],
                           ['opt-suggestions', 'home_suggestions'], ['opt-recent', 'home_recent']]) {
    $('#' + id).addEventListener('change', (e) => post({ type: 'set', key, value: e.target.checked }));
  }
  $('#customize').addEventListener('click', () => {
    if (!sheet.hidden) { sheet.hidden = true; return; }
    renderSheet();
    sheet.hidden = false;
  });
  $('#sheet-close').addEventListener('click', () => { sheet.hidden = true; });
  addEventListener('mousedown', (e) => {
    if (!sheet.hidden && !sheet.contains(e.target) && !e.target.closest('#customize')) sheet.hidden = true;
  }, true);
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { sheet.hidden = true; closeMenu(); closeDialog(); closeEngines(); }
  });

  // ------------------------------------------------------------------ state
  function render() {
    still = !!state.options.reduceMotion || matchMedia('(prefers-reduced-motion: reduce)').matches;
    body.classList.toggle('still', still);
    body.classList.toggle('private', !!state.private);
    body.classList.toggle('dark', !!state.dark);
    $('#engine-name').textContent = state.engine;
    renderEngines();
    q.placeholder = `Search with ${state.engine}, or type a web address`;
    $('#private-note').hidden = !state.private;
    applyVars();
    setKicker();
    renderTiles();
    renderRecent();
    paintWall(false);
    if (live) { live.colors(state.rig); if (animateOn()) startLive(); else { if (liveRaf) cancelAnimationFrame(liveRaf); liveRaf = 0; live.draw(liveClock); } }
    if (!sheet.hidden) renderSheet();
    if (!parallaxOn()) { cx = cy = 0; placeLayers(false); }
  }

  window.chrome && window.chrome.webview && window.chrome.webview.addEventListener('message', (e) => {
    const msg = e.data || {};
    if (msg.type === 'state') {
      const first = !state;
      state = msg;
      render();
      if (first) requestAnimationFrame(() => body.classList.add('ready'));
    } else if (msg.type === 'icons' && state) {
      Object.assign(state.icons, msg.icons || {});
      renderTiles();
      renderRecent();
      renderEngines();
    } else if (msg.type === 'chrome' && state) {
      // The bars above the page changed height (the bookmarks bar, say).
      state.chromeTop = msg.top;
      paintWall(true);
    } else if (msg.type === 'active') {
      windowActive = !!msg.on;
      if (live) { if (animateOn()) startLive(); else if (liveRaf) { cancelAnimationFrame(liveRaf); liveRaf = 0; } }
    } else if (msg.type === 'replay' && !still) {
      // Shown again (a new tab): the column rises in once more.
      body.classList.remove('ready');
      void body.offsetWidth;
      body.classList.add('ready');
    }
  });

  let resizeTimer = 0;
  addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => paintWall(false), 140);
  });

  // For the app's own tests.
  window.__home = {
    state: () => state,
    layers: () => layers.map(l => ({ depth: l.depth, transform: l.el.style.transform })),
    live: () => ({ name: live ? wallpaperName() : null, running: !!liveRaf, clock: liveClock }),
    click: (i, button = 0) => {
      const t = document.querySelectorAll('#tiles .tile')[i];
      if (!t) return false;
      t.dispatchEvent(new MouseEvent(button === 1 ? 'auxclick' : 'click', { bubbles: true, button }));
      return true;
    },
  };

  post({ type: 'ready' });
})();
