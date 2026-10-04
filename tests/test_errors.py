"""Raw yt-dlp errors become a headline and an action, never terminal advice."""
import _support
from _support import check

from app.core import errors

CASES = [
    ("ERROR: [Instagram] DcoUemhio5E: Instagram sent an empty media response. "
     "Check if this post is accessible in your browser without being logged-in. "
     "If it is not, then use --cookies-from-browser or --cookies for the authentication.",
     "only visible to a signed-in account", True),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser",
     "wants a sign-in", True),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate",
     "age-restricted", True),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
     "isn't available anymore", False),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
     "private", False),
    ("ERROR: Unsupported URL: https://example.com/", "isn't supported", False),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "(403)", False),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
     "rate-limiting", False),
    ("ERROR: Postprocessing: ffmpeg not found. Please install", "FFmpeg is missing", False),
    ("<urlopen error [Errno 11001] getaddrinfo failed>", "Couldn't reach the site", False),
    ("[Errno 28] No space left on device", "disk is full", False),
    ("ERROR: [youtube] abc: Requested format is not available", "quality isn't available", False),
]

for raw, expected, sign_in in CASES:
    headline, advice = errors.friendly(raw)
    print("%-58s | %s" % (headline[:58], advice[:50]))
    check(expected.lower() in headline.lower(), "%r -> %r" % (raw[:50], headline))
    check("--cookies" not in headline + advice, "terminal advice leaked: %r" % headline)
    check(errors.needs_sign_in(raw) == sign_in, "needs_sign_in wrong for %r" % raw[:50])

# An unknown error keeps its own words, minus yt-dlp's framing and advice.
headline, advice = errors.friendly(
    "ERROR: [generic] Something odd happened. Confirm you are on the latest version using yt-dlp -U")
print("\nunmatched ->", repr(headline))
check(headline == "Something odd happened.", headline)
check(advice == "", advice)

# "ffmpeg not found" must not be mistaken for a 404 -- both say "not found".
check(errors.friendly("ffmpeg not found")[0] == "FFmpeg is missing.", "ffmpeg vs 404 ordering")

# The one-line form used on download cards joins both halves.
line = errors.one_line("HTTP Error 404: Not Found")
check(line.startswith("The link wasn't found (404).") and "Check the link" in line, line)

print("\nERRORS OK")
