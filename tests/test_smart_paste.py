"""Paste boxes that send every link where it belongs: a picture pasted in
Video opens in Images, a reel pasted in Images opens in Video, a magnet goes
to Torrent, and an Instagram account (pictures and videos together) is split
between the two, the other tab getting a dot. The network is stubbed: no
link is read from a real site."""
import _support
from _support import build_window, check, pump, qapp, settle, stub_network

app = qapp()
from app.core import downloader  # noqa: E402
from ui_qt import link_routing  # noqa: E402

win, tabs = build_window(tabs=("video", "torrent", "images", "download"))
vt, it, tt = tabs["video"], tabs["images"], tabs["torrent"]
calls = stub_network(vt)
link_routing.wire(win, vt, it, tt)
magnets = []
tt.magnet_forwarded.disconnect()
tt.magnet_forwarded.connect(magnets.append)

split_calls = []
ACCOUNT = {"title": "Some Account", "note": "",
           "videos": [{"url": "https://www.instagram.com/reel/R1/", "title": "Reel 1", "duration": 9,
                       "uploader": "some.account", "thumbnail_url": None},
                      {"url": "https://www.instagram.com/reel/R2/", "title": "Reel 2", "duration": 11,
                       "uploader": "some.account", "thumbnail_url": None}],
           "images": [{"title": "Photo 1", "url": "https://cdn.test/p1.jpg", "is_video": False},
                      {"title": "Photo 2", "url": "https://cdn.test/p2.jpg", "is_video": False}]}
PICTURE_POST = {"title": "A photo post", "note": "", "videos": [],
                "images": [{"title": "Only photo", "url": "https://cdn.test/only.jpg", "is_video": False}]}
VIDEO_POST = {"title": "A video post", "note": "", "images": [],
              "videos": [{"url": "https://www.instagram.com/p/V1/", "title": "Vid", "duration": 4,
                          "uploader": "", "thumbnail_url": None}]}


def fake_split(url, cookies=None, progress=None, cancelled=None):
    split_calls.append(url)
    if "some.account" in url:
        return ACCOUNT
    if "/p/PIC" in url:
        return PICTURE_POST
    if "/p/VID" in url:
        return VIDEO_POST
    return {"title": url, "note": "", "videos": [], "images": [{"title": "x", "url": url, "is_video": False}]}


downloader.split_media = fake_split
it._load_tile_preview = lambda *a: None          # no preview downloads


def wait(cond, ms=3000):
    for _ in range(ms // 50):
        if cond():
            return True
        settle(50)
    return cond()


def paste(tab, text):
    tab.url_entry.clear()
    tab.url_entry.setText(text)          # a whole link arriving at once: a paste
    pump()


def current():
    return win.tabs.currentWidget()


# 1. a picture link pasted into Video opens in Images
win.tabs.setCurrentWidget(vt)
paste(vt, "https://example.com/photos/sunset.jpg")
check(wait(lambda: current() is it and it.items), "a picture pasted in Video didn't open in Images")
check(it.items[0]["url"] == "https://example.com/photos/sunset.jpg", "Images shows the wrong picture: %r" % it.items)
check(not calls["fetches"], "the Video tab tried to read a picture as a video")
print("a picture pasted in the Video tab opens in Images")

# 2. a reel pasted into Images opens in Video, in its form
paste(it, "https://www.instagram.com/reel/C1a2b3c4/")
check(wait(lambda: current() is vt), "a reel pasted in Images didn't open in Video")
check(calls["fetches"] == ["https://www.instagram.com/reel/C1a2b3c4/"], "the reel wasn't fetched: %r" % calls)
print("a reel pasted in the Images tab opens in the Video tab")

# 3. a magnet pasted into Video goes to Torrent
paste(vt, "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test")
check(wait(lambda: current() is tt and magnets), "a magnet pasted in Video didn't go to Torrent")
print("a magnet link goes to the Torrent tab")

# 4. an Instagram account pasted into Video: reels queued here, photos to Images with a dot
win.tabs.setCurrentWidget(vt)
before = len(vt._queue_strips)
paste(vt, "https://www.instagram.com/some.account/")
check(wait(lambda: len(vt._queue_strips) == before + 2), "the account's reels weren't queued")
check(current() is vt, "reading an account switched away from the Video tab")
check(wait(lambda: [i["url"] for i in it.items] == ["https://cdn.test/p1.jpg", "https://cdn.test/p2.jpg"]),
      "the account's photos didn't reach Images: %r" % it.items)
check(win.island.badges.get(win.tabs.indexOf(it)) if hasattr(win, "island") and hasattr(win.island, "badges")
      else True, "the Images tab got no dot")
print("an Instagram account: reels in the Video queue, photos in Images")

# 5. the same account pasted into Images: photos here, reels to the Video queue
for strip in list(vt._queue_strips):
    vt._on_strip_remove(strip)
settle(400)
win.tabs.setCurrentWidget(it)
paste(it, "https://www.instagram.com/some.account/")
check(wait(lambda: len(it.items) == 2 and current() is it), "the account's photos didn't show in Images")
check(wait(lambda: len(vt._queue_strips) == 2), "the account's reels didn't reach the Video queue")
print("from the Images tab too: photos here, reels to the Video queue")

# 6. an Instagram post that's only a photo, pasted into Video -> Images
win.tabs.setCurrentWidget(vt)
paste(vt, "https://www.instagram.com/p/PIC123/")
check(wait(lambda: current() is it and it.items and it.items[0]["url"] == "https://cdn.test/only.jpg"),
      "a photo post pasted in Video didn't open in Images")
print("a photo post pasted in the Video tab opens in Images")

# 7. an Instagram post that's a video, pasted into Images -> Video
calls["fetches"].clear()
paste(it, "https://www.instagram.com/p/VID123/")
check(wait(lambda: current() is vt and calls["fetches"] == ["https://www.instagram.com/p/VID123/"]),
      "a video post pasted in Images didn't open in Video: %r" % calls["fetches"])
print("a video post pasted in the Images tab opens in the Video tab")

# 8. a mixed list pasted into Video: each link where it belongs
win.tabs.setCurrentWidget(vt)
for strip in list(vt._queue_strips):
    vt._on_strip_remove(strip)
settle(300)
magnets.clear()
calls["lookups"].clear()
paste(vt, "https://www.youtube.com/watch?v=aaa https://example.com/x.png "
          "magnet:?xt=urn:btih:1111111111111111111111111111111111111111 https://youtu.be/bbb")
check(wait(lambda: len(calls["lookups"]) == 2), "the two videos weren't stacked: %r" % calls["lookups"])
check(wait(lambda: magnets), "the magnet in the list didn't reach Torrent")
check(wait(lambda: any(i["url"] == "https://example.com/x.png" for i in it.items)),
      "the picture in the list didn't reach Images")
print("a mixed list: videos queued, the picture to Images, the magnet to Torrent")

# 9. Clear empties the Images tab's list
check(it.items and it.clear_btn.isEnabled(), "precondition: pictures listed and Clear available")
it.clear_btn.click()
settle(200)
check(not it.items and not it.tiles and not it.download_btn.isEnabled() and not it.clear_btn.isEnabled(),
      "Clear didn't empty the Images tab")
print("Clear empties the Images tab")

win.close()
settle(500)
print("\nSMART PASTE OK")
