"""The merged top strip: every control reachable by a real cursor, the bare
strip a working drag handle (which is what Windows watches for snapping),
nothing cropped or run under the caption dots at any width, and the dots on
the row's centre line."""
import _support
from _support import build_window, check, pump, qapp

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

qapp()
from ui_qt import main_window as mw

moves = []
mw.startSystemMove = lambda window, pos: moves.append(pos)

win, tabs = build_window()
tb = win.titleBar
row = win.topbar
LABELS = [win.tabs.tabText(i) for i in range(win.tabs.count())]

# ---- one row, hosted in the title bar ---------------------------------------
check(tb.hBoxLayout.itemAt(0).widget() is row, "the nav row isn't hosted in the title bar")

# ---- every control reachable by a real cursor ----------------------------------
controls = list(win._tab_buttons) + [b for b in win.action_buttons() if b.isVisible()]
for btn in controls:
    g = btn.mapToGlobal(QPoint(btn.width() // 2, btn.height() // 2))
    hit = QApplication.widgetAt(g)
    reaches = hit is btn or (hit is not None and btn.isAncestorOf(hit))
    check(reaches, "a real click would miss %r (lands on %s)" % (
        btn.text() or btn.accessibleName(), type(hit).__name__ if hit else None))
print("all %d top-bar controls reachable by hit-testing" % len(controls))


def send(kind, pt, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton):
    g = QPointF(row.mapToGlobal(pt))
    app = QApplication.instance()
    app.sendEvent(row, QMouseEvent(kind, QPointF(pt), g, button, buttons,
                                   Qt.KeyboardModifier.NoModifier))


def press_drag(pt):
    moves.clear()
    send(QEvent.Type.MouseButtonPress, pt)
    send(QEvent.Type.MouseMove, QPoint(pt.x() + 12, pt.y()), button=Qt.MouseButton.NoButton)
    pump()
    return len(moves) == 1


# ---- the bare strip drags ---------------------------------------------------------
y = row.height() // 2
spans, run = [], None
for x in range(row.width()):
    if row.childAt(QPoint(x, y)) is None:
        run = x if run is None else run
    elif run is not None:
        spans.append((run, x - 1))
        run = None
if run is not None:
    spans.append((run, row.width() - 1))
spans = [s for s in spans if s[1] - s[0] >= 12]
bare = sum(b - a for a, b in spans)
print("draggable: %dpx of %dpx (%.0f%%)" % (bare, row.width(), 100.0 * bare / row.width()))
check(bare > row.width() * 0.2, "almost none of the strip drags the window")
for a, b in spans:
    check(press_drag(QPoint((a + b) // 2, y)), "bare span %s didn't start a drag" % ((a, b),))

# ...but a press on a control never does.
pill = win._tab_buttons[1]
pt = row.mapFromGlobal(pill.mapToGlobal(QPoint(pill.width() // 2, pill.height() // 2)))
check(not press_drag(pt), "dragging from a tab moved the window")

# ...and a double-click on the bare strip maximises.
was = win.isMaximized()
a, b = spans[0]
send(QEvent.Type.MouseButtonDblClick, QPoint((a + b) // 2, y))
pump(3)
check(win.isMaximized() != was, "double-click on the strip didn't maximise")
win.showNormal()
pump(3)

# ---- caption dots: on the nav row's centre line, inset like the pages -----------
cluster = tb._cluster
mid = lambda w_: w_.mapTo(win, QPoint(0, w_.height() // 2)).y()
check(abs(mid(cluster) - mid(win._island)) <= 1,
      "caption dots sit at y=%d, the nav row's centre is y=%d" % (mid(cluster), mid(win._island)))
for b in tb._buttons:
    check(abs(mid(b) - mid(win._island)) <= 1, "a caption dot is off the row's centre line")
inset = win.width() - cluster.mapTo(win, QPoint(cluster.width(), 0)).x()
check(inset == win._PADDED_MARGINS[2], "caption capsule is %dpx from the right edge" % inset)
# Hovering one shows all three glyphs; leaving puts them back.
import win32con
win._update_nc_hover(win32con.HTCLOSE)
check(win._nc_hover_btn is tb._buttons[2], "hovering close didn't light it")
win._clear_nc_hover()
check(win._nc_hover_btn is None, "leaving the dots left one lit")
print("caption dots centred on the row, %dpx from the edge" % inset)

# ---- nothing crops or runs under the chips, at any usable width ------------------
print("\nwidth  cropped / overlap")
for w in (1400, 1200, 1006, 980, 940, 900, 840, 800, 760):
    win.resize(w, 700)
    pump(3)
    cropped = [btn.text() for btn in win._tab_buttons
               if btn.fontMetrics().horizontalAdvance(btn.text()) > btn.width() - 16]
    close_left = tb._buttons[0].mapTo(win, QPoint(0, 0)).x()
    rightmost = max(w_.mapTo(win, QPoint(w_.width(), 0)).x()
                    for w_ in controls if w_.isVisible())
    print("  %4d  %s / %s" % (w, cropped or "none", "OVERLAP" if rightmost > close_left else "ok"))
    check(not cropped, "labels cropped at %dpx: %s" % (w, cropped))
    check(rightmost <= close_left, "controls run under the caption chips at %dpx" % w)

# ---- snapping prerequisites ------------------------------------------------------
import win32con
import win32gui
style = win32gui.GetWindowLong(int(win.winId()), win32con.GWL_STYLE)
for name in ("WS_CAPTION", "WS_THICKFRAME", "WS_MAXIMIZEBOX", "WS_MINIMIZEBOX"):
    bit = getattr(win32con, name)
    check(style & bit == bit, "%s missing -- the window can't snap" % name)
check(win32con.HTMAXBUTTON in [c for c, _ in win._titlebar_nc_buttons()],
      "no HTMAXBUTTON region -- no Snap Layouts flyout")
avail = QApplication.primaryScreen().availableGeometry()
check(win.minimumWidth() <= avail.width() // 2,
      "minimum width exceeds half the screen, so snapping to a half is refused")
print("snap prerequisites present")

print("\nTOP BAR OK")
