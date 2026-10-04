"""The startup update check reads GitHub's release JSON correctly and only
reports versions that are really newer -- without touching the network."""
import io
import json

import _support
from _support import check

from app import config
from app.utils import app_update

RELEASE = {
    "tag_name": "v9.1.0",
    "html_url": "https://github.com/x/y/releases/tag/v9.1.0",
    "draft": False, "prerelease": False,
    "body": "Notes here.",
    "assets": [
        {"name": "AwesomeVideoDownloaderSetup.exe.sha256", "browser_download_url": "https://x/sha"},
        {"name": "AwesomeVideoDownloaderSetup.exe", "browser_download_url": "https://x/setup.exe"},
    ],
}

r = app_update.parse_release(RELEASE)
print("parsed:", r)
check(r["version"] == "9.1.0", r)                       # leading v stripped
check(r["installer"] == "https://x/setup.exe", r)       # not the .sha256 sibling
check(r["page"].endswith("v9.1.0"), r)

check(app_update.parse_release(dict(RELEASE, prerelease=True)) is None, "a prerelease was offered")
check(app_update.parse_release(dict(RELEASE, draft=True)) is None, "a draft was offered")
check(app_update.parse_release(dict(RELEASE, tag_name="nightly")) is None, "an unversioned tag was offered")
check(app_update.parse_release("not json") is None, "garbage accepted")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(payload):
    app_update.urllib.request.urlopen = lambda req, timeout=None: _Resp(json.dumps(payload).encode())


serve(RELEASE)
check(app_update.newer_release("2.5.0")["version"] == "9.1.0", "a newer release wasn't reported")
serve(dict(RELEASE, tag_name="v2.5.0"))
check(app_update.newer_release("2.5.0") is None, "the same version was reported as an update")
serve(dict(RELEASE, tag_name="v2.4.9"))
check(app_update.newer_release("2.5.0") is None, "an older version was reported as an update")


def offline(req, timeout=None):
    raise OSError("no network")


app_update.urllib.request.urlopen = offline
check(app_update.newer_release("2.5.0") is None, "an offline check didn't fail quietly")
check(config.GITHUB_REPO in app_update.API_URL, app_update.API_URL)

print("\nAPP UPDATE OK")
