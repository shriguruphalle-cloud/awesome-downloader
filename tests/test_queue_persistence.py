"""Stacked links survive a restart; a started download leaves the queue (it
lives in the Download tab from then on); a card that never resolved is
looked up again rather than shown ready."""
import json

import _support
from _support import check, pump, qapp, settings

qapp()
from app.utils import video_queue_state
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

LOOKED_UP = []
# Patched on the class: restore runs inside __init__, before an instance
# patch could be installed, and its lookups would otherwise hit the network.
VideoTab._strip_info_thread = lambda self, strip, url: LOOKED_UP.append(url)
VideoTab._thumb_only_thread = lambda self, card, url: None
VideoTab._download_thread = lambda self, *a, **k: None
VideoTab._image_download_thread = lambda self, *a, **k: None

st = settings()


def make_tab():
    LOOKED_UP.clear()
    tab = VideoTab(settings=st, download_tab=DownloadTab(settings=st))
    pump()
    return tab


def info(t, hs, d=120):
    return {"title": t, "uploader": "Chan", "duration": d, "heights": hs,
            "height": max(hs), "is_image": False, "thumbnail_url": "https://t/%s.jpg" % t,
            "thumb_image": None}


vt = make_tab()
vt.url_entry.setText("\n".join("https://a.com/%d" % i for i in range(4)))
cards = list(vt._queue_strips)
cards[0].resolve(info("Never started", [2160, 1080, 720]), None)
cards[1].resolve(info("Will be cancelled", [1080, 720]), None)
cards[2].resolve(info("Will be downloaded", [1080]), None)
cards[3].mark_failed("Video unavailable")
cards[0].res_combo.setCurrentIndex(1)
check(cards[0].payload["height"] == 1080, "picker didn't set height")

jobs_before = len(vt._jobs)
vt._on_strip_download(cards[1])
vt._on_strip_download(cards[2])
check(len(vt._jobs) == jobs_before + 2, "the two downloads didn't start")
check(cards[1] not in vt._queue_strips and cards[2] not in vt._queue_strips,
      "a started download stayed in the queue")

vt.save_state()
saved = json.load(open(video_queue_state.STATE_PATH, encoding="utf-8"))
titles = [e["title"] for e in saved]
print("saved:", titles)
check("Never started" in titles, titles)
check("Will be downloaded" not in titles and "Will be cancelled" not in titles,
      "a started download was saved as queued")
check(len(saved) == 2, titles)

vt2 = make_tab()
check(len(vt2._queue_strips) == 2, "queue did not come back")
first, unresolved = vt2._queue_strips
check(first._full_title == "Never started" and first.payload["height"] == 1080,
      "the picked resolution wasn't kept")
check([first.res_combo.itemText(i) for i in range(first.res_combo.count())] ==
      ["2160p", "1080p", "720p"], "resolution ladder lost")
check(first.res_combo.currentText() == "1080p", first.res_combo.currentText())
check(unresolved._pending and not unresolved.start_btn.isEnabled(),
      "a card that never resolved came back looking ready")
check(LOOKED_UP == ["https://a.com/3"], "the unresolved link wasn't looked up again: %r" % LOOKED_UP)
print("restored: 2 cards, choice kept, unresolved one re-read")

first.payload["time_range"] = (10, 40)
vt2.save_state()
vt3 = make_tab()
check(vt3._queue_strips[0].payload["time_range"] == (10, 40), "clip range not restored as a tuple")

for c in list(vt3._queue_strips):
    vt3._on_strip_remove(c)
vt3.save_state()
vt4 = make_tab()
check(not vt4._queue_strips and not vt4.queue_card.isVisibleTo(vt4), "an emptied queue came back")

print("\nQUEUE PERSISTENCE OK")
