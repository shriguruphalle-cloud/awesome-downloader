"""Share video: the song as a short MP4, made with the FFmpeg in the app. A made-up song and a cover drawn on the spot;
the file is read back with FFmpeg: its size, length, frame rate, codecs, that its sound is the stretch asked for, and
that its picture moves with the music. Nothing here writes outside the test's folder, or touches the real clipboard."""
import _support
from _support import build_window, check, settle

import math
import os
import re
import subprocess
import tempfile
from array import array

from PySide6.QtGui import QColor, QImage, QPainter

from app.core import music_sources
from ui_qt import music_share, share_video

FF = music_sources._ffmpeg()
tmp = tempfile.mkdtemp(prefix="awd-share-", dir=_support.STATE_DIR)
music_share.videos_dir = lambda: os.path.join(tmp, "Videos", "Awesome Music")
SONG = os.path.join(tmp, "song.flac")
# silent for 10 s, then kicks: a stretch that starts at 10 s has sound from its first moment, one from 0 has none yet
subprocess.run([FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-t", "90", "-i",
                "aevalsrc='if(gte(t\\,10)\\,0.7*sin(2*PI*70*t)*exp(-9*mod(t\\,0.5))\\,0)':s=44100:c=stereo", SONG], check=True)

win, tabs = build_window(tabs=("music", "download"), size=(1300, 860))
win.show()
settle(500)
mt = tabs["music"]
sheet = mt.sharer
track = {"kind": "track", "id": "local:song", "title": "A Synthetic Song With A Really Rather Long Name To Elide",
         "artist": "Nobody At All", "source": "local", "stream": SONG, "duration": 90, "artwork": ""}


def cover():
    img = QImage(600, 600, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.fillRect(img.rect(), QColor("#ff5a36"))
    p.fillRect(100, 100, 400, 400, QColor("#1b3cff"))
    p.end()
    return img


def wait_for(cond, ms=120000):
    for _ in range(ms // 100):
        settle(100)
        if cond():
            return True
    return False


def probe(path):
    """What FFmpeg reads off the file's header."""
    err = subprocess.run([FF, "-nostdin", "-hide_banner", "-i", path], capture_output=True).stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    video = re.search(r"Video:\s*(\w+).*?,\s*(\d+)x(\d+).*?(\d+(?:\.\d+)?) fps", err)
    audio = re.search(r"Audio:\s*(\w+)", err)
    return dur, video.groups() if video else None, audio.group(1) if audio else None


def loudness(path, a, b):
    pcm = subprocess.run([FF, "-nostdin", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", path, "-vn", "-ac", "1",
                          "-ar", "8000", "-f", "s16le", "pipe:1"], capture_output=True).stdout
    x = array("h")
    x.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    return math.sqrt(sum(v * v for v in x) / max(1, len(x)))


def frame(path, at, name):
    out = os.path.join(tmp, name)
    subprocess.run([FF, "-nostdin", "-v", "error", "-y", "-ss", str(at), "-i", path, "-frames:v", "1", out], check=True)
    img = QImage(out)
    check(not img.isNull(), "couldn't read a frame back")
    return img


# ---- the sheet ----
sheet.open_for(track)
sheet.art = cover()
sheet._refresh_preview()
settle(300)
check(sheet.isVisible() and sheet.choices.isVisible(), "the sheet didn't open")
check(not sheet.link_btn.isVisible(), "a local song has no link")
shot = os.environ.get("AWD_SHOT")
if shot:
    win.grab().save(shot + "-sheet.png")
print("the sheet opens, with a preview")

# the stretch: 15 s from a minute in, the song's sound, a Story
sheet._choose("start", "pick")
check(sheet.pick.isVisible(), "picking a moment should show its slider")
sheet.pick.setValue(60)
check(sheet.start_ms() == 60000 and "1:00 to 1:15" in sheet.pick_note.text(), "the moment: %s" % sheet.pick_note.text())
sheet.make_btn.click()
check(sheet.making.isVisible() and not sheet.choices.isVisible(), "the progress should show")
if shot:
    settle(1500)
    win.grab().save(shot + "-making.png")
check(wait_for(lambda: sheet.file or sheet.fail_note.isVisible()), "the video never finished")
check(sheet.file and sheet.ready.isVisible(), "it failed: %s" % sheet.fail_note.text())
dur, video, audio = probe(sheet.file)
print("made: %.1f s, %s, %s" % (dur, video, audio))
check(abs(dur - 15) < 0.3, "15 s were asked for: %.2f" % dur)
check(video and video[0] == "h264" and video[1:3] == ("1080", "1920") and float(video[3]) == 30, "video: %s" % (video,))
check(audio == "aac", "audio: %s" % audio)
check(loudness(sheet.file, 1, 3) > 300, "the song's sound is missing")
check(loudness(sheet.file, 14.5, 15) < loudness(sheet.file, 5, 6), "the sound should fade out at the end")
a, b, c = frame(sheet.file, 1.0, "a.png"), frame(sheet.file, 1.03, "b.png"), frame(sheet.file, 12.0, "c.png")
if shot:
    a.save(shot + "-frame.png")


def differs(x, y):
    """How many of a grid of points in the background differ, away from the card."""
    d = 0
    for px in range(10, x.width(), 90):
        for py in list(range(10, 200, 40)) + list(range(x.height() - 200, x.height() - 10, 40)):
            ca, cb = x.pixelColor(px, py), y.pixelColor(px, py)
            d += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue()) > 6
    return d


moved = differs(a, c)
check(moved > 5, "the background should have moved (%d points)" % moved)
# the card's on it: the cover's colours where the cover is
card = share_video.Card(1080, 1920, track, sheet.art, sheet.pal, 90000)
inside = a.pixelColor(int(card.art_rect.center().x()), int(card.art_rect.center().y()))
check(inside.blue() > 150 > inside.red(), "the cover isn't on the card: %s" % inside.name())
print("the background moves (%d of the points), the cover's on the card" % moved)

# saved and copied
sheet.save()
check(os.path.exists(os.path.join(music_share.videos_dir(), os.path.basename(sheet.file))), "not saved to Videos")
from PySide6.QtWidgets import QApplication  # noqa: E402
copied = []
QApplication.clipboard().setMimeData = copied.append      # (_support's stand-in clipboard: nothing reaches the real one)
sheet.copy_file()
check(copied and copied[0].urls()[0].toLocalFile().replace("/", os.sep).lower() == sheet.file.replace("/", os.sep).lower(),
      "the file isn't what was copied")
print("saved to Videos, copied")

# ---- the other shape, silent, from the beginning: a post's 4:5, with no sound track ----
sheet._show("choices")
for key, value in (("story", False), ("start", "beginning"), ("sound", False), ("look", "focus")):
    sheet._choose(key, value)
first = sheet.file
sheet.file = None
sheet.make_btn.click()
check(wait_for(lambda: sheet.file or sheet.fail_note.isVisible()), "the second video never finished")
check(sheet.file and sheet.file != first or sheet.file, "it failed: %s" % sheet.fail_note.text())
dur, video, audio = probe(sheet.file)
check(video[1:3] == ("1080", "1350") and audio is None and abs(dur - 15) < 0.3, "a silent post: %s %s %.1f" % (video, audio, dur))
print("a silent 4:5 post: %s" % (video,))

# ---- past the end, the stretch starts earlier: the video is never cut short ----
pcm, at = share_video.decode(SONG, 85000, 15, 90000)
check(at == 75000 and len(pcm) >= 15 * 48000 * 4 - 8, "a stretch past the end: from %d, %d bytes" % (at, len(pcm)))
lv = share_video.levels(pcm, 15 * 30)
check(len(lv) == 450 and max(x[0] for x in lv) > 0.02 and all(0 <= v <= 1.5 for x in lv for v in x), "the levels")
silent = share_video.levels(bytes(48000 * 4), 30)
check(all(max(x) == 0 for x in silent), "silence should have no levels")
print("a stretch past the song's end starts earlier; the levels are read")

# ---- lyrics, only with words in time; made-up lines ----
lines = [(0, "A made-up line"), (4000, "Another made-up line that is rather longer, to wrap"), (8000, ""),
         (12000, "A last one")]
mt.synced_lines = lambda _t: lines
sheet.open_for(track)
sheet._choose("look", "lyrics")
check(sheet.look == "lyrics", "the lyrics look should be offered with words in time")
img = share_video.still(540, 960, track, sheet.art, sheet.pal, 90000, "lyrics", lines, 5000)
check(not img.isNull(), "the lyrics look didn't draw")
if shot:
    img.save(shot + "-lyrics.png")
c0 = share_video.Card(1080, 1920, track, None, sheet.pal, 90000, "lyrics", lines)
check(c0.at(1000) and not c0.at(1100) and c0.at(4300), "the card should redraw when the line changes, not otherwise")
mt.synced_lines = lambda _t: []
sheet.open_for(track)
check(sheet.look == "card" and not any(b.value == "lyrics" for b in sheet._groups["look"]), "no words: no lyrics look")
print("the lyrics look: only with words in time, and it follows the lines")

# ---- cancelled: no file left, and it can go again; a broken song says so ----
sheet._choose("seconds", 60)
before = set(os.listdir(share_video.SHARE_DIR))
sheet.make_btn.click()
check(wait_for(lambda: sheet.maker._timer.isActive(), 20000), "it never started")
settle(500)
sheet.cancel()
check(sheet.choices.isVisible(), "cancelling should go back to the choices")
check(wait_for(lambda: not sheet.maker.running and set(os.listdir(share_video.SHARE_DIR)) == before, 15000),
      "a cancelled video left a file: %s" % (set(os.listdir(share_video.SHARE_DIR)) - before))
sheet._choose("seconds", 15)
bad = dict(track, stream=os.path.join(tmp, "nothing.flac"), id="local:none")
sheet.open_for(bad)
sheet.make_btn.click()
check(wait_for(lambda: sheet.fail_note.isVisible() and not sheet.maker.running, 30000), "a missing song should be said")
check("couldn't be read" in sheet.fail_note.text(), "what went wrong: %s" % sheet.fail_note.text())
print("cancelling leaves nothing behind; a song that can't be read is said")
sheet.close_sheet()
win.close()
settle(300)
print("SHARE VIDEO OK")
