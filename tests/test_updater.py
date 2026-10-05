"""In-app updates, end to end, offline: GitHub is replaced by an in-memory
stand-in, nothing is installed or launched. Covers the signature (RFC 8032
vectors, tampering), the network guard (HTTPS, GitHub hosts only), the
download checks (size, checksum, partial files), the installer checks, the
remembered state (rollback, "don't remind me"), the release tool, and the
UI: the update panel, the title bar pill, the Browser tab's bar."""
import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types

import _support
from _support import build_window, check, qapp, settle

app = qapp()
from app import config  # noqa: E402
from app.utils import ed25519, updater  # noqa: E402

# ---- 1. Ed25519 ---------------------------------------------------------------------------------
sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
check(ed25519.public_key(sk).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
      "RFC 8032 test 1: public key")
check(ed25519.sign(sk, b"").hex() == "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590"
      "a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b", "RFC 8032 test 1: signature")
seed = hashlib.sha256(b"test key").digest()
pub = ed25519.public_key(seed)
sig = ed25519.sign(seed, b"hello")
check(ed25519.verify(pub, b"hello", sig), "a good signature didn't verify")
check(not ed25519.verify(pub, b"hellp", sig), "a changed message verified")
check(not ed25519.verify(pub, b"hello", sig[:-1] + bytes([sig[-1] ^ 1])), "a changed signature verified")
check(not ed25519.verify(ed25519.public_key(b"x" * 32), b"hello", sig), "another key's signature verified")
check(not ed25519.verify(b"short", b"hello", sig), "a malformed key didn't fail cleanly")
print("ed25519: RFC vector, tampering and wrong keys rejected")

# ---- 2. the network guard -------------------------------------------------------------------------
for bad in ("http://github.com/x", "https://evil.example/x", "ftp://github.com/x"):
    try:
        updater._open(bad)
        check(False, "opened %s" % bad)
    except updater.UpdateError:
        pass
guard = updater._GuardedRedirects(updater.TRUSTED_HOSTS)
try:
    guard.redirect_request(None, None, 302, "Found", {}, "https://evil.example/setup.exe")
    check(False, "followed a redirect off GitHub")
except updater.UpdateError:
    pass
print("network: HTTPS to GitHub's hosts only, redirects included")

# ---- an in-memory GitHub --------------------------------------------------------------------------
FILES = {}


class Resp(io.BytesIO):
    def __init__(self, url, data):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_open(url, hosts=updater.TRUSTED_HOSTS, timeout=0, accept=None):
    if not updater._host_ok(url, hosts):
        raise updater.UpdateError("refused")
    if url not in FILES:
        raise updater.UpdateError("GitHub answered 404")
    return Resp(url, FILES[url])


updater._open = fake_open
config.UPDATE_PUBLIC_KEY = pub.hex()
BASE = "https://github.com/x/y/releases/download/v9.9.0/"
installer = b"MZ" + os.urandom(2 * 1024 * 1024)


def publish(manifest_over=None, sign_with=seed, version="9.9.0", installer_bytes=installer):
    m = {"app": "Awesome Downloader", "version": version, "installer": "AwesomeVideoDownloaderSetup.exe",
         "sha256": hashlib.sha256(installer_bytes).hexdigest(), "size": len(installer_bytes), "notes": "## New\n- things"}
    m.update(manifest_over or {})
    data = json.dumps(m).encode()
    FILES.clear()
    FILES[BASE + "update.json"] = data
    FILES[BASE + "update.json.sig"] = base64.b64encode(ed25519.sign(sign_with, data))
    FILES[BASE + "AwesomeVideoDownloaderSetup.exe"] = installer_bytes
    return {"version": version, "page": "https://github.com/x/y/releases/tag/v" + version, "notes": "",
            "assets": {"update.json": BASE + "update.json", "update.json.sig": BASE + "update.json.sig",
                       "AwesomeVideoDownloaderSetup.exe": BASE + "AwesomeVideoDownloaderSetup.exe"}}


def refused(fn, why):
    try:
        fn()
        check(False, why)
    except updater.UpdateError as e:
        return str(e)


# ---- 3. the signed manifest -------------------------------------------------------------------------
rel = publish()
upd = updater.describe(rel)
check(upd["version"] == "9.9.0" and upd["size"] == len(installer), "a good release wasn't accepted: %s" % upd)
refused(lambda: updater.describe(publish(sign_with=hashlib.sha256(b"someone else").digest())),
        "a release signed by someone else was accepted")
r2 = publish()
FILES[BASE + "update.json"] = FILES[BASE + "update.json"].replace(b'"size"', b'"size" ')   # altered after signing
refused(lambda: updater.describe(r2), "an altered manifest was accepted")
refused(lambda: updater.describe(publish({"version": "9.9.1"})), "a manifest for another version was accepted")
refused(lambda: updater.describe(publish({"installer": "evil.exe"})), "an unexpected installer name was accepted")
refused(lambda: updater.describe(publish({"size": 10})), "an implausible size was accepted")
r3 = publish()
del r3["assets"]["update.json.sig"]
msg = refused(lambda: updater.describe(r3), "an unsigned release was accepted")
check("release page" in msg, "an unsigned release should point to the release page: %s" % msg)
print("manifest: signed by the developer, matching the release -- everything else refused")

# ---- 4. download --------------------------------------------------------------------------------------
updater.UPDATES_DIR = tempfile.mkdtemp(prefix="awd-updates-")
seen = []
path = updater.download(updater.describe(publish()), progress=lambda d, t: seen.append(d))
check(os.path.exists(path) and open(path, "rb").read() == installer, "the download is wrong")
check(seen and seen[-1] == len(installer), "progress didn't reach the end")
check(not os.path.exists(path + ".part"), "a .part file was left behind")
check(updater.download(updater.describe(publish())) == path, "a verified copy already here wasn't reused")
shutil.rmtree(updater.UPDATES_DIR)
u = updater.describe(publish())
FILES[BASE + "AwesomeVideoDownloaderSetup.exe"] = b"MZ" + os.urandom(len(installer) - 2)     # swapped on GitHub
msg = refused(lambda: updater.download(u), "a swapped installer was accepted")
check("checksum" in msg and not os.path.exists(updater.target_path(u)) and not os.path.exists(updater.target_path(u) + ".part"),
      "a bad download left a file behind: %s" % msg)
u = updater.describe(publish())
FILES[BASE + "AwesomeVideoDownloaderSetup.exe"] = installer + b"extra"
refused(lambda: updater.download(u), "an installer larger than signed was accepted")
u = updater.describe(publish(installer_bytes=b"PK" + installer[2:]))
refused(lambda: updater.download(u), "a file that isn't a Windows program was accepted")
u = updater.describe(publish())
refused(lambda: updater.download(u, cancelled=lambda: True), "a cancelled download finished")
print("download: checksum, size and type enforced; nothing half-done left behind; cancel works")

# ---- 5. installer checks --------------------------------------------------------------------------------
exe_copy = os.path.join(tempfile.mkdtemp(), "AwesomeVideoDownloaderSetup.exe")
shutil.copy(sys.executable, exe_copy)
ver = updater.file_version(exe_copy)
check(ver and ver.startswith("3.12"), "couldn't read a Windows program's version: %s" % ver)
good = {"version": ver, "sha256": updater._sha256(exe_copy)}
res = updater.check_installer(exe_copy, good, scan=lambda p: ("clean", "no threats found"))
check(all(ok for _, ok, _ in res) and os.path.exists(exe_copy), "a good installer failed: %s" % res)
res = updater.check_installer(exe_copy, dict(good, version="9.9.9"), scan=lambda p: ("clean", "no threats found"))
check(not res[1][1] and not os.path.exists(exe_copy), "a wrong-version installer passed, or wasn't deleted")
shutil.copy(sys.executable, exe_copy)
res = updater.check_installer(exe_copy, good, scan=lambda p: ("threat", "Defender found a threat"))
check(not res[2][1] and not os.path.exists(exe_copy), "a flagged installer passed, or wasn't deleted")
print("installer checks: version from the file itself; a Defender threat stops it; Defender at",
      updater.defender_exe() or "(not on this PC)")

# ---- 6. installing, rollback, reminders -------------------------------------------------------------
updater.STATE_PATH = os.path.join(tempfile.mkdtemp(), "update_state.json")
launched = []
check(updater.install("C:/x/setup.exe", "2.5.0", "2.6.0", launch=lambda p, a: launched.append((p, a)) or True),
      "install didn't start")
check(launched and "/SILENT" in launched[0][1] and "/RELAUNCH=1" in launched[0][1], "installer arguments: %s" % launched)
check(updater.rollback_version("2.6.0") == "2.5.0", "no rollback offered after updating")
check(updater.rollback_version("2.5.0") is None, "rollback offered before the update took")
updater.install("C:/x/setup.exe", "2.6.0", "2.5.0", rollback=True, launch=lambda p, a: True)
check(updater.rollback_version("2.5.0") is None, "after a rollback it offered to 'roll back' forward")
check(updater.is_dismissed("2.6.0"), "the version rolled back from still nags")
check(not updater.is_dismissed("2.7.0"), "an unrelated version is dismissed")
updater.dismiss("2.7.0")
check(updater.is_dismissed("2.7.0"), "don't remind me didn't stick")
print("install: silent with relaunch; rollback remembered; don't-remind persists")

iss = open(os.path.join(config.BASE_DIR, "installer.iss"), encoding="utf-8-sig").read()
check("Check: ShouldRelaunch" in iss and "{param:RELAUNCH|0}" in iss, "the installer doesn't relaunch after an update")

# ---- 7. the release tool -------------------------------------------------------------------------------
sys.path.insert(0, os.path.join(config.BASE_DIR, "tools"))
key_file = os.path.join(tempfile.mkdtemp(), "k.key")
with open(key_file, "w") as f:
    f.write(seed.hex())
import release_sign  # noqa: E402

release_sign.KEY_PATH = key_file
out_dir = tempfile.mkdtemp()
setup = os.path.join(out_dir, "AwesomeVideoDownloaderSetup.exe")
with open(setup, "wb") as f:
    f.write(installer)
release_sign.sign(types.SimpleNamespace(installer=setup, version="9.9.0", notes="Notes"))
data = open(os.path.join(out_dir, "update.json"), "rb").read()
m = updater.verify_manifest(data, open(os.path.join(out_dir, "update.json.sig")).read())
check(m["sha256"] == hashlib.sha256(installer).hexdigest() and m["size"] == len(installer) and m["version"] == "9.9.0",
      "the release tool's manifest is wrong: %s" % m)
print("release tool: its signed manifest is accepted by the app")

# ---- 8. the update panel ---------------------------------------------------------------------------------
from ui_qt.dialogs import app_update_dialog as aud  # noqa: E402

quits = []
aud.QApplication = types.SimpleNamespace(instance=lambda: types.SimpleNamespace(quit=lambda: quits.append(1)))
FAKE = {"version": "9.9.0", "installer": "AwesomeVideoDownloaderSetup.exe", "url": BASE, "sha256": "0" * 64,
        "size": 50 * 1048576, "notes": "## What's new\n- a thing", "page": "https://github.com/x"}
installs = []
aud.updater = types.SimpleNamespace(
    UpdateError=updater.UpdateError,
    release=lambda v=None: {"version": "9.9.0"},
    describe=lambda rel: dict(FAKE),
    download=lambda u, progress=None, cancelled=None: (progress(u["size"] // 2, u["size"]), progress(u["size"], u["size"]),
                                                       "C:/x/AwesomeVideoDownloaderSetup.exe")[-1],
    check_installer=lambda p, u: [("c", True, "ok"), ("v", True, "9.9.0"), ("s", True, "no threats found")],
    install=lambda p, f, t, rollback=False: installs.append((p, f, t, rollback)) or True,
)


def wait(cond, s=5):
    end = time.time() + s
    while time.time() < end and not cond():
        settle(50)
    return cond()


dlg = aud.AppUpdateDialog(None)
dlg.show()
check(wait(lambda: dlg.go_btn.isEnabled()), "the panel never got the update")
check("9.9.0" in dlg.subtitle.text() and dlg.notes.isVisible(), "the panel doesn't describe the update")
check(dlg._marks["signed"].text() == "✓", "signature step not ticked")
dlg.go_btn.click()
check(wait(lambda: dlg.go_btn.text() == "Restart and install"), "the panel never got ready to install")
check(all(dlg._marks[k].text() == "✓" for k in aud.STEPS), "not every step is ticked: %s" %
      {k: dlg._marks[k].text() for k in aud.STEPS})
dlg.go_btn.click()
check(installs == [("C:/x/AwesomeVideoDownloaderSetup.exe", config.APP_VERSION, "9.9.0", False)] and quits,
      "Restart and install didn't install and quit: %s %s" % (installs, quits))
print("panel: describes, downloads, ticks every check, installs and quits")

aud.updater.describe = lambda rel: (_ for _ in ()).throw(updater.UpdateError("This release can't be installed from inside the app (it isn't signed)"))
dlg = aud.AppUpdateDialog(None)
dlg.show()
check(wait(lambda: dlg.page_btn.isVisible()), "a refused update didn't offer the release page")
check(not dlg.go_btn.isVisible() and "isn't signed" in dlg.message.text(), "the refusal isn't explained")
dlg.reject()
dlg = aud.AppUpdateDialog(None, rollback_to="2.4.0")
check("2.4.0" in dlg.windowTitle() or "2.4.0" in dlg.findChildren(type(dlg.subtitle))[0].text(), "rollback panel not about 2.4.0")
dlg.reject()
print("panel: an unsigned release is refused with the release page offered; rollback mode")

# ---- 9. the title bar pill and the Browser tab's bar -------------------------------------------------------
updater.STATE_PATH = os.path.join(tempfile.mkdtemp(), "update_state.json")
win, tabs = build_window(tabs=("video", "browser", "download"), size=(1300, 760))
settle(1500)
win._on_update_found({"version": "9.9.0", "page": "https://github.com/x"})
settle(800)
pill = win.update_pill
check(pill.isVisible() and pill.text() == "Update available", "no 'Update available' in the title bar")
from PySide6.QtCore import QPoint  # noqa: E402
gx = lambda w: w.mapTo(win, QPoint(0, 0)).x()  # noqa: E731
mid = lambda w: w.mapTo(win, QPoint(0, w.height() // 2)).y()  # noqa: E731
check(gx(win._action_tray) + win._action_tray.width() <= gx(pill) and gx(pill) + pill.width() <= gx(win._donate_capsule),
      "the pill isn't between the tray and the coffee cup")
check(pill.height() == win._donate_capsule.height() and abs(mid(pill) - mid(win._donate_capsule)) <= 1,
      "the pill isn't the capsules' height, on their centre line")
print("title bar: 'Update available' between the tray and the cup, same height, centred")

bt = tabs["browser"]
win.tabs.setCurrentWidget(bt)
settle(600)
bar = bt.update_bar
check(bar.isVisible() and bar.height() == bar.H and "9.9.0" in bar.text.text(), "no update bar in the Browser tab")
opened = []
win.open_app_update = lambda rollback_to=None: opened.append(rollback_to)
bar.update_btn.click()
check(opened == [None], "Update now didn't open the update panel")
bar.never_btn.click()
settle(600)
check(not bar.isVisible() and updater.is_dismissed("9.9.0"), "Don't remind me didn't hide it for good")
bt._update_bar_closed = False
bt.show_update_notice({"version": "9.9.0"})
settle(500)
check(not bar.isVisible(), "a dismissed version came back")
bt.show_update_notice({"version": "9.9.1"})
settle(600)
check(bar.isVisible(), "a newer version didn't show")
bar.close_btn.click()
settle(600)
check(not bar.isVisible() and not updater.is_dismissed("9.9.1"), "close should hide it for this session only")
bt.show_update_notice({"version": "9.9.1"})
settle(300)
check(not bar.isVisible(), "closed, it came back in the same session")
print("browser bar: shows each start; Update now; Don't remind me sticks; close is for this session")
win.close()
print("\nUPDATER OK")
