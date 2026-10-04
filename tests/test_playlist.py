"""A playlist or channel link becomes one ready card per video, in place of
the pending card it made -- without a network: the downloader is stubbed."""
import _support
from _support import check, pump, qapp, settings

qapp()
from app.core import downloader
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

ENTRIES = [
    {"url": "https://www.youtube.com/watch?v=v%d" % i, "title": "Episode %d" % i,
     "duration": 600 + i, "uploader": "Channel", "thumbnail_url": "https://t/%d.jpg" % i}
    for i in range(1, 6)
]
FAKE_PLAYLIST = {"_type": "playlist", "title": "Season One", "entries": [{"url": e["url"]} for e in ENTRIES]}

downloader.fetch_info_with_sizes = lambda url, cookies_from_browser=None: (FAKE_PLAYLIST, {})
downloader.playlist_entries = lambda info, cookies_from_browser=None: list(ENTRIES)

st = settings()
vt = VideoTab(settings=st, download_tab=DownloadTab(settings=st))
thumbs = []
vt._thumb_only_thread = lambda card, url: thumbs.append(url)

# A normal link before and after, so "in place" means something.
vt._suppress_url_change = True
before = vt._add_queue_strip(vt._blank_payload("https://x.test/before"))
pending = vt._add_queue_strip(vt._blank_payload("https://www.youtube.com/playlist?list=PL1"))
pending.mark_pending()
after = vt._add_queue_strip(vt._blank_payload("https://x.test/after"))
vt._suppress_url_change = False

# Run the real lookup body in this thread; its signal then delivers directly.
vt._strip_info_thread(pending, "https://www.youtube.com/playlist?list=PL1")
pump()

urls = [c.payload["url"] for c in vt._queue_strips]
print("queue now:", [u.rsplit("/", 1)[-1] for u in urls])
check(pending not in vt._queue_strips, "the pending playlist card wasn't replaced")
check(len(vt._queue_strips) == 7, len(vt._queue_strips))
check(urls[0].endswith("before") and urls[-1].endswith("after"), "not inserted in place")
check(urls[1:6] == [e["url"] for e in ENTRIES], "entries out of order")
check(vt.status_label.text() == "Stacked 5 videos from “Season One”.", vt.status_label.text())

card = vt._queue_strips[1]
options = [card.res_combo.itemText(i) for i in range(card.res_combo.count())]
print("entry card:", card._full_title, "|", card._full_meta, "|", options)
check(card.start_btn.isEnabled() and not card._pending, "entry card not ready to start")
check(options[0] == "Best" and "1080p" in options, options)
check(card.payload["height"] is None, "Best should mean height None")
check(len(thumbs) == 5, "thumbnails not requested through the pool: %r" % thumbs)

# Choosing a height, then Best again, really goes back to "best".
card.res_combo.setCurrentIndex(options.index("720p"))
check(card.payload["height"] == 720, card.payload["height"])
card.res_combo.setCurrentIndex(0)
check(card.payload["height"] is None, "picking Best after 720p left 720p set")

# ---- stacking the same playlist again adds nothing twice ---------------------
again = vt._add_queue_strip(vt._blank_payload("https://www.youtube.com/playlist?list=PL1&x=2"))
again.mark_pending()
vt._strip_info_thread(again, "https://www.youtube.com/playlist?list=PL1&x=2")
pump()
check(len(vt._queue_strips) == 7, "duplicates stacked: %d" % len(vt._queue_strips))
check("already in the queue" in vt.status_label.text(), vt.status_label.text())
print("re-pasted playlist:", vt.status_label.text())

# ---- Settings > Preferred quality picks the default on new cards -----------
st["preferred_quality"] = "720"
for c in list(vt._queue_strips):
    vt._on_strip_remove(c)
vt._on_form_playlist("https://www.youtube.com/playlist?list=PL1", "Season One", ENTRIES)
pump()
first = vt._queue_strips[0]
print("\nwith preferred quality 720:", first.res_combo.currentText())
check(first.res_combo.currentText() == "720p" and first.payload["height"] == 720,
      first.res_combo.currentText())

# The form path (clipboard Fetch of a playlist) stacks too, and clears the form.
check(len(vt._queue_strips) == 5, len(vt._queue_strips))
check(not vt.info_card.isVisibleTo(vt), "form left filled after a playlist fetch")

# ---- persistence keeps the ladder and the choice --------------------------
first.res_combo.setCurrentIndex(0)   # Best
vt._queue_strips[1].res_combo.setCurrentIndex(
    [vt._queue_strips[1].res_combo.itemText(i) for i in range(vt._queue_strips[1].res_combo.count())].index("1440p"))
vt.save_state()
vt2 = VideoTab(settings=st, download_tab=DownloadTab(settings=st))
pump()
r0, r1 = vt2._queue_strips[0], vt2._queue_strips[1]
print("restored:", r0.res_combo.currentText(), r1.res_combo.currentText())
check(r0.res_combo.currentText() == "Best" and r0.payload["height"] is None, "Best not restored")
check(r1.res_combo.currentText() == "1440p" and r1.payload["height"] == 1440, "1440p not restored")

# ---- an empty playlist fails the card instead of vanishing ------------------
downloader.playlist_entries = lambda info, cookies_from_browser=None: []
empty = vt._add_queue_strip(vt._blank_payload("https://www.youtube.com/playlist?list=EMPTY"))
empty.mark_pending()
vt._strip_info_thread(empty, "https://www.youtube.com/playlist?list=EMPTY")
pump()
check(empty in vt._queue_strips and empty.is_failed(), "empty playlist not reported")
print("empty playlist card:", empty._full_meta)

print("\nPLAYLIST OK")
