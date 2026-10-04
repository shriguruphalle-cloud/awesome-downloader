"""`Awesome Downloader.exe --self-test report.json`

Checks the pieces a build can get wrong without the app itself noticing,
writes what it found to report.json, and exits -- before the single-instance
check, so it runs even while the app is open, and with a throwaway browser
profile, so it touches nothing of the user's:

  * the WebView2 engine loads through .NET and starts a page;
  * that page can play H.264 (the reason the Browser tab moved to WebView2);
  * AdGuard is bundled where the app looks for it;
  * ffmpeg is where the downloader looks for it.
"""
import json
import os
import shutil
import sys
import tempfile
import time


def run(out_path):
    report = {"frozen": getattr(sys, "frozen", False)}
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QWidget

        app = QApplication.instance() or QApplication(sys.argv[:1])
        from app import config
        from ui_qt import browser_engine, webview2

        ok, info = webview2.load()
        report["webview2_loaded"] = ok
        report["webview2_version"] = info
        report["adguard_dir"] = browser_engine.adguard_dir()
        report["adguard_bundled"] = browser_engine.adguard_bundled()
        report["ffmpeg"] = os.path.exists(getattr(config, "FFMPEG_BUNDLED_PATH", "") or "")
        if ok:
            profile = tempfile.mkdtemp(prefix="awd-selftest-")
            engine = webview2.Engine(profile)
            host = QWidget()
            host.resize(640, 400)
            view = webview2.WebView2Widget(engine, parent=host)
            state = {}

            def probe():
                view.run_js("document.createElement('video').canPlayType("
                            "'video/mp4; codecs=\"avc1.42E01E, mp4a.40.2\"')",
                            lambda r: state.setdefault("h264", r))
            view.loadFinished.connect(lambda _ok: probe())
            view.created.connect(lambda: view.load("data:text/html,<title>self-test</title>ok"))
            engine.failed.connect(lambda msg: state.setdefault("error", msg))
            engine.start()
            t0 = time.time()
            while "h264" not in state and "error" not in state and time.time() - t0 < 30:
                app.processEvents()
                time.sleep(0.01)
            report["engine_error"] = state.get("error")
            report["h264"] = state.get("h264")
            view.close_page()
            QTimer.singleShot(0, lambda: None)
            app.processEvents()
            shutil.rmtree(profile, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 -- the report is the point
        report["exception"] = "%s: %s" % (e.__class__.__name__, e)
    report["ok"] = bool(report.get("webview2_loaded") and report.get("h264") == "probably"
                        and report.get("adguard_bundled"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return 0 if report["ok"] else 1
