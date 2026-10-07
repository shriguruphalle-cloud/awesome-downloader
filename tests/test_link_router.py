"""Which tab a pasted link goes to, how an Instagram account's posts are
sorted into videos and pictures, and a YouTube channel giving its Videos
*and* its Shorts. No network: the classifier reads only the link, the
Instagram parser gets made-up API answers, and the channel listing's lookups
are stubbed."""
import _support
from _support import check

from app.core import downloader, instagram, link_router as lr  # noqa: E402

cases = {
    # YouTube
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ": ("video", lr.VIDEO),
    "https://youtu.be/dQw4w9WgXcQ": ("video", lr.VIDEO),
    "https://www.youtube.com/shorts/abc123": ("video", lr.VIDEO),
    "https://www.youtube.com/live/abc123": ("video", lr.VIDEO),
    "https://www.youtube.com/playlist?list=PL123": ("yt_playlist", lr.VIDEO),
    "https://www.youtube.com/@mkbhd": ("yt_channel", lr.VIDEO),
    "https://www.youtube.com/@mkbhd/shorts": ("yt_channel", lr.VIDEO),
    "https://www.youtube.com/@mkbhd/podcasts": ("yt_channel", lr.VIDEO),
    "https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ": ("yt_channel", lr.VIDEO),
    "https://www.youtube.com/c/somename/videos": ("yt_channel", lr.VIDEO),
    "https://music.youtube.com/watch?v=abc": ("video", lr.VIDEO),
    # Instagram
    "https://www.instagram.com/reel/C1a2b3c4/": ("ig_reel", lr.VIDEO),
    "https://www.instagram.com/reels/C1a2b3c4/": ("ig_reel", lr.VIDEO),
    "https://www.instagram.com/tv/C1a2b3c4/": ("ig_reel", lr.VIDEO),
    "https://www.instagram.com/p/C1a2b3c4/": ("ig_post", lr.POST),
    "https://www.instagram.com/p/C1a2b3c4/?img_index=2": ("ig_post", lr.POST),
    "https://www.instagram.com/natgeo/": ("ig_profile", lr.SPLIT),
    "https://instagram.com/natgeo": ("ig_profile", lr.SPLIT),
    "https://www.instagram.com/natgeo/reels/": ("ig_profile", lr.SPLIT),
    "https://www.instagram.com/natgeo/p/C1a2b3c4/": ("ig_post", lr.POST),
    "https://www.instagram.com/stories/natgeo/": ("ig_story", lr.SPLIT),
    "https://www.instagram.com/stories/natgeo/3456789012345678901/": ("ig_story", lr.SPLIT),
    "https://www.instagram.com/stories/highlights/17912345678901234/": ("ig_story", lr.SPLIT),
    "https://www.instagram.com/explore/tags/cats/": ("video", lr.VIDEO),
    "https://scontent.cdninstagram.com/v/t51/123_n.jpg?stp=x": ("image", lr.IMAGES),   # a picture file
    # pictures
    "https://example.com/photos/sunset.JPG": ("image", lr.IMAGES),
    "https://example.com/a/b.webp?w=1200": ("image", lr.IMAGES),
    "https://pbs.twimg.com/media/Fabc?format=jpg&name=large": ("image", lr.IMAGES),
    "https://i.redd.it/abcdef.png": ("image", lr.IMAGES),
    "https://www.reddit.com/gallery/1abcde": ("gallery", lr.IMAGES),
    "https://imgur.com/a/AbCdE": ("gallery", lr.IMAGES),
    "https://www.facebook.com/photo/?fbid=123": ("image", lr.IMAGES),
    # posts, decided by content
    "https://x.com/user/status/1234567890": ("post", lr.POST),
    "https://twitter.com/user/status/1234567890": ("post", lr.POST),
    "https://www.reddit.com/r/pics/comments/abc/title/": ("post", lr.POST),
    "https://www.pinterest.com/pin/123456/": ("post", lr.POST),
    "https://www.facebook.com/somepage/posts/123": ("post", lr.POST),
    # videos and torrents
    "https://www.facebook.com/watch/?v=123": ("video", lr.VIDEO),
    "https://example.com/clip.mp4": ("video", lr.VIDEO),
    "https://vimeo.com/12345": ("video", lr.VIDEO),
    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567": ("torrent", lr.TORRENT),
    "https://releases.ubuntu.com/24.04/ubuntu-24.04-desktop-amd64.iso.torrent": ("torrent", lr.TORRENT),
}
bad = []
for url, (kind, tab) in cases.items():
    r = lr.classify(url)
    if (r.kind, r.tab) != (kind, tab):
        bad.append("%s -> %s/%s, want %s/%s" % (url, r.kind, r.tab, kind, tab))
check(not bad, "misrouted links:\n  " + "\n  ".join(bad))
check(lr.classify("https://www.instagram.com/natgeo/").name == "natgeo", "the account name wasn't kept")
print("%d kinds of link go to the right tab" % len(cases))

# ---- an Instagram account's posts, sorted -----------------------------------
img = lambda n: {"candidates": [{"url": "https://cdn/%s_small.jpg" % n, "width": 320, "height": 320},
                                 {"url": "https://cdn/%s_big.jpg" % n, "width": 1440, "height": 1440}]}
photo = {"pk": 1, "code": "PHOTO1", "media_type": 1, "caption": {"text": "Sunrise over the bay\nmore"},
         "image_versions2": img("p1")}
reel = {"pk": 2, "code": "REEL1", "media_type": 2, "product_type": "clips", "video_duration": 12.4,
        "caption": {"text": "A reel"}, "image_versions2": img("r1"),
        "video_versions": [{"url": "https://cdn/r1.mp4", "width": 720, "height": 1280}]}
carousel = {"pk": 3, "code": "CAR1", "media_type": 8, "caption": None, "carousel_media": [
    {"media_type": 1, "image_versions2": img("c1")},
    {"media_type": 2, "video_duration": 5, "image_versions2": img("c2"),
     "video_versions": [{"url": "https://cdn/c2_lo.mp4", "width": 480, "height": 480},
                        {"url": "https://cdn/c2_hi.mp4", "width": 1080, "height": 1080}]},
    {"media_type": 1, "image_versions2": img("c3")}]}
videos, images = [], []
for item in (photo, reel, carousel):
    v, i = instagram.media_from_feed_item(item, "someone")
    videos += v
    images += i
check([i["url"] for i in images] == ["https://cdn/p1_big.jpg", "https://cdn/c1_big.jpg", "https://cdn/c3_big.jpg"],
      "pictures (largest version, carousel slides included): %r" % [i["url"] for i in images])
check(images[0]["title"] == "Sunrise over the bay", "a post's picture isn't named after its caption")
check([v["url"] for v in videos] == ["https://www.instagram.com/reel/REEL1/", "https://cdn/c2_hi.mp4"],
      "videos (a reel by its link, a carousel video by its file): %r" % [v["url"] for v in videos])
check(videos[0]["duration"] == 12 and videos[0]["uploader"] == "someone", "a reel's details were lost")
print("an Instagram account's photos go to Images, its reels and videos to the queue")

node = {"shortcode": "G1", "is_video": False, "display_url": "https://cdn/g1.jpg",
        "edge_sidecar_to_children": {"edges": [
            {"node": {"is_video": True, "video_url": "https://cdn/g1v.mp4", "display_url": "https://cdn/g1t.jpg"}},
            {"node": {"is_video": False, "display_url": "https://cdn/g1b.jpg"}}]}}
v, i = instagram.media_from_graph_node(node, "someone")
check([x["url"] for x in v] == ["https://cdn/g1v.mp4"] and [x["url"] for x in i] == ["https://cdn/g1b.jpg"],
      "signed-out answers aren't sorted the same way")
print("the signed-out answer (latest 12 posts) is sorted the same way")

# ---- a channel gives its Videos and its Shorts -------------------------------
tab_listing = {"_type": "playlist", "uploader": "Chan", "entries": [
    {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@chan/videos"},
    {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@chan/shorts"},
    {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@chan/streams"}]}
tabs = {
    "https://www.youtube.com/@chan/videos": [{"id": "v1", "ie_key": "Youtube", "title": "Long one", "duration": 600},
                                             {"id": "v2", "ie_key": "Youtube", "title": "[Private video]"}],
    "https://www.youtube.com/@chan/shorts": [{"url": "https://www.youtube.com/shorts/s1", "title": "Short one"}],
    "https://www.youtube.com/@chan/streams": [{"id": "l1", "ie_key": "Youtube", "title": "Stream"}],
}
asked = []


def fake_fetch(url, cookies=None):
    asked.append(url)
    return {"_type": "playlist", "entries": tabs[url]}, {}


downloader.fetch_info_with_sizes = fake_fetch
got = downloader.playlist_entries(tab_listing)
check([e["url"] for e in got] == ["https://www.youtube.com/watch?v=v1", "https://www.youtube.com/shorts/s1"],
      "a channel didn't give its videos and shorts: %r" % [e["url"] for e in got])
check("https://www.youtube.com/@chan/streams" not in asked, "past live streams were read too")
print("a YouTube channel gives its videos and its Shorts")

podcasts = {"_type": "playlist", "entries": [
    {"_type": "url", "url": "https://www.youtube.com/playlist?list=PODA", "title": "Show A"},
    {"_type": "url", "url": "https://www.youtube.com/playlist?list=PODB", "title": "Show B"}]}
tabs.update({"https://www.youtube.com/playlist?list=PODA": [{"id": "e1", "ie_key": "Youtube", "title": "Ep 1"}],
             "https://www.youtube.com/playlist?list=PODB": [{"id": "e2", "ie_key": "Youtube", "title": "Ep 2"}]})
got = downloader.playlist_entries(podcasts)
check([e["title"] for e in got] == ["Ep 1", "Ep 2"], "a Podcasts tab didn't give its episodes: %r" % got)
print("a channel's Podcasts tab gives the episodes")

# ---- a video queued by its file is named after its post ----------------------
import os  # noqa: E402
t = downloader._outtmpl("C:/d", "https://scontent-x.cdninstagram.com/o1/v/t2/123_n.mp4?x=1", "Sunrise: day 1")
check(os.path.basename(t).startswith("Sunrise") and t.endswith(".%(ext)s"), "direct file name: %r" % t)
check(downloader._outtmpl("C:/d", "https://youtube.com/watch?v=x", "T").endswith("%(title)s.%(ext)s"),
      "a page link stopped being named by its own title")
print("a video queued by its file is named after its post")
# ---- a carousel: each video slide queued as its own file, named apart --------
# (formats as Instagram really sends them: no codec details, a silent DASH piece)
def slide(n, video):
    fmts = ([{"format_id": str(k), "protocol": "https", "ext": "mp4", "vcodec": None, "acodec": "none",
              "url": "https://scontent.cdninstagram.com/v/s%d_%d.mp4" % (n, k)} for k in range(3)]
            + [{"format_id": "dash-1v", "protocol": "https", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
                "height": 640, "url": "https://scontent.cdninstagram.com/v/s%d_dash.mp4" % n}]) if video else []
    return {"title": "Video by someone", "formats": fmts,
            "thumbnails": [{"url": "https://scontent.cdninstagram.com/i/s%d.jpg" % n, "width": 1080, "height": 1080}]}


carousel_info = {"title": "Post by someone", "uploader": "someone",
                 "entries": [slide(1, True), slide(2, False), slide(3, True)]}
downloader._run = lambda opts, url, work: carousel_info
res = downloader.split_media("https://www.instagram.com/p/CAROUSEL/")
check([v["url"] for v in res["videos"]] == ["https://scontent.cdninstagram.com/v/s1_0.mp4",
                                            "https://scontent.cdninstagram.com/v/s3_0.mp4"],
      "carousel videos weren't queued as their own best single file: %r" % [v["url"] for v in res["videos"]])
check(len({v["title"] for v in res["videos"]}) == 2, "two slides would save over each other: %r" % res["videos"])
check([i["url"] for i in res["images"]] == ["https://scontent.cdninstagram.com/i/s2.jpg"], "the photo slide was lost")
print("a carousel: each video slide queued as its own file, the photo to Images")

print("\nLINK ROUTER OK")
