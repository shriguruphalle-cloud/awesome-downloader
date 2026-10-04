"""Buy me a coffee: the coffee button sits between the action tray and the
caption dots, and its panel offers what the website's donate card does --
PayPal (the link opens in the browser) and UPI (the website's QR).

No browser is opened: the panel's openUrl is swapped for a recorder."""
import _support
from _support import build_window, check, qapp, settle

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

qapp()
from app import config
from ui_qt.dialogs import donate_panel

opened = []
donate_panel.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()) or True)

win, tabs = build_window(tabs=("video", "download"))
settle(400)

# ---- where it sits ---------------------------------------------------------------------
btn = win.donate_btn
left = lambda w: w.mapTo(win, QPoint(0, 0)).x()  # noqa: E731
right = lambda w: left(w) + w.width()  # noqa: E731
dots = win.titleBar._cluster
check(btn.isVisible(), "no coffee button in the title bar")
check(right(win._action_tray) <= left(win._donate_capsule) and right(win._donate_capsule) <= left(dots),
      "the coffee button isn't between the tray (%d) and the caption dots (%d): %d..%d"
      % (right(win._action_tray), left(dots), left(win._donate_capsule), right(win._donate_capsule)))
mid = lambda w: w.mapTo(win, QPoint(0, w.height() // 2)).y()  # noqa: E731
check(abs(mid(win._donate_capsule) - mid(win._action_tray)) <= 1, "the coffee disc is off the tray's centre line")
check(not btn.icon().isNull(), "the coffee button has no icon")
print("coffee button between the tray and the caption dots")

# ---- the panel: PayPal or UPI -------------------------------------------------------------
QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
settle(400)
panel = next((w for w in qapp().topLevelWidgets()
              if isinstance(w, donate_panel.DonatePanel) and w.isVisible()), None)
check(panel is not None, "the coffee button opened nothing")
check(panel.pages.currentIndex() == 0, "the panel didn't open on the choice")
check(panel.geometry().top() >= btn.mapToGlobal(QPoint(0, btn.height())).y() - panel.MARGIN,
      "the panel isn't under the button")

QTest.mouseClick(panel.upi, Qt.MouseButton.LeftButton)
settle(200)
check(panel.pages.currentIndex() == 1 and panel.qr.isVisible(), "UPI didn't show the QR")
check(panel.qr.renderer.isValid(), "the QR file didn't load: %s" % donate_panel.QR_PATH)
img = panel.qr.grab().toImage()
dark = sum(1 for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)
           if QColor(img.pixel(x, y)).lightness() < 60)
light = sum(1 for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)
            if QColor(img.pixel(x, y)).lightness() > 230)
check(dark > 200 and light > 200, "the QR didn't draw (dark %d, light %d)" % (dark, light))
check(panel.height() > 300, "the panel didn't grow to fit the QR (%d px)" % panel.height())
print("UPI: the website's QR, %dpx" % panel.qr.width())

QTest.mouseClick(panel.back, Qt.MouseButton.LeftButton)
settle(200)
check(panel.pages.currentIndex() == 0, "Back didn't return to the choice")

QTest.mouseClick(panel.paypal, Qt.MouseButton.LeftButton)
settle(300)
check(opened == [config.DONATE_PAYPAL], "PayPal opened %s" % opened)
import shiboken6  # noqa: E402
check(not shiboken6.isValid(panel) or not panel.isVisible(), "the panel stayed open after PayPal")
print("PayPal: opens %s" % config.DONATE_PAYPAL)

win.close()
print("\nDONATE OK")
