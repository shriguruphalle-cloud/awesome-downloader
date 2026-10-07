"""Known phishing, malware and scam sites, from open blocklists, so the
Browser tab can stop a page before it loads.

The lists (public domain, both):
  URLhaus by abuse.ch -- sites spreading malware right now (CC0)
  The Block List Project -- phishing and scam domains (Unlicense)

They're downloaded in the background at most once a day and kept in the
app's data folder, so checking a link never waits on the network and never
sends the link anywhere: the check is a lookup in a list on this PC.
Lists only catch what they know, so this is a seatbelt, not a guarantee --
which the warning page says.
"""
import os
import threading
import time
import urllib.parse
import urllib.request

from .. import config
from ..logging_setup import get_logger

logger = get_logger("safe_browsing")

LISTS = [
    ("malware", "URLhaus (abuse.ch)", "https://urlhaus.abuse.ch/downloads/hostfile/"),
    ("phishing", "The Block List Project", "https://blocklistproject.github.io/Lists/alt-version/phishing-nl.txt"),
    ("scam", "The Block List Project", "https://blocklistproject.github.io/Lists/alt-version/scam-nl.txt"),
]
CACHE_DIR = os.path.join(config.APPDATA_DIR, "safe_browsing")
MAX_AGE_S = 24 * 3600
MAX_BYTES = 20 * 1024 * 1024
USER_AGENT = "AwesomeDownloader-SafeBrowsing/1 (+https://awesome-downloader.pages.dev)"

_lock = threading.Lock()
_domains = {}          # domain -> (kind, list name)
_allowed = set()       # hosts the person chose to visit anyway, this session
_extra = {}            # sites added by the tests, kept apart from the downloaded lists
_updated = 0.0
_started = False


def parse(text):
    """Domains from a hosts file ("0.0.0.0 example.com") or a plain list."""
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        parts = line.split()
        host = parts[-1] if len(parts) > 1 else parts[0]
        host = host.strip(".")
        if "." not in host or host in ("localhost", "localhost.localdomain", "0.0.0.0", "127.0.0.1"):
            continue
        out.add(host)
    return out


def _cache_path(kind):
    return os.path.join(CACHE_DIR, "%s.txt" % kind)


def _load_cached():
    global _updated
    loaded, newest = {}, 0.0
    for kind, name, _url in LISTS:
        path = _cache_path(kind)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for host in parse(f.read()):
                    loaded.setdefault(host, (kind, name))
            newest = max(newest, os.path.getmtime(path))
        except FileNotFoundError:
            continue
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't read the %s list", kind)
    with _lock:
        _domains.clear()
        _domains.update(loaded)
        _updated = newest
    return len(loaded)


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("list larger than expected")
    return data.decode("utf-8", errors="replace")


def refresh(force=False):
    """Downloads any list older than a day (or all, with force). Blocking."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    changed = False
    for kind, _name, url in LISTS:
        path = _cache_path(kind)
        try:
            fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE_S
        except OSError:
            fresh = False
        if fresh and not force:
            continue
        try:
            text = _download(url)
            if not parse(text):
                raise ValueError("the list came back empty")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
            changed = True
        except Exception as exc:   # noqa: BLE001 -- keep the copy we have
            logger.warning("Couldn't update the %s list: %s", kind, exc)
    if changed or not _domains:
        _load_cached()


def start():
    """Loads the saved lists and updates them, in the background, once."""
    global _started
    if _started:
        return
    _started = True

    def work():
        try:
            _load_cached()
            if not os.environ.get("AWD_OFFLINE_LISTS"):     # the test suite runs offline
                refresh()
        except Exception:   # noqa: BLE001
            logger.exception("Safe browsing lists failed to load")
    threading.Thread(target=work, name="safe-browsing", daemon=True).start()


def check(url):
    """(kind, list name) when `url`'s site -- or a domain above it -- is on a
    list, else None. Only web pages are checked."""
    try:
        parsed = urllib.parse.urlparse(url or "")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower().strip(".")
    if not host or host in _allowed:
        return None
    with _lock:
        if not _domains and not _extra:
            return None
        labels = host.split(".")
        for i in range(len(labels) - 1):
            name = ".".join(labels[i:])
            hit = _domains.get(name) or _extra.get(name)
            if hit:
                return hit
    return None


def allow(url):
    """Visit anyway: this host isn't stopped again until the app restarts."""
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    if host:
        _allowed.add(host)


def status():
    with _lock:
        return len(_domains), _updated


def add_for_test(domains, kind="phishing"):
    """Tests put their own made-up sites on the list."""
    with _lock:
        for d in domains:
            _extra[d] = (kind, "test list")
