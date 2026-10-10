"""Every number the Music tab's suggestions are tuned with, in one place.

What a listen says about a song (an implicit score per event), how fast old
listening fades, how much the first-run picks count and how quickly real
listening takes over from them, and how the feed is mixed.
"""

# ---- what each thing you do says about a song (and its artist) ----------------
EVENT_SCORES = {
    "complete": 1.0,        # played to (near) the end
    "play": 0.15,           # started
    "search_play": 0.8,     # searched for, then played: a strong want
    "repeat": 1.5,          # played again (repeat-one, or again within the day)
    "like": 2.5,            # starred
    "playlist_add": 1.2,    # added to the queue by hand
    "download": 2.0,        # kept as a file
    "dislike": -4.0,        # "Not for me"
}
SKIP_EARLY_S = 30           # a skip in the first half-minute ...
SKIP_EARLY = -0.8           # ... says "no" ...
SKIP_LATE = -0.25           # ... a later one, only "enough for now"
COMPLETE_AT = 0.85          # this far through counts as played to the end
DISLIKE_ARTIST_SHARE = 0.5  # "Not for me" is about the song: half of it reaches its artist
ARTIST_DROPPED_BELOW = -6.0 # an artist this far down (many skips and dislikes) isn't suggested

# ---- time --------------------------------------------------------------------------
HALF_LIFE_DAYS = 30         # a listen counts half as much a month on
SESSION_HOURS = 2           # "right now": what was played in the last two hours ...
SESSION_BOOST = 0.6         # ... leans the next picks toward it
HOUR_BOOST = 0.25           # artists you play at this time of day, a little ahead

# ---- the first-run picks --------------------------------------------------------------
SEED_ARTIST = 3.0           # a picked artist starts this strong ...
SEED_FADE_PLAYS = 10        # ... fading (e^-n/10) as real listening comes in
SEED_LANGUAGE = 1.2         # the first-ranked language; later ones a little less
SEED_LANGUAGE_STEP = 0.75

# ---- making a list ------------------------------------------------------------------------
EXPLORE_SHARE = 0.12        # about one song in eight from outside what you know
RECENT_PENALTY_DAYS = 2     # a song played in the last two days drops back
MAX_PER_ARTIST = 3          # in one mix or list
TOP_ARTISTS = 6             # artists whose pages feed the suggestions
CACHE_DAYS = 3              # artist pages and radios are fetched again after this
MIX_LENGTH = 30
EVENTS_KEPT = 6000          # the oldest listens beyond this are summed into the artist totals
