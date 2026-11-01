"""The Browser tab's extensions: added from the Chrome Web Store (or a .crx or a folder), listed under the toolbar's
button with a switch each, and used: WebView2 has no toolbar of its own, so an extension's popup opens from here."""
import os
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from app.core import extensions as core
from app.logging_setup import get_logger

from . import webview2
from .browser_chrome import ChromeButton, GlassPopup, Toggle, _quiet_button, style_menu
from .widgets.toast import show_toast

logger = get_logger("browser_extensions")


class ExtensionWindow(QWidget):
    """An extension's popup or page, in a small window of its own."""
    POP_SIZE = (420, 600)

    def __init__(self, engine, url, title, open_tab, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(*self.POP_SIZE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = webview2.WebView2Widget(engine, parent=self)
        lay.addWidget(self.view)
        self.url = url
        self.view.created.connect(lambda: self.view.load(self.url))
        self.view.windowCloseRequested.connect(self.close)

        def new_window(args):
            args.Handled = True
            open_tab(str(args.Uri))
        self.view.newWindowRequested.connect(new_window)

    def closeEvent(self, event):
        try:
            self.view.close_page()
        except Exception:   # noqa: BLE001 -- it's going anyway
            logger.debug("Closing an extension's window", exc_info=True)
        super().closeEvent(event)


class ExtensionsPanel(GlassPopup):
    """The toolbar's button: what's added, a switch for each; and where to add more."""

    def __init__(self, manager, items, page_id, t, dark, parent):
        super().__init__(parent, 340, t, dark)
        self.manager = manager
        self.body.addWidget(self.label("Extensions", px=14, weight=QFont.Weight.Bold))
        if not items:
            self.body.addWidget(self.label("None added yet. Find one in the Chrome Web Store and press its button, "
                                           "or the one here while you're on its page.", role="muted", px=12, wrap=True))
        self.rows = []
        for e in items:
            self.body.addWidget(self._row(e))
        self.body.addWidget(self.divider())
        if page_id:
            add = _quiet_button("Add the extension on this page", t)
            add.clicked.connect(lambda: (self.close(), manager.install_from_store(page_id)))
            self.body.addWidget(add)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        store = _quiet_button("Chrome Web Store", t)
        store.clicked.connect(lambda: (self.close(), manager.tab._create_tab(url=core.STORE, activate=True)))
        foot.addWidget(store, 1)
        more = _quiet_button("From a file…", t)
        more.setToolTip("A .crx package, or a folder with an extension in it")
        more.clicked.connect(lambda: (self.close(), manager.add_from_file()))
        foot.addWidget(more)
        self.body.addLayout(foot)

    def _row(self, e):
        t = self._t
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(28, 28)
        icon.setStyleSheet("background: transparent;")
        path = os.path.join(e["meta"].get("folder", ""), e["meta"].get("icon", "")) if e["meta"].get("icon") else ""
        pm = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        if not pm.isNull():
            icon.setPixmap(pm.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        else:
            ph = ChromeButton("puzzle", "", size=28, icon_size=18, parent=icon)
            ph.apply_theme(t)
            ph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(icon)
        name = QPushButton(e["name"])
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.setFlat(True)
        name.setToolTip(e["meta"].get("description") or "Open %s" % e["name"])
        name.setStyleSheet(f"QPushButton {{ color: {t['text']}; font-size: 13px; font-weight: 600; text-align: left;"
                           " background: transparent; border: none; padding: 4px 0; }")
        name.clicked.connect(lambda _c=False: (self.close(), self.manager.open(e)))
        lay.addWidget(name, 1)
        switch = Toggle()
        switch.apply_theme(t)
        switch.setChecked(e["enabled"])
        switch.setToolTip("On or off")
        switch.toggled.connect(lambda on: self.manager.set_enabled(e, on))
        lay.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        dots = ChromeButton("dots", "More", size=28, icon_size=15)
        dots.apply_theme(t)
        dots.clicked.connect(lambda _c=False: self.manager.row_menu(e, self))
        lay.addWidget(dots)
        self.rows.append((e, switch))
        return row


class ExtensionManager(QObject):
    """One per Browser tab."""
    _fetched = Signal(object, object)       # (the package's details or None, why not)

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.services = tab.services
        self.windows = []
        self._busy = set()
        self._offered = set()
        self._fetched.connect(self._on_fetched)

    def _say(self, text, kind="info", **kw):
        show_toast(self.tab.pages, text, kind=kind, **kw)

    # ---- adding ----
    def take_download(self, tab, args):
        """The Store's own install, as a download: it's ours to do. True when it was."""
        try:
            uri = str(args.DownloadOperation.Uri)
            name = os.path.basename(str(args.ResultFilePath or ""))
        except Exception:   # noqa: BLE001
            return False
        ext_id = core.store_download(tab.url, uri, name)
        if not ext_id:
            return False
        args.Cancel = True
        self.install_from_store(ext_id, tab)
        return True

    def offer(self, tab):
        """On a Store page, a way to add it from here too."""
        ext_id = core.store_id(tab.url)
        if not ext_id or tab.private or ext_id in self._offered or tab is not self.tab._current():
            return
        self._offered.add(ext_id)
        self._say("Add this extension to this browser?", action="Add", on_action=lambda: self.install_from_store(ext_id),
                  ms=9000)

    def install_from_store(self, ext_id, tab=None):
        tab = tab or self.tab._current()
        if tab is not None and tab.private:
            self._say("Extensions don't run in private tabs. Open this page in a normal tab to add it.", "warning")
            return
        if ext_id in self._busy:
            return
        self._busy.add(ext_id)
        self._say("Getting the extension…")

        def work():
            try:
                self._fetched.emit(core.fetch(ext_id), "")
            except core.ExtensionError as e:
                self._fetched.emit({"id": ext_id, "failed": True}, str(e))
            except Exception as e:   # noqa: BLE001
                logger.exception("Couldn't get an extension")
                self._fetched.emit({"id": ext_id, "failed": True}, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_fetched(self, info, why):
        self._busy.discard(info.get("id"))
        if info.get("failed"):
            self._say("Couldn't add the extension: %s" % why, "warning", ms=8000)
            return
        if self.confirm(info):
            self.add(info, "store")

    def confirm(self, info):
        """As Chrome asks: what it is and what it can do, before it can do any of it."""
        box = QMessageBox(self.tab)
        box.setWindowTitle("Add extension")
        box.setText('Add "%s"?' % info["name"])
        can = info.get("can") or []
        box.setInformativeText(("It can:\n" + "\n".join("•  " + c for c in can)) if can
                               else "It asks for no special permissions.")
        add = box.addButton("Add extension", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is add

    def add(self, info, source, done=None):
        def load(*_a):
            try:
                folder = core.unpack(info, source)
            except core.ExtensionError as e:
                self._say("Couldn't add %s: %s" % (info["name"], e), "warning", ms=8000)
                return
            self._load(folder, info["name"], done)
        # the version it replaces goes first: its folder is about to change
        self.services.extensions_list(lambda items: self._drop_then(items, info, load))

    def _drop_then(self, items, info, then):
        old = [e for e in items if info.get("id") and e["id"] == info["id"]]
        if old:
            self.services.extension_remove(old[0]["ext"], then, then)
        else:
            then()

    def _load(self, folder, name, done=None):
        def ok(ext):
            self._say("%s added. It works on pages you open or reload from now." % ext.Name, "success", ms=6000)
            self._reload_page()
            if done:
                done(ext)

        def fail(msg):
            logger.warning("Couldn't load %s: %s", folder, msg)
            self._say("This browser couldn't load %s: %s" % (name, str(msg).splitlines()[0][:160]), "warning", ms=10000)
            if done:
                done(None)
        self.services.extension_add(folder, ok, fail)

    def add_from_file(self):
        menu = style_menu(QMenu(self.tab), self.tab._t)
        menu.addAction("A .crx package…", self._pick_crx)
        menu.addAction("A folder…", self._pick_folder)
        menu.exec(self.tab.extensions_btn.mapToGlobal(self.tab.extensions_btn.rect().bottomLeft()))

    def _pick_crx(self):
        path, _f = QFileDialog.getOpenFileName(self.tab, "Add an extension", "", "Extensions (*.crx *.zip)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                info = core.inspect(f.read())
        except (OSError, core.ExtensionError) as e:
            self._say("Couldn't add it: %s" % e, "warning", ms=8000)
            return
        if self.confirm(info):
            self.add(info, "file")

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self.tab, "Add an extension from a folder")
        if not path:
            return
        try:
            folder = core.copy_folder(path)
        except core.ExtensionError as e:
            self._say(str(e), "warning", ms=8000)
            return
        self._load(folder, os.path.basename(path))

    # ---- using ----
    def show_panel(self):
        if not self.services.extensions_ready():
            self._say("The browser is still starting.")
            return
        tab = self.tab._current()
        page_id = core.store_id(tab.url) if tab is not None and not tab.private else None

        def got(items):
            panel = ExtensionsPanel(self, items, page_id, self.tab._t, self.tab._dark, self.tab)
            panel.closed.connect(lambda: setattr(self.tab, "_panel", None))
            self.tab._panel = panel
            panel.open_under(self.tab.extensions_btn)
        self.services.extensions_list(got)

    def _page(self, e, rel):
        return "chrome-extension://%s/%s" % (e["id"], rel.lstrip("/"))

    def open(self, e):
        m = e["meta"]
        if not e["enabled"]:
            self._say("%s is off. Switch it on to use it." % e["name"])
        elif m.get("popup"):
            self.open_window(self._page(e, m["popup"]), e["name"])
        elif m.get("options"):
            self.tab._create_tab(url=self._page(e, m["options"]), activate=True)
        else:
            self._say("%s has no page of its own: it works by itself on the sites it's for." % e["name"])

    def open_window(self, url, title):
        win = ExtensionWindow(self.services.engine, url, title,
                              lambda u: self.tab._create_tab(url=u, activate=True), self.tab)
        btn = self.tab.extensions_btn
        at = btn.mapToGlobal(btn.rect().bottomRight())
        win.move(at.x() - win.width(), at.y() + 8)
        win.show()
        self.windows = [w for w in self.windows if webview2_alive(w)] + [win]
        return win

    def row_menu(self, e, panel):
        menu = style_menu(QMenu(panel), self.tab._t)
        m = e["meta"]
        if m.get("options"):
            menu.addAction("Options", lambda: (panel.close(), self.tab._create_tab(url=self._page(e, m["options"]),
                                                                                  activate=True)))
        if m.get("popup"):
            menu.addAction("Open in a tab", lambda: (panel.close(), self.tab._create_tab(url=self._page(e, m["popup"]),
                                                                                        activate=True)))
        if m.get("source", "store") == "store" and m.get("id"):
            menu.addAction("See it in the Web Store", lambda: (panel.close(), self.tab._create_tab(
                url=core.STORE + "detail/" + m["id"], activate=True)))
        menu.addSeparator()
        menu.addAction("Remove", lambda: (panel.close(), self.remove(e)))
        menu.exec(self.tab.cursor().pos())

    def set_enabled(self, e, on):
        self.services.extension_enable(e["ext"], on, lambda *_a: self._reload_page(),
                                       lambda msg: self._say("Couldn't switch %s: %s" % (e["name"], msg), "warning"))

    def remove(self, e, done=None):
        def ok(*_a):
            core.forget(e["id"], e["name"])
            self._say("%s removed." % e["name"], "success")
            self._reload_page()
            if done:
                done()
        self.services.extension_remove(e["ext"], ok, lambda msg: self._say("Couldn't remove it: %s" % msg, "warning"))

    def _reload_page(self):
        cur = self.tab._current()
        if cur is not None and not cur.on_home and cur.view is not None and not core.store_id(cur.url):
            QTimer.singleShot(150, cur.view.reload)


def webview2_alive(w):
    try:
        return w.isVisible()
    except RuntimeError:
        return False
