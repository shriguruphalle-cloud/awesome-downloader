"""Turns raw yt-dlp / network errors into something a person can act on.

yt-dlp's messages are written for a terminal: they carry an "ERROR:" prefix,
an extractor tag and a video id, and frequently end in command-line advice
("use --cookies-from-browser", "Confirm you are on the latest version using
yt-dlp -U"). Shown verbatim in a dialog or a download card they read as a
crash report. Each rule below matches one failure people actually hit and
says what happened and what to do about it, in that order.

Framework-agnostic on purpose, like the rest of app/core/.
"""
import re

# (pattern, short headline, what to do). Checked in order -- the first match
# wins, so the more specific patterns sit above the general ones they would
# otherwise be swallowed by (a 403 inside a "Sign in to confirm" message is a
# sign-in problem, not a generic refusal).
_RULES = [
    # First: this one is about the *browser*, not the link. Chrome and Edge
    # lock their cookie stores with app-bound encryption now, so borrowing
    # their sign-in fails before the link is even read. (The downloader
    # retries without the browser's cookies, so this only reaches the user
    # when that retry can't happen.)
    (r"failed to decrypt with dpapi|failed to load cookies|could not copy chrome cookie database|"
     r"cookie database",
     "Couldn't read that browser's sign-in.",
     "Chrome and Edge lock their cookies so other apps can't read them. Sign in inside "
     "this app's Browser tab instead, or choose Firefox in Settings > Sign-in."),
    (r"sign in to confirm you.?re not a bot",
     "YouTube wants a sign-in before it will serve this video.",
     "Sign in to YouTube in the Browser tab, or pick a browser in Settings > Sign-in."),
    (r"sign in to confirm your age|age[- ]restricted|inappropriate for some users",
     "This video is age-restricted.",
     "Sign in to an adult account in the Browser tab, then try again."),
    (r"empty media response|login required|requires authentication|use --cookies|"
     r"only available for registered users|isn't available to everyone|"
     r"can't be seen by certain audiences|only available to logged-in",
     "This link is only visible to a signed-in account.",
     "Sign in to the site in the Browser tab, or pick a browser in Settings > Sign-in."),
    (r"private video|this video is private",
     "This video is private.",
     "Only the uploader and people they share it with can see it."),
    (r"members[- ]only|join this channel",
     "This video is for channel members only.",
     "Sign in with a member account in the Browser tab to download it."),
    (r"not available in your country|geo.?restrict|blocked it in your country",
     "This video isn't available in your region.",
     "The uploader has limited where it can be watched."),
    (r"premieres? in|live event will begin|this live event|scheduled",
     "This premiere or live stream hasn't started yet.",
     "Try again once it has gone live."),
    (r"video unavailable|has been removed|no longer available|account .* terminated|"
     r"this video does not exist|does not exist",
     "This video isn't available anymore.",
     "It may have been deleted or made private."),
    (r"unsupported url",
     "This site or link isn't supported.",
     "Check the link opens a video page, not a search or home page."),
    (r"requested format is not available|no video formats found",
     "That quality isn't available for this video.",
     "Pick a different resolution and try again."),
    # Above the 404 rule: "ffmpeg not found" contains "not found" too.
    (r"ffmpeg (?:is )?not (?:found|installed)|ffprobe .* not found|ffmpeg not found",
     "FFmpeg is missing.",
     "Reinstall the app, or use Updates to install FFmpeg."),
    (r"http error 404|not found",
     "The link wasn't found (404).",
     "Check the link is complete and still online."),
    (r"http error 429|too many requests",
     "The site is rate-limiting requests.",
     "Wait a few minutes, or lower Settings > Simultaneous downloads."),
    (r"http error 403|forbidden",
     "The site refused the request (403).",
     "Try again. If it keeps happening, update yt-dlp from the Updates panel."),
    (r"errno 28|no space left on device|disk full",
     "The disk is full.",
     "Free up space or choose another Save to folder."),
    (r"permission denied|access is denied|errno 13",
     "Windows refused to write the file.",
     "Choose a Save to folder you own, such as Downloads."),
    (r"timed out|timeout|getaddrinfo failed|name or service not known|"
     r"network is unreachable|connection (?:reset|refused|aborted)|"
     r"temporary failure in name resolution|urlopen error|could not resolve host|"
     r"failed to perform, curl: \((?:6|7|28|35|56)\)",
     "Couldn't reach the site.",
     "Check your internet connection and try again."),
]

_PREFIX = re.compile(r"^\s*ERROR:\s*(?:\[[^\]]+\]\s*)?(?:[\w-]+:\s*)?", re.I)


def friendly(error_text):
    """Returns (headline, advice). `advice` is "" when no rule matched and the
    best we can do is the cleaned-up original message."""
    text = str(error_text or "").strip()
    low = text.lower()
    for pattern, headline, advice in _RULES:
        if re.search(pattern, low):
            return headline, advice
    return clean(text), ""


def clean(error_text):
    """The original message with yt-dlp's terminal framing removed: the
    "ERROR: [extractor] id:" prefix, and everything from its command-line
    advice onwards. Keeps the first line only -- the rest is a traceback-ish
    tail nobody reading a card needs."""
    first = str(error_text or "").strip().splitlines()[0] if str(error_text or "").strip() else ""
    first = _PREFIX.sub("", first)
    for tail in (" Confirm you are on the latest version", " Check if this post",
                 " Use --", " If it is not, then use", " please report this issue"):
        idx = first.find(tail)
        if idx > 0:
            first = first[:idx]
    first = first.strip().rstrip(".") + "." if first.strip() else "Something went wrong."
    return first[:1].upper() + first[1:]


def one_line(error_text):
    """Headline and advice joined, for a single-line surface like a card."""
    headline, advice = friendly(error_text)
    return f"{headline} {advice}".strip()


def needs_sign_in(error_text):
    """True for the failures a signed-in session would fix."""
    headline, _ = friendly(error_text)
    return headline in (
        "YouTube wants a sign-in before it will serve this video.",
        "This video is age-restricted.",
        "This link is only visible to a signed-in account.",
        "This video is for channel members only.",
    )
