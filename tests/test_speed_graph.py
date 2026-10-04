"""Each torrent's speed, drawn in its card behind everything on it.

The first speed graph stretched whatever it had to full height (a few bytes
a second drew the same mountains as megabytes) and was reported as noise; a
boxed graph beside the numbers was reported as ugly. This one is the card's
background: faint, on a scale that only takes round values and has a floor,
nothing at all when nothing moves, and the shared clock asked for frames
only while something does. No network: the speeds are made up.
"""
import time
import types

import _support  # noqa: F401
from _support import check, qapp, settle

app = qapp()
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from ui_qt import theme  # noqa: E402
from ui_qt.torrent_tab import TorrentTab  # noqa: E402
from ui_qt.widgets.speed_graph import GraphCard, nice_top, smooth_path  # noqa: E402
from ui_qt.widgets.stats_strip import StatsStrip  # noqa: E402

KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3

# ---- the scale: round values only, never below 100 KB/s --------------------------
for value, want in ((0, 100 * KB), (50 * KB, 100 * KB), (300 * KB, 400 * KB), (900 * KB, 1 * MB),
                    (3.4 * MB, 4 * MB), (4.5 * MB, 5 * MB), (6 * MB, 8 * MB), (11 * MB, 20 * MB),
                    (1.5 * GB, 2 * GB)):
    check(nice_top(value) == want, "a peak of %d B/s topped the scale at %d, not %d" % (
        value, nice_top(value), want))

# ---- a curve never bulges past its points ------------------------------------------
path = smooth_path([QPointF(i * 10, y) for i, y in enumerate([50, 50, 10, 50, 50, 48, 50])])
ys = [path.pointAtPercent(k / 400).y() for k in range(401)]
check(min(ys) >= 10 - 0.01 and max(ys) <= 50 + 0.01, "the curve overshoots its samples")

host = QWidget()
host.resize(900, 150)
card = GraphCard(host)
card.setGeometry(0, 0, 900, 150)
host.show()
settle(150)
brand = QColor(theme.tokens(True)["brand"])


def feed(down, up=0.0, seconds=70):
    card.trace.samples.clear()
    now = time.monotonic()
    for i in range(seconds):
        card.trace.push(down, up, now=now - seconds + i + 1)
    card.trace.snap_scale()
    card.update()


def render():
    img = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0))
    card.render(img)
    return img


def line_y(img, x):
    """Where the download line crosses column x: the bluest pixel there."""
    area = card.graph_area()
    best, at = 0.0, None
    for y in range(int(area.top()) - 4, int(area.bottom())):
        c = QColor.fromRgba(img.pixel(x, y))
        score = c.blue() - (c.red() + c.green()) / 2
        if score > best:
            best, at = score, y
    return at, best


area = card.graph_area()
x = int(area.left() + area.width() * 0.5)

# ---- nothing moving: nothing drawn, no frames asked for ------------------------------
feed(0)
check(card.trace.is_idle() and not card.trace.wants_frames(), "an idle card still wants frames")
blank = render()
feed(3 * MB, 200 * KB)
drawn = render()
changed = sum(1 for xx in range(int(area.left()), int(area.right()), 9)
              for yy in range(int(area.top()), int(area.bottom()), 5)
              if blank.pixel(xx, yy) != drawn.pixel(xx, yy))
check(changed > 60, "a moving torrent's card draws no graph (%d pixels changed)" % changed)
check(card.trace.wants_frames(), "a moving card doesn't ask to be animated")

# ---- 3 MB/s on a 4 MB/s scale sits three quarters up --------------------------------
check(card.trace.target_top() == 4 * MB, "3 MB/s got a scale of %d" % card.trace.target_top())
y, strength = line_y(drawn, x)
want = area.bottom() - 0.75 * area.height()
check(y is not None and abs(y - want) <= 4, "3 MB/s drawn at y=%s, want ~%.0f" % (y, want))
print("3 MB/s: the line crosses at y=%d (want %.0f) in a card %dpx tall" % (y, want, card.height()))

# ---- a trickle stays a trickle ----------------------------------------------------
feed(1 * KB)
check(card.trace.target_top() == 100 * KB, "a trickle stretched the scale to %d" % card.trace.target_top())
y, _ = line_y(render(), x)
check(y is not None and area.bottom() - y <= 5, "1 KB/s drew %s px tall" % (None if y is None else area.bottom() - y))

# ---- faint: whatever is on the card stays readable ----------------------------------
feed(3 * MB, 200 * KB)
img = render()
peak_alpha_like = max(QColor.fromRgba(img.pixel(x, yy)).blue() for yy in range(int(area.top()), int(area.bottom())))
check(peak_alpha_like < 235, "the graph is drawn at full strength (blue %d)" % peak_alpha_like)

# ---- upload: a spike a second, rising as it comes in ---------------------------------
from ui_qt.widgets.speed_graph import LAG_S, SPIKE_GROW_S, upload_spikes  # noqa: E402
feed(0, 2 * MB, seconds=10)
area = card.graph_area()
now = card.trace.samples[-1][0] + LAG_S + 0.1          # the newest spike has just come in
y_of = lambda v: area.bottom() - min(v / card.trace.top, 1.04) * area.height()  # noqa: E731
path = upload_spikes(area, card.trace, now, y_of)
polys = path.toSubpathPolygons()
check(len(polys) == 10, "10 seconds of upload drew %d spikes" % len(polys))
newest = max(polys, key=lambda p: p.boundingRect().x()).boundingRect().height()
grown = min(polys, key=lambda p: p.boundingRect().x()).boundingRect().height()
check(newest < grown * 0.8, "a spike just in is already full height (%.1f vs %.1f)" % (newest, grown))
later = upload_spikes(area, card.trace, now + SPIKE_GROW_S, y_of)
newest_later = max(later.toSubpathPolygons(), key=lambda p: p.boundingRect().x()).boundingRect().height()
check(abs(newest_later - grown) < 1.5, "a spike didn't finish rising (%.1f vs %.1f)" % (newest_later, grown))
feed(0, 0, seconds=10)
check(upload_spikes(area, card.trace, now, y_of).isEmpty(), "no upload still drew spikes")
print("upload: %d spikes, the newest rising (%.0f of %.0f px)" % (len(polys), newest, grown))

# ---- the tab feeds each row's card ------------------------------------------------
owner = types.SimpleNamespace(_estimate_eta=lambda status: "--", _files_finish_time=lambda h, p: None)
row = {"stats": StatsStrip(), "graph": GraphCard(), "active_s": 0.0, "took_s": None, "finished_at": None,
       "uploaded_base": 0, "downloaded_base": 0, "last_poll": time.monotonic() - 1}
status = types.SimpleNamespace(paused=False, has_metadata=True, total_wanted=GB, total_wanted_done=GB // 2,
                               total_payload_download=GB // 2, total_upload=0, download_rate=2 * MB,
                               upload_rate=50 * KB, num_peers=4, num_seeds=2)
TorrentTab._update_stats(owner, row, status, 0.5, False, False)
check(row["graph"].trace.samples and row["graph"].trace.samples[-1][1] == 2 * MB, "the row's card wasn't fed")
status.paused = True
TorrentTab._update_stats(owner, row, status, 0.5, False, False)
check(row["graph"].trace.samples[-1][1] == 0, "a paused torrent's card still shows it moving")
print("\nSPEED GRAPH OK")
