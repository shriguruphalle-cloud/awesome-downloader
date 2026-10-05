"""The 2.5 design system: the painted backdrop and its three modes, glass
that frosts what is behind it, the nav indicator, the update pill, and the
Settings panel applying its changes live."""
import time

import _support
from _support import build_window, check, pump, qapp, stub_network

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

app = qapp()
from ui_qt import cinema  # noqa: E402
from ui_qt.widgets import make_card  # noqa: E402


def settle(ms=400):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


win, tabs = build_window(tabs=("video", "download", "history"), size=(1100, 720))
vt = tabs["video"]
stub_network(vt)
surface = win.backdrop_surface

# ---- the backdrop is painted, opaque, and offers frost to glass ------------------
check(surface.mode() == cinema.CINEMATIC, "cinematic isn't the default backdrop")
frost = surface.frost()
check(frost is not None and not frost.isNull(), "no frost for the glass to sample")
img = win.grab().toImage()
corner = QColor(img.pixel(5, img.height() - 5))
check(corner.alpha() == 255, "the painted backdrop isn't opaque")
print("backdrop: cinematic, frost %dx%d" % (frost.width(), frost.height()))

# The website's rig: a navy field, lit sky blue from above the top centre.
top = QColor(frost.pixel(frost.width() // 2, 1))
corner = QColor(frost.pixel(frost.width() - 2, frost.height() - 2))
check(top.blue() > top.red() + 40, "the top light isn't blue: %s" % top.name())
check(top.lightness() > corner.lightness(), "the top isn't lit: %s vs %s" % (top.name(), corner.name()))
check(corner.blue() > corner.red(), "the field isn't navy: %s" % corner.name())

# The grid is on the backdrop itself (not under the glass, which blurs it
# away), strongest at the top: a row of the backdrop just under the title
# band has brighter hairlines at a regular 68px pitch.
backdrop = surface._sharp.toImage()
dpr = surface._sharp.devicePixelRatio()
band = win.titleBar.height()


def grid_peaks(y0, y1):
    # Averaged over a strip of rows, so the faint anti-banding dither cancels
    # out and only real lines stand out.
    rows = range(int(y0 * dpr), int(y1 * dpr))
    row = [sum(QColor(backdrop.pixel(x, y)).lightness() for y in rows) / len(rows)
           for x in range(backdrop.width())]
    return [x for x in range(1, len(row) - 1) if row[x] >= row[x - 1] + 3 and row[x] >= row[x + 1] + 3]


peaks = grid_peaks(band + cinema.BAND_SHADOW + 2, band + cinema.BAND_SHADOW + 34)
gaps = [b - a for a, b in zip(peaks, peaks[1:])]
print("grid lines along the top at x=%s" % peaks[:6])
check(len(peaks) >= 8 and all(abs(g - 68 * dpr) <= 1.5 for g in gaps),
      "no regular 68px grid on the backdrop: %s" % gaps[:8])

# ---- the title bar is a band of dark glass, not the same slab as the page ------
# Glass: the grid doesn't run through it. Dark: it's deeper than the backdrop
# just under it. Separated: a seam along its lower edge.
check(len(grid_peaks(4, band - 4)) <= 1, "the grid runs through the title band")
x_mid = backdrop.width() // 2 + int(34 * dpr)   # between grid lines (one runs through the centre)
light = lambda y: QColor(backdrop.pixel(x_mid, int(y * dpr))).lightness()
check(light(band / 2) < light(band + cinema.BAND_SHADOW + 6) - 4,
      "the title band isn't darker than the page under it: %d vs %d"
      % (light(band / 2), light(band + cinema.BAND_SHADOW + 6)))
check(light(band) < light(band - 2) and light(band) < light(band + cinema.BAND_SHADOW + 6),
      "no seam between the title band and the page")
print("title band: dark glass, %dpx, with a seam" % band)

# ---- glass frosts whatever is behind it --------------------------------------------
# The same card placed under the top light and in the dark lower corner must
# not come out the same colour -- if it did, it would be a grey box, not glass.
card, _lay = make_card()
card.setParent(surface)
card.resize(120, 80)


def card_centre_colour(x, y):
    card.move(x, y)
    card.show()
    pump(2)
    shot = card.grab().toImage()
    return QColor(shot.pixel(60, 40))


over_light = card_centre_colour(surface.width() // 2 - 60, 60)
over_dark = card_centre_colour(surface.width() - 180, surface.height() - 100)
print("glass under the top light %s, in the dark corner %s" % (over_light.name(), over_dark.name()))
check(over_light != over_dark, "the glass doesn't show what's behind it")
check(over_light.blue() > over_dark.blue(), "the light didn't come through the glass")
check(over_light.blue() > over_light.red() + 30, "the glass isn't blue glass: %s" % over_light.name())
card.deleteLater()

# ---- the three backdrop modes --------------------------------------------------------
win.set_backdrop_mode(cinema.SOLID)
pump(2)
check(surface.frost() is None, "solid mode still frosts")
solid = win.grab().toImage()
a, b = QColor(solid.pixel(3, solid.height() - 3)), QColor(solid.pixel(solid.width() - 3, solid.height() - 3))
check(a == b, "solid mode isn't flat: %s vs %s" % (a.name(), b.name()))
win.set_backdrop_mode(cinema.DESKTOP)
pump(2)
# Acrylic, floating: the same palette as maximized, the desktop only faintly
# through it -- not clear glass that turns opaque when maximized (reported as
# a transparency issue on every minimize and maximize).
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QImage, QRegion  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
# Rendered on its own into a transparent image: grab() composites onto an
# opaque window background and can't show translucency.
see = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
see.fill(0)
surface.render(see, QPoint(), QRegion(), QWidget.RenderFlag(0))
check(surface._see_through(), "a floating window isn't see-through in the acrylic mode")
band_alpha = QColor.fromRgba(see.pixel(see.width() // 2, 4)).alpha()
page_alpha = QColor.fromRgba(see.pixel(6, see.height() // 2)).alpha()
check(band_alpha == 255, "the title band is see-through in the acrylic mode: %d" % band_alpha)
check(180 <= page_alpha < 255, "the acrylic page isn't mostly the painted backdrop: alpha %d" % page_alpha)
check(surface.frost() is not None, "glass has nothing to frost in the acrylic mode")
win.set_backdrop_mode(cinema.CINEMATIC)
pump(2)
check(surface.frost() is not None, "frost didn't come back")
print("backdrop modes: cinematic / solid / desktop switch live")

# ---- the theme re-lights the backdrop -------------------------------------------------
before = QColor(surface.frost().pixel(surface.frost().width() // 2, surface.frost().height() // 2))
win.toggle_theme()
pump(2)
after = QColor(surface.frost().pixel(surface.frost().width() // 2, surface.frost().height() // 2))
check(after.lightness() > before.lightness() + 100, "light mode didn't re-light the backdrop")
win.toggle_theme()
pump(2)
check(win.dark_mode, "theme didn't toggle back")

# ---- the nav indicator lands on the selected tab ------------------------------------
island = win._island
win.tabs.setCurrentIndex(2)
settle(500)
check(island._sliding is None, "the indicator is still sliding")
check(island._current == 2, "indicator is on tab %d" % island._current)
island.reduce_motion = True
win.tabs.setCurrentIndex(0)
pump(1)
check(island._sliding is None and island._current == 0, "reduce motion still animated")
print("nav indicator follows the tab, and reduce motion jumps")

# ---- the update pill appears for a newer release and opens nothing by itself -------
check(not win.update_pill.isVisible(), "update pill shown with no release")
win._on_update_found({"version": "9.9.0", "page": "https://example.invalid/r"})
pump(2)
check(win.update_pill.isVisible() and win.update_pill.text() == "Update available"
      and "9.9.0" in win.update_pill.toolTip(), "update pill didn't appear")
tb = win.titleBar
close_left = tb._buttons[0].mapTo(win, QPoint(0, 0)).x()
right = win.update_pill.mapTo(win, QPoint(win.update_pill.width(), 0)).x()
check(right <= close_left, "the update pill runs under the caption chips")
win.resize(780, 700)
pump(3)
tray_right = win._action_tray.mapTo(win, QPoint(win._action_tray.width(), 0)).x()
check(tray_right <= tb._buttons[0].mapTo(win, QPoint(0, 0)).x(), "tray overlaps the chips at 780px")
win.resize(1100, 720)
pump(3)
print("update pill shows, and the row still fits")

# ---- Settings applies live ---------------------------------------------------------------
changed = []
vt.settings_changed = lambda: changed.append(1)


def drive_settings():
    for w in app.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            w.concurrent.setCurrentIndex(w.concurrent.findData(5))
            w.format.setCurrentIndex(w.format.findData("mkv"))
            w.motion.setChecked(True)
            w.accept()
            return
    raise AssertionError("the Settings panel didn't open")


QTimer.singleShot(300, drive_settings)
win.open_panel("settings")
pump(2)
check(win.settings["max_concurrent"] == 5, "concurrency wasn't saved")
check(win.settings["default_format"] == "mkv", "format wasn't saved")
check(win.settings["reduce_motion"] is True and win._island.reduce_motion, "reduce motion wasn't applied")
check(changed, "the Video tab wasn't told about the change")
from app.utils import settings as settings_store  # noqa: E402
check(settings_store.load_settings()["max_concurrent"] == 5, "the change didn't reach settings.json")
print("Settings: saved to disk and applied without a restart")

# ---- paint cost stays small ---------------------------------------------------------------
t0 = time.perf_counter()
for _ in range(10):
    win.repaint()
per_frame = (time.perf_counter() - t0) / 10 * 1000
print("full-window repaint: %.1f ms" % per_frame)
check(per_frame < 60, "a full repaint takes %.0f ms" % per_frame)

print("\nDESIGN OK")
