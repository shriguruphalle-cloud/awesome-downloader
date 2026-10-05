"""Signs a release so the app will install it from inside itself.

The app only installs an update whose update.json is signed with the
developer's private key (app/utils/updater.py checks it against the public
key in app/config.py). This tool makes that key once and signs each release.

  1. Once, on your PC:
       .venv312\\Scripts\\python.exe tools\\release_sign.py keygen
     -> writes the private key to %USERPROFILE%\\.awesome-downloader\\release-signing.key
        and prints the public key to put in app/config.py (UPDATE_PUBLIC_KEY).
     BACK THE PRIVATE KEY UP somewhere safe and never commit or share it:
     whoever has it can publish updates the app will install; whoever loses
     it can't publish any (the app would then need a new public key, which
     people only get by installing a new version by hand).

  2. For every release, after building the installer:
       .venv312\\Scripts\\python.exe tools\\release_sign.py sign
     -> Output\\update.json      version, installer name, SHA-256, size, notes
        Output\\update.json.sig  its Ed25519 signature
     The version comes from app/config.py; the notes from RELEASE_v<version>.md.

  3. Publish: a GitHub release tagged v<version> with three assets --
       AwesomeVideoDownloaderSetup.exe, update.json, update.json.sig
     e.g.  gh release create v2.6.0 Output\\AwesomeVideoDownloaderSetup.exe
              Output\\update.json Output\\update.json.sig --notes-file RELEASE_v2.6.0.md
     The app picks it up at its next start (Settings > Updates > Check now
     for one already open).

A release without update.json is still announced in the app; it just opens
the release page instead of installing.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from app import config  # noqa: E402
from app.utils import ed25519  # noqa: E402

KEY_PATH = os.environ.get("AWD_SIGNING_KEY") or os.path.join(
    os.path.expanduser("~"), ".awesome-downloader", "release-signing.key")
INSTALLER = os.path.join(ROOT, "Output", "AwesomeVideoDownloaderSetup.exe")


def keygen(args):
    if os.path.exists(KEY_PATH) and not args.force:
        sys.exit("A signing key already exists at %s (use --force to replace it -- the app's "
                 "public key would have to change too)." % KEY_PATH)
    seed = os.urandom(32)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    with open(KEY_PATH, "w", encoding="ascii") as f:
        f.write(seed.hex() + "\n")
    print("Private key written to:", KEY_PATH)
    print("Back it up safely; never commit or share it.")
    print("Public key for app/config.py UPDATE_PUBLIC_KEY:")
    print(ed25519.public_key(seed).hex())


def _seed():
    try:
        with open(KEY_PATH, encoding="ascii") as f:
            seed = bytes.fromhex(f.read().strip())
    except FileNotFoundError:
        sys.exit("No signing key at %s -- run `release_sign.py keygen` first." % KEY_PATH)
    if len(seed) != 32:
        sys.exit("The signing key at %s isn't a 32-byte key." % KEY_PATH)
    return seed


def _notes(version):
    path = os.path.join(ROOT, "RELEASE_v%s.md" % version)
    if not os.path.exists(path):
        return ""
    text = open(path, encoding="utf-8").read()
    # The "What's new" part, as plain markdown, kept short for the update panel.
    m = re.search(r"## What's new[^\n]*\n(.*?)(\n## |\Z)", text, re.S)
    body = (m.group(1) if m else text).strip()
    return body[:6000]


def sign(args):
    seed = _seed()
    public = ed25519.public_key(seed)
    if public.hex() != config.UPDATE_PUBLIC_KEY:
        sys.exit("This signing key doesn't match UPDATE_PUBLIC_KEY in app/config.py -- the app "
                 "would refuse the update. Use the matching key, or update the config (and ship "
                 "that version by hand first).")
    installer = os.path.abspath(args.installer)
    if not os.path.exists(installer):
        sys.exit("No installer at %s -- build it first (build_exe.bat, then ISCC installer.iss)." % installer)
    h = hashlib.sha256()
    with open(installer, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    manifest = {
        "app": "Awesome Downloader",
        "version": args.version or config.APP_VERSION,
        "installer": os.path.basename(installer),
        "sha256": h.hexdigest(),
        "size": os.path.getsize(installer),
        "notes": args.notes if args.notes is not None else _notes(args.version or config.APP_VERSION),
    }
    data = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")
    signature = ed25519.sign(seed, data)
    assert ed25519.verify(public, data, signature)
    out = os.path.dirname(installer)
    with open(os.path.join(out, "update.json"), "wb") as f:
        f.write(data)
    with open(os.path.join(out, "update.json.sig"), "w", encoding="ascii") as f:
        f.write(base64.b64encode(signature).decode("ascii") + "\n")
    print("Signed %s %s" % (manifest["installer"], manifest["version"]))
    print("  sha256 %s, %d bytes" % (manifest["sha256"], manifest["size"]))
    print("  -> %s, %s" % (os.path.join(out, "update.json"), os.path.join(out, "update.json.sig")))
    print("Publish all three on the GitHub release v%s." % manifest["version"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="make the signing key (once)")
    k.add_argument("--force", action="store_true")
    s = sub.add_parser("sign", help="sign Output\\AwesomeVideoDownloaderSetup.exe")
    s.add_argument("--installer", default=INSTALLER)
    s.add_argument("--version", default=None, help="defaults to app/config.py APP_VERSION")
    s.add_argument("--notes", default=None, help="defaults to RELEASE_v<version>.md's What's new")
    args = ap.parse_args()
    {"keygen": keygen, "sign": sign}[args.cmd](args)


if __name__ == "__main__":
    main()
