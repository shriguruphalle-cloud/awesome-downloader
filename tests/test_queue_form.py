"""The form and the queue as two separate paths that meet cleanly.

Clipboard Fetch fills the form and must not also stack a card; pasting one
link into the field does exactly the same (it used to stack a card instead,
and the two ways in behaving differently was reported as confusing); pasting
several stacks them. Queue banks the form
as a card; clicking a card loads it back; downloading from the form removes
the card so it can't run twice; and every one of those carries its retry and
resume data."""
import _support
from _support import check, no_modal_dialogs, pump, qapp, settings, stub_network

app = qapp()
from ui_qt.download_tab import DownloadTab
from ui_qt.video_tab import VideoTab

shown = no_modal_dialogs()
st = settings()
dl = DownloadTab(settings=st)
vt = VideoTab(settings=st, download_tab=dl)
calls = stub_network(vt)


def fill_form(url, title="Title", heights=None, duration=300):
    """What a completed clipboard Fetch leaves behind."""
    vt._last_fetch_url = url
    vt._suppress_url_change = True
    try:
        vt.url_entry.setText(url)
    finally:
        vt._suppress_url_change = False
    vt._on_fetch_done(title, "Chan", duration, heights or {1080: 50_000_000, 720: 20_000_000},
                      None, "https://t/x.jpg", False)


# ---- clipboard Fetch: form only ---------------------------------------------------
app.clipboard().setText("https://a.com/form")
vt.on_fetch()
pump()
check(calls["fetches"] == ["https://a.com/form"], calls["fetches"])
check(not vt._queue_strips, "clipboard Fetch also stacked a card")
check(vt.url_entry.text() == "https://a.com/form", "form field lost its link")

# ---- pasting one link is Fetch --------------------------------------------------
vt.url_entry.clear()
calls["fetches"].clear()
vt.url_entry.setText("https://a.com/pasted")
check(not vt._queue_strips and calls["fetches"] == ["https://a.com/pasted"],
      "a pasted link didn't open in the form the way Fetch does")
check(vt.url_entry.text() == "https://a.com/pasted", "the pasted link left the field")
# ...and typing one isn't fetched letter by letter.
vt.url_entry.clear()
calls["fetches"].clear()
for ch in "https://a.com/typed":
    vt.url_entry.setText(vt.url_entry.text() + ch)
check(not calls["fetches"] and not vt._queue_strips, "a link being typed was fetched early")
vt.url_entry.clear()
print("paste = fetch, typing waits for Enter: ok")

# ---- Queue banks the form as a card and clears it ------------------------------
fill_form("https://a.com/q1", "Queued One")
check(vt.info_card.isVisibleTo(vt) and vt.queue_btn.isEnabled(), "form didn't fill")
vt.on_queue()
check(len(vt._queue_strips) == 1, "Queue didn't bank the form")
check(not vt.info_card.isVisibleTo(vt), "form didn't clear after Queue")
q1 = vt._queue_strips[0]
check(q1.payload["height"] == 1080 and "MP4" in q1._full_meta, q1._full_meta)

# ---- clicking a card loads it back into the form -------------------------------
calls["fetches"].clear()
vt._on_card_selected(q1)
check(vt.url_entry.text() == "https://a.com/q1", "card link not put back in the form")
# Instant: what the card already read fills the form, no second lookup (it
# used to fetch the link again, which took seconds -- reported as slow).
check(not calls["fetches"], "selecting a card read the link from the site again")
check(vt.info_card.isVisibleTo(vt) and vt.info_title_label.text() == "Queued One", "the form didn't fill")
check(vt.res_height_map.get(vt.res_combo.currentText()) == 1080, "the card's resolution wasn't carried over")
check(q1.property("selected") == "true", "selected card not marked")

# ---- a clip range set there is honoured, and the card leaves the queue ---------
fill_form("https://a.com/q1", "Queued One")
vt.range_check.setChecked(True)
vt.start_entry.setText("0:10")
vt.end_entry.setText("0:40")
vt.on_download()
pump()
check(len(calls["downloads"]) == 1, calls["downloads"])
check(calls["downloads"][0][7] == (10, 40), "clip range lost: %r" % (calls["downloads"][0][7],))
check(not vt._queue_strips, "the card stayed queued after downloading from the form")

job = list(vt._jobs.values())[-1]
check(job["relaunch"] is not None and job["resume_info"] is not None,
      "a form download can't be retried or resumed")
print("form download: clip range kept, card removed, retry/resume data present")

# ---- a still-reading card refuses to load into the form -----------------------
vt.queue_links(["https://a.com/pending"])
p = vt._queue_strips[-1]
calls["fetches"].clear()
vt._on_card_selected(p)
check(not calls["fetches"], "a pending card was fetched into the form")

# ---- the Browser's download button stacks without any dialog -------------------
shown.clear()
vt.queue_url("https://youtube.com/watch?v=b1")
check(any(c.payload["url"].endswith("b1") for c in vt._queue_strips), "queue_url didn't stack")
check(not shown, "queue_url put up a dialog: %r" % shown)

# ---- Save Image path for image posts goes through the same starter ------------
vt._last_fetch_url = "https://a.com/img"
vt._on_fetch_done("An Image", "", 0, {}, None, "https://t/i.jpg", True)
vt._on_image_download()
pump()
check(calls["images"], "image download didn't start")

print("\nQUEUE FORM OK")
