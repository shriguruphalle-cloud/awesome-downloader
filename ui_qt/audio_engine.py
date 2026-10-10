"""The Music tab's player: FFmpeg decodes, Qt's audio sink plays.

Qt's own media player has no way to change the sound on its way out, so the
equalizer and effects (app/core/eq.py) need the decoded sound in hand:
FFmpeg (bundled) decodes the file or stream, runs it through the filter
chain, and writes 16-bit, 48 kHz stereo to a pipe; a reader thread keeps a
few seconds of it in memory.

The sound card is fed from a thread of its own (_Output), so a busy screen --
a page being built, a cover being blurred -- can't starve it: the music
doesn't break up while the window works.

Nothing is ever stopped to change the sound. A changed equalizer starts a
second FFmpeg a moment ahead of what's playing, warmed up (its filters
settle on a few hundred milliseconds that are thrown away), and the two are
joined sample-exactly with a 24 ms crossfade -- heard within half a second,
without a gap or a click. The same join carries one song into the next:
the next song is decoded before this one ends, and either follows it with
no gap at all, or -- with "Blend songs" on -- the two are crossfaded by
FFmpeg's acrossfade over the last seconds of the first.

A stream is opened with FFmpeg's reconnect options, so a dropped connection
is picked up again instead of ending the song; one that does end early is
reported where it stopped (not as the song's end), so the tab can carry on
from there.

It answers to the same calls and signals as the QMediaPlayer it replaces,
with the same enums, plus `advanced` (a prepared next song is now heard),
set_next(), set_blend(), set_gain() and set_eq().
"""
import atexit
import math
import re
import subprocess
import threading
from array import array
from bisect import bisect_left
from collections import deque

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices, QMediaPlayer

from app.core import eq as eq_core
from app.logging_setup import get_logger

logger = get_logger("audio_engine")

RATE, CHANNELS, FRAME = 48000, 2, 4          # 16-bit stereo
BPMS = RATE * FRAME / 1000.0                 # bytes per millisecond
AHEAD_S = 8                                  # decoded ahead of what's playing (a stall's cushion)
SINK_S = 0.40                                # what the sound card holds
JOIN_MS = 24                                 # the crossfade that joins two decodes of the same song
WARMUP_MS = 400                              # decoded and thrown away, so new filters start settled
EQ_LEAD_MS = 180                             # how far ahead of what's playing a new equalizer joins
GAPLESS_PREP_MS = 12000                      # the next song is opened this long before the end
BLEND_PREP_MS = 6000                         # ... or this long before a blend starts
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_INPUT = re.compile(r"^Input #(\d+)")
_STREAM = re.compile(r"Stream #(\d+):\d+.*?Audio:\s*([^,(\s]+)[^,]*,\s*(\d+)\s*Hz,\s*([^,]+)(?:,\s*([^,]+))?"
                     r"(?:,\s*(\d+)\s*kb/s)?")
_LUFS = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")

State = QMediaPlayer.PlaybackState
Status = QMediaPlayer.MediaStatus


def _ffmpeg():
    from app.core import music_sources
    return music_sources._ffmpeg()


def _bytes(ms):
    n = int(max(0.0, ms) * BPMS)
    return n - n % FRAME


def _join(a, b):
    """`a` fading out into `b` (same length or shorter), linearly -- the two
    are the same music, so a straight line keeps the level even."""
    x, y = array("h"), array("h")
    x.frombytes(a)
    y.frombytes(b[:len(a)])
    if len(y) < len(x):
        y.extend([0] * (len(x) - len(y)))
    n = max(1, len(x) // 2)
    out = array("h", bytes(len(x) * 2))
    for i in range(len(x)):
        g = (i // 2 + 0.5) / n
        out[i] = int(x[i] * (1.0 - g) + y[i] * g)
    return out.tobytes()


class _Volume(QObject):
    """The volume and mute the tab sets (as on Qt's QAudioOutput)."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._v, self._m = 1.0, False

    def setVolume(self, v):   # noqa: N802 -- QAudioOutput's names
        self._v = max(0.0, min(1.0, float(v)))
        self.changed.emit()

    def volume(self):
        return self._v

    def setMuted(self, m):   # noqa: N802
        self._m = bool(m)
        self.changed.emit()

    def isMuted(self):   # noqa: N802
        return self._m

    def level(self):
        return 0.0 if self._m else self._v


class _Song:
    """A song as the engine knows it: where it comes from, how long it is,
    what it's encoded as, and its loudness correction."""

    def __init__(self, src, key=None, duration_ms=0, gain_db=0.0):
        self.src = src
        self.key = key or src
        self.duration_ms = int(duration_ms or 0)
        self.info = {}
        self.gain_db = float(gain_db or 0.0)


class _Decode:
    """One FFmpeg run into memory: `inputs` is [(src, at_ms)] -- one song, or
    two whose join is crossfaded over `blend_ms` (FFmpeg's acrossfade) --
    through `af`; the first `warmup_ms` are thrown away."""

    def __init__(self, inputs, af="", blend_ms=0, warmup_ms=0, on_info=None, spatial=""):
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.eof = False
        self.produced = 0
        self.rc = None
        self.err = []
        self.stopped = False
        self._skip = _bytes(warmup_ms)
        self._on_info = on_info
        cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-v", "info"]
        for src, at in inputs:
            if src.startswith(("http://", "https://")):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                        "-reconnect_delay_max", "8"]
            if at > 0:
                cmd += ["-ss", "%.3f" % (at / 1000.0)]
            cmd += ["-i", src]
        if len(inputs) == 2 or spatial:
            norm = "aresample=%d,aformat=channel_layouts=stereo" % RATE
            if len(inputs) == 2:
                fc = "[0:a:0]%s[a];[1:a:0]%s[b];[a][b]acrossfade=d=%.3f:c1=qsin:c2=qsin[x]" % (
                    norm, norm, blend_ms / 1000.0)
            else:
                fc = "[0:a:0]%s[x]" % norm
            last = "x"
            if spatial:
                fc += ";" + spatial.format(i="x", o="sp")
                last = "sp"
            fc += ";[%s]%s[out]" % (last, af or "anull")
            cmd += ["-filter_complex", fc, "-map", "[out]"]
        else:
            cmd += ["-map", "0:a:0", "-vn"]
            if af:
                cmd += ["-af", af]
        cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(RATE), "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        out = self.proc.stdout
        import time
        while not self.stopped:
            with self.lock:
                full = len(self.buf) > AHEAD_S * RATE * FRAME
            if full:
                time.sleep(0.02)
                continue
            chunk = out.read(16384)
            if not chunk:
                break
            if self._skip:
                cut = min(self._skip, len(chunk))
                self._skip -= cut
                chunk = chunk[cut:]
                if not chunk:
                    continue
            with self.lock:
                self.buf += chunk
                self.produced += len(chunk)
        self.rc = self.proc.wait()
        self.eof = True

    def _read_err(self):
        infos, cur = {}, 0
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            self.err.append(line)
            del self.err[:-12]
            m = _INPUT.match(line)
            if m:
                cur = int(m.group(1))
                continue
            info = infos.setdefault(cur, {})
            m = _DURATION.search(line)
            if m and "duration_ms" not in info:
                h, mi, s = m.groups()
                info["duration_ms"] = int((int(h) * 3600 + int(mi) * 60 + float(s)) * 1000)
                self._tell(cur, info)
            m = _STREAM.search(line)
            if m and "codec" not in infos.setdefault(int(m.group(1)), {}):
                idx = int(m.group(1))
                _i, codec, hz, layout, fmt, kbps = m.groups()
                desc = line.split("Audio:", 1)[1].split(",", 1)[0].strip()
                infos[idx].update(codec=codec, sample_rate=int(hz), channels=layout.strip(),
                                  sample_format=(fmt or "").strip(), kbps=int(kbps) if kbps else None,
                                  profile=desc)
                self._tell(idx, infos[idx])

    def _tell(self, idx, info):
        if self._on_info is not None and not self.stopped:
            self._on_info(idx, dict(info))

    def take(self, n):
        with self.lock:
            n = min(n, len(self.buf))
            n -= n % FRAME
            chunk = bytes(self.buf[:n])
            del self.buf[:n]
        return chunk

    def buffered(self):
        with self.lock:
            return len(self.buf)

    def failed(self):
        return self.eof and self.produced == 0

    def stop(self):
        self.stopped = True
        try:
            self.proc.kill()
        except OSError:
            pass


class _Feed:
    """A decode as it's fed to the sound card: `parts` says where in its
    output each song begins -- [(byte, song, that song's ms there)]."""

    def __init__(self, dec, parts):
        self.dec = dec
        self.parts = parts
        self.taken = 0

    def locate(self, b):
        fb, song, ms = self.parts[0]
        for part in self.parts[1:]:
            if part[0] <= b:
                fb, song, ms = part
        return song, ms + (b - fb) / BPMS

    def last_song(self):
        return self.parts[-1][1]

    def available(self):
        return self.taken + self.dec.buffered()


class _Join:
    """A decode waiting to take over: "align" joins it where the music is the
    same (a new equalizer, a blend's start), "follow" when the current one
    runs out (the next song, gapless)."""

    def __init__(self, kind, feed, song=None, base_ms=0.0):
        self.kind = kind
        self.feed = feed
        self.song = song
        self.base_ms = base_ms


class _Output(QObject):
    """The sound card, on its own thread: every 8 ms it takes what the
    engine has decoded and writes as much as the card will hold."""

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self.sink = None
        self.dev = None
        self.timer = None
        self.gen = -1
        self.carry = b""
        self.suspended = False
        self.gain = 1.0
        self.vol = None

    @Slot()
    def begin(self):
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.pump)
        self.timer.start()

    @Slot()
    def finish(self):
        if self.timer is not None:
            self.timer.stop()
        self._close()

    def _open(self):
        self._close()
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
        self.sink.setBufferSize(int(RATE * FRAME * SINK_S))
        self.vol = None
        self._apply_volume()
        self.dev = self.sink.start()
        self.suspended = False
        self.carry = b""

    def _close(self):
        if self.sink is not None:
            self.sink.stop()
            self.sink.deleteLater()
        self.sink = None
        self.dev = None
        self.carry = b""

    def _apply_volume(self):
        want = self.e._gain_target
        # a loudness correction glides in (half a second), the volume slider doesn't
        self.gain += (want - self.gain) * 0.06 if abs(want - self.gain) > 0.002 else (want - self.gain)
        v = max(0.0, min(1.0, self.e.audio.level() * self.gain))
        if self.sink is not None and (self.vol is None or abs(v - self.vol) > 0.0005):
            self.sink.setVolume(v)
            self.vol = v

    @Slot()
    def pump(self):
        e = self.e
        with e._lock:
            active, playing, gen = e._active, e._playing, e._reset_gen
        if not active:
            if self.sink is not None:
                self._close()
            return
        if gen != self.gen or self.sink is None:
            self.gen = gen
            self._open()
        if not playing:
            if not self.suspended:
                self.sink.suspend()
                self.suspended = True
            return
        if self.suspended:
            self.sink.resume()
            self.suspended = False
        self._apply_volume()
        free = self.sink.bytesFree()
        free -= free % FRAME
        if free >= FRAME * 240:                       # 5 ms or more
            data = self.carry
            if len(data) < free:
                with e._lock:
                    if gen != e._reset_gen:
                        return
                    data += e._pull(free - len(data))
            if data:
                n = self.dev.write(data) if self.dev is not None else 0
                self.carry = data[max(0, n):]
        played = int(self.sink.processedUSecs() * BPMS / 1000.0)
        with e._lock:
            if gen == e._reset_gen:
                e._played = played - played % FRAME
                e._idle = self.sink.state() == QAudio.State.IdleState


class Engine(QObject):
    """Plays one song at a time (and the next one into it); QMediaPlayer's
    calls, signals and enums."""
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    infoChanged = Signal(object)
    advanced = Signal(str)            # the prepared next song (its key) is now the one heard
    _info_sig = Signal(object, object)

    PlaybackState = State
    MediaStatus = Status

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio = _Volume(self)
        self.eq = eq_core.settings(None)
        self.blend_ms = 0
        self._lock = threading.RLock()
        self._song = None             # the song heard (or loaded)
        self._next = None             # the song to follow it
        self._feed = None
        self._join = None
        self._segs = []               # (sink byte, feed, feed byte): what the card plays, from where
        self._written = 0
        self._played = 0
        self._idle = False
        self._drained_at = None
        self._active = False
        self._playing = False
        self._reset_gen = 0
        self._gain_target = 1.0
        self._state = State.StoppedState
        self._status = Status.NoMedia
        self._pos = 0
        self._last_emit = -1
        self._error = ""
        self._eq_pending = False
        self.info = {}
        self._lp = 0.0                # the bass filter's state
        self._lv = deque(maxlen=900)  # (sink byte, bass, loudness): what the music is doing, where
        self._info_sig.connect(self._on_info)
        self._tick = QTimer(self)
        self._tick.setInterval(40)
        self._tick.timeout.connect(self._on_tick)
        self._thread = QThread()
        self._thread.setObjectName("music-output")
        self._out = _Output(self)
        self._out.moveToThread(self._thread)
        self._thread.started.connect(self._out.begin)
        self._thread.start(QThread.Priority.TimeCriticalPriority)
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._device_changed)
        atexit.register(self.shutdown)
        self.destroyed.connect(lambda *_a, t=self._thread, o=self._out: Engine._stop_thread(t, o))

    # ---- QMediaPlayer's calls ----

    def setSource(self, url, key=None, duration_ms=0):   # noqa: N802
        self._halt()
        url = QUrl(url)
        src = url.toLocalFile() if url.isLocalFile() else url.toString()
        self._song = _Song(src, key, duration_ms) if src else None
        self._next = None
        self._pos, self.info, self._error = 0, {}, ""
        self.durationChanged.emit(int(duration_ms or 0))
        self._set_status(Status.LoadingMedia if self._song else Status.NoMedia)
        self._set_state(State.StoppedState)

    def source(self):
        if self._song is None:
            return QUrl()
        src = self._song.src
        return QUrl(src) if src.startswith(("http://", "https://")) else QUrl.fromLocalFile(src)

    def play(self):
        if self._song is None:
            return
        if self._state == State.PausedState and self._feed is not None:
            with self._lock:
                self._playing = True
            self._set_state(State.PlayingState)
            return
        if self._state == State.PlayingState:
            return
        if self._status == Status.EndOfMedia:
            self._pos = 0
        self._start(self._pos)

    def pause(self):
        if self._state == State.PlayingState:
            self._pos = self.position()
            with self._lock:
                self._playing = False
            self._set_state(State.PausedState)

    def stop(self):
        self._halt()
        self._pos = 0
        self._set_state(State.StoppedState)
        self.positionChanged.emit(0)

    def setPosition(self, ms):   # noqa: N802
        ms = max(0, int(ms))
        dur = self.duration()
        if dur:
            ms = min(ms, max(0, dur - 200))
        self._pos = ms
        if self._state in (State.PlayingState, State.PausedState):
            paused = self._state == State.PausedState
            self._start(ms, playing=not paused)
        self._last_emit = ms
        self.positionChanged.emit(ms)

    def position(self):
        if self._active and self._state != State.StoppedState:
            with self._lock:
                where = self._audible()
            if where is not None:
                return int(where[1])
        return self._pos

    def duration(self):
        return self._song.duration_ms if self._song is not None else 0

    def playbackState(self):   # noqa: N802
        return self._state

    def current_key(self):
        return self._song.key if self._song is not None else None

    # ---- what comes next, and how ----
    def set_next(self, url, key=None, duration_ms=0, blend=True):
        """The song to follow this one -- decoded before this one ends, so it
        follows with no gap (or blended in). None: nothing prepared."""
        if url:
            u = QUrl(url)
            src = u.toLocalFile() if u.isLocalFile() else u.toString()
        else:
            src = None
        with self._lock:
            same = self._next is not None and src and self._next.src == src
            if same:
                self._next.blend = blend
                return
            self._cancel_join(of_next=True)
            self._next = _Song(src, key, duration_ms) if src else None
            if self._next is not None:
                self._next.blend = blend

    def set_blend(self, ms):
        """Seconds of crossfade between songs, in ms (0: gapless, no blend)."""
        self.blend_ms = max(0, min(12000, int(ms or 0)))

    def set_gain(self, key, db):
        """A song's loudness correction (dB, from its measured loudness)."""
        for song in (self._song, self._next):
            if song is not None and song.key == key:
                song.gain_db = max(-15.0, min(6.0, float(db)))
        self._update_gain()

    # ---- the equalizer ----
    def set_eq(self, settings):
        """New equalizer settings: a new decode is started just ahead of what's
        playing and joined in (no gap, no click) -- heard within half a second."""
        self.eq = eq_core.settings(settings)
        if not self._active or self._song is None:
            return
        with self._lock:
            self._eq_pending = True
        self._apply_eq()

    def _apply_eq(self):
        with self._lock:
            feed = self._feed
            if feed is None or not self._eq_pending:
                return
            song, ms_written = feed.locate(feed.taken)
            if song is not feed.last_song() or (self._join is not None and self._join.kind == "align"
                                                 and self._join.feed.last_song() is not song):
                return                           # mid-blend: once the next song has taken over
            self._eq_pending = False
            dur = song.duration_ms
            at = ms_written + EQ_LEAD_MS
            if dur and at > dur - 1500:
                return                           # too near the end to matter
            warm = min(WARMUP_MS, at)
            self._cancel_join()
            try:
                dec = _Decode([(song.src, at - warm)], eq_core.chain(self.eq), warmup_ms=warm,
                              on_info=self._info_for([song]), spatial=eq_core.spatial_graph(self.eq))
            except OSError:
                logger.warning("Couldn't start FFmpeg for the equalizer", exc_info=True)
                return
            self._join = _Join("align", _Feed(dec, [(0, song, at)]), song, at)

    # ---- inside ----
    def _info_for(self, songs):
        def tell(idx, info):
            if 0 <= idx < len(songs):
                self._info_sig.emit(songs[idx], info)
        return tell

    def _start(self, at_ms, playing=True):
        song = self._song
        with self._lock:
            self._stop_feeds()
            try:
                dec = _Decode([(song.src, at_ms)], eq_core.chain(self.eq), on_info=self._info_for([song]),
                              spatial=eq_core.spatial_graph(self.eq))
            except OSError as e:
                dec = None
                err = e
            if dec is not None:
                self._feed = _Feed(dec, [(0, song, at_ms)])
                self._segs = [(0, self._feed, 0)]
                self._written = self._played = 0
                self._lv.clear()
                self._lv_read = 0
                self._drained_at = None
                self._idle = False
                self._reset_gen += 1
                self._active = True
                self._playing = playing
                self._eq_pending = False
        if dec is None:
            self._fail("Couldn't start FFmpeg: %s" % err)
            return
        self._pos = at_ms
        self._last_emit = -1
        self._update_gain()
        self._set_status(Status.BufferingMedia)
        self._set_state(State.PlayingState if playing else State.PausedState)
        self._tick.start()

    def _stop_feeds(self):
        if self._feed is not None:
            self._feed.dec.stop()
        self._feed = None
        self._cancel_join()
        self._segs = []

    def _cancel_join(self, of_next=False):
        j = self._join
        if j is None:
            return
        if of_next and j.kind == "align" and len(j.feed.parts) == 1:
            return                               # an equalizer join, not the next song's
        j.feed.dec.stop()
        self._join = None

    def _halt(self):
        self._tick.stop()
        with self._lock:
            self._stop_feeds()
            self._active = False
            self._playing = False
            self._drained_at = None

    def _audible(self):
        """(song, ms) the card is playing now (under the lock)."""
        if not self._segs:
            return None
        played = min(self._played, self._written)
        seg = self._segs[0]
        for s in self._segs[1:]:
            if s[0] <= played:
                seg = s
            else:
                break
        # what's behind the card's position isn't needed any more
        while len(self._segs) > 1 and self._segs[1][0] <= played:
            self._segs.pop(0)
        return seg[1].locate(seg[2] + (played - seg[0]))

    def _pull(self, n):
        """Up to `n` bytes for the card (the output thread, under the lock):
        the current decode, joined onto a waiting one where it's due."""
        n -= n % FRAME
        out = bytearray()
        drained = False
        for _guard in range(6):
            if len(out) >= n:
                break
            f = self._feed
            if f is None:
                break
            j = self._join
            if j is not None and j.kind == "align" and self._try_align(j, out):
                continue
            chunk = f.dec.take(n - len(out))
            if chunk:
                out += chunk
                f.taken += len(chunk)
                continue
            if not (f.dec.eof and f.dec.buffered() == 0):
                break                            # waiting on the decode (a stream catching up)
            if j is not None and j.kind == "follow" and j.feed.available() > 0:
                # gapless: the next song carries straight on
                self._segs.append((self._written + len(out), j.feed, j.feed.taken))
                self._feed, self._join = j.feed, None
                continue
            if j is not None and j.kind == "follow" and not j.feed.dec.eof:
                break                            # the next song is still opening
            drained = True
            break
        if out:
            self._measure(out, self._written + len(out))
        self._written += len(out)
        if drained:
            self._drained_at = self._written
        elif out:
            self._drained_at = None
        return bytes(out)

    def _measure(self, data, end):
        """The bass and the loudness of what's about to be heard. The left
        channel is averaged in blocks of 32 samples (a box filter -- nothing
        above about 1.5 kHz folds back into the bass), then low-passed at
        about 150 Hz; the loudness is the plain RMS."""
        a = array("h")
        a.frombytes(bytes(data[:len(data) - len(data) % 4]))
        left = a[::2]
        if len(left) < 32:
            return
        lp, k = self._lp, 0.47                  # one pole at ~150 Hz, at 1.5 kHz
        bass = loud = 0.0
        n = 0
        for i in range(0, len(left) - 31, 32):
            block = left[i:i + 32]
            m = sum(block) / 32.0
            lp += (m - lp) * k
            bass += lp * lp
            loud += max(block) - min(block)
            n += 1
        self._lp = lp
        self._lv.append((end, math.sqrt(bass / n) / 32768.0, loud / n / 65536.0))

    def levels(self):
        """(bass, loudness), 0..1, of what's been heard since the last call --
        the bass's peak (a kick between two frames isn't missed), the
        loudness's average. For things that move with the music."""
        with self._lock:
            if not self._lv or not self._playing:
                return 0.0, 0.0
            played = self._played
            since = getattr(self, "_lv_read", 0)
            if since > played:
                since = 0
            i = bisect_left(self._lv, (since, -1.0, -1.0))
            j = bisect_left(self._lv, (played, -1.0, -1.0))
            j = min(j, len(self._lv) - 1)
            span = [self._lv[k] for k in range(min(i, j), j + 1)]
            self._lv_read = played
        return max(e[1] for e in span), sum(e[2] for e in span) / len(span)

    def _try_align(self, j, out):
        a = self._feed
        song, ms = a.locate(a.taken)
        if song is not j.song:
            self._cancel_join()                  # the music moved on (a blend took over)
            return False
        ahead = ms - j.base_ms                   # how far the playing one is past where the new one starts
        if ahead < 0:
            return False
        if j.feed.dec.failed() or (j.feed.dec.eof and j.feed.available() < _bytes(ahead) + FRAME):
            logger.info("A join's decode ended early; carrying on as it was")
            self._cancel_join()
            return False
        drop, xf = _bytes(ahead), _bytes(JOIN_MS)
        if j.feed.available() < drop + xf:
            return False
        while j.feed.taken < drop:
            got = j.feed.dec.take(drop - j.feed.taken)
            if not got:
                return False
            j.feed.taken += len(got)
        old = a.dec.take(xf)
        a.taken += len(old)
        new = j.feed.dec.take(len(old)) if old else b""
        self._segs.append((self._written + len(out), j.feed, j.feed.taken))
        j.feed.taken += len(new)
        out += _join(old, new) if old else b""
        a.dec.stop()
        self._feed, self._join = j.feed, None
        return True

    def _prepare_next(self):
        """Opens the next song before this one ends: blended in by FFmpeg, or
        following with no gap (the UI thread, every tick)."""
        nxt = self._next
        if nxt is None or self._join is not None or self._feed is None or not self._playing:
            return
        feed = self._feed
        song, ms = feed.locate(feed.taken)
        if song is not feed.last_song() or song is nxt or not song.duration_ms:
            return
        remain = song.duration_ms - ms
        blend = self.blend_ms if getattr(nxt, "blend", True) else 0
        chain = eq_core.chain(self.eq)
        if blend and remain <= blend + BLEND_PREP_MS and remain > blend + 900:
            at = ms + 500
            blend = min(blend, max(0, song.duration_ms - at - 300))
            try:
                dec = _Decode([(song.src, at), (nxt.src, 0)], chain, blend_ms=blend,
                              on_info=self._info_for([song, nxt]), spatial=eq_core.spatial_graph(self.eq))
            except OSError:
                logger.warning("Couldn't start the blend", exc_info=True)
                return
            parts = [(0, song, at), (_bytes(song.duration_ms - at - blend), nxt, 0.0)]
            self._join = _Join("align", _Feed(dec, parts), song, at)
            return
        if remain <= GAPLESS_PREP_MS:
            try:
                dec = _Decode([(nxt.src, 0)], chain, on_info=self._info_for([nxt]),
                              spatial=eq_core.spatial_graph(self.eq))
            except OSError:
                return
            self._join = _Join("follow", _Feed(dec, [(0, nxt, 0.0)]))

    def _update_gain(self):
        song = self._song
        db = song.gain_db if (song is not None and self.eq.get("normalize")) else 0.0
        with self._lock:
            self._gain_target = 10 ** (min(0.0, db) / 20.0)

    def _on_tick(self):
        if not self._active:
            return
        with self._lock:
            where = self._audible()
            drained = self._drained_at is not None and (self._played >= self._drained_at - FRAME * 480
                                                        or self._idle)
            feed = self._feed
            idle = self._idle
            buffered = feed.dec.buffered() if feed is not None else 0
            eof = feed.dec.eof if feed is not None else True
            self._prepare_next()
        if self._eq_pending:
            self._apply_eq()
        if where is None:
            return
        song, ms = where
        if song is not self._song and song is not None:
            # the next song is heard now -- gapless or blended
            self._song = song
            if self._next is song:
                self._next = None
            self.info = dict(song.info)
            self._update_gain()
            self.durationChanged.emit(song.duration_ms)
            self.infoChanged.emit(dict(self.info))
            self.advanced.emit(song.key)
        pos = int(ms)
        if abs(pos - self._last_emit) >= 50:
            self._last_emit = pos
            self.positionChanged.emit(pos)
        if self._status in (Status.BufferingMedia, Status.LoadingMedia, Status.StalledMedia) and buffered and \
                not idle:
            self._set_status(Status.BufferedMedia)
        if drained and feed is not None:
            dec = feed.dec
            self._pos = pos
            if dec.produced == 0 and dec.rc not in (0, None):
                self._fail(dec.err[-1] if dec.err else "FFmpeg couldn't read this song")
                return
            dur = self.duration()
            if dec.rc not in (0, None) and dur and pos < dur - 3000:
                # the stream broke off part-way: said where, so the tab can carry on from there
                self._fail("The stream stopped at %d s (%s)" % (pos // 1000, dec.err[-1] if dec.err else "error"),
                           keep_pos=True)
                return
            self._halt()
            self._set_state(State.StoppedState)
            self._set_status(Status.EndOfMedia)
        elif not eof and not buffered and idle and self._playing:
            self._set_status(Status.StalledMedia)

    def _fail(self, text, keep_pos=False):
        pos = self.position() if keep_pos else self._pos
        self._halt()
        self._pos = pos
        self._error = text
        self._set_status(Status.InvalidMedia)
        self._set_state(State.StoppedState)
        self.errorOccurred.emit(QMediaPlayer.Error.ResourceError, text)

    def _on_info(self, song, info):
        song.info.update(info)
        dur = info.get("duration_ms")
        if dur and dur != song.duration_ms:
            song.duration_ms = dur
            if song is self._song:
                self.durationChanged.emit(dur)
        if song is self._song:
            self.info = dict(song.info)
            if self._status == Status.LoadingMedia:
                self._set_status(Status.LoadedMedia)
            self.infoChanged.emit(dict(self.info))

    def _device_changed(self):
        """Headphones in or out: carry on, on the new default output."""
        if self._state == State.PlayingState:
            self._start(self.position())

    def _set_state(self, s):
        if s != self._state:
            self._state = s
            self.playbackStateChanged.emit(s)

    def _set_status(self, s):
        if s != self._status:
            self._status = s
            self.mediaStatusChanged.emit(s)

    @staticmethod
    def _stop_thread(thread, out):
        try:
            if thread.isRunning():
                thread.quit()
                thread.wait(1500)
        except RuntimeError:
            pass

    def shutdown(self):
        """Stops the decodes and the sound card's thread (on quitting)."""
        try:
            with self._lock:
                self._stop_feeds()
                self._active = False
            Engine._stop_thread(self._thread, self._out)
        except RuntimeError:     # already gone with Qt
            pass


def loudness(path):
    """The song's integrated loudness in LUFS (EBU R128), or None. Blocking --
    a quick FFmpeg pass over the file."""
    try:
        err = subprocess.run([_ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                              "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, timeout=120,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stderr
    except Exception:   # noqa: BLE001
        return None
    found = _LUFS.findall(err.decode("utf-8", "replace"))
    return float(found[-1]) if found else None


def waveform(path, bars=160):
    """The song's loudness in `bars` steps (0..1). Blocking (a quick FFmpeg
    pass at a low rate)."""
    try:
        out = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "4000",
                              "-f", "s16le", "pipe:1"], capture_output=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:   # noqa: BLE001
        return []
    a = array("h")
    a.frombytes(out[:len(out) - len(out) % 2])
    if not a:
        return []
    step = max(1, len(a) // bars)
    peaks = []
    for i in range(0, step * bars, step):
        seg = a[i:i + step]
        if not seg:
            break
        peaks.append(max(max(seg), -min(seg)))
    top = max(peaks) or 1
    return [round(p / top, 3) for p in peaks]
