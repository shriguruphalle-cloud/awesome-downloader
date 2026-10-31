"""What someone means when they tell the DJ something: not the words to search for, but the situation, the energy it
calls for, the language, the era, and anyone or anything they named. "i am running very fast in punjabi" is hard
Punjabi workout songs; "driving" is a calm drive. Nothing here reaches the network: it only reads the request."""
import random
import re

LANGUAGES = (
    ("hi", "hindi", r"hindi|bollywood"), ("pa", "punjabi", r"punjabi"), ("en", "english", r"english"),
    ("ta", "tamil", r"tamil|kollywood"), ("te", "telugu", r"telugu|tollywood"), ("mr", "marathi", r"marathi"),
    ("bn", "bengali", r"bengali|bangla"), ("kn", "kannada", r"kannada"), ("ml", "malayalam", r"malayalam"),
    ("gu", "gujarati", r"gujarati"), ("ko", "korean", r"korean|k-?pop"), ("ja", "japanese", r"japanese|j-?pop"),
    ("es", "spanish", r"spanish|latin"), ("ar", "arabic", r"arabic"),
)

HARD = r"lo-?fi|slowed|reverb|unplugged|acoustic|lullaby|sad|heartbreak|meditation|sleep|piano|ghazal|bhajan"
SOFT = r"remix|party|edm|bass boosted|gym|workout|phonk|hard rock|metal|trap|club|dj\b"

# the situations, the more particular first: (what it says, energy, a name for the set, what to look for)
SITUATIONS = (
    (r"(rough|hard|wild|intense|aggressive)\s+(sex|sex\w*)|sex\w*\s+(rough|hard|wild)", "hype", "High voltage",
     ("high energy dark bass songs", "aggressive hard hitting songs", "intense powerful beats songs")),
    (r"\b(drag|street)?\s*rac(e|es|ing)\b|driv\w*\s+(very\s+)?fast|fast\s+driv\w*|speeding", "hype", "Foot down",
     ("high energy driving songs", "fast powerful songs", "adrenaline songs")),
    (r"\b(run|runs|running|sprint\w*|jog\w*|marathon|cardio|treadmill)\b", "hype", "Keep the pace",
     ("running workout songs", "high energy fast songs", "power workout motivation songs")),
    (r"\b(gym|work\s?out|workouts|lift\w*|deadlift|bench press|crossfit|hiit|boxing|mma|training|beast mode)\b", "hype", "Beast mode",
     ("gym workout songs", "hard hitting power songs", "high energy motivation songs")),
    (r"\b(adrenaline|pumped|hype\w*|rage|angry|fight\w*|battle)\b", "hype", "Full throttle",
     ("adrenaline high energy songs", "hard hitting songs", "powerful aggressive songs")),
    (r"\b(part(y|ies)|club\w*|danc\w*|celebrat\w*|wedding|sangeet|baraat|birthday|festival)\b", "hype", "Party's on",
     ("party dance hits", "club dance songs", "celebration dance songs")),
    (r"\b(driv\w*|road\s?trip|long drive|cruis\w*|highway)\b", "chill", "The long road",
     ("chill long drive songs", "relaxing road trip songs", "soft feel good drive songs")),
    (r"\b(sleep\w*|bed\s?time|insomnia|night\s?time|can'?t sleep)\b", "chill", "Lights low",
     ("soft sleep songs", "calm night songs", "gentle slow songs")),
    (r"\b(study\w*|studying|focus\w*|coding|cod\w+|reading|office|working|deep work|exam\w*)\b", "chill", "Heads down",
     ("calm focus songs", "soft instrumental study music", "lofi study songs")),
    (r"\b(medita\w*|yoga|relax\w*|calm|peace\w*|unwind\w*|spa)\b", "chill", "Breathe out",
     ("peaceful relaxing songs", "calm soothing songs", "soft mellow songs")),
    (r"\b(rain|rains|rainy|raining|monsoon|chai|tea|coffee|evening|sunset|balcony)\b", "chill", "Slow evening",
     ("rainy mood songs", "soft evening songs", "mellow soulful songs")),
    (r"\b(sad|cry\w*|heart\s?broken|breakup|break up|missing|miss you|lonely|alone|hurt\w*|depress\w*)\b", "chill", "For the heavy days",
     ("sad heartbreak songs", "emotional soulful songs", "lonely night songs")),
    (r"\b(romantic|romance|love|date|dating|candle\w*|anniversary|sensual|sex\w*|making love|intimate|cuddl\w*)\b", "chill", "Just us two",
     ("romantic slow songs", "soft sensual love songs", "intimate love songs")),
    (r"\b(clean\w*|cook\w*|chores|morning|commut\w*|walk\w*|happy|good mood|sunny|feel good)\b", "steady", "Feel good",
     ("feel good happy songs", "upbeat cheerful songs", "good mood songs")),
)

# words that carry no request of their own
_FILLER = set("""i im i'm am a an the and or but with for of in on at to my me we us our it its this that is are was be being very
really so just some something song songs music track tracks play playing put give need want wanna like please now today
mood feel feeling while during when having doing going gonna get let lets let's can could would should you your
fast slow hard strong night tonight songs bit little""".split())

# not one song: hours of a mix, or a channel making background sound. Asked for a situation, these aren't what a DJ plays
_NOT_A_SONG = re.compile(r"non[- ]?stop|mash-?up|jukebox|back\s?ground|\bmix\b|\bvol\.?\s*\d|\bhours?\b|tantric|hanuman|chalisa|\baarti\b|bhajan|"
                         r"\bmantra\b|music for|\bplaylist\b|ultimate .* anthem")
_A_CHANNEL = re.compile(r"\b(music|beats|meditation|relaxing|sleep|study|workout|gym|lo-?fi|ambient|moodz?|playlist|sounds?|productions?|station)\b")
_LONGEST = 9 * 60


def read(text, own_languages=()):
    """{"energy": "hype" | "chill" | "steady" | None, "label", "language" (a code, when asked for), "queries": [...],
    "avoid": a pattern for what doesn't belong}. `own_languages`: the codes of the listener's, used when none is said."""
    low = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if not low:
        return {"energy": None, "label": "", "language": None, "queries": [], "avoid": None}
    rest = low
    language, lang_name = None, ""
    for code, name, words in LANGUAGES:
        m = re.search(r"\b(%s)\b" % words, rest)
        if m:
            language, lang_name = code, name
            rest = rest.replace(m.group(0), " ")
            break
    era = re.search(r"\b((?:19|20)?\d0)'?s\b", rest)
    era_text = ""
    if era:
        era_text = era.group(1) + "s"
        rest = rest.replace(era.group(0), " ")
    energy, label, looks = None, "", ()
    for pattern, band, name, queries in SITUATIONS:
        m = re.search(pattern, rest)
        if m:
            energy, label, looks = band, name, queries
            rest = re.sub(pattern, " ", rest)
            break
    # the words said outright, when no situation settled it
    if energy is None and re.search(r"\b(energetic|fast|upbeat|strong|loud|powerful)\b", low):
        energy = "hype"
    elif energy is None and re.search(r"\b(chill|slow|soft|mellow|quiet|gentle)\b", low):
        energy = "chill"
    # what's left is theirs: an artist, a film ("arijit", "kabir singh")
    named = " ".join(w for w in re.findall(r"[^\W_]+(?:'[^\W_]+)?", rest, re.UNICODE) if w not in _FILLER)
    if looks:
        langs = [lang_name] if lang_name else [n for c, n, _ in LANGUAGES if c in (own_languages or ())][:2] or [""]
        queries = [" ".join(x for x in (lang, era_text, named, q) if x) for lang in langs for q in looks]
        random.shuffle(queries)
    else:
        queries = [" ".join(x for x in (lang_name, era_text, named or low, "songs") if x)]
    return {"energy": energy, "label": label, "language": language, "queries": queries[:4],
            "avoid": {"hype": HARD, "chill": SOFT}.get(energy)}


def fits(track, want):
    """Not plainly the wrong thing for what was asked: its name says the opposite energy."""
    avoid = want.get("avoid")
    title = str(track.get("title") or "").lower()
    if avoid and re.search(avoid, title):
        return False
    if not want.get("label"):           # a plain request ("kabir singh", "a mashup"): what it says, as it is
        return True
    if _NOT_A_SONG.search(title) or (track.get("duration") or 0) > _LONGEST:
        return False
    # studying asks for exactly this; nothing else does
    return want["label"] == "Heads down" or not _A_CHANNEL.search(str(track.get("artist") or "").lower())
