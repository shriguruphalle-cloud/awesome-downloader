"""In-app updates: find a newer release on GitHub, prove it's genuine,
download it, check it, install it -- and roll back to the version before.

How a release is trusted (tools/release_sign.py makes the first two):

1. **Signed manifest.** The release carries update.json (version, installer
   name, SHA-256, size, notes) and update.json.sig, an Ed25519 signature of
   it by the developer's private key. The app checks it against the public
   key built into it (config.UPDATE_PUBLIC_KEY). A release whose manifest is
   missing, unsigned, signed by anyone else, or altered is never installed --
   the app only offers its release page. This holds even if the GitHub
   account or the download were tampered with: without the private key no
   one can produce a manifest the app accepts.
2. **The installer matches it.** Downloaded over HTTPS from GitHub only
   (redirects off GitHub's own hosts are refused), never more than the
   signed size, then its SHA-256 must equal the signed one. The file starts
   life as .part and is renamed only once it checks out.
3. **It is what it says.** The installer's own version resource must name
   the signed version.
4. **Clean.** If Microsoft Defender is there, it scans the file; a threat
   stops the update and deletes the file. (No Defender: said so, not hidden.)

Installing hands the file to the installer with /SILENT, which then closes
the app (it waits on the app's "running" mutex), updates it and starts it
again (/RELAUNCH=1, see installer.iss). Before that, the version being left
is remembered, so Settings can offer to go back to it: a rollback is the same
pipeline run on that older release.

Framework-agnostic: blocking calls, run by the UI on a background thread.
"""
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from ..core.update_checker import _version_tuple, is_outdated
from ..logging_setup import get_logger
from . import ed25519

logger = get_logger("updater")

API = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases"
MANIFEST = "update.json"
SIGNATURE = "update.json.sig"
INSTALLER_RE = re.compile(r"^AwesomeVideoDownloaderSetup(-[0-9A-Za-z.\-]+)?\.exe$")
TRUSTED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")
MAX_MANIFEST = 256 * 1024
MIN_INSTALLER = 1024 * 1024
MAX_INSTALLER = 1024 * 1024 * 1024
UPDATES_DIR = os.path.join(config.APPDATA_DIR, "updates")
STATE_PATH = os.path.join(config.APPDATA_DIR, "update_state.json")
_TIMEOUT = 20


class UpdateError(Exception):
    """Something the person can be told, in plain words."""


# ---- network: HTTPS, GitHub only ---------------------------------------------------------
def _host_ok(url, hosts):
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in hosts


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_ok(newurl, self.hosts):
            raise UpdateError("The download was redirected somewhere other than GitHub, so it was stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, hosts=TRUSTED_HOSTS, timeout=_TIMEOUT, accept=None):
    if not _host_ok(url, hosts):
        raise UpdateError("Refused to download from %s -- updates only come from GitHub." % url)
    opener = urllib.request.build_opener(_GuardedRedirects(hosts))
    headers = {"User-Agent": f"AwesomeDownloader/{config.APP_VERSION}"}
    if accept:
        headers["Accept"] = accept
    try:
        resp = opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        raise UpdateError("GitHub answered %s for %s." % (e.code, url.rsplit("/", 1)[-1])) from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("Couldn't reach GitHub -- check your connection and try again.") from e
    if not _host_ok(resp.geturl(), hosts):
        resp.close()
        raise UpdateError("The download ended up somewhere other than GitHub, so it was stopped.")
    return resp


def _read_small(url, limit, hosts):
    with _open(url, hosts) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("%s is larger than it should be." % url.rsplit("/", 1)[-1])
    return data


# ---- releases ---------------------------------------------------------------------------------
def _normalize(tag):
    tag = (tag or "").strip()
    return tag[1:] if tag[:1] in ("v", "V") else tag


def release(version=None, hosts=TRUSTED_HOSTS, api=API):
    """A release from GitHub: the latest, or the one tagged `version`.
    {"version", "page", "notes", "assets": {name: url}}."""
    url = api + ("/latest" if version is None else "/tags/v" + _normalize(version))
    try:
        payload = json.loads(_read_small(url, 4 * 1024 * 1024, hosts).decode("utf-8"))
    except ValueError as e:
        raise UpdateError("GitHub's answer about the release couldn't be read.") from e
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("No published release was found.")
    ver = _normalize(payload.get("tag_name") or payload.get("name"))
    if not _version_tuple(ver):
        raise UpdateError("The release has no version number.")
    assets = {}
    for a in payload.get("assets") or []:
        if a.get("name") and a.get("browser_download_url"):
            assets[a["name"]] = a["browser_download_url"]
    return {"version": ver, "page": payload.get("html_url") or config.RELEASES_PAGE,
            "notes": (payload.get("body") or "").strip(), "assets": assets}


def verify_manifest(data, signature_text, public_key_hex=None):
    """The manifest's fields if `signature_text` (base64) is a valid signature
    of `data` by the developer's key; UpdateError otherwise."""
    key = public_key_hex if public_key_hex is not None else config.UPDATE_PUBLIC_KEY
    if not key:
        raise UpdateError("This build has no update key, so it can't check updates are genuine.")
    try:
        signature = base64.b64decode((signature_text or "").strip(), validate=True)
    except ValueError as e:
        raise UpdateError("The update's signature is malformed.") from e
    if not ed25519.verify(bytes.fromhex(key), data, signature):
        raise UpdateError("The update's signature doesn't check out -- it wasn't published by the "
                          "developer, or it was altered. It won't be installed.")
    try:
        manifest = json.loads(data.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The signed update description couldn't be read.") from e
    if not isinstance(manifest, dict):
        raise UpdateError("The signed update description is malformed.")
    return manifest


def describe(rel, hosts=TRUSTED_HOSTS, public_key_hex=None):
    """The verified update for a release: {"version", "installer", "url",
    "sha256", "size", "notes", "page"}. UpdateError if it can't be installed
    from inside the app (unsigned, altered, inconsistent)."""
    assets = rel.get("assets") or {}
    if MANIFEST not in assets or SIGNATURE not in assets:
        raise UpdateError("This release can't be installed from inside the app (it isn't signed) -- "
                          "open the release page to download it.")
    data = _read_small(assets[MANIFEST], MAX_MANIFEST, hosts)
    sig = _read_small(assets[SIGNATURE], 4096, hosts).decode("ascii", "replace")
    m = verify_manifest(data, sig, public_key_hex)
    version = _normalize(str(m.get("version") or ""))
    if version != rel["version"]:
        raise UpdateError("The signed version (%s) doesn't match the release (%s)." % (version, rel["version"]))
    name = str(m.get("installer") or "")
    if not INSTALLER_RE.match(name) or name not in assets:
        raise UpdateError("The signed installer name doesn't match the release's files.")
    sha = str(m.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise UpdateError("The signed checksum is malformed.")
    size = m.get("size")
    if not isinstance(size, int) or not MIN_INSTALLER <= size <= MAX_INSTALLER:
        raise UpdateError("The signed installer size is implausible.")
    return {"version": version, "installer": name, "url": assets[name], "sha256": sha, "size": size,
            "notes": str(m.get("notes") or rel.get("notes") or ""), "page": rel.get("page") or config.RELEASES_PAGE}


# ---- download & checks --------------------------------------------------------------------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def target_path(update):
    return os.path.join(UPDATES_DIR, update["version"], update["installer"])


def download(update, progress=None, cancelled=None, hosts=TRUSTED_HOSTS):
    """Downloads the installer (or reuses a copy already here that checks
    out) and returns its path. `progress(done, total)`; `cancelled()` -> bool."""
    path = target_path(update)
    if os.path.exists(path) and os.path.getsize(path) == update["size"] and _sha256(path) == update["sha256"]:
        if progress:
            progress(update["size"], update["size"])
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    h = hashlib.sha256()
    done = 0
    try:
        with _open(update["url"], hosts, timeout=60) as resp, open(part, "wb") as out:
            length = resp.headers.get("Content-Length")
            if length and int(length) != update["size"]:
                raise UpdateError("The installer on GitHub isn't the size the signed description says.")
            while True:
                if cancelled and cancelled():
                    raise UpdateError("Cancelled.")
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                done += len(chunk)
                if done > update["size"]:
                    raise UpdateError("The installer is larger than the signed description says.")
                h.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, update["size"])
        if done != update["size"]:
            raise UpdateError("The download stopped early (%d of %d bytes)." % (done, update["size"]))
        if h.hexdigest() != update["sha256"]:
            raise UpdateError("The installer's checksum doesn't match the signed one -- it was damaged "
                              "or altered on the way. It was deleted.")
        with open(part, "rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateError("The download isn't a Windows program.")
        os.replace(part, path)
        return path
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def file_version(path):
    """The version a Windows executable says it is ("2.5.0"), or None."""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
        while len(parts) > 3 and parts[-1] == 0:
            parts.pop()
        return ".".join(str(p) for p in parts)
    except Exception:  # noqa: BLE001
        return None


def defender_exe():
    """Microsoft Defender's command-line scanner, the newest platform first."""
    root = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows Defender", "Platform")
    found = []
    try:
        for d in os.listdir(root):
            exe = os.path.join(root, d, "MpCmdRun.exe")
            if os.path.isfile(exe):
                found.append((_version_tuple(d) or (0,), exe))
    except OSError:
        pass
    if found:
        return max(found)[1]
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Windows Defender", "MpCmdRun.exe")
    return exe if os.path.isfile(exe) else None


def defender_scan(path, timeout=300):
    """("clean" | "threat" | "skipped", detail)."""
    exe = defender_exe()
    if not exe:
        return "skipped", "Microsoft Defender isn't available on this PC"
    try:
        r = subprocess.run([exe, "-Scan", "-ScanType", "3", "-File", path, "-DisableRemediation"],
                           capture_output=True, text=True, timeout=timeout, creationflags=config.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "skipped", "Defender couldn't scan it (%s)" % e.__class__.__name__
    if r.returncode == 0:
        return "clean", "no threats found"
    if r.returncode == 2:
        return "threat", "Defender found a threat"
    return "skipped", "Defender's scan didn't finish (code %d)" % r.returncode


def check_installer(path, update, scan=defender_scan):
    """Every check after the download, as [(label, ok, detail)]. Any ok=False
    means: don't install (the file has been deleted)."""
    results = []
    sha_ok = _sha256(path) == update["sha256"]
    results.append(("Checksum matches the signed one", sha_ok, update["sha256"][:16] + "..."))
    ver = file_version(path)
    ver_ok = ver is not None and _version_tuple(ver)[:3] == _version_tuple(update["version"])[:3]
    results.append(("The installer is version %s" % update["version"], ver_ok, ver or "no version information"))
    verdict, detail = scan(path)
    results.append(("Microsoft Defender scan", verdict != "threat", detail))
    if not all(ok for _, ok, _ in results):
        try:
            os.remove(path)
        except OSError:
            pass
    return results


# ---- installing ------------------------------------------------------------------------------------
INSTALL_ARGS = "/SILENT /SP- /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RELAUNCH=1"


# The first version that encrypts saved data (app/utils/secure_store.py).
# One before it reads only plain files, and finding encrypted ones it would
# show an empty history -- and then save over it.
ENCRYPTION_SINCE = "2.5.1"


def install(path, from_version, to_version, rollback=False, launch=None):
    """Starts the installer (it asks Windows for permission, closes this app,
    installs, starts the app again) after remembering where we came from.
    The caller quits the app right after. Returns whether it started."""
    _record(from_version, to_version, rollback)
    if rollback and is_outdated(to_version, ENCRYPTION_SINCE):
        # going back past encryption: leave the files as that version reads them
        try:
            from . import secure_store
            secure_store.ENCRYPT = False
            secure_store.rewrite_all(secure_store.covered_paths())
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't decrypt saved data before going back to %s", to_version)
    if launch is None:
        shell = ctypes.windll.shell32.ShellExecuteW
        rc = shell(None, "open", path, INSTALL_ARGS, os.path.dirname(path), 1)
        ok = rc > 32
    else:
        ok = bool(launch(path, INSTALL_ARGS))
    if not ok:
        logger.error("Couldn't start the installer %s", path)
    return ok


# ---- what's remembered ------------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


def _record(from_version, to_version, rollback):
    s = load_state()
    s.update({"previous_version": from_version, "installed_version": to_version,
              "kind": "rollback" if rollback else "update", "at": time.time()})
    if rollback:
        # Went back from to_version's successor: don't nag about it again.
        dismissed = set(s.get("dont_remind") or [])
        dismissed.add(from_version)
        s["dont_remind"] = sorted(dismissed)
    _save_state(s)


def rollback_version(current=config.APP_VERSION):
    """The version this one was updated from, if going back is possible."""
    s = load_state()
    prev = s.get("previous_version")
    if (s.get("kind") == "update" and s.get("installed_version") == current and prev
            and prev != current and _version_tuple(prev)):
        return prev
    return None


def is_dismissed(version):
    return version in (load_state().get("dont_remind") or [])


def dismiss(version):
    s = load_state()
    dismissed = set(s.get("dont_remind") or [])
    dismissed.add(version)
    s["dont_remind"] = sorted(dismissed)
    _save_state(s)


def cleanup(keep=None):
    """Removes downloaded installers, except `keep`'s version folder."""
    try:
        for name in os.listdir(UPDATES_DIR):
            if name != keep:
                shutil.rmtree(os.path.join(UPDATES_DIR, name), ignore_errors=True)
    except OSError:
        pass


def newer(rel, current=config.APP_VERSION):
    return bool(rel) and is_outdated(current, rel["version"])
