"""Synced lyrics for the Music tab's karaoke view, from LRCLIB.

LRCLIB (lrclib.net) is a free, open-source lyrics database with no account
or key; its synced lyrics are LRC text, one "[mm:ss.xx] line" per line. The
app asks for them when the karaoke view is opened and keeps nothing on disk.

Matching a song is the hard part: YouTube titles come as "Artist - Song
(Official Video) [HD]". The title is cleaned, split into artist and song,
and a candidate is only accepted when its length is within a few seconds of
the song being played -- a wrong song's words are worse than none.

A Hindi song's words come in two scripts: Devanagari, and Roman letters the
way they're usually typed ("Hinglish"). find_all() keeps one of each when
LRCLIB has both; with only Devanagari, the Roman version is spelt out from
it (romanize) so either can be shown.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..logging_setup import get_logger

logger = get_logger("lyrics")

API = "https://lrclib.net/api"
UA = {"User-Agent": "AwesomeDownloader/2.5 (https://github.com/shriguruphalle-cloud/awesome-downloader)"}
LENGTH_SLACK_S = 4

_NOISE = re.compile(
    r"[\(\[](?:[^\)\]]*?(?:official|video|audio|lyrics?|lyric video|visuali[sz]er|hd|hq|4k|remaster(?:ed)?|"
    r"live|music video|mv|explicit|clean|full song|with lyrics)[^\)\]]*)[\)\]]", re.I)
_FEAT = re.compile(r"\s+(?:ft\.?|feat\.?|featuring)\s+.*$", re.I)

_cache = {}


def parse_lrc(text):
    """[(ms, line)] in time order, from LRC text. A line with several stamps
    ("[00:10.00][01:20.00] chorus") is repeated at each; tags such as
    "[ar: ...]" are skipped."""
    out = []
    for raw in (text or "").splitlines():
        stamps = re.findall(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]", raw)
        if not stamps:
            continue
        words = re.sub(r"\[[^\]]*\]", "", raw).strip()
        for m, s in stamps:
            ms = int(m) * 60000 + int(round(float(s.replace(":", ".")) * 1000))
            out.append((ms, words))
    out.sort(key=lambda x: x[0])
    return out


def clean_title(title, artist=""):
    """(artist, song) from a video-style title."""
    t = _NOISE.sub("", title or "")
    t = re.sub(r"\s*[|•]\s*.*$", "", t)              # "Song | Label" -> "Song"
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—")
    a = (artist or "").removesuffix(" - Topic").removesuffix("VEVO").strip()
    for dash in (" - ", " – ", " — "):
        if dash in t:
            left, right = t.split(dash, 1)
            a, t = left.strip(), right.strip()
            break
    return _FEAT.sub("", a).strip(), _FEAT.sub("", t).strip()


def _get(path, params):
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _usable(rec, duration):
    if not rec or rec.get("instrumental"):
        return False
    if not (rec.get("syncedLyrics") or rec.get("plainLyrics")):
        return False
    if duration and rec.get("duration") and abs(float(rec["duration"]) - duration) > LENGTH_SLACK_S:
        return False
    return True


def find(track):
    """{"lines": [(ms, text)], "plain": str, "synced": bool, "name": "Artist - Song"}
    for `track`, or None. Blocking; cached for the session."""
    key = track.get("id")
    if key in _cache:
        return _cache[key]
    artist, song = clean_title(track.get("title", ""), track.get("artist", ""))
    duration = int(track.get("duration") or 0)
    rec = None
    try:
        if artist and song:
            params = {"artist_name": artist, "track_name": song}
            if duration:
                params["duration"] = duration
            rec = _get("get", params)
            if not _usable(rec, duration):
                rec = None
        if rec is None:
            for cand in _get("search", {"q": ("%s %s" % (artist, song)).strip()}) or []:
                if _usable(cand, duration) and cand.get("syncedLyrics"):
                    rec = cand
                    break
    except Exception:   # noqa: BLE001 -- no lyrics is a fine answer
        logger.info("Lyrics lookup failed for %s", track.get("title"), exc_info=True)
        return None
    result = None
    if rec is not None:
        result = _result(rec, artist, song, duration)
    _cache[key] = result
    return result


def script_of(text):
    """"hi" for Devanagari text, "en" for text in Roman letters."""
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return "en"
    deva = sum(1 for c in letters if "\u0900" <= c <= "\u097f")
    return "hi" if deva / len(letters) >= 0.3 else "en"


def _result(rec, artist, song, duration=0):
    lines = parse_lrc(rec.get("syncedLyrics") or "")
    plain = rec.get("plainLyrics") or ""
    if lines and duration and lines[-1][0] > duration * 1000 + 1500:
        # timed for another cut of the song (LRCLIB entries are sometimes
        # labelled with the wrong length): the words, without the timing
        plain = plain or "\n".join(t for _ms, t in lines)
        lines = []
    return {"lines": lines, "plain": plain, "synced": bool(lines),
            "name": "%s – %s" % (rec.get("artistName") or artist, rec.get("trackName") or song)}


def find_all(track):
    """{"hi": result or None, "en": result or None, "default": "hi" | "en"} --
    the song's words in Devanagari and in Roman letters, as far as LRCLIB
    has them (a Roman version is spelt out from the Devanagari one when it
    has only that). Blocking; cached for the session."""
    key = ("all", track.get("id"))
    if key in _cache:
        return _cache[key]
    artist, song = clean_title(track.get("title", ""), track.get("artist", ""))
    duration = int(track.get("duration") or 0)
    found = {"hi": None, "en": None}
    try:
        cands = []
        if artist and song:
            params = {"artist_name": artist, "track_name": song}
            if duration:
                params["duration"] = duration
            rec = _get("get", params)
            if _usable(rec, duration):
                cands.append(rec)
        cands += [c for c in (_get("search", {"q": ("%s %s" % (artist, song)).strip()}) or [])
                  if _usable(c, duration)]
        for rec in cands:
            text = rec.get("syncedLyrics") or rec.get("plainLyrics") or ""
            kind = script_of(text)
            have = found[kind]
            # synced beats plain; otherwise the first (the closest match) stays
            res = _result(rec, artist, song, duration)
            if have is None or (not have["synced"] and res["synced"]):
                found[kind] = res
    except Exception:   # noqa: BLE001 -- no lyrics is a fine answer
        logger.info("Lyrics lookup failed for %s", track.get("title"), exc_info=True)
    default = "hi" if found["hi"] else "en"
    if found["hi"] and not found["en"]:
        found["en"] = romanize_result(found["hi"])
    out = {"hi": found["hi"], "en": found["en"], "default": default}
    _cache[key] = out
    return out


# ---------------------------------------------------- Devanagari -> Roman --
# How Hindi is usually typed in Roman letters: aa / ee / oo for the long
# vowels inside a word, a / i / u at its end, the silent "a" dropped.
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_SIGNS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e"}
_CONS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
         "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh",
         "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v",
         "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
         "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y"}
_NUKTA = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़", "य": "य़"}
_WORDS = {"में": "mein", "मैं": "main", "है": "hai", "हैं": "hain", "नहीं": "nahi", "नहि": "nahi",
          "क्यों": "kyun", "क्यूँ": "kyun", "क्यूं": "kyun", "हूँ": "hoon", "हूं": "hoon", "यूँ": "yun",
          "यूं": "yun", "तू": "tu", "तो": "toh", "वो": "woh", "वह": "woh", "यह": "yeh", "और": "aur",
          "क्या": "kya", "कहाँ": "kahan", "यहाँ": "yahan", "वहाँ": "wahan", "भी": "bhi", "ही": "hi",
          "की": "ki", "के": "ke", "का": "ka", "को": "ko", "से": "se", "जो": "jo", "ना": "na", "न": "na"}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}


def _units(word):
    """[consonant(s), vowel, inherent?, nasal] per syllable of a Devanagari word."""
    units, i, w = [], 0, word
    while i < len(w):
        ch = w[i]
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in _NUKTA and nxt == "\u093c":
            ch, nxt, i = _NUKTA[ch], (w[i + 2] if i + 2 < len(w) else ""), i + 1
        if ch in _CONS:
            unit = [_CONS[ch], "a", True, ""]
            if nxt in _SIGNS:
                unit[1], unit[2] = _SIGNS[nxt], False
                i += 1
            elif nxt == "\u094d":             # virama: no vowel
                unit[1], unit[2] = "", False
                i += 1
            units.append(unit)
        elif ch in _VOWELS:
            units.append(["", _VOWELS[ch], False, ""])
        elif ch in ("\u0902", "\u0901"):        # anusvara, chandrabindu
            if units:
                units[-1][3] = "n"
        elif ch == "\u0903" and units:           # visarga
            units[-1][3] += "h"
        i += 1
    return units


def _romanize_word(word):
    import unicodedata
    word = unicodedata.normalize("NFD", word)
    for k, v in _WORDS.items():
        if unicodedata.normalize("NFD", k) == word:
            return v
    units = _units(word)
    if not units:
        return "".join(_DIGITS.get(c, c) for c in word)
    # the silent "a": dropped at the end of a word, and between two sounded
    # syllables (V C a C V) -- unless that would pile up three consonants
    if len(units) > 1 and units[-1][2] and not units[-1][3]:
        units[-1][1] = ""
    for k in range(len(units) - 2, 0, -1):
        u, before, after = units[k], units[k - 1], units[k + 1]
        if (u[2] and not u[3] and before[1] and not before[3] and after[0] and after[1]
                and len(u[0]) <= 2):
            u[1] = ""
    out = []
    for k, (cons, vowel, _inh, nasal) in enumerate(units):
        last = k == len(units) - 1
        if last and not nasal:
            vowel = {"aa": "a", "ee": "i", "oo": "u"}.get(vowel, vowel)
        out.append(cons + vowel + nasal)
    return "".join(out)


def romanize(text):
    """Devanagari text in Roman letters, the way Hindi is usually typed."""
    import re as _re
    parts = _re.split(r"([\u0900-\u097f]+)", text or "")
    out = []
    for p in parts:
        if p and "\u0900" <= p[0] <= "\u097f":
            out.append(_romanize_word(p.replace("।", ".").replace("॥", ".")) if p not in ("।", "॥") else ".")
        else:
            out.append(p)
    line = "".join(out)
    return line[:1].upper() + line[1:]


def romanize_result(result):
    if not result:
        return None
    return {"lines": [(ms, romanize(text)) for ms, text in result["lines"]],
            "plain": "\n".join(romanize(x) for x in (result.get("plain") or "").splitlines()),
            "synced": result["synced"], "name": result.get("name", ""), "spelt_out": True}


def sung_fraction(lines, i, ms):
    """How far through line `i` the singing is at `ms` (0..1). A line is
    sung at about the pace of its letters, not stretched over the pause
    before the next one -- so the highlight moves with the words."""
    if i < 0 or i >= len(lines):
        return 0.0
    start = lines[i][0]
    gap = (lines[i + 1][0] if i + 1 < len(lines) else start + 4000) - start
    letters = sum(1 for c in lines[i][1] if c.isalpha())
    sung = max(900, min(gap, 70 * letters + 300))
    return min(1.0, max(0.0, (ms - start) / max(1, sung)))


def current_line(lines, ms):
    """Index of the line being sung at `ms` (-1 before the first), and how far
    through it (0..1) -- the karaoke highlight's sweep."""
    if not lines or ms < lines[0][0]:
        return -1, 0.0
    lo, hi = 0, len(lines) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if lines[mid][0] <= ms:
            lo = mid
        else:
            hi = mid - 1
    start = lines[lo][0]
    end = lines[lo + 1][0] if lo + 1 < len(lines) else start + 4000
    span = max(1, end - start)
    return lo, min(1.0, max(0.0, (ms - start) / span))


# ------------------------------------------------- every version, and translations --
# Unicode blocks by script, for the lyrics' own fonts and the choice of version
SCRIPTS = (("Deva", 0x0900, 0x097F), ("Beng", 0x0980, 0x09FF), ("Guru", 0x0A00, 0x0A7F), ("Gujr", 0x0A80, 0x0AFF),
           ("Taml", 0x0B80, 0x0BFF), ("Telu", 0x0C00, 0x0C7F), ("Knda", 0x0C80, 0x0CFF), ("Mlym", 0x0D00, 0x0D7F),
           ("Arab", 0x0600, 0x06FF), ("Hang", 0xAC00, 0xD7AF), ("Jpan", 0x3040, 0x30FF), ("Hani", 0x4E00, 0x9FFF),
           ("Cyrl", 0x0400, 0x04FF), ("Grek", 0x0370, 0x03FF), ("Thai", 0x0E00, 0x0E7F))
SCRIPT_NAMES = {"Latn": "English letters", "Deva": "Devanagari · हिन्दी", "Beng": "Bengali · বাংলা",
                "Guru": "Gurmukhi · ਪੰਜਾਬੀ", "Gujr": "Gujarati · ગુજરાતી", "Taml": "Tamil · தமிழ்",
                "Telu": "Telugu · తెలుగు", "Knda": "Kannada · ಕನ್ನಡ", "Mlym": "Malayalam · മലയാളം",
                "Arab": "Urdu / Arabic · اردو", "Hang": "Korean · 한국어", "Jpan": "Japanese · 日本語",
                "Hani": "Chinese · 中文", "Cyrl": "Cyrillic", "Grek": "Greek", "Thai": "Thai · ไทย"}
SCRIPT_LANG = {"Deva": "hi", "Beng": "bn", "Guru": "pa", "Gujr": "gu", "Taml": "ta", "Telu": "te", "Knda": "kn",
               "Mlym": "ml", "Arab": "ur", "Hang": "ko", "Jpan": "ja", "Hani": "zh-CN", "Cyrl": "ru", "Grek": "el",
               "Thai": "th"}
TRANSLATE_TO = (("en", "English"), ("hi", "Hindi"), ("ur", "Urdu"), ("pa", "Punjabi"), ("bn", "Bengali"),
                ("mr", "Marathi"), ("gu", "Gujarati"), ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada"),
                ("ml", "Malayalam"), ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("pt", "Portuguese"),
                ("it", "Italian"), ("ru", "Russian"), ("ar", "Arabic"), ("tr", "Turkish"), ("id", "Indonesian"),
                ("ja", "Japanese"), ("ko", "Korean"), ("zh-CN", "Chinese"))
_TRANSLATE = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=%s&tl=%s&dt=t"
_tr_cache = {}


def script_code(text):
    """The script most of `text`'s letters are in ("Latn" for Roman letters)."""
    counts = {}
    latin = 0
    for ch in text or "":
        if not ch.isalpha():
            continue
        o = ord(ch)
        for code, lo, hi in SCRIPTS:
            if lo <= o <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
        else:
            latin += 1
    if counts:
        best = max(counts, key=counts.get)
        if counts[best] >= 0.3 * (counts[best] + latin):
            return best
    return "Latn"


# Google's input tools spell a language typed in English letters in its own script
SPELL = {"hi": ("hi-t-i0-und", "Deva"), "mr": ("mr-t-i0-und", "Deva"), "pa": ("pa-t-i0-und", "Guru"),
         "ur": ("ur-t-i0-und", "Arab"), "bn": ("bn-t-i0-und", "Beng"), "gu": ("gu-t-i0-und", "Gujr"),
         "ta": ("ta-t-i0-und", "Taml"), "te": ("te-t-i0-und", "Telu"), "kn": ("kn-t-i0-und", "Knda"),
         "ml": ("ml-t-i0-und", "Mlym")}
SHORT = {"Latn": "English letters", "Deva": "देवनागरी", "Guru": "ਗੁਰਮੁਖੀ", "Arab": "اردو", "Beng": "বাংলা",
         "Gujr": "ગુજરાતી", "Taml": "தமிழ்", "Telu": "తెలుగు", "Knda": "ಕನ್ನಡ", "Mlym": "മലയാളം",
         "Hang": "한국어", "Jpan": "日本語", "Hani": "中文", "Cyrl": "Кириллица", "Grek": "Ελληνικά", "Thai": "ไทย"}


def _spell_one(text, itc):
    out = []
    for piece in re.split(r"([^\w\s']+)", text):
        if not piece.strip() or not re.search(r"[A-Za-z]", piece):
            out.append(piece)
            continue
        q = urllib.parse.urlencode({"text": piece.strip(), "itc": itc, "num": 1, "cp": 0, "cs": 1, "ie": "utf-8",
                                    "oe": "utf-8", "app": "demopage"})
        req = urllib.request.Request("https://inputtools.google.com/request?" + q, headers={"User-Agent":
                                                                                          "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            got = json.load(resp)
        if got and got[0] == "SUCCESS" and got[1]:
            words = " ".join(seg[1][0] for seg in got[1] if seg and len(seg) > 1 and seg[1])
            lead = " " if piece[:1].isspace() else ""
            trail = " " if piece[-1:].isspace() else ""
            out.append(lead + words + trail)
        else:
            out.append(piece)
    return "".join(out)


def spell(texts, lang):
    """Lines typed in English letters ("tum hi ho") in their language's own
    script ("तुम ही हो"). [] if the language isn't one that can be spelt."""
    if lang not in SPELL:
        return []
    itc = SPELL[lang][0]
    key = (tuple(texts), itc)
    if key in _tr_cache:
        return _tr_cache[key]
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            return _spell_one(t, itc) if t.strip() else ""
        except Exception:   # noqa: BLE001
            return ""
    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(one, texts))
    if not any(out):
        return []
    _tr_cache[key] = out
    return out


def find_versions(track, lang_hint=None):
    """Every version of the song's words LRCLIB has -- one per script (the
    Devanagari words, the same words in English letters, Gurmukhi, Urdu...)
    -- with a Roman-letter version spelt out from Devanagari when there's
    none. {"versions": {script: result}, "order": [script...], "lang": code,
    "main": script, "sub": script or None, "spell": script or None -- the
    script words found only in English letters can be spelt in
    (`lang_hint`, the song's language as far as it's known)}. Blocking; cached."""
    key = ("versions", track.get("id"), int(track.get("duration") or 0))
    if key in _cache:
        return _cache[key]
    artist, song = clean_title(track.get("title", ""), track.get("artist", ""))
    duration = int(track.get("duration") or 0)
    found = {}
    try:
        cands = []
        if artist and song:
            params = {"artist_name": artist, "track_name": song}
            if duration:
                params["duration"] = duration
            rec = _get("get", params)
            if _usable(rec, duration):
                cands.append(rec)
        cands += [c for c in (_get("search", {"q": ("%s %s" % (artist, song)).strip()}) or [])
                  if _usable(c, duration)]
        if not cands and song:
            cands += [c for c in (_get("search", {"track_name": song}) or []) if _usable(c, duration)]
        for rec in cands:
            text = rec.get("syncedLyrics") or rec.get("plainLyrics") or ""
            code = script_code(text)
            have = found.get(code)
            res = _result(rec, artist, song, duration)
            if have is None or (not have["synced"] and res["synced"]):
                found[code] = res
    except Exception:   # noqa: BLE001 -- no lyrics is a fine answer
        logger.info("Lyrics lookup failed for %s", track.get("title"), exc_info=True)
    if "Deva" in found and "Latn" not in found:
        found["Latn"] = romanize_result(found["Deva"])
    order = sorted(found, key=lambda c: (c != "Latn", c))
    native = next((c for c in order if c != "Latn"), None)
    lang = SCRIPT_LANG.get(native) if native else None
    # as Apple Music shows a Hindi song: the words in English letters, the
    # Devanagari under each line
    if native and "Latn" in found and native == "Deva" and (found["Latn"]["synced"] or not found[native]["synced"]):
        main, sub = "Latn", native
    else:
        main, sub = (native or "Latn"), None
    spelt = None
    if not native and "Latn" in found and lang_hint in SPELL:
        lang, spelt = lang_hint, SPELL[lang_hint][1]
        sub = spelt                                   # the Devanagari (spelt out) under each line
    out = {"versions": found, "order": order, "lang": lang, "main": main if found else None, "sub": sub,
           "spell": spelt}
    _cache[key] = out
    return out


def align(lines, other):
    """`other`'s words line for line with `lines` -- by position when they
    match up, else by the nearest time (two LRCLIB entries rarely split a
    song identically)."""
    if not lines:
        return []
    if not other:
        return [""] * len(lines)
    if len(other) == len(lines):
        return [t for _ms, t in other]
    out, j = [], 0
    for ms, _t in lines:
        while j + 1 < len(other) and abs(other[j + 1][0] - ms) <= abs(other[j][0] - ms):
            j += 1
        out.append(other[j][1] if abs(other[j][0] - ms) <= 2500 else "")
    return out


def _gtx(texts, target, source="auto"):
    data = urllib.parse.urlencode({"q": "\n".join(texts)}).encode("utf-8")
    req = urllib.request.Request(_TRANSLATE % (source, urllib.parse.quote(target)), data=data,
                                 headers={"User-Agent": "Mozilla/5.0", "Content-Type":
                                          "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        got = json.load(resp)
    joined = "".join(seg[0] for seg in (got[0] or []) if seg and isinstance(seg[0], str))
    return joined.split("\n")


def translate(texts, target, source="auto"):
    """Each line of `texts` in language `target` (Google Translate's free
    web endpoint) -- [] if it can't be had. Blocking; cached."""
    texts = [t or "" for t in texts]
    key = (tuple(texts), target, source)
    if key in _tr_cache:
        return _tr_cache[key]
    out = []
    try:
        chunk, size = [], 0
        for t in texts:
            if size + len(t) > 3500 and chunk:
                out += _fit(chunk, _gtx(chunk, target, source), target, source)
                chunk, size = [], 0
            chunk.append(t)
            size += len(t) + 1
        if chunk:
            out += _fit(chunk, _gtx(chunk, target, source), target, source)
    except Exception:   # noqa: BLE001
        logger.info("Translating lyrics failed", exc_info=True)
        return []
    _tr_cache[key] = out
    return out


def _fit(src, got, target, source):
    """The translated lines back in place (blank lines stay blank)."""
    got = [g.strip() for g in got]
    if len(got) == len(src):
        return [g if s.strip() else "" for s, g in zip(src, got)]
    # the service merged or split a line: one at a time instead
    out = []
    for s in src:
        if not s.strip():
            out.append("")
            continue
        try:
            out.append(" ".join(x.strip() for x in _gtx([s], target, source) if x.strip()))
        except Exception:   # noqa: BLE001
            out.append("")
    return out



def pronounce(texts, script):
    """Each line in Roman letters -- how it's said. Devanagari is spelt out
    here; another script asks Google Translate for its romanisation."""
    if script == "Deva":
        return [romanize(t) for t in texts]
    key = (tuple(texts), "rm")
    if key in _tr_cache:
        return _tr_cache[key]
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        if not t.strip():
            return ""
        try:
            req = urllib.request.Request(
                "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=rm",
                data=urllib.parse.urlencode({"q": t}).encode("utf-8"),
                headers={"User-Agent": "Mozilla/5.0",
                         "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                got = json.load(resp)
            for seg in got[0] or []:
                if seg and len(seg) > 3 and isinstance(seg[3], str) and seg[3].strip():
                    return seg[3].strip()
        except Exception:   # noqa: BLE001
            pass
        return ""
    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(one, texts))
    _tr_cache[key] = out
    return out
