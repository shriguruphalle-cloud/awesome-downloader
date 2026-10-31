"""Mixing like a DJ: what a song sounds like at its two ends, and how to get
from one song into the next (the phone app's Mixer.kt, listening with FFmpeg).

A song's head (its first 45 s) and tail (its last 50 s) are decoded into three
curves at ~86 frames a second: loudness, low end (under 150 Hz: kicks and bass)
and onsets (how sharply the high end jumps: drums, claps, hats). From them: the
tempo (the onsets' autocorrelation, 70-180 BPM leaning to the common range, then
fine-tuned to a 50th of a frame), the beat grid, the bar grid (of the four beats,
the one with the most low end under it), where the sound begins and ends, and
how punchy the body is.

Then, when the tempos agree (or one is half or double the other) and both beats
are clear, a Plan: the next song comes in under this one's last bars, in step,
its bass held back; halfway, the bass changes over on a bar line, and this song
fades over the bars it has left. Nothing of either song is passed over but
silence. With no plan, the plain blend, from sound to sound.

No numpy in the app: FFmpeg does the filtering, and the sums are run a block at
a time in C (`sum(map(...))`), so this thread never holds the sound card's up.
"""
import math
import operator
import subprocess
import threading
import time
from array import array
from collections import OrderedDict, namedtuple

from app.logging_setup import get_logger

logger = get_logger("mixer")

HEAD_MS = 45000
TAIL_MS = 50000
MAX_SILENCE_MS = 15000
LISTEN_RATE = 22050
HOP = 256                      # 86.13 frames a second at that rate
LISTEN_TIMEOUT_S = 25          # a stalled stream: give up
MIN_ONSETS = 0.02              # less jump in the high end than this, on average, isn't a beat (a steady tone's ripple)
_KEEP = 300

Plan = namedtuple("Plan", "cur_key next_key start_at swap_at end_at in_ms speed")
Plan.__doc__ = """How the next song comes in: `start_at` (this song's ms) is when it starts, at `in_ms` of its own, at
`speed`; `swap_at` is the bass changing over; `end_at` is this song's last sound."""


class Profile(namedtuple("Profile", "dur_ms bpm confidence head_bar tail_bar sound_start sound_end punch")):
    """A song's ends, as heard. Times in ms from its start; bpm 0 when no steady beat was found."""
    __slots__ = ()

    @property
    def beat_ms(self):
        return 60000.0 / self.bpm if self.bpm > 0 else 500.0

    @property
    def steady(self):
        return self.bpm > 0 and self.confidence >= 0.3


class _Env:
    """A stretch of the song as frames (~11.6 ms): loudness, low end and onsets, with the time of its first frame."""
    __slots__ = ("start_ms", "frame_ms", "energy", "low", "flux")

    def __init__(self, start_ms, energy, low, flux):
        self.start_ms = start_ms
        self.frame_ms = HOP * 1000.0 / LISTEN_RATE
        self.energy, self.low, self.flux = energy, low, flux


_profiles = OrderedDict()
_lock = threading.Lock()


def known(key):
    """The song's profile if it's been listened to."""
    with _lock:
        return _profiles.get(key)


# ---- listening ----
def _ffmpeg():
    from app.core import music_sources
    return music_sources._ffmpeg()


def _duration_of(src):
    """The length FFmpeg reads from the file's header (ms), or 0."""
    import re
    try:
        p = subprocess.run([_ffmpeg(), "-nostdin", "-hide_banner", "-i", src], capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return 0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr.decode("utf-8", "replace"))
    if not m:
        return 0
    return int((int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) * 1000)


def analyse(key, src, dur_hint_ms=0):
    """The song at `src` (a file or a stream link), both ends; None when it can't be read. Blocking."""
    have = known(key)
    if have is not None:
        return have
    try:
        dur = _duration_of(src) or int(dur_hint_ms or 0)
        if dur < 40000:
            return None
        # a short song: all of it, once (its head alone would make its end seem to come early)
        tail_from = max(0, dur - TAIL_MS)
        whole = tail_from <= HEAD_MS
        head = _envelope(src, 0, dur if whole else HEAD_MS)
        if head is None:
            return None
        tail = head if whole else _envelope(src, tail_from, dur - tail_from)
        if tail is None:
            return None
        prof = _profile_of(dur, head, tail)
    except Exception:   # noqa: BLE001 -- the plain blend does without
        logger.warning("Couldn't listen to a song", exc_info=True)
        return None
    with _lock:
        _profiles[key] = prof
        while len(_profiles) > _KEEP:
            _profiles.popitem(last=False)
    logger.info("Listened to a song: %.1f BPM (sure %.2f), sound %.1f-%.1f s of %.1f s", prof.bpm, prof.confidence,
                prof.sound_start / 1000.0, prof.sound_end / 1000.0, prof.dur_ms / 1000.0)
    return prof


def _envelope(src, from_ms, len_ms):
    """FFmpeg gives two channels at 22 kHz: the song in mono, and the same under 150 Hz."""
    cmd = [_ffmpeg(), "-nostdin", "-v", "error"]
    if src.startswith(("http://", "https://")):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                "-reconnect_delay_max", "4"]
    if from_ms > 0:
        cmd += ["-ss", "%.3f" % (from_ms / 1000.0)]
    cmd += ["-t", "%.3f" % (len_ms / 1000.0), "-i", src, "-vn", "-filter_complex",
            "[0:a:0]aresample=%d,aformat=channel_layouts=mono,asplit[a][b];[b]lowpass=f=150[l];"
            "[a][l]join=inputs=2:channel_layout=stereo[o]" % LISTEN_RATE,
            "-map", "[o]", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    timer = threading.Timer(LISTEN_TIMEOUT_S, proc.kill)
    timer.start()
    started = time.monotonic()
    try:
        raw = proc.stdout.read()
        proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()
    if time.monotonic() - started >= LISTEN_TIMEOUT_S:
        return None
    pcm = array("h")
    pcm.frombytes(raw[:len(raw) - len(raw) % 4])
    mono, bass = pcm[::2], pcm[1::2]
    frames = len(mono) // HOP
    if frames < 300:
        return None
    energy, low, highs = [], [], []
    mul, sub = operator.mul, operator.sub
    scale = 1.0 / (HOP * 32768.0 * 32768.0)
    for i in range(frames):
        a = i * HOP
        block = mono[a:a + HOP]
        lo = bass[a:a + HOP]
        energy.append(sum(map(mul, block, block)) * scale)
        low.append(sum(map(mul, lo, lo)) * scale)
        d = list(map(sub, block[1:], block))
        highs.append(sum(map(mul, d, d)) * scale)
        time.sleep(0)                          # let the sound card's thread in
    logs = [math.log(h + 1e-9) for h in highs]
    flux = [0.0] + [max(0.0, logs[i] - logs[i - 1]) for i in range(1, len(logs))]
    return _Env(float(from_ms), energy, low, flux)


# ---- what it heard ----
def _median(values):
    v = sorted(values)
    return v[len(v) // 2] if v else 0.0


def _profile_of(dur, head, tail):
    bpm_h, conf_h = _tempo(head)
    bpm_t, conf_t = _tempo(tail)
    bpm, conf = (bpm_t, conf_t) if conf_t >= conf_h else (bpm_h, conf_h)
    period = 60000.0 / bpm if bpm > 0 else 500.0
    h_bar = _grid(head, period)
    t_bar = _grid(tail, period)
    hb = _bars(head, h_bar, period)
    tb = _bars(tail, t_bar, period)
    body_bars = hb[len(hb) // 3:] + tb[:len(tb) // 2]
    body = _median([b[0] for b in body_bars])
    punch = _median([b[1] for b in body_bars])
    # silence is far below the song (about 35 dB): a soft opening or a long fade is still the song
    quiet = body * 0.0003
    first = next((i for i, e in enumerate(head.energy) if e > quiet), 0)
    sound_start = head.start_ms + first * head.frame_ms
    if sound_start > MAX_SILENCE_MS:
        sound_start = 0.0
    last = next((i for i in range(len(tail.energy) - 1, -1, -1) if tail.energy[i] > quiet), len(tail.energy) - 1)
    # the frame after the last one with sound in it, so a fade's very end isn't cut
    sound_end = min(tail.start_ms + (last + 1) * tail.frame_ms, float(dur))
    # silence runs seconds, not half a minute: a reading past that isn't believed, and the song plays to its end
    if dur - sound_end > MAX_SILENCE_MS:
        sound_end = float(dur)
    return Profile(dur, bpm, conf, h_bar, t_bar, sound_start, sound_end, punch)


def _tempo(e):
    """The tempo from the onsets' autocorrelation (70-180 BPM, leaning to ~120), fine-tuned; and how sure (0..1)."""
    f = e.flux
    mean = sum(f) / len(f)
    if mean < MIN_ONSETS:
        return 0.0, 0.0                        # a held sound: nothing in it hits at all
    x = array("d", (max(0.0, v - mean) for v in f))
    r0 = sum(map(operator.mul, x, x))
    if r0 <= 0:
        return 0.0, 0.0
    fps = 1000.0 / e.frame_ms
    lo = int(60.0 / 180 * fps)
    hi = int(60.0 / 70 * fps) + 1
    if hi >= len(x) // 2:
        return 0.0, 0.0
    r = [0.0] * (hi + 2)
    for lag in range(lo - 1, hi + 2):
        r[lag] = sum(map(operator.mul, x[lag:], x))
    best, best_score = -1, 0.0
    for lag in range(lo, hi + 1):
        bpm = 60.0 * fps / lag
        w = math.exp(-0.5 * (math.log(bpm / 120.0) / math.log(2.0) / 0.9) ** 2)
        if r[lag] * w > best_score:
            best_score, best = r[lag] * w, lag
    if best < 0:
        return 0.0, 0.0
    a, b, c = r[best - 1], r[best], r[best + 1]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c != 0 else 0.0
    lag0 = best + max(-0.5, min(0.5, shift))
    best_p, best_s = lag0, -1.0
    n = len(f)
    p = lag0 - 0.6
    while p < lag0 + 0.6:
        steps = int(n / p) + 1
        top = 0.0
        o = 0.0
        while o < p:
            s = sum(f[int(o + j * p)] for j in range(steps) if o + j * p < n)
            if s > top:
                top = s
            o += 0.5
        if top > best_s:
            best_s, best_p = top, p
        p += 0.02
        time.sleep(0)
    return 60.0 * fps / best_p, max(0.0, min(1.0, b / r0))


def _grid(e, period_ms):
    """The bar line (ms): the beat's place, and of the four beats, the one with the most low end under it."""
    per = period_ms / e.frame_ms
    flux, low = e.flux, e.low
    n = len(flux)
    best, best_sum = 0.0, -1.0
    o = 0.0
    while o < per:
        s, k = 0.0, o
        while k < n:
            s += flux[int(k)]
            k += per
        if s > best_sum:
            best_sum, best = s, o
        o += 0.5
    # sharp off-beat hats can out-score the beat itself: the kick is where the low end is
    on, off = 0.0, 0.0
    k = best
    while k + per / 2 < n:
        on += low[int(k)]
        off += low[int(k + per / 2)]
        k += per
    if off > on * 1.25:
        best = (best + per / 2) % per
    best_k, best_low = 0, -1.0
    for kk in range(4):
        s, k = 0.0, best + kk * per
        while k < n:
            s += low[int(k)] + flux[int(k)] * 1e-3
            k += per * 4
        if s > best_low:
            best_low, best_k = s, kk
    return e.start_ms + best * e.frame_ms + best_k * period_ms


def _bars(e, bar_ms, period_ms):
    """Each whole bar's (loudness, punch)."""
    out = []
    t = bar_ms
    while t < e.start_ms:
        t += period_ms * 4
    while True:
        a = int((t - e.start_ms) / e.frame_ms)
        b = int((t + period_ms * 4 - e.start_ms) / e.frame_ms)
        if b > len(e.energy) or a < 0:
            break
        count = max(1, b - a)
        out.append((sum(e.energy[a:b]) / count, sum(e.flux[a:b]) / count))
        t += period_ms * 4
    return out


# ---- choosing and planning ----
def fit(a, b):
    """How well `b` follows `a` (lower is better): tempo, and no big drop in punch."""
    d = 0.0
    if a.steady and b.steady:
        r = min(abs(math.log(a.bpm / (b.bpm * k))) for k in (1.0, 2.0, 0.5))
        d += 0.0 if r <= 0.06 else min(2.0, r * 6)
    else:
        d += 0.6
    d += abs(math.log(max(1e-3, b.punch) / max(1e-3, a.punch))) * 1.2
    if b.punch < a.punch * 0.6:
        d += 1.0
    return d


def _snap_up(t, phase, period):
    return phase + math.ceil((t - phase) / period) * period if period > 0 else t


def plan(cur_key, a, next_key, b, not_before_ms, blend_ms):
    """The next song in step under this one's last bars, about `blend_ms` long and starting no sooner than
    `not_before_ms`; None unless both beats are clear and agree, and there are bars left to do it in."""
    if not a.steady or not b.steady:
        return None
    half = next((k for k in (1.0, 2.0, 0.5) if abs(a.bpm / (b.bpm * k) - 1) <= 0.06), None)
    if half is None:
        return None
    speed = a.bpm / (b.bpm * half)
    pa = a.beat_ms
    bars = max(2.0, min(4.0, math.floor(blend_ms / pa / 4))) * 4 * pa
    # from its first sound (never later), started early by what comes before its first bar line, so the bars agree
    in_ms = max(0.0, b.sound_start - 20)
    lead = (_snap_up(b.sound_start, b.head_bar, b.beat_ms * 4) - in_ms) / speed
    line = _snap_up(a.sound_end - bars, a.tail_bar, pa * 4)
    if line - lead < not_before_ms:
        line = _snap_up(not_before_ms + lead, a.tail_bar, pa * 4)
    left = math.floor((a.sound_end - line) / pa)
    if left < 4:
        return None
    swap = line + max(2.0, math.floor(left / 8) * 4) * pa
    return Plan(cur_key, next_key, int(line - lead), int(swap), int(a.sound_end), int(in_ms), speed)


# ---- doing it, in FFmpeg ----
_SWAP_S = 0.025                # the bass changes over in this long (a mixer's low EQ turned, not a click)
_EASE_S = 0.05                 # the seam where the next song goes back to its own tempo
_LIFT_S = 0.4                  # a song's loudness correction glides over this long
BASS_HZ = 180


def _bass_change(label, out, at_s, cut_first):
    """`label` with its bass cut below 180 Hz until `at_s` (cut_first), or from it on."""
    fade_dry, fade_cut = ("in", "out") if cut_first else ("out", "in")
    return ("[%s]asplit[%sd][%sw];[%sd]afade=t=%s:st=%.4f:d=%.3f[%se];[%sw]highpass=f=%d,afade=t=%s:st=%.4f:d=%.3f[%sf];"
            "[%se][%sf]amix=inputs=2:normalize=0[%s]" % (
                label, out, out, out, fade_dry, at_s, _SWAP_S, out, out, BASS_HZ, fade_cut, at_s, _SWAP_S, out,
                out, out, out))


def graph(plan_, at_ms, norm, lift=1.0):
    """The filter graph of a mix: [0] is this song from `at_ms`, [1] the next from the plan's way in; the output
    [x] starts at this song's `at_ms`. `lift`: this song's level against the next's once the next is the one
    whose loudness correction is on the sound card (1: no change)."""
    p = plan_
    start = (p.start_at - at_ms) / 1000.0        # when the next song starts, on this graph's clock
    swap = (p.swap_at - at_ms) / 1000.0
    end = (p.end_at - at_ms) / 1000.0
    parts = ["[0:a:0]%s,atrim=end=%.4f,asetpts=PTS-STARTPTS[a0]" % (norm, end)]
    a_in = "a0"
    if abs(lift - 1.0) > 0.01:
        parts.append("[a0]volume='1+%.4f*min(1,max(0,(t-%.4f)/%.2f))':eval=frame[a1]" % (lift - 1.0, start, _LIFT_S))
        a_in = "a1"
    parts.append(_bass_change(a_in, "a2", swap, cut_first=False))
    parts.append("[a2]afade=t=out:st=%.4f:d=%.4f:curve=hsin[a]" % (swap, max(0.05, end - swap)))

    parts.append("[1:a:0]%s,asetpts=PTS-STARTPTS[b0]" % norm)
    b_in = "b0"
    if abs(p.speed - 1.0) > 0.002:
        # at its tempo while the two are heard together, then its own again (the phone eases back; a stretch that
        # stays would leave the song's clock wrong for the rest of it)
        length = (p.end_at - p.start_at) / 1000.0 * p.speed       # of the song's own time
        parts.append("[b0]asplit[bf][bs];[bf]atrim=end=%.4f,asetpts=PTS-STARTPTS,atempo=%.5f[b1];"
                     "[bs]atrim=start=%.4f,asetpts=PTS-STARTPTS[b2];[b1][b2]acrossfade=d=%.3f:c1=tri:c2=tri[b3]" % (
                         length + _EASE_S * p.speed, p.speed, length, _EASE_S))
        b_in = "b3"
    parts.append(_bass_change(b_in, "b4", swap - start, cut_first=True))
    parts.append("[b4]afade=t=in:st=0:d=%.4f:curve=hsin,adelay=delays=%dS:all=1[b]" % (
        max(0.05, swap - start), round(start * 48000)))
    # both at full at the swap: the limiter keeps the two from clipping, at no delay
    parts.append("[a][b]amix=inputs=2:normalize=0:duration=longest:dropout_transition=0,"
                 "alimiter=limit=0.95:attack=4:release=80:level=disabled:latency=1[x]")
    return ";".join(parts)
