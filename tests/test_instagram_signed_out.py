"""Instagram signed out: a public account read through Instagram's app API
(the website asks for a sign-in), page after page, keeping what it read if
Instagram stops answering; a carousel post's every slide found in its
account's feed; and the Images tab fetching a link again by itself once
someone signs in to the site in the Browser tab. No network: Instagram gets
made-up answers shaped like the real ones."""
import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app.core import downloader, instagram  # noqa: E402
from app.utils import browser_cookies  # noqa: E402

instagram.PAGE_PAUSE_S = 0


def item(n, kind=1, children=None):
    it = {"pk": n, "code": "C%03d" % n, "media_type": kind, "caption": {"text": "Made-up shot %d" % n}}
    if kind == 8:
        it["carousel_media"] = children
    else:
        it["image_versions2"] = {"candidates": [{"url": "https://cdn.test/%d.jpg" % n, "width": 1080,
                                                 "height": 1080}]}
    return it


asked = []


def fake_app_get(url, timeout=20):
    asked.append(url)
    if "web_profile_info" in url:
        return {"data": {"user": {"id": "123", "full_name": "Test Shots", "is_private": False,
                                  "edge_owner_to_timeline_media": {"count": 30, "edges": []}}}}
    if "/feed/user/123/" in url:
        if "max_id" not in url:
            return {"items": [item(n) for n in range(1, 13)], "more_available": True, "next_max_id": "m1"}
        if "max_id=m1" in url:
            return {"items": [item(n) for n in range(13, 25)], "more_available": True, "next_max_id": "m2"}
        raise instagram.InstagramError("login required")
    raise AssertionError("unexpected request: %s" % url)


instagram._app_get = fake_app_get
res = downloader.split_media("https://www.instagram.com/testshots/")
check(len(res["images"]) == 24 and res["title"] == "Test Shots",
      "a public account signed out: %d images, %r" % (len(res["images"]), res["title"]))
check(all("i.instagram.com" not in u or "/api/v1/" in u for u in asked) and any("feed/user/123" in u for u in asked),
      "the account wasn't read through the app API: %r" % asked)
check("24 of 30" in res["note"] and "sign in" in res["note"].lower(),
      "stopping part-way isn't said, with what to do: %r" % res["note"])
print("a public account signed out: read page after page; when Instagram stops, what was read is kept and why said")

# a private account: said plainly
instagram._app_get = lambda url, timeout=20: {"data": {"user": {"id": "9", "is_private": True,
                                                                "edge_owner_to_timeline_media": {"count": 5}}}}
try:
    downloader.split_media("https://www.instagram.com/someoneprivate/")
    check(False, "a private account wasn't reported")
except instagram.InstagramError as e:
    check("private" in str(e) and "Browser tab" in str(e), "a private account's message: %s" % e)
print("a private account says it's private, and how to get it")

# ---- a carousel post: its page shows one slide signed out; its account's feed has them all ----
slides = [{"media_type": 1, "image_versions2": {"candidates": [{"url": "https://cdn.test/s1.jpg", "width": 1, "height": 1}]}},
          {"media_type": 2, "video_versions": [{"url": "https://cdn.test/s2.mp4", "width": 1, "height": 1}],
           "image_versions2": {"candidates": [{"url": "https://cdn.test/s2.jpg", "width": 1, "height": 1}]}},
          {"media_type": 1, "image_versions2": {"candidates": [{"url": "https://cdn.test/s3.jpg", "width": 1, "height": 1}]}}]


def feed_with_carousel(url, timeout=20):
    if "/feed/user/123/" in url:
        return {"items": [item(1), dict(item(2, 8, slides), code="CARO")], "more_available": False}
    raise AssertionError("unexpected request: %s" % url)


instagram._app_get = feed_with_carousel
one_slide = {"title": "Post by testshots", "uploader_id": "123", "channel": "testshots", "formats": [],
             "thumbnails": [{"url": "https://cdn.test/s1.jpg", "width": 1080, "height": 1080}]}
downloader._run = lambda opts, url, work: dict(one_slide)
res = downloader.split_media("https://www.instagram.com/p/CARO/")
check([i["url"] for i in res["images"]] == ["https://cdn.test/s1.jpg", "https://cdn.test/s3.jpg"]
      and [v["url"] for v in res["videos"]] == ["https://cdn.test/s2.mp4"],
      "a carousel signed out lost slides: %r / %r" % (res["images"], res["videos"]))
print("a carousel post signed out: every slide, from its account's feed")

# not among the account's latest: what the post page gave stands
instagram._app_get = lambda url, timeout=20: {"items": [item(1)], "more_available": False}
res = downloader.split_media("https://www.instagram.com/p/OLDPOST/")
check([i["url"] for i in res["images"]] == ["https://cdn.test/s1.jpg"], "the post page's own answer was lost")
print("an older post keeps what its own page gave")

# ---- the Images tab: signed in at last -> fetched again by itself --------------------------------
win, tabs = build_window(tabs=("images", "download"), size=(1100, 720))
it = tabs["images"]
fetched, back = [], []
it.fetch_links = lambda urls, append=False: fetched.extend(urls)
it.signed_in_again.connect(lambda: back.append(True))
link = "https://www.instagram.com/testshots/"
it._wait_for_sign_in(link)
check(it._sign_in_timer.isActive() and "Browser tab" in it.status_label.text(), "not waiting for the sign-in")
browser_cookies.scoped_rows = lambda url: [(".instagram.com", "csrftoken", "x", "/", 0, 1)]
it._check_signed_in()
check(not fetched and it._sign_in_timer.isActive(), "fetched again before the sign-in")
browser_cookies.scoped_rows = lambda url: [(".instagram.com", "sessionid", "x", "/", 0, 1)]
it._check_signed_in()
check(fetched == [link] and back and not it._sign_in_timer.isActive(),
      "signing in didn't fetch the link again: %r" % fetched)
print("the Images tab fetches the link again by itself once you've signed in in the Browser tab")
win.close()
settle(200)
print("\nINSTAGRAM SIGNED OUT OK")
