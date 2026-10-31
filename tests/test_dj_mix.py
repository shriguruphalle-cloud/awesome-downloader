"""Mix like a DJ: the listening finds a made-up song's tempo, bars and silence;
the plan only exists where two steady beats agree; and the mix itself, played
through the real engine's decode, lands the next song's kicks on this one's,
changes the bass over at the planned moment, cuts the silence, and leaves the
next song at its own tempo and in the right place afterwards. Tones and kicks
are generated with FFmpeg; no sound card is needed for the checks on the PCM."""
import _support
from _support import check, qapp, settle

import math
import os
import subprocess
import tempfile
import time
from array import array

from PySide6.QtCore import QUrl

qapp()
from app.core import mixer, music_sources
from ui_qt import audio_engine
from ui_qt.audio_engine import Engine, _Decode, _Feed, _bytes

FF = music_sources._ffmpeg()
tmp = tempfile.mkdtemp(prefix="awd-dj-", dir=_support.STATE_DIR)


def song(name, bpm, seconds, pad_in=0.0, pad_out=0.0, tone=330):
    """Four-on-the-floor kicks, with a stronger first beat of the bar, and a hat between beats, over a soft tone."""
    beat = 60.0 / bpm
    kick = "sin(2*PI*55*t)*exp(-14*mod(t,{b}))*(0.55+0.45*lt(mod(t,{bar}),{b}))".format(b=beat, bar=beat * 4)
    hat = "0.5*random(0)*exp(-90*mod(t+{h},{b}))".format(h=beat / 2, b=beat)
    expr = "0.5*({k})+0.2*({h})+0.08*sin(2*PI*{f}*t)".format(k=kick, h=hat, f=tone)
    expr = "if(lt(t,{a})+gt(t,{z}),0,{e})".format(a=pad_in, z=seconds - pad_out, e=expr).replace("mod(t,", "mod(t-%f," % pad_in)
    path = os.path.join(tmp, name + ".flac")
    subprocess.run([FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-t", str(seconds), "-i",
                    "aevalsrc=%s:s=44100:c=stereo" % expr.replace(",", "\\,"), path], check=True)
    return path


def pcm_of(dec, timeout=60):
    """Everything a decode produces."""
    out = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = dec.take(1 << 20)
        out += chunk
        if not chunk:
            if dec.eof and dec.buffered() == 0:
                break
            time.sleep(0.01)
    return bytes(out)


def bands(pcm, win_ms=25, low=False):
    """RMS in windows, of the left channel; `low`: under ~120 Hz (a cascade of box filters)."""
    a = array("h")
    a.frombytes(pcm[:len(pcm) - len(pcm) % 4])
    x = a[::2]
    if low:
        n = 200                                # at 48 kHz, nulls at 240 Hz
        total, smooth = 0, []
        for i, v in enumerate(x):
            total += v
            if i >= n:
                total -= x[i - n]
            smooth.append(total / n)
        x = smooth
    w = int(48 * win_ms)
    return [math.sqrt(sum(v * v for v in x[i:i + w]) / w) for i in range(0, len(x) - w, w)]


def kicks_in(pcm, beat_ms, win):
    """When the low end jumps up (each kick's start), a beat's most of a beat apart."""
    fine = bands(pcm, win_ms=win, low=True)
    back = max(1, int(40 / win))
    out = []
    for i in range(back, len(fine)):
        if fine[i] > 1500 and fine[i] > fine[i - back] * 1.7 and (not out or i * win - out[-1] > beat_ms * 0.7):
            out.append(i * win)
    return out


# ---- what it hears ----
t0 = time.time()
a_path = song("a", 124, 70, pad_out=4.0)
b_path = song("b", 120, 70, pad_in=2.5, tone=440)
c_path = song("c", 90, 70)                   # a tempo the first doesn't agree with
drone_path = os.path.join(tmp, "drone.flac")
subprocess.run([FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-t", "70", "-i",
                "sine=frequency=220:sample_rate=44100", "-af", "volume=0.3", drone_path], check=True)
print("made four songs in %.1f s" % (time.time() - t0))

t0 = time.time()
a = mixer.analyse("a", a_path)
took = time.time() - t0
check(a is not None, "couldn't listen to a generated file")
print("listened in %.2f s: %.1f BPM, sure %.2f, sound %.0f-%.0f ms, bar at %.0f, %.0f" % (
    took, a.bpm, a.confidence, a.sound_start, a.sound_end, a.head_bar, a.tail_bar))
check(took < 15, "listening took %.1f s" % took)
check(abs(a.bpm - 124) < 1.0, "tempo %.2f, should be 124" % a.bpm)
check(a.steady, "a steady beat wasn't called steady (%.2f)" % a.confidence)
check(abs(a.sound_end - 66000) < 150, "the sound ends at 66 s, heard %.0f" % a.sound_end)
beat = 60000.0 / 124
bar_err = (a.head_bar % (beat * 4))
check(min(bar_err, beat * 4 - bar_err) < 60, "the bar line is %.0f ms off the strong beat" % bar_err)

b = mixer.analyse("b", b_path)
check(abs(b.bpm - 120) < 1.0, "tempo %.2f, should be 120" % b.bpm)
check(abs(b.sound_start - 2500) < 80, "the sound starts at 2.5 s, heard %.0f" % b.sound_start)
c = mixer.analyse("c", c_path)
check(abs(c.bpm - 90) < 1.5 or abs(c.bpm - 180) < 3, "tempo %.2f, should be 90" % c.bpm)
drone = mixer.analyse("drone", drone_path)
check(drone is None or not drone.steady, "a steady tone was given a steady beat: %s" % (drone,))
print("the other three: %.1f BPM from %.0f ms; %.1f BPM; the drone %s" % (
    b.bpm, b.sound_start, c.bpm, "not read" if drone is None else "not steady"))
check(mixer.analyse("gone", os.path.join(tmp, "nothing.flac")) is None, "a missing file should come back as None")
check(mixer.known("a") is a, "a song heard once should be remembered")

# ---- the plan ----
plan = mixer.plan("a", a, "b", b, 40000, 8000)
check(plan is not None, "no plan for 124 into 120")
print("plan: start %d, swap %d, end %d, in at %d, x%.3f" % plan[2:])
check(abs(plan.speed - a.bpm / b.bpm) < 1e-6, "the next song should run at this one's tempo")
check(plan.end_at == int(a.sound_end), "this song should be heard to its last sound")
check(plan.start_at < plan.swap_at < plan.end_at, "the order of a mix: %s" % (plan,))
check(plan.in_ms <= b.sound_start, "the next song is heard from its first sound")
bars_off = ((plan.swap_at - a.tail_bar) / (a.beat_ms)) % 1
check(min(bars_off, 1 - bars_off) < 0.02, "the swap should fall on a beat (%.3f)" % bars_off)
check(mixer.plan("a", a, "c", c, 40000, 8000) is None, "124 and 90 don't agree: no plan")
if drone is not None:
    check(mixer.plan("a", a, "d", drone, 40000, 8000) is None, "no steady beat: no plan")
check(mixer.plan("a", a, "b", b, int(a.sound_end) - 500, 8000) is None, "no bars left: no plan")
half = a._replace(bpm=240.0)
check(mixer.plan("h", half, "b", b, 40000, 8000) is not None, "double the tempo agrees")
check(mixer.fit(a, b) < mixer.fit(a, c), "124 should follow on from 120 better than from 90")

# ---- the mix, as the engine's decode makes it ----
AT = plan.start_at - 3000
norm = "aresample=48000,aformat=channel_layouts=stereo"
dec = _Decode([(a_path, AT), (b_path, plan.in_ms)], mix=lambda n: mixer.graph(plan, AT, n))
pcm = pcm_of(dec)
check(dec.rc == 0, "FFmpeg refused the mix's graph: %s" % dec.err[-3:])
clean = pcm_of(_Decode([(a_path, AT)]))
loud = bands(pcm)
low = bands(pcm, low=True)
print("the mix: %.1f s of sound" % (len(pcm) / audio_engine.BPMS / 1000.0))


def at(series, ms, span=200, win=25):
    i = int(ms / win)
    part = series[i:i + max(1, int(span / win))]
    return sum(part) / max(1, len(part))


# it starts as this song, sample for sample: the join can't be heard
x, y = array("h"), array("h")
x.frombytes(pcm[:_bytes(1500)])
y.frombytes(clean[:_bytes(1500)])
diffs = [abs(p - q) for p, q in zip(x, y)]
check(max(diffs) <= 3 * 40, "before the next song comes in, the mix should be this song exactly (off by %d)" % max(diffs))
print("up to the next song's start it is this song to within %d of 32768: no delay from the limiter" % max(diffs))

# the kicks of the two land together: during the overlap, each beat has one kick, on this song's grid
start = plan.start_at - AT
swap = plan.swap_at - AT
end = plan.end_at - AT
pa = a.beat_ms
win = 5
kicks = kicks_in(pcm[_bytes(start):_bytes(end)], pa, win)
check(len(kicks) >= 6, "too few kicks in the overlap: %d" % len(kicks))
gaps = [q - p for p, q in zip(kicks, kicks[1:])]
worst = max(abs(g - pa) for g in gaps)
check(worst < 45, "the kicks wander: gaps %s, a beat is %.0f ms" % ([round(g) for g in gaps], pa))
print("%d kicks in the overlap, one to a beat (worst gap off by %.0f ms)" % (len(kicks), worst))

# the bass: before the swap this song's, after it the next's, and never gone
before, after = at(low, swap - 900, 800), at(low, swap + 200, 800)
check(before > 800 and after > 800, "the bass should carry on through the swap: %.0f then %.0f" % (before, after))

# this song is out by its last sound and the next plays on at its own tempo from where the parts say
tail = pcm[_bytes(end + 200):]
check(len(tail) > _bytes(10000), "the next song stopped after the mix")
feed = _Feed(dec, [(0, "a", AT), (_bytes(start), "b", float(plan.in_ms), plan.speed),
                   (_bytes(end), "b", plan.in_ms + (plan.end_at - plan.start_at) * plan.speed)])
s0, m0 = feed.locate(_bytes(start))
s1, m1 = feed.locate(_bytes(end + 4000))
check(s0 == "b" and s1 == "b", "the position should be the next song's from its start")
want = plan.in_ms + (plan.end_at - plan.start_at) * plan.speed + 4000
check(abs(m1 - want) < 2, "after the mix its clock runs at 1x: %.0f, want %.0f" % (m1, want))
own = 60000.0 / b.bpm
kicks2 = kicks_in(tail[:_bytes(12000)], own, win)
gap = (kicks2[-1] - kicks2[0]) / (len(kicks2) - 1)
check(abs(gap - own) < 6, "after the mix the next song runs at its own tempo: a beat of %.0f, want %.0f" % (gap, own))
print("after the mix: its own tempo (%.0f ms a beat, want %.0f)" % (gap, own))
total = len(pcm) / audio_engine.BPMS
want_total = end + (b.dur_ms - (plan.in_ms + (plan.end_at - plan.start_at) * plan.speed))
check(abs(total - want_total) < 300, "nothing lost: %.0f ms of sound, want %.0f" % (total, want_total))
peak = max(max(array("h", pcm[:len(pcm) - len(pcm) % 2])), 1)
check(peak < 32767, "the two together shouldn't clip")

# with a loudness correction, this song's level is carried across the moment the sound card changes over
lifted = pcm_of(_Decode([(a_path, AT), (b_path, plan.in_ms)], mix=lambda n: mixer.graph(plan, AT, n, 0.7)))
loud2 = bands(lifted)
ratio = at(loud2, start + 800, 400) / max(1.0, at(loud, start + 800, 400))
check(ratio < 0.85, "the lift didn't apply: %.2f" % ratio)

# ---- the engine, end to end ----
engine = Engine()
engine.audio.setVolume(0.0)
engine.set_blend(8000)
engine.set_smart_mix(True)
advanced = []
engine.advanced.connect(advanced.append)
engine.setSource(QUrl.fromLocalFile(a_path), key="a", duration_ms=70000)
engine.play()
settle(300)
engine.set_next(QUrl.fromLocalFile(b_path), key="b", duration_ms=70000)
engine.setPosition(int(plan.start_at) - 9000)
limit = time.time() + 30
while time.time() < limit and engine.upcoming_mix() is None:
    settle(100)
if not engine._out.sink or engine.position() <= 0:
    print("no sound card here: the engine's run is skipped")
else:
    live = engine.upcoming_mix()
    check(live is not None, "the engine never planned the mix")
    limit = time.time() + 30
    while time.time() < limit and not advanced:
        settle(100)
    check(advanced == ["b"], "the next song never took over: %s" % advanced)
    print("the engine took the next song in, at its %.1f s" % (engine.position() / 1000.0))
    check(engine.position() < live.in_ms + 2000, "the next song should start from its way in, at %d" % engine.position())
    # the equalizer waits for the mix to end
    engine.set_eq(dict(engine.eq, enabled=True))
    settle(300)
    check(engine._join is None or engine._join.feed.dec is not None, "the join went missing")
    check(engine._feed.mixed_until > 0, "the mix should still be going")
    check(engine._eq_pending, "an equalizer change mid-mix should wait for it to finish")
    engine.stop()

    # a no-beat song: the plain blend, with this song's silence left out
    engine._plan = None
    d_path = song("d", 124, 45, pad_out=6.0)
    advanced.clear()
    engine.setSource(QUrl.fromLocalFile(d_path), key="d", duration_ms=45000)
    engine.play()
    settle(200)
    engine.set_next(QUrl.fromLocalFile(drone_path), key="drone", duration_ms=70000)
    engine.setPosition(25000)
    limit = time.time() + 20
    while time.time() < limit and not advanced:
        settle(100)
    check(advanced == ["drone"], "the plain blend never took over: %s" % advanced)
    check(engine.upcoming_mix() is None, "no steady beat: no mix")
    heard_d = mixer.known("d")
    check(heard_d is not None and abs(heard_d.sound_end - 39000) < 150, "the silence wasn't read: %s" % (heard_d,))
    print("no steady beat: blended plainly, from the sound's end at %.1f s" % (heard_d.sound_end / 1000.0))
    engine.stop()

    # with it off, and a short blend, the plain blend still happens (it isn't beaten to it by the gapless join)
    engine.set_smart_mix(False)
    engine.set_blend(5000)
    advanced.clear()
    engine.setSource(QUrl.fromLocalFile(a_path), key="a2", duration_ms=70000)
    engine.play()
    settle(200)
    engine.set_next(QUrl.fromLocalFile(b_path), key="b2", duration_ms=70000)
    engine.setPosition(55000)
    limit = time.time() + 12
    kind = None
    while time.time() < limit and kind is None:
        settle(100)
        if engine._join is not None:
            kind = engine._join.kind
    check(kind == "align", "a 5 s blend was beaten by the gapless join: %s" % kind)
    print("a 5 s blend starts as a blend, not gapless")
    engine.stop()

engine.shutdown()
print("OK")
