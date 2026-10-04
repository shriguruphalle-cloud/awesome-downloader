"""Settings > Simultaneous downloads is actually enforced: extra jobs wait in
line, a finished one lets the next start, pausing frees a slot, and nothing
-- retry, resume-at-startup, raising the limit -- can bypass the queue."""
import _support
from _support import check, pump, qapp, settings, stub_network

qapp()
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

st = settings(max_concurrent=2)
dl = DownloadTab(settings=st)
vt = VideoTab(settings=st, download_tab=dl)
calls = stub_network(vt)


def payload(n):
    return {"url": "https://x.test/%d" % n, "mode": "video", "height": 720,
            "container": "mp4", "bitrate": "192", "time_range": None,
            "title": "Video %d" % n, "meta": "", "is_image": False,
            "thumbnail_url": None, "thumb_pixmap": None}


jobs = [vt._start_payload(payload(n)) for n in range(5)]
pump()
print("started 5 with a limit of 2:")
print("  running:", sorted(vt._running), " waiting:", [w[0] for w in vt._waiting])
check(len(vt._running) == 2, "limit not enforced: %r" % vt._running)
check([w[0] for w in vt._waiting] == jobs[2:], "waiting order wrong")
check(len(calls["downloads"]) == 2, "more threads started than the limit")

# Waiting cards say where they are in line, and hide Pause.
card3 = dl._cards[jobs[2]]
card5 = dl._cards[jobs[4]]
print("  card 3:", repr(card3.detail_label.text()))
print("  card 5:", repr(card5.detail_label.text()))
check("next up" in card3.detail_label.text(), card3.detail_label.text())
check("#3 in line" in card5.detail_label.text(), card5.detail_label.text())
check(not card3.pause_btn.isVisibleTo(card3), "a waiting card offers Pause")

# ---- a job finishing starts the next in line -------------------------------
vt._on_download_done(jobs[0], "", "video", None)
pump()
print("\nafter job 1 finished: running", sorted(vt._running), "waiting", [w[0] for w in vt._waiting])
check(jobs[2] in vt._running and len(vt._running) == 2, "next job didn't start")
check(len(calls["downloads"]) == 3, calls["downloads"])
check(dl._cards[jobs[3]].detail_label.text().endswith("next up"), "positions not refreshed")

# ---- a failure frees its slot too -------------------------------------------
vt._on_download_error(jobs[1], "HTTP Error 404: Not Found")
pump()
check(jobs[3] in vt._running, "a failed job kept its slot")
print("failure card reads:", repr(dl._cards[jobs[1]].detail_label.text()))
check(dl._cards[jobs[1]].detail_label.text().startswith("The link wasn't found (404)."),
      "failure text is not the friendly one")

# ---- pausing gives the slot away, resuming takes it back --------------------
vt._toggle_pause(jobs[2], True)
pump()
print("\npaused job 3: running", sorted(vt._running), "paused", sorted(vt._paused_jobs))
check(jobs[4] in vt._running, "pausing didn't let the last job start")
check(jobs[2] in vt._paused_jobs and jobs[2] not in vt._running, "pause bookkeeping")
vt._toggle_pause(jobs[2], False)
check(jobs[2] in vt._running, "resume didn't take the slot back")
print("resumed: running", sorted(vt._running), "(one over the limit is intended)")

# ---- cancelling a waiting job takes it out of line --------------------------
more = [vt._start_payload(payload(n)) for n in (10, 11)]
pump()
check([w[0] for w in vt._waiting] == more, [w[0] for w in vt._waiting])
vt._cancel_job(more[0])
pump()
check([w[0] for w in vt._waiting] == [more[1]], "cancelled job still waiting")
check(vt._jobs[more[0]]["finished"], "cancelled waiting job not marked finished")
print("\ncancelled a waiting job: it left the line, card reads",
      repr(dl._cards[more[0]].detail_label.text()))

# ---- retry goes through the limit too ---------------------------------------
before = len(calls["downloads"])
vt._retry_job(jobs[1])          # the one that failed earlier
pump()
check(len(calls["downloads"]) == before, "retry bypassed a full queue")
check(any(w[0] == jobs[1] for w in vt._waiting), "retry didn't join the line")
print("retry with no free slot: waits in line")

# ---- raising the limit starts waiting jobs straight away -------------------
st["max_concurrent"] = 6
vt.settings_changed()
pump()
print("\nlimit raised to 6: running", sorted(vt._running), "waiting", vt._waiting)
check(not vt._waiting, "raising the limit left jobs waiting")

print("\nSCHEDULER OK")
