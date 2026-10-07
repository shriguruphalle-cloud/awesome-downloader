"""YouTube Music's answers read right (search, an artist's page, an album's
page), one search for every library, and a song played without anyone's
sign-in -- or fetched to the cache when its stream won't open. No network:
YouTube Music and yt-dlp get made-up answers shaped like the real ones."""
import os

import _support
from _support import check

from app.core import downloader, music_sources as ms, ytmusic  # noqa: E402

ARTIST, ALBUM = ytmusic.ARTIST, ytmusic.ALBUM


def run(text, browse=None, page=None, video=None):
    r = {"text": text}
    if browse:
        r["navigationEndpoint"] = {"browseEndpoint": {
            "browseId": browse,
            "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {"pageType": page}}}}
    if video:
        r["navigationEndpoint"] = {"watchEndpoint": {"videoId": video}}
    return r


def flex(*runs):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": list(runs)}}}


def thumb(name, px=120):
    base = "https://lh3.googleusercontent.com/%s" % name
    return {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
        {"url": base + "=w60-h60-l90-rj", "width": 60, "height": 60},
        {"url": base + "=w%d-h%d-l90-rj" % (px, px), "width": px, "height": px}]}}}


def shelf(items, title="Songs"):
    return {"musicShelfRenderer": {"title": {"runs": [{"text": title}]}, "contents": items}}


def wrap(*sections):
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": list(sections)}}}}]}}}


song = {"musicResponsiveListItemRenderer": {
    "thumbnail": thumb("cover-one"),
    "flexColumns": [flex(run("Made Up Song", video="vid00000001")),
                    flex(run("Test Band", "UCband", ARTIST), run(" • "), run("Test Album", "MPREb_album1", ALBUM),
                         run(" • "), run("3:05")),
                    flex(run("1.2M plays"))],
    "playlistItemData": {"videoId": "vid00000001"}}}
duet = {"musicResponsiveListItemRenderer": {
    "thumbnail": thumb("cover-two"),
    "flexColumns": [flex(run("Second Song", video="vid00000002")),
                    flex(run("Test Band", "UCband", ARTIST), run(" & "), run("Guest", "UCguest", ARTIST),
                         run(" • "), run("Other Album", "MPREb_album2", ALBUM), run(" • "), run("1:02:03"))],
    "playlistItemData": {"videoId": "vid00000002"}}}
album_row = {"musicResponsiveListItemRenderer": {
    "thumbnail": thumb("album-one", 544),
    "navigationEndpoint": {"browseEndpoint": {"browseId": "MPREb_album1"}},
    "flexColumns": [flex(run("Test Album")),
                    flex(run("Album"), run(" • "), run("Test Band", "UCband", ARTIST), run(" • "), run("2021"))]}}
artist_row = {"musicResponsiveListItemRenderer": {
    "thumbnail": thumb("band-photo"),
    "navigationEndpoint": {"browseEndpoint": {"browseId": "UCband"}},
    "flexColumns": [flex(run("Test Band")), flex(run("Artist"), run(" • "), run("1.5M monthly audience"))]}}
top_card = {"musicCardShelfRenderer": {
    "title": {"runs": [run("Test Band", "UCband", ARTIST)]},
    "subtitle": {"runs": [run("Artist"), run(" • "), run("1.5M monthly audience")]},
    "thumbnail": thumb("band-photo", 226), "contents": [song]}}

answers = {None: wrap(top_card), ytmusic.FILTERS["songs"]: wrap(shelf([song, duet])),
           ytmusic.FILTERS["albums"]: wrap(shelf([album_row], "Albums")),
           ytmusic.FILTERS["artists"]: wrap(shelf([artist_row], "Artists"))}
asked = []


def fake_post(endpoint, body, timeout=15):
    asked.append((endpoint, body.get("params"), body.get("query") or body.get("browseId")))
    if endpoint == "search":
        return answers[body.get("params")]
    return pages[body["browseId"]]


ytmusic._post = fake_post

# ---- a search: the four requests the site makes, read ---------------------------
found = ytmusic.search("test band")
check(len(asked) == 4 and {p for _e, p, _q in asked} == set(answers),
      "didn't ask for the top result, songs, albums and artists: %r" % asked)
top = found["top"]
check(top["kind"] == "artist" and top["title"] == "Test Band" and top["browse_id"] == "UCband"
      and top["subtitle"] == "1.5M monthly audience", "the top result read wrong: %r" % top)
check(top["songs"] and top["songs"][0]["title"] == "Made Up Song", "the top card's songs weren't read")
s = found["songs"][0]
check((s["id"], s["title"], s["artist"], s["album"], s["album_browse"], s["artist_browse"], s["duration"])
      == ("yt:vid00000001", "Made Up Song", "Test Band", "Test Album", "MPREb_album1", "UCband", 185),
      "a song read wrong: %r" % s)
check(s["artwork"].endswith("cover-one=w544-h544-l90-rj"), "a song's cover isn't asked for large: %s" % s["artwork"])
check(s["page_url"] == "https://www.youtube.com/watch?v=vid00000001" and s["source"] == "youtube",
      "a song's page: %r" % s["page_url"])
d = found["songs"][1]
check(d["artist"] == "Test Band, Guest" and d["duration"] == 3723, "two artists / an hour-long song: %r" % d)
a = found["albums"][0]
check((a["kind"], a["title"], a["type"], a["year"], a["artist"], a["browse_id"])
      == ("album", "Test Album", "Album", "2021", "Test Band", "MPREb_album1"), "an album read wrong: %r" % a)
ar = found["artists"][0]
check((ar["kind"], ar["title"], ar["subtitle"], ar["browse_id"]) == ("artist", "Test Band", "1.5M monthly audience",
                                                                     "UCband"), "an artist read wrong: %r" % ar)
print("a search: the top result, songs (artists, album, length, large cover), albums and artists")

# one part failing still leaves the rest; all failing is an error
real = ytmusic._post


def half_post(endpoint, body, timeout=15):
    if body.get("params") == ytmusic.FILTERS["albums"]:
        raise ytmusic.YTMusicError("albums down")
    return real(endpoint, body, timeout)


ytmusic._post = half_post
part = ytmusic.search("test band")
check(part["songs"] and part["albums"] == [] and part["artists"], "one failed request lost the rest: %r" % part)
ytmusic._post = lambda *a, **k: (_ for _ in ()).throw(ytmusic.YTMusicError("offline"))
try:
    ytmusic.search("test band")
    check(False, "a search with every request failing didn't say so")
except ytmusic.YTMusicError:
    pass
ytmusic._post = real
print("one part of a search failing leaves the rest; all of it failing is reported")

check(ytmusic.sized("https://lh3.googleusercontent.com/x=w120-h120-l90-rj", 900)
      == "https://lh3.googleusercontent.com/x=w900-h900-l90-rj", "resizing a cover")
check(ytmusic.sized("https://yt3.googleusercontent.com/y=w226-h226-p-l90-rj", 544)
      == "https://yt3.googleusercontent.com/y=w544-h544-p-l90-rj", "resizing an artist photo")
check(ytmusic.sized("https://i.ytimg.com/vi/x/hqdefault.jpg", 544) == "https://i.ytimg.com/vi/x/hqdefault.jpg",
      "a video thumbnail was mangled")

# ---- an artist's page and an album's page -----------------------------------------
top_song = {"musicResponsiveListItemRenderer": {
    "thumbnail": thumb("cover-one"),
    "flexColumns": [flex(run("Made Up Song", video="vid00000001")), flex(run("Test Band", "UCband", ARTIST)),
                    flex(run("2M plays")), flex(run("Test Album", "MPREb_album1", ALBUM))],
    "playlistItemData": {"videoId": "vid00000001"}}}


def two_row(title, bid, subtitle_runs, art):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [run(title, bid, ALBUM if bid.startswith("MPRE") else ARTIST)]},
        "subtitle": {"runs": subtitle_runs},
        "navigationEndpoint": {"browseEndpoint": {"browseId": bid}},
        "thumbnailRenderer": thumb(art, 226)}}


def carousel(title, items):
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": title}]}}},
        "contents": items}}


pages = {
    "UCband": {
        "header": {"musicImmersiveHeaderRenderer": {
            "title": {"runs": [run("Test Band")]},
            "description": {"runs": [run("A band made up for this test.")]},
            "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
                {"url": "https://lh3.googleusercontent.com/banner=w1440-h600-p-l90-rj", "width": 1440,
                 "height": 600}]}}}}},
        "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
            "sectionListRenderer": {"contents": [
                shelf([top_song], "Top songs"),
                carousel("Albums", [two_row("Test Album", "MPREb_album1", [run("2021")], "album-one")]),
                carousel("Singles & EPs", [two_row("A Single", "MPREb_single1",
                                                   [run("Single"), run(" • "), run("2023")], "single-one")]),
                carousel("Videos", [{"musicTwoRowItemRenderer": {
                    "title": {"runs": [run("A Video")]},
                    "navigationEndpoint": {"watchEndpoint": {"videoId": "vidvideo0001"}}}}]),
                carousel("Fans might also like", [two_row("Other Band", "UCother", [run("9M monthly audience")],
                                                          "other-photo")]),
            ]}}}}]}}},
    "MPREb_album1": {
        "contents": {"twoColumnBrowseResultsRenderer": {
            "tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": [
                {"musicResponsiveHeaderRenderer": {
                    "title": {"runs": [run("Test Album")]},
                    "subtitle": {"runs": [run("Album"), run(" • "), run("2021")]},
                    "straplineTextOne": {"runs": [run("Test Band", "UCband", ARTIST)]},
                    "thumbnail": thumb("album-one", 544)}}]}}}}],
            "secondaryContents": {"sectionListRenderer": {"contents": [shelf([
                {"musicResponsiveListItemRenderer": {
                    "flexColumns": [flex(run("First Made Up", video="vidtrack0001")), flex(), flex(run("10K plays"))],
                    "fixedColumns": [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [run("2:30")]}}}],
                    "playlistItemData": {"videoId": "vidtrack0001"}}},
                {"musicResponsiveListItemRenderer": {
                    "flexColumns": [flex(run("Second Made Up", video="vidtrack0002")), flex(), flex()],
                    "fixedColumns": [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [run("4:00")]}}}],
                    "playlistItemData": {"videoId": "vidtrack0002"}}},
            ], "")]}}}}},
}

page = ytmusic.artist("UCband")
check(page["title"] == "Test Band" and page["artwork"].endswith("banner=w1440-h600-p-l90-rj"),
      "the artist's name and banner: %r" % ((page["title"], page["artwork"]),))
check([t["title"] for t in page["songs"]] == ["Made Up Song"] and page["songs"][0]["album"] == "Test Album"
      and page["songs"][0]["year"] == "2021", "the artist's top songs (with the album's year): %r" % page["songs"])
check([x["title"] for x in page["albums"]] == ["Test Album"] and page["albums"][0]["year"] == "2021"
      and page["albums"][0]["artist"] == "Test Band", "the artist's albums: %r" % page["albums"])
check([x["title"] for x in page["singles"]] == ["A Single"] and page["singles"][0]["type"] == "Single",
      "the artist's singles: %r" % page["singles"])
check([x["title"] for x in page["related"]] == ["Other Band"], "related artists: %r" % page["related"])
check(page["about"] == "A band made up for this test.", "the artist's about")
print("an artist's page: name, banner, top songs, albums, singles, related artists (videos left out)")

al = ytmusic.album("MPREb_album1")
check((al["title"], al["artist"], al["artist_browse"], al["year"], al["type"])
      == ("Test Album", "Test Band", "UCband", "2021", "Album"), "an album's header: %r" % al)
check([(t["title"], t["duration"], t["artist"], t["album"]) for t in al["tracks"]]
      == [("First Made Up", 150, "Test Band", "Test Album"), ("Second Made Up", 240, "Test Band", "Test Album")],
      "an album's tracks: %r" % [(t["title"], t["duration"], t["artist"]) for t in al["tracks"]])
check(all(t["artwork"] == al["artwork"] for t in al["tracks"]) and al["artwork"].endswith("=w900-h900-l90-rj"),
      "an album's tracks don't carry its cover")
check(al["length"] == 390, "an album's length")
print("an album's page: title, artist, year, cover and its tracks with lengths")

# ---- one search for every library ------------------------------------------------------
ms.search_openverse = lambda q, page=1: [ms._track(id="ov:1", title="Free one", source="openverse",
                                                   stream="https://cdn.test/1.mp3")]
ms.search_archive = lambda q, page=1: [ms._track(kind="album", id="ia:x", title="Live one", source="archive",
                                                 album_id="x")]
parts = {}
for t in ms.search_everywhere("test band", lambda part, value, err: parts.__setitem__(part, (value, err))):
    t.join(10)
check(set(parts) == {"music", "free", "live"}, "not every library answered: %r" % list(parts))
check(parts["music"][0]["songs"][0]["title"] == "Made Up Song" and parts["free"][0][0]["title"] == "Free one"
      and parts["live"][0][0]["kind"] == "album", "the parts' answers: %r" % parts)
ms.search_archive = lambda q, page=1: (_ for _ in ()).throw(OSError("archive down"))
parts = {}
for t in ms.search_everywhere("x", lambda part, value, err: parts.__setitem__(part, (value, err))):
    t.join(10)
check(parts["live"] == (None, "archive down") and parts["music"][0]["songs"], "a library down: %r" % parts)
print("one search asks every library at once; one being down leaves the others")

# YouTube Music unreadable: yt-dlp's YouTube search stands in for its songs
ytmusic._post = lambda *a, **k: (_ for _ in ()).throw(ytmusic.YTMusicError("changed"))
ms.search_youtube = lambda q: [ms._track(id="yt:fallback01", title="From yt-dlp", source="youtube")]
check(ms.search_music("x")["songs"][0]["title"] == "From yt-dlp", "no stand-in when YouTube Music can't be read")
print("YouTube Music unreadable: yt-dlp's search stands in")

# ---- playing: without anyone's sign-in ----------------------------------------------------
released, seen = [], []
downloader.base_ydl_opts = lambda cookies=None, url=None: {"quiet": True, "cookiefile": "C:/tmp/signed-in.txt"}
downloader.release_opts = lambda opts: released.append(opts.get("cookiefile"))


def fake_run(opts, url, work):
    seen.append(dict(opts))
    if opts.get("skip_download"):
        return {"url": "https://stream.test/audio.m4a"}
    # a download: the file lands where outtmpl says
    path = opts["outtmpl"].replace("%(id)s", url.rsplit("=", 1)[1]).replace("%(ext)s", "m4a")
    open(path, "wb").write(b"m4a")
    return {"id": url.rsplit("=", 1)[1], "ext": "m4a"}


downloader._run = fake_run
yt = ms._track(id="yt:vidplay0001", title="Played", source="youtube",
               page_url="https://www.youtube.com/watch?v=vidplay0001")
check(ms.resolve_stream(yt) == "https://stream.test/audio.m4a", "the stream address")
check("cookiefile" not in seen[-1] and "cookiesfrombrowser" not in seen[-1],
      "the stream was asked for with the Browser tab's sign-in: %r" % seen[-1])
check("C:/tmp/signed-in.txt" in released, "the temporary cookie file wasn't released")
print("a song's stream is asked for without anyone's sign-in (signed-in links won't open in the player)")

path = ms.cache_audio(yt)
check(os.path.dirname(path) == ms.CACHE_DIR and open(path, "rb").read() == b"m4a", "fetched to the cache: %s" % path)
check("cookiefile" not in seen[-1], "the cache fetch used the sign-in when it didn't need to")
check(ms.resolve_stream(yt) == path, "a fetched song isn't played from the cache next time")
n = len(seen)
check(ms.cache_audio(yt) == path and len(seen) == n, "a cached song was fetched again")
yt2 = dict(yt, id="yt:vidplay0002", page_url="https://www.youtube.com/watch?v=vidplay0002")
ms.cache_audio(yt2, cookies=True)
check(seen[-1].get("cookiefile") == "C:/tmp/signed-in.txt", "asked for the sign-in, the fetch didn't use it")
print("a song that won't stream is fetched to the cache (signed in only when asked) and played from there")

ms.CACHE_KEEP = 3
for i in range(5):
    ms.cache_audio(dict(yt, id="yt:vidprune%03d" % i, page_url="https://www.youtube.com/watch?v=vidprune%03d" % i))
left = [f for f in os.listdir(ms.CACHE_DIR)]
check(len(left) == 3, "the cache wasn't kept small: %r" % left)
print("the cache keeps only the latest songs")
print("\nYTMUSIC OK")
