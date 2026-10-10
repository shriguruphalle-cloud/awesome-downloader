"""The Music tab's equalizer and effects, as FFmpeg audio filters.

FFmpeg's filters are open source and studio-grade -- the same ones mpv plays
through: a 10-band peaking equalizer (one biquad per octave), a bass shelf,
a stereo widener for headphones ("virtual surround"), a dynamic loudness
normaliser and a limiter. audio_engine runs the song through the chain this
builds; with everything flat and off, the chain is empty and the sound
passes through untouched.

"Level the volume" isn't a filter: each song's loudness is measured (EBU
R128, as Spotify and Apple Music do) and the player turns a loud one down
to the same level -- no pumping, nothing to warm up.
"""

BANDS = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
LABELS = ("32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K")
MAX_DB = 12

PRESETS = {
    "Flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Bass boost": (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
    "Bollywood": (3, 3, 1, 0, -1, 1, 2, 3, 3, 2),
    "Pop": (-1, 1, 3, 4, 3, 0, -1, -1, 1, 2),
    "Rock": (4, 3, 2, 0, -1, -1, 1, 3, 4, 4),
    "Hip-hop": (5, 4, 2, 2, -1, -1, 1, 0, 2, 3),
    "Electronic": (4, 4, 1, 0, -2, 1, 0, 1, 4, 4),
    "Jazz": (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Classical": (4, 3, 2, 1, -1, -1, 0, 2, 3, 4),
    "Acoustic": (3, 2, 1, 1, 2, 1, 2, 2, 2, 1),
    "Vocal": (-2, -2, -1, 1, 3, 4, 4, 3, 1, 0),
    "Podcast": (-4, -3, -1, 1, 3, 4, 3, 1, -1, -3),
    "Late night": (2, 2, 1, 0, 0, -1, -2, -2, -1, 0),
}

DEFAULT = {"enabled": False, "preset": "Flat", "gains": list(PRESETS["Flat"]), "bass": False,
           "surround": False, "normalize": False, "mono": False, "spatial": "off"}
SPATIAL = ("off", "headphones", "speakers")


def settings(stored):
    """Saved equalizer settings, with anything missing filled in."""
    out = dict(DEFAULT)
    out.update({k: v for k, v in (stored or {}).items() if k in DEFAULT})
    if out.get("spatial") not in SPATIAL:
        out["spatial"] = "off"
    gains = list(out.get("gains") or [])[:len(BANDS)]
    out["gains"] = [max(-MAX_DB, min(MAX_DB, float(g))) for g in gains + [0] * (len(BANDS) - len(gains))]
    return out


def chain(s):
    """The FFmpeg -af filter chain for settings `s`; "" when it would change nothing."""
    s = settings(s)
    parts = []
    if s["enabled"]:
        boost = max([g for g in s["gains"] if g > 0] or [0])
        if boost:
            # headroom: lowered first by half the largest boost (a boosted band
            # rarely peaks where the song does); the limiter catches the rest
            parts.append("volume=-%.1fdB" % (boost * 0.5))
        for f, g in zip(BANDS, s["gains"]):
            if abs(g) >= 0.25:
                parts.append("equalizer=f=%d:t=o:w=1:g=%.1f" % (f, g))
    if s["bass"]:
        parts.append("volume=-4dB")
        parts.append("bass=g=6:f=110:w=0.6")
    if s["surround"]:
        parts.append("stereowiden=delay=18:feedback=0.25:crossfeed=0.3:drymix=0.85")
    if s["mono"]:
        parts.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")
    if s["spatial"] == "speakers":
        # a wider stage from two speakers: the sides lifted, a hint of room
        parts.append("extrastereo=m=1.35")
        parts.append("aecho=0.85:0.55:14|23:0.10|0.07")
    if parts:
        parts.append("alimiter=limit=0.95:attack=4:release=60:level=disabled")
    return ",".join(parts)


def _hrir():
    """Head-related impulse responses for the eight virtual speakers of a 7.1
    layout, from a spherical head model (Woodworth's time difference, a
    level difference and a softened far ear) -- made here, no file needed."""
    import math
    radius, sound, rate = 0.0875, 343.0, 48000
    speakers = (("FL", -30), ("FR", 30), ("FC", 0), ("LFE", 0), ("BL", -140), ("BR", 140), ("SL", -95), ("SR", 95))
    exprs = []
    for name, angle in speakers:
        a = abs(math.asin(math.sin(math.radians(angle))))
        delay = int(round(radius / sound * (a + math.sin(a)) * rate))
        far = 1.0 - 0.42 * math.sin(a)
        rear = 0.82 if abs(angle) > 100 else 1.0
        low = 0.7 if name == "LFE" else 1.0
        for ear in ("L", "R"):
            near = angle == 0 or (angle < 0 and ear == "L") or (angle > 0 and ear == "R")
            if near:
                exprs.append(r"%.3f*eq(n\,0)" % (rear * low))
            else:
                g = far * rear * low
                exprs.append(r"%.3f*eq(n\,%d)+%.3f*eq(n\,%d)+%.3f*eq(n\,%d)"
                             % (0.5 * g, delay, 0.3 * g, delay + 1, 0.2 * g, delay + 2))
    return "aevalsrc=exprs=%s:s=%d:d=0.002" % ("|".join(exprs), rate)


def spatial_graph(s):
    """Open-source spatial sound for headphones, as a filter graph from
    [{i}] to [{o}]: the stereo song upmixed to 7.1 (FFmpeg's surround) and
    each virtual speaker placed round your head (FFmpeg's headphone, with
    the head model above). "" when it's off."""
    if settings(s)["spatial"] != "headphones":
        return ""
    return ("[{i}]surround=chl_out=7.1[{i}up];" + _hrir() + "[{i}hr];"
            "[{i}up][{i}hr]headphone=map=FL|FR|FC|LFE|BL|BR|SL|SR:hrir=multich,volume=-2dB[{o}]")


def apply_preset(s, name):
    s = settings(s)
    if name in PRESETS:
        s["preset"], s["gains"], s["enabled"] = name, list(PRESETS[name]), True
    return s


TARGET_LUFS = -14.0         # the level songs are brought to (Spotify's and YouTube's)


def normal_gain(lufs):
    """dB to bring a song measured at `lufs` to TARGET_LUFS -- turned down
    when louder; a quiet one only a little up (no headroom to spare)."""
    if lufs is None:
        return 0.0
    return max(-12.0, min(2.0, TARGET_LUFS - float(lufs)))
