"""Pasting several links stacks them, deduplicated, each resolving on its own
and never startable before it has. (One link opens in the form, like Fetch --
see test_queue_form.py.)"""
import _support
from _support import check, pump, qapp, settings, stub_network

qapp()
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

st = settings()
vt = VideoTab(settings=st, download_tab=DownloadTab(settings=st))
calls = stub_network(vt)


def info(title, heights, uploader="Chan", duration=90):
    return {"title": title, "uploader": uploader, "duration": duration,
            "heights": heights, "height": max(heights) if heights else None,
            "is_image": False, "thumbnail_url": "https://t/x.jpg", "thumb_image": None}


# ---- link splitting ------------------------------------------------------------
S = VideoTab._split_links
check(len(S("https://a.com/1\nhttps://a.com/2\nhttps://a.com/3")) == 3, "newlines")
check(len(S("https://a.com/1 https://a.com/2")) == 2, "spaces")
check(S("watch this https://a.com/1, then https://a.com/2.") ==
      ["https://a.com/1", "https://a.com/2"], "prose and trailing punctuation")
check(len(S("https://a.com/1\nhttps://a.com/1")) == 1, "duplicates in one paste")
print("link splitting: ok")

# ---- one link opens in the form, not the queue --------------------------------
vt.url_entry.setText("https://a.com/solo")
check(not vt._queue_strips and calls["fetches"] == ["https://a.com/solo"], "a single link didn't go to the form")
vt.url_entry.clear()
calls["lookups"].clear()

# ---- a block of three stacks three, all pending -------------------------------
vt.url_entry.setText("https://a.com/1\nhttps://a.com/2\nhttps://a.com/3")
pump()
check(len(vt._queue_strips) == 3 and len(calls["lookups"]) == 3, "block didn't stack")
c0, c1, c2 = vt._queue_strips
check(c0._pending and not c0.start_btn.isEnabled(), "a pending card can be started")
check(vt.queue_card.isVisibleTo(vt), "queue card hidden with cards in it")

vt.start_queue()
check(not calls["downloads"], "Start all fired an unresolved card")

# ---- resolutions land out of order, each on its own card ---------------------
vt._on_strip_info(c2, info("Third", [1080, 720], "C", 61), "")
vt._on_strip_info(c0, info("First", [4320, 2160, 1080], "A", 120), "")
check(c0._full_title == "First" and c0.payload["height"] == 4320, "card 0 wrong")
check(c2._full_title == "Third" and c2.payload["height"] == 1080, "card 2 wrong")
check(c1._pending, "an unrelated card was touched")
options = [c0.res_combo.itemText(i) for i in range(c0.res_combo.count())]
check(options == ["4320p", "2160p", "1080p"], options)
c0.res_combo.setCurrentIndex(2)
check(c0.payload["height"] == 1080, "the per-card resolution didn't reach the payload")

# ---- a failed lookup says why, on that card only ------------------------------
vt._on_strip_info(c1, None, "ERROR: [youtube] x: Video unavailable")
print("failed card meta:", repr(c1._full_meta))
check(c1.is_failed() and not c1.start_btn.isEnabled(), "failed card still startable")
check(c1._full_meta == "This video isn't available anymore.", c1._full_meta)

# ---- Start all fires exactly the resolved cards, with their chosen heights ----
vt.start_queue()
pump()
heights = sorted(a[4] for a in calls["downloads"])
print("Start all started:", len(calls["downloads"]), "downloads at", heights)
check(len(calls["downloads"]) == 2, "expected the two resolved cards")
check(heights == [1080, 1080], heights)

# ---- a late result for a removed card is ignored -----------------------------
vt._on_strip_remove(c1)
vt._on_strip_info(c1, info("late", [720]), "")

# ---- re-pasting a stacked link doesn't duplicate it ---------------------------
check(not any(c.payload["url"] in ("https://a.com/1", "https://a.com/3") for c in vt._queue_strips),
      "started downloads stayed in the queue")
vt.queue_links(["https://a.com/7"])
n = len(vt._queue_strips)
vt.url_entry.clear()
vt.url_entry.setText("https://a.com/7 https://a.com/9")
check(len(vt._queue_strips) == n + 1, "a duplicate was stacked")

# ---- audio mode hides the resolution picker ----------------------------------
vt.audio_radio.setChecked(True)
vt.queue_links(["https://a.com/audio"])
ac = vt._queue_strips[-1]
vt._on_strip_info(ac, info("Song", [1080]), "")
check(not ac.res_combo.isVisibleTo(ac) and "MP3" in ac._full_meta, "audio card shows a resolution")

print("\nQUEUE PASTE OK")

# ---- Clear queue empties it -----------------------------------------------------
vt.queue_links(["https://a.com/c1", "https://a.com/c2"])
check(vt._queue_strips, "nothing queued to clear")
vt.clear_queue()
check(not vt._queue_strips, "Clear queue left cards behind")
check(vt.start_queue_btn.text() in ("Download all", "Download"), vt.start_queue_btn.text())
print("clear queue: ok")
