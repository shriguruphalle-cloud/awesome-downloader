"""Everything an Instagram account has posted: its photos for the Images tab,
its videos and reels for the Video tab's queue.

yt-dlp's own account extractor has been switched off upstream (it is marked
not working), so this reads Instagram's web API directly -- the same calls
instagram.com makes for its own profile page -- through yt-dlp's networking,
so the cookies (the Browser tab's sign-in, or a browser chosen in Settings)
and the Chrome TLS fingerprint are the same as every other request the app
makes.

Signed in, the website's own API reads the whole feed. Signed out, the
website now asks for a sign-in even for a public account -- but Instagram's
own app API (i.instagram.com, asked the way the Android app asks) still
lists a public account's posts, a page of 12 at a time, so a public account
downloads in full without one. Either way the read stops at POST_LIMIT
posts and waits a moment between pages: Instagram limits how fast an
account can be read, and reading faster than a person scrolls risks being
cut off (or, signed in, the account being challenged).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ..logging_setup import get_logger

logger = get_logger("instagram")

API = "https://www.instagram.com/api/v1"
APP_ID = "936619743392459"      # instagram.com's own web app id
# Instagram's Android app, as it identifies itself: its API answers signed out
APP_API = "https://i.instagram.com/api/v1"
APP_UA = ("Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; "
          "en_US; 458229237)")
APP_ANDROID_ID = "567067343352427"
POST_LIMIT = 300
PAGE_SIZE = 33
PAGE_PAUSE_S = 1.2


class InstagramError(Exception):
    pass


def _caption_title(text, fallback):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = " ".join(line.split())
    if len(line) > 70:
        line = line[:67].rstrip() + "..."
    return line or fallback


def _best(candidates):
    """The largest of Instagram's renditions of one image or video."""
    best, area = None, -1
    for c in candidates or []:
        if not isinstance(c, dict) or not c.get("url"):
            continue
        a = (c.get("width") or 0) * (c.get("height") or 0)
        if a > area:
            best, area = c, a
    return best["url"] if best else None


def _permalink(code, reel):
    return "https://www.instagram.com/%s/%s/" % ("reel" if reel else "p", code)


# ------------------------------------------------------------------ parsing
def media_from_feed_item(item, username=""):
    """One post from the v1 feed API -> (videos, images).

    videos: [{"url", "title", "duration", "uploader", "thumbnail_url"}] --
    the shape the Video tab's queue takes. A post that is one video (or a
    reel) is queued by its own link, so the download is yt-dlp's best
    version of it; a video inside a carousel has no link of its own, so its
    file is queued directly.
    images: [{"title", "url", "is_video": False}] -- the Images tab's shape.
    """
    code = item.get("code") or ""
    caption = (item.get("caption") or {}).get("text") if isinstance(item.get("caption"), dict) else ""
    title = _caption_title(caption, "Instagram post %s" % code if code else "Instagram post")
    reel = item.get("product_type") == "clips"
    videos, images = [], []
    kind = item.get("media_type")
    if kind == 8:                                   # carousel
        for n, child in enumerate(item.get("carousel_media") or [], start=1):
            slide_title = "%s (%d)" % (title, n)
            thumb = _best((child.get("image_versions2") or {}).get("candidates"))
            if child.get("media_type") == 2:
                url = _best(child.get("video_versions"))
                if url:
                    videos.append({"url": url, "title": slide_title, "duration": int(child.get("video_duration") or 0),
                                   "uploader": username, "thumbnail_url": thumb})
            elif thumb:
                images.append({"title": slide_title, "url": thumb, "is_video": False})
    elif kind == 2:                                 # one video or reel
        if code:
            videos.append({"url": _permalink(code, reel), "title": title,
                           "duration": int(item.get("video_duration") or 0), "uploader": username,
                           "thumbnail_url": _best((item.get("image_versions2") or {}).get("candidates"))})
    else:                                           # one photo
        url = _best((item.get("image_versions2") or {}).get("candidates"))
        if url:
            images.append({"title": title, "url": url, "is_video": False})
    return videos, images


def media_from_graph_node(node, username=""):
    """The same for the anonymous profile answer's GraphQL-shaped posts."""
    code = node.get("shortcode") or ""
    edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [])
    caption = ((edges[0] or {}).get("node") or {}).get("text") if edges else ""
    title = _caption_title(caption, "Instagram post %s" % code if code else "Instagram post")
    reel = node.get("product_type") == "clips"
    videos, images = [], []
    children = ((node.get("edge_sidecar_to_children") or {}).get("edges") or [])
    if children:
        for n, edge in enumerate(children, start=1):
            child = edge.get("node") or {}
            slide_title = "%s (%d)" % (title, n)
            if child.get("is_video") and child.get("video_url"):
                videos.append({"url": child["video_url"], "title": slide_title, "duration": 0,
                               "uploader": username, "thumbnail_url": child.get("display_url")})
            elif child.get("display_url"):
                images.append({"title": slide_title, "url": child["display_url"], "is_video": False})
    elif node.get("is_video"):
        if code:
            videos.append({"url": _permalink(code, reel), "title": title,
                           "duration": int(node.get("video_duration") or 0), "uploader": username,
                           "thumbnail_url": node.get("display_url") or node.get("thumbnail_src")})
    elif node.get("display_url"):
        images.append({"title": title, "url": node["display_url"], "is_video": False})
    return videos, images


# ------------------------------------------------------------------ fetching
def _get_json(ydl, url, referer):
    from yt_dlp.networking import Request
    headers = {"X-IG-App-ID": APP_ID, "X-Requested-With": "XMLHttpRequest", "Referer": referer,
               "Accept": "*/*", "X-ASBD-ID": "129477"}
    try:
        csrf = next((c.value for c in ydl.cookiejar if c.name == "csrftoken" and "instagram" in c.domain), None)
        if csrf:
            headers["X-CSRFToken"] = csrf
    except Exception:   # noqa: BLE001 -- no cookies is fine
        pass
    with ydl.urlopen(Request(url, headers=headers)) as resp:
        raw = resp.read()
    try:
        return json.loads(raw)
    except ValueError:
        # A login page instead of JSON: Instagram wants a session for this.
        raise InstagramError("login required")


def _app_get(url, timeout=20):
    """Instagram's app API, signed out -- it still answers for a public
    account where the website's asks for a sign-in."""
    headers = {"User-Agent": APP_UA, "X-IG-App-ID": APP_ANDROID_ID, "Accept": "*/*", "Accept-Language": "en-US"}
    try:
        from curl_cffi import requests as creq
        resp = creq.get(url, headers=headers, timeout=timeout)
        status, body = resp.status_code, resp.content
    except ImportError:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read()
    if status == 404:
        raise InstagramError("not found")
    if status in (401, 403):
        raise InstagramError("login required")
    if status == 429:
        raise InstagramError("Instagram is limiting how fast it answers — wait a few minutes and try again.")
    if status >= 400:
        raise InstagramError("Instagram answered %d" % status)
    try:
        return json.loads(body)
    except ValueError:
        raise InstagramError("login required")


def _feed_pages(user_id, limit, cancelled=None, pause=None):
    """The app API's pages of an account's posts (v1 items), newest first,
    until `limit` posts or the end. Yields lists of items."""
    pause = PAGE_PAUSE_S if pause is None else pause
    max_id, got = "", 0
    while got < limit:
        if cancelled and cancelled():
            return
        url = "%s/feed/user/%s/?count=%d%s" % (APP_API, user_id, PAGE_SIZE,
                                               "&max_id=%s" % urllib.parse.quote(max_id) if max_id else "")
        page = _app_get(url)
        items = page.get("items") or []
        got += len(items)
        yield items
        max_id = page.get("next_max_id") or ""
        if not page.get("more_available") or not max_id or not items:
            return
        time.sleep(pause)


def profile_media_signed_out(username, limit=POST_LIMIT, progress=None, cancelled=None):
    """profile_media(), signed out, through Instagram's app API."""
    try:
        info = _app_get("%s/users/web_profile_info/?username=%s" % (APP_API, urllib.parse.quote(username)))
    except InstagramError as e:
        if str(e) == "not found":
            raise InstagramError("There's no Instagram account called %s." % username)
        raise
    user = ((info or {}).get("data") or {}).get("user")
    if not user or not user.get("id"):
        raise InstagramError("There's no Instagram account called %s." % username)
    title = user.get("full_name") or username
    timeline = user.get("edge_owner_to_timeline_media") or {}
    total = int(timeline.get("count") or 0)
    if user.get("is_private"):
        raise InstagramError("%s is a private account. Sign in to Instagram in the Browser tab with an account that "
                             "follows it to download it." % username)
    videos, images, posts, seen, stopped = [], [], 0, set(), ""
    try:
        for items in _feed_pages(user["id"], limit, cancelled):
            for item in items:
                if posts >= limit or item.get("pk") in seen:
                    continue
                seen.add(item.get("pk"))
                v, i = media_from_feed_item(item, username)
                videos += v
                images += i
                posts += 1
            if progress:
                progress(posts)
    except InstagramError as e:
        stopped = str(e)
        logger.info("Instagram's app API stopped after %d posts of %s: %s", posts, username, e)
    if not posts:
        # no pages at all: the first dozen the profile answer itself carries
        for edge in timeline.get("edges") or []:
            v, i = media_from_graph_node(edge.get("node") or {}, username)
            videos += v
            images += i
            posts += 1
    return {"title": title, "username": username, "videos": videos, "images": images, "posts": posts,
            "total": total, "partial": posts < min(total, limit) or bool(stopped), "signed_in": False,
            "stopped": stopped}


def post_media_signed_out(user_id, code, username="", pages=4):
    """A post's every photo and video, signed out, found in its account's
    feed -- the post's own page shows a signed-out visitor only the first of
    a carousel. (videos, images), or None if it isn't among the latest."""
    for items in _feed_pages(user_id, pages * 12, pause=PAGE_PAUSE_S / 2):
        for item in items:
            if item.get("code") == code:
                return media_from_feed_item(item, username)
    return None


def _signed_in(ydl):
    try:
        return any(c.name == "sessionid" and "instagram" in c.domain and c.value for c in ydl.cookiejar)
    except Exception:   # noqa: BLE001
        return False


def profile_media(username, cookies_from_browser=None, limit=POST_LIMIT, progress=None, cancelled=None):
    """Blocking. Reads `username`'s posts. Returns a dict:
        {"title", "username", "videos": [...], "images": [...], "posts": n,
         "total": the account's post count, "partial": bool, "signed_in": bool}
    `progress(n_posts)` is called as pages arrive; `cancelled()` stops it.
    Raises InstagramError for a private account (unless followed), an account
    that doesn't exist, or an answer that needs a sign-in."""
    from . import downloader
    referer = "https://www.instagram.com/%s/" % username
    opts = downloader.base_ydl_opts(cookies_from_browser, referer)

    def work(ydl):
        signed_in = _signed_in(ydl)
        if not signed_in:
            return profile_media_signed_out(username, limit, progress, cancelled)
        try:
            info = _get_json(ydl, "%s/users/web_profile_info/?username=%s" % (API, username), referer)
        except Exception as exc:
            text = str(exc)
            if "404" in text:
                raise InstagramError("There's no Instagram account called %s." % username)
            if "401" in text or "403" in text or "login" in text.lower():
                raise InstagramError("login required")
            raise
        user = ((info or {}).get("data") or {}).get("user")
        if not user:
            raise InstagramError("There's no Instagram account called %s." % username)
        title = user.get("full_name") or username
        timeline = user.get("edge_owner_to_timeline_media") or {}
        total = int(timeline.get("count") or 0)
        if user.get("is_private") and not user.get("followed_by_viewer"):
            raise InstagramError("%s is a private account. Follow it from your signed-in account to download it."
                                 % username)

        videos, images, posts = [], [], 0
        if signed_in and user.get("id"):
            max_id, seen = "", set()
            while posts < limit:
                if cancelled and cancelled():
                    break
                url = "%s/feed/user/%s/?count=%d%s" % (API, user["id"], PAGE_SIZE,
                                                       "&max_id=%s" % max_id if max_id else "")
                page = _get_json(ydl, url, referer)
                for item in page.get("items") or []:
                    if posts >= limit or item.get("pk") in seen:
                        continue
                    seen.add(item.get("pk"))
                    v, i = media_from_feed_item(item, username)
                    videos += v
                    images += i
                    posts += 1
                if progress:
                    progress(posts)
                max_id = page.get("next_max_id") or ""
                if not page.get("more_available") or not max_id:
                    break
                time.sleep(PAGE_PAUSE_S)
            partial = posts < total
        else:
            for edge in timeline.get("edges") or []:
                v, i = media_from_graph_node(edge.get("node") or {}, username)
                videos += v
                images += i
                posts += 1
            partial = total > posts
        return {"title": title, "username": username, "videos": videos, "images": images,
                "posts": posts, "total": total, "partial": partial, "signed_in": signed_in}

    try:
        return downloader._run(opts, referer, work)
    except InstagramError as e:
        if str(e) != "login required":
            raise
        # a sign-in that no longer works (expired, logged out elsewhere): the
        # public account still reads signed out
        logger.info("Instagram's signed-in read of %s was refused; reading it signed out", username)
        return profile_media_signed_out(username, limit, progress, cancelled)
    finally:
        downloader.release_opts(opts)
