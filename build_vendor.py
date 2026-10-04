"""Prepares the third-party pieces the build bundles next to the app.

    .venv312\\Scripts\\python.exe build_vendor.py

  vendor/adguard-dl/unpacked/  AdGuard AdBlocker, the official Chrome MV3
                               release, unzipped unmodified (GPL-3.0, see
                               NOTICE.md). Fetched once per checkout.
  vendor/webview2/             The two WebView2 SDK files the Browser tab
                               loads the engine with, copied out of the
                               pywebview package (a build dependency only).

Both are cached: a second run does nothing. Exits non-zero if either is
missing afterwards, so the build stops rather than shipping a Browser tab
that can't start.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")

ADGUARD_VERSION = "5.5.2.3"
ADGUARD_URL = ("https://github.com/AdguardTeam/AdguardBrowserExtension/releases/download/"
               "v%s/chrome-mv3.zip" % ADGUARD_VERSION)
ADGUARD_ZIP = os.path.join(VENDOR, "adguard-dl", "chrome-mv3.zip")
ADGUARD_DIR = os.path.join(VENDOR, "adguard-dl", "unpacked")

WEBVIEW2_DIR = os.path.join(VENDOR, "webview2")
WEBVIEW2_FILES = [
    "Microsoft.Web.WebView2.Core.dll",
    os.path.join("runtimes", "win-x64", "native", "WebView2Loader.dll"),
    os.path.join("runtimes", "win-arm64", "native", "WebView2Loader.dll"),
    os.path.join("runtimes", "win-x86", "native", "WebView2Loader.dll"),
]


def _manifest_version(folder):
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def adguard():
    if _manifest_version(ADGUARD_DIR) == ADGUARD_VERSION:
        print("AdGuard %s: already unpacked" % ADGUARD_VERSION)
        return True
    os.makedirs(os.path.dirname(ADGUARD_ZIP), exist_ok=True)
    if not os.path.exists(ADGUARD_ZIP):
        print("Fetching AdGuard %s from %s" % (ADGUARD_VERSION, ADGUARD_URL))
        tmp = ADGUARD_ZIP + ".part"
        with urllib.request.urlopen(ADGUARD_URL, timeout=60) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
        os.replace(tmp, ADGUARD_ZIP)
    with open(ADGUARD_ZIP, "rb") as f:
        print("chrome-mv3.zip sha256", hashlib.sha256(f.read()).hexdigest())
    shutil.rmtree(ADGUARD_DIR, ignore_errors=True)
    with zipfile.ZipFile(ADGUARD_ZIP) as z:
        z.extractall(ADGUARD_DIR)
    version = _manifest_version(ADGUARD_DIR)
    print("AdGuard unpacked, manifest version", version)
    return version == ADGUARD_VERSION


def webview2_sdk():
    spec = importlib.util.find_spec("webview")
    if spec is None or not spec.origin:
        print("pywebview isn't installed -- it's where the WebView2 SDK comes from")
        return all(os.path.exists(os.path.join(WEBVIEW2_DIR, f)) for f in WEBVIEW2_FILES)
    source = os.path.join(os.path.dirname(spec.origin), "lib")
    for rel in WEBVIEW2_FILES:
        src = os.path.join(source, rel)
        dst = os.path.join(WEBVIEW2_DIR, rel)
        if not os.path.exists(src):
            print("missing in pywebview:", rel)
            return False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copy2(src, dst)
    print("WebView2 SDK ready in", WEBVIEW2_DIR)
    return True


if __name__ == "__main__":
    ok = True
    try:
        ok = adguard() and ok
    except Exception as e:  # noqa: BLE001
        print("AdGuard couldn't be prepared:", e)
        ok = False
    ok = webview2_sdk() and ok
    sys.exit(0 if ok else 1)
