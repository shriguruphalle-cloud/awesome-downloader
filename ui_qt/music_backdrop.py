"""The full-screen player's living background: a fluid animation in the
playing cover's colours, drawn by the graphics card, that breathes with the
music's beat. Two looks:

  "aurora" -- liquid aurora: soft bands of the cover's colours folding
              slowly over each other (the default)
  "nebula" -- electric nebula: broad clouds with glowing filaments along
              their folds, a fringe of colour, stars
  "still"  -- no animation: the cover, softened, behind the glass
              (drawn by the player itself, not here)

And four mirrors -- a kaleidoscope that keeps opening out of the middle,
symmetric all round, rushing outward on the beat. Each is built in three
sizes: the large shapes move with the kick, the middle ones with the
snare and the voice, the fine detail with the hi-hats.

  "glass"  -- crystal: facets of stained glass in the cover's colours,
              their edges catching the light
  "galaxy" -- a spiral of nebula clouds with stars streaming outward
  "neon"   -- a mandala of thin glowing lines on the dark
  "silk"   -- folds of liquid silk with a mirror sheen

Each frame is drawn small (the glass cards over it soften it anyway) by a
fragment shader into an off-screen buffer, then scaled up smoothly behind
the player. Where OpenGL isn't available, ok is False and the player keeps
its drifting cover.

The beat: audio_engine measures the bass and the loudness of the sound as
it's played (Engine.levels); BeatFollower turns that into a 0..1 pulse that
jumps on a kick and eases off -- subtle, so it's felt more than seen.
"""
import math
import time

from PySide6.QtGui import (QColor, QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector2D, QVector3D,
                           QVector4D)

from app.logging_setup import get_logger

logger = get_logger("music_backdrop")

MODES = (("aurora", "Aurora"), ("nebula", "Nebula"), ("glass", "Crystal"), ("neon", "Neon"), ("storm", "Rain"),
         ("image", "Your image"), ("still", "Still"))
THEMES = ()                # scenes made from the reference images go here once they're approved
# Beta: new backgrounds, to try before they join the list above
BETA = (("synthesis", "Synthesis"), ("storm_story", "Storm story"))


STORY_S = 90.0             # Storm story: one whole storm, start to clear sky, in a minute and a half


def story(progress):
    """The storm over a song: (rain density, clearing, lightning allowed, pace) for how far through
    (0..1). Drizzle, building, the storm, then it passes and the light comes back."""
    p = max(0.0, min(1.0, progress))
    ease = lambda a, b, x: a + (b - a) * (x * x * (3 - 2 * x))
    if p < 0.20:
        return ease(0.20, 0.40, p / 0.20), 0.35 * (1 - p / 0.20), False, 0.6
    if p < 0.55:
        return ease(0.40, 1.00, (p - 0.20) / 0.35), 0.0, False, ease(0.6, 1.0, (p - 0.20) / 0.35)
    if p < 0.82:
        return 1.35, 0.0, True, 1.3
    x = (p - 0.82) / 0.18
    return ease(1.35, 0.0, x), ease(0.0, 1.0, x), False, ease(1.3, 0.5, x)

# The visualiser's controls, for every moving background: (setting, label, [(value, name)...], default)
FX = (("react", "React", ((0.6, "Gentle"), (1.0, "Normal"), (1.6, "Strong")), 1.0),
      ("trails", "Trails", ((0.0, "Off"), (0.55, "Short"), (0.82, "Long")), 0.0),
      ("glow", "Glow", ((0.0, "Off"), (0.35, "Soft"), (0.7, "Strong")), 0.35),
      ("sym", "Symmetry", ((0, "Off"), (4, "4"), (6, "6"), (8, "8")), 0))


class FlashLimiter:
    """No more than three strong flashes a second -- the line the usual guidance for photosensitive
    epilepsy draws (WCAG 2.3.1). A kick past it still moves the picture, just not as a flash."""

    def __init__(self, per_second=3):
        self.per_second = per_second
        self.times = []
        self.prev = 0.0
        self.hold_until = 0.0

    def __call__(self, beat, now=None):
        now = time.monotonic() if now is None else now
        if beat > 0.65 and self.prev <= 0.65:
            self.times = [t for t in self.times if now - t < 1.0]
            if len(self.times) >= self.per_second:
                self.hold_until = now + 0.35
            else:
                self.times.append(now)
        self.prev = beat
        return min(beat, 0.4) if now < self.hold_until else beat
MIRRORS = ("glass", "neon")
_MODE_NO = {"aurora": 0, "nebula": 1, "glass": 2, "galaxy": 3, "neon": 4, "silk": 5,
            "halo": 6, "warp": 7, "ripple": 8, "storm": 9,
            # concepts from the reference images (previews; not in MODES until approved)
            "image": 30, "dots": 20, "prism": 21, "contour": 22, "fluted": 23, "liquid": 24}

_VERT = """#version 330 core
out vec2 uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}"""

_FRAG = """#version 330 core
in vec2 uv;
out vec4 frag;
uniform float time;
uniform float beat;       // 0..1, the music's pulse
uniform float energy;     // 0..1, how loud it is overall (slow)
uniform float b_mid;      // 0..1, the snare and the voice
uniform float b_hi;       // 0..1, the hi-hats
uniform float travel;     // how far the mirror has opened outward (runs faster on the beat)
uniform float pace;       // how fast the music drives it right now
uniform float density;    // rain: how much falls (1 = Rain as it always was)
uniform float clear;      // rain: the clouds parting and the light coming back (0 = Rain as it always was)
uniform float hq;         // rain: Storm story's finer water and stronger album colour (0 = Rain as it always was)
uniform int mode;         // 0 aurora, 1 nebula, 2 glass, 3 galaxy, 4 neon, 5 silk,
                          // 6 prism, 7 wormhole, 8 ripple, 9 rain
uniform int mono;         // 1: in black and white
uniform int sym;          // a kaleidoscope fold over any scene: 0 off, else the number of slices
uniform float storm;      // 0..1, a lightning flash (rain)
uniform float bolt;       // where the bolt strikes, and its shape (rain)
uniform vec4 w_age0, w_age1, w_amp0, w_amp1;   // the last eight ripples: how old, how strong (ripple)
uniform vec2 res;
uniform vec3 c_deep, c_main, c_second, c_hot;
uniform sampler2D img;    // "Your image": the picture the person chose
uniform float img_aspect; // its width / height

float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 6; i++) { v += a * noise(p); p = r * p * 2.03 + 11.7; a *= 0.5; }
    return v;
}
vec3 stars(vec2 p, float t) {
    vec3 s = vec3(0.0);
    for (int l = 0; l < 2; l++) {
        vec2 g = p * (55.0 + 40.0 * float(l));
        vec2 id = floor(g), f = fract(g) - 0.5;
        float h = hash(id + float(l) * 7.1);
        if (h > 0.965) {
            vec2 o = vec2(hash(id + 1.3), hash(id + 2.7)) - 0.5;
            float d = length(f - o * 0.6);
            float tw = 0.6 + 0.4 * sin(t * 2.0 + h * 40.0);
            s += vec3(0.8, 0.85, 1.0) * smoothstep(0.08, 0.0, d) * tw * (0.5 + beat * 0.5);
        }
    }
    return s;
}

vec3 aurora(vec2 p, float t) {
    vec2 q = p * (1.0 - 0.025 * beat);                 // a breath on the beat
    for (int i = 1; i < 6; i++) {
        float fi = float(i);
        q.x += 0.35 / fi * sin(fi * 1.9 * q.y + t * 1.2 + 0.3 * fi);
        q.y += 0.35 / fi * cos(fi * 1.5 * q.x + t * 0.9 + 0.7 * fi);
    }
    float a = 0.5 + 0.5 * sin(q.x * 1.3 + t);
    float b = 0.5 + 0.5 * cos(q.y * 1.1 - t * 0.7);
    // deep troughs, the cover's colours on the folds, its brightest colour where they meet
    vec3 col = mix(c_deep * 0.8, c_main, smoothstep(0.18, 0.92, a));
    col = mix(col, c_second * 1.05, smoothstep(0.35, 1.0, b) * 0.65);
    col = mix(col, c_hot, pow(a * b, 2.4) * 0.85);
    col *= 0.55 + 0.45 * smoothstep(0.0, 0.7, a + b * 0.5);
    float sheen = pow(0.5 + 0.5 * sin((q.x + q.y) * 3.0 + t), 10.0);
    col += sheen * 0.16 * mix(c_hot, vec3(1.0), 0.45);
    return col * (0.80 + 0.12 * energy);
}

float warp(vec2 q, float t, out vec2 r) {
    vec2 w = vec2(fbm(q + vec2(0.0, t)), fbm(q + vec2(5.2, 1.3) - t));
    r = vec2(fbm(q * 1.3 + 2.6 * w + vec2(1.7, 9.2) + t * 0.5), fbm(q * 1.3 + 2.6 * w + vec2(8.3, 2.8) - t * 0.35));
    return fbm(q * 0.9 + 3.0 * r);
}
vec3 nebula(vec2 p, float t) {
    vec2 q = p * (0.62 - 0.025 * beat);
    vec2 r; float n = warp(q, t, r);
    vec2 r2; float nr = warp(q * 1.004 + vec2(0.003, 0.0), t, r2);
    float nb = warp(q * 0.996 - vec2(0.003, 0.0), t, r2);
    vec3 ridge = vec3(1.0 - abs(nr * 2.0 - 1.0), 1.0 - abs(n * 2.0 - 1.0), 1.0 - abs(nb * 2.0 - 1.0));
    vec3 fil = pow(ridge, vec3(7.0));
    vec3 halo = pow(ridge, vec3(2.5));
    float body = smoothstep(0.30, 0.85, n);
    vec3 cloud = mix(c_main, c_second, smoothstep(0.25, 0.75, r.x));
    vec3 col = c_deep * 0.7 + cloud * (0.25 + body * 0.85);
    col += halo * mix(cloud, c_hot, 0.3) * 0.55 * (1.0 + 0.5 * beat + 0.3 * energy);
    col += fil * mix(c_hot, vec3(1.0), 0.35) * (0.80 + 0.8 * beat);
    col *= 0.65 + 0.35 * smoothstep(0.1, 0.55, n);
    col += stars(p, t * 20.0) * (1.0 - body) * 0.8;
    return col;
}

// ---- the mirrors ----
// The screen is a kaleidoscope: folded round the middle into n mirror-image slices, then mirrored again
// into tiles out to the edges -- and the tiles keep growing outward (two sizes, one fading into the next,
// so it never ends). Over it, in the middle, a sharp flower of light. Three sizes move with three parts
// of the music: the flower and the big shapes with the kick, the petals' outlines and the middle shapes
// with the snare and the voice, the fine detail and the little star at the heart with the hi-hats.
const float TAU = 6.2831853;
float glow(float d, float w) { return w / (abs(d) + w); }
vec3 pal3(float x) {                               // a walk through the cover's colours
    x = fract(x);
    vec3 c = mix(c_main, c_second, smoothstep(0.0, 0.5, x));
    c = mix(c, c_hot, smoothstep(0.45, 0.8, x));
    return mix(c, c_main, smoothstep(0.8, 1.0, x));
}
vec2 folded(vec2 p, float n) {                     // one slice of n, mirrored: x along it, y across
    float r = length(p);
    float seg = TAU / n;
    float a = abs(mod(atan(p.y, p.x), seg) - seg * 0.5);
    return r * vec2(cos(a), sin(a));
}
vec2 tiles(vec2 f, float z) {                      // mirrored tiles: 0..1 across each, flipped at every edge
    return abs(fract(f * z * 0.5 + 0.5) * 2.0 - 1.0);
}
float tri_edge(vec2 g, out vec2 id) {              // distance to a triangle grid's edges, and which triangle
    vec2 f = fract(g);
    float up = step(1.0, f.x + f.y);
    id = floor(g) * 2.0 + up;
    return min(min(min(f.x, f.y), min(1.0 - f.x, 1.0 - f.y)), abs(f.x + f.y - 1.0) * 0.7071);
}

// the field each look fills its tiles with, for c in 0..1
vec3 field(vec2 c, int m) {
    vec3 col;
    if (m == 2) {                                  // crystal: panes of coloured glass, lit edges, glints
        vec2 id; float e1 = tri_edge(c * 2.0, id);
        float h = hash(id);
        col = pal3(h * 0.7 + 0.1) * (0.22 + 0.30 * h) * (0.7 + 0.6 * beat) * (0.7 + 0.6 * smoothstep(0.0, 0.4, e1));
        col += mix(c_hot, vec3(1.0), 0.5) * glow(e1, 0.012) * (0.25 + 0.8 * beat);
        vec2 id2; float e2 = tri_edge(c * 6.0, id2);
        col += mix(c_second, vec3(1.0), 0.3) * glow(e2, 0.006) * (0.06 + 0.6 * b_mid) * hash(id2);
        vec2 id3; float e3 = tri_edge(c * 18.0, id3);
        col += vec3(1.0) * glow(e3, 0.004) * step(0.85, hash(id3 + floor(time * 6.0))) * (0.03 + 0.9 * b_hi);
    } else if (m == 3) {                           // galaxy: clouds and filaments, stars
        float big = fbm(c * 2.2 + vec2(time * 0.03, 0.0));
        col = c_deep * 0.5 + mix(c_main, c_second, smoothstep(0.3, 0.75, big)) * smoothstep(0.3, 0.85, big)
              * (0.55 + 0.7 * beat);
        float fil = pow(1.0 - abs(fbm(c * 5.0 + 3.0) * 2.0 - 1.0), 9.0);
        col += mix(c_hot, vec3(1.0), 0.3) * fil * (0.10 + 0.8 * b_mid);
        vec2 g = c * 22.0, id = floor(g), f = fract(g) - 0.5;
        float h = hash(id);
        col += vec3(0.85, 0.9, 1.0) * step(0.88, h) * smoothstep(0.18, 0.0, length(f)) * (0.25 + 1.2 * b_hi)
               * (0.6 + 0.4 * sin(time * 3.0 + h * 50.0));
    } else if (m == 4) {                           // neon: arcs and lattices of light on the dark
        col = c_deep * 0.30;
        float arc = min(abs(length(c) - 0.72), abs(length(c - 1.0) - 0.55));
        col += pal3(0.15) * glow(arc, 0.010 + 0.010 * beat) * (0.45 + 0.9 * beat);
        float diag = min(abs(c.x - c.y), abs(c.x + c.y - 1.0));
        col += mix(c_second, vec3(1.0), 0.2) * glow(diag, 0.005) * (0.10 + 0.8 * b_mid);
        vec2 b = c * 8.0;
        col += c_hot * glow(abs(fract(b.x) - 0.5) - 0.48, 0.002) * (0.03 + 0.35 * b_hi);
        col += vec3(1.0) * smoothstep(0.14, 0.0, length(fract(b) - 0.5)) * step(0.55, hash(floor(b)))
               * (0.03 + 0.9 * b_hi);
    } else {                                       // silk: soft folds and glowing ridges, a fine fur of light
        vec2 s = c * 2.6;
        for (int i = 1; i < 5; i++) {
            float fi = float(i);
            s.x += (0.32 + 0.10 * beat) / fi * sin(fi * 2.3 * s.y + time * 0.25 + fi);
            s.y += 0.30 / fi * cos(fi * 1.9 * s.x - time * 0.2 + fi);
        }
        float v = 0.5 + 0.5 * sin(s.x * 2.4) * cos(s.y * 2.2);
        col = mix(c_deep * 0.55, c_main, smoothstep(0.15, 0.95, v)) * (0.55 + 0.5 * beat);
        float ridge = pow(1.0 - abs(sin(s.x * 1.6 + s.y * 1.2)), 7.0);
        col += mix(c_second, vec3(1.0), 0.35) * ridge * (0.22 + 0.7 * beat);
        float vein = pow(1.0 - abs(sin(s.x * 5.0 - s.y * 3.0)), 12.0);
        col += mix(c_hot, vec3(1.0), 0.3) * vein * (0.06 + 0.6 * b_mid);
        col += mix(c_hot, vec3(1.0), 0.6) * pow(0.5 + 0.5 * sin(s.x * 34.0 + s.y * 9.0), 16.0) * (0.02 + 0.3 * b_hi);
    }
    return col;
}

// the tiles, growing outward without end: two sizes, the smaller fading in as the bigger leaves
vec3 grown(vec2 f, int m) {
    float k = fract(travel * 0.5);
    float za = 1.5 * exp2(-k);
    return field(tiles(f, za), m) * (1.0 - k) + field(tiles(f, za * 2.0), m) * k;
}

// the flower in the middle
vec3 flower(vec2 p, float n) {
    vec2 f = folded(p, n);                         // x out along a ray, y across it
    float r = length(p);
    float R = 0.52 * (1.0 + 0.16 * beat);          // the kick swells it
    vec3 white = mix(c_hot, vec3(1.0), 0.75);
    vec3 col = vec3(0.0);
    // rays of light out to the petals' tips
    col += white * glow(f.y, 0.0025) * smoothstep(R * 1.05, R * 0.2, f.x) * (0.45 + 0.9 * beat);
    // petals: thin outlines, a faint fill, bright dashes running out along the inside
    vec2 e = (f - vec2(R * 0.56, 0.0)) / vec2(R * 0.46, R * 0.15);
    float le = length(e);
    float de = (le - 1.0) * R * 0.15;
    col += mix(c_second, vec3(1.0), 0.45) * glow(de, 0.0022) * (0.35 + 0.7 * b_mid);
    col += c_main * 0.22 * smoothstep(0.004, -0.004, de);
    float dash = smoothstep(0.08, 0.0, abs(le - 0.72)) * step(0.5, fract(f.x / R * 4.0 - travel * 3.0));
    col += white * dash * step(0.0, f.y) * (0.10 + 1.1 * b_mid);
    // a second, offset ring of petals, wider and fainter
    vec2 g = folded(p * mat2(cos(TAU / n * 0.5), sin(TAU / n * 0.5), -sin(TAU / n * 0.5), cos(TAU / n * 0.5)), n);
    vec2 e2 = (g - vec2(R * 0.62, 0.0)) / vec2(R * 0.52, R * 0.22);
    col += pal3(0.35) * glow((length(e2) - 1.0) * R * 0.2, 0.002) * (0.18 + 0.4 * beat);
    // the little star at the heart, sparkling with the hi-hats
    float rs = 0.09 * (1.0 + 0.45 * b_hi);
    float star = f.y - max(0.0, rs - f.x) * 0.32;
    col += white * glow(star, 0.0018) * step(f.x, rs) * (0.5 + 1.3 * b_hi);
    col += white * glow(r - rs * 0.35, 0.002) * (0.3 + 0.9 * b_hi);
    return col;
}

// ---- four scenes ----
// Real light, not drawn lines: smoke and cloud that flow (warped noise, stirred by the music), lit by
// what's in the scene and dimming it in turn; light that can run brighter than white and is brought
// back by a film curve; glints, haze and soft focus.
vec3 hot(vec3 c, float k) { return mix(c, vec3(1.0), k); }
mat2 rot(float a) { return mat2(cos(a), sin(a), -sin(a), cos(a)); }

// flowing smoke: noise warped by noise warped by noise -- it folds and curls like ink in water
float smoke(vec2 q, float t) {
    vec2 w = vec2(fbm(q * 1.1 + vec2(0.0, t * 0.6)), fbm(q * 1.1 + vec2(5.2, -t * 0.5)));
    vec2 w2 = vec2(fbm(q * 1.5 + 2.4 * w + vec2(1.7, 9.2) + t * 0.3), fbm(q * 1.5 + 2.4 * w + vec2(8.3, 2.8) - t * 0.25));
    return fbm(q * 1.05 + 2.8 * w2);
}

// HALO: a ring of light hanging in drifting smoke. The smoke glows where the ring lights it and veils
// it where it passes in front; light falls through the gaps in shafts; dust turns in the beam.
// The ring answers the kick, the smoke's folds the snare, the dust the hi-hats.
vec3 halo_m(vec2 p) {
    float t = travel * 0.30 + time * 0.015;
    vec2 q = p;
    q.y *= 1.22;                                   // tipped back a little: an ellipse
    float R = 0.56 * (1.0 + 0.05 * beat);
    float dr = abs(length(q) - R);
    vec3 lc = hot(c_hot, 0.30);
    float d = smoke(p * 1.25, t);
    float dens = smoothstep(0.10, 0.72, d);
    float light = 0.20 / (dr * dr * 5.0 + 0.20) + 0.12;   // the ring's light, and a little from all round  // how strongly the ring lights this bit of smoke
    vec3 smokec = mix(c_main, c_second, smoothstep(0.45, 0.85, d));
    vec3 col = c_deep * 0.04;
    col += smokec * dens * dens * light * (0.70 + 1.1 * beat);
    float fil = pow(1.0 - abs(d * 2.0 - 1.0), 9.0);
    col += hot(smokec, 0.45) * fil * light * (0.04 + 0.55 * b_mid);
    float ring = exp(-dr * dr * 2500.0) * 0.85 + exp(-dr * 24.0) * 0.30 + exp(-dr * 5.0) * 0.08;
    col += lc * ring * (0.5 + 1.5 * beat) * (1.0 - 0.7 * dens * dens);
    // shafts: how much smoke lies between here and the ring
    vec2 to = normalize(q + 1e-4) * R;
    to.y /= 1.22;
    float occ = 0.0;
    for (int i = 1; i <= 5; i++) {
        occ += smoothstep(0.45, 0.9, fbm(mix(p, to, float(i) / 6.0) * 2.4 + vec2(t, -t)));
    }
    occ /= 5.0;
    col += lc * 0.05 / (dr + 0.05) * (1.0 - occ) * 0.20 * (0.4 + beat) * smoothstep(1.4, 0.2, length(p));
    // dust, slowly turning, lit only where the light is
    for (int l = 0; l < 2; l++) {
        float fl = float(l);
        vec2 g = (rot(time * 0.02 * (fl + 1.0)) * p) * (34.0 + 24.0 * fl) + vec2(fl * 7.0, time * (0.25 + 0.2 * fl));
        vec2 id = floor(g), f = fract(g) - 0.5;
        float h = hash(id + fl * 11.0);
        vec2 off = (vec2(hash(id + 1.3), hash(id + 2.9)) - 0.5) * 0.6;
        float mote = step(0.9, h) * exp(-dot(f - off, f - off) * (90.0 - 30.0 * fl));
        col += vec3(1.0, 0.97, 0.92) * mote * min(light, 1.2) * (0.06 + 1.1 * b_hi) * (0.5 + 0.5 * sin(time * 2.0 + h * 30.0));
    }
    return col;
}

// WARP: down a tunnel of swirling nebula toward a light; stars smear past, the trails longer the
// harder the music drives -- the big ones on the kick, the middle ones the snare, the fine ones the hats
vec3 warp_m(vec2 p) {
    float r = length(p) + 1e-4, a = atan(p.y, p.x);
    float z = 0.30 / r + travel * 1.4;
    float tw = a + z * 0.35;                         // the walls twist as they come
    vec2 wq = vec2(cos(tw), sin(tw)) * 1.6 + vec2(0.0, z * 0.35);
    float d = smoke(wq, time * 0.03 + travel * 0.2);
    vec3 neb = mix(c_main, c_second, smoothstep(0.3, 0.9, d)) * smoothstep(0.30, 1.0, d) * 0.85;
    float fil = pow(1.0 - abs(d * 2.0 - 1.0), 8.0);
    vec3 col = c_deep * 0.05 + (neb * 0.55 + hot(c_hot, 0.3) * fil * (0.05 + 0.4 * b_mid)) * smoothstep(0.04, 0.9, r)
               * (0.75 + 0.5 * beat);
    col += hot(c_hot, 0.55) * (exp(-r * 18.0) * 1.8 + exp(-r * 5.0) * 0.25 + exp(-r * 1.6) * 0.05) * (0.7 + 0.9 * beat);
    float sp = clamp(pace, 0.2, 6.0);
    for (int l = 0; l < 3; l++) {
        float fl = float(l);
        float cols = l == 0 ? 70.0 : (l == 1 ? 140.0 : 260.0);
        float react = l == 0 ? beat : (l == 1 ? b_mid : b_hi);
        float ca = (a / TAU + 0.5) * cols;
        float id = floor(ca);
        float fa = fract(ca) - 0.5;
        float h = hash(vec2(id, fl * 7.0 + 1.0));
        float h2 = hash(vec2(id, fl * 7.0 + 3.0));
        float zs = fract(h2 - travel * (0.16 + 0.07 * fl) * (0.8 + 0.4 * h)) * 3.0 + 0.06;
        float rs = 0.11 / zs;
        float len = rs * min(0.8, 0.02 + 0.14 * sp);
        float along = (rs - r) / max(len, 1e-3);          // 0 at the star, 1 at the end of its trail
        float trail = along > 0.0 ? pow(max(0.0, 1.0 - along), 2.2) : exp(-along * along * 400.0);
        float across = abs(fa) * TAU / cols * r;
        float wdt = (l == 0 ? 0.0026 : (l == 1 ? 0.0017 : 0.0011)) * (1.0 + 0.5 * react) / max(0.4, zs);
        float lit = exp(-pow(across / wdt, 2.0)) * trail;
        float twk = 0.55 + 0.45 * sin(time * (1.5 + 10.0 * h) + h * 50.0);
        float bright = step(0.45, h) * lit * twk * smoothstep(3.0, 1.0, zs) * (0.25 + 1.8 * react);
        vec3 sc = mix(vec3(0.80, 0.87, 1.0), hot(pal3(h), 0.45), 0.4);
        col += sc * bright * (l == 0 ? 2.0 : (l == 1 ? 1.3 : 0.9));
    }
    col += hot(c_hot, 0.5) * exp(-abs(p.y) * 70.0) * exp(-abs(p.x) * 0.9) * (0.05 + 0.22 * beat);
    return col;
}

// the camera for the scenes with water: a metre above it, tipped down
const float PITCH = 0.30;
vec3 cam_ray(vec2 p) {
    vec3 rd = normalize(vec3(p.x, p.y, 1.7));
    rd.yz = rot(PITCH) * rd.yz;
    return rd;
}

// RIPPLE: still dark water, a drop of light hanging just above it. Each beat sends rings out from under
// it; its light lies on the water as a path of glints that the rings break up; the hi-hats shiver the surface.
const vec3 DROP = vec3(0.0, 0.32, 3.6);
float water_h(vec2 xz) {
    float r = length(xz - DROP.xz);
    float ages[8] = float[8](w_age0.x, w_age0.y, w_age0.z, w_age0.w, w_age1.x, w_age1.y, w_age1.z, w_age1.w);
    float amps[8] = float[8](w_amp0.x, w_amp0.y, w_amp0.z, w_amp0.w, w_amp1.x, w_amp1.y, w_amp1.z, w_amp1.w);
    float h = 0.0;
    for (int i = 0; i < 8; i++) {
        float d = r - ages[i] * 0.8;
        h += amps[i] * 0.035 * exp(-d * d * 6.0) * cos(d * 15.0) * exp(-ages[i] * 0.30) / (1.0 + r * 0.4);
    }
    // the calm: long slow swells and a breath of wind
    h += 0.008 * sin(xz.x * 0.9 + time * 0.35) * sin(xz.y * 1.3 - time * 0.27);
    h += 0.006 * (fbm(xz * 1.6 + vec2(time * 0.10, time * 0.06)) - 0.5);
    return h;
}
vec3 night(vec3 rd) {
    vec3 c = mix(c_main * 0.14 + c_deep * 0.22, c_deep * 0.03, smoothstep(-0.02, 0.55, rd.y));
    c += mix(c_second, c_main, 0.5) * 0.12 * exp(-abs(rd.y) * 16.0);   // the glow along the horizon
    return c;
}
vec3 drop_light(vec3 ro, vec3 rd, float pulse) {
    vec3 v = normalize(DROP - ro);
    float c = max(dot(rd, v), 0.0);
    float ang = sqrt(max(0.0, 2.0 - 2.0 * c));
    return hot(c_hot, 0.55) * (exp(-ang * ang * 30000.0) * 4.0 + exp(-ang * 70.0) * 0.45 + exp(-ang * 14.0) * 0.08) * pulse;
}
vec3 ripple_m(vec2 p) {
    vec3 ro = vec3(0.0, 1.0, 0.0), rd = cam_ray(p);
    float pulse = 0.7 + 1.0 * beat;
    vec3 col;
    if (rd.y >= 0.0) {
        col = night(rd);
    } else {
        float t = -ro.y / rd.y;
        vec3 hit = ro + rd * t;
        vec2 xz = hit.xz;
        float e = 0.01;
        float h0 = water_h(xz);
        vec3 n = normalize(vec3(-(water_h(xz + vec2(e, 0.0)) - h0) / e, 1.0, -(water_h(xz + vec2(0.0, e)) - h0) / e));
        n = normalize(n + vec3(noise(xz * 22.0 + time * 1.6) - 0.5, 0.0, noise(xz * 22.0 - time * 1.6) - 0.5)
                      * (0.03 + 0.18 * b_hi) * exp(-t * 0.25));
        vec3 rr = reflect(rd, n);
        float fres = 0.02 + 0.98 * pow(1.0 - max(dot(n, -rd), 0.0), 5.0);
        vec3 lv = normalize(DROP - hit);
        float sd = max(dot(rr, lv), 0.0);
        float glitter = pow(sd, 1400.0) * 9.0 + pow(sd, 120.0) * 0.9 + pow(sd, 12.0) * 0.06;
        col = mix(c_deep * 0.035, night(rr), fres);
        col += hot(c_hot, 0.45) * glitter * pulse;
        col += c_main * 0.06 * exp(-length(xz - DROP.xz) * 0.9) * pulse;       // light under the surface
        col = mix(col, night(vec3(rd.x, 0.0, rd.z)), smoothstep(8.0, 40.0, t));
    }
    col += drop_light(ro, rd, pulse);
    return col;
}

// STORM: low cloud lit from below by distant lights, mist, rain that drives harder with the music,
// wet ground that mirrors it all; on the biggest hits lightning forks down and lights the cloud from inside
float bolt_x(float y, float seed) {
    return (fract(seed * 7.31) - 0.5) * 1.4 + (fbm(vec2(y * 2.2, seed * 13.0)) - 0.5) * 0.7
           + (noise(vec2(y * 18.0, seed * 5.0)) - 0.5) * 0.06;
}
float bolt_d(vec2 p, float bottom) {
    float main = abs(p.x - bolt_x(p.y, bolt)) + (p.y < bottom ? 9.0 : 0.0);
    float y0 = 0.35;
    float bx = bolt_x(y0, bolt) + (y0 - p.y) * 0.55 * (fract(bolt * 3.7) < 0.5 ? -1.0 : 1.0)
               + (noise(vec2(p.y * 16.0, bolt * 9.0)) - 0.5) * 0.05;
    float branch = abs(p.x - bx) + ((p.y > y0 || p.y < y0 - 0.45) ? 9.0 : 0.0);
    return min(main, branch * 1.7);
}
vec3 storm_sky(vec3 rd, vec2 sp) {
    vec3 c = mix(c_main * 0.10 + c_deep * 0.18, c_deep * 0.05, smoothstep(0.0, 0.45, rd.y));
    float t = 2.2 / max(rd.y, 0.03);
    vec2 uv = rd.xz * t * 0.15 + vec2(time * 0.010, time * 0.025);
    float d = smoke(uv * 0.8, time * 0.01);
    float dl = smoke(uv * 0.8 + vec2(0.03, 0.06), time * 0.01);
    float dens = smoothstep(0.3, 0.9, d) * smoothstep(0.0, 0.10, rd.y) * (1.0 - 0.85 * clear);
    float edge = clamp((d - dl) * 6.0, 0.0, 1.0);                       // the side the light comes from
    float flash_near = exp(-abs(sp.x - bolt_x(sp.y, bolt)) * 2.2);
    vec3 under = c_hot * 0.10 * exp(-max(rd.y, 0.0) * 6.0);                       // lights below, on the cloud's belly
    vec3 cloud = c_deep * 0.10 + mix(c_main, c_second, d) * 0.10 + under + hot(c_main, 0.3) * edge * 0.08;
    cloud += hot(c_hot, 0.7) * storm * (0.3 + 1.6 * flash_near) * (0.4 + edge + d);
    c = mix(c, cloud, dens);
    c += vec3(0.82, 0.88, 1.0) * storm * 0.10;
    // the storm passing: a warm light low in the sky, the air brightening
    c += hot(c_hot, 0.35) * clear * (0.30 * exp(-max(rd.y, 0.0) * 5.0) + 0.06);
    return c;
}
// a scatter of far-off lights along the horizon (a town across the water); `spread` widens them upward
// -- what a wet road does to a light: a long streak toward you
vec3 lamps(vec3 rd, float spread) {
    float az = rd.x / max(rd.z, 0.05);
    vec3 c = vec3(0.0);
    for (int k = 0; k < 2; k++) {
        float cellw = k == 0 ? 0.16 : 0.07;
        float cell = floor(az / cellw);
        for (int j = -1; j <= 1; j++) {
            float id = cell + float(j);
            float h = hash(vec2(id, float(k) * 5.0 + 1.0));
            if (h < 0.55) continue;
            float x = (id + 0.2 + 0.6 * hash(vec2(id, float(k) + 9.0))) * cellw;
            float y = 0.004 + 0.012 * hash(vec2(id, float(k) + 3.0));
            float dx = (az - x) * (k == 0 ? 260.0 : 420.0);
            float dy = (abs(rd.y) - y) * 300.0 / spread;
            vec3 tint = mix(hot(c_hot, 0.35), vec3(1.0, 0.78, 0.5), hash(vec2(id, 7.0)) * 0.6);
            float tw = 0.85 + 0.15 * sin(time * (1.0 + 3.0 * h) + h * 40.0);
            c += tint * (exp(-dx * dx - dy * dy) * 1.4 + exp(-(dx * dx + dy * dy) * 0.02) * 0.05) * tw
                 * (k == 0 ? 1.0 : 0.6);
        }
    }
    return c;
}
vec3 mist(vec3 c, float y, float amount) {
    vec3 fog = c_main * 0.10 + c_hot * 0.05 + c_deep * 0.1;
    return mix(c, fog, exp(-abs(y) * 9.0) * amount);
}
vec3 vivid(vec3 c) {                    // the cover's colour at full strength, whatever its brightness
    return c / max(max(c.r, max(c.g, c.b)), 0.06);
}
// Storm story's last touches: the album's colours through the air, and water on the glass in front of
// you -- drops that sit and catch the light, a few running down, more of them as the rain gets heavier
vec3 story_finish(vec3 col, vec2 p) {
    // grade: shadows toward the cover's deep colour, the sky's light toward its main one, the flash its hot one
    float l = dot(col, vec3(0.299, 0.587, 0.114));
    vec3 tint = mix(vivid(c_main), vivid(c_second), smoothstep(-0.6, 0.6, p.y));
    col = mix(col, tint * l * 1.15, 0.45 * hq);
    col += vivid(c_deep) * 0.025 * hq;
    col += hot(vivid(c_hot), 0.4) * storm * 0.12 * hq;
    // drops on the glass: few, of every size, almost clear -- each a tiny lens showing the sky above
    // as a brighter foot, with a hairline rim and one sharp glint; more of them as the rain gets heavier
    float amount = clamp(density, 0.0, 1.3);
    for (int k = 0; k < 3; k++) {
        float fk = float(k);
        vec2 g = p * (3.0 + fk * 3.5) + vec2(fk * 17.0, fk * 5.0);
        vec2 id = floor(g);
        float h = hash(id + fk * 7.0);
        if (h > 0.97 - 0.30 * amount) {
            float slide = step(0.85, hash(id + 3.3));
            vec2 c = vec2(hash(id + 1.1), fract(hash(id + 2.2) - slide * time * 0.035 * (1.0 + density))) - 0.5;
            vec2 d = fract(g) - 0.5 - c * 0.55;
            float r = (0.05 + 0.13 * pow(hash(id + 5.5), 2.0)) * (1.0 - fk * 0.25);
            d.y *= 1.0 - 0.25 * slide;                                  // a running drop draws out a little
            float e = length(d) / r;
            if (e < 1.0) {
                float foot = smoothstep(-0.2, 0.9, d.y / r);           // the sky, turned over: light at its foot
                vec3 inside = col * (0.82 + 0.30 * foot) + tint * l * 0.25 * foot;
                inside *= 1.0 - 0.30 * smoothstep(0.80, 1.0, e);        // the hairline rim
                inside += vec3(1.0) * smoothstep(0.16, 0.0, length(d / r - vec2(-0.30, -0.35))) * 0.75;
                col = mix(col, inside, smoothstep(1.0, 0.90, e) * hq);
            }
        }
    }
    return col;
}

vec3 storm_m(vec2 p) {
    vec3 ro = vec3(0.0, 1.0, 0.0), rd = normalize(vec3(p.x, p.y + 0.10, 1.7));
    float horizon = -0.10;
    vec3 col;
    if (rd.y > 0.0) {
        col = storm_sky(rd, p);
        float bd = bolt_d(p, horizon);
        col += vec3(0.92, 0.95, 1.0) * exp(-bd * 900.0) * storm * 4.0;
        col += hot(c_hot, 0.6) * (exp(-bd * 60.0) * 0.6 + exp(-bd * 8.0) * 0.15) * storm;
        col += c_hot * 0.10 * exp(-rd.y * 40.0);                           // a town's glow on the horizon
        col = mist(col, rd.y, 0.7);
        col += lamps(rd, 1.0);
    } else {
        float t = -ro.y / rd.y;
        vec2 xz = (ro + rd * t).xz;
        float far = smoothstep(4.0, 22.0, t);
        // puddles: crisp-edged pools; the rest is wet asphalt, its grit catching the light
        float pm = fbm(xz * 0.42) + 0.12 * fbm(xz * 2.1);
        float puddle = smoothstep(0.56, 0.585, pm);
        float grit = fbm(xz * 14.0) * 0.6 + noise(xz * 55.0) * 0.4;
        float rough = mix(0.10 + 0.05 * grit, 0.006, puddle) * (1.0 - far * 0.6);
        vec3 n = normalize(vec3((noise(xz * 9.0) - 0.5) * rough * 4.0 + (grit - 0.5) * rough * 2.0, 1.0,
                                (noise(xz * 9.0 + 7.0) - 0.5) * rough * 4.0));
        // drops landing: a sharp ring and a fainter one inside it, and a fleck of splash as each lands
        float rings = 0.0, splash = 0.0;
        for (int k = 0; k < 3; k++) {
            float fk = float(k);
            vec2 cell = xz * (2.6 + fk * 1.7) + fk * 3.7;
            vec2 id = floor(cell);
            vec2 f = fract(cell) - 0.5 - (vec2(hash(id + 1.0), hash(id + 2.0)) - 0.5) * 0.5;
            float h = hash(id + fk * 9.0);
            float ph = fract(time * (0.55 + 0.45 * hash(id + 3.0)) * (0.6 + 0.25 * pace) + h);
            float d = length(f);
            float r1 = d - ph * 0.40, r2 = d - ph * 0.24;
            float life = (1.0 - ph) * (1.0 - ph) * step(0.3, h);
            float ring = (exp(-r1 * r1 * 4000.0) + 0.5 * exp(-r2 * r2 * 4000.0)) * life;
            rings += ring;
            splash += exp(-d * d * 3000.0) * smoothstep(0.08, 0.0, ph) * step(0.3, h);
            n = normalize(n + vec3(f.x, 0.0, f.y) / max(d, 1e-3) * ring * 0.35 * (1.0 - far));
        }
        vec3 rr = reflect(rd, n);
        float fres = 0.04 + 0.96 * pow(1.0 - max(dot(n, -rd), 0.0), 5.0);
        vec2 mp = vec2(p.x + (rr.x - rd.x) * 0.3, 2.0 * horizon - p.y);
        vec3 refl = storm_sky(vec3(rr.x, max(rr.y, 0.001), rr.z), mp);
        float bd = bolt_d(mp, horizon);
        refl += vec3(0.9, 0.94, 1.0) * (exp(-bd * 500.0) * 2.0 + exp(-bd * 30.0) * 0.3) * storm;
        refl += c_hot * 0.10 * exp(-max(rr.y, 0.0) * 40.0);
        // the far lights on the wet ground: short and sharp in a puddle, long streaks on the asphalt
        vec3 lr = lamps(vec3(rr.x, max(rr.y, 0.0), rr.z), mix(9.0, 1.4, puddle));
        vec3 base = c_deep * 0.025 + vec3(0.012) * grit;
        col = mix(base, refl, mix(0.22, 1.0, puddle) * min(1.0, fres * 1.7));
        col += lr * mix(0.45, 0.9, puddle);
        col += hot(c_second, 0.6) * rings * mix(0.25, 1.0, puddle) * (0.10 + 0.45 * b_hi) * (1.0 - far);
        col += vec3(0.9, 0.93, 1.0) * splash * (0.08 + 0.6 * b_hi) * (1.0 - far);
        col = mist(col, rd.y, 0.8);
    }
    // the rain: pointed drops at three depths -- a bright head, the trail tapering to a fine point,
    // crisp edges and a breath of glow; near drops longer and a little softer. They fall faster, and
    // the trails grow longer, as the music drives
    float sp = clamp(pace, 0.2, 6.0);
    for (int l = 0; l < 3; l++) {
        float fl = float(l);
        float cols = l == 0 ? 24.0 : (l == 1 ? 58.0 : 130.0);
        vec2 q = vec2(p.x + p.y * 0.08 + fl * 0.37, p.y);
        float cx = floor(q.x * cols);
        float hx = hash(vec2(cx, fl + 1.0));
        float y = q.y + travel * (3.0 - fl * 0.6) * (0.85 + hx * 0.3) + hx * 17.0;
        float rows = 0.8 + fl * 0.6;
        float seg = fract(y * rows);
        float row = floor(y * rows);
        float hb = hash(vec2(cx, row + fl * 3.0));
        float len = (0.06 + 0.045 * sp) * (1.0 - fl * 0.25) * (0.7 + 0.6 * hb) * rows;
        float s01 = seg / len;                             // 0 at the head, 1 at the tip of the trail
        if (s01 < 1.0 && hb > 1.0 - 0.38 * density) {     // about a third of them: a shower, not a flood
            float wdt = (l == 0 ? 0.0026 : (l == 1 ? 0.0011 : 0.0006));
            float taper = pow(1.0 - s01, 0.9) * smoothstep(0.0, 0.04, s01) + smoothstep(0.05, 0.0, s01) * 0.9;
            float w = wdt * max(taper, 0.05);
            float xoff = (hash(vec2(cx, row)) - 0.5) * 0.4 / cols;
            float across = abs(fract(q.x * cols) - 0.5 - xoff * cols) / cols;
            float core = smoothstep(w, w * 0.35, across);
            float halo = exp(-pow(across / (wdt * 2.5), 2.0)) * 0.12;
            float bright = (pow(1.0 - s01, 1.6) * 0.85 + 0.15) * (0.5 + hb);
            float lit = l == 0 ? 0.18 : (l == 1 ? 0.24 : 0.16);
            vec3 dc = mix(c_second * 0.5, vec3(0.86, 0.91, 1.0), 0.82);
            if (hq > 0.0) {
                // water, not light: the body takes the sky's colour, the head is a bead with a bright point
                vec3 sky = mix(vivid(c_second), vivid(c_main), 0.4);
                dc = mix(dc, mix(sky, vec3(1.0), 0.55), hq * 0.6);
                float bead = smoothstep(wdt * 1.6, wdt * 0.6, length(vec2(across, (seg - wdt * 1.2) * 0.9)));
                core = max(core, bead);
                bright += bead * 0.9 * hq;
            }
            col += dc * (core + halo) * bright * lit * (1.0 + 0.8 * b_mid + 2.5 * storm);
        }
    }
    if (hq > 0.0) col = story_finish(col, p);
    return col;
}

// ---- concepts from the reference images ----
// The colour science they share: near-black grounds, so colour glows; light that runs past white at its
// core and falls off into saturated colour (a film curve brings it back); a spectral ramp -- deep blue,
// violet, magenta, red, orange, yellow, white -- climbing with height or intensity; red, green and blue
// parting at the edges of light (dispersion); a fine grain over all of it.
vec3 spectral(float t) {                // 0 deep blue .. 1 white-hot
    t = clamp(t, 0.0, 1.0);
    vec3 c0 = vec3(0.06, 0.10, 0.95), c1 = vec3(0.50, 0.08, 1.00), c2 = vec3(1.00, 0.10, 0.72);
    vec3 c3 = vec3(1.00, 0.30, 0.22), c4 = vec3(1.00, 0.62, 0.10), c5 = vec3(1.00, 0.95, 0.35), c6 = vec3(1.0);
    float k = t * 6.0;
    vec3 c = mix(c0, c1, clamp(k, 0.0, 1.0));
    c = mix(c, c2, clamp(k - 1.0, 0.0, 1.0));
    c = mix(c, c3, clamp(k - 2.0, 0.0, 1.0));
    c = mix(c, c4, clamp(k - 3.0, 0.0, 1.0));
    c = mix(c, c5, clamp(k - 4.0, 0.0, 1.0));
    return mix(c, c6, clamp(k - 5.0, 0.0, 1.0));
}
vec3 cover_tint(vec3 c, float k) {      // a nudge toward the playing cover's colour, keeping brightness
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    vec3 m = c_main / max(0.05, dot(c_main, vec3(0.299, 0.587, 0.114)));
    return mix(c, m * l, k);
}

// DOT TERRAIN (ref: a dot-matrix wave): a field of dots in depth, lifted into peaks by the music --
// the big swell by the kick, a second by the snare, a ripple by the hats -- coloured by height on the
// spectral ramp, crisp, with a little bloom.
float terrain(float x, float z) {
    float h = 0.0;
    h += (0.55 + 0.75 * beat) * exp(-((x + 0.9) * (x + 0.9) * 1.6 + (z - 4.2) * (z - 4.2) * 0.35));
    h += (0.45 + 0.80 * b_mid) * exp(-((x - 1.25) * (x - 1.25) * 1.3 + (z - 5.4) * (z - 5.4) * 0.30));
    h -= (0.25 + 0.30 * beat) * exp(-((x - 0.15) * (x - 0.15) * 2.2 + (z - 3.0) * (z - 3.0) * 0.6));
    h += 0.06 * sin(x * 2.3 + travel * 1.4) * sin(z * 1.7 - travel) * (0.4 + 1.6 * b_hi);
    return h;
}
vec3 dots_m(vec2 p) {
    vec3 col = vec3(0.0);
    const float F = 1.55, CAM = 1.05, HZ = 0.10;
    for (int j = 0; j < 34; j++) {
        float z = 2.0 + float(j) * 0.17;
        float sp = 0.085;
        float xw = p.x * z / F;
        float i = floor(xw / sp + 0.5);
        for (int k = -1; k <= 1; k++) {
            float x = (i + float(k)) * sp;
            float h = terrain(x, z);
            vec2 q = vec2(x * F / z, HZ + (h - CAM) * F / z + 0.55);
            float r = 0.011 * F / z * 2.2;
            float d = length(p - q);
            float t = clamp(h * 0.75 + 0.18, 0.0, 1.0);
            vec3 c = cover_tint(spectral(t), 0.18) * (1.0 + 1.6 * smoothstep(0.85, 1.0, t));
            float dot_ = smoothstep(r, r * 0.72, d);
            float halo = exp(-d / r * 1.6) * 0.12;
            float fade = smoothstep(7.8, 5.5, z);
            col = max(col, c * (dot_ + halo) * fade);
        }
    }
    return col;
}

float sfbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 3; i++) { v += a * noise(p); p = r * p * 2.02 + 7.3; a *= 0.45; }
    return v / 0.8;
}
float sflow(vec2 q, float t) {         // broad folds, warped twice
    vec2 w = vec2(sfbm(q + vec2(0.0, t)), sfbm(q + vec2(5.2, -t * 0.8)));
    vec2 w2 = vec2(sfbm(q + 1.8 * w + vec2(1.7, 9.2)), sfbm(q + 1.8 * w + vec2(8.3, 2.8) + t * 0.3));
    return sfbm(q + 2.0 * w2);
}

// PRISM (ref: light through crystal): ribbons of light sweeping out of a white-hot middle on a deep
// navy ground -- mostly white and ice-blue, their edges only just splitting into a warm and a cool
// fringe; soft out-of-focus bands behind. The kick opens and brightens them, the snare bends them, the
// hats glint.
float ribbon(vec2 p, float a0, float bend, float w) {
    float r = length(p);
    float th = atan(p.y, p.x);
    float ang = a0 + bend * r + 0.25 * sin(r * 2.2 + a0 * 3.0);
    float d = abs(mod(th - ang + 3.14159265, 6.2831853) - 3.14159265) * r;
    return exp(-(d * d) / (w * w));
}
vec3 prism_m(vec2 p) {
    float r = length(p);
    vec3 col = mix(vec3(0.012, 0.016, 0.050), vec3(0.05, 0.07, 0.20), exp(-r * 0.9));
    col = cover_tint(col, 0.3);
    vec3 ice = cover_tint(vec3(0.62, 0.78, 1.0), 0.20);
    for (int i = 0; i < 14; i++) {
        float fi = float(i);
        float h = hash(vec2(fi, 3.7));
        float a0 = fi / 14.0 * 6.2831853 + 0.35 * sin(travel * 0.18 + fi * 1.3);
        float bend = (h - 0.5) * (1.4 + 1.2 * b_mid) + 0.5 * sin(travel * 0.25 + fi * 0.7);
        float w = 0.006 + 0.022 * r * (0.4 + h);
        float k = ribbon(p, a0, bend, w);
        float warm = ribbon(p, a0 + 0.012 + 0.010 * r, bend, w * 1.6);     // the fringe: just outside the core
        float cool = ribbon(p, a0 - 0.012 - 0.010 * r, bend, w * 1.6);
        float reach = smoothstep(0.0, 0.06, r) * exp(-r * (0.85 - 0.35 * beat)) * (0.45 + 0.55 * h);
        float glow = ribbon(p, a0, bend, w * 6.0) * 0.10;                  // the haze each one lights
        col += (vec3(1.0) * k * 0.9 + ice * glow + vec3(1.0, 0.45, 0.25) * max(warm - k, 0.0) * 0.28
                + vec3(0.25, 0.5, 1.0) * max(cool - k, 0.0) * 0.32) * reach * (0.55 + 0.9 * beat) * 1.6;
    }
    // broad, out-of-focus bands behind
    for (int i = 0; i < 4; i++) {
        float fi = float(i);
        col += ice * 0.05 * ribbon(p * 0.7, fi * 1.7 + travel * 0.05, 0.6, 0.35) * smoothstep(0.1, 0.6, r);
    }
    col += vec3(1.0) * (exp(-r * 22.0) * 1.6 + exp(-r * 5.0) * 0.25) * (0.9 + 1.2 * beat);
    vec2 g = p * 26.0;
    float gl = step(0.988, hash(floor(g) + floor(time * 5.0))) * smoothstep(0.3, 0.0, length(fract(g) - 0.5));
    col += vec3(1.0) * gl * b_hi * 1.2 * smoothstep(1.0, 0.15, r);
    return col;
}

// CONTOUR (ref: layered translucent topography): broad, smooth folds cut into bands -- violet, magenta,
// red, orange, teal -- each band darker at one side and lit at its rim, like stacked tinted glass, on a
// deep indigo ground. The kick deepens the folds, the snare turns the colours, the hats sharpen the rims.
vec3 contour_m(vec2 p) {
    float t = travel * 0.06 + time * 0.008;
    vec3 col = vec3(0.025, 0.008, 0.09);
    for (int layer = 0; layer < 2; layer++) {
        float fl = float(layer);
        float f = sflow(p * (0.55 + 0.12 * fl) + vec2(fl * 3.1, -fl * 1.7), t * (1.0 + 0.5 * beat) + fl * 2.0);
        float n = f * 14.0;
        float band = floor(n);
        float within = fract(n);
        // the hue walks slowly through the bands: violet -> magenta -> red -> orange, with teal between
        float hue = fract(f * 1.2 + fl * 0.35 + 0.08 * b_mid + t * 0.15);
        vec3 c = spectral(0.12 + 0.62 * hue);
        c = mix(c, vec3(0.03, 0.66, 0.62), smoothstep(0.55, 0.75, fract(f * 2.3 + fl * 0.5)) * 0.8);
        c = cover_tint(c, 0.12);
        // each thin band: a soft ramp, dark at its foot and lit along its upper rim (stacked tinted glass)
        float ramp = mix(0.08, 1.0, pow(within, 2.2));
        float rim = exp(-pow((1.0 - within) * (14.0 + 16.0 * b_hi), 2.0)) * 0.45;
        vec3 lay = c * ramp * 1.15 + mix(c, vec3(1.0), 0.30) * rim * 0.7;
        lay *= smoothstep(0.10, 0.30, f) * (0.45 + 0.75 * smoothstep(0.3, 0.9, f) + 0.25 * beat);
        col = layer == 0 ? max(col, lay) : max(col * 0.6, lay) + col * lay * 0.4;   // the second sheet over the first
    }
    return col;
}

// FLUTED GLASS (ref: ribbed glass): light moving behind a sheet of vertical ribs; each rib squeezes what's
// behind it, red, green and blue by different amounts, so every rib's edge splits into colour. The light
// swells with the kick, drifts with the snare; the hats flicker the grain.
vec3 behind(vec2 q) {
    vec3 c = vec3(0.0);
    vec2 a = vec2(0.35 * sin(travel * 0.35), 0.05 + 0.25 * sin(travel * 0.22));
    c += vec3(1.0, 0.97, 0.92) * exp(-pow(length((q - a) * vec2(0.9, 2.4)), 2.0) * 3.0) * (1.3 + 1.2 * beat);
    c += cover_tint(vec3(1.0, 0.40, 0.10), 0.3) * exp(-pow(length((q - a - vec2(0.55, 0.0)) * vec2(1.4, 2.0)), 2.0) * 2.2) * 0.9;
    c += cover_tint(vec3(0.05, 0.75, 0.80), 0.3) * exp(-pow(length((q - a + vec2(0.75, 0.1)) * vec2(1.2, 1.6)), 2.0) * 2.0) * 0.8;
    c += cover_tint(vec3(0.15, 0.25, 1.0), 0.3) * exp(-pow(length((q + vec2(1.3, 0.0)) * vec2(1.0, 1.4)), 2.0) * 1.5) * 0.5 * (0.6 + b_mid);
    return c;
}
vec3 fluted_m(vec2 p) {
    float ribs = 26.0;
    float x = p.x * ribs * 0.5;
    float id = floor(x);
    float u = fract(x) - 0.5;                       // across one rib
    float cx = (id + 0.5) / (ribs * 0.5);
    vec3 col;
    float sq = 0.55;
    col.r = behind(vec2(cx + u * sq * 0.86 / (ribs * 0.5) * 6.0, p.y)).r;
    col.g = behind(vec2(cx + u * sq * 1.00 / (ribs * 0.5) * 6.0, p.y)).g;
    col.b = behind(vec2(cx + u * sq * 1.16 / (ribs * 0.5) * 6.0, p.y)).b;
    col *= 0.55 + 0.45 * cos(u * 3.14159);          // each rib darker toward its edges
    col += vec3(0.9, 0.95, 1.0) * exp(-pow((abs(u) - 0.47) * 60.0, 2.0)) * 0.08 * (0.5 + length(col));
    col += (hash(p * res + floor(time * 24.0)) - 0.5) * (0.05 + 0.05 * b_hi);   // heavy grain
    return col;
}

// LIQUID (ref: liquid glass): big, smooth folds of clear liquid in the cover's colour; light gathering
// in white lines along each crest, deep colour in the troughs, a few bright specks caught inside. The kick
// swells the folds, the snare turns them, the hats sparkle the specks.
float liq(vec2 q, float t) {
    return sflow(q * 0.75, t);
}
vec3 liquid_m(vec2 p) {
    float t = travel * 0.05 + time * 0.006;
    vec2 q = p * (1.0 - 0.05 * beat);
    float h = liq(q, t);
    float e = 0.006;
    vec2 g = vec2(liq(q + vec2(e, 0.0), t) - h, liq(q + vec2(0.0, e), t) - h) / e;
    vec3 n = normalize(vec3(-g * (0.55 + 0.35 * b_mid), 1.0));
    vec3 base = cover_tint(vec3(0.16, 0.36, 0.72), 0.5);
    vec3 col = base * (0.10 + 0.75 * h * h);
    float fres = pow(1.0 - n.z, 1.5);
    col += mix(base, vec3(0.80, 0.90, 1.0), 0.55) * fres * 0.75;
    vec3 V = vec3(0.0, 0.0, 1.0);
    float s1 = pow(max(dot(reflect(-normalize(vec3(-0.4, 0.6, 0.7)), n), V), 0.0), 120.0);
    float s2 = pow(max(dot(reflect(-normalize(vec3(0.5, -0.3, 0.8)), n), V), 0.0), 30.0);
    col += vec3(1.0) * s1 * 2.4 + mix(base, vec3(1.0), 0.7) * s2 * 0.45;
    // the crests: where the surface turns over, light runs along it in a thin line
    float crest = exp(-pow(length(g) * 0.9 - 1.6, 2.0) * 3.0) * fres;
    col += vec3(0.85, 0.93, 1.0) * crest * 0.35;
    vec2 sg = p * 18.0;
    float sh = hash(floor(sg));
    float sp = step(0.986, sh) * smoothstep(0.22, 0.0, length(fract(sg) - 0.5)) * (0.5 + 0.5 * sin(time * 2.0 + sh * 40.0));
    col += vec3(1.0) * sp * (0.2 + 1.2 * b_hi) * (0.4 + fres);
    return col;
}

// YOUR IMAGE: the person's own picture, brought to life by the music. It fills the screen (cropped, never
// stretched) and breathes in depth with the kick; a slow drift moves it like a camera; the snare sends
// a heat-haze ripple through it; on the kick its colours part a touch at the edges and its light blooms;
// the hats run a glint of light across it. Nothing is drawn over it but light.
vec3 img_at(vec2 q) {
    return texture(img, clamp(q, 0.001, 0.999)).rgb;
}
vec3 image_m(vec2 p) {
    float sa = res.x / res.y;
    vec2 u = uv - 0.5;
    // cover the screen: crop the long side
    vec2 k = sa > img_aspect ? vec2(1.0, img_aspect / sa) : vec2(sa / img_aspect, 1.0);
    u *= k;
    float zoom = 1.06 + 0.035 * beat + 0.02 * sin(travel * 0.12);
    u /= zoom;
    u += 0.012 * vec2(sin(travel * 0.07), cos(travel * 0.05));                      // the slow drift
    vec2 q = vec2(u.x, -u.y) + 0.5;
    q.y = 1.0 - q.y;
    // heat haze on the snare
    vec2 haze = vec2(noise(q * 9.0 + time * 0.6), noise(q * 9.0 - time * 0.6 + 4.0)) - 0.5;
    q += haze * 0.010 * b_mid;
    // colours parting at the edges on the kick
    vec2 d = (q - 0.5) * 0.006 * beat;
    vec3 col = vec3(img_at(q + d).r, img_at(q).g, img_at(q - d).b);
    // bloom: the bright parts spread
    vec3 b = vec3(0.0);
    for (int i = 0; i < 8; i++) {
        float a = float(i) * 0.785398;
        vec3 s4 = img_at(q + vec2(cos(a), sin(a)) * 0.012);
        b += max(s4 - 0.55, 0.0);
    }
    col += b / 8.0 * (0.8 + 1.8 * beat);
    // a glint of light crossing on the hats
    float sweep = fract(time * 0.07 + travel * 0.02);
    float band = exp(-pow((uv.x + uv.y * 0.35 - sweep * 1.8 + 0.3) * 9.0, 2.0));
    col += col * band * (0.05 + 0.5 * b_hi);
    return col * (0.92 + 0.12 * beat);
}

vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }

vec3 mirror_scene(vec2 p, int m) {
    float n = m == 4 ? 12.0 : (m == 3 ? 6.0 : 8.0);
    vec3 col = grown(folded(p, n), m) * 1.25;
    float r = length(p);
    col *= 0.45 + 0.55 * smoothstep(0.1, 0.7, r);   // the field steps back round the flower
    return col + flower(p, n);
}

void main() {
    vec2 p = (uv - 0.5) * vec2(res.x / res.y, 1.0) * 2.0;
    vec3 col;
    if (sym > 1) {                                  // the kaleidoscope, over any scene
        float seg = TAU / float(sym);
        float a = mod(atan(p.y, p.x), seg);
        a = abs(a - seg * 0.5);
        p = length(p) * vec2(cos(a), sin(a));
    }
    if (mode == 30) col = image_m(p);
    else if (mode == 20) col = dots_m(p);
    else if (mode == 21) col = prism_m(p);
    else if (mode == 22) col = contour_m(p);
    else if (mode == 23) col = fluted_m(p);
    else if (mode == 24) col = liquid_m(p);
    else if (mode == 6) col = halo_m(p);
    else if (mode == 7) col = warp_m(p);
    else if (mode == 8) col = ripple_m(p);
    else if (mode == 9) col = storm_m(p);
    else if (mode >= 2) col = mirror_scene(p, mode);
    else col = mode == 1 ? nebula(p, time * 0.04) : aurora(p, time * 0.25);
    col *= 1.0 - 0.35 * dot(uv - 0.5, uv - 0.5) * 2.0;
    col += (hash(uv * res + time) - 0.5) * 0.012;
    col = mode == 30 ? col : (mode >= 6 ? aces(col * 1.35) : col / (1.0 + col * 0.25));   // the scenes: a film's curve
    if (mono == 1) col = vec3(dot(col, vec3(0.299, 0.587, 0.114))) * vec3(0.96, 0.98, 1.0);
    frag = vec4(pow(clamp(col, 0.0, 1.0), vec3(0.95)), 1.0);
}"""


def _vec(c):
    c = QColor(c)
    return QVector3D(c.redF(), c.greenF(), c.blueF())


class FluidRenderer:
    """The shader, an off-screen OpenGL context and a buffer to draw into."""

    def __init__(self):
        self.ok = False
        self._fbo = None
        try:
            from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject
            fmt = QSurfaceFormat()
            fmt.setVersion(3, 3)
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
            self.ctx = QOpenGLContext()
            self.ctx.setFormat(fmt)
            if not self.ctx.create():
                return
            self.surface = QOffscreenSurface()
            self.surface.setFormat(self.ctx.format())
            self.surface.create()
            if not self.ctx.makeCurrent(self.surface):
                return
            self.prog = QOpenGLShaderProgram()
            if not (self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERT)
                    and self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAG)
                    and self.prog.link()):
                logger.warning("The background's shader didn't build: %s", self.prog.log())
                return
            self.vao = QOpenGLVertexArrayObject()
            self.vao.create()
            self.ctx.doneCurrent()
            self.ok = True
        except Exception:   # noqa: BLE001 -- the drifting cover stands in
            logger.warning("No OpenGL for the background", exc_info=True)

    def set_image(self, qimage):
        """The picture for "Your image" (a QImage), or None."""
        self._pending_image = qimage
        self._image_aspect = (qimage.width() / max(1, qimage.height())) if qimage is not None else 1.0

    def _upload_image(self):
        from PySide6.QtOpenGL import QOpenGLTexture
        img = getattr(self, "_pending_image", None)
        if img is None:
            return
        self._pending_image = None
        if getattr(self, "_tex", None) is not None:
            self._tex.destroy()
        # at most 2048 px on its long side: plenty for a background, kind to the graphics card
        if max(img.width(), img.height()) > 2048:
            from PySide6.QtCore import Qt
            img = img.scaled(2048, 2048, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._tex = QOpenGLTexture(img.mirrored(False, True))
        self._tex.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
        self._tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        self._tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

    def render(self, mode, t, beat, energy, pal, w, h, mid=0.0, hi=0.0, travel=0.0, mono=False,
               storm=0.0, bolt=0.0, ripples=(), pace=1.0, sym=0, density=1.0, clear=0.0, hq=0.0):
        """`ripples`: up to eight (age in seconds, strength) -- the rings a
        beat sent out across the water."""
        """One frame, w x h, as a QImage (None if it can't be drawn)."""
        if not self.ok:
            return None
        from PySide6.QtOpenGL import QOpenGLFramebufferObject
        if not self.ctx.makeCurrent(self.surface):
            return None
        try:
            if self._fbo is None or self._fbo.width() != w or self._fbo.height() != h:
                self._fbo = QOpenGLFramebufferObject(w, h)
            self._fbo.bind()
            f = self.ctx.functions()
            f.glViewport(0, 0, w, h)
            p = self.prog
            p.bind()
            p.setUniformValue1f("time", float(t))
            p.setUniformValue1f("beat", float(beat))
            p.setUniformValue1f("energy", float(energy))
            p.setUniformValue1f("b_mid", float(mid))
            p.setUniformValue1f("b_hi", float(hi))
            p.setUniformValue1f("travel", float(travel))
            p.setUniformValue1f("pace", float(pace))
            p.setUniformValue1i("mode", _MODE_NO.get(mode, 0))
            p.setUniformValue1i("mono", 1 if mono else 0)
            p.setUniformValue1i("sym", int(sym))
            p.setUniformValue1f("density", float(density))
            p.setUniformValue1f("clear", float(clear))
            p.setUniformValue1f("hq", float(hq))
            p.setUniformValue1f("storm", float(storm))
            p.setUniformValue1f("bolt", float(bolt))
            rs = (list(ripples) + [(99.0, 0.0)] * 8)[:8]
            p.setUniformValue("w_age0", QVector4D(*[a for a, _m in rs[:4]]))
            p.setUniformValue("w_age1", QVector4D(*[a for a, _m in rs[4:]]))
            p.setUniformValue("w_amp0", QVector4D(*[m for _a, m in rs[:4]]))
            p.setUniformValue("w_amp1", QVector4D(*[m for _a, m in rs[4:]]))
            p.setUniformValue("res", QVector2D(w, h))
            cover = pal.get("cover")             # the cover's own colours, where they've been read
            if cover:
                deep, main, second, hot = cover["deep"], cover["main"], cover["second"], cover["hot"]
            else:
                deep, main, second, hot = pal["deep"], pal["base"], pal["glow"], QColor(pal["accent"]).lighter(115)
            p.setUniformValue("c_deep", _vec(deep))
            p.setUniformValue("c_main", _vec(main))
            p.setUniformValue("c_second", _vec(second))
            p.setUniformValue("c_hot", _vec(hot))
            if mode == "image":
                self._upload_image()
                if getattr(self, "_tex", None) is None:
                    return None
                self._tex.bind(0)
                p.setUniformValue1i("img", 0)
                p.setUniformValue1f("img_aspect", float(getattr(self, "_image_aspect", 1.0)))
            self.vao.bind()
            f.glDrawArrays(0x0004, 0, 3)            # GL_TRIANGLES: one triangle covers the screen
            self.vao.release()
            img = self._fbo.toImage()
            self._fbo.release()
            return img
        finally:
            self.ctx.doneCurrent()


class BeatFollower:
    """The beat, from the bass, as the speed the fluid flows at -- nothing
    flashes or jumps. The bass's envelope (quick to rise, slower to fall)
    is watched for sudden rises (a kick, a dhol hit) against its own recent
    run of rises. Between beats the flow drifts slowly; each beat kicks it
    forward -- a punch of speed, several times the drift, that's gone again
    in about a quarter of a second -- so it lurches on the beat and rests
    in between, and a ballad just drifts."""

    def __init__(self):
        self.fast = self.prev = self.slow = self.avg = 0.0
        self.pulse = 0.0
        self.mid = _Onset(16.0)          # the snare and the voice: shorter flashes
        self.hi = _Onset(24.0)           # the hi-hats: the quickest
        self.energy = 0.0
        self.rate = 0.6
        self._n = 0
        self._t = time.monotonic()
        self._dt = 0.016

    def update(self, bass, loud, mid=None, hi=None):
        now = time.monotonic()
        dt = self._dt = max(0.001, min(0.1, now - self._t))
        self._t = now
        self._n += 1
        self.fast += (bass - self.fast) * (0.65 if bass > self.fast else 0.18)
        self.slow += (bass - self.slow) * min(1.0, dt * 0.8)
        flux = max(0.0, self.fast - self.prev)
        self.prev = self.fast
        onset = flux / (self.slow + 0.01)
        self.avg += (onset - self.avg) * min(1.0, dt * 1.5)
        hit = max(0.0, min(1.0, (onset - self.avg * 1.6 - 0.02) * 5.0)) if self._n > 20 else 0.0
        self.pulse = max(self.pulse * math.exp(-dt * 13.0), hit)
        self.energy += (min(1.0, loud * 3.2) - self.energy) * min(1.0, dt * 1.0)
        if mid is not None:
            self.mid.update(mid, dt, self._n > 20)
            self.hi.update(hi or 0.0, dt, self._n > 20)
        return self.pulse, self.energy

    def speed(self, dt=None):
        dt = self._dt if dt is None else dt
        want = 0.28 + 0.22 * self.energy + 5.2 * self.pulse     # each beat a punch, ~5x the drift
        k = 40.0 if want > self.rate else 11.0          # punches in at once, settles back quickly
        self.rate += (want - self.rate) * min(1.0, dt * k)
        return self.rate


class _Onset:
    """A sudden rise in one band, against its own recent run of rises, as a
    0..1 flash that fades at `decay` per second."""

    def __init__(self, decay):
        self.decay = decay
        self.fast = self.prev = self.slow = self.avg = 0.0
        self.pulse = 0.0

    def update(self, level, dt, ready=True):
        self.fast += (level - self.fast) * (0.7 if level > self.fast else 0.25)
        self.slow += (level - self.slow) * min(1.0, dt * 0.8)
        flux = max(0.0, self.fast - self.prev)
        self.prev = self.fast
        onset = flux / (self.slow + 1e-4)
        self.avg += (onset - self.avg) * min(1.0, dt * 1.5)
        hit = max(0.0, min(1.0, (onset - self.avg * 1.5 - 0.02) * 4.0)) if ready else 0.0
        self.pulse = max(self.pulse * math.exp(-dt * self.decay), hit)
        return self.pulse
