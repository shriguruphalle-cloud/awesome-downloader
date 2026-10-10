"""The full-screen player's living background: a fluid animation in the
playing cover's colours, drawn by the graphics card, that breathes with the
music's beat. Two looks:

  "aurora" -- liquid aurora: soft bands of the cover's colours folding
              slowly over each other (the default)
  "nebula" -- electric nebula: broad clouds with glowing filaments along
              their folds, a fringe of colour, stars
  "still"  -- no animation: the cover, softened, behind the glass
              (drawn by the player itself, not here)

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

from PySide6.QtGui import QColor, QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector2D, QVector3D

from app.logging_setup import get_logger

logger = get_logger("music_backdrop")

MODES = (("aurora", "Liquid aurora"), ("nebula", "Electric nebula"), ("still", "Still cover"))

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
uniform int mode;         // 0 aurora, 1 nebula
uniform vec2 res;
uniform vec3 c_deep, c_main, c_second, c_hot;

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

void main() {
    vec2 p = (uv - 0.5) * vec2(res.x / res.y, 1.0) * 2.0;
    vec3 col = mode == 1 ? nebula(p, time * 0.04) : aurora(p, time * 0.25);
    col *= 1.0 - 0.35 * dot(uv - 0.5, uv - 0.5) * 2.0;
    col += (hash(uv * res + time) - 0.5) * 0.012;
    col = col / (1.0 + col * 0.25);
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

    def render(self, mode, t, beat, energy, pal, w, h):
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
            p.setUniformValue1i("mode", 1 if mode == "nebula" else 0)
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
        self.energy = 0.0
        self.rate = 0.6
        self._n = 0
        self._t = time.monotonic()
        self._dt = 0.016

    def update(self, bass, loud):
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
        return self.pulse, self.energy

    def speed(self, dt=None):
        dt = self._dt if dt is None else dt
        want = 0.28 + 0.22 * self.energy + 5.2 * self.pulse     # each beat a punch, ~5x the drift
        k = 40.0 if want > self.rate else 11.0          # punches in at once, settles back quickly
        self.rate += (want - self.rate) * min(1.0, dt * k)
        return self.rate
