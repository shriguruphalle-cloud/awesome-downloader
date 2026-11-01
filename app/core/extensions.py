"""Browser extensions: a .crx or a folder made ready for the WebView2 engine, which only loads unpacked folders.

Chrome installs a .crx itself; WebView2 has no such step. So the package is read here: its header gives the
developer's key (which fixes the extension's ID, so its settings and sign-ins survive an update), the zip after the
header is unpacked into the app's data folder, and that folder is what the engine loads."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import struct
import urllib.request
import zipfile

from app import config
from app.logging_setup import get_logger

logger = get_logger("extensions")

# Short on purpose, like the browser's profile: extension databases live deep inside and Windows' 260-character
# limit stops them opening.
ROOT = os.path.join(config.APPDATA_DIR, "ext")
MAX_BYTES = 150 * 1024 * 1024
STORE = "https://chromewebstore.google.com/"
_ID = r"[a-p]{32}"
# the Chromium version the Store is told about: new enough for any extension to be offered
_PRODVERSION = "131.0.0.0"
_UPDATE_URL = ("https://clients2.google.com/service/update2/crx?response=redirect&prodversion=%s"
               "&acceptformat=crx2,crx3&x=id%%3D%s%%26installsource%%3Dondemand%%26uc")


class ExtensionError(Exception):
    """Why an extension couldn't be added, in words for the person adding it."""


# ---- which extension ----
def store_id(url):
    """The extension a Chrome Web Store page is about, or None."""
    m = re.match(r"https://(?:chromewebstore\.google\.com|chrome\.google\.com/webstore)/detail/(?:[^/?#]+/)?(%s)"
                 % _ID, url or "")
    return m.group(1) if m else None


def _from_store(url):
    return bool(re.match(r"https://(chromewebstore\.google\.com|chrome\.google\.com/webstore)(/|$)", url or ""))


def _crx_host(uri):
    return bool(re.match(r"https://([a-z0-9.-]+\.google\.com|[a-z0-9.-]+\.googleusercontent\.com|"
                         r"[a-z0-9.-]+\.gvt1\.com|[a-z0-9.-]+\.googleapis\.com)/", uri or ""))


def store_download(page_url, uri, filename):
    """The extension a download is, if it's the Store's own install: a .crx, from Google, started on the Store's page.
    A .crx from anywhere else stays an ordinary file, as in Chrome: a site can't install things by starting a download."""
    if not _from_store(page_url) or not _crx_host(uri):
        return None
    if not (".crx" in (filename or "").lower() or "/crx" in (uri or "").lower()):
        return None
    m = re.match(r"(%s)" % _ID, os.path.basename(filename or "")) or re.search(r"id%%3D(%s)" % _ID, uri or "")
    return m.group(1) if m else store_id(page_url)


# ---- the package ----
def _proto(data):
    """A protobuf message's fields: (number, bytes) for its length-delimited ones; the rest are stepped over."""
    i, out = 0, []

    def varint():
        nonlocal i
        v, shift = 0, 0
        while True:
            b = data[i]
            i += 1
            v |= (b & 0x7f) << shift
            if not b & 0x80:
                return v
            shift += 7
    while i < len(data):
        tag = varint()
        kind = tag & 7
        if kind == 0:
            varint()
        elif kind == 1:
            i += 8
        elif kind == 2:
            n = varint()
            out.append((tag >> 3, data[i:i + n]))
            i += n
        elif kind == 5:
            i += 4
        else:
            raise ValueError("wire type %d" % kind)
    return out


def id_of_key(der):
    """The ID Chromium gives a key: the first half of its SHA-256, in the letters a to p."""
    return "".join("abcdefghijklmnop"[int(c, 16)] for c in hashlib.sha256(der).hexdigest()[:32])


def parse_crx(blob):
    """(the zip's bytes, the developer's public key or None, the extension's ID or None) of a .crx."""
    try:
        if blob[:2] == b"PK":
            return blob, None, None     # a plain zip
        if blob[:4] != b"Cr24":
            raise ExtensionError("That isn't an extension package.")
        version = struct.unpack("<I", blob[4:8])[0]
        if version == 2:
            klen, slen = struct.unpack("<II", blob[8:16])
            key = blob[16:16 + klen]
            return blob[16 + klen + slen:], key, id_of_key(key)
        if version != 3:
            raise ExtensionError("That extension package is a kind this browser doesn't know.")
        size = struct.unpack("<I", blob[8:12])[0]
        fields = _proto(blob[12:12 + size])
        crx_id = None
        for number, value in fields:
            if number == 10000:
                for n, v in _proto(value):
                    if n == 1 and len(v) == 16:
                        crx_id = "".join("abcdefghijklmnop"[b >> i & 15] for b in v for i in (4, 0))
        key = None
        for number, value in fields:
            if number in (2, 3):        # the proofs; the one whose key gives this ID is the developer's
                for n, v in _proto(value):
                    if n == 1 and id_of_key(v) == crx_id:
                        key = v
        return blob[12 + size:], key, crx_id
    except (struct.error, IndexError, ValueError) as e:
        raise ExtensionError("That extension package is damaged.") from e


def _message(read, manifest, text):
    """A manifest's "__MSG_name__", in English or the extension's own default language."""
    m = re.fullmatch(r"__MSG_(.+)__", text or "")
    if not m:
        return text or ""
    for lang in ("en", "en_US", "en_GB", manifest.get("default_locale")):
        try:
            words = json.loads(read("_locales/%s/messages.json" % lang).decode("utf-8-sig"))
        except Exception:   # noqa: BLE001 -- not in this language
            continue
        for k, v in words.items():
            if k.lower() == m.group(1).lower():
                return v.get("message") or ""
    return ""


def describe(manifest, read):
    """What's worth showing about an extension, from its manifest."""
    action = manifest.get("action") or manifest.get("browser_action") or manifest.get("page_action") or {}
    icons = manifest.get("icons") or {}
    if not icons and isinstance(action.get("default_icon"), dict):
        icons = action["default_icon"]
    best = None
    if icons:
        try:
            best = icons[max(icons, key=lambda k: int(k))]
        except (TypeError, ValueError):
            best = next(iter(icons.values()))
    options = manifest.get("options_page") or (manifest.get("options_ui") or {}).get("page") or ""
    return {"name": _message(read, manifest, manifest.get("name")) or "Extension",
            "description": _message(read, manifest, manifest.get("description")),
            "version": str(manifest.get("version") or ""),
            "popup": action.get("default_popup") or "", "options": options, "icon": best or ""}


def what_it_can_do(manifest):
    """Its permissions in plain words, to ask with before adding it."""
    perms = set(str(p) for p in (manifest.get("permissions") or []) + (manifest.get("host_permissions") or []))
    for cs in manifest.get("content_scripts") or []:
        perms.update(str(m) for m in cs.get("matches") or [])
    words = []
    hosts = [p for p in perms if "://" in p or p == "<all_urls>"]
    if any(p in ("<all_urls>", "*://*/*", "http://*/*", "https://*/*") for p in hosts):
        words.append("Read and change everything on all websites")
    elif hosts:
        sites = sorted({re.sub(r"^[^:]+://(\*\.)?|/.*$", "", p) for p in hosts})
        words.append("Read and change what's on " + ", ".join(sites[:4]) + (" and %d more" % (len(sites) - 4)
                                                                          if len(sites) > 4 else ""))
    for perm, say in (("cookies", "Read your cookies"), ("history", "Read your browsing history"),
                      ("tabs", "See your open tabs"), ("webRequest", "See what pages load"),
                      ("proxy", "Change how you connect to the internet"), ("downloads", "Manage your downloads"),
                      ("clipboardRead", "Read what you copy"), ("management", "Manage your other extensions"),
                      ("nativeMessaging", "Talk to programs on this PC")):
        if perm in perms:
            words.append(say)
    return words


def inspect(blob):
    """What a package holds, without unpacking it: {"id", "key", "zip", "manifest", ...describe()}."""
    data, key, crx_id = parse_crx(blob)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("manifest.json").decode("utf-8-sig"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ExtensionError("That extension package is damaged.") from e
    if key is not None and crx_id != id_of_key(key):
        raise ExtensionError("That extension package's signature details don't match it.")
    info = describe(manifest, z.read)
    info.update(id=crx_id, key=key, zip=data, manifest=manifest, can=what_it_can_do(manifest))
    return info


def fetch(ext_id):
    """The package of a Web Store extension, checked to be that extension."""
    if not re.fullmatch(_ID, ext_id or ""):
        raise ExtensionError("That isn't an extension's ID.")
    req = urllib.request.Request(_UPDATE_URL % (_PRODVERSION, ext_id), headers={"User-Agent": "Mozilla/5.0 Chrome/131"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read(MAX_BYTES + 1)
    except OSError as e:
        raise ExtensionError("The Chrome Web Store couldn't be reached.") from e
    if len(blob) > MAX_BYTES:
        raise ExtensionError("That extension is too large.")
    if not blob:
        raise ExtensionError("The Chrome Web Store has no package of it for this browser.")
    info = inspect(blob)
    if info["id"] != ext_id:
        raise ExtensionError("The Chrome Web Store sent a different extension than the one asked for.")
    return info


# ---- on disk ----
def _meta_path():
    return os.path.join(ROOT, "extensions.json")


def known():
    """What's kept about each added extension, by the folder it's in."""
    try:
        with open(_meta_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _remember(folder, info):
    meta = known()
    meta[os.path.basename(folder)] = {k: info.get(k, "") for k in ("name", "version", "popup", "options", "icon", "id",
                                                                   "description", "source")}
    os.makedirs(ROOT, exist_ok=True)
    with open(_meta_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _replace(folder, tmp):
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(tmp, folder)


def unpack(info, source="store"):
    """A package made ready to load; returns the folder. Its key goes into the manifest so the engine gives it the
    ID the Store knows it by. "_metadata" comes out: Chromium refuses a folder with it, as anything starting with _
    but _locales is reserved."""
    name = info.get("id") or hashlib.sha256(info["zip"]).hexdigest()[:32]
    folder = os.path.join(ROOT, name)
    tmp = folder + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    base = os.path.realpath(tmp)
    try:
        with zipfile.ZipFile(io.BytesIO(info["zip"])) as z:
            for item in z.infolist():
                target = os.path.realpath(os.path.join(tmp, item.filename))
                if not target.startswith(base + os.sep):     # nothing lands outside its folder
                    raise ExtensionError("That extension package is damaged.")
                if item.filename.replace("\\", "/").split("/")[0] == "_metadata":
                    continue
                z.extract(item, tmp)
        manifest = dict(info["manifest"])
        if info.get("key"):
            manifest["key"] = base64.b64encode(info["key"]).decode("ascii")
        manifest.pop("update_url", None)
        with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        _replace(folder, tmp)
    except (OSError, zipfile.BadZipFile) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExtensionError("The extension couldn't be unpacked: %s" % e) from e
    _remember(folder, dict(info, source=source))
    return folder


def copy_folder(path):
    """An unpacked extension from a folder of the person's own, copied in (the engine writes into what it loads)."""
    try:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8-sig") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        raise ExtensionError("That folder has no extension in it: it needs a manifest.json.") from e

    def read(rel):
        with open(os.path.join(path, rel), "rb") as f:
            return f.read()
    info = describe(manifest, read)
    info.update(id=None, source="folder", manifest=manifest, can=what_it_can_do(manifest))
    folder = os.path.join(ROOT, "dev-" + hashlib.sha256(os.path.abspath(path).lower().encode()).hexdigest()[:16])
    tmp = folder + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(path, tmp, ignore=shutil.ignore_patterns("_metadata", ".git"))
        _replace(folder, tmp)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExtensionError("The folder couldn't be copied: %s" % e) from e
    _remember(folder, info)
    return folder


def meta_for(ext_id, name):
    """What's kept about an extension the engine has loaded: its ID where it's known, else its name."""
    for folder, m in known().items():
        if m.get("id") == ext_id or folder == ext_id:
            return dict(m, folder=os.path.join(ROOT, folder))
    for folder, m in known().items():
        if m.get("name") == name:
            return dict(m, folder=os.path.join(ROOT, folder))
    return {}


def forget(ext_id, name):
    """Its files and notes, once it's removed. (A file still held by the engine goes next time.)"""
    m = meta_for(ext_id, name)
    if not m:
        return
    shutil.rmtree(m["folder"], ignore_errors=True)
    meta = known()
    meta.pop(os.path.basename(m["folder"]), None)
    try:
        with open(_meta_path(), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except OSError:
        logger.debug("Couldn't save the extensions' notes", exc_info=True)
