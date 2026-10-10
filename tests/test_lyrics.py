"""Karaoke lyrics: LRC timings read right, video-style titles matched to
the right song (and a wrong-length match refused), the line being sung and
how far through it, and the full-screen view following the song and seeking
when a line is clicked. Every lyric line here is made up for the test; no
lyrics are fetched."""
import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import lyrics  # noqa: E402

# ---- reading LRC -----------------------------------------------------------------
lrc = """[ar: Test Band]
[ti: Test Song]
[00:01.00] First made-up line
[00:03.50][00:09.00] A line sung twice
[00:06.25]
[00:07.000] Third invented line
"""
lines = lyrics.parse_lrc(lrc)
check([ms for ms, _ in lines] == [1000, 3500, 6250, 7000, 9000], "LRC timings read wrong: %r" % lines)
check(lines[1][1] == lines[4][1] == "A line sung twice", "a line with two stamps wasn't placed at both")
check(lines[2][1] == "", "an empty (instrumental) line was lost")
print("LRC timings are read, repeated lines placed at each stamp")

# ---- which line, and how far through it -----------------------------------------
check(lyrics.current_line(lines, 500) == (-1, 0.0), "before the first line, something was highlighted")
i, frac = lyrics.current_line(lines, 2250)
check(i == 0 and abs(frac - 0.5) < 0.01, "halfway through line 1: %r" % ((i, frac),))
check(lyrics.current_line(lines, 9500)[0] == 4, "the last line wasn't found")
print("the line being sung, and how far through it")

# ---- matching a video title to a song --------------------------------------------
cases = {
    ("Some Artist - Some Song (Official Video) [HD]", "Some Artist VEVO"): ("Some Artist", "Some Song"),
    ("Some Song (Lyrics)", "Some Artist - Topic"): ("Some Artist", "Some Song"),
    ("Some Artist - Some Song ft. Guest | Label Records", ""): ("Some Artist", "Some Song"),
    ("Some Artist – Some Song (Remastered 2011)", ""): ("Some Artist", "Some Song"),
}
for (title, artist), want in cases.items():
    got = lyrics.clean_title(title, artist)
    check(got == want, "cleaned %r / %r -> %r, want %r" % (title, artist, got, want))
print("video-style titles are cleaned into artist and song")

answers = {}


def fake_get(path, params):
    answers.setdefault("asked", []).append((path, dict(params)))
    if path == "get":
        return {"artistName": "Some Artist", "trackName": "Some Song", "duration": 200,
                "syncedLyrics": "[00:01.00] An invented opening line\n[00:04.00] Another invented line",
                "plainLyrics": "An invented opening line\nAnother invented line"}
    return []


lyrics._get = fake_get
found = lyrics.find({"id": "t1", "title": "Some Artist - Some Song (Official Video)", "artist": "", "duration": 201})
check(found and found["synced"] and len(found["lines"]) == 2, "lyrics for a matching song weren't found")
check(answers["asked"][0] == ("get", {"artist_name": "Some Artist", "track_name": "Some Song", "duration": 201}),
      "asked LRCLIB the wrong question: %r" % (answers["asked"][0],))
lyrics._cache.clear()
wrong = lyrics.find({"id": "t2", "title": "Some Artist - Some Song", "artist": "", "duration": 260})
check(wrong is None, "lyrics for a song a minute longer were accepted -- the wrong song's words")
print("a song is matched by artist, title and length; a wrong-length match is refused")

# an entry labelled 160 s whose timings run to 4:44 (timed for the long cut): the words, untimed
mislabelled = {"duration": 160.0, "artistName": "A", "trackName": "S",
               "syncedLyrics": "[01:04.54] Line one\n[04:44.75] Line two", "plainLyrics": ""}
r = lyrics._result(mislabelled, "A", "S", 160)
check(not r["synced"] and "Line two" in r["plain"], "lyrics timed for another cut were shown in time: %r" % r)
check(lyrics._result(mislabelled, "A", "S", 300)["synced"], "lyrics that fit the song lost their timing")
print("lyrics timed past the song's end are shown as plain words, not out of sync")

# ---- Hindi in two scripts ------------------------------------------------------------
check(lyrics.script_of("मेरा दिल") == "hi" and lyrics.script_of("Mera dil") == "en", "scripts told apart wrong")
spelt = {"मेरा दिल": "Mera dil", "समझना": "Samajhna", "ज़िंदगी": "Zindagi", "जानता है": "Jaanta hai",
         "नहीं में": "Nahi mein", "मोहब्बत": "Mohabbat", "क़दम": "Qadam", "दिलबर": "Dilbar"}
for deva, roman in spelt.items():
    check(lyrics.romanize(deva) == roman, "spelt out %r as %r, want %r" % (deva, lyrics.romanize(deva), roman))
print("Devanagari is spelt out in Roman letters the way Hindi is usually typed")

lyrics._cache.clear()


def two_scripts(path, params):
    if path == "get":
        return {"artistName": "Test Gayak", "trackName": "Test Geet", "duration": 180,
                "syncedLyrics": "[00:01.00] बनाई हुई पहली पंक्ति\n[00:04.00] बनाई हुई दूसरी पंक्ति"}
    return [{"artistName": "Test Gayak", "trackName": "Test Geet", "duration": 181,
             "syncedLyrics": "[00:01.00] Banayi hui pehli line\n[00:04.00] Banayi hui doosri line"}]


lyrics._get = two_scripts
both = lyrics.find_all({"id": "h1", "title": "Test Gayak - Test Geet", "artist": "", "duration": 180})
check(both["hi"] and both["en"] and both["default"] == "hi", "both scripts weren't kept: %r" % both)
check(lyrics.script_of(both["hi"]["lines"][0][1]) == "hi" and both["en"]["lines"][0][1].startswith("Banayi"),
      "the two versions are mixed up: %r" % both)
lyrics._cache.clear()
lyrics._get = lambda path, params: ({"artistName": "Test Gayak", "trackName": "Test Geet", "duration": 180,
                                     "syncedLyrics": "[00:01.00] मेरा दिल"} if path == "get" else [])
only = lyrics.find_all({"id": "h2", "title": "Test Gayak - Test Geet", "artist": "", "duration": 180})
check(only["en"] and only["en"]["lines"][0][1] == "Mera dil" and only["en"].get("spelt_out"),
      "with only Devanagari, no Roman version was spelt out: %r" % only["en"])
print("a Hindi song's words come in Devanagari and in Roman letters (spelt out when LRCLIB has only one)")

# the highlight keeps pace with the words, not the pause after them
paced = [(0, "Four short made up words"), (20000, "Next")]
check(lyrics.sung_fraction(paced, 0, 1500) > 0.6, "the highlight crawled across a pause-long line: %.2f"
      % lyrics.sung_fraction(paced, 0, 1500))
check(lyrics.sung_fraction(paced, 0, 6000) == 1.0, "a sung line wasn't fully lit before the pause")
print("the highlight moves at the pace of the words")

# ---- the full-screen view follows the song ----------------------------------------
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402

from app.core import music_sources as ms  # noqa: E402
win, tabs = build_window(tabs=("video", "music", "download"), size=(1280, 820))
mt = tabs["music"]
win.tabs.setCurrentWidget(mt)
mt.audio.setMuted(True)
settle(600)
made_up = {"lines": [(0, "Invented line one"), (2000, "Invented line two"), (4000, "Invented line three"),
                     (6000, "Invented line four")], "plain": "", "synced": True, "name": "Test"}
lyrics.find_versions = lambda track, hint=None: {"versions": {"Latn": made_up}, "order": ["Latn"], "lang": None,
                                                 "main": "Latn", "sub": None, "spell": None}
mt.queue = [ms._track(id="x:1", title="Test Song", artist="Test", source="openverse", stream="")]
mt.index = 0
mt.open_full(lyrics=True)
for _ in range(40):
    if mt.full.lyrics.state == "ok":
        break
    settle(50)
lv = mt.full.lyrics
check(mt.full.isVisible() and lv.isVisible() and lv.state == "ok", "the lyrics didn't open with the words")
lv.set_position(3000, playing=False)
settle(900)
check(lv.index == 1, "the lyrics aren't on line 2: %r" % lv.index)
frac = lyrics.sung_fraction(lv.lines, 1, lv.now_ms())
check(0.2 < frac < 1.0, "line 2 isn't part-sung: %.2f" % frac)


def line_top(i):
    row = lv._layout()[i]
    return row["y"] - lv._row_scroll.get(i, 0)


check(abs(line_top(1) - lv.height() * lv.ANCHOR) < 8, "the lyrics didn't glide to the line being sung")
check(lv._emph.get(1, 0) > 0.9 and lv._emph.get(0, 1) < 0.1, "the sung line isn't the lit one")
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
lv.BACK_AFTER_S = 0.3
before = line_top(1)
app.sendEvent(lv, QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -240),
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                              Qt.ScrollPhase.NoScrollPhase, False))
settle(300)
check(lv._manual != 0 and line_top(1) != before, "the wheel didn't scroll the lyrics")
settle(2200)
check(lv._manual == 0 and abs(line_top(1) - lv.height() * lv.ANCHOR) < 8,
      "after scrolling, the lyrics didn't come back to the song")
print("the lyrics glide to the line being sung, scroll with the wheel and come back")
mt.full.grab()
sought = []
lv.seek_requested.disconnect()
lv.seek_requested.connect(sought.append)
rect, ms_at = next(h for h in lv._hits if h[1] == 4000)
from PySide6.QtGui import QMouseEvent  # noqa: E402
app.sendEvent(lv, QMouseEvent(QEvent.Type.MouseButtonPress, rect.center(), rect.center(),
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
check(sought == [4000], "clicking a line didn't seek there: %r" % sought)

# a Hindi song: the words in English letters, the Devanagari under each line (as Apple Music shows it)
hindi = {"lines": [(0, "बनाई हुई पहली पंक्ति"), (2000, "बनाई हुई दूसरी पंक्ति")], "plain": "", "synced": True,
         "name": "Test"}
roman = lyrics.romanize_result(hindi)
lyrics.find_versions = lambda track, hint=None: {"versions": {"Deva": hindi, "Latn": roman}, "order": ["Latn", "Deva"],
                                                 "lang": "hi", "main": "Latn", "sub": "Deva", "spell": None}
mt._fetch_lyrics(mt.current())
for _ in range(40):
    if lv.state == "ok":
        break
    settle(50)
check(lv.script == "Latn" and lv.lines[0][1].startswith("Banaai") and lv.subs[0] == hindi["lines"][0][1],
      "a Hindi song isn't English letters over Devanagari: %r / %r" % (lv.lines[:1], lv.subs[:1]))
check(mt.full.lang_btn.isVisible() and "देवनागरी" in mt.full.lang_btn.text(), "the language button: %r"
      % mt.full.lang_btn.text())
mt._set_lyrics_pref(main="Deva", sub="")
check(lv.script == "Deva" and lv.lines[0][1] == hindi["lines"][0][1] and not any(lv.subs),
      "choosing Devanagari didn't switch the words")
check("Nirmala UI" in lv._layout()[0]["mf"].families(), "Devanagari isn't set in a Hindi face")
mt.full.grab()
mt._set_lyrics_pref(main="Latn", sub="auto")
print("a Hindi song: English letters with Devanagari under each line; either can lead; the choice is kept")
mt.full.lyrics_pill.click()
check(not lv.isVisible(), "the lyrics button didn't hide the lyrics")
mt.close_full()
check(not mt.full.isVisible(), "the full-screen view didn't close")
print("the lyrics view follows the song, glides, and seeks on a click")

win.close()
settle(400)
print("\nLYRICS OK")
